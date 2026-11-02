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
`--max-batches`, `--max-tokens` and `--max-seconds`; it stops cleanly at the
first bound and reports which. Progress is what the session itself writes:
an agenda item is DONE when `comprehend_result` holds a `readings` receipt
for its `(work_id, input_hash)` — the same key `SqlComprehendRecords.readings`
dedups on — so a restart never re-comprehends, an edit or a landed reading
re-runs the item, and a failed session's stimuli are `fail_stimulus`-recorded
and parked at five failures until an operator requeues them (29 §3's own
mechanism — the historical lane never touches `cognition_wake`,
`cognition_wake_outcome` or `cognition_wake_carry`).

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

import logging
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
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


class MeteredAgentRunner:
    """The live agent runner plus a token meter, so `--max-tokens` bounds a
    run's total model spend across sessions. It adds nothing else; failures
    propagate as the wrapped runner's own (a session that cannot run is a
    failure, never a silent pass)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.total_tokens = 0
        self.calls = 0

    async def run(self, **kwargs: Any) -> Any:
        result = await self._inner.run(**kwargs)
        self.calls += 1
        self.total_tokens += result.total_tokens or 0
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
        engine, runner=runner, records=live.memory.records,
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


async def run_units(
    engine: AsyncEngine, *, runner: Any, records: Any,
    units: Sequence[str],
    home_rooms: Mapping[str, Room],
    rooms_of_unit: Any, unit_of: Any,
    person_rooms: Mapping[tuple[str, str], tuple[str, str]],
    self_ids: Collection[str],
    bounds: Bounds, metered: Any = None,
    batch_stimuli: int = DEFAULT_BATCH_STIMULI,
    from_ms: int | None = None, until_ms: int | None = None,
) -> dict[str, Any]:
    """The historical lane's loop over one already-built runner — the part
    every test drives. `runner` answers `inspect`/`replay`/`max_items`;
    `records` answers `fail_stimulus` (`SqlComprehendRecords`); `metered`
    accumulates spend (a `MeteredAgentRunner`, or a test's own counter; a
    bare namespace with `total_tokens`/`calls` ints works too)."""
    if metered is None:
        metered = type("_M", (), {"total_tokens": 0, "calls": 0})()
    started = time.monotonic()
    report: dict[str, Any] = {"units": {}, "tokens": 0, "stopped": None}
    for unit in units:
        outcome = UnitOutcome(unit=unit)
        report["units"][unit] = outcome
        home = home_rooms.get(unit)
        if home is None:
            outcome.stopped = "no_home_room"
            continue
        async with engine.connect() as conn:
            stimuli, _ = await enumerate_unit(
                conn, unit, rooms=rooms_of_unit(unit), unit_of=unit_of,
                person_rooms=person_rooms, self_ids=self_ids,
                from_ms=from_ms, until_ms=until_ms)
            failures = await sql.failure_counts(
                conn, unit, [s.id for s in stimuli])
        parked = {sid for sid, n in failures.items() if n >= sql.PARK_AFTER}
        outcome.parked = len(parked)
        pending_all = [s for s in stimuli if s.id not in parked]
        outcome.stimuli = len(pending_all)
        last_ms: int | None = None
        for batch in form_batches(
            pending_all, home, max_stimuli=batch_stimuli,
            max_items=runner.max_items,
        ):
            if outcome.batches >= bounds.max_batches:
                outcome.stopped = report["stopped"] = "max_batches"
                break
            if time.monotonic() - started >= bounds.max_seconds:
                outcome.stopped = report["stopped"] = "max_seconds"
                break
            if metered.total_tokens >= bounds.max_tokens:
                outcome.stopped = report["stopped"] = "max_tokens"
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
                info = await runner.inspect(view=view, now_ms=now_ms)
            except Exception:
                log.exception("unified backfill: inspect failed for %s", unit)
                for s in batch:
                    await records.fail_stimulus(unit, s.id, None)
                outcome.failures += 1
                continue
            if info is None:
                last_ms = now_ms
                continue
            work = info["work"]
            async with engine.connect() as conn:
                done = await sql.readings_done(
                    conn, [w["work_id"] for w in work])
            done_sids = {
                sid for w in work
                if done.get(w["work_id"]) == w["input_hash"]
                for sid in work_id_sids(w["work_id"])
            }
            pending = [s for s in batch if s.id not in done_sids]
            if not pending:
                outcome.skipped_done += 1
                last_ms = now_ms
                continue
            view = UnitView(
                unit=unit, pending=tuple(pending), last_activity_ms=None,
                last_wake=None, last_session=view.last_session, carry={},
            )
            try:
                await runner.replay(view=view, now_ms=now_ms, historical=True)
            except Exception:
                log.exception("unified backfill: session failed for %s", unit)
                for s in pending:
                    await records.fail_stimulus(unit, s.id, None)
                outcome.failures += 1
                continue
            outcome.sessions += 1
            last_ms = now_ms
        if report["stopped"]:
            break
        outcome.stopped = outcome.stopped or "done"
    report["tokens"] = metered.total_tokens
    report["model_calls"] = metered.calls
    report["units"] = {u: vars(o) for u, o in report["units"].items()}
    return report


# ---------------------------------------------------------------------------
# Catch-up
# ---------------------------------------------------------------------------

async def catch_up(
    engine: AsyncEngine, *,
    stimulus_store: Any,
    units: Sequence[str],
    rooms_of_unit: Any, unit_of: Any,
    person_rooms: Mapping[tuple[str, str], tuple[str, str]],
    self_ids: Collection[str],
    now_ms: int,
    from_ms: int | None = None, until_ms: int | None = None,
) -> dict[str, Any]:
    """One `time` stimulus per unit whose history is COMPLETE — every
    enumerable stimulus either comprehended (any hash) or parked. An
    incomplete unit is refused loudly with its remaining count; the
    historical lane itself never fires this. The live wake evaluator owns
    what happens next: a real-clock session, full tools, acts allowed."""
    from nora.domain.stimuli import NewStimulus

    out: dict[str, Any] = {"units": {}, "stopped": None}
    async with engine.connect() as conn:
        for unit in units:
            stimuli, _ = await enumerate_unit(
                conn, unit, rooms=rooms_of_unit(unit), unit_of=unit_of,
                person_rooms=person_rooms, self_ids=self_ids,
                from_ms=from_ms, until_ms=until_ms)
            covered = await covered_stimulus_ids(conn, unit)
            failures = await sql.failure_counts(
                conn, unit, [s.id for s in stimuli])
            parked = {sid for sid, n in failures.items() if n >= sql.PARK_AFTER}
            uncovered = [
                s for s in stimuli
                if s.id not in covered and s.id not in parked
            ]
            if uncovered:
                out["units"][unit] = {
                    "status": "incomplete",
                    "uncovered_stimuli": len(uncovered),
                }
                continue
            sid = await stimulus_store.append(
                stimulus=NewStimulus(
                    unit=unit, kind="time", item=CATCH_UP_ITEM,
                    ref=f"{CATCH_UP_ITEM}:{unit}:{now_ms // 86_400_000}",
                    at_ms=now_ms, due_ms=now_ms,
                ),
                recorded_at_ms=now_ms,
            )
            out["units"][unit] = {
                "status": "queued" if sid is not None else "already_queued",
                "stimulus_id": sid,
            }
    return out
