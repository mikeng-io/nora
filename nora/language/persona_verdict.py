"""The Persona verdict judge (design/unified-memory/45 §3): a hypothesis is
never graded by the adaptor that wrote it, so the verdict is this adaptor's
alone.

**The docket** is live state, not a watermark: a unit's open hypotheses whose
`prediction_window_end` has passed (`SqlPersonaStore.due_hypotheses`). The
watermark the loop keeps for it is only the loop's plumbing — a judged line
leaves the docket by its verdict, not by a cursor, so a held run is retried
and a judged one never re-read.

**Two judges, one door.** A hypothesis whose `prediction_observable` IS one
Rollup key (`rollup:<kind>:<subject>`, exactly — `countable_observable`) is
judged by CODE in `commit`: a read of that key over the window, `confirmed`
when a row overlaps it, `expired` when none does, `unknown` when the build
could not be read (R-27, never a side). Any other observable is judged by the
MODEL, handed the prediction, the observable, the window and the window's
Record rows — and nothing the Persona adaptor wrote (no account, no lines).
The model's verdict lands through the same `record_verdict` as code's, which
refuses a line already judged, so the two never grade one line twice.

**`unknown` is not success.** It is counted on its own label, and the
confirmation rate's denominator stays every hypothesis ever written (45 §3).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    assemble,
    render_group,
    render_row_window,
)
from nora.domain.culture import RoomMessage
from nora.domain.errors import AccountNotRecordableError
from nora.domain.persona import (
    VERDICTS,
    PersonaLine,
    countable_observable,
)
from nora.domain.ports import Proposal
from nora.domain.prompt import age, escape
from nora.domain.rollup import iso
from nora.domain.rollup_read import RollupRead, RollupReadQuery
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

__all__ = [
    "PERSONA_VERDICT_ADAPTOR_NAME",
    "DocketAccount",
    "PersonaVerdictAdaptor",
    "PersonaVerdictEvidence",
]

#: The adaptor's `name`: its watermark identity, schedule, budget and grant
#: key, and its objective file.
PERSONA_VERDICT_ADAPTOR_NAME = "persona-verdict"

#: The most hypotheses one run judges.
DOCKET_MAX = 8

#: The most window rows one hypothesis is judged on (the newest of them).
WINDOW_MESSAGES_MAX = 30

#: `inputs` holds one site (the window read); `commit` three (the docket
#: re-read, `record_verdict`, and the code path's Rollup read).
PERSONA_VERDICT_STORE_CALLS_PER_TICK = 4

#: A judge's verdict the model may give: never `open` (that is the absence of
#: one).
JUDGE_VERDICTS = tuple(v for v in VERDICTS if v != "open")

_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("persona_verdict_docket", 10, "turn"),
    ("persona_verdict_window", 20, "turn"),
)
_ROW: dict[str, tuple[int, BlockKey]] = {name: (order, key) for name, order, key in _BLOCKS}


def _at(name: str) -> dict[str, Any]:
    order, key = _ROW[name]
    return {"order": order, "key": key}


class _VerdictStore(Protocol):
    """`nora.infra.sql.persona.SqlPersonaStore`'s docket and verdict write."""

    async def due_hypotheses(
        self, *, unit: str, now_ms: int, limit: int
    ) -> tuple[PersonaLine, ...]: ...

    async def record_verdict(
        self, *, key: str, line_id: str, verdict: str, verdict_by: str, now_ms: int
    ) -> Literal["ok", "conflict", "refused"]: ...


class _Rollup(Protocol):
    """`nora.infra.sql.rollup_read.SqlRollupRead`."""

    async def read(self, q: RollupReadQuery) -> RollupRead: ...


class _Reads(Protocol):
    """`nora.infra.sql.character.SqlCharacterEvidence`'s window read."""

    async def windowed(
        self, *, rooms: Sequence[tuple[str, str]], since_ms: int, until_ms: int,
        limit: int,
    ) -> list[RoomMessage]: ...


