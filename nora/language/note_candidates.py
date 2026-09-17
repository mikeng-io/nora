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
2. **Gate.** A DETERMINISTIC RESOLUTION — `_resolve_updates`, over the title
   the MODEL named in its `updates` field — decides whether a standing note
   already covers this occasion. The model says which note it means; the code
   says whether that note exists, and exactly once. Meaning is the model's
   job; existence is the code's.
3. **Materialize or refuse.** No match: the candidate becomes a
   `NoteCreationProposal`, written through `DreamSink.propose` exactly as the
   tag path's future create tool would. A match: the candidate becomes a
   `NoteRevisionProposal` against the matched note's own head whose body is
   the head's text with the candidate's **appended** (`_appended`) — never
   the candidate's alone — and only if it adds something the existing head
   lacks (`_adds_information`); otherwise it is refused as a duplicate, or as
   `full` when the appended body would pass `BODY_MAX_CHARS`. Either way the
   candidate row is advanced to a TERMINAL status (`created`/`refused`) so a
   re-observation of the same occasion never relitigates a settled decision.

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
an update or nothing, not a duplicate create.*

**And since 2026-09-13 the gate READS the answer to that question instead of
throwing it away (#228).** The model was already asked which standing note a
candidate belongs to; `commit` ignored what it concluded and re-decided on
the anchor date. The recorded reason for that refusal was §4's — "relying on
the model to decline is self-attestation on a destructive operation" — and
**the operation is no longer destructive.** A revision from this path
APPENDS to the head rather than replacing it (`_appended`, the first half of
#228), so neither way a wrong judgement here can go DESTROYS text: naming the
wrong note conflates two arrangements into one entry whose halves are both
still readable, and naming nothing writes a second entry beside the first.
A self-attestation bar sized for destruction is the wrong bar for a write
that cannot destroy. That is the whole argument this change rests on: the
reason to refuse the model's judgement dissolved when the loss did.

**NOTHING MERGES A DUPLICATE, and this argument does not get to pretend
otherwise (#236).** The curator's merge verb was deleted on 2026-09-08 —
`nora.language.note_curator`'s own first line, and `NoteSupersedeReason.ALL`
no longer names `merged` — and part of what made that safe to do was THIS
gate's date match handling duplicates where they are first seen, which is
the thing the paragraph above removes. What actually bounds a duplicate
today is ordinary retirement and nothing else: the curator selects on a
passed anchor or a due `revisit_at`, and retires only on a grounded `closed`
citation or on `expired`/`moot`, which `_covers` refuses unless the evidence
window reaches PAST the anchor and BACK before the note was written. So a
duplicate retires on its own anchor, on the same terms as the note it
duplicates, and never because it is a duplicate — and in a room that goes
quiet after the anchor, `_covers` refuses both copies and both stand. The
`<notes>` index is UNCAPPED by deliberate decision
(`nora.language.note_render`), so every live duplicate renders on every tag
until then. **Duplicates are bounded in COUNT by nothing at all, and in TIME
only where the room keeps talking.** The TIME half read «bounded in TIME»
flat until 2026-09-13, one sentence after the sentence that disproves it:
`_covers` (`nora.language.note_curator`) returns `False`
on `bounds is None or not evidence`, and requires `evidence[-1].ts >= end`
— so a room that falls silent after the anchor retires nothing, and silence
after an arrangement is exactly what produces the duplicates. A duplicate in
a quiet room is bounded by neither axis and stands until somebody speaks.

That is the real cost, stated rather than assumed away, and the reason it is
still the side to fail toward is that it is VISIBLE and ADDITIVE where the
other side is neither: a second entry is something a reader can see and act
on, while two arrangements appended into one entry look like one arrangement
and read as authored. What makes "state it and measure it" a position rather
than a shrug is that the rate is readable — `ambiguous` and `unresolved` are
distinct gate actions precisely so the two ways this gate deliberately
writes a duplicate are counted apart from an ordinary create. #236 carries
the bound itself and is not decided here.

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
That reading is unchanged and is why a resolved entity set (issue #100) is
not what replaced the date either — the registry that would have minted one
was removed on 2026-09-08 and nothing mints entities today.

**`anchor_date` is WHEN THE THING HAPPENS, and as of 2026-09-13 it is
nothing else (#228).** It was the gate's whole match key from 2026-09-08,
the deliberately looser reading: two arrangements on one day became one note
rather than two. The reversal condition this docstring wrote for that choice
— *"two genuinely different occasions on one date in one workspace, updated
into each other"* — was MET in production on 2026-09-13. The update arm had
fired exactly twice since the tier shipped and both times it merged two
genuinely different occasions: a sign-up deadline and a games thread that
shared nothing but a calendar day, and a gathering note overwritten forty
minutes later by an unrelated room's pass (notes are workspace-keyed, so
every room in a workspace wrote onto whichever note shared the date). Two
firings, two collisions, is not a rate to tune — it is a key that is wrong.

So the date is out of the key entirely, and `_find_anchor_match` is
**DELETED rather than kept as a fallback**. A date fallback would fire on
exactly the candidates the model did NOT name a note for — which is to say
on the ones it judged to be new — so it is a second, weaker opinion
overruling the first, on the same axis, with no way to tell from the metric
which one decided. It would restore the collision this change exists to
remove, on the population where the collision is most likely, and it would
do it silently. A fallback that quietly restores the old behaviour is worse
than no fallback. What `anchor_date` still IS: the note's own `revisit_at`
seed, the "in N days" the index renders, the staging key's first half, and
what the curator reads to ask whether a moment has passed. All four are
questions about time, which is what the field was always answering.

**What replaces it, and which way it fails.** The candidate carries an
optional `updates` — the title of the standing note it revises, *exactly as
the index showed it* — the same contract the tag path's `note_update` uses
for its own `title` argument, resolved through the same
`nora.domain.notes.note_handle` normalisation so the two cannot drift apart.
`commit` resolves it against the workspace's standing notes:

- exactly one match -> REVISION (appended, still gated by `_adds_information`
  and the `full` ceiling check);
- absent -> CREATE, counted `created`: the model said this is new, and it is
  the common answer;
- present but resolving to NOTHING -> **CREATE, counted as `unresolved`** —
  the model named a title no standing note answers to. It lands in the same
  place as `created` and it is a different reading, which is why it is a
  different action: this is the model mistyping or inventing a title, the
  one failure mode this whole design rests on being rare, and until
  2026-09-13 it was counted plain `created` with no log and no metric —
  indistinguishable from a candidate that named nothing at all. A design
  whose safety rests on a rate must be able to read that rate (AGENTS.md:
  never read an instrument's output as evidence without first checking it
  can measure the thing);
- resolving to MORE than one -> **CREATE, counted as `ambiguous`** — never a
  guess. `_resolve_updates` returns every match rather than the first for
  the reason `nora.agent.tools._resolve` does: `standing()` is ordered, so
  "the first one" is a stable, plausible, wrong answer. Appending to the
  wrong note conflates two arrangements and is the failure this whole change
  removes; a duplicate is visible and overwrites nothing. The gate fails
  toward the side that loses no text, and `ambiguous` is a distinct action
  so the rate of it is readable rather than hidden inside `created`.

*The reading that would reopen this:* a measured rate on `ambiguous` +
`unresolved` — at which point the answer is a bound on the pile (#236), not
a code-side guess at which note the model meant.

**A re-observation that lands on a SETTLED staging row is counted too, and
which kind it is matters (#235).** `note_candidate`'s uniqueness is
`(workspace, anchor_date, anchor_entity_ids, lower(btrim(title)))`, so a
candidate can be handed back a row an earlier candidate already settled and
returns before the gate ever sees it. Two very different things reached that
branch — the same occasion observed again (a benign no-op) and a DIFFERENT
arrangement destroyed by the key (a real loss) — and until 2026-09-13 they
had an identical counter signature, which is the `unavailable`-reported-as-
`nothing_found` shape R-27 exists to prevent. `_settle` separates them on the
one axis #228 made identity — the title, through the same `note_handle` — and
reports them as `nora_note_candidate_staging_total{outcome}`
(`STAGING_OUTCOMES`).

**The collision itself is fixed as of 2026-09-16: the title is in the key.**
The entity list was supposed to separate two arrangements on one day, and was
measured not to — a candidate naming two people was destroyed by a same-day
row naming the same two, because in a fixed-membership room the cast is a
constant and the key degenerated to `(workspace, anchor_date)`. M10 is upheld
for the NOTE (a note stays findable by one name across revisions) and
reversed for the staging row, which is a one-shot dedupe slot for one
sighting rather than an identity. What that buys is a silent, subtractive
failure traded for a visible, additive one: a re-described occasion now
stages twice and the gate may write a duplicate, which nothing merges
(#236). `displaced` survives in the vocabulary because a loss counter that
cannot fire is the right state for a loss that no longer happens — deleting
it would let a re-widening of the key land silent.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar, Protocol

from nora.domain.blocks import ContextBlock, render_elements, speaker_name
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
    note_handle,
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
#: The ceiling on `updates` — the title of the standing note a candidate says
#: it revises. **Deliberately NOT `TITLE_MAX_CHARS`.** The value is the title
#: as the model SAW it, and the index ESCAPES what it renders (R-13), so an
#: escaped title is longer than the stored one and 「&」 alone costs five
#: characters there. Capping at the title's own limit would silently refuse
#: exactly the notes whose titles carry punctuation — the class of failure
#: `note_handle`'s own docstring records from the tag path. Six times the cap
#: is past anything `escape` can produce from sixty characters, and still
#: short enough that a model pasting a sentence instead of a title is refused.
MAX_UPDATES_CHARS = TITLE_MAX_CHARS * 6
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

#: How many awaited collaborator CALL SITES `inputs` and `commit` hold between
#: them — the number `DreamerRuntime` multiplies by `[dreamer]
#: store_timeout_seconds` to bound each of them (issue #60). It lives HERE, in
#: the module that makes the calls, for the reason
#: `STANDING_STORE_CALLS_PER_FOLD` lives beside the stage that makes its own:
#: a bound derived from a number written somewhere else drifts silently, and
#: the drift is invisible because a wider hold looks exactly like a healthy
#: one until something wedges.
#:
#: One `standing()` in `inputs`; in `commit`, per candidate,
#: `record_create_candidate`, a second `standing()`, and then either the
#: create arm (`propose` + up to two `set_create_candidate_status`) or the
#: update arm (`read`, `propose`, two `set_create_candidate_status`).
#: `tests/architecture/test_dream_store_calls_are_declared.py` counts them off
#: this module's AST and reddens when a call is added without this number
#: moving.
STORE_CALLS_PER_TICK = 10

#: Every value `nora_note_candidate_gate_total{action}` may carry, DERIVED
#: rather than hand-copied into the metric's help text (`nora.obs.
#: metric_specs`) — `nora.language.note_curator.CURATOR_ACTIONS`' own
#: precedent, enforced at RUNTIME because the metric call this value reaches
#: (`Metrics.inc`/`.observe`, `nora/obs/metrics.py`) takes `**labels: object`
#: — mypy cannot check a string passed through an `object` parameter no
#: matter which module it runs on, so a `Literal` alone would be checked by
#: nothing here. (`nora/language` is not out of mypy's scope: issue #61
#: closed 2026-09-11 and `make typecheck` names this package explicitly.)
GATE_ACTIONS: frozenset[str] = frozenset({
    "created",          # materialized as a brand new note
    "updated",          # materialized as a revision APPENDED to the standing
                        # note the model NAMED in `updates`. Carried
                        # «unreachable today» until 2026-09-13, which was
                        # false by then: it had fired twice in production, and
                        # both times it took a note the model never named and
                        # replaced its body (#228).
    "ambiguous",        # the model named a title that answers to MORE than
                        # one standing note. Materialized as a CREATE rather
                        # than a guess — a duplicate overwrites nothing and is
                        # visible to a reader, appending to the wrong note is
                        # the conflation #228 exists to remove. Counted apart
                        # from `created` so the rate of it is readable; exactly
                        # one action still lands per settled candidate, so the
                        # sum over this vocabulary stays the number decided.
    "unresolved",       # the model named a title NO standing note answers to
                        # — a mistyped or invented title. Also a CREATE, and a
                        # different reading from `created`: this is the one
                        # failure mode the whole design rests on being rare,
                        # and it was counted plain `created` with no log and
                        # no metric until 2026-09-13, which made its rate
                        # unmeasurable. Same invariant as `ambiguous`: one
                        # action per settled candidate.
    "refused",          # a duplicate with nothing new, or malformed
    "full",             # the appended body would pass BODY_MAX_CHARS — the
                        # revision is refused rather than truncated, and a
                        # rate here says the curator is not retiring
    "deferred",         # a transient failure; left pending, retried next tick
})

#: Every value `nora_note_candidate_staging_total{outcome}` may carry, derived
#: from the code for the same reason `GATE_ACTIONS` is. This is the SETTLED-ROW
#: branch of `_settle`, which the gate's own vocabulary deliberately does not
#: cover: nothing is decided there, because the decision already happened.
#:
#: **Both values exist so that neither is silence.** `note_candidate`'s
#: uniqueness was `(workspace, anchor_date, anchor_entity_ids)` until
#: 2026-09-16, so a candidate that shared a day with an already-settled one —
#: and named no people, which was the default — was handed back that settled
#: row and returned before the gate. Two different events reach that branch and
#: they had an IDENTICAL counter signature until 2026-09-13:
#: `nora_note_candidates_total` minus `nora_note_candidate_gate_total` could
#: not tell a destroyed proposal from a benign no-op, which is R-27's
#: `unavailable`-as-`nothing_found` one layer down. Splitting them is what made
#: the drop readable.
#:
#: **#235 then removed the drop**: the key is now `(workspace, anchor_date,
#: anchor_entity_ids, lower(btrim(title)))`, so two arrangements on one day are
#: two staged rows and the second reaches the gate. `displaced` is KEPT, with
#: its branch, and is expected to read zero — the key normalises with Postgres
#: `lower()` while the branch judges with `note_handle`'s `casefold()`, and a
#: residue exists only where those disagree. A loss counter that no longer
#: fires is the right state for a loss that no longer happens; removing it
#: would let a re-widening of the key land silent, which is the exact defect
#: this series was declared to close.
STAGING_OUTCOMES: frozenset[str] = frozenset({
    "reobserved",       # the SAME arrangement seen again after the gate
                        # settled it — the benign case, and the one the
                        # staging table exists to absorb. Nothing was lost.
    "displaced",        # a DIFFERENT arrangement landed on a settled row and
                        # was dropped before the gate saw it. A real loss: no
                        # note, no gate action, and no second chance, because
                        # the row is terminal. EXPECTED TO READ ZERO since
                        # 2026-09-16 (#235) — the title joined the staging key,
                        # so two arrangements on one day are two rows. Judged
                        # on the TITLE through `note_handle`, while the key
                        # normalises with Postgres `lower()`; the residue is
                        # exactly where those two disagree. Kept rather than
                        # deleted: `displaced` errs toward «something may have
                        # been lost», which is the direction a loss counter has
                        # to fail in, and a re-widening of the key must not
                        # land silent.
})

#: **What this series does NOT see, stated because a loss counter that reads
#: as total is worse than none.** It only fires on a SETTLED row. A different
#: arrangement landing on a key whose row is still `pending` — left that way
#: by a `deferred` outcome — overwrites the first proposal's `title`,
#: `description`, `body` and `closing_condition` in place and increments
#: nothing here: `SqlNoteStore.record_create_candidate`'s upsert sets those
#: columns from `excluded` under `where status = 'pending'`
#: (`nora.infra.sql.notes`), and `nora.testing.note_store` matches it. So the
#: `displaced` residue is two holes, not one — two arrangements sharing the
#: key AND a title, and a collision on a row the gate has not reached yet.
#: Both are #235's collision seen from different sides, both are narrow and
#: both pre-date #228; neither is fixed here. The counter is a floor on the
#: loss, never a measure of it.


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
    #: The title of the standing note this candidate REVISES, exactly as the
    #: index rendered it — `None` for "this is new". The model's own judgement
    #: about meaning, resolved to a note (or not) by `_resolve_updates`.
    #:
    #: **Not staged.** `record_create_candidate` never sees it, and that is
    #: deliberate rather than an omission: `note_candidate` records the
    #: OCCASION (its key is `(workspace, anchor_date, anchor_entity_ids,
    #: lower(btrim(title)))`),
    #: while this is a routing decision about THIS sighting of it. Staging it
    #: would need a migration to store a value the gate reads once and never
    #: reads again.
    updates: str | None = None


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

    # `updates`: OPTIONAL, and the ways it can fail to name a note are not
    # all the same thing.
    #
    # Missing, `None`, or an EMPTY/whitespace-only string is "this is new" —
    # a real answer and the common one. The empty string was refused until
    # 2026-09-13, on the argument below about a garbled value, and the cost
    # was badly asymmetric to the risk: `""` is one of the commonest model
    # spellings of "not applicable", it is not a garbled title (there is
    # nothing in it to have garbled), and refusing it threw away the WHOLE
    # note proposal — title, body, closing condition and all — silently. The
    # worst case of reading it as absent is a create the model may have meant
    # as an update, which is the side that overwrites nothing and is counted
    # (`created`); the worst case of refusing it is a note that is never
    # written at all, counted nowhere.
    #
    # **And `created` is where it stops: `""` is indistinguishable afterwards
    # from a model that omitted the field, so THIS change's own rate is not
    # readable.** Deliberate, not an oversight. The two spellings mean the
    # same thing — «this is new» — so a series that told them apart would
    # measure the model's JSON style rather than anything the gate decided,
    # and it would break the one invariant `GATE_ACTIONS` rests on (exactly
    # one action per settled candidate, so the sum is the number decided).
    # What IS readable is the thing that would say this went wrong:
    # `unresolved` and `ambiguous` are the failure modes an over-eager
    # «absent» reading would inflate, and both are counted.
    #
    # Present and genuinely unusable — not a string, or past
    # `MAX_UPDATES_CHARS` — is still the model having TRIED to name a note and
    # failed, so the WHOLE candidate goes, exactly as a malformed
    # `anchor_entity_ids` does. Collapsing THAT into absent would turn a
    # garbled title into a silent create: a duplicate arriving with no tell
    # that anything went wrong. Kept RAW — `note_handle` normalises at
    # resolution time, and a log line about a title that resolved to nothing
    # has to show what the model actually wrote.
    updates: str | None = None
    raw_updates = item.get("updates")
    if isinstance(raw_updates, str) and not raw_updates.strip():
        raw_updates = None
    if raw_updates is not None:
        updates = _text(raw_updates, limit=MAX_UPDATES_CHARS)
        if updates is None:
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
        updates=updates,
    )


def _media_placeholder(row: TranscriptRow) -> str:
    """What a bodiless row renders as. Deliberately terse — this adaptor does
    not need to distinguish media TYPES, only to show that a row without text
    is still shown (with its id, so `accept`'s message-id
    membership check sees the same set `inputs` rendered)."""
    return "[media]" if row.media_json else ""


#: What the `<room>` block is, said to the model in the block's own `note`
#: attribute — the shape every injected block in this tree follows: the first
#: half states the axis the block is true on, the second is the shared
#: not-an-instruction clause (`nora.language.note_render.NOTES_BLOCK_NOTE`'s
#: own precedent).
#:
#: The second half is load-bearing rather than ceremonial here. The contents
#: are OPERATOR-authored prose, which is the one kind of text in this prompt a
#: model has every reason to read as a directive — so the block says, in the
#: place the model reads before the prose, that it is a description of what it
#: is about to read and not a task.

#: The room's name when the allowlist gives neither a label nor a workspace.
#: NOT the conversation id, which is what this rendered before issue #161 and
#: is exactly the shape R-18/R-28 keep off a wire: a room's identifier is not
#: its name, and a model told to attribute an arrangement to a room has no use
#: for one.
UNNAMED_ROOM = "unnamed room"


def _room_block(*, room: str, description: str | None) -> str:
    """The ONE block that says what room this batch is from — rendered above
    the batch, once, never per line.

    **Why it exists** (issue #161). The batch lines have always carried a room
    per line, and until this block they carried the WORKSPACE's name instead:
    on a workspace holding a Signal group, a WhatsApp group and a Discord
    games channel, every line of every batch read `(friends)`, so the model
    could not tell the three rooms apart and nothing in the prompt said what
    kind of room it was reading. It wrote «Bath gathering» twice out of a
    games room's shorthand — 「2200」 is when people log on there, not an
    appointment — and under the objective as written that was the correct
    reading of the input it was given. The instruction was not the broken
    half; the input was.

    **Why the description is the operator's and not a model's.** What a room
    IS cannot be derived from the room's own messages without the judgement
    the pass is trying to make. The operator is the only party who can state
    it, so it is stated in `policy.toml` (`Conversation.description`) and
    rendered verbatim.

    Absent description renders the block anyway, self-closing: the room's
    name is the half that always exists, and a room with no prose about it
    must still be a different room from its neighbour.
    """
    block = ContextBlock(
        name="room", attrs=(("label", room),),
        # The OPERATOR's sentence and the room's own name. Neither is
        # member-authored, so the marker would be false — and this block is
        # what tells the model how to read the ones that are.
        body=escape(description or ""),
    )
    if not description:
        # Self-closing when the operator wrote nothing: `ContextBlock.render`
        # always emits a body, and an empty one would read as «the operator
        # said this room is nothing» rather than «nobody has said».
        return block.render().split(">", 1)[0] + "/>"
    # ESCAPED for the same reason a member's line is (R-13): this is
    # file-authored text landing above blocks with real tags in them, and
    # "the operator wrote it" is a reason to trust the CONTENT, never a
    # reason to let it close a tag it did not open — `ContextBlock` escapes the
    # body and `escape_attr` closes the label's quote.
    return block.render()


#: How a message's moment is rendered: the ROOM's local date, its weekday and
#: the zone name — `2026-09-08 Tue 02:13 HKT`. Measured 2026-09-08 (issue
#: #118): rendered as `2026-09-07 18:13:23 UTC`, the model resolved «今個禮拜六»
#: to the 13th in two of ten samples — it was doing calendar arithmetic across
#: a zone boundary with no reasoning budget. The weekday and the local day are
#: facts this process has; asking the model to derive them is a cost with no
#: upside, and the objective's own rule («resolve a relative date against the
#: message's timestamp») only works when that timestamp is the room's.
#: The zone stays on the rendered moment for exactly that reason, now as the
#: `at` ATTRIBUTE of each `<m>` rather than a bracketed field on a flat line
#: (#292). The `RowStyle` that used to describe the line grammar is gone with
#: the line: `render_elements` has no handle or time style to choose, because
#: an attribute's name says what it is.

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
    """One attributed message, as its own element:
    `<m n="<id>" at="<local moment>" from="<name>" room="<room>">body</m>` —
    attributed on every message, so the model can attribute an
    arrangement to a person, a moment and a room, which matters more here
    than for a triple: a note's `established_in` is a routing key the tag
    path reads later (`nora.domain.notes.Note`'s own docstring).

    `room` is the ROOM's own name, which this docstring promised and did not
    get for the first five weeks of the adaptor's life: the caller handed it
    the WORKSPACE, so every room of a workspace rendered the same string and
    the promise above was false (issue #161). It is never a conversation id —
    `inputs` resolves it, and `UNNAMED_ROOM` is the floor.

    `tz` is the operator's `[reminders] timezone`, the same zone the curator
    reads «today» from — the room's day, not the process's."""
    def _when(other_ts: int) -> str:
        return _dt.datetime.fromtimestamp(
            other_ts / 1000, tz=tz,
        ).strftime(_LINE_TIME_FORMAT)

    def _attrs(r: TranscriptRow, _i: int) -> tuple[tuple[str, str | None], ...]:
        # `n` IS THE ROW ID HERE, not the position — `accept()` validates the
        # model's `message_ids` against this batch and drops any item naming an
        # id outside it ("an invented id is fabricated provenance"). A
        # positional index would fail that join silently and empty the pass.
        return (
            ("n", str(r.id)),
            ("at", _dt.datetime.fromtimestamp(
                r.ts / 1000, tz=tz).strftime(_LINE_TIME_FORMAT) if r.ts else None),
            # `or "unknown"`, NOT `or row.sender_id` — the raw id is an E.164
            # or an ACI, and this block is a prompt (R-18, DEC-80).
            ("from", speaker_name(r.person_name, r.sender_name)),
            ("room", room),
            ("reply_to", None if r.quoted_ts is None else _when(r.quoted_ts)),
        )

    return render_elements(
        [row], tag="m", attrs=_attrs,
        body=lambda r: r.body or _media_placeholder(r),
    )


def _resolve_updates(
    *, updates: str | None, standing: Sequence[Note],
) -> tuple[Note, ...]:
    """The standing notes that answer to the title the model named — `()` when
    it named none, or named one nothing answers to.

    **EVERY match, never the first.** `standing()` is ordered, so "the first
    one" would be a stable, plausible, wrong answer on an ambiguous title —
    `nora.agent.tools._resolve`'s own argument, and this function exists in
    the shape it does so the two resolutions of one contract cannot drift.
    The caller decides what two means; this only says how many there are.

    Normalised through `nora.domain.notes.note_handle` on BOTH sides, which
    is what makes the model's answer comparable at all: the index escapes
    what it renders (R-13), so «Dinner & drinks» reaches the model as
    «Dinner &amp;amp; drinks» and a raw `==` would never match the note it
    plainly named. `note_handle` moved into the domain for this caller.

    **`_find_anchor_match` — a match on the anchor date alone — was DELETED
    here on 2026-09-13, not demoted to a fallback (#228).** The module
    docstring carries the argument in full; the short form is that a date
    fallback fires precisely on the candidates the model declined to mark as
    updates, so it is a weaker second opinion silently overruling the first
    on the population where it is most likely to be wrong.
    """
    if updates is None:
        return ()
    wanted = note_handle(updates)
    return tuple(n for n in standing if note_handle(n.title) == wanted)


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


def _appended(existing_body: str, new_body: str) -> str:
    """The head a revision of `existing_body` by `new_body` should carry: the
    existing text, then the new text, as one body.

    **The verb was always APPEND, and the code did the opposite.** Both this
    module's docstring ("two arrangements on one day become one note with an
    appended body rather than two notes") and `NoteRevisionProposal`'s ("a
    revision to append") say what a revision from this path is, and
    `_adds_information` above describes its own miss as a paraphrase
    "appended as a revision". `_update` nonetheless handed the candidate's
    body to the proposal on its own, which REPLACES the head — so a match
    made on the date alone did not widen a note, it overwrote one.

    Measured in production, 2026-09-13 (issue #228). The update arm had fired
    exactly twice since the tier shipped, and both times it destroyed an
    unrelated body:

    - one note about a sign-up deadline lost the half of its body recording
      that somebody HAD answered, to a summary of a games thread from the
      same evening that shared nothing with it but the calendar day. The note
      then carried one subject's title and description over another's body
      for two days, until a proactive fire turn read it, found nothing about
      the deadline in the body, fell back to the index description and told
      the room something false about who had answered. That message is how
      the tier came to be looked at at all.
    - one gathering note was created from one room's pass and overwritten
      forty minutes later by another room's, on an unrelated subject. Notes
      are workspace-keyed, so under the date key every room in a workspace
      wrote onto whichever note shared the date.

    Appending keeps a collision non-destructive: the note is a conflation of
    two arrangements, but nothing that was written down is lost and the
    reader downstream sees both threads instead of one.

    **That property is now load-bearing rather than consoling.** The key
    itself narrowed in the second half of #228 — the model names the note it
    updates and the date stops being identity — and the argument for letting
    the model decide at all is exactly this function: with an appended body a
    wrong judgement costs a conflation, not an erasure, so the
    self-attestation bar that kept the decision in code no longer applies.
    Reverting `_appended` would not just reintroduce the old data loss; it
    would remove the ground the gate above it now stands on.

    The separator is a blank line rather than a marker: a body is prose a
    model reads, and the one other writer of these bodies (the tag path's
    `note_update`) hands back whole paragraphs. The caller — never this
    function — decides what to do when the result exceeds `BODY_MAX_CHARS`;
    truncating here is the silent loss `nora.domain.notes` moved into its
    constructors to be rid of.
    """
    return f"{existing_body.rstrip()}\n\n{new_body.strip()}"


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
    #: `DreamAdaptor.store_calls_per_tick` — see `STORE_CALLS_PER_TICK` above.
    store_calls_per_tick = STORE_CALLS_PER_TICK

    _VOCABULARIES: ClassVar[dict[str, tuple[str, frozenset[str]]]] = {
        "nora_note_candidate_gate_total": ("action", GATE_ACTIONS),
        "nora_note_candidate_staging_total": ("outcome", STAGING_OUTCOMES),
    }

    def __init__(
        self,
        *,
        notes: _NoteStore,
        candidates: _CandidateStore,
        workspace_for: Callable[[str], str | None],
        # THE ROOM, not the workspace (issue #161). `key` is already the
        # room's conversation id, so both of these are the same `.get` over
        # the allowlist that `workspace_for` is — the composition root builds
        # all three off `conv.primary_id` in one place, so they cannot
        # disagree about which room a key names.
        #
        # `label_for` is what the room is CALLED (`Conversation.label` —
        # «Zone 42», «Friends»), rendered on every batch line and in the
        # `<room>` block. `description_for` is what the operator says the
        # room IS (`Conversation.description`), rendered once above the
        # batch. Both are REQUIRED rather than defaulted: a wiring gap here
        # renders a plausible, wrong prompt and nothing counts it, which is
        # the failure this adaptor already paid for once.
        label_for: Callable[[str], str | None],
        description_for: Callable[[str], str | None],
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
        self._label_for = label_for
        self._description_for = description_for
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
        """The `<room>` block, the room's message batch attributed to that
        same room, and the workspace's standing note index — so the model can
        propose "not written down yet" and decline to repeat what already is
        (§4: "if this arrangement is already written down, propose an update
        instead, or nothing").

        The `<room>` block comes FIRST and exists once (issue #161): it names
        the room and carries the operator's own description of it, which is
        what tells the model that a games room's 「2200」 is when people log on
        rather than an appointment. See `_room_block`.

        `batch` empty renders `""`: `DreamAdaptor` has no `should_fire` verb,
        the runtime's fire condition is "did `inputs` render anything", and a
        quiet room must cost nothing — the runtime's own docstring on the
        blank-prompt fire condition, restated because it is load-bearing here.
        """
        self._message_ids[key] = frozenset(row.id for row in batch)
        if not batch:
            return ""
        workspace = self._workspace_for(key)
        # THE ROOM (issue #161). `key` IS the room's conversation id, so the
        # room was always knowable here; `room = workspace or key` threw it
        # away and attributed every line of every room of a workspace to the
        # workspace. Label first (what the room is called), workspace only as
        # a fallback for a room the operator left unlabelled, and the
        # conversation id NEVER — see `UNNAMED_ROOM`.
        room = self._label_for(key) or workspace or UNNAMED_ROOM
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
        parts = [
            # FIRST, and once: what room this is and what the operator says
            # it is. Before the batch, because it is how the batch is meant
            # to be read.
            _room_block(room=room, description=self._description_for(key)),
            # WRAPPED, and the wrapper is a safety property rather than tidiness
            # (#292 audit). A bare `"Messages in this batch:" + lines` put these
            # elements at prompt TOP LEVEL, so a body's newline — which the
            # element form deliberately preserves — wrote free text beside the
            # room block and the note index rather than inside anything. The
            # container gives every member-authored byte a parent.
            ContextBlock(
                name="batch",
                body="\n".join(lines),
            ).render(),
        ]
        if workspace is not None:
            # THE INDEX IS THE BALLOT. Since 2026-09-13 the gate resolves the
            # TITLE the model names in `updates` (#228), so every title it may
            # legitimately name is a line of this block and nowhere else — a
            # note missing here is a note the model cannot update, and it will
            # write a duplicate instead. That is the same argument
            # `note_render` makes for the index being uncapped, arriving from
            # the other direction.
            #
            # WITH TODAY, still: the day is what separates two notes whose
            # titles read alike, and the block's own hedge («one still listed
            # here may already have settled») is unanswerable without it.
            #
            # (The duplicate copy of this comment that sat here until
            # 2026-09-13 said the gate compares the date alone. It said it
            # twice, and by then it was false once.)
            #
            # Today comes from the NEWEST row of the batch in the room's own
            # zone, not from a wall clock — the same basis the objective's
            # relative-date resolution reads from, so «today» in the index and
            # «today» in the transcript cannot disagree.
            index = render_notes(
                await self._notes.standing(workspace=workspace),
                today=_batch_day(batch, tz=self._tz),
            )
            if index is not None:
                # `.render()` — the block builds its own tag now, and this pass
                # assembles a flat prompt rather than a `build_prefill` one.
                parts.append(index.render())
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
        if row.attempts == 0 and row.status == CANDIDATE_PENDING:
            self._inc("nora_note_candidates_total", trigger="create")
        if row.status != CANDIDATE_PENDING:
            # A candidate handed back a row the gate ALREADY settled. Not a
            # decision — the decision already happened — so it is not counted
            # on the gate's own vocabulary. But WHICH of the two things this
            # is has to be readable (#235, `STAGING_OUTCOMES`): the same
            # occasion observed again is a no-op, a different arrangement
            # colliding on the staging key is a proposal destroyed, and the
            # two were indistinguishable from outside until 2026-09-13.
            #
            # `attempts` cannot tell them apart, which is also why the
            # arrivals increment above is guarded on `pending` rather than on
            # `attempts` alone: the store returns a settled row UNCHANGED (by
            # contract, both implementations), so a row that settled on its
            # first sighting comes back with `attempts == 0` however many
            # candidates land on it afterwards. The title is what tells them
            # apart.
            outcome = (
                "reobserved"
                if note_handle(row.title) == note_handle(candidate.title)
                else "displaced"
            )
            if outcome == "displaced":
                # WARNING, and it carries both titles — a deliberate choice,
                # reviewed 2026-09-13. A destroyed proposal is the one event
                # in this branch an operator has to be able to find, and the
                # two titles are the only thing that says WHICH proposal went
                # and what it collided with; a warning naming neither is a
                # line nobody can act on. The text is note titles the pass
                # itself composed, the same class the `ambiguous` `log.info`
                # below already emits, and it goes to this deployment's own
                # docker log driver — there is no third-party log aggregator
                # wired (traces go to Langfuse, counts to Prometheus, and
                # neither carries this line). If one is ever wired, this is
                # the call site to revisit first.
                log.warning(
                    "note-candidates: %r was dropped before the gate — the "
                    "staging key (anchor_date=%s, %d entities) already holds "
                    "%r, settled as %s (workspace=%s, issue #235)",
                    candidate.title, row.anchor_date,
                    len(row.anchor_entity_ids), row.title, row.status,
                    workspace,
                )
            self._inc(
                "nora_note_candidate_staging_total", outcome=outcome,
            )
            return 0

        standing = await self._notes.standing(workspace=workspace)
        # The MODEL's answer, resolved. `candidate.updates` and not
        # `row.updates`: the staged row records the occasion, this records
        # which note THIS sighting says it belongs to (see `_CreateCandidate`).
        matches = _resolve_updates(
            updates=candidate.updates, standing=standing,
        )
        if len(matches) == 1:
            return await self._update(
                workspace=workspace, row=row, match=matches[0],
                proposal=proposal, now_ms=now_ms,
            )
        if len(matches) > 1:
            # FAIL TOWARD THE SIDE THAT LOSES NO TEXT. Appending to the wrong
            # one of two same-titled notes conflates two arrangements, which
            # is the failure #228 exists to remove; a duplicate overwrites
            # nothing and a reader can see both. Nothing MERGES it afterwards
            # (#236) — stated in the module docstring rather than assumed
            # here. Counted as its own action rather than folded into
            # `created`, so the rate is readable.
            log.info(
                "note-candidates: %r answers to %d standing notes; created "
                "rather than guessed (workspace=%s)",
                candidate.updates, len(matches), workspace,
            )
            return await self._create(
                workspace=workspace, established_in=established_in, row=row,
                proposal=proposal, now_ms=now_ms, action="ambiguous",
            )
        if candidate.updates is not None:
            # The model NAMED a standing note and nothing answers to it — it
            # mistyped a title, or invented one. Writing it as new is right
            # (the alternative is a code-side guess at what it meant), but it
            # is not the same event as a candidate that named nothing, and
            # counting it `created` made the rate of this design's own central
            # failure mode unmeasurable. `_resolve_updates` normalises both
            # sides through `note_handle`, so this is not the index-escaping
            # mismatch it would otherwise be: it is the model's answer not
            # matching anything the index actually showed it.
            log.info(
                "note-candidates: %r answers to no standing note; created as "
                "new (workspace=%s)", candidate.updates, workspace,
            )
            return await self._create(
                workspace=workspace, established_in=established_in, row=row,
                proposal=proposal, now_ms=now_ms, action="unresolved",
            )
        return await self._create(
            workspace=workspace, established_in=established_in, row=row,
            proposal=proposal, now_ms=now_ms,
        )

    async def _create(
        self, *, workspace: str, established_in: str, row: NoteCreateCandidate,
        proposal: Proposal, now_ms: int, action: str = "created",
    ) -> int:
        """`action` is what a SUCCESSFUL create is counted as. `"created"` is
        the ordinary path — the model named no standing note. `"ambiguous"`
        and `"unresolved"` are the same write reached because the title it
        DID name answers to more than one standing note, or to none. One
        action per settled candidate in all three cases — the alternative,
        counting either beside `created`, would make the vocabulary's sum
        stop being the number of candidates the gate decided."""
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
        self._inc("nora_note_candidate_gate_total", action=action)
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
        # Two refusals, ONE store-call site: `STORE_CALLS_PER_TICK` counts
        # sites off this module's AST, so a second `set_create_candidate_status`
        # here would have to be declared even though only one of the two can
        # run. They keep separate metric actions because they are separate
        # diagnoses — `refused` is the healthy one (this occasion is already
        # written down), `full` says a note has accumulated
        # `BODY_MAX_CHARS` without the curator retiring it.
        composed = _appended(head.body, row.body)
        action = (
            "refused" if not _adds_information(row.body, head.body)
            else "full" if len(composed) > BODY_MAX_CHARS
            else None
        )
        if action is not None:
            if action == "full":
                log.warning(
                    "note-candidates: note %s is at the body ceiling; the "
                    "revision was refused rather than truncated (workspace=%s)",
                    match.id, workspace,
                )
            await self._candidates.set_create_candidate_status(
                workspace=workspace, candidate_id=row.id,
                status=CANDIDATE_REFUSED, now_ms=now_ms,
            )
            self._inc("nora_note_candidate_gate_total", action=action)
            return 0
        payload = NoteRevisionProposal(
            note_id=match.id, body=composed,
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
