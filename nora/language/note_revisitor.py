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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from nora.domain.models import TranscriptRow
from nora.domain.notes import Note, NoteSupersedeReason
from nora.domain.ports import DreamSink, Proposal
from nora.obs.metrics import Metrics

log = logging.getLogger(__name__)

# How many notes one tick will judge. A ceiling on the PROMPT as much as on the
# work: each candidate carries its own evidence block, so this multiplies with
# `DEFAULT_EVIDENCE_MESSAGES_PER_NOTE` below into the size of one call.
DEFAULT_MAX_REVISITS_PER_TICK = 10

# Messages of `established_in` rendered as one note's evidence. Newest-first
# from `recent`, then filtered to those actually postdating the note — a
# closing condition cannot have been met before the note existed, and rendering
# earlier traffic invites the model to answer from it.
DEFAULT_EVIDENCE_MESSAGES_PER_NOTE = 40

# A `retired-closed` verdict must cite one of these; a note id must be one of
# those asked about. Both are joins back to something this process chose, never
# to something the model supplied.
_VERDICT_CLOSED = "closed"
_VERDICT_EXPIRED = "expired"
_VERDICT_UNRESOLVED = "unresolved"
_VERDICTS = (_VERDICT_CLOSED, _VERDICT_EXPIRED, _VERDICT_UNRESOLVED)

_BECAUSE = {
    _VERDICT_CLOSED: NoteSupersedeReason.RETIRED_CLOSED,
    _VERDICT_EXPIRED: NoteSupersedeReason.RETIRED_EXPIRED,
}

_MS_PER_DAY = 86_400_000


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
class _Candidate:
    """One note this tick will ask about, with the evidence it will be judged
    against. Built entirely in code — the model chooses nothing here."""

    note: Note
    evidence: tuple[TranscriptRow, ...]
    anchor_passed: bool
    # Why `evidence` is what it is, as ONE value — «the room said nothing» and
    # «the record read failed» both arrive here as an empty tuple, and they are
    # different states. Carried rather than re-derived at each use, because the
    # first version of this counted both and produced 2 for one candidate: the
    # unavailable-collapsing-into-absent defect this module exists to refuse,
    # committed inside its own instrument.
    evidence_state: str

    @property
    def message_ids(self) -> frozenset[int]:
        return frozenset(row.id for row in self.evidence)


