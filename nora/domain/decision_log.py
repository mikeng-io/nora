"""The shared decision log — one row per ambient decision, and the block that
renders it back into the NEXT decider's prompt (issue #199).

WHAT THIS IS, AND WHY IT IS NEW
-------------------------------
Nora has three decision paths that can each take a room's floor, and until this
module none of them could see what the others had done:

  the Relevance Gate + the participation turn  (`nora.ingest.relevance_gate`,
                                                `FirePath.speak` kind="participation")
  the proactive decide + act turns             (`nora.language.proactive`,
                                                `FirePath.speak` kind="proactive")
  the tag turn                                 (`nora.engagement.pipeline`)

The gate persisted NOTHING durable — metrics and trace attributes only. The
proactive adaptor kept a log of its OWN reminder rows and nothing else. The
determination log (`nora.obs.determination`) is a log: written, never read back
into a prompt. So on 2026-09-10 participation answered a question at 16:43, the
tag turn one second later re-derived the whole answer from scratch and died, and
the proactive decider raised the same question in another room at 18:03. Three
paths, one room, no shared state.

WHO WRITES AND WHO READS — AND WHY THE GATE DOES NEITHER
--------------------------------------------------------
| writer | records | written by |
|---|---|---|
| `gate` | `go` / `no_go`, with the fail-closed reason | `ParticipationTrigger` |
| `participation` | `spoke` / `silent`, with the disposition | `FirePath._determined` |
| `proactive_act` | `spoke` / `silent`, with the disposition | `FirePath._determined` |
| `proactive_decide` | `remind` / `decline`, with its own `why` | `ProactiveAdaptor.commit` |
| `tag` | `replied` / `failed`, with the disposition | `EngagementPipeline.run` |

Two readers, and each one excludes its own writer — a decider reading back its
own last verdict is a decider agreeing with itself, which is not evidence about
anything. The participation turn reads the PROACTIVE rows for its room; the
proactive decide turn reads the gate, participation and tag rows for every room
of its workspace.

**`RelevanceGate` neither writes nor reads, and that is a decision rather than
an omission.** Its own module docstring is «ONE call, transcript + standing, no
tools, no doctrine, no loop, JSON out — admission control and nothing else», and
a writer with a fail-soft path and a store read is not that shape. Design 36 §6
is the sharper half: the gate must not be able to answer «does Nora have
something true to add?», and a block of what she has raised carries that
question in under a new label — a pending `kind="proactive"` row has a
`payload["note_title"]` on it. So the gate stays a pure function, and its
VERDICT is still durable, recorded one layer out by the trigger that called it.
That placement is also the only one that can record a NO_GO at all: a `no_go`
runs no turn, so nothing downstream ever sees it.

WHAT A ROW IS — AND WHAT IT IS NOT
----------------------------------
A row is **what the loop itself did**. It is not a note, not a memory, not a
fact, and not anything a person said. Every render says so in its own opening
tag, because a block a model cannot classify is a block it will classify wrong.

**No speech goes in a row, ever.** `reason` is a label or one short clause —
the gate's `error_kind`, a determination's `disposition`, a tag turn's
disposition, the decide turn's own `why`. Her draft, her reply and the room's
words stay where they already are (the audit rows, the determination log), and
`REASON_MAX_CHARS` is enforced in three places rather than trusted to one.

**No identifier goes in a render, ever** (R-18/R-28). A row carries
`conversation_id` so it can be READ back for one room; `render_decisions` never
puts it in the string. A room appears in the block under its operator-authored
policy LABEL or under nothing at all, and `writer`/`verdict` are closed
vocabularies this module owns.

FAIL-SOFT, AND WHY THAT IS THE WHOLE POINT OF `DecisionLogRecorder`
-------------------------------------------------------------------
A log write must NEVER fail a turn. The five writers sit inside a reply slot, a
fire turn, a quiescence tick and a Dreamer pass; a store that stops answering must
cost a thinner prompt on the next decision, never a dropped reply. So the write
is not a store call at a call site — it is `DecisionLogRecorder.record`, which
bounds the call, swallows every exception, counts it, and returns `None`. One
object, one place to audit, and no call site holding an `except Exception` of
its own that the next edit can narrow by accident.

The READ is the same shape for the same reason, and it is bounded twice: by a
wall clock (the store may hang) and by `RECENT_LIMIT` + `RECENT_WINDOW_MS` (the
store may answer with a year of rows).

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
`nora.domain.media_join` is the precedent for a domain module that owns a
timeout and a metrics Protocol — the alternative is a copy of this logic in
each of the packages that writes a row, and they sit at four different layers
of the import contract.
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

from nora.domain.prompt import age as _age
from nora.domain.prompt import escape

log = logging.getLogger("nora.domain.decision_log")

#: Which decider wrote the row. A CLOSED vocabulary, and the literal is the
#: source both the CHECK constraint and the renders read — a hand-copied
#: vocabulary is what drifts (`nora_ingest_outcomes_total`'s precedent).
DecisionWriter = Literal[
    "gate",               # the Relevance Gate's admission verdict
    "participation",      # the participation turn's determination
    "proactive_decide",   # the proactive adaptor's decide turn
    "proactive_act",      # a kind="proactive" row's act turn
    "tag",                # the tag path, somebody addressed her
]

#: What the writer decided. Also closed, and deliberately NOT one vocabulary
#: per writer collapsed into a `bool`: «the gate said no» and «she looked again
#: and held back» are different facts about a room and the next decider has to
#: be able to tell them apart.
DecisionVerdict = Literal[
    "go", "no_go",        # gate
    "spoke", "silent",    # participation, proactive_act
    "remind", "decline",  # proactive_decide
    "replied", "failed",  # tag
]

WRITERS: tuple[str, ...] = get_args(DecisionWriter)
VERDICTS: tuple[str, ...] = get_args(DecisionVerdict)

#: How much of a `reason` is kept. ONE number, not a store cap and a render cap:
#: two would be a number somebody has to look up, and the render is the only
#: consumer that matters. 80 characters is what keeps 20 rows under the ~300
#: prompt tokens design 36 allows the gate — the row costs ~11 tokens at this
#: width, and a reason is a label or one clause by construction.
REASON_MAX_CHARS = 80

#: How many rows a read may answer with, and how far back it may reach. Both
#: bounds are on the READ rather than on the render, so a store that answers
#: with a year of rows costs a bounded query rather than a bounded string.
RECENT_LIMIT = 20
RECENT_WINDOW_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True, slots=True)
class DecisionRow:
    """One decision, as the next decider needs to read it.

    `note_id` is nullable because only the proactive decide turn decides ABOUT
    a note; every other writer decides about a room. `workspace` may be empty
    for the same shape of reason `FirePath._workspace_for` returns `None` — a
    room with no policy entry is a wiring gap, and guessing a workspace would
    put one social unit's decisions in another's prompt.
    """

    id: str
    writer: DecisionWriter
    platform: str
    conversation_id: str
    workspace: str
    note_id: str | None
    verdict: DecisionVerdict
    #: A CODE-DERIVED label and never model text: the gate's `error_kind`, a
    #: determination's `disposition`, a tag turn's disposition. Empty is
    #: ordinary.
    #:
    #: The proactive decide turn's own `why` was written here until the third
    #: review of this branch and is not any more. It is the one model-authored
    #: string on this table, it is written ABOUT a note, and it therefore
    #: carries note titles into a prompt whose whole claim is that it holds no
    #: notes. `note_id` is what a reader needs from that row, and a reader that
    #: cannot resolve an id has no business being handed a title.
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

    async def recent_for_workspace(
        self, *, workspace: str, since_ms: int, limit: int = ...,
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

    `workspace_of` is how a row gets its workspace without four call sites each
    growing a `Policy` read. `None`, or a lookup that raises or answers `None`,
    leaves the column empty — the wiring gap `FirePath._workspace_for` already
    refuses to guess past.
    """

    def __init__(
        self,
        *,
        store: _Store,
        metrics: DecisionLogMetrics,
        clock: Callable[[], datetime],
        workspace_of: Callable[[str, str], str | None] | None = None,
        timeout_seconds: float = 2.0,
        limit: int = RECENT_LIMIT,
        window_ms: int = RECENT_WINDOW_MS,
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._clock = clock
        self._workspace_of = workspace_of
        self._timeout_seconds = timeout_seconds
        self._limit = limit
        self._window_ms = window_ms
        # See `_next_id`. One counter per recorder, and the composition root
        # builds exactly one recorder for the whole process.
        self._seq = itertools.count()

    def _next_id(self) -> str:
        """`<ts>-<seq>-<random>`, and the middle field is load-bearing.

        Both stores order on `(ts, id)`, so rows written in the SAME
        millisecond are ordered by their id — and with a bare `uuid4` that is
        random, which means one pass's own decisions render in arbitrary order.
        Not hypothetical: a Dreamer `commit` walks an envelope of four items
        with no `await` that yields between them, so all four land on one
        millisecond, and «remind, then decline, because X» would render as
        often backwards as forwards. A block whose rows are out of order is the
        undated-transcript failure again, one axis over.

        The leading timestamp keeps the id sorting the same way `ts` does, so
        the two never disagree; the counter breaks a same-millisecond tie by
        INSERTION order; the random tail keeps two processes from colliding on
        the primary key. Zero-padded because this sorts as TEXT.
        """
        ts = int(self._clock().timestamp() * 1000)
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
        now = self._clock()
        try:
            row = DecisionRow(
                id=self._next_id(),
                writer=writer,
                platform=platform,
                conversation_id=conversation_id,
                workspace=self._workspace(platform, conversation_id),
                note_id=note_id,
                verdict=verdict,
                # Truncated HERE, so `DecisionRow` can refuse an over-long one
                # and the CHECK can refuse whatever bypasses the type. The cap
                # is a render budget, so silently trimming is the right
                # direction: a decision that happened, recorded short, beats a
                # decision that was not recorded because its clause was long.
                reason=reason.strip()[:REASON_MAX_CHARS],
                ts=int(now.timestamp() * 1000),
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

    async def read_workspace(
        self, *, platform: str, workspace: str,
    ) -> tuple[DecisionRow, ...]:
        """**The primary read**: every room of one workspace, over the window,
        newest-LAST. `()` on any failure.

        **Workspace and not room, and that is what makes the log answer issue
        #199 at all.** A `kind="proactive"` row fires into the workspace's HOME
        room and nowhere else (39 D2/D3), and the home room is one platform;
        the thread it is about runs wherever the group happens to be talking.
        In the measured incident those were different rooms on different
        platforms — so a reader scoped to «this room, last 24 hours» sees
        nothing, and the duplicate it exists to prevent goes through anyway.
        The room a decision was made in is rendered as a LABEL instead, which
        is the information a reader actually needs from it.

        Newest-LAST because the block is read top-to-bottom beside a transcript
        that is also oldest-first (`RecordStore.recent`'s order, pinned by
        `tests/contract/test_audit_store_contract.py`). One document, one
        direction of time: the Relevance Gate's renderer reversed its
        transcript once on a docstring that claimed otherwise, and every
        verdict would have been judged on a conversation read backwards.

        `platform` is the metric label only — a read failure has to be counted
        somewhere, and a workspace is not a platform.
        """
        return await self._read(
            lambda since: self._store.recent_for_workspace(
                workspace=workspace, since_ms=since, limit=self._limit,
            ),
            platform=platform,
        )

    async def read(
        self, *, platform: str, conversation_id: str,
    ) -> tuple[DecisionRow, ...]:
        """The NARROW read: one room. Same window, same bound, same order.

        Kept for the reader that genuinely means one room — a turn asking «what
        happened HERE in the last few minutes» — and used by nothing today.
        `read_workspace` is what both prompt renders call; see its docstring
        for why the narrow scope cannot answer the question this log exists
        for.
        """
        return await self._read(
            lambda since: self._store.recent(
                conversation_id=conversation_id, since_ms=since,
                limit=self._limit,
            ),
            platform=platform,
        )

    async def _read(
        self,
        query: Callable[[int], Awaitable[Sequence[DecisionRow]]],
        *,
        platform: str,
    ) -> tuple[DecisionRow, ...]:
        since = int(self._clock().timestamp() * 1000) - self._window_ms
        try:
            rows = await asyncio.wait_for(
                query(since), timeout=self._timeout_seconds,
            )
        except Exception:
            log.exception("decision log read failed (%s); rendering no block", platform)
            self._count("decision_log_read", platform)
            return ()
        return tuple(rows)

    def _workspace(self, platform: str, conversation_id: str) -> str:
        if self._workspace_of is None:
            return ""
        try:
            return self._workspace_of(platform, conversation_id) or ""
        except Exception:
            log.exception(
                "decision log: policy read failed for %s; leaving the "
                "workspace empty rather than guessing", platform,
            )
            self._count("decision_log_workspace", platform)
            return ""

    def _count(self, stage: str, platform: str) -> None:
        try:
            self._metrics.inc("nora_errors_total", stage=stage, platform=platform)
        except Exception:      # pragma: no cover — a metric must not fail a turn either
            log.exception("decision log: counting the failure failed")


#: What the block says it is. Stated once, here, because it is the whole
#: measurement: AGENTS.md's rule is that a block handed to a model is evidence
#: only when the model can tell WHAT IT IS READING, and the 2026-09-09/10
#: finding was that telling a model what it CAN DO made the same gate
#: measurably worse while telling it what it is READING fixed it. So this note
#: describes the rows and their axis of time and stops — there is no verb in it.
_NOTE = (
    "what Nora's own ambient loop already did here, oldest first, over the last "
    "24 hours. Each line is one decision by one of her deciders - gate (was "
    "this room worth a reply at all), participation (an untagged room she "
    "could have spoken into), tag (somebody addressed her), proactive_decide "
    "and proactive_act (something she raised unprompted) - with how long ago "
    "it was and one short clause of its own reasoning. It is NOT notes, NOT "
    "memory, and NOT anything a person said: it is the record of her own "
    "turns. A record of the past, not an instruction."
)


def decision_line(
    row: DecisionRow, *, now: datetime, label: str = "",
) -> str:
    """One row: how long ago, which room, which decider, what it decided, and
    whether it was about a note.

    **Six fields, and the ones that are absent are the decision.** There is no
    title, no body and no model-authored sentence here. A note appears as
    `note <id>` and nothing else — the reader that needs to know WHICH note has
    `<notes>` in the same prompt to resolve it against, and the reader that
    does not have `<notes>` (a deployment with `[notes] enabled = false`) must
    not be handed note-derived content it has no other source for. Rendering
    `payload["note_title"]` would be exactly that: the note tier leaking into a
    prompt through a side door, under a block that says it carries no notes.

    `label` is the operator-authored policy label for the room, never an id
    (R-18/R-28). It is how a workspace-scoped block stays readable: the row
    that matters is often from the OTHER room.

    `reason` is a code-derived label — a fail-closed error kind or a
    determination's disposition — never model text; `DecisionRow` says why.
    """
    where = f"{escape(label)} " if label else ""
    parts = [f"({_age(row.ts, now)}) {where}{row.writer} {row.verdict}"]
    if row.note_id:
        parts.append(f"note {escape(row.note_id)}")
    reason = escape(row.reason.strip()[:REASON_MAX_CHARS])
    if reason:
        parts.append(reason)
    return " — ".join(parts)


def render_decisions(
    groups: Sequence[tuple[str, Sequence[DecisionRow]]],
    *,
    now: datetime,
    empty: str = "(nothing decided in this window)",
) -> str:
    """`<loop_decisions>` — the rows, with an age on every one of them.

    **Named `loop_decisions` and not `decisions`** because
    `nora.language.proactive` already renders a `<decisions>` block, and that
    one is her REMINDER rows — what she scheduled and how each fired. Two
    blocks with one name in one prompt is exactly the undecidable input this
    repo has already paid for: the model cannot tell which is which, and the
    fix is never the instruction.

    **An age per row, not a timestamp.** Every question this block is read to
    answer is a question about WHEN — has this already been handled, did
    somebody just answer it, is that verdict stale — and the gate's own
    measured lesson is that a temporal judgement made against undated lines
    fails silently at a rate nothing reports. Rendering an age per transcript
    row cut that gate's error rate by two thirds over 40 labelled cases.

    **No identifier reaches the string** (R-18/R-28): no `conversation_id`, no
    `note_id`, no person. A room is named by its policy label or not at all,
    and `writer`/`verdict` are this module's own closed vocabularies. What is
    model-authored is `reason`, so the block is marked untrusted and every
    field is escaped, exactly like `<self_card>` one layer up.

    `groups` is `(label, rows)` so one call serves both readers: the gate
    passes a single group with an empty label (it judges one room), the
    proactive pass passes one group per room of its workspace.
    """
    lines: list[str] = []
    for label, rows in groups:
        lines.extend(decision_line(row, now=now, label=label) for row in rows)
    return (
        f'<loop_decisions untrusted="true" note="{escape(_NOTE)}">\n'
        + ("\n".join(lines) if lines else empty)
        + "\n</loop_decisions>"
    )
