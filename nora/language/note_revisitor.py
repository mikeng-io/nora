"""The note tier's REVISITOR — the notebook's negative measurement.

The curator acts across notes; this adaptor acts on one note's own question:
**has its moment come, and did anything answer it?** It is the only thing in the
tier that RETIRES a note, and retirement is irreversible in the direction that
matters — a note swept away while its question is still open is a tracked
uncertainty silently stopped being tracked, and nothing downstream can tell that
apart from a question that got answered.

So the whole module is built around one refusal:

> **If it cannot tell whether a note's closing condition was met or the note
> merely went quiet, it writes NOTHING and the note stays.**

That is R-27 at the tier's own grain — «I could not check» is never «there was
nothing to find» — and every state below is arranged so that the *default* is
the note surviving, never the note being retired.

## The three states, and the code fact under each

`NoteSupersedeReason` gives the tier exactly three words, and two of them are
this adaptor's:

**the closing condition was MET** -> `retired-closed`. Decidable because the
model must cite a message AND that message must be in the evidence it was
shown.

**the moment PASSED and nothing answered it** -> `retired-expired`. Decidable
because the anchor date is behind `now_ms` — arithmetic, not opinion — AND
there was real evidence for the question to have gone unanswered *in*.

**anything else** -> no call is made at all, and the note stands.

`merged` is the curator's and is never written here: merging is a judgement
across notes, and this adaptor sees one note at a time by construction.

**Both retirements require evidence to EXIST.** A room that has said nothing
since the note was written cannot establish that a question went unanswered — it
establishes that nobody has spoken. Treating silence as an answer is the exact
substitution this tier's whole design refuses, and it is why `retired-expired`
is not reachable from a passed anchor date alone.

## The envelope carries an OPAQUE HANDLE, never a note id

**This is the defect that made the whole tier inert, and it is worth stating
before the design.** `DreamerRuntime` runs `nora.agent.dream_screen.screen`
over the model's raw envelope items **before** `accept` is called, and that
screen walks every string reachable in an item and drops the WHOLE item on a
match. A note id is `str(uuid4())`, `egress._UUID` matches any hyphenated UUID,
and an earlier revision of this module asked the model to echo `note_id` back —
so **every item was screened, always**, and the only trace was a generic
`nora_dream_screened_total{reason="identifier"}`. The tier's own five counters
showed candidates climbing and verdicts, refusals and retirements flat, which
reads as a model that stopped answering. This adaptor was the first to put a
UUID in an envelope item, so no earlier tier had tripped it.

So the prompt renders a **per-tick ordinal** — `1`, `2`, `3` — and `_accept_one`
maps it back through the asked-set. The map had to exist anyway (see below), so
this costs nothing, and it takes the UUIDs out of the prompt as a second win.

**The schema is deliberately minimal for the same reason.** Every
model-authored string in an item is a chance for the screen to drop a correct
verdict — a `why` field quoting a phone number out of the evidence would
silently kill the retirement it was explaining. The citation IS the
explanation, so there is no free-text field. **Widening the screen for this
tier was rejected**: it is a shared security rule, and widening it for one
caller is how such a rule dies.

## Why the model does not get to decide alone

The verdict is semantic and belongs to a model. The GROUNDING is not, and this
module owns it — the same split `nora.language.observer` draws for the language
tier, and the same one `_extract` has always drawn for a hallucinated
`message_id`:

- a `retired-closed` verdict must name a message id, and that id must be in the
  evidence rendered for *that note*. A model that cites a message it was never
  shown is inventing the answer it wanted;
- a `retired-expired` verdict is checked against the CLOCK, not against the
  model's opinion of the clock;
- a note id the model returns that was not in the set it was asked about is
  refused outright — retiring an unrelated note is the worst thing this adaptor
  could do, and it is one hallucinated identifier away.

## What is deliberately NOT here

**`schedule_reminder`, and therefore the reaching-a-person half of the
revisitor** ([27](../../design/dreamer-agent-loop/27-note-dreamer.md) §3, D21).
D21 resolved that an adaptor may hold a verb that ENQUEUES even though it may
not hold one that DELIVERS — but the amendment lives in the design and NOT in
`nora.agent.dreamer.tool_subset_faults`, which still refuses every tool outside
the read-only ceiling. Building this half would mean widening what *every*
background adaptor may hold, which is a security-relevant change to a shared
rule and is not needed to retire a note. `tools` is therefore empty, which also
makes the workspace-safe restriction moot rather than argued around.

## Why a NOTE-tier module lives under `nora/language/`

Raised in review, and the answer is a constraint rather than a preference — so
it is written here rather than settled by moving the file.

**`nora/dreamer/` is not a Python package and cannot hold this.** It has no
`__init__.py`, nothing in the repo imports `nora.dreamer`, and it does not
appear in `pyproject.toml`'s layered-architecture contract at all. It holds two
directories of markdown — `doctrine/` and `objectives/` — and
`docker-compose.yml:263` bind-mounts it **read-only** into the container:

```
- ./nora/dreamer:/app/nora/dreamer:ro
```

That mount is what makes an operator's instruction edit hot-reload without a
rebuild, and it is exactly why code must not go there. A `.py` file baked into
the image under that path is **replaced at runtime by whatever is in the host
checkout** — so the running adaptor would be the working tree's, not the
image's, and an image built from a known-good commit would silently execute
code from a tree that has moved on. That reintroduces «never build from the
live checkout» as a permanent runtime property instead of a build-time mistake.
It would also place this module outside the import-boundary contract, which is
a larger hole than the one the move was meant to close.

**Both fact adaptors are here for the same reason**, with the argument stated
in `nora.language.fact_adaptor`'s own docstring: the layer contract forbids
`nora.agent` from holding tier logic and forbids `nora.language` from importing
`nora.agent`/`nora.doctrine`/`nora.config`, so an adaptor declares tool NAMES
and takes its caps through `__init__`. `nora.language` is where a tier's
adaptors sit today, and this one is not an exception to that.

**The concern behind the question is real and is not dismissed.** A note-tier
module under `nora/language/` is findable by a sweep over «what does
`[language]` own», and that is precisely the shape that hid the Dreamer clock's
coupling to `[language]` for weeks. What makes this one safe is that the
coupling is absent rather than merely documented: this adaptor's lifetime is
gated on `cfg.notes.enabled and note_store is not None`
(`nora.app.composition`), it reads no language config, and
`tests/test_composition.py::test_the_revisitor_needs_both_the_dreamer_and_the_notes_gate`
fails if that ever changes. **The directory name is not the enforcement; that
test is.**

If the adaptors should move out of `nora/language/`, all three should move
together into a package that exists, with the layers contract updated in the
same change. Moving this one alone would leave the fact adaptors behind and
make the convention harder to read, not easier.

## Keyed on a WORKSPACE, and what that costs

A note is workspace-scoped, so this adaptor is too — `uses_watermark = False`,
its input is the note table rather than a message batch, and it runs on every
tick with an empty `batch` (`FactAdaptor` is the standing precedent).

The cost is that no room-scoped TOOL is available to it. The evidence is
therefore assembled **in code**, from the note's own `established_in` — §4.2's
argument that selection stays where it can be capped and counted, rather than
being handed to a model as a tool. `established_in` is a routing key and the
workspace is already the disclosure boundary (DEC-105/R-49), so reading the room
a note was born in to judge that note is inside the boundary the note already
sits in.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, get_args

from nora.domain.models import TranscriptRow
from nora.domain.notes import Note, NoteSupersedeReason
from nora.domain.ports import DreamSink, Proposal
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

# How many notes one tick will judge. A ceiling on the PROMPT as much as on the
# work: each candidate carries its own evidence block, so this multiplies with
# `DEFAULT_EVIDENCE_MESSAGES_PER_NOTE` below into the size of one call.
DEFAULT_MAX_REVISITS_PER_TICK = 10

# Messages of `established_in` rendered as one note's evidence. `recent`
# returns the newest `limit` rows OLDEST-FIRST; they are then filtered to those
# actually postdating the note — a closing condition cannot have been met
# before the note existed, and rendering earlier traffic invites the model to
# answer from it — and to those that are not Nora's own.
#
# This cap is ALSO what `_covers` measures the anchor window against: a
# truncated window is a window that may be hiding the answer, which is why
# raising it is not merely a cost decision.
DEFAULT_EVIDENCE_MESSAGES_PER_NOTE = 40

# A `retired-closed` verdict must cite one of these; a note id must be one of
# those asked about. Both are joins back to something this process chose, never
# to something the model supplied.
_VERDICT_CLOSED = "closed"
_VERDICT_EXPIRED = "expired"
_VERDICT_UNRESOLVED = "unresolved"
_VERDICTS = (_VERDICT_CLOSED, _VERDICT_EXPIRED, _VERDICT_UNRESOLVED)

# A claim about a note whose VERDICT could not be read. Deliberately outside
# `_VERDICTS` — it is not a fourth answer, it is the absence of a readable one,
# and it exists only so such an item can still COLLIDE with a readable verdict
# for the same note. See `_accept_one`'s attribution split.
_VERDICT_UNREADABLE = "unreadable"

_BECAUSE = {
    _VERDICT_CLOSED: NoteSupersedeReason.RETIRED_CLOSED,
    _VERDICT_EXPIRED: NoteSupersedeReason.RETIRED_EXPIRED,
}
# The keys `_accept_one` reads out of one envelope item. Named here so a test
# can compare them against what the objective PROMISES — a rename on one side
# and not the other is a verdict silently refused as `unmeasured`, which reads
# as a model that stopped answering.
# Every value `_candidate` can put on `nora_note_revisit_candidates_total`'s
# `evidence` label.
#
# **DERIVED from the type, not written out beside it**, and that is the whole
# point. The first version of this was a hand-written tuple that nothing in the
# module referenced: `_candidate` assigned bare literals, so adding a fifth
# state kept every test green and the parity test below could only catch
# DELETION from the help text. The drift it was written to prevent — a state in
# the code and not in the spec, which is exactly how `self_only` got there — was
# still undetectable. **The fix for the silent-failure finding had the silent
# failure's own shape.**
#
# Two enforcers now, because this package is outside `make gate`'s mypy scope
# (issue #61) and a static-only guard would be a claim rather than a check:
# `EvidenceState` makes an undeclared literal a TYPE error, and
# `_Candidate.__post_init__` makes it a RUNTIME error in every test that builds
# a candidate.
EvidenceState = Literal["present", "absent", "self_only", "unavailable"]
EVIDENCE_STATES: tuple[str, ...] = get_args(EvidenceState)

# The keys `_accept_one` reads out of one envelope item. Named here so a test
# can compare them against what the objective PROMISES — a rename on one side
# and not the other is a verdict silently refused as `unmeasured`, which reads
# as a model that stopped answering.
_KEY_NOTE = "note"
_KEY_VERDICT = "verdict"
_KEY_CITED = "cited_message_id"
ITEM_KEYS: frozenset[str] = frozenset({_KEY_NOTE, _KEY_VERDICT, _KEY_CITED})

_MS_PER_DAY = 86_400_000


def _now_ms() -> int:
    """The default `clock` collaborator. Defined here, above the class, because
    it is a default ARGUMENT rather than a call — a module-level function is
    the smallest thing that satisfies `Callable[[], int]` without a
    module-level mutable singleton, which DEC-13 forbids."""
    import time

    return int(time.time() * 1000)


class _NoteStore(DreamSink, Protocol):
    """`SqlNoteStore`, seen from here: the tier's sink plus the one read this
    adaptor makes.

    A local structural type rather than a declared port, following
    `nora.language.fact_adaptor`'s `_FactStore`: `standing` has no
    `nora.domain.ports` Protocol today, and inventing one for a single consumer
    would commit a second implementation and a shared contract suite for a shape
    nothing else names. `DreamSink` IS a declared port and is inherited rather
    than restated, so `propose`/`supersede` here are the same contract the
    runtime holds every sink to.
    """

    async def standing(self, *, workspace: str) -> tuple[Note, ...]: ...


class _TranscriptSource(Protocol):
    """The one `RecordStore` read this adaptor makes — newest-first, capped.

    Narrowed to a single method for the reason `nora.app.runtime`'s own
    `_ConsolidationCursor` gives: the concrete store also hands out writes, and
    a background pass reaching one would be a defect with nothing to catch it.
    """

    async def recent(
        self, *, conversation_id: str, limit: int
    ) -> list[TranscriptRow]: ...


@dataclass(frozen=True)
class NoteRetirement:
    """One decided retirement — this adaptor's payload type.

    `note_id` is a note this process selected and rendered, never one the model
    named freely (`_accept_one` refuses an unknown id). `because` is already
    narrowed to `NoteSupersedeReason`'s vocabulary at construction, so `commit`
    cannot widen it by accident.

    There is no `replaced_by` field. A retirement here NEVER has a successor —
    a dinner that happened is not replaced by anything, and a question that got
    answered is not either. The port allows a null successor precisely so this
    tier does not have to mint a placeholder id to satisfy a signature; carrying
    a field that is always `None` would invite someone to fill it.
    """

    note_id: str
    because: str
    # The message that established it, for `retired-closed`; `None` for
    # `retired-expired`, whose ground is the clock rather than a citation.
    # Kept on the payload rather than discarded after validation because it is
    # the one thing that makes a wrong retirement reviewable afterwards.
    cited_message_id: int | None


@dataclass(frozen=True)
class _Verdict:
    """One item that PARSED and grounded — the unit `accept` resolves per note.

    Carries the verdict itself and not only the retirement it may imply,
    because `unresolved` has to take part in the contradiction check. An
    earlier revision returned `None` for it, so it never entered the grouping
    and `["unresolved", "closed"]` retired the note in either order — a model
    saying «I cannot tell» AND «it is closed» getting the answer that throws
    the note away.
    """

    note_id: str
    verdict: str
    # `None` when this verdict cannot ACT — `unresolved`, which decides nothing
    # by construction, and every verdict that failed grounding.
    #
    # **A verdict that failed grounding is still a CLAIM**, and that is the
    # line this type exists to draw. Grounding decides whether a claim may act,
    # never whether it was made. Two revisions in a row got this wrong in the
    # same direction: the first grouped only retirements, so `unresolved` could
    # not contradict; the second let `unresolved` in but still dropped every
    # REFUSAL, so `[expired-refused, closed-grounded]` retired the note on the
    # survivor. A model that says both things about one note has said «I cannot
    # tell which», and which of its two answers happened to survive a grounding
    # check is not a tie-break.
    retirement: NoteRetirement | None


@dataclass(frozen=True)
class _Candidate:
    """One note this tick will ask about, with the evidence it will be judged
    against. Built entirely in code — the model chooses nothing here."""

    # The per-tick ordinal the prompt renders and a verdict echoes back. NOT
    # the note id: a UUID in an envelope item is dropped by the Dreamer's
    # egress screen before `accept` ever runs — see the module docstring.
    handle: str
    note: Note
    evidence: tuple[TranscriptRow, ...]
    anchor_passed: bool
    # Whether the evidence window actually SPANS the moment the note is about.
    # `anchor_passed` says the moment has gone by; this says we were LOOKING
    # when it did. Without it, `retired-expired` needed only that the room had
    # said something — anything, at any time after the note was written — and a
    # room that fell silent in August would retire a note anchored to September
    # with no evidence whatsoever about September. See `_candidate`.
    anchor_covered: bool
    # Why `evidence` is what it is, as ONE value — «the room said nothing» and
    # «the record read failed» both arrive here as an empty tuple, and they are
    # different states. Carried rather than re-derived at each use, because the
    # first version of this counted both and produced 2 for one candidate: the
    # unavailable-collapsing-into-absent defect this module exists to refuse,
    # committed inside its own instrument.
    evidence_state: EvidenceState

    def __post_init__(self) -> None:
        # The runtime half of the guard — see `EVIDENCE_STATES`. `nora.language`
        # is outside the gate's mypy scope, so the `Literal` alone would be
        # enforced only by a by-hand run; this fails in every test that builds a
        # candidate, which is all of them.
        if self.evidence_state not in EVIDENCE_STATES:
            raise ValueError(
                f"evidence_state {self.evidence_state!r} is not one of "
                f"{EVIDENCE_STATES} — add it to EvidenceState AND to "
                "nora_note_revisit_candidates_total's help text"
            )

    @property
    def message_ids(self) -> frozenset[int]:
        return frozenset(row.id for row in self.evidence)


def _anchor_bounds_ms(anchor_date: int) -> tuple[int, int] | None:
    """The FIRST and LAST instants of a `YYYYMMDD` anchor, in epoch ms, or
    `None` if the value is not a date this can read.

    Both, not just the end — but **only the end is read**. The start was once
    what `anchor_covered` measured the window against, and that was the defect
    `_covers` documents: the reference point is the NOTE, not the anchor. The
    start is kept because the pair is the honest reading of a `YYYYMMDD` value
    and a caller may yet want it; `bounds[0]` has no reader today, and this
    sentence says so rather than describing a use that was removed. Fourth
    stale statement retired from this file.

    END of the day, not the start: a note anchored to the 15th is not stale at
    00:00 on the 15th. Computing it from the calendar rather than from a
    stored timestamp because `anchor_date` IS an integer date — the tier chose
    that shape deliberately, and re-deriving a moment from it is this module's
    job rather than the store's.

    Returns `None` rather than raising on a malformed value, and the caller
    treats `None` as «not decidable» — an unreadable anchor must leave the note
    standing, not expire it.

    **UTC, while the deployment runs in HKT — stated rather than fixed.** A
    note anchored to the 9th therefore only reads as passed at 08:00 local on
    the 10th. That is the CONSERVATIVE direction (it retires later, never
    earlier, so it cannot expire a note whose evening has not happened yet) and
    it is why this is recorded instead of changed: picking a timezone is a
    decision about what a note's DATE MEANS to the people writing it, and that
    is the operator's, not this module's. *The reading that would settle it:*
    a note whose anchor day matters to within a few hours — a same-day
    deadline rather than a dinner.
    """
    import datetime as _dt

    try:
        day = _dt.date(
            anchor_date // 10_000, anchor_date // 100 % 100, anchor_date % 100,
        )
    except (ValueError, TypeError):
        return None
    start = int(
        _dt.datetime.combine(day, _dt.time.min, tzinfo=_dt.UTC).timestamp() * 1000
    )
    return start, start + _MS_PER_DAY - 1


def _render_note(candidate: _Candidate) -> str:
    """One note and its evidence, as the model sees them.

    The evidence is rendered as `[<id>] <speaker>: <body>` — the same format
    `nora.language.observer._build_prompt` uses, and deliberately so: the id in
    the bracket is what a `closed` verdict has to cite, and a format the rest of
    the repo already uses is one fewer thing for a model to get creatively
    wrong.

    Notes with NO evidence still render, with the absence stated in words. The
    alternative — dropping them from the prompt — would hide from the model
    that the question exists, and hide from a reader of the trace that this tick
    considered it and could not decide. An absence that is stated can be
    reasoned about; one that is omitted cannot.
    """
    lines = [
        f"### note {candidate.handle}",
        f"title: {candidate.note.title}",
        f"uncertain: {candidate.note.uncertainty}",
        f"closing condition: {candidate.note.closing_condition}",
    ]
    if candidate.note.anchor_date is not None:
        lines.append(
            f"anchored to: {candidate.note.anchor_date}"
            f"{' (this date has passed)' if candidate.anchor_passed else ''}"
        )
    if not candidate.evidence:
        # Two absences, two sentences, because one of them would be a LIE as
        # the other. `absent` really is «nobody has spoken since this note»;
        # `self_only` is «the group has spoken, and none of what I can see is
        # theirs» — Nora's own traffic can fill the window on its own. Telling
        # the model the room has been quiet when it has not is a false premise
        # handed to the thing being asked to judge.
        #
        # `unavailable` deliberately renders as `absent`'s sentence: the
        # model's job is the same either way — decide nothing — and telling it
        # «the record was unreachable» invites it to reason about our
        # infrastructure instead of about the note. THAT distinction is for the
        # operator and lives on the counter.
        lines.append(
            "evidence: NONE — nothing the group has said since this note was "
            "written is in the messages I can see."
            if candidate.evidence_state == "self_only" else
            "evidence: NONE — nothing has been said in that conversation since "
            "this note was written."
        )
        return "\n".join(lines)
    lines.append("evidence, newest last:")
    for row in candidate.evidence:
        # `person_name` first, matching `nora.language.observer._build_prompt`:
        # it is the operator-authored name the roster knows somebody by, and
        # falling straight to the platform's `sender_name` renders whatever
        # display name they happen to be using today.
        speaker = row.person_name or row.sender_name or row.sender_id
        lines.append(f"[{row.id}] {speaker}: {row.body or ''}")
    return "\n".join(lines)


class NoteRevisitor:
    """Satisfies `nora.domain.ports.DreamAdaptor`. See the module docstring for
    the three states and for what is deliberately not built.

    Collaborators through `__init__` only, per the test-seam policy. `metrics`
    is optional and defaulted, following this repo's standing pattern for a
    decoration-counter collaborator — a caller with no metrics wired still gets
    correct behaviour.
    """

    name = "note-revisit"
    key_kind = "workspace"
    uses_watermark = False
    objective = "note-revisit"
    envelope_key = "revisits"
    # EMPTY, and that is a decision rather than an omission — see the module
    # docstring's "What is deliberately NOT here". It also makes the
    # workspace-safe restriction moot: there is no room-scoped tool to argue
    # about, because there is no tool.
    tools: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        notes: _NoteStore,
        record: _TranscriptSource,
        max_revisits_per_tick: int = DEFAULT_MAX_REVISITS_PER_TICK,
        evidence_messages_per_note: int = DEFAULT_EVIDENCE_MESSAGES_PER_NOTE,
        metrics: Metrics | None = None,
        clock: Callable[[], int] = _now_ms,
    ) -> None:
        self._notes = notes
        self._record = record
        self._max_revisits = max_revisits_per_tick
        self._evidence_limit = evidence_messages_per_note
        self._metrics = metrics
        # A COLLABORATOR, not a test hook. `inputs` has no `now_ms` parameter
        # (`commit` does), and «has this note's moment passed» is one of the two
        # grounds for an irreversible action — so it has to be testable ON the
        # boundary, not merely near it. Without this, fixtures are dated against
        # the wall clock and a boundary case cannot be written at all; worse,
        # a date chosen to be safely in the future silently stops testing
        # anything the day it arrives, and it stops by rendering nothing, which
        # reads as passing.
        self._clock = clock
        # What this tick ASKED about: `key -> handle -> candidate`, the join a
        # verdict is checked against in `accept`.
        #
        # **Keyed by `key` and not a bare attribute**, following
        # `nora.language.fact_adaptor`'s `_message_ids` and its stated reason:
        # `dream_loop` dispatches each key as its own task and its per-(adaptor,
        # key) `running` lock makes ONE key's inputs/accept pair
        # non-overlapping, while nothing makes two DIFFERENT keys' pairs
        # non-overlapping. A bare attribute is wiped by a sibling workspace
        # mid-flight, and the live `policy.toml` has two — so workspace A's
        # grounded verdict would be dropped and counted `unknown_note`, a
        # WIRING GAP reported as a model fault, inside the instrument built to
        # refuse exactly that. Bounded by the number of workspaces, which
        # `policy.toml` fixes at startup.
        #
        # One key's entry is REPLACED wholesale by its own `inputs` call, never
        # merged: a handle from a previous tick is exactly as unaskable as one
        # the model invented, because the evidence that justified asking is
        # gone and the ordinals have been reused.
        self._asked: dict[str, dict[str, _Candidate]] = {}

    # -- inputs -------------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[TranscriptRow]) -> str:
        """The due notes and their evidence, assembled in code.

        `batch` is always empty here (`uses_watermark = False`) and is accepted
        and ignored — the port's shape rather than a surprise.

        **Selection is arithmetic and happens BEFORE the model sees anything.**
        A note is due when its anchor date has passed or its `revisit_at` has
        been reached. A note with neither is not due: it is an open question
        with no moment attached, and asking a model to decide whether an
        undated question has gone stale is asking it to invent a deadline.
        """
        asked: dict[str, _Candidate] = {}
        standing = await self._notes.standing(workspace=key)
        self._gauge("nora_note_revisit_standing", float(len(standing)), workspace=key)
        now_ms = self._clock()
        due = [n for n in standing if _is_due(n, now_ms=now_ms)]
        if len(due) > self._max_revisits:
            # NAMED, not silent. A tick that judged ten of forty due notes and
            # said so is a backlog; one that said nothing is indistinguishable
            # from a notebook with ten due notes in it.
            log.info(
                "note revisit: %d notes due in workspace %s, judging %d this "
                "tick (max_revisits_per_tick)", len(due), key, self._max_revisits,
            )
            self._inc("nora_note_revisit_deferred_total", value=len(due) - self._max_revisits)
            due = due[: self._max_revisits]

        rendered: list[str] = []
        for ordinal, note in enumerate(due, start=1):
            candidate = await self._candidate(
                note, now_ms=now_ms, handle=str(ordinal),
            )
            asked[candidate.handle] = candidate
            # ONCE per candidate, from the single value that decided it.
            self._inc(
                "nora_note_revisit_candidates_total",
                evidence=candidate.evidence_state,
            )
            rendered.append(_render_note(candidate))
        # Assigned at the END, as one replacement rather than mutated in place,
        # so a concurrent `accept` for this key either sees the whole previous
        # tick or the whole new one — never a half-built map missing the note it
        # is being asked about.
        self._asked[key] = asked
        return "\n\n".join(rendered)

    async def _candidate(
        self, note: Note, *, now_ms: int, handle: str,
    ) -> _Candidate:
        """One note plus the evidence it will be judged against.

        Filtered to messages that POSTDATE the note: a closing condition cannot
        have been met before the note existed, and rendering earlier traffic
        invites the model to answer from it. **No reordering happens** —
        `recent` already returns oldest-first, `_render_note` renders in that
        order, and `_covers` reads `evidence[0]` as the oldest. This docstring
        claimed the opposite for three revisions: the third stale ordering
        statement this file has carried, describing a bug that was fixed while
        the sentence outlived it.

        A read that FAILS leaves the candidate with no evidence rather than
        propagating — which puts the note on the «cannot tell» path, where it
        survives. That is the correct direction for this tier: an unreachable
        record must never be able to cause a retirement.
        """
        state: EvidenceState = "present"
        try:
            rows = await self._record.recent(
                conversation_id=note.established_in, limit=self._evidence_limit,
            )
        except Exception:
            log.exception(
                "note revisit: evidence unavailable for note %s (conversation "
                "%s); the note is left standing", note.id, note.established_in,
            )
            rows = []
            state = "unavailable"
        # `RecordStore.recent` returns the newest `limit` rows **OLDEST-FIRST**
        # — its own docstring says so and
        # `tests/contract/test_audit_store_contract.py::
        # test_recent_returns_rows_oldest_first` pins it on both
        # implementations. An earlier revision reversed them here, which put
        # the prompt in newest-first order under a header reading "newest
        # last": the model then read the OLDEST message as the latest word and
        # could close a note by citing a booking that a later message had
        # already cancelled — a citation that passes every grounding check,
        # retiring a note in the unrecoverable direction. Do not reorder.
        #
        # `is_nora` rows are excluded HERE because `recent` does not exclude
        # them (`since` does, in the query, and its docstring says why: "so a
        # caller can never forget to filter Nora's own words out of the
        # evidence the Dreamer derives from"). Her saying «I'll book Yardbird
        # for the 9th» is an intention she stated, not the group settling
        # anything — closing a note on it is self-attestation deciding a
        # retirement, which is the one thing no measure in this repo may rest
        # on.
        #
        # TRUNCATION is read BEFORE either filter, because it is a property of
        # what `recent` could return, not of what survived. `recent` gives the
        # newest `limit` rows of the WHOLE conversation; fewer than `limit`
        # means there is no older traffic at all, which is the only way to know
        # the window is not hiding something.
        truncated = len(rows) >= self._evidence_limit
        since = [row for row in rows if row.ts > note.created_at]
        evidence = tuple(row for row in since if not row.is_nora)
        if state != "unavailable" and not evidence:
            # `absent` means the room has genuinely said nothing since the note.
            # If it HAS spoken and every one of those rows was Nora's own, that
            # is a different state: her traffic can exhaust the window on its
            # own, and calling that «the room has been quiet» is `unavailable`
            # wearing `nothing_found` inside the counter built to keep them
            # apart. No retirement follows from either, so the direction is
            # safe; the honesty is the point.
            state = "self_only" if since else "absent"
        bounds = (
            _anchor_bounds_ms(note.anchor_date)
            if note.anchor_date is not None else None
        )
        return _Candidate(
            handle=handle,
            note=note,
            evidence=evidence,
            anchor_passed=bounds is not None and now_ms > bounds[1],
            anchor_covered=_covers(
                evidence, rows=rows, created_at=note.created_at,
                bounds=bounds, truncated=truncated,
            ),
            evidence_state=state,
        )

    # -- accept -------------------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """The MEASURE: the model's verdicts -> the retirements that survive
        grounding.

        Every branch that is not a clean, grounded decision returns nothing for
        that note, which leaves it standing. There is no path here that retires
        a note on a doubt.
        """
        asked = self._asked.get(key, {})
        # Grouped by NOTE before anything is decided, because a note's verdict
        # is a property of the note and not of an item's position in a list.
        # EVERY parsed verdict enters this map, `unresolved` included — see
        # `_Verdict`.
        by_note: dict[str, list[_Verdict]] = {}
        for item in items:
            verdict = self._accept_one(item, asked=asked)
            if verdict is not None:
                by_note.setdefault(verdict.note_id, []).append(verdict)

        out: list[NoteRetirement] = []
        for proposed in by_note.values():
            reasons = {v.verdict for v in proposed}
            if len(reasons) > 1:
                # **A model contradicting itself about one note is the
                # DEFINITION of «cannot tell which», and both answers throw the
                # note away.** An earlier revision de-duplicated first-come, so
                # `[closed, expired]` retired it as closed and the same two
                # items in the other order retired it as expired — an
                # irreversible decision resolved by list position. And a later
                # one grouped only RETIREMENTS, so `["unresolved", "closed"]`
                # was not a conflict at all and the note was retired: «I cannot
                # tell» and «it is closed» resolving to closed. Refused
                # outright, and the note stands.
                self._refuse("contradictory")
                continue
            # Same verdict twice is one decision stated twice, not a conflict.
            # Superseding twice would fire this module's own `result="no_rows"`
            # alarm on the second call — a false positive on the counter built
            # to make a real silent no-op visible.
            resolved = proposed[0]
            if resolved.verdict == _VERDICT_UNREADABLE:
                # Every item for this note was unreadable. Already counted as
                # `unmeasured`, and it is NOT a verdict — counting it on
                # `verdicts_total` would put a non-answer in the series that
                # measures answers.
                continue
            # Counted HERE rather than in `_accept_one`, which runs per ITEM:
            # two items for one note incremented `verdicts_total` twice and
            # then produced one retirement, so the counters asserted two
            # accepted verdicts for one action. A verdict is accepted when it
            # survives this resolution, not when it parses — and that now
            # covers `unresolved` too, which was still counted per item.
            self._inc(
                "nora_note_revisit_verdicts_total", verdict=resolved.verdict,
            )
            if resolved.retirement is not None:
                out.append(resolved.retirement)
        return tuple(out)

    def _accept_one(
        self, item: Mapping[str, object], *, asked: Mapping[str, _Candidate],
    ) -> _Verdict | None:
        """One verdict, or `None` with a counted reason.

        The order is deliberate and each step is a different question:

        1. **is this a note we asked about** — a handle outside `asked` is
           refused before anything else is read. Retiring an unrelated note is
           the worst thing this adaptor can do and it is one hallucinated
           identifier away; nothing about the rest of the item can make that
           safe.
        2. **is the verdict one of the three** — a fourth value is the model not
           answering the question, never a reason to guess which it meant.
        3. **does the verdict's own ground hold** — the citation for `closed`,
           the clock for `expired`. This is where a plausible answer stops being
           enough.

        **The split between 1 and 2 is ATTRIBUTION, and it is deliberate rather
        than an accident of ordering.** To block a note's retirement an item has
        to be a claim ABOUT THAT NOTE, and malformed items fall on both sides of
        that:

        - **unattributable** — the handle is unreadable, so the item names no
          note. It cannot block anything BY CONSTRUCTION, not by policy: there
          is nothing to attach it to. Returns `None`.
        - **attributable, unreadable** — the handle resolves and the VERDICT
          does not. That IS a claim about this note, and one we cannot read.
          `{"note": "1", "verdict": "clsoed"}` beside a well-formed `closed` is
          not one answer plus noise; it is one answer plus an answer we cannot
          read, which is «cannot tell which». Returns `_VERDICT_UNREADABLE` so
          it collides in `accept`, and the note stands.

        **Unreadable is not absent**, which is the same reasoning as the settled
        line one level up: grounding decides whether a claim may ACT, never
        whether it was MADE — and parseability is the same kind of thing.

        An earlier revision let the second case fall through to the first, on
        the argument that one malformed item should not veto a grounded
        decision. That argument is about COST, and «this would be inconvenient»
        is exactly the shape of reasoning that produced the three-instance table
        in `27` §11.3.4. The cost is real and it is accepted: a model emitting
        noise beside good answers will hold notes open, and holding is the
        recoverable direction.
        """
        handle = item.get(_KEY_NOTE)
        # Accepted as `int` too: the handle is an ordinal rendered into the
        # prompt, and a model answering 1 rather than "1" has pointed at the
        # same note. `bool` is excluded for the reason the citation check gives
        # below — `isinstance(True, int)` is True, and `str(True)` is not a
        # handle anyone rendered, but relying on that is relying on an
        # accident.
        if isinstance(handle, bool) or not isinstance(handle, (str, int)):
            self._refuse("unknown_note")
            return None
        candidate = asked.get(str(handle).strip())
        if candidate is None:
            self._refuse("unknown_note")
            return None
        note_id = candidate.note.id

        verdict = item.get(_KEY_VERDICT)
        if not isinstance(verdict, str) or verdict.strip().lower() not in _VERDICTS:
            # ATTRIBUTABLE and unreadable — the handle resolved, so this is a
            # claim about a known note that cannot be read. Counted exactly as
            # before (`unmeasured` is unchanged for an operator) and RETURNED,
            # so it collides with any readable verdict for the same note.
            self._refuse("unmeasured")
            return _Verdict(
                note_id=note_id, verdict=_VERDICT_UNREADABLE, retirement=None,
            )
        verdict = verdict.strip().lower()

        if verdict == _VERDICT_UNRESOLVED:
            # The model was asked and said it could not tell. That is a real
            # answer and the correct one for a quiet room — counted separately
            # from the refusals above, because «asked and could not tell» and
            # «did not answer the question» are different states and collapsing
            # them would hide a model that had stopped engaging.
            #
            # RETURNED rather than dropped, so it takes part in the
            # contradiction check: paired with a `closed` or an `expired` for
            # the same note it is a conflict, and the note must survive it.
            return _Verdict(note_id=note_id, verdict=verdict, retirement=None)

        if not candidate.evidence:
            # Neither retirement is reachable without evidence. A room that has
            # said nothing since the note was written cannot establish that a
            # question went unanswered — it establishes that nobody spoke.
            return self._ungrounded(
                "no_evidence", note_id=note_id, verdict=verdict,
            )

        if verdict == _VERDICT_CLOSED:
            cited = item.get(_KEY_CITED)
            # `int` and a numeric `str` are both accepted, because the id was
            # rendered INSIDE the prompt text and a model re-typing it as
            # "7" rather than 7 has still pointed at the right message. A bool
            # is refused explicitly: `isinstance(True, int)` is True in Python,
            # and `int(True) == 1` would silently become a citation of message
            # 1 — a real id, in the evidence, pointing at the wrong thing.
            if isinstance(cited, bool) or not isinstance(cited, (int, str)):
                return self._ungrounded(
                    "citation_missing", note_id=note_id, verdict=verdict,
                )
            try:
                cited_id = int(cited)
            except ValueError:
                return self._ungrounded(
                    "citation_missing", note_id=note_id, verdict=verdict,
                )
            if cited_id not in candidate.message_ids:
                # The model named a message it was never shown. Same refusal a
                # hallucinated `message_id` gets in `nora.language.observer`,
                # and for the same reason: fabricated grounding is worse than a
                # missing entry, and here the entry costs a note.
                return self._ungrounded(
                    "citation_ungrounded", note_id=note_id, verdict=verdict,
                )
            return _Verdict(
                note_id=note_id, verdict=verdict,
                retirement=NoteRetirement(
                    note_id=note_id,
                    because=_BECAUSE[_VERDICT_CLOSED],
                    cited_message_id=cited_id,
                ),
            )

        # `expired`, and the clock decides it rather than the model. A model
        # that believes a note is stale while its anchor date is still ahead is
        # not looking at a calendar, and a note with no anchor at all has no
        # moment to have passed.
        if not candidate.anchor_passed:
            return self._ungrounded(
                "anchor_not_passed", note_id=note_id, verdict=verdict,
            )
        if not candidate.anchor_covered:
            # The moment has gone by, and we were not looking when it did. See
            # `_covers`: the room either fell silent before the anchor or is
            # busier than the window, so «nothing answered it» is a claim about
            # conversation this pass never read. `unavailable`, and the note
            # stays.
            return self._ungrounded(
                "anchor_not_covered", note_id=note_id, verdict=verdict,
            )
        return _Verdict(
            note_id=note_id, verdict=verdict,
            retirement=NoteRetirement(
                note_id=note_id,
                because=_BECAUSE[_VERDICT_EXPIRED],
                cited_message_id=None,
            ),
        )

    # -- commit -------------------------------------------------------------

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """Retire what survived, and report what actually MOVED.

        **`propose` is never called.** This adaptor writes no new rows — it is
        the tier's negative measurement, and `DreamSink`'s two verbs are not a
        sequence every adaptor must run. Calling `propose(key, ())` would be
        R-27's «I looked and found nothing» about a question this adaptor was
        never asked, and the note sink has no decay pass for it to drive.

        **Returns rows AFFECTED, not retirements attempted**, and the gap
        between the two is counted rather than swallowed. `supersede` returns 0
        for an unknown or malformed target and never raises, which means a
        retirement that silently did nothing looks exactly like one that was
        never attempted — the same unmeasured-versus-zero collapse this tier
        exists to refuse. `result="no_rows"` is that gap made visible.
        """
        affected = 0
        for proposal in proposals:
            payload = proposal.payload
            if not isinstance(payload, NoteRetirement):
                # The runtime types `Proposal.payload` as `object` on purpose;
                # this is the one place that knows better. A foreign payload is
                # a wiring fault, not a note to retire.
                log.error(
                    "note revisit: refusing a %s payload in workspace %s",
                    type(payload).__name__, key,
                )
                self._inc("nora_note_retirements_total", because="unknown", result="refused")
                continue
            rows = await self._notes.supersede(
                key=key,
                target=payload.note_id,
                # ALWAYS None. A retirement here has no successor — see
                # `NoteRetirement`'s own note.
                replaced_by=None,
                because=payload.because,
                # MUST be None: `source` is a warrant, and the note tier has no
                # column for a supersession's citation. A non-null value raises
                # in the store. The citation that grounded a `retired-closed`
                # lives on the payload and in the trace, not in this argument.
                source=None,
                now_ms=now_ms,
            )
            affected += rows
            self._inc(
                "nora_note_retirements_total",
                because=payload.because,
                result="applied" if rows else "no_rows",
            )
            if not rows:
                log.warning(
                    "note revisit: supersede(%s) in workspace %s affected NO "
                    "rows — the note was already retired or the id no longer "
                    "resolves", payload.note_id, key,
                )
        return affected

    # -- counters -----------------------------------------------------------

    def _refuse(self, reason: str) -> None:
        self._inc("nora_note_revisit_refused_total", reason=reason)

    def _ungrounded(
        self, reason: str, *, note_id: str, verdict: str,
    ) -> _Verdict:
        """A verdict the model DID make and that failed to ground.

        Counted exactly as before — the reason label is unchanged and an
        operator sees no difference — but returned rather than dropped, so it
        still counts as a claim when `accept` asks whether this note got two
        different answers. See `_Verdict.retirement`.
        """
        self._refuse(reason)
        return _Verdict(note_id=note_id, verdict=verdict, retirement=None)

    def _inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        if self._metrics is not None:
            self._metrics.inc(name, value, **labels)

    def _gauge(self, name: str, value: float, **labels: object) -> None:
        if self._metrics is not None:
            self._metrics.gauge(name, value, **labels)


def _covers(
    evidence: Sequence[TranscriptRow], *,
    rows: Sequence[TranscriptRow], created_at: int,
    bounds: tuple[int, int] | None, truncated: bool,
) -> bool:
    """Whether the evidence window actually SPANS the moment the note is about
    — the ground `retired-expired` needs and did not have.

    `anchor_passed` says the moment has gone by. It says nothing about whether
    anybody was looking when it did, and an earlier revision treated the two as
    the same thing: `expired` needed only that the room had said SOMETHING,
    anything, at any point after the note was written. Two ways that retires a
    note wrongly, both reachable in a live room:

    - **the room dies before the moment.** Note written 1 Aug, anchored to 9
      Sept, thirty messages across the first fortnight of August, silence after.
      The window is non-empty and contains no answer, so the model answers
      `expired` — correctly, on what it was shown — and a note is retired with
      ZERO evidence about September.
    - **the room is busier than the window.** The message that ANSWERS the
      question sits outside the newest `limit` rows. The window shows unrelated
      traffic, `expired` is accepted, and an ANSWERED note is recorded as having
      gone unanswered.

    Two conditions, and both are about the WINDOW rather than about the model:

    **It must reach past the moment.** At least one message at or after the
    anchor's last instant — the room was still talking once the moment had gone
    by, and still did not settle it. A room that fell silent beforehand cannot
    testify about a moment it was not present for.

    **It must reach back to before the NOTE.** Either the window is not
    truncated (no older traffic exists at all) or its oldest row predates
    `created_at`, so everything said since the note was written is in view.

    **The reference point is the note, not the anchor, and getting that wrong
    was a real defect at the shipped cap.** An earlier revision measured against
    the anchor's first instant, reasoning that unseen traffic mattered only
    where it lay "across the moment, which is where an answer would be". That is
    false by the definition of a closing condition: a note anchored to 9
    September is answered by «booked Yardbird for the 9th» said on 3 August.
    Reproduced at `revisit_evidence_messages = 40` — answer two days after the
    note, forty later messages straddling the anchor day, the window's oldest
    row comfortably before the anchor so this check PASSED, the answer invisible
    to the model, and an ANSWERED note retired as expired.

    Measuring against `created_at` closes it and drops the anchor's start from
    this clause entirely — which removes a second defect with it:
    `_anchor_bounds_ms` computes in UTC while the deployment runs in HKT, and
    while that makes `reaches_past` STRICTER (safe), it made the old
    `reaches_back` LOOSER by the same eight hours (not safe).

    Fails CLOSED: no bounds, no evidence, or either condition unmet returns
    `False`, and `_accept_one` then leaves the note standing.

    **The cost of holding, stated plainly because its pressure valve is NOT
    BUILT.** A note whose room fell quiet before its date, or whose room is
    permanently busier than the window, is refused here EVERY tick: `_is_due`
    re-selects it, and `due[:max_revisits]` takes `standing()`'s oldest-first
    order, so held notes at the head consume revisit slots forever and starve
    the due notes behind them. The design's answer is the `strikes` breaker
    (spec §9.6) and `strikes` HAS NO WRITER — see `_is_due`. So this deferral
    currently defers to nothing: the direction is safe and the cost is
    unbounded, which is the strongest argument for building the wake pass next.

    One related pressure, pre-existing and made certain by the above: `closed`
    does not pass through this check (its citation is its own ground), and that
    check requires only that the cited id be IN the window, not that it answers
    anything. A note held here is re-asked every tick, so it gets an unbounded
    number of chances to come back as `closed` instead.

    *The reading that would reopen it:* `reason="anchor_not_covered"` climbing
    and never resolving.
    """
    if bounds is None or not evidence:
        return False
    end = bounds[1]
    reaches_past = evidence[-1].ts >= end
    # `rows`, not `evidence`: the raw PRE-FILTER window. `evidence` has already
    # dropped everything at or before `created_at`, so its oldest row cannot
    # answer «did the window reach back far enough» — it is bounded by
    # `created_at` by construction, and asking it that is circular.
    reaches_back = (not truncated) or (bool(rows) and rows[0].ts <= created_at)
    return reaches_past and reaches_back


def _is_due(note: Note, *, now_ms: int) -> bool:
    """Whether this note's moment has arrived — arithmetic, no model.

    Two independent triggers, and a note needs only one. `revisit_at` is an
    explicit «look at this again then»; an anchor date is a moment the note is
    about. A note with neither is an open question with no deadline, and it is
    NOT due: asking a model whether an undated question has gone stale is
    asking it to invent a deadline, which is the judgement this whole module
    refuses to make.

    **`revisit_at` HAS NO WRITER — this branch is unreachable in production
    today, and that is a gap rather than a decision.** The column exists since
    migration `0026` with a `CHECK`, `SqlNoteStore` reads it into `Note`, and
    nothing anywhere sets it: not the tag-path tools, not the curator, not this
    pass. So every note that reaches this function today is due by ANCHOR DATE
    alone, and a note without one is never revisited at all.

    The mechanism that should fill it is the note-tier spec's §9.6 wake pass,
    and that spec names `NoteRevisitor` as its owner: on each wake, a note whose
    head revision has not moved takes a strike and gets NO new `revisit_at`; a
    note whose head moved **on the tag path** resets its strikes and gets the
    next one; past K strikes it stops waking and becomes eligible for dateless
    retirement. `strikes` and `last_wake_revision_id` are the other two columns
    with no writer, for the same reason.

    **It is not built here because it needs a store verb this tier does not
    have.** `DreamSink` offers `propose` and `supersede`; neither writes
    `revisit_at`, `strikes` or `last_wake_revision_id`, and `nora/infra/sql/
    notes.py` is not this session's to change. Building the wake pass against
    an invented write path would be worse than leaving the branch honest.

    Kept rather than deleted because deleting it would make the revisitor
    permanently anchor-only and quietly drop half the spec'd trigger set — and
    because the strike loop is what stops an undated note being due forever,
    which is the pressure valve `_covers` above defers to when it holds a note
    that a quiet room can never settle.
    """
    if note.revisit_at is not None and now_ms >= note.revisit_at:
        return True
    if note.anchor_date is None:
        return False
    bounds = _anchor_bounds_ms(note.anchor_date)
    return bounds is not None and now_ms > bounds[1]