def _anchor_end_ms(anchor_date: int) -> int | None:
    """The last instant of a `YYYYMMDD` anchor, in epoch ms, or `None` if the
    value is not a date this can read.

    END of the day, not the start: a note anchored to the 15th is not stale at
    00:00 on the 15th. Computing it from the calendar rather than from a
    stored timestamp because `anchor_date` IS an integer date — the tier chose
    that shape deliberately, and re-deriving a moment from it is this module's
    job rather than the store's.

    Returns `None` rather than raising on a malformed value, and the caller
    treats `None` as «not decidable» — an unreadable anchor must leave the note
    standing, not expire it.
    """
    import datetime as _dt

    try:
        day = _dt.date(
            anchor_date // 10_000, anchor_date // 100 % 100, anchor_date % 100,
        )
    except (ValueError, TypeError):
        return None
    start = _dt.datetime.combine(day, _dt.time.min, tzinfo=_dt.UTC)
    return int(start.timestamp() * 1000) + _MS_PER_DAY - 1


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
        f"### note {candidate.note.id}",
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
        # The reason is NOT rendered. The model's job is the same either way —
        # decide nothing — and telling it «the record was unreachable» invites
        # it to reason about our infrastructure instead of about the note. The
        # distinction is for the operator, and it lives on the counter.
        lines.append(
            "evidence: NONE — nothing has been said in that conversation since "
            "this note was written."
        )
        return "\n".join(lines)
    lines.append("evidence, newest last:")
    for row in candidate.evidence:
        speaker = row.sender_name or row.sender_id
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
    ) -> None:
        self._notes = notes
        self._record = record
        self._max_revisits = max_revisits_per_tick
        self._evidence_limit = evidence_messages_per_note
        self._metrics = metrics
        # What this tick ASKED about, keyed by note id — the join a verdict is
        # checked against in `accept`. Rebuilt by every `inputs` call and never
        # merged across ticks: a note id from a previous tick is exactly as
        # unaskable as one the model invented.
        self._asked: dict[str, _Candidate] = {}

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
        self._asked = {}
        standing = await self._notes.standing(workspace=key)
        self._gauge("nora_note_revisit_standing", float(len(standing)), workspace=key)
        now_ms = _now_ms()
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
        for note in due:
            candidate = await self._candidate(note, now_ms=now_ms)
            self._asked[note.id] = candidate
            # ONCE per candidate, from the single value that decided it.
            self._inc(
                "nora_note_revisit_candidates_total",
                evidence=candidate.evidence_state,
            )
            rendered.append(_render_note(candidate))
        return "\n\n".join(rendered)

    async def _candidate(self, note: Note, *, now_ms: int) -> _Candidate:
        """One note plus the evidence it will be judged against.

        Filtered to messages that POSTDATE the note: a closing condition cannot
        have been met before the note existed, and rendering earlier traffic
        invites the model to answer from it. Re-ordered oldest-last because
        `recent` is newest-first for the prompt and a conversation read
        backwards is a conversation a reader gets wrong.

        A read that FAILS leaves the candidate with no evidence rather than
        propagating — which puts the note on the «cannot tell» path, where it
        survives. That is the correct direction for this tier: an unreachable
        record must never be able to cause a retirement.
        """
        state = "present"
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
        evidence = tuple(
            row for row in reversed(rows) if row.ts > note.created_at
        )
        if state != "unavailable" and not evidence:
            state = "absent"
        anchor_end = (
            _anchor_end_ms(note.anchor_date) if note.anchor_date is not None else None
        )
        return _Candidate(
            note=note,
            evidence=evidence,
            anchor_passed=anchor_end is not None and now_ms > anchor_end,
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
        out: list[NoteRetirement] = []
        for item in items:
            retirement = self._accept_one(item)
            if retirement is not None:
                out.append(retirement)
        return tuple(out)

    def _accept_one(self, item: Mapping[str, object]) -> NoteRetirement | None:
        """One verdict, or `None` with a counted reason.

        The order is deliberate and each step is a different question:

        1. **is this a note we asked about** — a note id outside `_asked` is
           refused before anything else is read. Retiring an unrelated note is
           the worst thing this adaptor can do and it is one hallucinated
           identifier away; nothing about the rest of the item can make that
           safe.
        2. **is the verdict one of the three** — a fourth value is the model not
           answering the question, never a reason to guess which it meant.
        3. **does the verdict's own ground hold** — the citation for `closed`,
           the clock for `expired`. This is where a plausible answer stops being
           enough.
        """
        note_id = item.get("note_id")
        if not isinstance(note_id, str) or note_id not in self._asked:
            self._refuse("unknown_note")
            return None
        candidate = self._asked[note_id]

        verdict = item.get("verdict")
        if not isinstance(verdict, str) or verdict.strip().lower() not in _VERDICTS:
            self._refuse("unmeasured")
            return None
        verdict = verdict.strip().lower()

        if verdict == _VERDICT_UNRESOLVED:
            # The model was asked and said it could not tell. That is a real
            # answer and the correct one for a quiet room — counted separately
            # from the refusals above, because «asked and could not tell» and
            # «did not answer the question» are different states and collapsing
            # them would hide a model that had stopped engaging.
            self._inc("nora_note_revisit_verdicts_total", verdict="unresolved")
            return None

        if not candidate.evidence:
            # Neither retirement is reachable without evidence. A room that has
            # said nothing since the note was written cannot establish that a
            # question went unanswered — it establishes that nobody spoke.
            self._refuse("no_evidence")
            return None

        if verdict == _VERDICT_CLOSED:
            cited = item.get("cited_message_id")
            # `int` and a numeric `str` are both accepted, because the id was
            # rendered INSIDE the prompt text and a model re-typing it as
            # "7" rather than 7 has still pointed at the right message. A bool
            # is refused explicitly: `isinstance(True, int)` is True in Python,
            # and `int(True) == 1` would silently become a citation of message
            # 1 — a real id, in the evidence, pointing at the wrong thing.
            if isinstance(cited, bool) or not isinstance(cited, (int, str)):
                self._refuse("citation_missing")
                return None
            try:
                cited_id = int(cited)
            except ValueError:
                self._refuse("citation_missing")
                return None
            if cited_id not in candidate.message_ids:
                # The model named a message it was never shown. Same refusal a
                # hallucinated `message_id` gets in `nora.language.observer`,
                # and for the same reason: fabricated grounding is worse than a
                # missing entry, and here the entry costs a note.
                self._refuse("citation_ungrounded")
                return None
            self._inc("nora_note_revisit_verdicts_total", verdict="closed")
            return NoteRetirement(
                note_id=note_id,
                because=_BECAUSE[_VERDICT_CLOSED],
                cited_message_id=cited_id,
            )

        # `expired`, and the clock decides it rather than the model. A model
        # that believes a note is stale while its anchor date is still ahead is
        # not looking at a calendar, and a note with no anchor at all has no
        # moment to have passed.
        if not candidate.anchor_passed:
            self._refuse("anchor_not_passed")
            return None
        self._inc("nora_note_revisit_verdicts_total", verdict="expired")
        return NoteRetirement(
            note_id=note_id,
            because=_BECAUSE[_VERDICT_EXPIRED],
            cited_message_id=None,
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

    def _inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        if self._metrics is not None:
            self._metrics.inc(name, value, **labels)

    def _gauge(self, name: str, value: float, **labels: object) -> None:
        if self._metrics is not None:
            self._metrics.gauge(name, value, **labels)


def _is_due(note: Note, *, now_ms: int) -> bool:
    """Whether this note's moment has arrived — arithmetic, no model.

    Two independent triggers, and a note needs only one. `revisit_at` is an
    explicit «look at this again then»; an anchor date is a moment the note is
    about. A note with neither is an open question with no deadline, and it is
    NOT due: asking a model whether an undated question has gone stale is
    asking it to invent a deadline, which is the judgement this whole module
    refuses to make.
    """
    if note.revisit_at is not None and now_ms >= note.revisit_at:
        return True
    if note.anchor_date is None:
        return False
    end = _anchor_end_ms(note.anchor_date)
    return end is not None and now_ms > end


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)
