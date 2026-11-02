"""The ONE unified-memory backfill (27 §10, 29, 31 §6): chronological,
historical, one Cognition session per batch — never a second pipeline.

**What it is.** For each unit, every evidence stimulus — every recorded
`stimulus` row of an evidence kind, plus the stimulus-equivalents of the
message/social/observation rows that predated stimulus recording — is walked
OLDEST FIRST by evidence time, batched, and run through the ONE
`CognitionSessionRunner` in `replay(historical=True)` mode: the same assembly
as a live wake, act tools withheld by `historical`, all reads bounded as-of
the batch's evidence instant (`Grounder`'s `now_ms`, the memory lane's
`work.as_of`).

**Default is dry-run.** `census` reads only — no writes, no model call —
and reports what the lane covers, what is parked, what is done, and what is
not enumerable (register kinds are the free projection lane's, pre-epoch
`play`/`rollup` have no source rows to re-derive). Nothing in a census claims
"complete": coverage is stated as attempted/done/parked counts.

**Apply is bounded and recoverable.** `--apply` requires explicit
`--max-batches`, `--max-tokens` and `--max-seconds`; the bounds are run-wide
and enforced at the session boundary (each batch's budget is clamped to
what the run has left, and unknown usage is an ERROR, never a zero). A
batch already comprehended costs only an `inspect` — never a model slot —
so a bounded resume always reaches new work. Progress is what the session
itself writes: an agenda item is DONE when `comprehend_result` holds a
`readings` receipt for its exact `(work_id, input_hash)` — every admitted
item of a multi-item proposal gets its own receipt — so a restart never
re-comprehends and a changed input re-runs. A session that returns without
comprehending (refused, unadmitted, raised) stops the chronological
frontier: it is failure-receipted under this lane's ISOLATED
`backfill-stimulus:` key (five parks it for operator requeue; the live
wake's own `stimulus:` counters are untouched) and later history never
runs while earlier work is unresolved. The lane never touches
`cognition_wake*`, live stimuli or profile-writer retirement.

**Historical does not act.** The runner is a variant of the live
composition's with act verbs already refused by `historical`; this lane also
withholds every read tool (none can be bounded as-of), the Register index
blocks and self/shared/presence context that only exist as CURRENT heads
(the graph preview — `valid_at = evidence time` — carries as-of context
instead), and `platform_observation` folded heads (immutable event rows stay
readable). Media readings render at their latest fold — the same bound
`records.load` applies everywhere else; `media_objects` carries no read
timestamp for a stricter as-of.

**Catch-up is separate.** `--catch-up` is a deliberate, current-clock
operation run only after history reports attempted coverage of every
enumerable stimulus: it appends ONE `time` stimulus per unit (dedup'd by
`uq_stimulus_time_ref`) so the LIVE wake evaluator fires an ordinary session
on the real clock — full tools, acts allowed, the still-open selection the
live session already shows. It never runs during the historical lane and
never acts itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.cognition_session import Room, build_agenda
from nora.domain.live import LiveMapping
from nora.domain.stimuli import LastSession, Stimulus, UnitView
from nora.infra.sql import unified_backfill as sql
from nora.infra.sql.schema import COGNITION_SESSION
from nora.infra.sql.schema import COMPREHEND_RESULT as RESULT

log = logging.getLogger(__name__)

#: The default stimuli per batch — below the live `batch` trigger's 80, so a
#: historical session never sees more pending than a live one could.
DEFAULT_BATCH_STIMULI = 40
#: The catch-up stimulus's `time` item: a `time` wake reviews the open
#: notebook on the real clock — the design's step-3 catch-up, one per unit.
CATCH_UP_ITEM = "unified-backfill-catch-up"


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------

def _by_kind_ref(
    keys: Mapping[tuple[str, str, str], set[int]],
) -> set[tuple[str, str]]:
    return {(kind, ref) for kind, _item, ref in keys}


def _deduped(
    fabricated: Sequence[Stimulus],
    keys: Mapping[tuple[str, str, str], set[int]],
) -> list[Stimulus]:
    """Drop fabricated stimuli a recorded row already covers. `social` and
    `observation` refs are unique per row forever; a `message`/`reaction`/
    `delete` item's ref is unique to its own row; only an `edit` shares its
    `messages:<id>` ref with other edits of the same message, so it dedups on
    the full (kind, item, ref, at_ms) identity."""
    kind_refs = _by_kind_ref(keys)
    out: list[Stimulus] = []
    for s in fabricated:
        if s.kind in ("social", "observation"):
            if (s.kind, s.ref) in kind_refs:
                continue
        elif s.item == "edit":
            if s.at_ms in keys.get((s.kind, s.item, s.ref), set()):
                continue
        elif (s.kind, s.item, s.ref) in keys:
            continue
        out.append(s)
    return out


async def enumerate_unit(
    conn: AsyncConnection, unit: str, *,
    rooms: Collection[tuple[str, str]],
    unit_of: Any,
    person_rooms: Mapping[tuple[str, str], tuple[str, str]],
    self_ids: Collection[str],
    from_ms: int | None = None, until_ms: int | None = None,
) -> tuple[list[Stimulus], dict[str, int]]:
    """The unit's evidence, oldest first by `at_ms`: recorded stimulus rows
    plus fabricated stimulus-equivalents of the rows recording predates.
    Returns (stimuli, fabricated-by-kind) for the census."""
    real = await sql.evidence_stimuli(
        conn, unit, from_ms=from_ms, until_ms=until_ms)
    keys = await sql.stimulus_keys(conn, unit)
    fabricated: list[Stimulus] = []
    fabricated += await sql.fabricated_message_stimuli(
        conn, unit, rooms, self_ids=self_ids, from_ms=from_ms, until_ms=until_ms)
    # A social item lands in every (person, unit) room the mapping names.
    own_social = {k: v for k, v in person_rooms.items() if k[1] == unit}
    fabricated += await sql.fabricated_social_stimuli(
        conn, person_rooms=own_social, from_ms=from_ms, until_ms=until_ms)
    fabricated += [
        s for s in await sql.fabricated_observation_stimuli(
            conn, unit_of=unit_of, from_ms=from_ms, until_ms=until_ms)
        if s.unit == unit
    ]
    kept = _deduped(fabricated, keys)
    by_kind: dict[str, int] = {}
    for s in kept:
        by_kind[s.kind] = by_kind.get(s.kind, 0) + 1
    out = sorted([*real, *kept], key=lambda s: (s.at_ms, s.id))
    return out, by_kind


def form_batches(
    stimuli: Sequence[Stimulus], home_room: Room, *,
    max_stimuli: int = DEFAULT_BATCH_STIMULI,
    max_items: int = 12,
) -> list[tuple[Stimulus, ...]]:
    """Chronological prefixes of the remaining stimuli: at most `max_stimuli`
    each AND never more agenda items than `max_items` (a live wake's cap), so
    no batch ever has deferred items — the next batch picks up where this one
    stopped. `build_agenda` is the same pure function the session uses, so the
    item check below is exactly the session's grouping."""
    if max_stimuli < 1 or max_items < 1:
        raise ValueError("batch and agenda limits must be positive")
    out: list[tuple[Stimulus, ...]] = []
    i = 0
    while i < len(stimuli):
        j = min(i + max_stimuli, len(stimuli))
        while j - i > 1 and build_agenda(
            stimuli[i:j], home_room=home_room, max_items=max_items,
        ).deferred_stimulus_ids:
            j -= 1
        out.append(tuple(stimuli[i:j]))
        i = j
    return out


def work_id_sids(work_id: str) -> set[int]:
    """The stimulus ids a `stimuli:<ids>` work id names (the format
    `WakeMemory.prepare` builds)."""
    if not work_id.startswith("stimuli:"):
        return set()
    return {int(s) for s in work_id.removeprefix("stimuli:").split(",") if s}


def input_pairs(work: Sequence[Mapping[str, Any]], unit: str) -> dict[int, tuple[str, str]]:
    """Stable source identities, independent of the agenda's batch grouping."""
    result: dict[int, tuple[str, str]] = {}
    for item in work:
        for sid, digest in item.get("inputs", {}).items():
            result[int(sid)] = (f"input:{unit}:{sid}", digest)
    return result


async def completed_inputs(
    conn: AsyncConnection, unit: str, work: Sequence[Mapping[str, Any]],
) -> set[int]:
    identities = input_pairs(work, unit)
    done = await sql.readings_done(conn, [key for key, _ in identities.values()])
    return {sid for sid, pair in identities.items() if pair in done}


async def covered_stimulus_ids(conn: AsyncConnection, unit: str) -> set[int]:
    """Every stimulus id that EVER reached `readings` in this unit (any
    input hash): the attempted-coverage floor. `readings_done`'s exact-hash
    check is the stronger one the apply loop runs per item."""
    rows = (await conn.execute(
        sa.select(RESULT.c.work_id)
        .select_from(RESULT.join(
            COGNITION_SESSION, COGNITION_SESSION.c.id == RESULT.c.session_id))
        .where(
            COGNITION_SESSION.c.unit == unit,
            RESULT.c.part == "readings",
            RESULT.c.work_id.like("stimuli:%"),
        )
    )).all()
    out: set[int] = set()
    for r in rows:
        out |= work_id_sids(str(r.work_id))
    return out


# ---------------------------------------------------------------------------
# Dry-run census
# ---------------------------------------------------------------------------

async def census(
    engine: AsyncEngine, *, units: Collection[str],
    rooms_of_unit: Any, unit_of: Any,
    person_rooms: Mapping[tuple[str, str], tuple[str, str]],
    self_ids: Collection[str],
    from_ms: int | None = None, until_ms: int | None = None,
) -> dict[str, Any]:
    """Zero writes, zero model calls: the enumeration's own counts plus what
    durable state says about the lane. Reads only."""
    report: dict[str, Any] = {}
    async with engine.connect() as conn:
        for unit in units:
            stimuli, fabricated = await enumerate_unit(
                conn, unit, rooms=rooms_of_unit(unit), unit_of=unit_of,
                person_rooms=person_rooms, self_ids=self_ids,
                from_ms=from_ms, until_ms=until_ms)
            failures = await sql.failure_counts(
                conn, unit, [s.id for s in stimuli])
            parked = {sid for sid, n in failures.items() if n >= sql.PARK_AFTER}
            covered = await covered_stimulus_ids(conn, unit)
            lane = await sql.lane_counts(conn, unit)
            real = [s for s in stimuli if s.id > 0]
            kinds: dict[str, int] = {}
            for s in real:
                kinds[s.kind] = kinds.get(s.kind, 0) + 1
            report[unit] = {
                "enumerated": len(stimuli),
                "recorded_stimuli": len(real),
                "fabricated": fabricated,
                "by_kind": kinds,
                "range_ms": (
                    [stimuli[0].at_ms, stimuli[-1].at_ms] if stimuli else None),
                "attempted_stimuli": len(
                    [s for s in stimuli if s.id in covered]),
                "uncovered_stimuli": len(
                    [s for s in stimuli if s.id not in covered
                     and s.id not in parked]),
                "parked_stimuli": len(parked),
                "failing_stimuli": len(
                    [n for n in failures.values() if n > 0]),
                "historical_lane": lane,
            }
    return {"units": report, "unenumerable": {
        "time": "the clock's floors are not evidence; nothing historical to "
                "comprehend",
        "self": "her own scheduled wakes; a historical lane may not re-fire "
                "acts",
        "register": "notes/memos/events/reminders/ledger are Nora-authored "
                    "state, carried to the graph by the free projection lane "
                    "(graph-project), not by sessions",
        "play/rollup pre-stimulus": "derived items; only their recorded "
                    "stimulus rows enumerate",
    }}


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

@dataclass
class Bounds:
    """The finite bounds `--apply` requires; `None` is never allowed."""

    max_batches: int
    max_tokens: int
    max_seconds: float

    def __post_init__(self) -> None:
        if not all(isfinite(value) and value > 0 for value in (
            self.max_batches, self.max_tokens, self.max_seconds,
        )):
            raise ValueError("run limits must be finite and positive")


class UnknownSpendError(RuntimeError):
    """A session returned with NO readable usage. `CognitionRunResult.
    total_tokens` is `None` for that, never zero — counting it as zero would
    let paid sessions run past `--max-tokens` unbounded, so the run fails
    closed instead."""


class MeteredAgentRunner:
    """The live agent runner plus the run's spend meter and remaining
    allowance, so `--max-tokens`/`--max-seconds` bound the WHOLE run, not a
    precheck.

    - A successful result with unknown usage raises `UnknownSpendError`
      (ERROR, never a silent zero).
    - A FAILED session's usage is read from `SESSION_TOKENS`, the holder the
      session leaves for exactly this (a raised/timed-out loop is the
      costliest kind and has no result to read).
    - `remaining_tokens`/`remaining_seconds`, set by the loop before each
      batch, CLAMP the session's own `AdaptorBudget`: the session cannot
      spend or run past what the run has left."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.total_tokens = 0
        self.calls = 0
        self.usage_known = True
        self.remaining_tokens: int | None = None
        self.remaining_seconds: float | None = None

    async def run(self, **kwargs: Any) -> Any:
        from dataclasses import replace

        from nora.app.cognition_session import SESSION_TOKENS

        spend = SESSION_TOKENS.get()
        if spend is not None:
            spend.total = None
        budget = kwargs.get("budget")
        if budget is not None:
            kwargs = dict(kwargs, budget=replace(
                budget,
                total_tokens=(
                    min(budget.total_tokens, self.remaining_tokens)
                    if self.remaining_tokens is not None
                    else budget.total_tokens),
                timeout_seconds=(
                    min(budget.timeout_seconds, self.remaining_seconds)
                    if self.remaining_seconds is not None
                    else budget.timeout_seconds),
            ))
        self.calls += 1
        try:
            result = await self._inner.run(**kwargs)
        except BaseException:
            spend = SESSION_TOKENS.get()
            if spend is not None and spend.total:
                self.total_tokens += spend.total
            else:
                self.usage_known = False
            raise
        total = result.total_tokens
        if total is None:
            spend = SESSION_TOKENS.get()
            total = spend.total if spend is not None else None
        if total is None:
            self.usage_known = False
            raise UnknownSpendError(
                "cognition session returned without readable token usage")
        self.total_tokens += total
        return result


class _AsOfObservations:
    """The observation store in the historical lane. `by_ids` resolves the
    immutable event rows (the observation's own record, unchanged since it
    was written — as-of safe). `heads`/`head_for_handle` answer TODAY's
    folded object state — withheld: a historical session must not read the
    future fold."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def by_ids(self, *a: Any, **kw: Any) -> Any:
        return await self._store.by_ids(*a, **kw)

    async def heads(self, *a: Any, **kw: Any) -> list[Any]:
        return []

    async def head_for_handle(self, *a: Any, **kw: Any) -> Any:
        return None


async def _no_titles(unit: str) -> tuple[str, ...]:
    return ()


async def _no_events(unit: str, platform: str, conversation_id: str) -> tuple[Any, ...]:
    return ()


async def _no_standing(platform: str, conversation_id: str) -> Any:
    return None


@dataclass
class UnitOutcome:
    """One unit's run, for the report."""

    unit: str
    batches: int = 0
    sessions: int = 0
    skipped_done: int = 0
    skipped_empty: int = 0
    failures: int = 0
    parked: int = 0
    stimuli: int = 0
    stopped: str | None = None


def historical_runner(live: Any, *, grounder: Any, agent_runner: Any) -> Any:
    """The live composition's session runner, variant: historical grounding,
    no read tools, no live-only seams. `with_collaborators` shares every other
    collaborator, so doctrine, skills, budgets, clocks and stores cannot drift
    from the live session's."""
    return live.with_collaborators(
        grounder=grounder,
        agent_runner=agent_runner,
        read_tools_for=lambda _unit, _rooms: [],
        read_only_tools=frozenset(),
        shared=None,
        standing_fold=None,
        open_note_titles=_no_titles,
        open_events=_no_events,
        prior_standing=_no_standing,
    )


async def apply(
    engine: AsyncEngine, *,
    cfg: Any, deps: Any,
    units: Sequence[str],
    home_rooms: Mapping[str, Room],
    rooms_of_unit: Any, unit_of: Any,
    person_rooms: Mapping[tuple[str, str], tuple[str, str]],
    self_ids: Collection[str],
    bounds: Bounds,
    batch_stimuli: int = DEFAULT_BATCH_STIMULI,
    from_ms: int | None = None, until_ms: int | None = None,
) -> dict[str, Any]:
    """Run the historical lane. Raises `ValueError` with the missing
    prerequisite — never runs without the comprehend lane wired and the
    bounds given."""
    live = deps.cognition_runner
    if live is None:
        raise ValueError(
            "unavailable: the Cognition session runner is not wired "
            "([cognition] enabled / [dreamer] enabled)")
    if live.memory is None:
        raise ValueError(
            "unavailable: the unified memory lane is not wired "
            "([memory.graph] enabled and [memory.graph.comprehend] enabled)")
    if bounds is None or not all(
        v is not None for v in
        (bounds.max_batches, bounds.max_tokens, bounds.max_seconds)
    ):
        raise ValueError(
            "--apply requires --max-batches, --max-tokens and --max-seconds")


    grounder = historical_grounder(
        engine, cfg=cfg, deps=deps, units=units)
    metered = MeteredAgentRunner(live.agent_runner)
    runner = historical_runner(live, grounder=grounder, agent_runner=metered)
    return await run_units(
        engine, runner=runner,
        units=units, home_rooms=home_rooms, rooms_of_unit=rooms_of_unit,
        unit_of=unit_of, person_rooms=person_rooms, self_ids=self_ids,
        bounds=bounds, metered=metered, batch_stimuli=batch_stimuli,
        from_ms=from_ms, until_ms=until_ms)


def historical_grounder(engine: AsyncEngine, *, cfg: Any, deps: Any,
                        units: Sequence[str]) -> Any:
    """The historical lane's grounder: the same `build_grounder` seam as live,
    with the current-head blocks withheld (none can be read as-of) and the
    reads bounded by the session's own `now_ms` the runner now passes."""
    from nora.app.assembler import lineage_policy
    from nora.app.cognition_grounding import build_grounder
    from nora.infra.sql.record_query import SqlRecordQuery

    allowlist = deps.config_reload.policy
    cognition_unit_rooms = LiveMapping(lambda: {
        unit: {(c.platform, cid) for c in allowlist.rooms()
               if c.unit == unit and c.absorbed for cid in c.ids}
        for unit in units
    })
    cognition_room_labels = LiveMapping(lambda: {
        (c.platform, cid): c.label for c in allowlist.rooms() for cid in c.ids
    })

    async def _no_notes(unit: str) -> tuple[Any, ...]:
        return ()

    return build_grounder(
        records=SqlRecordQuery(engine), persons=deps.persons,
        media_join=deps.media_join,
        # The Register index blocks only exist as CURRENT heads — none can be
        # read as-of T, so the historical lane withholds them rather than
        # show tomorrow's notebook in yesterday's session. The graph preview
        # (`valid_at`) carries the as-of context instead.
        note_index=_no_notes, events_in=_no_events, memo_index=None,
        pending_reminds=None,
        lineage=lineage_policy(cfg, policy=allowlist, persons=deps.persons),
        unit_rooms=lambda unit: cognition_unit_rooms.get(unit, set()),
        ai_display_name=cfg.doctrine.display_name,
        room_label=lambda p, c: cognition_room_labels.get((p, c), ""),
        own_send_lookback_seconds=cfg.cognition.own_send_lookback_seconds,
        own_sends_max=cfg.cognition.own_sends_max,
        tz=ZoneInfo(cfg.cognition.timezone),
        # Presence, culture and profile heads, and the SharedView are
        # current-state too — withheld by the same rule.
        stay_context=None, place_release=None,
        self_account=None, self_profile=None,
        metrics=deps.metrics,
        observations=_AsOfObservations(deps.cognition_runner.observations),
    )


async def run_units(engine: AsyncEngine, *, bounds: Bounds, **kwargs: Any) -> dict[str, Any]:
    """One deadline encloses enumeration, inspection, model work and receipts."""
    started = time.monotonic()
    report: dict[str, Any] = {
        "units": {}, "tokens": 0, "stopped": None, "model_batches": 0}
    metered = kwargs.pop("metered", None) or MeteredAgentRunner(None)
    try:
        async with asyncio.timeout(bounds.max_seconds):
            return await _run_units(
                engine, bounds=bounds, metered=metered, started=started,
                report=report, **kwargs)
    except TimeoutError:
        report["stopped"] = "max_seconds"
        for outcome in report["units"].values():
            if isinstance(outcome, UnitOutcome) and outcome.stopped is None:
                outcome.stopped = "max_seconds"
        report["usage_known"] = getattr(metered, "usage_known", True)
        report["tokens"] = metered.total_tokens if report["usage_known"] else None
        report["agent_invocations"] = metered.calls
        report["units"] = {
            unit: vars(outcome) if isinstance(outcome, UnitOutcome) else outcome
            for unit, outcome in report["units"].items()}
        return report


async def _run_units(
    engine: AsyncEngine, *, runner: Any,
    units: Sequence[str],
    home_rooms: Mapping[str, Room],
    rooms_of_unit: Any, unit_of: Any,
    person_rooms: Mapping[tuple[str, str], tuple[str, str]],
    self_ids: Collection[str],
    bounds: Bounds, metered: Any = None,
    batch_stimuli: int = DEFAULT_BATCH_STIMULI,
    from_ms: int | None = None, until_ms: int | None = None,
    started: float,
    report: dict[str, Any],
) -> dict[str, Any]:
    """The historical lane's loop over one already-built runner — the part
    every test drives. `runner` answers `inspect`/`replay`/`max_items`;
    failures are receipts written under this lane's isolated key
    (`sql.fail_historical`, never the live `stimulus:` key); `metered`
    accumulates spend and is told what the run has left before each batch
    (a `MeteredAgentRunner`, or a test's own counter).

    Ordering is a chronological FRONTIER: a batch that cannot be shown
    comprehended after its session — refused, unadmitted or failed — stops
    the unit (and the receipts record exactly which stimuli), so later
    history never runs while earlier work is unresolved and a restart
    resumes AT the failure instead of after it."""
    if metered is None:
        metered = type("_M", (), {"total_tokens": 0, "calls": 0})()
    for unit in units:
        outcome = UnitOutcome(unit=unit)
        report["units"][unit] = outcome
        home = home_rooms.get(unit)
        if home is None:
            outcome.stopped = "no_home_room"
            continue
        if time.monotonic() - started >= bounds.max_seconds:
            outcome.stopped = report["stopped"] = "max_seconds"
            break
        async with engine.connect() as conn:
            stimuli, _ = await enumerate_unit(
                conn, unit, rooms=rooms_of_unit(unit), unit_of=unit_of,
                person_rooms=person_rooms, self_ids=self_ids,
                from_ms=from_ms, until_ms=until_ms)
            failures = await sql.failure_counts(
                conn, unit, [s.id for s in stimuli])
        parked = {sid for sid, n in failures.items() if n >= sql.PARK_AFTER}
        outcome.parked = len(parked)
        pending_all = list(stimuli)
        outcome.stimuli = len(pending_all)
        last_ms: int | None = None
        for batch in form_batches(
            pending_all, home, max_stimuli=batch_stimuli,
            max_items=runner.max_items,
        ):
            if time.monotonic() - started >= bounds.max_seconds:
                outcome.stopped = report["stopped"] = "max_seconds"
                break
            outcome.batches += 1
            now_ms = max(s.at_ms for s in batch)
            view = UnitView(
                unit=unit, pending=batch, last_activity_ms=None,
                last_wake=None,
                last_session=(
                    LastSession(state="closed", started_at_ms=last_ms,
                                ended_at_ms=last_ms)
                    if last_ms is not None else None),
                carry={},
            )
            try:
                info = await runner.inspect(view=view, now_ms=now_ms, historical=True)
            except Exception:
                log.exception("unified backfill: inspect failed for %s", unit)
                for s in batch:
                    await sql.fail_historical(engine, unit, s.id, None)
                outcome.failures += 1
                outcome.stopped = "inspect_failed"
                break
            if info is None:
                outcome.failures += 1
                outcome.stopped = "unresolved"
                break
            work = info["work"]
            async with engine.connect() as conn:
                done_sids = await completed_inputs(conn, unit, work)
            omitted = {s.id for s in batch} - input_pairs(work, unit).keys()
            if omitted:
                outcome.failures += 1
                outcome.stopped = "unresolved"
                break
            pending = [s for s in batch if s.id not in done_sids]
            if not pending:
                # A completed-prefix scan costs nothing and consumes NO
                # model-work slot — otherwise a bounded resume would burn
                # its whole allowance re-checking done batches.
                outcome.skipped_done += 1
                last_ms = now_ms
                continue
            if any(s.id in parked for s in pending):
                outcome.failures += 1
                outcome.stopped = "unresolved"
                break
            if report["model_batches"] >= bounds.max_batches:
                outcome.stopped = report["stopped"] = "max_batches"
                break
            if metered.total_tokens >= bounds.max_tokens:
                outcome.stopped = report["stopped"] = "max_tokens"
                break
            # The session is handed only what the run has LEFT.
            metered.remaining_tokens = bounds.max_tokens - metered.total_tokens
            metered.remaining_seconds = (
                bounds.max_seconds - (time.monotonic() - started))
            view = UnitView(
                unit=unit, pending=tuple(pending), last_activity_ms=None,
                last_wake=None, last_session=view.last_session, carry={},
            )
            try:
                # Re-inspect the FILTERED pending: dropping done stimuli
                # regroups items, so this is the work the session actually
                # gets invoked on — the digests the post-run check must
                # verify, never the pre-filter batch's.
                invoked = await asyncio.wait_for(
                    runner.inspect(
                        view=view, now_ms=now_ms, historical=True),
                    timeout=metered.remaining_seconds)
            except Exception:
                log.exception("unified backfill: re-inspect failed for %s",
                              unit)
                for s in pending:
                    await sql.fail_historical(engine, unit, s.id, None)
                outcome.failures += 1
                outcome.stopped = "inspect_failed"
                break
            if invoked is None or not invoked["work"]:
                # A nonempty remainder forming no agenda item is
                # ungroundable — count it, never silently certify.
                outcome.skipped_empty += len(pending)
                outcome.failures += 1
                outcome.stopped = "unresolved"
                break
            work = invoked["work"]
            if {s.id for s in pending} - input_pairs(work, unit).keys():
                outcome.failures += 1
                outcome.stopped = "unresolved"
                break
            metered.remaining_seconds = bounds.max_seconds - (time.monotonic() - started)
            report["model_batches"] += 1
            try:
                await asyncio.wait_for(
                    runner.replay(view=view, now_ms=now_ms, historical=True),
                    timeout=metered.remaining_seconds)
            except Exception:
                log.exception("unified backfill: session failed for %s", unit)
                for s in pending:
                    await sql.fail_historical(engine, unit, s.id, None)
                outcome.failures += 1
                outcome.stopped = "session_failed"
                break
            outcome.sessions += 1
            # A normal return is NOT comprehension: re-read the durable
            # receipts. Every agenda item the session was invoked on must
            # now be provably comprehended — its own exact
            # (work_id, input_hash) pair. Coverage at a different hash
            # cannot certify this input. Unresolved work stops the
            # frontier and is failure-receipted under this lane's key.
            async with engine.connect() as conn:
                done_sids = await completed_inputs(conn, unit, work)
            unresolved = {s.id for s in pending} - done_sids
            unresolved_pending = [s for s in pending if s.id in unresolved]
            if unresolved_pending:
                for s in unresolved_pending:
                    await sql.fail_historical(engine, unit, s.id, None)
                outcome.failures += 1
                outcome.stopped = "unresolved"
                break
            last_ms = now_ms
        if report["stopped"] or outcome.stopped in (
            "inspect_failed", "session_failed", "unresolved",
        ):
            break
        outcome.stopped = outcome.stopped or "done"
    report["usage_known"] = getattr(metered, "usage_known", True)
    report["tokens"] = metered.total_tokens if report["usage_known"] else None
    report["agent_invocations"] = metered.calls
    report["units"] = {u: vars(o) for u, o in report["units"].items()}
    return report


# ---------------------------------------------------------------------------
# Catch-up
# ---------------------------------------------------------------------------

async def catch_up(
    engine: AsyncEngine, *,
    stimulus_store: Any,
    runner: Any,
    units: Sequence[str],
    home_rooms: Mapping[str, Room],
    rooms_of_unit: Any, unit_of: Any,
    person_rooms: Mapping[tuple[str, str], tuple[str, str]],
    self_ids: Collection[str],
    now_ms: int,
    pending_reminds: Any = None,
) -> dict[str, Any]:
    """One `time` stimulus per unit whose WHOLE history is complete — every
    enumerable agenda item proven comprehended at its CURRENT input
    identity (the exact `(work_id, input_hash)` receipts the apply loop
    verifies, computed here without a model call via `runner.inspect`),
    and no historical-parked or unresolved work. The gate always measures
    the entire unit: a `--from/--until` window narrows a run, it can never
    certify completion, so it is not an argument here. An incomplete unit
    is refused loudly with what remains; the live wake evaluator owns what
    happens next — a real-clock session whose own grounding selects the
    still-open work (open notes via `open_note_titles`, pending reminders
    and open events via `pending_reminds`/`events_in`, live read tools and
    the graph at the real clock): the design's step-3 catch-up, never fired
    by the historical lane."""
    from nora.domain.stimuli import NewStimulus

    out: dict[str, Any] = {"units": {}, "stopped": None}
    reminders = list(await pending_reminds()) if pending_reminds is not None else []
    for unit in units:
        home = home_rooms.get(unit)
        if home is None:
            out["units"][unit] = {"status": "no_home_room"}
            continue
        unit_rooms = rooms_of_unit(unit)
        remind_subjects = [
            str(reminder.text) for reminder in reminders
            if (reminder.platform, reminder.conversation_id) in unit_rooms
        ]
        async with engine.connect() as conn:
            stimuli, _ = await enumerate_unit(
                conn, unit, rooms=rooms_of_unit(unit), unit_of=unit_of,
                person_rooms=person_rooms, self_ids=self_ids)
            failures = await sql.failure_counts(
                conn, unit, [s.id for s in stimuli])
        parked = {sid for sid, n in failures.items() if n >= sql.PARK_AFTER}
        # A recorded failure blocks ONLY while its stimulus is still
        # uncovered: a later successful retry at the exact current input
        # clears it (the receipt is the truth, not the attempt log). Parked
        # above already blocks.
        failing = {sid for sid, n in failures.items() if n > 0}
        blocked: set[int] = set()
        failed_uncovered: set[int] = set()
        error: str | None = None
        for batch in form_batches(
            stimuli, home, max_stimuli=DEFAULT_BATCH_STIMULI,
            max_items=runner.max_items,
        ):
            now_ms_batch = max(s.at_ms for s in batch)
            view = UnitView(
                unit=unit, pending=batch, last_activity_ms=None,
                last_wake=None, last_session=None, carry={},
            )
            try:
                info = await runner.inspect(view=view, now_ms=now_ms_batch, historical=True)
            except Exception:
                log.exception("catch-up gate: inspect failed for %s", unit)
                error = "inspect_failed"
                break
            if info is None or not info["work"]:
                # A nonempty batch that formed NO agenda item cannot be
                # proven covered (ungrouped/ungroundable sources) — it
                # blocks, never silently certifies.
                blocked |= {s.id for s in batch}
                failed_uncovered |= {
                    s.id for s in batch if s.id in failing}
                continue
            work = info["work"]
            async with engine.connect() as conn:
                done_sids = await completed_inputs(conn, unit, work)
            blocked.update(s.id for s in batch if s.id not in done_sids)
            failed_uncovered |= blocked & failing
        if error is not None:
            out["units"][unit] = {"status": "error", "reason": error}
            continue
        if blocked:
            out["units"][unit] = {
                "status": "incomplete",
                "reason": "parked" if blocked & parked else "uncovered_or_stale_input",
                "blocked_stimuli": sorted(blocked),
                "failing_uncovered": sorted(failed_uncovered),
            }
            continue
        async with engine.connect() as conn:
            open_work = await sql.still_open(
                conn, unit, now_ms, remind_subjects=remind_subjects)
        sid = await stimulus_store.append(
            stimulus=NewStimulus(
                unit=unit, kind="time",
                item=sql.still_open_item(open_work),
                ref=f"{CATCH_UP_ITEM}:{unit}:{now_ms // 86_400_000}",
                at_ms=now_ms, due_ms=now_ms,
            ),
            recorded_at_ms=now_ms,
        )
        out["units"][unit] = {
            "status": "queued" if sid is not None else "already_queued",
            "stimulus_id": sid,
            # The deterministic still-open selection the session is about
            # to review: open ledger commitments with due times, upcoming
            # events, pending reminders. Closed/expired is out by the
            # row's own fields; the `item` line renders it verbatim in the
            # agenda and alias-seeds the graph preview.
            "still_open": open_work,
        }
    return out