class PersonaVerdictEvidence:
    """The dream loop's count and read for a unit's docket. `after` is the
    loop's watermark and does not narrow the count: the docket is live state
    (a judged line leaves it by its verdict), so there is no «since when»."""

    def __init__(self, *, store: _VerdictStore, clock_ms: Callable[[], int]) -> None:
        self._store = store
        self._clock_ms = clock_ms

    async def count(self, *, key: str, after: int) -> int:
        return len(await self._store.due_hypotheses(
            unit=key, now_ms=self._clock_ms(), limit=DOCKET_MAX))

    async def newest(
        self, *, key: str, after: int, limit: int,
    ) -> tuple[tuple[PersonaLine, ...], int]:
        """The docket (oldest window first, at most `limit`), and a cursor that
        is only the read's time: nothing is consumed by id."""
        now_ms = self._clock_ms()
        due = await self._store.due_hypotheses(
            unit=key, now_ms=now_ms, limit=max(1, min(limit, DOCKET_MAX)))
        return due, now_ms


@dataclass(frozen=True)
class _StagedVerdict:
    persona_key: str
    line_id: str
    verdict: str


@dataclass
class _Run:
    #: Judge handle -> the line it names, for the verb's `H1` handles.
    handles: Mapping[str, PersonaLine]
    #: The staged verdicts (`None` until the verb settles the session).
    staged: tuple[_StagedVerdict, ...] | None = None


@dataclass(frozen=True)
class DocketAccount:
    """What the judge needs of a hypothesis's account: who to say it is
    about, and the rooms whose Record rows its prediction window covers —
    already the LISTED ones (35 §3), so the read here applies no further
    filter."""

    about: str
    rooms: tuple[tuple[str, str], ...]


