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
   the candidate's, a **restatement** of the whole entry (design 42 S4a, §7),
   and only if every line of the head keeps its citations
   (`dropped_citations`; otherwise `dropped_citation`, left pending) and it
   differs from the head after whitespace (otherwise refused as a
   duplicate). Until 2026-09-26 the candidate's body was APPENDED to the head
   (`_appended`, #228), and an append was what made a wrong `updates` cost a
   conflation rather than an erasure; the carry-forward check and the
   revision chain now carry that ground. A decided candidate row is advanced
   to a TERMINAL status (`created`/`refused`) so a re-observation of the
   same occasion never relitigates a settled decision.

**Why scope-keyed and watermarked, unlike the curator or the revisitor.** A
note's arrangement spans days of conversation, not one turn — the operator's
own case, design 33 §4: "呢啲 conversation...可能 cross 一個禮拜去做". A
per-room watermark is the only way this adaptor sees the whole exchange
rather than a five-minute slice, and being scope-keyed (not unit-keyed)
is what lets `nora.app.runtime._dream_adaptor_once`'s existing
`AdaptorSchedule` mechanism apply unchanged (design 33 §5, finding N1): the
mechanism is watermark-bound, and this is now the only adaptor that is
both scope-keyed AND watermarked (the knowledge tier's extractor was the
first, and is gone).

**Why it reads the notebook too.** Unlike a claim extractor — new claims,
with dedupe left to adjudication — a note proposal must not duplicate a note
that already exists in the unit's standing notebook, so the model can be
told: *if this arrangement is already written down, propose an update or
nothing, not a duplicate create.* Until design 42 S3 (2026-09-26) it read the
tag path's `<notes>` index (title + description, never a body); since then it
reads `<today>` and one `<open_note>` per open note WITH its body
(`_notebook`), because an update can only say what a batch changes in a body
it was shown.

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
window reaches PAST the anchor and BACK before the note was written — or,
`ANCHOR_LAPSE_MS` after the anchor, reaches past it alone (DEC-203). So a
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

**The bound arrived, and not here (2026-09-24/25, DEC-202, design 41 §7).**
An `ambiguous` candidate now stays pending instead of creating a third note,
and a separate adaptor, `nora.language.note_merge`, folds two open notes
that ask the same question — `[notes] merge_mode`, off by default and run in
`shadow` first. The two paragraphs above describe this gate as it stands,
which still writes a duplicate where it cannot name a note; the back-stop is
that pass, and an update aimed at a note it folded forwards to the survivor.

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
— *"two genuinely different occasions on one date in one unit, updated
into each other"* — was MET in production on 2026-09-13. The update arm had
fired exactly twice since the tier shipped and both times it merged two
genuinely different occasions: a sign-up deadline and a games thread that
shared nothing but a calendar day, and a gathering note overwritten forty
minutes later by an unrelated room's pass (notes are unit-keyed, so
every room in a unit wrote onto whichever note shared the date). Two
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
`commit` resolves it against the unit's standing notes:

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
`(unit, anchor_date, anchor_entity_ids, lower(btrim(title)))`, so a
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
constant and the key degenerated to `(unit, anchor_date)`. M10 is upheld
for the NOTE (a note stays findable by one name across revisions) and
reversed for the staging row, which is a one-shot dedupe slot for one
sighting rather than an identity. What that buys is a silent, subtractive
failure traded for a visible, additive one: a re-described occasion now
stages twice and the gate may write a duplicate, which `note-merge` is
now the back-stop for (#236, DEC-202). `displaced` survives in the
vocabulary because a loss counter that cannot fire is the right state for a
loss that no longer happens — deleting it would let a re-widening of the key
land silent.
"""

from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import ClassVar, Protocol

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    render_row_window,
    restamp,
    row_speaker,
)
from nora.domain.models import TranscriptRow
from nora.domain.note_body import (
    CODE_WRITTEN,
    RULES,
    NoteBodyError,
    Shown,
    ShownMessage,
    cited_ids_in,
    dropped_citations,
    in_schema_form,
    parse_note_body,
)
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
    RetiredNoteError,
    StaleNoteHeadError,
    default_revisit_at,
    note_handle,
)
from nora.domain.ports import CultureReader, DreamSink, Proposal
from nora.domain.prompt import escape
from nora.language.culture_render import read_culture, render_culture
from nora.language.note_render import (
    FORM_LEGACY,
    FORM_NOT_SHOWN,
    open_note_parts,
    today_block,
)
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
#: The notebook's size bound (design 42 §8.6), over the rendered `<open_note>`
#: blocks. An ESTIMATE, not a measurement: 13 open notes at 500 to 1500
#: characters a body is about 6.5k to 20k, and this is over twice the upper
#: figure. A cap is not a reservation, so unused room costs nothing; sizing it
#: tight off one unit is how a live limit got cut on n=1 before.
NOTEBOOK_BODY_BUDGET_CHARS = 48_000
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
#:
#: 10 -> 12 on 2026-09-24 (design 41 §7.5-7.6): `merged_successor_by_title`
#: when a named title resolves to no open note, and `successor` when the
#: update's append is refused because the note retired.
#:
#: 12 -> 13 on 2026-09-25 (DEC-207): the room's `<culture>` head, one
#: `self._culture.head` per tick, bounded by `read_culture`.
#:
#: 13 -> 14 on 2026-09-26 (design 42 S3): `read` in `inputs`, once per open
#: note, for the head body the notebook now renders whole.
#:
#: 14 -> 15 on 2026-09-26: `_update`'s new-evidence refusal settles the row
#: (`set_create_candidate_status`) on its own branch.
STORE_CALLS_PER_TICK = 15

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
    "updated",          # materialized as a revision of the standing note the
                        # model NAMED in `updates`: APPENDED until design 42
                        # S4a (2026-09-26), a checked restatement since. Carried
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
    "deferred",         # a transient failure; left pending, retried next tick
    "dropped_citation", # a restated update whose body leaves some line of the
                        # head with none of its citations carried (design 42
                        # §7's carry-forward check). Left pending, nothing
                        # written: provenance never silently leaves a note.
    "no_new_evidence",  # an update whose body cites only messages OLDER than
                        # the note's last change (2026-09-26): a restatement
                        # of what the note already rests on, not news. Refused:
                        # the p11 eval case caught an untouched same-day sibling
                        # rewritten from its own old evidence.
    "not_shown",        # named a note this pass rendered WITHOUT its body
                        # (design 42 §8.6: the notebook's budget bound, or
                        # its head could not be read). Left pending, nothing
                        # written: an update is written against a body the
                        # model read. A rate here says the budget binds.
})

#: Every value `nora_note_candidate_staging_total{outcome}` may carry, derived
#: from the code for the same reason `GATE_ACTIONS` is. This is the SETTLED-ROW
#: branch of `_settle`, which the gate's own vocabulary deliberately does not
#: cover: nothing is decided there, because the decision already happened.
#:
#: **Both values exist so that neither is silence.** `note_candidate`'s
#: uniqueness was `(unit, anchor_date, anchor_entity_ids)` until
#: 2026-09-16, so a candidate that shared a day with an already-settled one —
#: and named no people, which was the default — was handed back that settled
#: row and returned before the gate. Two different events reach that branch and
#: they had an IDENTICAL counter signature until 2026-09-13:
#: `nora_note_candidates_total` minus `nora_note_candidate_gate_total` could
#: not tell a destroyed proposal from a benign no-op, which is R-27's
#: `unavailable`-as-`nothing_found` one layer down. Splitting them is what made
#: the drop readable.
#:
#: **#235 then removed the drop**: the key is now `(unit, anchor_date,
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

#: Every value `nora_note_candidate_body_refused_total{rule}` may carry: the
#: validator's own closed vocabulary (`nora.domain.note_body.RULES`), taken
#: from it rather than copied, for `GATE_ACTIONS`' reason. A body a create
#: carries is held to design 42 §4 in `accept()`, and one that breaks it is
#: REFUSED, whole, before anything is staged (G1, §4.5): the rule it broke is
#: the reading, one sample per refused candidate.
BODY_REFUSAL_RULES: frozenset[str] = RULES

#: Every value `nora_note_candidate_parse_dropped_total{field}` may carry: the
#: first envelope field `_parse_candidate` could not read. A dropped item never
#: reaches the gate or staging, so until 2026-09-26 it was indistinguishable
#: from an empty answer, in production and in the eval's reasons (#583
#: investigation: an update missing `description` read as «no update»).
PARSE_DROP_FIELDS: frozenset[str] = frozenset({
    "title", "description", "body", "closing_condition", "anchor_date",
    "anchor_entity_ids", "other",
})


def _parse_drop_field(item: Mapping[str, object]) -> str:
    """Which field made `_parse_candidate` return `None` — for the counter
    and the log only; the parse itself is unchanged."""
    for name, limit in (
        ("title", TITLE_MAX_CHARS), ("description", DESCRIPTION_MAX_CHARS),
        ("body", BODY_MAX_CHARS), ("closing_condition", MAX_CLOSING_CONDITION_CHARS),
    ):
        if _text(item.get(name), limit=limit) is None:
            return name
    anchor_date = item.get("anchor_date")
    if (
        isinstance(anchor_date, bool) or not isinstance(anchor_date, int)
        or anchor_date < _MIN_ANCHOR_DATE
    ):
        return "anchor_date"
    if _parse_entities(item.get("anchor_entity_ids")) is None:
        return "anchor_entity_ids"
    return "other"

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

    async def standing(self, *, unit: str) -> tuple[Note, ...]: ...

    async def read(
        self, *, unit: str, note_id: str,
    ) -> tuple[Note, NoteRevision] | None: ...

    async def successor(self, *, unit: str, note_id: str) -> Note | None: ...

    async def merged_successor_by_title(
        self, *, unit: str, title: str,
    ) -> Note | None: ...


class _CandidateStore(Protocol):
    """`SqlNoteStore`'s create-candidate surface, seen from here — the
    `note_candidate(kind='create')` staging table's own two verbs, structural
    rather than a declared port, for the usual reason a one-consumer shape
    is not a port: nothing else consumes it."""

    async def record_create_candidate(
        self, *, unit: str, title: str, description: str, body: str,
        closing_condition: str, anchor_date: int,
        anchor_entity_ids: Sequence[str], message_ids: Sequence[int],
        now_ms: int,
    ) -> NoteCreateCandidate: ...

    async def set_create_candidate_status(
        self, *, unit: str, candidate_id: str, status: str, now_ms: int,
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
    #: OCCASION (its key is `(unit, anchor_date, anchor_entity_ids,
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

#: The room's name when the allowlist gives neither a label nor a unit.
#: NOT the conversation id, which is what this rendered before issue #161 and
#: is exactly the shape R-18/R-28 keep off a wire: a room's identifier is not
#: its name, and a model told to attribute an arrangement to a room has no use
#: for one.
UNNAMED_ROOM = "unnamed room"


#: What this pass carries, declared as data
#: (`design/context-assembly/01-the-registry.md`) rather than left as the
#: `parts` list `NoteCandidateAdaptor.inputs` builds by hand.
#:
#: **A third row the design bundle's own survey missed.** `00-overview.md`'s
#: table lists this occasion as `batch, room` — two — but `inputs` also
#: appends `nora.language.note_render.render_notes`' block when `unit is not
#: None`, sharing a builder and a name (`notes`) with the tag path's
#: `prefill._BLOCKS` row. The same gap this bundle found in `proactive`
#: (`loop_decisions`, not counted in `00`'s seven either) — a survey done by
#: reading `ContextBlock(name=...)` literals misses a block built by a call
#: to a shared function.
#:
#: `order` records the append sequence in `inputs`; nothing sorts by it —
#: this pass concatenates by hand, like `proactive` and the note curator.
#: `key="turn"` throughout, for the same reason as those two: no established
#: cache boundary, nothing routes on `key` yet either way.
#:
#: `culture` (DEC-207) sits right under `<room>`: the operator's sentence says
#: what the room IS by fiat, and the account says what it has become — both
#: are how the batch is meant to be read, so both come before it.
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("room", 10, "turn"),
    ("culture", 15, "turn"),
    ("batch", 20, "turn"),
    ("today", 25, "turn"),
    ("candidate_open_note", 30, "turn"),
)

#: `name -> (order, key)`, derived so no call site restates a row `_BLOCKS`
#: already gives it.
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}


def _candidate_open_note(
    attrs: tuple[tuple[str, str], ...], body: str,
) -> ContextBlock:
    """One notebook entry as THIS pass's own type (design 42 S3): the line
    renderer is note-merge's, the type is not, so `form` never reaches
    merge's prompt."""
    order, key = _ROW["candidate_open_note"]
    return ContextBlock(
        name="candidate_open_note", order=order, key=key, attrs=attrs, body=body,
    )


def _room_block(*, room: str, description: str | None) -> str:
    """The ONE block that says what room this batch is from — rendered above
    the batch, once, never per line.

    **Why it exists** (issue #161). The batch lines have always carried a room
    per line, and until this block they carried the UNIT's name instead:
    on a unit holding a Signal group, a WhatsApp group and a Discord
    games channel, every line of every batch read `(friends)`, so the model
    could not tell the three rooms apart and nothing in the prompt said what
    kind of room it was reading. It wrote «Bath gathering» twice out of a
    games room's shorthand — 「2200」 is when people log on there, not an
    appointment — and under the objective as written that was the correct
    reading of the input it was given. The instruction was not the broken
    half; the input was.

    **Why the description is the operator's and not a model's.** THIS pass
    cannot derive what a room is from the batch it is judging without making
    the very judgement it is there to make — so the one sentence it takes as
    given is the operator's, stated in `policy.toml`
    (`Conversation.description`) and rendered verbatim.

    **That is not a claim that no model can say what a room is** (DEC-207,
    resolving the contradiction issue #312 named against DEC-182). The culture
    fold does exactly that — but OFFLINE, over the room's whole history, once a
    day, not over this batch — and its account now rides beside this block as
    `<culture>`. The two say different things and neither overrides the other:
    `<room>` is what the operator says the room is FOR, by fiat; `<culture>` is
    what she has watched it BECOME. The circularity #312 raised (a model's
    earlier inference shaping its later one over one corpus) is bounded by the
    windows differing and by `<culture>`'s own type description, which says it
    is her account and not an instruction; it is not zero, and the eval arm on
    this pass is what measures whether it matters.

    Absent description renders the block anyway, self-closing: the room's
    name is the half that always exists, and a room with no prose about it
    must still be a different room from its neighbour.
    """
    order, key = _ROW["room"]
    block = ContextBlock(
        name="room", order=order, key=key, attrs=(("label", room),),
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
    the UNIT, so every room of a unit rendered the same string and
    the promise above was false (issue #161). It is never a conversation id —
    `inputs` resolves it, and `UNNAMED_ROOM` is the floor.

    `tz` is the operator's `[reminders] timezone`, the same zone the curator
    reads «today» from — the room's day, not the process's."""
    def _when(other_ts: int) -> str:
        return _dt.datetime.fromtimestamp(
            other_ts / 1000, tz=tz,
        ).strftime(_LINE_TIME_FORMAT)

    def _attrs(r: TranscriptRow) -> tuple[tuple[str, str | None], ...]:
        # `n` IS THE ROW ID (`render_row_window` puts it first), not the
        # position — `accept()` validates the model's `message_ids` against this
        # batch and drops any item naming an id outside it ("an invented id is
        # fabricated provenance"). A positional index would fail that join
        # silently and empty the pass.
        return (
            ("at", _dt.datetime.fromtimestamp(
                r.ts / 1000, tz=tz).strftime(_LINE_TIME_FORMAT) if r.ts else None),
            # `or "unknown"`, NOT `or row.sender_id` — the raw id is an E.164
            # or an ACI, and this block is a prompt (R-18, DEC-80).
            ("from", row_speaker(r)),
            ("room", room),
            ("reply_to", None if r.quoted_ts is None else _when(r.quoted_ts)),
        )

    return render_row_window(
        [row], row_id=lambda r: r.id, attrs=_attrs,
        body=lambda r: r.body or _media_placeholder(r),
    )


def _shown_by(rendered: Sequence[TranscriptRow]) -> Shown:
    """What `accept` holds a create's body against (design 42 §4.5): each
    RENDERED row by the `from` its line carried (`_render_line`'s own
    `speaker_name`, after the roster projection), and the roster.

    **The roster is the batch's own senders** until the `people` block
    (§8.3a, S1b) puts the room's roster in front of the model: an `## Owed`
    owner has to be a name the model was shown, and this batch's `from`
    names are the only ones it is shown today. A member who owes and wrote
    nothing here is therefore refused as `unknown_owner`, and the objective
    says to write that under `## Open` instead. Nora's own rows are shown as
    hers (`cites_nora`) and are never a roster name. `nora_names` stays empty:
    her name reaches no line of this batch as a `from`, so a line naming her
    is `unknown_owner` or `misattributed` without it."""
    shown = tuple(
        ShownMessage(
            id=row.id, sender=row_speaker(row),
            person_id=row.person_id, is_nora=row.is_nora,
        )
        for row in rendered
    )
    return Shown(
        rendered=shown,
        # `person_id` is what code attaches for an owner (§4.3); an unmapped
        # sender has none, and nothing stores it before G5's column exists.
        roster={m.sender: m.person_id or "" for m in shown if not m.is_nora},
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


#: The read verbs the runtime WOULD admit for this scope-keyed pass and it is
#: not handed, each with why (design/dreamer-agent-loop/43 §2). The operator's
#: rule (2026-09-26): every allowed read tool, less only one the runtime
#: refuses or one with a real cost or side effect; the budget, not the list,
#: is the control. A read verb added to the registry tomorrow is granted by
#: omission from this map.
WITHHELD_TOOLS: Mapping[str, str] = {
    # COST: every call is a vision-model call (thousands of reasoning tokens
    # per read, measured on video), and the batch already renders each media
    # item as a placeholder with its id.
    "media_fetch": "each call is a separate vision-model call",
    # SIDE EFFECT: these read through the social bridge
    # (`nora/infra/social/router.py`): Instagram through the session its
    # MESSAGING shares, X through OpenCLI's headed browser. An unprompted
    # background lookup spends that session's standing on a pass nobody asked
    # for, and whether Nora may look people up unprompted in a Dreamer pass is
    # an open operator decision (design/instagram-adoption/18).
    "instagram_read": "reads through the social bridge's session; unprompted lookup undecided",
    "instagram_search": "reads through the social bridge's session; unprompted lookup undecided",
    "linkedin_read": "reads through the social bridge's session; unprompted lookup undecided",
    "linkedin_search": "reads through the social bridge's session; unprompted lookup undecided",
    "x_read": "reads through the social bridge's session; unprompted lookup undecided",
    "x_search": "reads through the social bridge's session; unprompted lookup undecided",
    # SCOPE: the Cognition session's two reads (`nora.agent.surface.
    # DREAMER_LOOP_TOOLS`, DEC-246). Both run lineage for the room the tool is
    # bound to, and this pass is scope-keyed with no home-room grant, so the
    # room they answer for is not one it was handed. Named here AND refused by
    # `tool_subset_faults`, so a derived grant cannot bring them back.
    "rollup_read": "Cognition-only: lineage binds it to a room this pass has no grant for",
    "standing_read": "Cognition-only: lineage binds it to a room this pass has no grant for",
}


def granted_tools(read_only: frozenset[str]) -> frozenset[str]:
    """What this pass holds: the registry's read-only ceiling (every verb the
    runtime admits for a scope-keyed adaptor, `tool_subset_faults`) less
    `WITHHELD_TOOLS`. Derived, never listed, so a new read verb reaches the
    pass without an edit here."""
    return frozenset(read_only) - frozenset(WITHHELD_TOOLS)


class NoteCandidateAdaptor:
    """Phase A of the note tier's create path — see the module docstring."""

    name = "note-candidates"
    key_kind = "scope"
    uses_watermark = True
    objective = "note-candidates"
    envelope_key = "candidates"
    #: A RESEARCH LOOP since design/dreamer-agent-loop/43 (reversing 37 §6's
    #: tool-free judge): the model decides what to read, code decides what may
    #: be written. The class default is the tool-free pass; the composition
    #: root hands the instance `granted_tools(registry.read_only_tools())` —
    #: every read verb the runtime admits for a scope-keyed adaptor, less
    #: `WITHHELD_TOOLS` — and `[dreamer.budgets.note-candidates]
    #: max_tool_calls` is the control, not the list.
    tools: frozenset[str] = frozenset()
    #: `DreamAdaptor.store_calls_per_tick` — see `STORE_CALLS_PER_TICK` above.
    store_calls_per_tick = STORE_CALLS_PER_TICK

    _VOCABULARIES: ClassVar[dict[str, tuple[str, frozenset[str]]]] = {
        "nora_note_candidate_gate_total": ("action", GATE_ACTIONS),
        "nora_note_candidate_staging_total": ("outcome", STAGING_OUTCOMES),
        "nora_note_candidate_body_refused_total": ("rule", BODY_REFUSAL_RULES),
        "nora_note_candidate_parse_dropped_total": ("field", PARSE_DROP_FIELDS),
    }

    def __init__(
        self,
        *,
        notes: _NoteStore,
        candidates: _CandidateStore,
        unit_for: Callable[[str], str | None],
        # THE ROOM, not the unit (issue #161). `key` is already the
        # room's conversation id, so both of these are the same `.get` over
        # the allowlist that `unit_for` is — the composition root builds
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
        # THE ROOM'S ACCOUNT (design/culture-tier, DEC-207). `key` is already
        # the room's `primary_id` — the same key the culture fold writes
        # under, since both are scope-keyed adaptors handed `scopes()` — so
        # no policy lookup stands between the two. `None` while
        # `[culture] enabled = false`.
        culture: CultureReader | None = None,
        # THE ROOM'S PLATFORM (issue #200) — the same `.get` over the
        # allowlist `unit_for`/`label_for`/`description_for` already are, so
        # a name resolver keyed on `(platform, sender_id)` can be called at
        # all. `None` (unwired, or the room maps to no platform) skips
        # resolution: `_render_line` already never renders a raw id either
        # way (`speaker_name`'s `or "unknown"`), so an unwired composition
        # only loses the canonical name, never gains a leak.
        platform_for: Callable[[str], str | None] | None = None,
        # A NAME RESOLVER, INJECTED the way `MediaJoin` gets `scan_identifiers`
        # at the composition root: `nora/language/` sits below `nora/persons`'
        # callers in the import contract, so this module cannot reach
        # `Persons.name_transcript` itself. Composition hands in
        # `persons.name_transcript` (wrapped to take `platform` positionally),
        # so a batch row's `person_name` is filled from the SAME roster the
        # tag path resolves against (DEC-80) before `_render_line` ever sees
        # it — closing the gap #200 found: this adaptor is the Dreamer's own
        # transcript assembler and never called `name_transcript`, so a
        # mapped sender rendered their self-set, spoofable `sender_name`
        # instead of the operator's canonical name for them, on every fire.
        resolve_names: (
            Callable[[Sequence[TranscriptRow], str], Sequence[TranscriptRow]] | None
        ) = None,
        # The read verbs this pass holds (design 43 §2): `granted_tools` over
        # the registry's read-only ceiling, computed at the composition root
        # because this layer may not import the registry. Empty is the
        # tool-free pass every caller that says nothing still gets.
        tools: frozenset[str] = frozenset(),
    ) -> None:
        self.tools = frozenset(tools)
        self._notes = notes
        self._culture = culture
        self._candidates = candidates
        self._unit_for = unit_for
        self._label_for = label_for
        self._description_for = description_for
        self._metrics = metrics
        self._tz = timezone
        self._platform_for = platform_for
        self._resolve_names = resolve_names
        # Per key, written by `inputs` and read by `accept` — the only
        # channel between them (`DreamAdaptor.accept` takes no batch).
        # Keyed by `key`, not a bare attribute, because
        # `dream_loop` dispatches each scope as its own task.
        self._message_ids: dict[str, frozenset[int]] = {}
        #: Each shown message's timestamp, by scope, for `_update`'s
        #: new-evidence check (the gate sees only a staged row's ids).
        self._message_ts: dict[str, dict[int, int]] = {}
        # Per key, the same channel: what the model was SHOWN, as the body
        # validator takes it (design 42 §4.5, «rendered»). Only the rows
        # `inputs` actually rendered, named as it rendered them.
        self._shown: dict[str, Shown] = {}
        # Per key, from `_notebook`: the open notes rendered WITHOUT a body
        # (the budget bound, or the head could not be read), which an update
        # may not name (`not_shown`); and every id the rendered bodies cite.
        self._bodiless: dict[str, frozenset[str]] = {}
        self._carried: dict[str, frozenset[int]] = {}

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
        same room, and the unit's standing note index — so the model can
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
        self._message_ts[key] = {row.id: row.ts for row in batch}
        self._shown[key] = Shown()
        if not batch:
            return ""
        # RESOLVE NAMES (issue #200), the same projection the tag path
        # applies before its own transcript ever renders (DEC-80). Every row
        # this adaptor gets from `record.since` carries `person_name=None` —
        # nothing on the Dreamer path ever called `Persons.name_transcript`
        # — so without this a mapped sender's line was attributed to their
        # self-set, spoofable `sender_name` instead of the operator's name
        # for them. `_render_line` still never renders a raw id either way
        # (`speaker_name`'s `or "unknown"`); this is what makes the MAPPED
        # case correct rather than merely non-leaking.
        if self._resolve_names is not None:
            platform = self._platform_for(key) if self._platform_for is not None else None
            if platform is not None:
                batch = tuple(self._resolve_names(batch, platform))
        unit = self._unit_for(key)
        # THE ROOM (issue #161). `key` IS the room's conversation id, so the
        # room was always knowable here; `room = unit or key` threw it
        # away and attributed every line of every room of a unit to the
        # unit. Label first (what the room is called), unit only as
        # a fallback for a room the operator left unlabelled, and the
        # conversation id NEVER — see `UNNAMED_ROOM`.
        room = self._label_for(key) or unit or UNNAMED_ROOM
        rendered = batch[:MAX_RENDERED_MESSAGES]
        lines = [_render_line(row, room=room, tz=self._tz) for row in rendered]
        self._shown[key] = _shown_by(rendered)
        if len(batch) > MAX_RENDERED_MESSAGES:
            omitted = batch[MAX_RENDERED_MESSAGES:]
            lines.append(
                f"... and {len(omitted)} more messages "
                f"(ids {omitted[0].id}..{omitted[-1].id}) not shown"
            )
        batch_order, batch_key = _ROW["batch"]
        parts = [
            # FIRST, and once: what room this is and what the operator says
            # it is. Before the batch, because it is how the batch is meant
            # to be read.
            _room_block(room=room, description=self._description_for(key)),
        ]
        # What this room has become (DEC-207), under what the operator says it
        # IS. A games room's 「2200」 is when people log on, and the account is
        # where that reading lives once the operator's one sentence runs out.
        # No block for a room never folded: `render_culture`'s reason.
        culture = render_culture(
            await read_culture(
                self._culture.head(key=key) if self._culture is not None
                else None,
                stage="note_candidates_culture", platform="dreamer",
                metrics=self._metrics,
            ),
            # Aged against the batch's NEWEST row, the same «now» `<notes>`
            # reads its today from, not the wall clock: a backlog batch is
            # read as of when it was said.
            now=_dt.datetime.fromtimestamp(batch[-1].ts / 1000, tz=_dt.UTC),
        )
        if culture is not None:
            parts.append(restamp(culture, _ROW).render())
        parts += [
            # WRAPPED, and the wrapper is a safety property rather than tidiness
            # (#292 audit). A bare `"Messages in this batch:" + lines` put these
            # elements at prompt TOP LEVEL, so a body's newline — which the
            # element form deliberately preserves — wrote free text beside the
            # room block and the note index rather than inside anything. The
            # container gives every member-authored byte a parent.
            ContextBlock(
                name="batch", order=batch_order, key=batch_key,
                body="\n".join(lines),
            ).render(),
        ]
        if unit is not None:
            # THE NOTEBOOK, WITH BODIES (design 42 S3, §8.6). It replaced the
            # title-and-summary index: an update restates what it reads, and
            # the model cannot restate, or even tell what a batch changes in,
            # a body it was never shown. Every open note is still here, so
            # every title the gate may resolve is still on the ballot (#228's
            # argument for the uncapped index, unchanged); only a body can be
            # left out, when the budget binds, and then it says so.
            #
            # Today comes from the NEWEST row of the batch in the room's own
            # zone, not from a wall clock, so «today» here and in the
            # transcript cannot disagree.
            parts += await self._notebook(key=key, unit=unit, batch=batch)
        return "\n\n".join(parts)

    async def _notebook(
        self, *, key: str, unit: str, batch: Sequence[TranscriptRow],
    ) -> list[str]:
        """`<today>`, then one `<open_note>` per open note of the unit, each
        with its head body WHOLE (never clipped: a restatement of a prefix
        drops the rest), under `NOTEBOOK_BODY_BUDGET_CHARS`.

        When the budget binds, notes born in this room come first, then the
        most recently revised; those that fit render whole, the rest render
        with no body and `form` saying so, and an update naming one of them
        is left pending at the gate (`not_shown`). Never clipped, never
        dropped: a note missing from the list is one the model writes again.
        """
        newest = _dt.datetime.fromtimestamp(batch[-1].ts / 1000, tz=self._tz)
        today = newest.date()
        heads: list[tuple[Note, NoteRevision | None]] = []
        for note in await self._notes.standing(unit=unit):
            current = await self._notes.read(unit=unit, note_id=note.id)
            heads.append((note, None if current is None else current[1]))
        heads.sort(key=lambda nh: (
            nh[0].established_in != key,
            -(nh[1].written_at if nh[1] is not None else 0),
        ))
        blocks: list[str] = []
        bodiless: set[str] = set()
        carried: set[int] = set()
        spent = 0
        for note, head in heads:
            room = self._label_for(note.established_in) or UNNAMED_ROOM
            block = None
            body_ids: frozenset[int] = frozenset()
            if head is not None:
                # A head this read could not reach falls through to the
                # bodiless form: the title stays on the ballot, and an update
                # naming it waits rather than restating what nobody read.
                block = _candidate_open_note(*open_note_parts(
                    note, head, room=room, now=newest, today=today,
                    form=None if in_schema_form(head.body) else FORM_LEGACY,
                ))
                body_ids = cited_ids_in(head.body)
                if spent + len(block.render()) > NOTEBOOK_BODY_BUDGET_CHARS:
                    block = None
            if block is None:
                bodiless.add(note.id)
                block = _candidate_open_note(*open_note_parts(
                    note, head, room=room, now=newest, today=today,
                    form=FORM_NOT_SHOWN, body=False,
                ))
            else:
                spent += len(block.render())
                carried |= body_ids
            blocks.append(block.render())
        self._bodiless[key] = frozenset(bodiless)
        self._carried[key] = frozenset(carried)
        if not blocks:
            return []
        return [restamp(today_block(today), _ROW).render(), *blocks]

    # -- accept -----------------------------------------------------------

    def saw(self, *, key: str, rows: Sequence[TranscriptRow]) -> None:
        """The rows this pass READ through its tools join what it was shown
        (design/dreamer-agent-loop/43 §3), so a body may cite, and
        `message_ids` may name, a message the model loaded rather than only
        one `inputs` rendered. Called by the runtime after the samples and
        before `accept`.

        Only the SHOWN SET grows; every guard that reads it is unchanged — the
        body validator, carry-forward, and the new-evidence rule (#590), which
        dates a loaded row like a rendered one, so an update citing only rows
        older than the note's last change is still refused. No `inputs` for
        this key means nothing was shown, and a load cannot stand in for it.
        """
        shown = self._shown.get(key)
        if shown is None:
            return
        known = {m.id for m in shown.rendered}
        fresh: dict[int, TranscriptRow] = {}
        for row in rows:
            if row.id not in known:
                fresh.setdefault(row.id, row)
        if not fresh:
            return
        loaded = _shown_by(tuple(fresh.values()))
        self._shown[key] = replace(
            shown,
            rendered=shown.rendered + loaded.rendered,
            # The batch's own senders win a name clash: they are what the
            # model read first, and `_shown_by` keys the roster by name.
            roster={**loaded.roster, **shown.roster},
        )
        self._message_ids[key] = (
            self._message_ids.get(key, frozenset()) | frozenset(fresh)
        )
        self._message_ts[key] = {
            **{row.id: row.ts for row in fresh.values()},
            **self._message_ts.get(key, {}),
        }

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """Typed parse into `_CreateCandidate`, dropping a malformed item
        silently (`DreamAdaptor.accept`'s own contract) and dropping any item
        naming a `message_ids` entry outside this run's batch —
        because an invented id is fabricated provenance, worse than a missing entry.

        **A CREATE's body is held to the schema here** (design 42 S2, §4.5):
        a candidate naming no `updates` is refused whole when its body does
        not parse, counted on `nora_note_candidate_body_refused_total{rule}`,
        and otherwise carries the NORMALISED body (whitespace, bullets and
        heading case, the only repairs, G1). Here and not at the gate: a
        staged row the gate settles is settled for good on its key, so a body
        refused after staging would make every corrected re-proposal of that
        title and anchor a no-op.

        What it does not hold, stated rather than discovered: an UPDATE's
        body (S3/S4 change that arm, one variable at a time), including one
        whose `updates` title the gate later resolves to no note, or to
        several, and so writes as a create (`unresolved`, `ambiguous`).
        """
        seen = self._message_ids.get(key)
        # No `inputs` for this key: nothing was shown, so nothing can be
        # cited, and a create's body is refused rather than taken on trust.
        shown = self._shown.get(key, Shown())
        parsed: list[object] = []
        for item in items:
            candidate = _parse_candidate(item)
            if candidate is None:
                field = _parse_drop_field(item)
                log.warning(
                    "note-candidates dropped a malformed item: %s missing, "
                    "empty or over its limit (updates=%r, scope=%s)",
                    field, item.get("updates"), key,
                )
                self._inc("nora_note_candidate_parse_dropped_total", field=field)
                continue
            if seen is not None and not set(candidate.message_ids) <= seen:
                log.warning(
                    "note-candidates dropped: message_ids %s are not all in "
                    "the batch (scope=%s)",
                    sorted(set(candidate.message_ids) - seen), key,
                )
                continue
            # Every body is held to the schema: a create's with at least one
            # `## Open` line (the objective's rule for a new entry); an
            # update's, since design 42 S4a a RESTATEMENT of the whole entry,
            # against the rows shown plus every id the bodies it was shown
            # already cite, which a restatement carries forward (§8.4).
            is_create = candidate.updates is None
            body = self._held_to_schema(
                candidate.body,
                shown=shown if is_create else replace(
                    shown, carried=self._carried.get(key, frozenset()),
                ),
                key=key, require_open=is_create,
            )
            if body is None:
                continue
            candidate = replace(candidate, body=body)
            parsed.append(candidate)
        return tuple(parsed)

    def _held_to_schema(
        self, body: str, *, shown: Shown, key: str, require_open: bool,
    ) -> str | None:
        """A body, normalised, or `None` when it is refused: the rule it broke
        is counted and logged, never the line, which is the room's own text
        and does not belong in a log."""
        try:
            parsed = parse_note_body(body, shown=shown, require_open=require_open)
            written = CODE_WRITTEN & {s.heading for s in parsed.sections}
            if written:
                # The validator cannot tell who wrote a section; the writer
                # can (`nora.domain.note_body`'s docstring). This pass is a
                # model's, and `## From memory`/`## Folded` are code's alone.
                raise NoteBodyError(
                    "unknown_section",
                    f"## {sorted(written)[0]} is written by code, never by a model",
                )
        except NoteBodyError as refused:
            self._inc("nora_note_candidate_body_refused_total", rule=refused.rule)
            log.warning(
                "note-candidates refused a candidate: its body broke %s (scope=%s)",
                refused.rule, key,
            )
            return None
        return parsed.text

    # -- commit -----------------------------------------------------------

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """Stage, gate, materialize — see the module docstring for the three
        steps. `key` is a SCOPE and both the candidate table and the note
        store are keyed on the UNIT (DEC-105), so it is translated —
        and an unresolvable scope RAISES rather than guessing, because filing a
        candidate under the scope id would hide it from every unit-level
        read, and filing it under a guessed unit lets one social unit's
        proposal enter another's notebook.
        """
        unit = self._unit_for(key)
        if unit is None:
            raise LookupError(
                f"note-candidates: scope {key!r} resolves to no unit, "
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
                unit=unit, established_in=key, candidate=candidate,
                proposal=proposal, now_ms=now_ms,
            )
        return written

    async def _settle(
        self, *, unit: str, established_in: str,
        candidate: _CreateCandidate, proposal: Proposal, now_ms: int,
    ) -> int:
        row = await self._candidates.record_create_candidate(
            unit=unit, title=candidate.title,
            description=candidate.description, body=candidate.body,
            closing_condition=candidate.closing_condition,
            anchor_date=candidate.anchor_date,
            anchor_entity_ids=candidate.anchor_entity_ids,
            message_ids=candidate.message_ids, now_ms=now_ms,
        )
        # A NEW sighting of this occasion, whichever unit-record trigger
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
                    "%r, settled as %s (unit=%s, issue #235)",
                    candidate.title, row.anchor_date,
                    len(row.anchor_entity_ids), row.title, row.status,
                    unit,
                )
            self._inc(
                "nora_note_candidate_staging_total", outcome=outcome,
            )
            return 0

        standing = await self._notes.standing(unit=unit)
        # The MODEL's answer, resolved. `candidate.updates` and not
        # `row.updates`: the staged row records the occasion, this records
        # which note THIS sighting says it belongs to (see `_CreateCandidate`).
        matches = _resolve_updates(
            updates=candidate.updates, standing=standing,
        )
        if not matches and candidate.updates is not None:
            # A MERGED NOTE FORWARDS (design 41 §7.5). This pass rendered its
            # notebook before a merge retired the note it names; without the
            # forward the title resolves to nothing and the question is
            # created AGAIN, undoing the merge.
            survivor = await self._notes.merged_successor_by_title(
                unit=unit, title=candidate.updates,
            )
            if survivor is not None:
                log.info(
                    "note-candidates: %r was merged; updating its survivor "
                    "%r (unit=%s)", candidate.updates, survivor.title, unit,
                )
                matches = (survivor,)
        if len(matches) == 1 and matches[0].id in self._bodiless.get(
            established_in, frozenset(),
        ):
            # NAMED A NOTE WHOSE BODY THIS PASS DID NOT SHOW (design 42
            # §8.6): the notebook's budget bound, or its head could not be
            # read. The model cannot say what a batch changes in a body it
            # never saw, so the candidate is left PENDING, nothing written,
            # and counted apart: a rate here says the budget binds.
            log.info(
                "note-candidates: %r names a note shown without its body; "
                "left pending (unit=%s)", candidate.updates, unit,
            )
            self._inc("nora_note_candidate_gate_total", action="not_shown")
            return 0
        if len(matches) == 1:
            return await self._update(
                unit=unit, row=row, match=matches[0],
                proposal=proposal, now_ms=now_ms,
            )
        if len(matches) > 1:
            # NEITHER GUESSED NOR CREATED (design 41 §7.2, 2026-09-24).
            # Appending to the wrong one of two same-titled notes conflates
            # two arrangements, which is the failure #228 exists to remove.
            # Creating a THIRD note was the old answer, and it made an
            # exact-title duplicate an amplifier: every later sighting that
            # named the title added another. The staging row stays PENDING
            # instead — its text is kept, nothing is written — and the pair
            # is left for `note-merge` to fold. Counted as its own action so
            # the rate is readable.
            log.info(
                "note-candidates: %r answers to %d standing notes; left "
                "pending rather than guessed or duplicated (unit=%s)",
                candidate.updates, len(matches), unit,
            )
            self._inc("nora_note_candidate_gate_total", action="ambiguous")
            return 0
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
                "new (unit=%s)", candidate.updates, unit,
            )
            return await self._create(
                unit=unit, established_in=established_in, row=row,
                proposal=proposal, now_ms=now_ms, action="unresolved",
            )
        return await self._create(
            unit=unit, established_in=established_in, row=row,
            proposal=proposal, now_ms=now_ms,
        )

    async def _create(
        self, *, unit: str, established_in: str, row: NoteCreateCandidate,
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
                "(unit=%s)", unit,
            )
            await self._candidates.set_create_candidate_status(
                unit=unit, candidate_id=row.id,
                status=CANDIDATE_REFUSED, now_ms=now_ms,
            )
            self._inc("nora_note_candidate_gate_total", action="refused")
            return 0
        try:
            ids = await self._notes.propose(
                key=unit,
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
                "note-candidates: create failed (unit=%s)", unit,
            )
            self._inc("nora_note_candidate_gate_total", action="deferred")
            return 0
        await self._candidates.set_create_candidate_status(
            unit=unit, candidate_id=row.id,
            status=CANDIDATE_CREATED, now_ms=now_ms,
        )
        self._inc("nora_note_candidate_gate_total", action=action)
        return len(ids)

    async def _update(
        self, *, unit: str, row: NoteCreateCandidate, match: Note,
        proposal: Proposal, now_ms: int, forwarded: bool = False,
    ) -> int:
        current = await self._notes.read(unit=unit, note_id=match.id)
        if current is None:
            # The matched note moved out of reach between the gate's read and
            # this one. Left pending: the next tick's `standing()` read
            # reflects whatever is true then. (`read` returns RETIRED notes,
            # so a retirement does not land here — the append's own guard
            # refuses it below, `RetiredNoteError`.)
            log.info(
                "note-candidates: matched note %s vanished before the "
                "update landed (unit=%s)", match.id, unit,
            )
            self._inc("nora_note_candidate_gate_total", action="deferred")
            return 0
        _, head = current
        # A RESTATEMENT, checked (design 42 S4a, §7). The candidate's body IS
        # the proposed head; append is gone. What replaces the ground #228's
        # gate stood on (an append costs a conflation, never an erasure):
        # every earlier head stays a revision row, and no line of the head
        # may lose its evidence. A body that drops a line's citations is
        # left PENDING, nothing written (`dropped_citation`); one that says
        # what the head already says, after whitespace, is a duplicate
        # (`refused`). The paraphrased re-sighting `_adds_information` used
        # to catch is now a new revision: revisions per note per day reads it.
        dropped = dropped_citations(head.body, row.body)
        if dropped:
            log.info(
                "note-candidates: a restatement of note %s dropped the evidence "
                "of %d line(s); left pending (unit=%s)",
                match.id, len(dropped), unit,
            )
            self._inc("nora_note_candidate_gate_total", action="dropped_citation")
            return 0
        # NEW EVIDENCE (2026-09-26). An update has to rest on something said
        # since the note last changed: when every message its body cites that
        # this adaptor was shown is older than the head, the restatement is
        # the note's own old evidence, not news, and is refused. Ids this
        # adaptor has not seen (a pending row retried on a later tick) cannot
        # be dated here, so they neither pass nor fail the check.
        cited = cited_ids_in(row.body)
        dated = [
            ts for shown in self._message_ts.values()
            for mid, ts in shown.items() if mid in cited
        ]
        if dated and all(ts < head.written_at for ts in dated):
            log.info(
                "note-candidates: an update of note %s cites only messages "
                "older than its last change; refused (unit=%s)", match.id, unit,
            )
            await self._candidates.set_create_candidate_status(
                unit=unit, candidate_id=row.id,
                status=CANDIDATE_REFUSED, now_ms=now_ms,
            )
            self._inc("nora_note_candidate_gate_total", action="no_new_evidence")
            return 0
        if " ".join(row.body.split()) == " ".join(head.body.split()):
            await self._candidates.set_create_candidate_status(
                unit=unit, candidate_id=row.id,
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
                key=unit,
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
        except RetiredNoteError:
            # The note was retired between the gate's read and this write
            # (design 41 §7.6, guard 1). A MERGED note forwards once to its
            # survivor; anything else — closed, expired, or a forward that
            # already happened — is left pending, as a stale head is.
            survivor = (
                None if forwarded
                else await self._notes.successor(unit=unit, note_id=match.id)
            )
            if survivor is not None:
                return await self._update(
                    unit=unit, row=row, match=survivor,
                    proposal=proposal, now_ms=now_ms, forwarded=True,
                )
            log.info(
                "note-candidates: note %s was retired before the update "
                "landed (unit=%s)", match.id, unit,
            )
            self._inc("nora_note_candidate_gate_total", action="deferred")
            return 0
        except StaleNoteHeadError:
            # The tag path (or another candidate this same tick) revised the
            # note between the gate's read and this write. NOT an error —
            # the same reading `NoteCuratorAdaptor.commit` gives a lost CAS
            # on a merge — left pending for the next tick.
            log.info(
                "note-candidates: update raced the note's head (unit=%s, "
                "note=%s)", unit, match.id,
            )
            self._inc("nora_note_candidate_gate_total", action="deferred")
            return 0
        except Exception:
            log.exception(
                "note-candidates: update failed (unit=%s, note=%s)",
                unit, match.id,
            )
            self._inc("nora_note_candidate_gate_total", action="deferred")
            return 0
        await self._candidates.set_create_candidate_status(
            unit=unit, candidate_id=row.id,
            status=CANDIDATE_CREATED, now_ms=now_ms,
        )
        self._inc("nora_note_candidate_gate_total", action="updated")
        return len(ids)
