"""The note tier's CREATE PATH — `NoteCandidateAdaptor` (design/dreamer-agent-
loop/33-note-dreamer-schedule.md §4, issue #98).

**The gap this closes.** The tag path's `note_create`/`note_update` tools
(`nora.agent.tools`) are measured to have fired zero times in production: the
cold start added recognition to `conduct.md`, but nothing can write a note
without first noticing, in one turn, that a whole arrangement is worth
tracking — and a single turn's context is the wrong grain for that judgement.
This adaptor is the "Phase A" shape the deleted knowledge tier's extractor
introduced (2026-09-07, issue #115) and the only surviving instance of it:
scope-keyed, watermarked, TOOL-FREE, reading a room's message batch across however many ticks a
watermark takes to accumulate a week's exchange, and proposing full notes —
never writing one directly.

**The model proposes; code disposes, the split every Dreamer adaptor
enforces.** `accept` turns the model's envelope items into typed, validated
`_CreateCandidate` values. `commit` does three things, in order, none of
which the model is asked about:

1. **Stage.** Every candidate is written to `note_candidate(kind='create')`
   FIRST, via `record_create_candidate` — before anything is decided. This is
   what makes a proposal durable across a crash between staging and the gate,
   and what lets `nora_note_candidates_total{trigger="create"}` count arrivals
   independently of the gate's own `nora_note_candidate_gate_total{action}`.
2. **Gate.** A DETERMINISTIC comparison — `_find_anchor_match`, on the
   ANCHOR DATE, never `title` (design 33 §4, Kimi review finding M10) —
   decides whether a standing note already covers this occasion.
3. **Materialize or refuse.** No match: the candidate becomes a
   `NoteCreationProposal`, written through `DreamSink.propose` exactly as the
   tag path's future create tool would. A match: the candidate becomes a
   `NoteRevisionProposal` against the matched note's own head, but ONLY if it
   adds something the existing head lacks (`_adds_information`) — otherwise
   it is refused as a duplicate. Either way the candidate row is advanced to
   a TERMINAL status (`created`/`refused`) so a re-observation of the same
   occasion never relitigates a settled decision.

**Why scope-keyed and watermarked, unlike the curator or the revisitor.** A
note's arrangement spans days of conversation, not one turn — the operator's
own case, design 33 §4: "呢啲 conversation...可能 cross 一個禮拜去做". A
per-room watermark is the only way this adaptor sees the whole exchange
rather than a five-minute slice, and being scope-keyed (not workspace-keyed)
is what lets `nora.app.runtime._dream_adaptor_once`'s existing
`AdaptorSchedule` mechanism apply unchanged (design 33 §5, finding N1): the
mechanism is watermark-bound, and this is now the only adaptor that is
both scope-keyed AND watermarked (the knowledge tier's extractor was the
first, and is gone).

**Why it reads the note index too.** Unlike a claim extractor — new claims,
with dedupe left to adjudication — a note proposal must not duplicate a note
that already exists in the workspace's standing notebook. `inputs` renders
`render_notes`' own projection (`nora.language.note_render`, the SAME one the
tag path's `<notes>` block gets: title + description only, never a body) so
the model can be told: *if this arrangement is already written down, propose
an update or nothing, not a duplicate create.* The model's own restraint is
advisory, though — `commit`'s gate is what actually decides, on a property
the model cannot fake (§4: "relying on the model to decline is
self-attestation on a destructive operation").

**`uncertainty` is set equal to `description`, a judgement call this module
records rather than hides.** Design 33 §3 widens the note field convention —
`description` becomes "what this entry is + why it is not finished + what
would finish it" — and lists this adaptor's output tuple as `(title,
description, body, anchor_date, closing_condition, message_ids)`, with no
`uncertainty` field. But `nora.domain.notes.Note`/`NoteCreationProposal`
still require one (a distinct field, "a statement of what is uncertain") and
this module does not touch that type. The widened `description` already
carries what `uncertainty` used to mean — the two fields converge under the
notebook reframe — so this adaptor asks the model for one field and uses it
for both, rather than asking for a fifth thing the design does not name.
*The reading that would reopen this*: `nora.domain.notes.Note` growing a
formal merge of the two fields, at which point this adaptor stops filling in
a value the model was never asked for.

**A created note carries an explicit `revisit_at` from the moment it exists**
(note-curator merge, design 34 §5.3 resolution (c), issue #99) — derived from
its own anchor by `nora.domain.notes.default_revisit_at`, the SAME formula the
merged curator uses after every later wake. Without this a gate-created note's
first `record_wake` call would see no `tag:` revision in its chain (its only
revision is background-authored) and no `revisit_at` either, charging a strike
on a note nobody has had a chance to answer yet — 34 §5.3 rejected the
alternative of seeding `last_wake_revision_id` at creation as a no-op (Kimi
review finding N3): it changes nothing under `tag_path_progressed_since_wake`'s
existing semantics, while an explicit `revisit_at` makes the first wake an
ordinary scheduled one.

**`anchor_entity_ids` is free text and the gate never reads it.** It is
accepted from the model exactly as the tag path's `note_create` tool stores
it — unresolved, unvalidated, written verbatim to the note it creates — and
it is half of `note_candidate`'s own dedupe index, so a re-observation of the
same occasion refreshes one staged row. What it is NOT is a match key: three
spellings of one wedding are three strings, and two unrelated notes that both
say 「wedding」 are one, so a key over it is wrong in both directions at once.
The gate compares the DATE alone. That is deliberately the looser reading —
two arrangements on one day become one note with an appended body rather
than two notes — because the alternative (a resolved entity set as the
second half of the key, issue #100) needed an entity registry that nothing
mints any more, and was removed with it on 2026-09-08. *The reading that
would reopen this:* two genuinely different occasions on one date in one
workspace, updated into each other — at which point the entity half of the
key is worth a registry of its own.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Protocol

from nora.domain.models import TranscriptRow
from nora.domain.notes import (
    BODY_MAX_CHARS,
    CANDIDATE_CREATED,
    CANDIDATE_PENDING,
    CANDIDATE_REFUSED,
    DESCRIPTION_MAX_CHARS,
    TITLE_MAX_CHARS,
    Note,
    NoteCreateCandidate,
    NoteCreationProposal,
    NoteRevision,
    NoteRevisionProposal,
    StaleNoteHeadError,
    default_revisit_at,
)
from nora.domain.ports import DreamSink, Proposal
from nora.domain.prompt import escape
from nora.language.note_render import render_notes
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.notes.candidates")

#: A model-authored `closing_condition`/entity string is capped at parse time,
#: ahead of the store — capping model-authored text at PARSE time rather than
#: trusting a column CHECK, applied here because
#: `note_candidate.closing_condition` carries no length CHECK
#: (matching `Note.closing_condition`, which the tag path never injects into a
#: prompt and therefore never capped either). Generous enough never to reject
#: a real closing condition; a value this long is the model pasting prose
#: rather than stating a condition.
MAX_CLOSING_CONDITION_CHARS = 2000
#: One free-text entity mention, and how many a candidate may carry. Small on
#: purpose: this is `note_create`'s own free-text field, never resolved here,
#: and a candidate naming forty "entities" is not naming an occasion's people,
#: it is padding a column nothing reads for merge (see the module docstring).
MAX_ENTITY_CHARS = 200
MAX_ENTITIES_PER_CANDIDATE = 10
#: How many rows of one batch `inputs` renders before switching to a summary
#: line. 500 is the number the knowledge tier's extractor used for the same
#: shape of batch (a room's message window), kept rather than re-derived
#: because there is no argument for a different bound.
MAX_RENDERED_MESSAGES = 500
#: A `YYYYMMDD` calendar day is at least eight digits — `nora.domain.notes`'
#: own `NoteCreationProposal.__post_init__` bound, restated here because this
#: module parses the value before that type ever sees it and a bad date must
#: be dropped rather than raise out of a background tick.
_MIN_ANCHOR_DATE = 10_000_000

#: Every value `nora_note_candidate_gate_total{action}` may carry, DERIVED
#: rather than hand-copied into the metric's help text (`nora.obs.
#: metric_specs`) — `nora.language.note_curator.CURATOR_ACTIONS`' own
#: precedent, enforced at RUNTIME because `nora/language` is outside `make
#: gate`'s mypy scope (issue #61).
GATE_ACTIONS: frozenset[str] = frozenset({
    "created",          # materialized as a brand new note
    "updated",          # materialized as a revision to a standing note —
                        # unreachable today; see the module docstring
    "refused",          # a duplicate with nothing new, or malformed
    "deferred",         # a transient failure; left pending, retried next tick
})


class _NoteStore(DreamSink, Protocol):
    """`SqlNoteStore`, seen from here: `DreamSink`'s two verbs (the create/
    update materialization) plus the two reads this adaptor makes directly —
    `standing` for the note index AND the gate's own comparison, `read` for
    the matched note's current head body and CAS parent."""

    async def standing(self, *, workspace: str) -> tuple[Note, ...]: ...

    async def read(
        self, *, workspace: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None: ...


class _CandidateStore(Protocol):
    """`SqlNoteStore`'s create-candidate surface, seen from here — the
    `note_candidate(kind='create')` staging table's own two verbs, structural
    rather than a declared port, for the usual reason a one-consumer shape
    is not a port: nothing else consumes it."""

    async def record_create_candidate(
        self, *, workspace: str, title: str, description: str, body: str,
        closing_condition: str, anchor_date: int,
        anchor_entity_ids: Sequence[str], message_ids: Sequence[int],
        now_ms: int,
    ) -> NoteCreateCandidate: ...

    async def set_create_candidate_status(
        self, *, workspace: str, candidate_id: str, status: str, now_ms: int,
    ) -> int: ...


@dataclass(frozen=True)
class _CreateCandidate:
    """One parsed, validated envelope item — this adaptor's OUTPUT type, never
    written anywhere directly (`commit` stages it, then decides)."""

    title: str
    description: str
    body: str
    closing_condition: str
    anchor_date: int
    anchor_entity_ids: tuple[str, ...]
    message_ids: tuple[int, ...]


def _text(value: object, *, limit: int) -> str | None:
    """One model-authored string field, or `None` if it is not one — strict
    `isinstance(str)`, never `str(value)`, because coercion turns `None` into
    `"None"` and `7` into `"7"`, values that look
    real and are the model not answering."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > limit:
        return None
    return stripped


def _parse_entities(value: object) -> tuple[str, ...] | None:
    """`anchor_entity_ids`: optional: absent or `None` is `()`, a real answer
    and the common one (this adaptor supplies a date; entities are the tag
    path's own free-text convention, carried through unresolved). Present and
    malformed — not a list, or a non-string/over-long/too-numerous member —
    is `None`, the model having tried to answer and failed, which drops the
    WHOLE candidate exactly as a bad `message_ids` entry does."""
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_ENTITIES_PER_CANDIDATE:
        return None
    out: list[str] = []
    for entry in value:
        text = _text(entry, limit=MAX_ENTITY_CHARS)
        if text is None:
            return None
        out.append(text)
    return tuple(out)


def _parse_candidate(item: Mapping[str, object]) -> _CreateCandidate | None:
    """One envelope item -> a `_CreateCandidate`, or `None` if malformed —
    no partial parse — the whole item is dropped rather than guessed at."""
    title = _text(item.get("title"), limit=TITLE_MAX_CHARS)
    description = _text(item.get("description"), limit=DESCRIPTION_MAX_CHARS)
    body = _text(item.get("body"), limit=BODY_MAX_CHARS)
    closing_condition = _text(
        item.get("closing_condition"), limit=MAX_CLOSING_CONDITION_CHARS,
    )
    if title is None or description is None or body is None or closing_condition is None:
        return None

    anchor_date = item.get("anchor_date")
    # `bool` is an `int` in Python — refused rather than coerced, the same
    # guard `_parse_candidate` applies to `message_ids` below.
    if (
        isinstance(anchor_date, bool)
        or not isinstance(anchor_date, int)
        or anchor_date < _MIN_ANCHOR_DATE
    ):
        return None

    entities = _parse_entities(item.get("anchor_entity_ids"))
    if entities is None:
        return None

    raw_ids = item.get("message_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return None
    try:
        message_ids = tuple(
            int(v) for v in raw_ids if not isinstance(v, bool)
        )
    except (TypeError, ValueError):
        return None
    if len(message_ids) != len(raw_ids):
        return None

    return _CreateCandidate(
        title=title, description=description, body=body,
        closing_condition=closing_condition, anchor_date=anchor_date,
        anchor_entity_ids=entities, message_ids=message_ids,
    )


def _media_placeholder(row: TranscriptRow) -> str:
    """What a bodiless row renders as. Deliberately terse — this adaptor does
    not need to distinguish media TYPES, only to show that a row without text
    is still shown (with its id, so `accept`'s message-id
    membership check sees the same set `inputs` rendered)."""
    return "[media]" if row.media_json else ""


#: How a message's moment is rendered: the ROOM's local date, its weekday and
#: the zone name — `2026-09-08 Tue 02:13 HKT`. Measured 2026-09-08 (issue
#: #118): rendered as `2026-09-07 18:13:23 UTC`, the model resolved «今個禮拜六»
#: to the 13th in two of ten samples — it was doing calendar arithmetic across
#: a zone boundary with no reasoning budget. The weekday and the local day are
#: facts this process has; asking the model to derive them is a cost with no
#: upside, and the objective's own rule («resolve a relative date against the
#: message's timestamp») only works when that timestamp is the room's.
_LINE_TIME_FORMAT = "%Y-%m-%d %a %H:%M %Z"


def _batch_day(batch: Sequence[TranscriptRow], *, tz: _dt.tzinfo) -> int | None:
    """`yyyymmdd` for the newest row of a batch, in the room's zone — or `None`
    for an empty batch, which renders the index without distances."""
    if not batch:
        return None
    newest = max(row.ts for row in batch)
    day = _dt.datetime.fromtimestamp(newest / 1000, tz=tz).date()
    return day.year * 10000 + day.month * 100 + day.day


def _render_line(row: TranscriptRow, *, room: str, tz: _dt.tzinfo) -> str:
    """One attributed transcript line: `<id> [<local moment>] <name> (<room>):
    <body>` — attributed on every line, so the model can attribute an
    arrangement to a person, a moment and a room, which matters more here
    than for a triple: a note's `established_in` is a routing key the tag
    path reads later (`nora.domain.notes.Note`'s own docstring).

    `tz` is the operator's `[reminders] timezone`, the same zone the curator
    reads «today» from — the room's day, not the process's."""
    name = row.person_name or row.sender_name or row.sender_id
    body = row.body or _media_placeholder(row)
    ts = _dt.datetime.fromtimestamp(row.ts / 1000, tz=tz).strftime(_LINE_TIME_FORMAT)
    # ESCAPED, per R-13 (added 2026-09-10). These lines are members' own typed
    # messages, and the `<notes>` index is appended to the SAME prompt below
    # them — so an unescaped body could forge that block's tags even though
    # this line is not itself inside one.
    line = f"{row.id} [{ts}] {escape(name)} ({escape(room)}): {escape(body)}"
    if row.quoted_ts is not None:
        quoted = _dt.datetime.fromtimestamp(
            row.quoted_ts / 1000, tz=tz,
        ).strftime(_LINE_TIME_FORMAT)
        line += f" (reply to {quoted})"
    return line


def _find_anchor_match(
    *, anchor_date: int, standing: Sequence[Note],
) -> Note | None:
    """The standing note that already covers this occasion — the OLDEST one
    anchored to the same calendar day — or `None`.

    A comparison on the DATE alone, never on `title` (M10: two notes can
    share a title and have nothing to do with each other) and never on
    `anchor_entity_ids` (free text; see the module docstring for why a key
    over it is wrong in both directions and why the resolved alternative was
    removed). `standing` is oldest-first from either store, so "the oldest"
    is "the first" — the same survivor rule the curator's merge used, kept so
    a re-observation lands on one stable note across ticks.
    """
    for note in standing:
        if note.anchor_date == anchor_date:
            return note
    return None


def _adds_information(new_body: str, existing_body: str) -> bool:
    """Whether a candidate's body would tell the existing note something its
    head does not already say — the operational reading of design 33 §4's
    "only if the new proposal adds information the existing note lacks".

    **Syntactic, not semantic — a stated limitation, not an oversight.** A
    body is judged to add information when it is NOT already a substring of
    the existing head, case-insensitively with whitespace collapsed. That
    catches the common, cheap case this adaptor's own watermark makes
    likely — the same arrangement re-stated near-verbatim across several
    ticks of one room's conversation — without attempting to reconcile two
    DIFFERENTLY WORDED accounts of one fact, which is exactly the judgement
    `nora.language.note_curator`'s merge objective exists for and this
    tool-free, model-proposes-code-disposes adaptor has no model call left to
    spend on. A paraphrase of the same arrangement will be misread as "new"
    and appended as a revision; a literal restatement is the case this check
    actually catches. *The reading that would reopen this*: a measured rate
    of paraphrase-triggered spurious revisions worth spending a model call to
    avoid.
    """
    def _norm(text: str) -> str:
        return " ".join(text.split()).casefold()

    return _norm(new_body) not in _norm(existing_body)


class NoteCandidateAdaptor:
    """Phase A of the note tier's create path — see the module docstring."""

    name = "note-candidates"
    key_kind = "scope"
    uses_watermark = True
    objective = "note-candidates"
    envelope_key = "candidates"
    #: TOOL-FREE (design 33 §4.1): read-only awareness would be permitted for
    #: a scope-keyed adaptor, but this one's design keeps it tool-free anyway,
    #: the cost split every extractor-shaped pass makes — one cheap call
    #: decides what is worth carrying forward, and a
    #: tool call per candidate is a cost nobody asked for here.
    tools: frozenset[str] = frozenset()

    _VOCABULARIES: ClassVar[dict[str, tuple[str, frozenset[str]]]] = {
        "nora_note_candidate_gate_total": ("action", GATE_ACTIONS),
    }

    def __init__(
        self,
        *,
        notes: _NoteStore,
        candidates: _CandidateStore,
        workspace_for: Callable[[str], str | None],
        metrics: Metrics | None = None,
        # The ROOM's zone for rendering each message's moment (see
        # `_LINE_TIME_FORMAT`). Defaults to UTC so a bare construction still
        # renders a true moment; the composition root passes `[reminders]
        # timezone`, the zone the curator already reads «today» from.
        timezone: _dt.tzinfo = _dt.UTC,
    ) -> None:
        self._notes = notes
        self._candidates = candidates
        self._workspace_for = workspace_for
        self._metrics = metrics
        self._tz = timezone
        # Per key, written by `inputs` and read by `accept` — the only
        # channel between them (`DreamAdaptor.accept` takes no batch).
        # Keyed by `key`, not a bare attribute, because
        # `dream_loop` dispatches each scope as its own task.
        self._message_ids: dict[str, frozenset[int]] = {}

    def _inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        vocab = self._VOCABULARIES.get(name)
        if vocab is not None:
            label, allowed = vocab
            got = labels.get(label)
            if got not in allowed:
                raise ValueError(
                    f"{name}{{{label}={got!r}}} is not in this metric's "
                    f"vocabulary {sorted(allowed)}. A typo here creates a new "
                    f"series rather than an error, and the old one silently "
                    f"goes flat"
                )
        if self._metrics is not None:
            self._metrics.inc(name, value, **labels)

    # -- inputs ---------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[TranscriptRow]) -> str:
        """The room's message batch, attributed, plus the workspace's standing
        note index — so the model can propose "not written down yet" and
        decline to repeat what already is (§4: "if this arrangement is
        already written down, propose an update instead, or nothing").

        `batch` empty renders `""`: `DreamAdaptor` has no `should_fire` verb,
        the runtime's fire condition is "did `inputs` render anything", and a
        quiet room must cost nothing — the runtime's own docstring on the
        blank-prompt fire condition, restated because it is load-bearing here.
        """
        self._message_ids[key] = frozenset(row.id for row in batch)
        if not batch:
            return ""
        workspace = self._workspace_for(key)
        room = workspace or key
        lines = [
            _render_line(row, room=room, tz=self._tz)
            for row in batch[:MAX_RENDERED_MESSAGES]
        ]
        if len(batch) > MAX_RENDERED_MESSAGES:
            omitted = batch[MAX_RENDERED_MESSAGES:]
            lines.append(
                f"... and {len(omitted)} more messages "
                f"(ids {omitted[0].id}..{omitted[-1].id}) not shown"
            )
        parts = ["Messages in this batch:\n" + "\n".join(lines)]
        if workspace is not None:
            # WITH TODAY: the gate this index exists for compares on the
            # anchor date alone, so a reader asked not to duplicate a note
            # needs to see the property the comparison uses.
            # WITH TODAY: the gate this index exists for compares on the
            # anchor date alone, so a reader asked not to duplicate a note
            # needs to see the property the comparison uses.
            #
            # Today comes from the NEWEST row of the batch in the room's own
            # zone, not from a wall clock — the same basis the objective's
            # relative-date resolution reads from, so «today» in the index and
            # «today» in the transcript cannot disagree.
            index = render_notes(
                await self._notes.standing(workspace=workspace),
                today=_batch_day(batch, tz=self._tz),
            )
            if index:
                parts.append(index)
        return "\n\n".join(parts)

    # -- accept -----------------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """Typed parse into `_CreateCandidate`, dropping a malformed item
        silently (`DreamAdaptor.accept`'s own contract) and dropping any item
        naming a `message_ids` entry outside this run's batch —
        because an invented id is fabricated provenance, worse than a missing entry.
        """
        seen = self._message_ids.get(key)
        parsed: list[object] = []
        for item in items:
            candidate = _parse_candidate(item)
            if candidate is None:
                continue
            if seen is not None and not set(candidate.message_ids) <= seen:
                log.warning(
                    "note-candidates dropped: message_ids %s are not all in "
                    "the batch (scope=%s)",
                    sorted(set(candidate.message_ids) - seen), key,
                )
                continue
            parsed.append(candidate)
        return tuple(parsed)

    # -- commit -----------------------------------------------------------

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """Stage, gate, materialize — see the module docstring for the three
        steps. `key` is a SCOPE and both the candidate table and the note
        store are keyed on the WORKSPACE (DEC-105), so it is translated —
        and an unresolvable scope RAISES rather than guessing, because filing a
        candidate under the scope id would hide it from every workspace-level
        read, and filing it under a guessed workspace lets one social unit's
        proposal enter another's notebook.
        """
        workspace = self._workspace_for(key)
        if workspace is None:
            raise LookupError(
                f"note-candidates: scope {key!r} resolves to no workspace, "
                f"so there is no key to file its candidates under."
            )
        written = 0
        for proposal in proposals:
            candidate = proposal.payload
            if not isinstance(candidate, _CreateCandidate):
                raise TypeError(
                    f"NoteCandidateAdaptor.commit only accepts a "
                    f"_CreateCandidate payload, got {type(candidate).__name__}"
                )
            written += await self._settle(
                workspace=workspace, established_in=key, candidate=candidate,
                proposal=proposal, now_ms=now_ms,
            )
        return written

    async def _settle(
        self, *, workspace: str, established_in: str,
        candidate: _CreateCandidate, proposal: Proposal, now_ms: int,
    ) -> int:
        row = await self._candidates.record_create_candidate(
            workspace=workspace, title=candidate.title,
            description=candidate.description, body=candidate.body,
            closing_condition=candidate.closing_condition,
            anchor_date=candidate.anchor_date,
            anchor_entity_ids=candidate.anchor_entity_ids,
            message_ids=candidate.message_ids, now_ms=now_ms,
        )
        # A NEW sighting of this occasion, whichever workspace-record trigger
        # counts staging (see `nora.obs.metric_specs`'s `trigger="create"`).
        # `attempts == 0` is the store's own tell for "freshly inserted, not
        # a re-observation" (`record_create_candidate`'s own contract).
        if row.attempts == 0:
            self._inc("nora_note_candidates_total", trigger="create")
        if row.status != CANDIDATE_PENDING:
            # A re-observation of an occasion this tier already decided.
            # Not a decision — the decision already happened — so it is not
            # counted on the gate's own vocabulary; `attempts` above already
            # says the row was seen again.
            return 0

        standing = await self._notes.standing(workspace=workspace)
        # Always `()`/`{}` in production — see `_find_anchor_match`'s own
        # docstring for why entity resolution is out of scope here.
        match = _find_anchor_match(anchor_date=row.anchor_date, standing=standing)
        if match is None:
            return await self._create(
                workspace=workspace, established_in=established_in, row=row,
                proposal=proposal, now_ms=now_ms,
            )
        return await self._update(
            workspace=workspace, row=row, match=match, proposal=proposal,
            now_ms=now_ms,
        )

    async def _create(
        self, *, workspace: str, established_in: str, row: NoteCreateCandidate,
        proposal: Proposal, now_ms: int,
    ) -> int:
        try:
            payload = NoteCreationProposal(
                title=row.title, description=row.description,
                # See the module docstring's "uncertainty is set equal to
                # description" note for why this is deliberate rather than a
                # placeholder.
                uncertainty=row.description,
                closing_condition=row.closing_condition,
                established_in=established_in, body=row.body,
                anchor_date=row.anchor_date,
                anchor_entity_ids=row.anchor_entity_ids,
                # design 34 §5.3 resolution (c): a gate-created note's first
                # wake is a normal SCHEDULED wake, not a strike bootstrap.
                # Same formula `nora.language.note_curator` uses after every
                # later wake, so the two callers of `default_revisit_at`
                # agree about what "next revisit" means for this note's own
                # anchor shape.
                revisit_at=default_revisit_at(
                    anchor_date=row.anchor_date, now_ms=now_ms,
                ),
            )
        except ValueError:
            log.warning(
                "note-candidates: refused a malformed create candidate "
                "(workspace=%s)", workspace,
            )
            await self._candidates.set_create_candidate_status(
                workspace=workspace, candidate_id=row.id,
                status=CANDIDATE_REFUSED, now_ms=now_ms,
            )
            self._inc("nora_note_candidate_gate_total", action="refused")
            return 0
        try:
            ids = await self._notes.propose(
                key=workspace,
                proposals=(
                    Proposal(
                        payload=payload,
                        instruction_sha256=proposal.instruction_sha256,
                        model=proposal.model,
                        established_by=proposal.established_by,
                    ),
                ),
                now_ms=now_ms,
            )
        except Exception:
            # Left PENDING, not refused: a store fault is `unavailable`, not
            # a verdict about this occasion, and the next tick re-selects it
            # because the candidate table is live state rather than a
            # cursor.
            log.exception(
                "note-candidates: create failed (workspace=%s)", workspace,
            )
            self._inc("nora_note_candidate_gate_total", action="deferred")
            return 0
        await self._candidates.set_create_candidate_status(
            workspace=workspace, candidate_id=row.id,
            status=CANDIDATE_CREATED, now_ms=now_ms,
        )
        self._inc("nora_note_candidate_gate_total", action="created")
        return len(ids)

    async def _update(
        self, *, workspace: str, row: NoteCreateCandidate, match: Note,
        proposal: Proposal, now_ms: int,
    ) -> int:
        current = await self._notes.read(workspace=workspace, note_id=match.id)
        if current is None:
            # The matched note retired or moved out of reach between the
            # gate's read and this one. Left pending: the next tick's
            # `standing()` read reflects whatever is true then.
            log.info(
                "note-candidates: matched note %s vanished before the "
                "update landed (workspace=%s)", match.id, workspace,
            )
            self._inc("nora_note_candidate_gate_total", action="deferred")
            return 0
        _, head = current
        if not _adds_information(row.body, head.body):
            await self._candidates.set_create_candidate_status(
                workspace=workspace, candidate_id=row.id,
                status=CANDIDATE_REFUSED, now_ms=now_ms,
            )
            self._inc("nora_note_candidate_gate_total", action="refused")
            return 0
        payload = NoteRevisionProposal(
            note_id=match.id, body=row.body,
            parent_revision_id=match.head_revision_id,
            entity_ids=row.anchor_entity_ids,
        )
        try:
            ids = await self._notes.propose(
                key=workspace,
                proposals=(
                    Proposal(
                        payload=payload,
                        instruction_sha256=proposal.instruction_sha256,
                        model=proposal.model,
                        established_by=proposal.established_by,
                    ),
                ),
                now_ms=now_ms,
            )
        except StaleNoteHeadError:
            # The tag path (or another candidate this same tick) revised the
            # note between the gate's read and this write. NOT an error —
            # the same reading `NoteCuratorAdaptor.commit` gives a lost CAS
            # on a merge — left pending for the next tick.
            log.info(
                "note-candidates: update raced the note's head (workspace=%s, "
                "note=%s)", workspace, match.id,
            )
            self._inc("nora_note_candidate_gate_total", action="deferred")
            return 0
        except Exception:
            log.exception(
                "note-candidates: update failed (workspace=%s, note=%s)",
                workspace, match.id,
            )
            self._inc("nora_note_candidate_gate_total", action="deferred")
            return 0
        await self._candidates.set_create_candidate_status(
            workspace=workspace, candidate_id=row.id,
            status=CANDIDATE_CREATED, now_ms=now_ms,
        )
        self._inc("nora_note_candidate_gate_total", action="updated")
        return len(ids)