class PersonaVerdictAdaptor:
    """One unit's hypothesis docket, judged by code or by the model."""

    name = PERSONA_VERDICT_ADAPTOR_NAME
    key_kind = "unit"
    uses_watermark = True
    objective = PERSONA_VERDICT_ADAPTOR_NAME
    #: Unread: answers through `verbs`, never an envelope.
    envelope_key = "persona"
    #: No read tools: the window's rows are in the prompt, and the judge is
    #: handed nothing else (45 §3: «and nothing the adaptor wrote»).
    tools: frozenset[str] = frozenset()
    store_calls_per_tick = PERSONA_VERDICT_STORE_CALLS_PER_TICK

    def __init__(
        self, *, store: _VerdictStore, reads: _Reads, rollup: _Rollup,
        keys: Mapping[str, DocketAccount], name_of: Callable[[str | None], str],
        clock_ms: Callable[[], int], metrics: Metrics | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._reads = reads
        self._rollup = rollup
        self._keys = keys
        self._name_of = name_of
        self._clock_ms = clock_ms
        self._metrics = metrics
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._runs: dict[str, _Run] = {}
        #: Read by the dream loop, never awaited here.
        self.evidence = PersonaVerdictEvidence(store=store, clock_ms=clock_ms)

    def _count(self, verdict: str, judge: str) -> None:
        if verdict not in VERDICTS or judge not in ("code", "model"):
            raise ValueError(
                f"nora_persona_verdict_total{{{verdict},{judge}}} is outside its vocabulary")
        if self._metrics is not None:
            self._metrics.inc("nora_persona_verdict_total", verdict=verdict, judge=judge)

    # -- inputs ------------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        """The docket's MODEL-judged hypotheses (a countable observable is
        code's, and never reaches the model): each with its prediction, its
        observable and its window, and the window's Record rows. `""` when the
        docket is empty or all of it is code's: `commit` still runs."""
        due = [line for line in batch if isinstance(line, PersonaLine)]
        prose = [line for line in due
                 if countable_observable(line.prediction_observable) is None]
        if not prose:
            return ""
        now = self._clock()
        handles = {f"H{i}": line for i, line in enumerate(prose, start=1)}
        self._runs[key] = _Run(handles=handles)
        windows: list[str] = []
        for handle, line in handles.items():
            account = self._keys.get(line.persona_key)
            rooms = [] if account is None else list(account.rooms)
            rows = await self._reads.windowed(
                rooms=rooms,
                since_ms=line.written_at,
                until_ms=line.prediction_window_end or line.written_at,
                limit=WINDOW_MESSAGES_MAX,
            )
            body = render_row_window(
                rows, row_id=lambda m: m.row.id,
                attrs=lambda m: (("age", age(m.row.ts, now)),
                                 ("by", self._name_of(m.row.person_id))),
                body=lambda m: m.row.body or "",
            )
            windows.append(render_group("for", [("id", handle)], body))
        parts = [
            ContextBlock(
                name="persona_verdict_docket", **_at("persona_verdict_docket"),
                body="\n".join(
                    render_group("hypothesis", [
                        ("id", handle),
                        ("about", self._subject(line)),
                        ("window_end", iso(line.prediction_window_end or 0)),
                    ], f"{escape(line.prediction or '')}\n"
                       f"observable: {escape(line.prediction_observable or '')}")
                    for handle, line in handles.items()),
            ),
            ContextBlock(
                name="persona_verdict_window", **_at("persona_verdict_window"),
                body="\n".join(windows),
            ),
        ]
        return assemble(parts, sep="\n\n")

    def _subject(self, line: PersonaLine) -> str:
        account = self._keys.get(line.persona_key)
        return account.about if account is not None else "someone"

    # -- the verb ----------------------------------------------------------

    def verbs(self, *, key: str) -> list[Callable[..., Awaitable[dict[str, object]]]]:
        """`persona_verdict`, bound to this run. A verdict is checked against
        the handles the session was shown; what passes is STAGED, and `commit`
        lands it through `record_verdict` (which refuses a line judged
        meanwhile). The runtime screens the verb's arguments before it runs."""

        async def persona_verdict(
            verdicts: list[dict[str, Any]],
        ) -> dict[str, object]:
            """Settle the hypotheses you were shown: one verdict each, in one call.

            Returns `status`: ok, or refused with `refusals` saying what to fix.

            Args:
                verdicts: One object per hypothesis, with `line` (its id as shown, like
                    H1) and `verdict`: confirmed (the observable occurred in the window),
                    refuted (the window shows it did not), expired (the window closed and
                    it never occurred), or unknown (you cannot tell from what you were
                    shown)."""
            run = self._runs.get(key)
            if run is None:
                return {"status": "unavailable", "reason": "no_session",
                        "detail": "this session is over; nothing was written"}
            if run.staged is not None:
                return {"status": "refused", "reason": "settled",
                        "detail": "this session already settled the docket; stop"}
            refusals: list[dict[str, object]] = []
            staged: list[_StagedVerdict] = []
            seen: set[str] = set()
            for i, item in enumerate(verdicts if isinstance(verdicts, list) else []):
                handle = str(item.get("line", "")).strip() if isinstance(item, dict) else ""
                verdict = str(item.get("verdict", "")).strip() if isinstance(item, dict) else ""
                line = run.handles.get(handle)
                if line is None:
                    refusals.append({"reason": "unknown_line", "op": i,
                                     "detail": f"no hypothesis named {handle!r} was shown"})
                    continue
                if handle in seen:
                    refusals.append({"reason": "line_twice", "op": i,
                                     "detail": f"{handle} judged twice"})
                    continue
                if verdict not in JUDGE_VERDICTS:
                    refusals.append({"reason": "malformed", "op": i,
                                     "detail": f"verdict is one of {JUDGE_VERDICTS}"})
                    continue
                seen.add(handle)
                staged.append(_StagedVerdict(
                    persona_key=line.persona_key, line_id=line.id, verdict=verdict))
            if not isinstance(verdicts, list):
                refusals.append({"reason": "malformed", "op": 0,
                                 "detail": "`verdicts` is a list"})
            if refusals:
                return {"status": "refused", "refusals": refusals}
            run.staged = tuple(staged)
            return {"status": "ok"}

        return [persona_verdict]

    # -- accept / commit ---------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]],
    ) -> Sequence[object]:
        """The staged verdicts. Unsettled — a rendered hypothesis the model
        gave no verdict — RAISES `AccountNotRecordableError`: the docket is
        live state, so the run is reported `unavailable` and retried, never
        settled half-judged (DEC-280's hold)."""
        run = self._runs.pop(key, None)
        if run is None or run.staged is None or len(run.staged) < len(run.handles):
            log.warning("persona-verdict: key=%s ended without judging every "
                        "rendered hypothesis; holding the docket", key)
            raise AccountNotRecordableError(
                f"the verdict session for key={key} did not settle")
        return tuple(run.staged)

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int,
    ) -> int:
        """Land the run's verdicts, then judge what code can. The docket is
        re-read here, not carried from `inputs`: a line judged meanwhile is a
        `conflict` and skipped, and a blank-`inputs` run (the whole docket
        code's) reaches this method with no proposals at all."""
        written = 0
        for proposal in proposals:
            staged = proposal.payload
            if not isinstance(staged, _StagedVerdict):
                raise TypeError("PersonaVerdictAdaptor.commit takes its own verdicts only")
            written += await self._record(
                key=staged.persona_key, line_id=staged.line_id, verdict=staged.verdict,
                verdict_by=proposal.established_by, now_ms=now_ms, judge="model")
        for line in await self._store.due_hypotheses(unit=key, now_ms=now_ms,
                                                     limit=DOCKET_MAX):
            rollup_key = countable_observable(line.prediction_observable)
            if rollup_key is not None:
                written += await self._judge_by_code(line, rollup_key, now_ms)
            elif line.persona_key not in self._keys:
                # The account's rooms are no longer known (the person was
                # unlisted since), so the window can never be read: `unknown`
                # is the honest verdict (R-27), not a judgement.
                written += await self._record(
                    key=line.persona_key, line_id=line.id, verdict="unknown",
                    verdict_by="code", now_ms=now_ms, judge="code")
        return written

    async def _judge_by_code(self, line: PersonaLine, rollup_key: str, now_ms: int) -> int:
        """The countable path: the observable IS one Rollup key, so the verdict
        is whether the current build holds a row of it overlapping the
        prediction window. A build that cannot be read is `unknown` (R-27),
        never `expired`."""
        verdict = "unknown"
        query = _rollup_query(line, rollup_key)
        if query is not None:
            try:
                found = await self._rollup.read(query)
            except Exception:
                log.exception("persona-verdict: rollup read failed for %s", rollup_key)
            else:
                if found.build_id is not None:
                    verdict = "confirmed" if found.rows else "expired"
        return await self._record(
            key=line.persona_key, line_id=line.id, verdict=verdict,
            verdict_by="code", now_ms=now_ms, judge="code")

    async def _record(
        self, *, key: str, line_id: str, verdict: str, verdict_by: str,
        now_ms: int, judge: str,
    ) -> int:
        outcome = await self._store.record_verdict(
            key=key, line_id=line_id, verdict=verdict, verdict_by=verdict_by,
            now_ms=now_ms)
        if outcome != "ok":
            log.warning("persona-verdict: key=%s line=%s verdict not landed: %s",
                        key, line_id, outcome)
            return 0
        self._count(verdict, judge)
        return 1


def _rollup_query(line: PersonaLine, rollup_key: str) -> RollupReadQuery | None:
    """The read a countable observable names: `rollup:<kind>:<subject>` over
    the prediction window, with the subject's unit and persons split out
    (`unit|a,b` for a tie, a bare person otherwise). None when the window is
    empty or inverted — which `commit` then reads as `unknown`."""
    _prefix, kind, subject = rollup_key.split(":", 2)
    if "|" in subject:
        unit, _, persons = subject.partition("|")
        subjects = tuple(p for p in persons.split(",") if p)
    else:
        unit, subjects = line.unit, (subject,)
    start, end = line.written_at, line.prediction_window_end or 0
    if not subjects or end <= start:
        return None
    return RollupReadQuery(kind=kind, unit=unit, subjects=subjects,
                           start_ms=start, end_ms=end, match="all", limit=1)
