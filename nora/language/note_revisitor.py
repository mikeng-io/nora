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
        evidence = tuple(
            row for row in rows
            if row.ts > note.created_at and not row.is_nora
        )
        if state != "unavailable" and not evidence:
            state = "absent"
        anchor_end = (
            _anchor_end_ms(note.anchor_date) if note.anchor_date is not None else None
        )
        return _Candidate(
            handle=handle,
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
        asked = self._asked.get(key, {})
        out: list[NoteRetirement] = []
        # One retirement per NOTE. Two `closed` items naming the same note are
        # one decision stated twice, and superseding twice would fire this
        # module's own `result="no_rows"` alarm on the second call — a false
        # positive on the counter built to make a real silent no-op visible.
        seen: set[str] = set()
        for item in items:
            retirement = self._accept_one(item, asked=asked)
            if retirement is None or retirement.note_id in seen:
                continue
            seen.add(retirement.note_id)
            out.append(retirement)
        return tuple(out)

    def _accept_one(
        self, item: Mapping[str, object], *, asked: Mapping[str, _Candidate],
    ) -> NoteRetirement | None:
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
        handle = item.get("note")
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

