"""The shared decision log — one row per ambient decision, and the block that
renders it back into the NEXT decider's prompt.

WHAT THIS IS
------------
Nora has three decision paths that can each take a room's floor; this log is
the shared state that lets each see what the others did:

  the Relevance Gate + the participation turn  (`nora.ingest.relevance_gate`,
                                                `FirePath.speak` kind="participation")
  the proactive act turn (no decide pass,      (`FirePath.speak` kind="proactive")
    DEC-277)
  the tag turn                                 (`nora.engagement.pipeline`)

The gate persists NOTHING durable (metrics and trace attributes only). The
determination log (`nora.obs.determination`) is a log: written, never read back
into a prompt. Without this table, one path could answer a question, the tag
turn a second later re-derive the whole answer from scratch, and the proactive
decider raise the same question in another room: three paths, one room, no
shared state.

WHO WRITES AND WHO READS — AND WHY THE GATE DOES NEITHER
--------------------------------------------------------
| writer | records | written by |
|---|---|---|
| `participation` | `spoke` / `silent`, with the disposition | `FirePath._determined` |
| `proactive_act` | `spoke` / `silent`, with the disposition | `FirePath._determined` |
| `proactive_decide` | `remind` / `decline`, and the note it was about | no writer (DEC-277) |
| `tag` | `replied` / `silent` / `failed`, with the disposition | `EngagementPipeline.run` |

`tag` is three-way and not two, because R-5's deliberate silence is a HEALTHY
outcome: calling it `failed` would make a working turn read as a broken one to
whatever renders the block next.

Three readers, all scoped to the UNIT, and each one excludes the writer
that answers ITS OWN recurring question — a decider reading back its own last
verdict is a decider agreeing with itself, which is not evidence about
anything. `PARTICIPATION_DECISION_WRITERS`, `PROACTIVE_DECISION_WRITERS` and
`PROACTIVE_ACT_DECISION_WRITERS` are those three
sets, and each is read TWICE: once as the `writers=` that bounds the query, once
by the block's own note. Both halves matter. The bound must count rows the
reader can USE — a room whose highest-volume writer fires far more often than
the others would otherwise fill all 20 rows by itself — and the note must name
the deciders the block actually carries, or a filtered-out decider's absence
reads as «that decider never ran».

**`RelevanceGate` neither writes nor reads, and that is a decision rather than
an omission.** Its own module docstring is «ONE call, transcript + standing, no
tools, no doctrine, no loop, JSON out — admission control and nothing else», and
a writer with a fail-soft path and a store read is not that shape. The gate
must not be able to answer «does Nora have something true to add?», and a block
of what she has raised carries that question in under a new label — a pending
`kind="proactive"` row has a `payload["note_title"]` on it. So the gate stays a
pure function on both sides of this table (DEC-179): it persists nothing
durable, and a `no_go` — which runs no turn — is invisible to every downstream
reader. `nora_relevance_gate_total` / `nora_relevance_gate_errors_total`
(R-23's split) are where a gate verdict is observable at all.

WHAT A ROW IS — AND WHAT IT IS NOT
----------------------------------
A row is **what the loop itself did**. It is not a note, not a memory, not a
fact, and not anything a person said. Every render says so in its own opening
tag, because a block a model cannot classify is a block it will classify wrong.

**No speech and NO MODEL TEXT goes in a row, ever.** `reason` is a code-derived
label from a closed set — a determination's `disposition`, a tag turn's
disposition — and the one model-authored string that
could have reached this table, the proactive decide turn's `why` (that turn
is gone, DEC-277), is deliberately not stored: it is written ABOUT a note and
would carry note titles into a block whose own note says it holds none. Her
draft, her reply and the room's words stay where they already are (the audit
rows, the determination log), and `REASON_MAX_CHARS` is enforced in three
places rather than trusted to one.

**No PERSON or ROOM identifier goes in a render, ever** (R-18/R-28). A row
carries `conversation_id` so it can be READ back; `render_decisions` never puts
it in the string. A room appears under its operator-authored policy LABEL or
under nothing at all, and `writer`/`verdict` are closed vocabularies this module
owns. A note's own id IS rendered, as a marker meaning «this decision was about
a tracked note» — the block says so, and says it cannot be looked up, because an
id a model believes is resolvable is worse than no id.

FAIL-SOFT, AND WHY THAT IS THE WHOLE POINT OF `DecisionLogRecorder`
-------------------------------------------------------------------
A log write must NEVER fail a turn. The four writers sit inside a reply slot, a
fire turn and a Dreamer pass; a store that stops answering must cost a thinner
prompt on the next decision, never a dropped reply. So the write
is not a store call at a call site — it is `DecisionLogRecorder.record`, which
bounds the call, swallows every exception, counts it, and returns `None`. One
object, one place to audit, and no call site holding an `except Exception` of
its own that the next edit can narrow by accident.

The READ is the same shape for the same reason, and it is bounded twice: by a
wall clock (the store may hang) and by `RECENT_LIMIT` + `RECENT_WINDOW_MS` (the
store may answer with a year of rows).

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
`nora.domain.media_join` is the precedent for a domain module that owns a
timeout and a metrics Protocol; the alternative is a copy of this logic in each
of the packages that writes a row, which sit at four different layers of the
import contract.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, get_args

from nora.domain.blocks import ContextBlock
from nora.domain.prompt import age as _age
from nora.domain.prompt import escape

log = logging.getLogger("nora.domain.decision_log")

#: Which decider wrote the row. A CLOSED vocabulary, and the literal is the
#: source both the CHECK constraint and the renders read — a hand-copied
#: vocabulary is what drifts (`nora_ingest_outcomes_total`'s precedent).
DecisionWriter = Literal[
    "participation",      # the participation turn's determination
    "proactive_decide",   # no writer (DEC-277); old rows remain
    "proactive_act",      # a kind="proactive" row's act turn
    "tag",                # the tag path, somebody addressed her
]

#: What the writer decided. Also closed, and deliberately NOT one vocabulary
#: per writer collapsed into a `bool`: «the tag path failed» and «she looked
#: again and held back» are different facts about a room and the next decider
#: has to be able to tell them apart.
#:
#: The Relevance Gate's `go`/`no_go` are deliberately absent (DEC-179): the
#: gate writes no row, and a closed vocabulary with an unreachable member is
#: decoration (every declared verdict must be glossed, and nothing glosses a
#: verdict nobody writes).
DecisionVerdict = Literal[
    "spoke", "silent",    # participation, proactive_act
    "remind", "decline",  # proactive_decide (no writer now; old rows)
    "replied", "failed",  # tag
]

WRITERS: tuple[str, ...] = get_args(DecisionWriter)
VERDICTS: tuple[str, ...] = get_args(DecisionVerdict)

#: What each reader asks the store for — ONE constant per reader, read by the
#: query AND by the block's own note, so the two cannot disagree.
#:
#: They are here rather than at the call sites because they are the same value
#: twice: the `writers=` that bounds the read, and the list `_note_for` glosses.
#: A hand-copied pair would drift, and the drift is invisible — the block would
#: keep claiming a decider it no longer carries, and its absence would read as
#: «that decider never ran» rather than as «that decider is not in here».
#:
#: Each reader excludes the writer that answers ITS OWN recurring question. A
#: decider reading back its own last verdict is a decider agreeing with itself:
#: it is the strongest signal in the block, it is not evidence about the room,
#: and on the ambient path it leans the way the existing silence bias already
#: leans.
PARTICIPATION_DECISION_WRITERS: tuple[DecisionWriter, ...] = (
    "proactive_decide", "proactive_act",
)
PROACTIVE_DECISION_WRITERS: tuple[DecisionWriter, ...] = (
    "participation", "tag",
)

#: The ACT turn's set, and the one place `proactive_act` is read by a
#: `proactive_act` reader. **That is the exception, and it is the whole point
#: of the constant.**
#:
#: `heartbeat._by_turn` coalesces only rows due in the SAME tick, so two
#: proactive rows about two different notes that come due apart are two turns.
#: The later turn's transcript window carries the earlier raise only as an
#: unlabelled line of Nora speech, with nothing saying it was a raise she had
#: made unprompted; this reader is what says so.
#:
#: **Why the exclusion rule does not bind here.** It bars a decider from
#: reading its own answer to its own recurring question — participation's last
#: verdict on this room, the decide pass's last pass over these notes. An act
#: turn is not that shape: it is a ONE-SHOT on one row about one note, its own
#: row's determination is written after this read (`FirePath._determined`), and
#: a sibling row's `spoke` is not its answer to its question but the fact that
#: the floor in this room was already taken, unprompted, N minutes ago. There
#: are also no words to echo — a `proactive_act` row carries `spoke`/`silent`
#: and a code-derived disposition, never model text (this module's own rule),
#: which is exactly what the decide pass's exclusion of itself turns on.
#:
#: **`proactive_decide` IS excluded, and that is the same rule honoured** (it
#: has no writer, DEC-277; its old rows still exist). The
#: decide row that scheduled THIS turn is the reason the turn is happening;
#: `ProactiveHeader` already puts it in front of her, in her own words, as
#: the intention to weigh. Handing it back a second time under a block of
#: what the loop DID would be the self-echo the rule is about, and it leans
#: toward speaking.
#:
#: `tag` is excluded for `PARTICIPATION_DECISION_WRITERS`' own reason, and the
#: bound is the second half of it: a tag is somebody asking, what it answered
#: is in the transcript underneath, and it is the highest-volume writer on the
#: table — 20 rows of it would evict the one `proactive_act spoke` row this
#: set exists to carry, in exactly the busy room where that matters most.
PROACTIVE_ACT_DECISION_WRITERS: tuple[DecisionWriter, ...] = (
    "participation", "proactive_act",
)

#: How much of a `reason` is kept. ONE number, not a store cap and a render cap:
#: two would be a number somebody has to look up.
#:
#: Sizing: a row is ~38 tokens at this width (151 characters with a label and
#: a note id). At the CEILING, 20 rows each carrying a
#: reason at this cap is ~3,700 characters, ~930 tokens; at the widths actually
#: written — a disposition or an error kind, ~10 characters — it is ~1,200
#: characters, ~300 tokens. That is the volatile tier of a fire-turn prompt,
#: beside a 40-row transcript, and it is affordable. The ceiling is the bound
#: that matters and it is stated rather than rounded away.
REASON_MAX_CHARS = 80

#: How many rows a read may answer with, and how far back it may reach. Both
#: bounds are on the READ rather than on the render, so a store that answers
#: with a year of rows costs a bounded query rather than a bounded string.
RECENT_LIMIT = 20
RECENT_WINDOW_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True, slots=True)
class DecisionRow:
    """One decision, as the next decider needs to read it.

    `note_id` is nullable because only the proactive decide turn (no writer
    now, DEC-277) decided ABOUT a note; every other writer decides about a room. `unit` may be empty
    for the same shape of reason `FirePath._unit_for` returns `None` — a
    room with no policy entry is a wiring gap, and guessing a unit would
    put one social unit's decisions in another's prompt.
    """

    id: str
    writer: DecisionWriter
    platform: str
    conversation_id: str
    unit: str
    note_id: str | None
    verdict: DecisionVerdict
    #: A CODE-DERIVED label and never model text: a determination's
    #: `disposition`, a tag turn's disposition. Empty is ordinary.
    #:
    #: The proactive decide turn's own `why` is NOT stored here: it is the one
    #: model-authored string that could land on this table, it is written ABOUT
    #: a note, and it would carry note titles into a prompt whose whole claim is
    #: that it holds no notes. `note_id` is what a reader needs from that row,
    #: and a reader that cannot resolve an id has no business being handed a
    #: title.
    reason: str
    ts: int

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("a DecisionRow needs an id")
        if self.writer not in WRITERS:
            raise ValueError(
                f"{self.writer!r} is not a decision writer; one of {WRITERS}"
            )
        if self.verdict not in VERDICTS:
            raise ValueError(
                f"{self.verdict!r} is not a decision verdict; one of {VERDICTS}"
            )
        if not self.platform.strip():
            raise ValueError("a DecisionRow needs a platform")
        if not self.conversation_id.strip():
            raise ValueError("a DecisionRow needs a conversation_id")
        if len(self.reason) > REASON_MAX_CHARS:
            raise ValueError(
                f"a reason is {len(self.reason)} characters, over the "
                f"{REASON_MAX_CHARS}-character cap; truncate at the recorder"
            )


class DecisionLogMetrics(Protocol):
    """`nora.obs.metrics.Metrics.inc`, structurally.

    A Protocol and not the class, for `nora.domain.media_join.JoinMetrics`'
    reason: `nora.domain` may not import `nora.obs`, and the two calls this
    module makes are both `nora_errors_total`, which is already declared.
    """

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None: ...


class _Store(Protocol):
    """The two verbs the recorder uses. Structural rather than
    `nora.domain.ports.DecisionLogStore` so this module does not import the
    port file it is a neighbour of."""

    async def append(self, *, row: DecisionRow) -> None: ...

    async def recent(
        self, *, conversation_id: str, since_ms: int, limit: int = ...,
    ) -> Sequence[DecisionRow]: ...

    async def recent_for_unit(
        self, *, unit: str, since_ms: int,
        writers: Sequence[str] = ..., limit: int = ...,
    ) -> Sequence[DecisionRow]: ...


class DecisionLogRecorder:
    """The ONE place a decision row is written or read, and the one place the
    fail-soft rule lives.

    Every call site holds this object rather than the store, so «can a log
    write fail a turn?» is a question about one method instead of four call
    sites. `record` and `read` both:

      * bound the store call on a wall clock — a store that hangs costs
        `timeout_seconds`, never the turn;
      * swallow EVERY exception, log it, and count it on `nora_errors_total`
        with its own `stage`, so a permanently failing log is loud in Prometheus
        and invisible to the room;
      * return `None` / `()` — there is no error channel for a caller to
        forget to check, because there is no decision a caller could make with
        one.

    This is R-27's three states pointed at a side-effect rather than at a tool
    result: the write either happened or is counted as not having happened, and
    nothing in between reaches the path being logged.

    `unit_of` is how a row gets its unit without four call sites each
    growing a `Policy` read. `None`, or a lookup that raises or answers `None`,
    leaves the column empty — the wiring gap `FirePath._unit_for` already
    refuses to guess past.
    """

    def __init__(
        self,
        *,
        store: _Store,
        metrics: DecisionLogMetrics,
        clock: Callable[[], datetime],
        unit_of: Callable[[str, str], str | None] | None = None,
        timeout_seconds: float = 2.0,
        limit: int = RECENT_LIMIT,
        window_ms: int = RECENT_WINDOW_MS,
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._clock = clock
        self._unit_of = unit_of
        self._timeout_seconds = timeout_seconds
        self._limit = limit
        self._window_ms = window_ms
        # See `_next_id`. One counter per recorder, and the composition root
        # builds exactly one recorder for the whole process.
        self._seq = itertools.count()

    def _next_id(self, ts: int) -> str:
        """`<ts>-<seq>-<random>`, and the middle field is load-bearing.

        Both stores order on `(ts, id)`, so rows written in the SAME
        millisecond are ordered by their id — and with a bare `uuid4` that is
        random, which means one pass's own decisions render in arbitrary order.
        A Dreamer `commit` walks an envelope of four items with no `await`
        that yields between them, so all four land on one millisecond, and
        «remind, then decline, because X» would render as often backwards as
        forwards. A block whose rows are out of order is the undated-transcript
        failure again, one axis over.

        The leading timestamp is the row's OWN `ts`, passed in rather than
        read again here: two clock reads a millisecond apart would put a
        different instant in the id than in the column, and the two would no
        longer sort alike. The
        counter breaks a same-millisecond tie by INSERTION order; the random
        tail keeps two processes from colliding on the primary key.

        Zero-padded because this sorts as TEXT, and 14 digits holds an
        epoch-millisecond past the year 5000. **The insertion-order guarantee
        is PROCESS-LOCAL**: the counter resets on restart and is per recorder,
        so two processes writing in the same millisecond fall back to the
        random tail and order arbitrarily between them. That is the honest
        scope of the claim — one process writes a pass's own envelope, which is
        the case the guarantee exists for.
        """
        return f"{ts:014d}-{next(self._seq):09d}-{uuid.uuid4().hex[:12]}"

    async def record(
        self,
        *,
        writer: DecisionWriter,
        platform: str,
        conversation_id: str,
        verdict: DecisionVerdict,
        reason: str = "",
        note_id: str | None = None,
    ) -> DecisionRow | None:
        """Append one row. Never raises, never blocks past `timeout_seconds`.

        Returns the row for a test to assert on; every caller on the reply
        path ignores it.
        """
        try:
            # INSIDE the try, both of them. `record`'s contract is that it never
            # raises; a clock read or an id mint left outside would make that a
            # promise about the CALLER's clock rather than about this method,
            # and the four «a decision log that is down never fails a turn»
            # tests would be asserting something the code does not hold.
            ts = int(self._clock().timestamp() * 1000)
            row = DecisionRow(
                id=self._next_id(ts),
                writer=writer,
                platform=platform,
                conversation_id=conversation_id,
                unit=self._unit(platform, conversation_id),
                note_id=note_id,
                verdict=verdict,
                # Truncated HERE, so `DecisionRow` can refuse an over-long one
                # and the CHECK can refuse whatever bypasses the type. The cap
                # is a render budget, so silently trimming is the right
                # direction: a decision that happened, recorded short, beats a
                # decision that was not recorded because its clause was long.
                reason=reason.strip()[:REASON_MAX_CHARS],
                ts=ts,
            )
        except Exception:
            log.exception("decision log: refusing a malformed row (%s)", writer)
            self._count("decision_log_row", platform)
            return None
        try:
            await asyncio.wait_for(
                self._store.append(row=row), timeout=self._timeout_seconds,
            )
        except Exception:
            log.exception(
                "decision log write failed (%s/%s); the turn is unaffected",
                writer, verdict,
            )
            self._count("decision_log_write", platform)
            return None
        return row

    async def read_unit(
        self, *, platform: str, unit: str,
        writers: Sequence[DecisionWriter] = (),
    ) -> tuple[DecisionRow, ...]:
        """**The primary read**: every room of one unit, over the window,
        newest-LAST. `()` on any failure.

        **Unit and not room, and that is what makes the log useful.** A
        `kind="proactive"` row fires into the unit's HOME room and nowhere
        else, and the home room is one platform; the thread it is about runs
        wherever the group happens to be talking. Those can be different rooms
        on different platforms, so a reader scoped to «this room, last 24
        hours» sees nothing, and the duplicate the log exists to prevent goes
        through anyway.
        The room a decision was made in is rendered as a LABEL instead, which
        is the information a reader actually needs from it.

        Newest-LAST because the block is read top-to-bottom beside a transcript
        that is also oldest-first (`RecordStore.recent`'s order, pinned by
        `tests/contract/test_audit_store_contract.py`). One document, one
        direction of time: a reversed block would be judged against a
        conversation read backwards.

        **`writers` is pushed into the QUERY; filtering after the read is the
        defect this argument exists to prevent.** Every reader wants a subset
        (each excludes its own writer) and the bound is `RECENT_LIMIT` rows. A
        busy room's dominant writer can out-produce every other writer by an
        order of magnitude; filtered afterwards, its rows from the last half
        hour would evict every other writer's and the reader would render NO
        BLOCK, silently, in exactly the busy room where it matters.

        So the bound has to be `RECENT_LIMIT` READABLE rows. An empty `writers` means every
        writer, which is what a caller with no filter means.

        `platform` is the metric label only — a read failure has to be counted
        somewhere, and a unit is not a platform.

        **A blank `unit` answers nothing, fail-closed.** An unmapped room
        writes `unit=""` deliberately (the wiring gap this module refuses
        to guess past), so `""` is not a unit — it is the POOL of every
        unmapped room in every social unit, and answering it would be the
        cross-unit leak this module's own argument forbids.
        """
        if not unit:
            return ()
        return await self._read(
            lambda since: self._store.recent_for_unit(
                unit=unit, since_ms=since,
                writers=tuple(writers), limit=self._limit,
            ),
            platform=platform,
        )

    async def _read(
        self,
        query: Callable[[int], Awaitable[Sequence[DecisionRow]]],
        *,
        platform: str,
    ) -> tuple[DecisionRow, ...]:
        try:
            # INSIDE the try, for `record`'s reason: a read that never raises
            # must not depend on the caller's clock not raising.
            since = int(self._clock().timestamp() * 1000) - self._window_ms
            rows = await asyncio.wait_for(
                query(since), timeout=self._timeout_seconds,
            )
        except Exception:
            log.exception("decision log read failed (%s); rendering no block", platform)
            self._count("decision_log_read", platform)
            return ()
        return tuple(rows)

    def _unit(self, platform: str, conversation_id: str) -> str:
        if self._unit_of is None:
            return ""
        try:
            unit = self._unit_of(platform, conversation_id) or ""
        except Exception:
            log.exception(
                "decision log: policy read failed for %s; leaving the "
                "unit empty rather than guessing", platform,
            )
            self._count("decision_log_unit", platform)
            return ""
        if not unit:
            # COUNTED, not just left empty. Every read is unit-keyed and
            # `read_unit("")` answers nothing by design, so a row written
            # with no unit is a row NOTHING WILL EVER READ — the wiring
            # gap degrades to a feature that is silently, permanently dark in
            # that room. `FirePath._unit_for` counts the same gap for the
            # same reason: «a block missing for the life of a deployment with
            # nothing saying so» is how `nora_peer_card_empty_total`'s lesson
            # was bought.
            log.warning(
                "decision log: no policy unit for %s/%s — the row is "
                "written but no reader will ever see it",
                platform, conversation_id,
            )
            self._count("decision_log_unit", platform)
        return unit

    def _count(self, stage: str, platform: str) -> None:
        try:
            self._metrics.inc("nora_errors_total", stage=stage, platform=platform)
        except Exception:      # pragma: no cover — a metric must not fail a turn either
            log.exception("decision log: counting the failure failed")


#: There is no per-render writer gloss here: the block's description belongs
#: to its TYPE, in `nora.domain.block_types` (the `writer` annotation lists all
#: four writers whatever the render holds, beside a description saying that a
#: decider absent from the rows is one this block does not carry). Second
#: person throughout, and that is not style: Nora IS the model reading it, and
#: a third-person «Nora's own loop» invites the model to read the block as
#: somebody else's record.


def decision_line(
    row: DecisionRow, *, now: datetime, label: str = "",
) -> str:
    """One row: how long ago, which room, which decider, what it decided, and
    whether it was about a note.

    **Six fields, and the ones that are absent are the decision.** There is no
    title, no body and no model-authored sentence here. Rendering
    `payload["note_title"]` would be the note tier leaking into a prompt
    through a side door, under a block that says it carries no notes — and on a
    deployment with `[notes] enabled = false` it would be note-derived content
    the turn has no other source for.

    **`note <id>` is a MARKER, not a lookup key.** `nora.language.note_render`
    emits `- <title> — <description> (about <date>)` and no id anywhere, so no
    reader can resolve it against the `<notes>` block. The id is for an
    OPERATOR joining a row to a note in the database. What the MODEL is told
    about it is only what is true — the block's `note` annotation says it means
    «this decision was about a tracked note» and that it cannot be looked up —
    because an id a model believes is lookupable is worse than no id at all.

    `label` is the operator-authored policy label for the room, never an id
    (R-18/R-28). It is how a unit-scoped block stays readable: the row
    that matters is often from the OTHER room.

    `reason` is a code-derived label — a fail-closed error kind or a
    determination's disposition — never model text; `DecisionRow` says why.
    """
    where = f"{escape(label)} " if label else ""
    parts = [f"({_age(row.ts, now)}) {where}{row.writer} {row.verdict}"]
    if row.note_id:
        parts.append(f"note {escape(row.note_id)}")
    reason = escape(row.reason.strip()[:REASON_MAX_CHARS])
    # Suppressed when it merely repeats the verdict. A determination that SPOKE
    # has `disposition == "spoke"`, so the unsuppressed line reads «spoke —
    # spoke»: a field that says nothing, in a block whose whole claim is that
    # every field on a row is worth reading. The dispositions that differ from
    # their verdict — `held_shadow`, `held_cooldown`, `declined` — are exactly
    # the ones that carry information, and they survive.
    if reason and reason != row.verdict:
        parts.append(reason)
    return " — ".join(parts)


def render_decisions(
    groups: Sequence[tuple[str, Sequence[DecisionRow]]],
    *,
    now: datetime,
    empty: str = "(nothing decided in this window)",
) -> ContextBlock:
    """`<loop_decisions>` — the rows, with an age on every one of them.

    **Named `loop_decisions` and not `decisions`** because a `<decisions>`
    block is her REMINDER rows — what she scheduled and how each fired. Two
    blocks with one name in one prompt is an undecidable input: the model
    cannot tell which is which, and the fix is never the instruction.

    **An age per row, not a timestamp.** Every question this block is read to
    answer is a question about WHEN — has this already been handled, did
    somebody just answer it, is that verdict stale — and a temporal judgement
    made against undated lines fails silently at a rate nothing reports.

    **No PERSON or ROOM identifier reaches the string** (R-18/R-28): no
    `conversation_id`, no phone number, no service id. A note's own id IS
    rendered — see `decision_line` for what that is for and what it is not. A
    room is named by its policy label or not at all,
    and `writer`/`verdict` are this module's own closed vocabularies. Every
    field is escaped anyway, exactly like `<peer_history>` one layer up.

    `groups` is `(label, rows)` so one call serves both readers, each of which
    labels a row with the room it was decided in.

    **THERE IS NO `writers` ARGUMENT.** The framing sentence belongs to the
    block's type and cannot vary per render, so the reading «a decider I do not
    see never ran» is closed in words instead: the description says a decider
    you do not see on any row is one this block does not carry.

    **What is model-authored in here: nothing.** Every `reason` any writer can
    produce is a code label from a closed set, and the decide turn's `why` is
    deliberately not stored. The per-field `escape` stays anyway — defence in
    depth on a block assembled from a database, where the next writer is one
    edit away from being less careful than these four.
    """
    lines: list[str] = []
    for label, rows in groups:
        lines.extend(decision_line(row, now=now, label=label) for row in rows)
    return ContextBlock(
        name="loop_decisions", body="\n".join(lines) if lines else empty,
    )
