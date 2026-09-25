"""The note tier's housekeeping pass — `NoteCuratorAdaptor` (spec §9, merged
with the revisitor per design/dreamer-agent-loop/34-note-dreamer-consolidation
issue #99).

**This pass RETIRES; it no longer merges (2026-09-08).** Merge was keyed on
the ANCHOR — `(anchor_date, RESOLVED entity set)`, never topical similarity,
so two weddings stayed two notes — and the resolved half came from
`note_entity`, populated by resolving `note.anchor_entity_ids` against the
shared entity registry (issue #100). When the knowledge tier was deleted
(issue #115) nothing minted an entity any more: the registry held eight stale
rows and no writer, every anchor name resolved to nothing, and a group whose
resolved entity set was empty was refused by design (a date alone is not an
occasion — «Dinner, 9 Sept» beside «Ada's flight to Tokyo»). Merge was
therefore inert by construction, and the whole chain — the registry store,
the resolution pass, `merge_groups`/`merge`, this module's merge half — was
removed rather than left wired-and-inert.

**What this paragraph used to say next was that duplicates are handled where
a duplicate is first SEEN — `nora.language.note_candidates`' create gate
matching a proposed note against standing notes by ANCHOR DATE and updating
rather than creating. That mechanism was deleted on 2026-09-13 (#228)**, on
its own evidence: the date arm had fired twice since the tier shipped and
conflated two unrelated occasions both times. The gate now asks the MODEL
which standing note a proposal updates and resolves the title it names; a
proposal that names nothing, or names something no note answers to, is
written as a NEW note. That is deliberately the side that overwrites no
text — and it means **nothing merges a duplicate anywhere in this tier any
more.** What bounds one is the retire half below, on its own anchor and its
own evidence, never its duplicate-ness. Stated here rather than left to be
rediscovered; **issue #236 carries the bound**, and
`nora_note_candidate_gate_total{action="ambiguous"|"unresolved"}` is what
makes the rate readable in the meantime. *The reading that would reopen
merge here:* a tier that mints canonical entities again, at which point a
resolved anchor is comparable and the two-weddings argument applies as it
did.

**Superseded in part on 2026-09-25 (DEC-202, design 41 §7).** Merging came
back — model-judged and code-folded, in its OWN adaptor,
`nora.language.note_merge`, behind `[notes] merge_mode` — and this pass stays
retire-only. What that changed here is one argument: the retirement carries
the head it judged (`supersede(expected_head=...)`, guard 3), so a fold
landing between this pass's re-read and its retire leaves the survivor open
with the question it just absorbed.

The retire half is the half that answers «the notebook only grows».

## What merged in (design 34, issue #99)

`nora.language.note_revisitor.NoteRevisitor` — a second adaptor that selected
notes by a per-note clock (`revisit_at`) or a passed anchor, judged one at a
time, and retired `retired-closed`/`retired-expired` — did substantially the
same work as this one and is now gone. 34 §3 argues each of the original
split's reasons in turn; the short version is that the revisitor's distinctive
machinery (`revisit_at`, `strikes`, early close) had **no writer anywhere** in
production, so the split was serving a design that had never actually run.
What moved across, verbatim in logic even where the code that carries it is
new:

- **`_is_due`** → the `due` read (`SqlNoteStore.due_for_revisit`), now that
  `record_wake` and the create path give `revisit_at` its first writers
  (§5.3).
- **`_covers`** (below) — the evidence-grounding rule, unchanged. This is the
  single most important thing this merge must not lose: silence is not an
  answer, and a `retired-expired`/`moot` verdict needs proof the room was
  still talking past the note's anchor, not merely that the anchor has
  passed.
- **The citation requirement for `closed`** — a model claiming a note's
  question was answered must name a message, and that message must be in the
  evidence this run showed it.
- **The wake/strike bookkeeping** (`record_wake`, called from `commit` below)
  — see its own section.

**What did NOT move**: the reaching-a-person half (`schedule_reminder`) was
never built here either — deferred to the proactive adapter
(design/dreamer-agent-loop/33 §8), which is a different tier's job. This
module still only retires (and, on a grounded close, discharges); it never
speaks.

## Selection: SQL-only, TWO reads, and their union

`inputs` runs two cheap reads before any model call — `anchored_before`
(expired anchors) and `due` (`revisit_at` due, now-that-it-has-a-writer). A
note selected by BOTH (an anchor that has passed, with a stale `revisit_at`
sitting behind it) renders exactly once, under `expired`. **This is what
keeps the pass from turning into a full-notebook model-call sweep** — the
model is only ever asked about what the SQL selected, capped by
`max_expired_per_tick` + `max_revisits_per_tick`.

**Every selected note carries an evidence block**, gathered in code from its
own `established_in` (a unit-keyed adaptor has no room-scoped TOOL to do
this with; §4.2's argument, inherited from the revisitor) — the
citation/`_covers` grounding applies to every note this pass judges. (Until
2026-09-08 a merge-group member was the one exception, rendered without
evidence and judged from its body alone; with merge gone there is no note
this pass asks about without evidence.)

**Scope: retire, and — on retirement — discharge (issue #101).** Retire is
what addresses the live problem — `config.toml` said it plainly: *"No curator
yet… the notebook only grows for now."* Growing is what retire stops.
Discharge is what stops a CLOSED note's settled
content going nowhere: design 33 §7 names a note as a staging area and closing
as a discharge, not a bare retire — see `_run_discharge`'s own docstring for
the classification/routing shape and the judgement calls it records.

## Discharge (design 33 §7, 19 §1, issue #101)

**Only for a GROUNDED `closed` verdict that actually retires.** `expired`/
`moot` mean the room's moment passed with nothing answering it — there is no
settled content on that path, by construction, so discharge never runs for
them; that IS this tier's "nothing to discharge" case for those two verdicts,
and it costs nothing to state because it never reaches a model call or a
write.

**Classification rides the SAME model call that decides `closed`, not a new
adaptor and not a second call.** `nora/dreamer/objectives/note-curation.md`
asks the model for an optional `discharge` array on a `closed` verdict; this
module parses it (`_parse_discharge`), drops what does not fit the two
warrant categories, and only ACTS on it in `commit`, after the note has
actually retired — a discharge write is never a reason to hold up or reopen a
retirement that already landed, and it never fails the tick when a downstream
tier is unavailable (see `_run_discharge`).

**Every item's real-world grounding is the CLOSING CITATION's sender, never a
model-typed name.** A `closed` verdict already requires a `cited_message_id`
this run showed for that note (`_ground`'s own citation check); that message's
`TranscriptRow.person_id` — the roster's resolved identity, not the raw
platform sender — is the one real, ungrounded-nothing fact this tier has about
"who is behind this note closing", and it is what stands in for a
`requester_person_id`/Honcho `target` no name-resolution machinery in this
tier can otherwise produce (`nora.persons` has no name -> person lookup a
unit-scoped background pass can reach). This is a real narrowing —
"Miles likes Yardbird for the jazz" only discharges when MILES's own message is
what closed the note, not when someone else's does — recorded here rather
than hidden. *The reading that would reopen this:* a name -> person resolver
reachable from this tier, at which point a person-claim could name a THIRD
person instead of only the citer.

**This module lives in `nora/language/` beside `note_render.py`** for the same
historical reason the Dreamer's watermark table does: the package predates the
tiers that use it. Recorded rather than fixed here — the extraction is its own
port change with its own migration.
"""

from __future__ import annotations

import functools
import itertools
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, ClassVar, Literal, Protocol, get_args

from nora.domain.blocks import (
    BlockKey,
    ContextBlock,
    render_elements,
    restamp,
    speaker_name,
)
from nora.domain.bounds import over_fetch
from nora.domain.gatherings import AmbiguousOpenGatheringsError
from nora.domain.media_join import MediaJoin
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.notes import (
    MAX_DATELESS_STRIKES,
    Note,
    NoteSupersedeReason,
    anchor_bounds_ms,
    default_revisit_at,
)
from nora.domain.ports import CultureReader, Proposal
from nora.domain.prompt import age, escape
from nora.language.culture_render import read_culture, render_culture
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.notes.curator")

#: The verdicts the objective may return, and nothing else. A model writing
#: anything outside this set is DROPPED and counted rather than guessed at.
#: `expired` is new with the merge — the revisitor's word for "the moment
#: passed and nothing answered it", now grounded by `_covers` the same way
#: `moot` is (see `_ground`).
_VERDICTS = frozenset({"closed", "open", "moot", "expired"})

#: Every value `nora_note_curator_total{action}` can carry, DERIVED rather than
#: hand-copied into the metric's help text.
#:
#: A hand-written vocabulary drifts the moment a branch adds an action — this
#: one already had, silently: `retire_failed` and `retire_stale` were emitted
#: for a round before the help text knew about them. `tests/unit/
#: test_note_curator.py` asserts the help text names exactly this set, and it
#: is enforced at RUNTIME rather than by types because the metric call this
#: value reaches (`Metrics.inc`/`.observe`, `nora/obs/metrics.py`) takes
#: `**labels: object` — mypy cannot check a string passed through an `object`
#: parameter no matter which module it runs on, so a `Literal` alone would be
#: checked by nothing here. (`nora/language` is not out of mypy's scope: issue
#: #61 closed 2026-09-11 and `make typecheck` names this package explicitly.)
CURATOR_ACTIONS: frozenset[str] = frozenset({
    # `merge` / `merge_refused` / `merge_failed` were here until 2026-09-08,
    # when the merge half was removed (module docstring).
    "retire_failed",   # supersede raised; the tick continues
    "retire_stale",    # the note moved between inputs and commit, so the
                       # reading the model judged no longer describes it
    "closed",          # retired: the question was answered
    "moot",            # retired: the occasion passed and nothing will settle it
    "expired",         # retired: the occasion passed with nothing answering it
                       # — the revisitor's word, merged in with its own
                       # grounding requirement (see `_ground`)
    "lapsed",          # an expired/moot verdict ACCEPTED on the lapse rule
                       # alone (`ANCHOR_LAPSE_MS`): the room spoke after the
                       # moment, but the window did not reach back to the
                       # note, so an answer older than the window would be
                       # labelled `expired` rather than `closed`. Counted at
                       # grounding, so it can exceed the retirements it led to
    "dateless_stale",  # OBSERVED, not acted on: a dateless note's strikes
                       # crossed MAX_DATELESS_STRIKES after a wake with no
                       # tag-path progress. Eligibility, not a retirement —
                       # see `commit`'s wake loop
})

#: Every value `nora_note_curator_dropped_total{reason}` can carry.
CURATOR_DROPS: frozenset[str] = frozenset({
    "malformed",                # a field missing, wrong type, or a verdict
                                # outside the four
    "unknown_note",             # a handle this run did not render
    "conflicting_verdict",      # two contradictory answers about one note
    # `not_shown_in_group` / `conflicting_merged_body` / `body_too_long` /
    # `shorter_than_inputs` were the merged-body refusals, gone with the
    # merge half (2026-09-08).
    # -- the revisitor's grounding refusals, merged in ----------------------
    "no_evidence",              # a closed/expired/moot verdict on a note with
                                # nothing in its evidence window to judge
                                # against — silence is not an answer
    "citation_missing",         # a closed verdict with no usable
                                # cited_message_id
    "citation_ungrounded",      # a closed verdict citing a message this run
                                # never showed for that note
    "anchor_not_passed",        # an expired/moot verdict on a note whose
                                # anchor has not gone by (or has none) — the
                                # clock decides that, not the model
    "anchor_not_covered",       # the anchor HAS passed but the evidence
                                # window does not span it — see `_covers`
    "discharge_malformed",      # a `closed` verdict's `discharge` field, or
                                # one entry in it, did not fit the three
                                # warrant shapes — dropped and counted rather
                                # than guessed at (issue #101)
})

#: Every `kind` a discharge item can classify as — design 33 §7 / 19 §1's
#: routing table, minus the note row itself (that half is `commit`'s ordinary
#: retirement, already counted on `nora_note_curator_total`/
#: `nora_note_retirements_total`).
DISCHARGE_KINDS: frozenset[str] = frozenset({
    "commitment", "person_claim",
})

#: R-27's three states, applied to ONE discharge item's write:
#: `written` (ok), `refused` (a code-level decision — malformed, or no
#: resolvable grounding — never a guess), `unavailable` (the flag is on but
#: the owning tier's collaborator is missing, or its own write raised).
DISCHARGE_OUTCOMES: frozenset[str] = frozenset({
    "written", "refused", "unavailable",
    # Issue #199 / design 39 D7: the closing citation was one of NORA'S OWN
    # delivered rows, so there is no person whose warrant this item carries.
    # Its OWN label and never `refused`, which already means «the sender maps
    # to nobody» — an unmapped human is a roster gap an operator can fix, and
    # this is a rule that will never stop applying. One bucket for both would
    # report a permanent boundary as a configuration problem.
    "self_cited",
})

#: Whether a note that actually retired `closed` had anything to discharge —
#: the ordinary, common answer is `empty` (R-27: a discharge finding nothing
#: is `nothing_found`, not a wiring gap), so this is what lets the operator
#: tell "discharge ran and found nothing" apart from "discharge never ran"
#: (the latter has NO sample under this metric for that note at all).
#:
#: **`lost` is the third state, and it is R-27's `unavailable` — the one the
#: pair above was missing (#195).** The note is already retired when discharge
#: runs, and `DreamerRuntime` runs `commit` under a wall clock (#60), so a
#: sink that stops answering CANCELS the call. Its content is then gone with
#: nothing to re-derive it from: `anchored_before` filters `retired_at is
#: null`, the classified items were a model judgement about a body that is now
#: history, and the grounding came from a transcript read this tick never
#: wrote down. `items`/`empty` say discharge FINISHED; `lost` says it started
#: and did not. Counted from a `finally`, because `CancelledError` is a
#: `BaseException` and `_run_discharge`'s own `except Exception` cannot see it.
DISCHARGE_NOTE_RESULTS: frozenset[str] = frozenset({"items", "empty", "lost"})

#: How many due-by-`revisit_at` notes one tick will judge — the revisitor's own
#: default, carried across with the mechanism (design 33/34; spec §9.6).
DEFAULT_MAX_REVISITS_PER_TICK = 10

#: Messages of a note's own `established_in` rendered as its evidence. `recent`
#: returns the newest `limit` rows OLDEST-FIRST; filtered to rows that actually
#: postdate the note (a closing condition cannot have been met before the note
#: existed). Her own rows are split off rather than dropped since issue #199 —
#: the human half is the evidence every existing signal is computed from, and
#: the delivered half is citable for `closed` and nothing else (`_own_sends`).
#: This cap is ALSO what `_covers` measures the anchor window against — see
#: that function.
DEFAULT_EVIDENCE_MESSAGES_PER_NOTE = 40

#: How long after an anchor's last instant `_covers`' reach-back half stops
#: being required for `expired`/`moot` (`_Evidence.anchor_lapsed`). The
#: reach-back half is unsatisfiable in a busy room: a group that posts more than
#: `DEFAULT_EVIDENCE_MESSAGES_PER_NOTE` messages between a note and its moment
#: can never show a window that predates the note, so the note stood forever.
#: Measured 2026-09-25 on the live `friends` unit: 8 of 13 open notes had
#: anchors 2 to 17 days past, 183 to 925 messages since they were written
#: (50 to 345 before the anchor itself), and 8 `anchor_not_covered` refusals of
#: an `expired` verdict in one pass. Two days, not one: an evening's «did we
#: end up going?» arrives the next day. (The anchor's end is read in UTC, 8h
#: after the HKT day ends, so the real margin is two days and eight hours.)
#:
#: **What it costs is a possible wrong RETIREMENT, not only a wrong label.**
#: Past the lapse the model judges a window that may not reach back to the
#: note, so a change of plan said before the window («moved it to the 16th»)
#: is not in front of it, and an `expired` verdict then retires a note whose
#: question is still live. The model is told when its window is cut short
#: (`_render_evidence`'s `cut_short`), which is the mitigation; the residue is
#: counted as `nora_note_curator_total{action="lapsed"}`.
ANCHOR_LAPSE_MS = 2 * 86_400_000

#: How many discharge items ONE closed note's classification may carry — a
#: settled note yields a handful of claims (a commitment, a venue, who likes
#: what), never a claim mine. Parsed items past this are simply not read;
#: `MAX_DISCHARGE_ITEMS_PER_NOTE + 1` would be the sign of a model padding the
#: field rather than classifying, and that is a prompt problem, not a runtime
#: one this cap needs to detect.
MAX_DISCHARGE_ITEMS_PER_NOTE = 5

#: How many awaited collaborator CALL SITES `inputs` and `commit` hold between
#: them — the number `DreamerRuntime` multiplies by `[dreamer]
#: store_timeout_seconds` to bound each of them (issue #60). It lives HERE, in
#: the module that makes the calls, on `STANDING_STORE_CALLS_PER_FOLD`'s own
#: argument: a bound derived from a number kept somewhere else drifts in
#: silence, because a hold that is too wide looks exactly like a healthy one
#: until something wedges.
#:
#: `inputs`: `anchored_before`, `due_for_revisit`, `standing`, and per rendered
#: note `_gather_evidence`'s `record.recent` and `media_join.enrich`. `commit`:
#: per retiring verdict `read` and `supersede`, per surviving note
#: `record_wake`, and the two discharge sinks (`create_event`, `conclude`).
#: `tests/architecture/test_dream_store_calls_are_declared.py` counts them off
#: this module's AST and reddens when a call is added without this number
#: moving.
#:
#: 10 -> 11 on 2026-09-25 (DEC-207): `self._culture.head` once per distinct
#: room the shown notes were raised in — the site `_standing.read` already is
#: in proactive, over the unit's rooms (at most one per platform, DEC-105).
STORE_CALLS_PER_TICK = 11

#: A commitment's Event title — generous prose, capped the same way
#: `nora.language.note_candidates` caps every model-authored field ahead of
#: the store rather than trusting the store's own ceiling to catch it first.
MAX_COMMITMENT_TITLE_CHARS = 200

#: A person-claim's content, on its way to `MemoryStore.conclude`. Mirrors
#: `nora.agent.tools`'s `remember` tool's own `_CONCLUSION_MAX_CHARS` for the
#: SAME port, duplicated rather than imported: `nora.agent` sits ABOVE
#: `nora.language` in the layered-architecture contract
#: (`pyproject.toml`'s importlinter layers), so this module cannot import it
#: — the two caps must never disagree about what the port accepts, which is
#: a reason to keep the number identical, not a reason either copy can drift
#: unnoticed (there is no test tying the two together; a change to one is a
#: change to read intentionally against the other).
MAX_PERSON_CLAIM_CHARS = 65535

#: One item's evidence-availability, DERIVED from the type rather than
#: hand-copied beside it (`nora_note_curator_candidates_total`'s help text
#: reads `get_args` off this). Two enforcers, because the value that reaches
#: the metric (`self._inc(..., evidence=state)`) crosses `Metrics.inc`'s
#: `**labels: object` boundary — mypy checks `state`'s own assignment against
#: `EvidenceState`, but not the string that lands in the metric, so a runtime
#: check is still what catches a drift there; `_Evidence`'s construction is a
#: runtime error in every test that builds one. (`nora/language` is not out of
#: mypy's scope: issue #61 closed 2026-09-11 and `make typecheck` names this
#: package explicitly.)
EvidenceState = Literal["present", "absent", "self_only", "unavailable"]
EVIDENCE_STATES: tuple[str, ...] = get_args(EvidenceState)


def _now_ms() -> int:
    """The default `clock` collaborator — a module-level function rather than
    a call, so it satisfies `Callable[[], int]` without a module-level mutable
    singleton (DEC-13)."""
    import time

    return int(time.time() * 1000)


class _NoteStore(Protocol):
    async def standing(self, *, unit: str) -> tuple[Note, ...]: ...

    async def anchored_before(
        self, *, unit: str, day: int, limit: int,
    ) -> tuple[tuple[Note, str], ...]: ...

    async def due_for_revisit(
        self, *, unit: str, now_ms: int, limit: int,
    ) -> tuple[tuple[Note, str], ...]: ...

    async def read(
        self, *, unit: str, note_id: str,
    ) -> tuple[Note, Any] | None: ...

    async def supersede(
        self, *, key: str, target: str, replaced_by: str | None,
        because: str, source: str | None, now_ms: int,
        expected_head: str | None = None,
    ) -> int: ...

    async def record_wake(
        self, *, unit: str, note_id: str, next_revisit_at: int,
        now_ms: int,
    ) -> Any: ...


class _TranscriptSource(Protocol):
    """The one `RecordStore` read this adaptor makes for evidence — newest
    first, capped. Narrowed to a single method for `nora.app.runtime`'s
    `_ConsolidationCursor`'s own reason: the concrete store also hands out
    writes, and a background pass reaching one would be a defect nothing would
    catch."""

    async def recent(
        self, *, conversation_id: str, limit: int
    ) -> list[TranscriptRow]: ...


class _EventSink(Protocol):
    """`nora.app.gatherings.GatheringService`'s `create_event`, seen
    structurally. **Cannot be imported concretely**: `nora.app` sits ABOVE
    `nora.language` in the layered-architecture contract
    (`pyproject.toml`'s importlinter layers), so this is the one collaborator
    in this module that MUST be a Protocol rather than a documented choice —
    the composition root wires the real `GatheringService` in, satisfying
    this shape without either module importing the other's package."""

    async def create_event(
        self, *, conversation_id: str, requester_person_id: str, title: str,
        when: Any = None, venue_text: str | None = None,
        person_names: frozenset[str] | None = None, platform: str,
    ) -> object: ...


class _ConclusionSink(Protocol):
    """Honcho's conclusion lane, seen from here — `MemoryStore.conclude`
    (`nora.domain.ports`), the same verb the tag path's `remember` tool
    already writes through. No Dreamer adaptor holds a Honcho collaborator
    today (issue #101 is the first), so this is a NEW collaborator on this
    class rather than a port already wired to it — never a new WRITE PATH:
    `conclude` itself is unchanged."""

    async def conclude(
        self, *, peer_id: str, content: str, target: str,
        workspace_id: str | None = None,
    ) -> bool: ...


@dataclass(frozen=True)
class _CommitmentClaim:
    """A closing note's settled commitment — design 33 §7's "dinner on the
    9th, 3 people" row. Just a title: the WHEN is the note's own
    `anchor_date`, already a validated `YYYYMMDD` the model never has to
    re-produce as free text, and `_discharge_commitment` derives it rather
    than asking for a second date that could disagree with the note's own.
    """

    title: str


@dataclass(frozen=True)
class _PersonClaim:
    """A closing note's settled claim about a PERSON — design 33 §7's "Miles
    likes Yardbird for the jazz" row. Just the content: WHO it is about is
    the closing citation's sender (`_run_discharge`'s own grounding, not a
    field on this type) — see the module docstring's "Every item's real-world
    grounding" section for why a model-typed name is not accepted here.
    """

    content: str


#: One classified discharge item — a closed note's settled content, en route
#: to the tier whose warrant it carries (design 33 §7 / 19 §1's table). TWO
#: shapes with a discriminant (the type itself), never one shape with
#: optional fields — this module's own convention, `nora.domain.notes`'
#: `NoteCreationProposal`/`NoteRevisionProposal` split one layer over.
#:
#: There were THREE. `_WorldClaim` routed a closed note's settled fact about
#: the world into the knowledge tier's `propose`; that tier was deleted, so
#: the route has no destination — and a branch that could only ever answer
#: `unavailable` is not a route. The objective no longer asks for a
#: `world_claim`, and `_parse_discharge_item` drops one if a model invents it.
_DischargeItem = _CommitmentClaim | _PersonClaim


@dataclass(frozen=True)
class _Verdict:
    """One parsed answer about one note — the CLAIM, always, even when it
    fails grounding. `grounded=False` still takes part in the contradiction
    check in `commit` (a model saying both «closed, but no citation» and
    «open» about one note has said «I cannot tell which»), but never causes an
    action on its own; only `commit` reads `grounded`.

    Not a domain type: it never leaves this module and it is not written
    anywhere — the curator's OUTPUT is a retirement, and this is what the
    model said on the way there.
    """

    note_id: str
    verdict: str
    why: str
    grounded: bool
    #: Set only for a GROUNDED `closed` verdict — the message that settles it,
    #: kept so a wrong retirement is reviewable afterwards.
    cited_message_id: int | None = None
    #: Set only for a GROUNDED `closed` verdict that also named something to
    #: discharge (issue #101) — `()` is the ordinary, common answer. Never
    #: populated for `expired`/`moot`: see the module docstring's Discharge
    #: section for why those two verdicts have nothing to route by
    #: construction.
    discharge: tuple[_DischargeItem, ...] = ()


@dataclass(frozen=True)
class _Evidence:
    """Evidence gathered in CODE for one note this tick asked about — the
    grounding `_ground` checks a `closed`/`expired`/`moot` claim against.
    Built once per note in `inputs`, from its own `established_in`.

    `has_anchor`/`anchor_passed`/`anchor_covered` are computed even for a
    dateless note (all `False`/`False` when there is no anchor to read) so
    `_ground` never has to special-case "this note has no anchor" separately
    from "this note's anchor has not passed" — both refuse `expired`/`moot`
    identically, under `anchor_not_passed`.
    """

    message_ids: frozenset[int]
    has_anchor: bool
    anchor_passed: bool
    anchor_covered: bool
    #: The anchor ended more than `ANCHOR_LAPSE_MS` ago AND a human spoke
    #: after it (`_reaches_past`). Replaces only `_covers`' reach-back half:
    #: a room that died before the moment still cannot expire the note.
    anchor_lapsed: bool = False


def _no_evidence() -> _Evidence:
    """The default for a note `_ground` must judge but has no evidence entry
    for. `inputs` gathers evidence for every note it renders, so this is
    reached only if `_shown` and `evidence` disagree — and the fail-closed
    answer to that is the same one a real evidence read finding nothing
    produces: empty `message_ids` sends the verdict straight into `_ground`'s
    `no_evidence` refusal. A stand-in for "never looked", never a guess.

    A function, not a module-level constant: DEC-13 bans a module-level
    instance of a locally-defined class (`test_no_module_level_mutable_
    singletons.py`), and `_Evidence` is frozen but still local. A fresh
    instance per call costs nothing measurable against one dict lookup.
    """
    return _Evidence(
        message_ids=frozenset(), has_anchor=False, anchor_passed=False,
        anchor_covered=False,
    )


@dataclass(frozen=True)
class _Shown:
    """What THIS run put in front of the model, so `accept`/`commit` can
    refuse a verdict about a note the run never showed and know what to do
    with the ones it did.
    """

    #: handle -> note id, THE JOIN THIS TICK ASKED ABOUT. The model never sees
    #: a note id and never returns one; it answers about ordinals this run
    #: rendered, and `accept` resolves them here.
    by_handle: Mapping[str, str]
    heads: Mapping[str, str]
    #: note id -> its gathered evidence, for every note `inputs` rendered
    #: (`anchored_before`/`due_for_revisit` — the whole shown set).
    evidence: Mapping[str, _Evidence]
    #: note id -> {message id -> row}, for the SAME notes `evidence` covers.
    #: This is what lets `commit`'s discharge step
    #: resolve a grounded `closed` verdict's `cited_message_id` back to the
    #: person who sent it (issue #101) without a second `RecordStore` read —
    #: `_gather_evidence` already fetched these rows once, for `_covers`.
    evidence_rows: Mapping[str, Mapping[int, TranscriptRow]]
    #: note id -> its anchor_date, for `commit`'s `record_wake` calls — carried
    #: here rather than re-read, so a note's own anchor shape at SELECTION time
    #: (not at commit time, when it may have moved) decides its next wake.
    anchor_date: Mapping[str, int | None]
    #: Notes selected by `anchored_before` or `due_for_revisit` this tick —
    #: the set `commit` calls `record_wake` on after retirement, for every
    #: one of them still standing. Since 2026-09-08 this is every note shown
    #: (it excluded merge-group members before), kept as its own field so
    #: the wake pass names the set it walks rather than deriving it.
    due_or_anchored: frozenset[str]


#: What this pass carries, declared as data
#: (`design/context-assembly/01-the-registry.md`) instead of left as a trace
#: through `inputs`, `_note_block`, `_render_evidence` and `_render_own_sends`.
#:
#: **Not four siblings — `today` is the only top-level block; `note` repeats
#: once per due/expired note and NESTS `evidence`, which in turn nests
#: `own_sends`.** `order` records the sequence a reader meets them in the
#: rendered text (top of the prompt, then reading into a `<note>`), not a
#: sort key `assemble()` applies — this pass, like `nora.language.proactive`,
#: concatenates by hand.
#:
#: `key="turn"` throughout: this pass is its own call pattern with no
#: established cache boundary, so a key borrowed from `prefill._BLOCKS`
#: would not describe the same thing here, and nothing routes on `key` yet
#: either way (`design/context-assembly/00-overview.md` §3).
#:
#: `culture` (DEC-207) is one block per ROOM the shown notes were raised in,
#: each naming its room, above the notes that name the same room: whether an
#: arrangement has closed turns on how that room settles things — an
#: attrition room never says «cancelled».
_BLOCKS: tuple[tuple[str, int, BlockKey], ...] = (
    ("today", 10, "turn"),
    ("culture", 15, "turn"),
    ("note", 20, "turn"),
    ("evidence", 30, "turn"),
    ("own_sends", 40, "turn"),
)

#: What a room the operator left unlabelled is called in `<culture room=…>`
#: and `<note room=…>` — the same words `note_candidates.UNNAMED_ROOM` uses.
_UNNAMED_ROOM = "unnamed room"

#: `name -> (order, key)`, derived so no call site restates a row `_BLOCKS`
#: already gives it.
_ROW: dict[str, tuple[int, BlockKey]] = {
    name: (order, key) for name, order, key in _BLOCKS
}


class NoteCuratorAdaptor:
    """One `DreamAdaptor`: read a unit's notebook, decide what has closed
    or expired, and write retirements.

    `key_kind = "unit"` — the notebook is unit-scoped (§3/DEC-105),
    so both `friends` rooms tick ONE notebook rather than two. That is what
    `Dependencies.unit_scopes` exists for.

    `uses_watermark = False`, and this is the interesting one. The other
    adaptors read an append-only log through a cursor. This one reads LIVE
    STATE: which anchors have passed, and which notes' `revisit_at` has come
    due. Neither question has a "since when" — **retirement is TIME-driven,
    not evidence-driven** (§9.1). A watermark
    would make the pass stop noticing an expired note the moment the notebook
    went quiet, which is exactly when expiry matters.

    **The pass is self-limiting FOR THE ACTIONS IT TAKES, and not otherwise.**
    A retirement removes its note. But a due note the model leaves `open`
    stays due (its `revisit_at` does not advance without tag-path progress,
    see `record_wake`) and is re-selected until either progress resets its
    clock or its strikes exhaust it (dateless) or its anchor passes
    (anchored).
    """

    name = "note-curation"
    key_kind = "unit"
    uses_watermark = False
    objective = "note-curation"
    envelope_key = "verdicts"
    #: No tools. The curator reasons about bodies and evidence this process
    #: selected and handed it; it has nothing to look up, and `read_only_tools`
    #: being a ceiling rather than a grant means an empty subset is the honest
    #: declaration rather than a missing one.
    tools: frozenset[str] = frozenset()
    #: `DreamAdaptor.store_calls_per_tick` — see `STORE_CALLS_PER_TICK` above.
    store_calls_per_tick = STORE_CALLS_PER_TICK

    def __init__(
        self,
        *,
        notes: _NoteStore,
        record: _TranscriptSource,
        today: Callable[[], int],
        max_expired_per_tick: int,
        max_revisits_per_tick: int = DEFAULT_MAX_REVISITS_PER_TICK,
        evidence_messages_per_note: int = DEFAULT_EVIDENCE_MESSAGES_PER_NOTE,
        metrics: Metrics | None = None,
        clock: Callable[[], int] = _now_ms,
        media_join: MediaJoin | None = None,
        # -- discharge (issue #101), all independent of the six args above --
        #
        # `discharge_enabled` is `NotesConfig.discharge_enabled` — its OWN
        # gate, never inferred from whether the three collaborators below are
        # `None` (composition always wires the real ones; the flag is what
        # decides whether `commit` ever calls them). A sink present while the
        # flag is off is inert; a flag on with a sink missing is a genuine
        # wiring gap, reported `unavailable` per item rather than silently
        # skipped — see `_run_discharge`.
        discharge_enabled: bool = False,
        events: _EventSink | None = None,
        conclusions: _ConclusionSink | None = None,
        ai_peer: str = "nora",
        # THE ROOMS' ACCOUNTS (design/culture-tier, DEC-207). `room_for` maps
        # a note's `(established_in, unit)` to its room's `primary_id` —
        # the key the culture fold writes under (DEC-181) — and the room's
        # label, which both the `<culture>` and the `<note>` carry so the
        # model can tell which account a note is read against. Both `None`
        # while `[culture] enabled = false`, and then neither renders.
        culture: CultureReader | None = None,
        room_for: Callable[[str, str], tuple[str, str | None] | None] | None = None,
        # THE ROOM'S PLATFORM and A NAME RESOLVER (issue #200), keyed the
        # same way `room_for` is — `(established_in, unit)`, since a note's
        # evidence read is by `Note.established_in`, whichever id form the
        # note was born under, never a bare `primary_id`. The same pair
        # `NoteCandidateAdaptor` and `CultureAdaptor` take: `_gather_evidence`
        # builds `evidence` from `record.recent`, which never called
        # `Persons.name_transcript`, so a mapped sender's evidence row was
        # attributed to their self-set, spoofable `sender_name` instead of
        # the operator's canonical name for them (`_render_evidence`'s
        # `who=speaker_name(...)`). `None` (unwired, or a note whose room maps
        # to no platform) skips resolution: `speaker_name` still never
        # renders a raw id either way, so an unwired composition only loses
        # the canonical name, never gains a leak.
        platform_for: Callable[[str, str], str | None] | None = None,
        resolve_names: (
            Callable[[Sequence[TranscriptRow], str], Sequence[TranscriptRow]] | None
        ) = None,
    ) -> None:
        self._notes = notes
        self._culture = culture
        self._room_for = room_for
        self._platform_for = platform_for
        self._resolve_names = resolve_names
        self._record = record
        # The shared media join (issue #116). The curator renders these rows as
        # EVIDENCE for a model deciding whether a note still stands, so a photo
        # it cannot see is evidence it silently does not have. On master the
        # description rode in `messages.body` and this path had it.
        self._media_join = media_join
        # A callable, not a date: the pass compares against a YYYYMMDD calendar
        # day and the clock is the caller's to own — a store or an adaptor that
        # reads the wall clock cannot be tested for the boundary case that
        # matters (a note anchored to TODAY is not expired).
        self._today = today
        self._max_expired = max_expired_per_tick
        self._max_revisits = max_revisits_per_tick
        self._evidence_limit = evidence_messages_per_note
        self._metrics = metrics
        # `now_ms` for `revisit_at`/anchor-bounds comparisons — a collaborator
        # for the same reason `today` is: a due-boundary case must be testable
        # ON the boundary, not merely near it (`note_revisitor`'s own
        # argument, inherited with the mechanism).
        self._clock = clock
        self._discharge_enabled = discharge_enabled
        self._events = events
        self._conclusions = conclusions
        self._ai_peer = ai_peer
        self._shown: dict[str, _Shown] = {}

    #: metric name -> the vocabulary its label may take. Checked on every
    #: increment, because a label typo produces a NEW SERIES rather than an
    #: error, and a new series is invisible until somebody notices the old one
    #: went flat.
    _VOCABULARIES: ClassVar[dict[str, tuple[str, frozenset[str]]]] = {
        "nora_note_curator_total": ("action", CURATOR_ACTIONS),
        "nora_note_curator_dropped_total": ("reason", CURATOR_DROPS),
        "nora_note_curator_candidates_total": ("evidence", frozenset(EVIDENCE_STATES)),
        # The two reasons the CURATOR writes, not the tier's whole vocabulary:
        # `retired-merged` belongs to note-merge and `retired-withdrawn` to
        # operator triage (design 41 §7.6), and neither is a curator reading.
        "nora_note_retirements_total": ("because", frozenset({
            NoteSupersedeReason.RETIRED_EXPIRED,
            NoteSupersedeReason.RETIRED_CLOSED,
        })),
        "nora_note_discharge_notes_total": ("result", DISCHARGE_NOTE_RESULTS),
    }

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

    def _gauge(self, name: str, value: float, **labels: object) -> None:
        if self._metrics is not None:
            self._metrics.gauge(name, value, **labels)

    def _inc_discharge(self, *, kind: str, outcome: str) -> None:
        """`nora_note_discharge_total{kind, outcome}` — TWO labels, one metric,
        so `_inc`'s single-label vocabulary check (every other metric in
        `_VOCABULARIES`) cannot validate it whole. Both are checked here,
        explicitly, before the metric is touched at all — the same runtime
        discipline `_inc` applies to one label, extended to two rather than
        silently trusting the second."""
        if kind not in DISCHARGE_KINDS:
            raise ValueError(
                f"nora_note_discharge_total{{kind={kind!r}}} is not in this "
                f"metric's vocabulary {sorted(DISCHARGE_KINDS)}"
            )
        if outcome not in DISCHARGE_OUTCOMES:
            raise ValueError(
                f"nora_note_discharge_total{{outcome={outcome!r}}} is not in "
                f"this metric's vocabulary {sorted(DISCHARGE_OUTCOMES)}"
            )
        if self._metrics is not None:
            self._metrics.inc(
                "nora_note_discharge_total", 1.0, kind=kind, outcome=outcome,
            )

    # -- evidence -------------------------------------------------------------

    async def _gather_evidence(
        self, note: Note, *, now_ms: int,
    ) -> tuple[_Evidence, str, tuple[TranscriptRow, ...]]:
        """One note's evidence, in code — `nora.language.note_revisitor`'s
        `_candidate`, moved here and folded into the merged selection.

        A read that FAILS leaves the note with NO evidence rather than
        propagating: that puts it on the «cannot tell» path, where it survives
        — the correct direction for this tier, since an unreachable record
        must never be able to cause a retirement.

        The THIRD element — the filtered evidence rows themselves — is new
        with issue #101's discharge step: `inputs` keeps them (keyed by
        message id, on `_Shown.evidence_rows`) so `commit`'s discharge can
        resolve a grounded `closed` verdict's `cited_message_id` back to the
        person who sent it, without a second `RecordStore` read for a row
        this method already fetched.
        """
        state: EvidenceState = "present"
        try:
            rows = await self._record.recent(
                conversation_id=note.established_in, limit=self._evidence_limit,
            )
        except Exception:
            log.exception(
                "note curator: evidence unavailable for note %s (conversation "
                "%s); the note is left standing", note.id, note.established_in,
            )
            rows = []
            state = "unavailable"
        if rows and self._media_join is not None:
            rows = list(await self._media_join.enrich(rows, path="note_curator"))
        # TRUNCATION read BEFORE either filter — a property of what `recent`
        # COULD return, not of what survived. `is_nora` rows are still kept
        # out of `evidence` HERE because `recent` does not exclude them: her
        # own words are an intention she stated, not the group settling
        # anything, and every signal derived from `evidence` — the quiet-room
        # reading, `_covers`, the rows discharge resolves against — has to
        # stay a statement about the GROUP.
        truncated = len(rows) >= self._evidence_limit
        since = [row for row in rows if row.ts > note.created_at]
        evidence = tuple(row for row in since if not row.is_nora)
        # RESOLVE NAMES (issue #200), the same projection `NoteCandidateAdaptor`
        # and `CultureAdaptor` apply before their own batches render. Nothing
        # else in `evidence` changes — only `person_name`, which
        # `_render_evidence`'s `who=speaker_name(...)` reads — so this is
        # applied AFTER the `is_nora`/`since` filters (their own identity is
        # unaffected either way) and does not touch `since` or the own-sends
        # split below, which never reads a sender's name at all
        # (`_render_own_sends` renders a fixed `who="you"`).
        # A RAISING RESOLVER DEGRADES, IT NEVER SKIPS THE NOTE (#546 review).
        # `resolve_names` is `Persons.name_transcript`, dict lookups only, so
        # this should never fire — but the `state`/`unavailable` branch above
        # is for the RECORD READ this note's retirement is grounded on; a
        # name a row is merely attributed BY is not one of `_Evidence`'s
        # inputs, and folding a resolver fault into `unavailable` would leave
        # the note standing over a failure that has nothing to do with
        # whether it should retire. Caught here, at the one call site.
        if self._resolve_names is not None and self._platform_for is not None:
            platform = self._platform_for(note.established_in, note.unit)
            if platform is not None:
                try:
                    evidence = tuple(self._resolve_names(evidence, platform))
                except Exception:
                    log.exception(
                        "note curator: resolve_names raised for note %s "
                        "(conversation %s, platform %s); rendering its "
                        "evidence on the pre-resolution speaker_name floor "
                        "instead", note.id, note.established_in, platform,
                    )
                    if self._metrics is not None:
                        self._metrics.inc(
                            "nora_dream_name_resolution_failed_total",
                            adaptor=self.name,
                        )
        # ISSUE #199's NARROW HALF: her own DELIVERED rows, kept apart from
        # `evidence` rather than folded into it. See `_own_sends` for the
        # boundary and `_render_own_sends` for what the model is told they
        # are. Everything below computes from `evidence` exactly as before —
        # `state`, `_covers`, and the rows `commit`'s discharge resolves
        # against — so the only thing her rows can newly do is be CITED.
        own_sends = _own_sends(since)
        if state != "unavailable" and not evidence:
            state = "self_only" if since else "absent"
        bounds = (
            anchor_bounds_ms(note.anchor_date)
            if note.anchor_date is not None else None
        )
        self._inc("nora_note_curator_candidates_total", evidence=state)
        ev = _Evidence(
            # The citation allow-list, and the ONE place her rows join. A
            # `closed` verdict may now point at a reply she actually
            # delivered; `expired`/`moot` still run through `anchor_covered`,
            # which is computed from `evidence` alone and returns False on an
            # empty one — so a window holding only her rows can still close a
            # note and still cannot expire one.
            message_ids=frozenset(
                row.id for row in (*evidence, *own_sends)
            ),
            has_anchor=bounds is not None,
            anchor_passed=bounds is not None and now_ms > bounds[1],
            anchor_covered=_covers(
                evidence, rows=rows, created_at=note.created_at,
                bounds=bounds, truncated=truncated,
            ),
            anchor_lapsed=(
                bounds is not None and now_ms > bounds[1] + ANCHOR_LAPSE_MS
                and _reaches_past(evidence, bounds)
            ),
        )
        return ev, _render_evidence(
            evidence, state,
            now=datetime.fromtimestamp(self._clock() / 1000, tz=UTC),
            own_sends=own_sends,
            cut_short=truncated and not (rows and rows[0].ts <= note.created_at),
        # THE THIRD ELEMENT CARRIES HER ROWS TOO, and it has to: it is
        # `commit`'s discharge lookup, and ONE tuple serves both slots. Her
        # row is in `message_ids`, so `_ground` accepts it as a closing
        # citation, so `_run_discharge` will look it up — and a map that
        # could not find it would make the refusal a lookup MISS rather than a
        # decision, which is the shape that reads as working right up until
        # somebody widens the map. The refusal lives where the warrant is
        # READ (`_run_discharge`), on its own label; this hands that refusal
        # the row it has to recognise.
        ), (*evidence, *own_sends)

    # -- inputs -----------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        """The expired notes and the due-by-`revisit_at` notes, rendered with
        their evidence.

        `batch` is always empty (`uses_watermark = False`) and is accepted and
        ignored — the port's shape rather than a surprise.

        **Returns `""` when there is nothing, and that is load-bearing.**
        `DreamAdaptor` has no `should_fire` verb; the runtime's fire condition
        is "did `inputs` render anything to reason about", so an adaptor that
        renders a polite empty form instead of nothing buys a model call every
        tick per unit forever.
        """
        now_ms = self._clock()
        expired_all = await self._notes.anchored_before(
            unit=key, day=self._today(), limit=self._max_expired,
        )
        # OVER-FETCH BY ONE (nora.domain.bounds' own convention): a plain
        # `LIMIT max_revisits` cannot tell "exactly that many were due" from
        # "many more were due and this is a slice", and that distinction is
        # what `nora_note_curator_deferred_total` exists to report. The store
        # does the counting for `audit.recent`; it cannot here (`SqlNoteStore`
        # takes no metrics collaborator, deliberately — its own docstring), so
        # the adaptor does it, on `anchored_before`'s own precedent of leaving
        # per-tick bookkeeping to the caller.
        due_over = await self._notes.due_for_revisit(
            unit=key, now_ms=now_ms, limit=over_fetch(self._max_revisits),
        )
        if len(due_over) > self._max_revisits:
            log.info(
                "note curator: more than %d notes due for revisit in "
                "unit %s this tick; judging %d and deferring the rest",
                self._max_revisits, key, self._max_revisits,
            )
            self._inc("nora_note_curator_deferred_total")
        due_over = due_over[: self._max_revisits]

        expired = tuple(expired_all)
        expired_ids = {n.id for n, _ in expired}
        # A note due by BOTH `anchored_before` and `due_for_revisit` (an
        # anchor that has passed, with a stale `revisit_at` sitting behind it)
        # renders once, under `expired`.
        due = tuple(
            (note, body) for note, body in due_over
            if note.id not in expired_ids
        )

        standing_count = len(await self._notes.standing(unit=key))
        # PUBLISHED UNCONDITIONALLY, INCLUDING ZERO, and BEFORE the early
        # return — an idle tick that never wrote these would leave the gauges
        # at whatever the last busy tick reported, which is a high-water mark
        # wearing a level's help text.
        self._gauge("nora_note_curator_standing", float(standing_count), unit=key)
        self._gauge(
            "nora_note_curator_bodies_read",
            float(len(expired_all) + len(due_over)),
            unit=key,
        )

        # ORDINAL HANDLES, NOT IDS — see the pre-merge module's own war story:
        # the Dreamer's egress screen drops any item containing a hyphenated
        # uuid, so a curator or revisitor that asked about ids got every
        # answer silently rejected. `nora.language.note_revisitor`'s own
        # comment on this survives only in git history now; the handle scheme
        # is unchanged.
        by_handle: dict[str, str] = {}
        evidence_by_note: dict[str, _Evidence] = {}
        evidence_rows_by_note: dict[str, dict[int, TranscriptRow]] = {}
        anchor_date_by_note: dict[str, int | None] = {}
        due_or_anchored: set[str] = set()
        ordinal = itertools.count(1)
        parts: list[str] = []
        # The rooms' accounts FIRST, so each `<note>` names its room only when
        # that room's `<culture>` actually renders — a label joining to no
        # account is noise, and the prompt of a room never folded stays
        # byte-identical to one with the tier off.
        cultures, rendered_rooms = await self._cultures(
            [n for n, _ in expired] + [n for n, _ in due])

        for note, body in expired:
            handle = str(next(ordinal))
            by_handle[handle] = note.id
            ev, ev_text, ev_rows = await self._gather_evidence(note, now_ms=now_ms)
            evidence_by_note[note.id] = ev
            evidence_rows_by_note[note.id] = {row.id: row for row in ev_rows}
            anchor_date_by_note[note.id] = note.anchor_date
            due_or_anchored.add(note.id)
            parts.append(
                _note_block(handle, note, body, ev_text, today=self._today(),
                            room=self._room_label(note, rendered_rooms))
            )

        for note, body in due:
            handle = str(next(ordinal))
            by_handle[handle] = note.id
            ev, ev_text, ev_rows = await self._gather_evidence(note, now_ms=now_ms)
            evidence_by_note[note.id] = ev
            evidence_rows_by_note[note.id] = {row.id: row for row in ev_rows}
            anchor_date_by_note[note.id] = note.anchor_date
            due_or_anchored.add(note.id)
            parts.append(
                _note_block(handle, note, body, ev_text, today=self._today(),
                            room=self._room_label(note, rendered_rooms))
            )

        self._shown[key] = _Shown(
            by_handle=by_handle,
            heads={
                n.id: n.head_revision_id
                for n in [n for n, _ in expired] + [n for n, _ in due]
            },
            evidence=evidence_by_note,
            evidence_rows=evidence_rows_by_note,
            anchor_date=anchor_date_by_note,
            due_or_anchored=frozenset(due_or_anchored),
        )
        if not expired and not due:
            return ""
        # THE CLOCK, first. The objective tells the model it is given each
        # note's anchor «and whether that date has passed», and it was given a
        # bare `anchor="20260912"` and no today at all — so `expired` and
        # `moot`, two of the four verdicts, were guesses. `_ground` catches the
        # guess in ONE direction (it refuses an ungrounded `expired`/`moot`
        # against the real clock) and nothing catches the other: an `open` on
        # a note whose anchor passed weeks ago is silent, and costs a
        # permanent index line injected on every tag.
        return "\n".join([_now_block(self._today()), *cultures, *parts])

    def _room(self, note: Note) -> tuple[str, str] | None:
        """`(primary_id, label)` of the room `note` was raised in, or `None`
        with no culture wired or a room outside the policy. Resolved by the
        note's thread id AND its unit: ids are unique per platform, not across
        them, and a note carries no platform — the unit is what makes the match
        unambiguous."""
        if self._culture is None or self._room_for is None:
            return None
        room = self._room_for(note.established_in, note.unit)
        if room is None:
            return None
        # The label, NEVER the id — `note_candidates.UNNAMED_ROOM`'s rule: an
        # identifier is not a name and must not reach a prompt.
        return room[0], room[1] or _UNNAMED_ROOM

    def _room_label(
        self, note: Note, rendered_rooms: frozenset[str],
    ) -> str | None:
        """The `room` a `<note>` carries: its room's label, only when that
        room's `<culture>` rendered in this prompt."""
        room = self._room(note)
        return room[1] if room is not None and room[0] in rendered_rooms else None

    async def _cultures(
        self, notes: Sequence[Note],
    ) -> tuple[list[str], frozenset[str]]:
        """One `<culture room=…>` per distinct room these notes were raised in
        (DEC-207), in first-shown order, and the rooms that rendered one.

        Per ROOM and not per note: two notes from one room read against one
        account, and repeating it would be the same paragraph twice in one
        prompt. A room outside the policy, or never folded, renders nothing.
        Aged against this pass's own injected clock, never the wall clock.
        """
        if self._culture is None:
            return [], frozenset()
        rooms: dict[str, str] = {}
        for note in notes:
            room = self._room(note)
            if room is not None and room[0] not in rooms:
                rooms[room[0]] = room[1]
        now = datetime.fromtimestamp(self._clock() / 1000, tz=UTC)
        out: list[str] = []
        rendered: set[str] = set()
        for primary_id, label in rooms.items():
            block = render_culture(
                await read_culture(
                    self._culture.head(key=primary_id),
                    stage="note_curator_culture", platform="dreamer",
                    metrics=self._metrics,
                ),
                now=now, room=label,
            )
            if block is not None:
                out.append(restamp(block, _ROW).render())
                rendered.add(primary_id)
        return out, frozenset(rendered)

    # -- accept -------------------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """Typed parse into `_Verdict`. Drop, log, count — never guess.

        The drop reasons are all counted under
        `nora_note_curator_dropped_total{reason}` and all named in that
        metric's help text (a parameterised test asserts the two agree):

        - `malformed` — a field missing, of the wrong type, or a `verdict`
          outside the four the objective defines.
        - `unknown_note` — names a note this RUN did not show.
        - `no_evidence` / `citation_missing` / `citation_ungrounded` /
          `anchor_not_passed` / `anchor_not_covered` — the grounding checks a
          `closed`/`expired`/`moot` claim must pass; see `_ground`. A claim
          that fails one of these is still returned as an UNGROUNDED
          `_Verdict` so it can collide with a contradicting grounded claim in
          `commit` (a model saying both «closed, uncited» and «open» about one
          note has said «I cannot tell which»), but it can never itself cause
          an action.
        """
        shown = self._shown.get(key) or _Shown({}, {}, {}, {}, {}, frozenset())
        out: list[object] = []
        for item in items:
            note_id_raw = item.get("note_id")
            verdict = item.get("verdict")
            why_raw = item.get("why", "")
            if (
                not isinstance(note_id_raw, str)
                or not isinstance(verdict, str)
                or verdict not in _VERDICTS
                or not isinstance(why_raw, str)
            ):
                self._inc(
                    "nora_note_curator_dropped_total", reason="malformed")
                log.warning("note curator dropped a malformed verdict")
                continue
            resolved = shown.by_handle.get(note_id_raw)
            if resolved is None:
                self._inc(
                    "nora_note_curator_dropped_total", reason="unknown_note")
                log.warning(
                    "note curator named a handle this run did not render")
                continue
            grounded_verdict = self._ground(
                resolved, verdict, why_raw, item, shown=shown,
            )
            # Discharge classification (issue #101) — only for a verdict that
            # is ACTUALLY closed and grounded. `self._discharge_enabled` gates this at
            # PARSE time too, not only at `commit`'s write time: a model that
            # returns a `discharge` field while the flag is off should not
            # even spend a `discharge_malformed` count on it, and it costs
            # nothing to check the flag before looking at the field at all.
            if (
                self._discharge_enabled
                and grounded_verdict.verdict == "closed"
                and grounded_verdict.grounded
                and resolved in shown.due_or_anchored
            ):
                discharge = self._parse_discharge(item.get("discharge"))
                if discharge:
                    grounded_verdict = replace(
                        grounded_verdict, discharge=discharge)
            out.append(grounded_verdict)
        return tuple(out)

    def _ground(
        self, note_id: str, verdict: str, why: str,
        item: Mapping[str, object], *, shown: _Shown,
    ) -> _Verdict:
        """The grounding a `closed`/`expired`/`moot` claim must pass before it
        may ACT — never whether it was MADE (a claim that fails still returns
        a `_Verdict(grounded=False)`, exactly as `nora.language.
        note_revisitor`'s own `_ungrounded` did, for the same conflict-check
        reason).

        `open` is always grounded — there is nothing to check, and it is the
        ordinary answer.

        `closed`, `expired` and `moot` all require the note's CITATION
        ALLOW-LIST to be non-empty (`no_evidence`) — nothing at all in the
        window since the note was written, from anybody including Nora.

        **That gate used to carry more than it does** (issue #199). It read a
        human-only set, so it also enforced «the GROUP must have spoken» for
        `expired`/`moot`. The allow-list now includes her delivered rows, so a
        window holding only those reaches the next gate instead of stopping
        here — and the group-must-have-spoken half is enforced by
        `anchor_covered` alone, which is computed from the human evidence and
        fails closed on an empty one. Behaviour is unchanged; the samples
        move, from `no_evidence` to `anchor_not_covered`. Past that:

        - `closed` needs a `cited_message_id` that is actually one of the ids
          this run showed for THAT note (`citation_missing` /
          `citation_ungrounded`).
        - `expired`/`moot` need the CLOCK to agree the anchor has passed
          (`anchor_not_passed` — also the refusal for a dateless note, which
          has no anchor to have passed) and the evidence window to actually
          SPAN that moment (`anchor_not_covered`, `_covers`'s own rule: the
          room fell silent before the anchor, or is busier than the window) —
          until `ANCHOR_LAPSE_MS` after the anchor, when a human speaking after
          the moment is enough (`anchor_lapsed`, counted `lapsed`).
        """
        if verdict == "open":
            return _Verdict(note_id=note_id, verdict=verdict, why=why, grounded=True)

        evidence = shown.evidence.get(note_id, _no_evidence())
        if not evidence.message_ids:
            self._inc("nora_note_curator_dropped_total", reason="no_evidence")
            return _Verdict(note_id=note_id, verdict=verdict, why=why, grounded=False)

        if verdict == "closed":
            cited = item.get("cited_message_id")
            # `int` and a numeric `str` are both accepted — the id was
            # rendered INSIDE the prompt text. `bool` is refused explicitly:
            # `isinstance(True, int)` is True in Python and `int(True) == 1`
            # would silently become a citation of message 1.
            if isinstance(cited, bool) or not isinstance(cited, (int, str)):
                self._inc(
                    "nora_note_curator_dropped_total", reason="citation_missing")
                return _Verdict(
                    note_id=note_id, verdict=verdict, why=why, grounded=False)
            try:
                cited_id = int(cited)
            except ValueError:
                self._inc(
                    "nora_note_curator_dropped_total", reason="citation_missing")
                return _Verdict(
                    note_id=note_id, verdict=verdict, why=why, grounded=False)
            if cited_id not in evidence.message_ids:
                self._inc(
                    "nora_note_curator_dropped_total",
                    reason="citation_ungrounded")
                return _Verdict(
                    note_id=note_id, verdict=verdict, why=why, grounded=False)
            return _Verdict(
                note_id=note_id, verdict=verdict, why=why, grounded=True,
                cited_message_id=cited_id,
            )

        # `expired` / `moot`: the clock and the evidence window decide, never
        # the model's opinion of either.
        if not evidence.anchor_passed:
            self._inc(
                "nora_note_curator_dropped_total", reason="anchor_not_passed")
            return _Verdict(note_id=note_id, verdict=verdict, why=why, grounded=False)
        if not evidence.anchor_covered:
            if not evidence.anchor_lapsed:
                self._inc(
                    "nora_note_curator_dropped_total", reason="anchor_not_covered")
                return _Verdict(
                    note_id=note_id, verdict=verdict, why=why, grounded=False)
            self._inc("nora_note_curator_total", action="lapsed")
        return _Verdict(note_id=note_id, verdict=verdict, why=why, grounded=True)

    # -- discharge classification (issue #101) -------------------------------

    def _parse_discharge(self, raw: object) -> tuple[_DischargeItem, ...]:
        """A `closed` verdict's optional `discharge` array -> typed items.
        Absent (`None`) is `()` with no count at all — the ordinary shape for
        most closed notes, and not itself evidence of anything. Present but
        not a list is `()` too, counted ONCE under `discharge_malformed`
        (the whole field did not fit the shape, so there is nothing per-item
        to blame). A list's OWN malformed entries are dropped individually,
        each counted the same way, without discarding the entries around
        them — `accept`'s own "drop, log, count, never guess" discipline,
        one level down.

        No item carries its own citation — the module docstring's grounding
        section explains why: every item this run accepts is grounded to the
        VERDICT's own `cited_message_id` in `commit`, not to a second,
        per-item one the model would have to produce and this parser would
        have to check.
        """
        if raw is None:
            return ()
        if not isinstance(raw, list):
            self._inc(
                "nora_note_curator_dropped_total", reason="discharge_malformed")
            log.warning("note curator dropped a malformed discharge field")
            return ()
        out: list[_DischargeItem] = []
        for entry in raw[:MAX_DISCHARGE_ITEMS_PER_NOTE]:
            item = _parse_discharge_item(entry)
            if item is None:
                self._inc(
                    "nora_note_curator_dropped_total",
                    reason="discharge_malformed")
                log.warning(
                    "note curator dropped a malformed discharge item")
                continue
            out.append(item)
        return tuple(out)

    # -- commit -------------------------------------------------------------

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """The tier's write sequence:

        1. **Retirements**, for a GROUNDED verdict of `closed`, `expired` or
           `moot`. `open` and an ungrounded claim both write nothing.
        2. **Wakes** (NEW with the merge) — for every note `inputs` selected
           that is STILL STANDING after step 1, `record_wake` is called with
           a freshly computed `next_revisit_at`. This is what gives
           `revisit_at`/`strikes` their production writer (§5.3) and what
           keeps a note that never changes from being due forever with no
           signal that it is.

        (Step 0 was **merges**, through `SqlNoteStore.merge`, until
        2026-09-08.)

        Returns rows written — for this pass, notes retired.
        """
        verdicts: dict[str, _Verdict] = {}
        conflicted: set[str] = set()
        for p in proposals:
            if not isinstance(p.payload, _Verdict):
                continue
            seen = verdicts.get(p.payload.note_id)
            if seen is not None and seen.verdict != p.payload.verdict:
                conflicted.add(p.payload.note_id)
                continue
            verdicts[p.payload.note_id] = p.payload
        for note_id in conflicted:
            verdicts.pop(note_id, None)
            self._inc(
                "nora_note_curator_dropped_total", reason="conflicting_verdict")
            log.warning(
                "note curator returned contradictory verdicts for one note; "
                "leaving it standing")

        shown = self._shown.get(key) or _Shown({}, {}, {}, {}, {}, frozenset())
        provenance = proposals[0] if proposals else None
        retired = 0

        retired_this_tick: set[str] = set()
        for note_id, verdict in verdicts.items():
            if verdict.verdict == "open" or not verdict.grounded:
                continue
            because = (
                NoteSupersedeReason.RETIRED_CLOSED
                if verdict.verdict == "closed"
                else NoteSupersedeReason.RETIRED_EXPIRED
            )
            try:
                current = await self._notes.read(
                    unit=key, note_id=note_id)
            except Exception:
                log.exception(
                    "note curator could not re-read a note before retiring "
                    "it; continuing")
                self._inc("nora_note_curator_total", action="retire_failed")
                continue
            if current is None or current[0].head_revision_id != shown.heads.get(
                note_id
            ):
                log.info(
                    "note moved under the curator between inputs and commit; "
                    "not retiring on a stale reading")
                self._inc("nora_note_curator_total", action="retire_stale")
                continue
            try:
                # THE HEAD IT JUDGED, not just the note (design 41 §7.6, guard
                # 3). The re-read above and this write are two transactions,
                # so a `note-merge` fold landing between them appended a
                # duplicate's question to this note; the retirement is then a
                # no-op (count 0) instead of retiring the survivor with it.
                count = await self._notes.supersede(
                    key=key, target=note_id, replaced_by=None,
                    because=because, source=None, now_ms=now_ms,
                    expected_head=shown.heads.get(note_id),
                )
            except Exception:
                log.exception("note retirement failed; continuing")
                self._inc(
                    "nora_note_curator_total", action="retire_failed")
                continue
            retired += count
            # `nora_note_retirements_total` — KEPT under its exact
            # pre-merge name (design 34 §5.5): the retirement reading, by
            # reason and by whether it actually moved a row, independent of
            # `nora_note_curator_total`'s own action-shaped view.
            self._inc(
                "nora_note_retirements_total", because=because,
                result="applied" if count else "no_rows",
            )
            if count:
                retired_this_tick.add(note_id)
                self._inc("nora_note_curator_total", action=verdict.verdict)
                if self._discharge_enabled and verdict.verdict == "closed":
                    # AFTER the retirement already landed — a discharge
                    # write is never a reason to hold up or reopen it (the
                    # module docstring's own framing: the row's fate never
                    # depends on what discharge does with its content).
                    #
                    # **That argument was made about EXCEPTIONS, and since
                    # 2026-09-11 there is a second way out of this line.**
                    # `DreamerRuntime` runs `commit` under a wall clock
                    # (issue #60), so a discharge sink that stops answering
                    # CANCELS here — and the note is already retired, so
                    # `anchored_before` will never select it again and its
                    # commitments and person-claims are lost with nothing to
                    # re-derive them from. `_run_discharge`'s own `except` is
                    # no help: `CancelledError` is a `BaseException`.
                    #
                    # NOT reordered, and #195 settled why: discharge-then-
                    # retire trades a silent LOSS for a silent DUPLICATE, and
                    # the duplicate is not benign — `GatheringService.
                    # create_event` dedupes on nothing, discharge passes
                    # `when=None` so its events are permanently `still_open`,
                    # and the second one wedges `create_event` for that whole
                    # conversation behind `AmbiguousOpenGatheringsError` until
                    # a human cancels one. Making the discharge re-derivable
                    # instead needs a durable payload, not a flag: neither the
                    # classified items nor the closing citation's sender is
                    # persisted anywhere, and `_shown` is per-process.
                    #
                    # **So the loss is ACCEPTED and COUNTED.** The rate is
                    # measured at zero today — `nora_dream_store_timeout_total`
                    # has never been sampled, and `discharge_enabled` ships
                    # False — which is exactly what makes paying for the
                    # durable version premature and makes counting it the
                    # thing that could justify paying later. The reversal
                    # condition, stated so a flip is visible as a flip: ANY
                    # non-zero `result="lost"` once the flag is on.
                    discharged = False
                    try:
                        await self._run_discharge(
                            note=current[0], verdict=verdict,
                            evidence_rows=shown.evidence_rows.get(note_id, {}),
                            now_ms=now_ms, provenance=provenance,
                        )
                        discharged = True
                    finally:
                        if not discharged:
                            # The note is RETIRED and its discharge did not
                            # finish — the content is gone with nothing to
                            # re-derive it from (#195).
                            #
                            # **A PURE IN-MEMORY INCREMENT, AND THAT IS WHAT
                            # MAKES IT SAFE.** This frame is being unwound by
                            # a `CancelledError`, which is a `BaseException`
                            # and which `_run_discharge`'s own `except
                            # Exception` therefore cannot see. A `finally`
                            # that AWAITS during cancellation is not reliable,
                            # so any future attempt to REPORT this loss — a
                            # store call, a write to a remote sink — must not
                            # go here. Counting is all this block may do.
                            self._inc(
                                "nora_note_discharge_notes_total",
                                result="lost",
                            )

        # -- wakes: record_wake's first production caller (§5.3) -------------
        #
        # Every note `inputs` selected that is still standing gets its wake
        # recorded, REGARDLESS of what
        # verdict it got (or whether it got one at all) — the note was ASKED
        # ABOUT this tick, which is what a wake means. `record_wake` itself
        # decides whether that counts as progress (a tag-path revision since
        # the last wake resets strikes and advances `revisit_at`) or a strike
        # (anything else, `revisit_at` left untouched so the note stays due).
        for note_id in shown.due_or_anchored:
            if note_id in retired_this_tick:
                continue
            anchor_date = shown.anchor_date.get(note_id)
            next_at = default_revisit_at(anchor_date=anchor_date, now_ms=now_ms)
            try:
                outcome = await self._notes.record_wake(
                    unit=key, note_id=note_id, next_revisit_at=next_at,
                    now_ms=now_ms,
                )
            except Exception:
                log.exception(
                    "note curator: record_wake failed for note %s; "
                    "continuing", note_id,
                )
                continue
            if outcome is None:
                # Not reachable from this unit any more — retired by
                # something else between `inputs` and here, or gone.
                continue
            if anchor_date is None and outcome.strikes > MAX_DATELESS_STRIKES:
                # ELIGIBLE, not retired — see `WakeOutcome`'s own docstring:
                # the wake reports, the caller decides what follows, and this
                # merge counts the crossing rather than building the
                # discharge decision (issue #101's, not this issue's).
                log.info(
                    "note curator: dateless note %s has %d strikes (> %d) "
                    "with no tag-path progress; eligible for dateless "
                    "retirement, not yet acted on", note_id, outcome.strikes,
                    MAX_DATELESS_STRIKES,
                )
                self._inc("nora_note_curator_total", action="dateless_stale")
        return retired

    # -- discharge writes (issue #101) ---------------------------------------

    async def _run_discharge(
        self, *, note: Note, verdict: _Verdict,
        evidence_rows: Mapping[int, TranscriptRow], now_ms: int,
        provenance: Proposal | None,
    ) -> None:
        """Route ONE closed, already-retired note's classified content to the
        tier whose warrant it carries (design 33 §7 / 19 §1's table). Called
        only from `commit`, only once the retirement itself has landed.

        `verdict.discharge == ()` — no items — is R-27's `nothing_found` and
        the ordinary, common case: most closed notes settle one question with
        nothing else worth routing. It is still counted, on
        `nora_note_discharge_notes_total{result="empty"}`, so an operator can
        tell "discharge ran and found nothing" apart from "discharge never
        ran for this note" (the latter leaves NO sample under this metric —
        `commit` only calls this method when `discharge_enabled` and the
        verdict is `closed`).

        Every item's grounding is the CLOSING CITATION's sender — see the
        module docstring's "Every item's real-world grounding" section for
        why. `cited_row` is resolved once, here, because it is the same fact
        for every item this call handles: which message closed the note.
        """
        if not verdict.discharge:
            self._inc("nora_note_discharge_notes_total", result="empty")
            return
        self._inc("nora_note_discharge_notes_total", result="items")
        cited_row = (
            evidence_rows.get(verdict.cited_message_id)
            if verdict.cited_message_id is not None else None
        )
        # **A NOTE CLOSED ON ONE OF NORA'S OWN ROWS DISCHARGES NOTHING**
        # (issue #199; design 39 D7 — she may see herself, she may not cite
        # herself as the state of the world).
        #
        # THE GUARD IS HERE, at the one place the warrant is READ, and not at
        # any of the three places it could have been written. `cited_row` is
        # resolved ONCE and serves both routes, so one refusal covers a
        # commitment reaching the Gathering service and a person-claim
        # reaching Honcho; a guard further up would cover whichever caller
        # happened to exist when it was written. Issue #199 admitted her
        # delivered rows to the citation slot — it had to, because the note it
        # was opened about had a closing condition only she could satisfy and
        # her row was the only row in the window — and admitting a row to
        # `message_ids` necessarily hands it to this line.
        #
        # Its own label, never `refused`: `_discharge_commitment` already uses
        # that for «the sender maps to nobody», which is a roster gap an
        # operator can close. This one never closes. The two would otherwise
        # share a series an operator reads to tell a fixable wiring problem
        # from a permanent boundary.
        if cited_row is not None and cited_row.is_nora:
            for item in verdict.discharge:
                self._inc_discharge(
                    kind=("commitment" if isinstance(item, _CommitmentClaim)
                          else "person_claim"),
                    outcome="self_cited",
                )
            log.info(
                "note discharge: note %s closed on one of Nora's own rows; "
                "the retirement stands and its %d item(s) carry no warrant",
                note.id, len(verdict.discharge),
            )
            return
        for item in verdict.discharge:
            if isinstance(item, _CommitmentClaim):
                await self._discharge_commitment(
                    note=note, claim=item, cited_row=cited_row)
            elif isinstance(item, _PersonClaim):
                await self._discharge_person_claim(
                    note=note, claim=item, cited_row=cited_row)

    async def _discharge_commitment(
        self, *, note: Note, claim: _CommitmentClaim,
        cited_row: TranscriptRow | None,
    ) -> None:
        """A settled commitment -> the Gathering/Event service's own
        `create_event` — never a new write path.

        **`when=None`, deliberately.** The note's own `anchor_date` already
        names the commitment's day, but setting `Gathering.when` from it
        would trigger `GatheringService._link_reminder`, which needs a REAL
        delivery `platform` for the conversation — this unit-scoped
        background pass has no honest way to produce one without a
        conversation -> platform lookup this tier does not have (unlike
        `note.established_in`, which the Gathering service already treats as
        the room the event belongs to). Discharge's job is to not lose the
        fact that a commitment was made; the note itself is retired,
        suppressed rather than deleted, so the exact date stays reachable by
        direct read even though the Event does not carry it. *The reading
        that would reopen this:* a conversation -> platform collaborator
        reachable from this tier, at which point `when` should be set from
        `anchor_date` and the commitment should get a real reminder like any
        tag-path-created event.

        **`requester_person_id` is the closing citation's sender.** Not
        invented, not a note field (`Note` carries no "who" at all) — see the
        module docstring's grounding section.
        """
        if self._events is None:
            self._inc_discharge(kind="commitment", outcome="unavailable")
            log.warning(
                "note discharge: no event sink wired; commitment dropped "
                "(note=%s)", note.id,
            )
            return
        if cited_row is None or cited_row.person_id is None:
            self._inc_discharge(kind="commitment", outcome="refused")
            log.warning(
                "note discharge: commitment for note %s has no resolvable "
                "requester (its closing citation's sender maps to nobody); "
                "dropped", note.id,
            )
            return
        try:
            await self._events.create_event(
                conversation_id=note.established_in,
                requester_person_id=cited_row.person_id,
                title=claim.title,
                when=None,
                # Unused whenever `when=None` — `_link_reminder` is the only
                # reader of this argument and it never runs on that path
                # (see the docstring above). Not persisted on `Gathering`
                # either (it has no `platform` field), so this is a required
                # parameter of an unrelated call shape rather than a value
                # that is stored or read back as if real.
                platform="",
                venue_text=None,
                person_names=None,
            )
        except AmbiguousOpenGatheringsError:
            # A real domain refusal, not a wiring gap: two open events
            # already exist for this room and the service will not guess
            # which one this commitment belongs to.
            self._inc_discharge(kind="commitment", outcome="refused")
            log.info(
                "note discharge: commitment for note %s refused — two open "
                "events already exist for its conversation", note.id,
            )
            return
        except Exception:
            log.exception(
                "note discharge: create_event failed for note %s", note.id)
            self._inc_discharge(kind="commitment", outcome="unavailable")
            return
        self._inc_discharge(kind="commitment", outcome="written")

    async def _discharge_person_claim(
        self, *, note: Note, claim: _PersonClaim,
        cited_row: TranscriptRow | None,
    ) -> None:
        """A settled person-claim -> Honcho's own `conclude` — never a new
        write path; the same verb the tag path's `remember` tool already
        writes through.

        `target` is the closing citation's sender's RESOLVED person id
        (`TranscriptRow.person_id`, the roster's own identity, never the raw
        platform `sender_id`) — see the module docstring's grounding section
        for why this is a self-report, not a third-person claim, until this
        tier has a name resolver.
        """
        if self._conclusions is None:
            self._inc_discharge(kind="person_claim", outcome="unavailable")
            log.warning(
                "note discharge: no Honcho sink wired; person-claim dropped "
                "(note=%s)", note.id,
            )
            return
        if cited_row is None or cited_row.person_id is None:
            self._inc_discharge(kind="person_claim", outcome="refused")
            log.warning(
                "note discharge: person-claim for note %s has no resolvable "
                "subject (its closing citation's sender maps to nobody); "
                "dropped", note.id,
            )
            return
        try:
            # NO `workspace_id`. `conclude`'s is HONCHO's workspace, and a
            # unit is not one: passing `note.unit` addressed a Honcho
            # workspace named after the unit — on this deployment `friends`
            # and `wedding`, which do not exist, and `ridgeline`, which exists
            # and belongs to something else entirely. Either way recall never
            # reads there: `representation`/`chat` take the default. One Nora
            # is one Honcho workspace (operator, 2026-09-19); the unit scopes
            # the NOTE tier, and nothing else.
            written = await self._conclusions.conclude(
                peer_id=self._ai_peer, target=cited_row.person_id,
                content=claim.content,
            )
        except Exception:
            # Belt-and-braces only, `remember`'s own shape: `conclude`'s
            # `False` on failure is the real signal, never an exception —
            # this just keeps a raised error from being missed.
            log.exception(
                "note discharge: conclude failed for note %s", note.id)
            written = False
        self._inc_discharge(
            kind="person_claim", outcome="written" if written else "unavailable",
        )


def _clip(value: object, *, limit: int) -> str | None:
    """One model-authored string field, or `None` if it is not one —
    `nora.language.note_candidates._text`'s own discipline, restated here
    because that module is a sibling, not something this one may import: a
    strict `isinstance(str)` check, since coercion turns `None` into
    `"None"` and `7` into `"7"`, values that look real and are the model not
    answering."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > limit:
        return None
    return stripped


def _parse_discharge_item(entry: object) -> _DischargeItem | None:
    """One `discharge` array entry -> one `_DischargeItem`, or `None` if it
    fits none of the three shapes `DISCHARGE_KINDS` names. A free function
    (never a method — it touches no collaborator and no metric; `_parse_
    discharge` is what counts a `None` here) mirroring `nora.language.
    note_candidates._parse_candidate`'s own shape: no partial parse, the
    whole entry is dropped rather than guessed at.
    """
    if not isinstance(entry, Mapping):
        return None
    kind = entry.get("kind")
    if kind == "commitment":
        title = _clip(entry.get("title"), limit=MAX_COMMITMENT_TITLE_CHARS)
        if title is None:
            return None
        return _CommitmentClaim(title=title)
    if kind == "person_claim":
        content = _clip(entry.get("content"), limit=MAX_PERSON_CLAIM_CHARS)
        if content is None:
            return None
        return _PersonClaim(content=content)
    return None


def _own_sends(since: Sequence[TranscriptRow]) -> tuple[TranscriptRow, ...]:
    """Nora's own rows that the send record says REACHED the room.

    **Issue #199, and it is the narrowest crack in design 39 §1's brake that
    closes the hole.** The brake says nothing Nora says can create, revise or
    close a note, and it is enforced in three places: `since`'s
    `is_nora == 0` predicate on the create path, the curator's own filter one
    function up, and the curator having no create or revise verb at all. The
    first two stay exactly as they are; the third is structural. What changes
    is the word CLOSE.

    A closing condition can name something only SHE can do — «Nora 成功送出路線
    建議», written by a fold on a turn that had crashed, about a route she had
    in fact sent seven seconds earlier. With her rows filtered out, nothing
    that could ever satisfy that condition was visible to the pass whose job
    is to retire it, so the note was unclosable by construction and the
    proactive pass raised it at the operator the next morning.

    **`SENT` only, and the status is the reason this is not self-attestation.**
    R-74 refuses her words as evidence because «I'll book Yardbird» is an
    intention she stated, not the group settling anything — the doer deciding
    it is done. `delivery_status` is not her word: the sender writes it back
    after the platform has accepted the message (A9's record-first, send-after
    pair), so what this admits is a fact about the record, not a claim she
    made. A `PENDING` or `FAILED` row is exactly the self-attestation R-74
    names — an intention with nothing behind it — and is excluded.

    Everything else about the pass is unchanged, so the brake still holds
    where it was holding:

    - **create** — the candidates adaptor reads `RecordStore.since`, whose SQL
      excludes her rows. Untouched, and it is the only path that creates.
    - **revise** — the curator has no revision verb; `_NoteStore` gives it
      `read`, `supersede` and `record_wake` and nothing else.
    - **expire** — `anchor_covered` is computed from the human evidence alone
      and `_covers` fails closed on an empty one, so her rows cannot establish
      that a window spans a moment, and «the moment went by unanswered» still
      requires the room to have spoken.
    - **the quiet-room reading** — `EvidenceState` is derived from the human
      rows too, so a stretch in which only she spoke is still `self_only` and
      still renders «nothing the group has said».
    - **discharge** — `_run_discharge` refuses every item on a note closed by
      citing one of these rows (design 39 D7). Her row settles the note; it
      never grounds a commitment or a person-claim, because every item's
      warrant is the closing citation's SENDER and she is not the person who
      settled anything.
    """
    return tuple(
        row for row in since
        if row.is_nora and row.delivery_status is DeliveryStatus.SENT
    )


#: `n` IS THE ROW ID in both of the curator's renders, not a per-render
#: position — the grounding check (`_ground`) cites these ids back, so they are
#: the note tier's real handles. That was `RowStyle(handle="row_id")` on a flat
#: line until #292 and is now the `n` attribute; the two `RowStyle` constants
#: went with the lines they described.
def _curator_attrs(
    row: TranscriptRow, _i: int, *, now: datetime, who: str,
) -> tuple[tuple[str, str | None], ...]:
    """One evidence or own-send row's attributes.

    `who` is REQUIRED and non-optional. A first cut typed it `str | None` with a
    docstring arguing that `None` meant «hers by construction» and that a `from`
    on the own-sends block would be noise — while both call sites passed a
    string and the block rendered `from="you"`. A branch no caller takes,
    described by prose the code contradicts, is worse than either alternative;
    the own-sends block says «you» and says it in the one place the model reads
    attribution from.
    """
    return (
        ("n", str(row.id)),
        ("age", age(row.ts, now) if row.ts else None),
        ("from", who),
    )


def _render_own_sends(
    own_sends: Sequence[TranscriptRow], *, now: datetime,
) -> str:
    """Her delivered rows, rendered under a marker that says what they are.

    **Telling the model what it is READING, not what to do** — the repo's own
    measured lesson, and the reason this is a render rather than a paragraph
    in `note-curation.md`. Three instruction rewrites of an adjacent gate
    changed nothing or made it worse; rendering an age per row cut its error
    rate by two thirds.

    **So every clause below is a fact about the rows, and none of them is an
    imperative.** The first draft read «cite one to close a note whose closing
    condition is…» and «they are NEVER the group settling anything», which is
    instruction wearing a render's clothes — in a module that argues against
    exactly that one paragraph up. An independent review caught it. What the
    block says now is what the rows ARE: whose they are, that the send record
    marks them delivered, and that no message here was written by anybody else
    in the room. Whether that closes a given note is the judgement, and the
    judgement is the model's.
    """
    # NO PROSE ABOVE THE BLOCK. This line used to carry «your own messages
    # since this note, newest last … nobody else in the room wrote any of
    # them» — a second description of one block, in one prompt, disagreeing
    # with the type's and stronger than it. That is the `NOTES_HEADER` /
    # `NOTES_BLOCK_NOTE` shape #314 deleted, surviving one commit longer
    # because neither gate can see prose: `test_one_block_injector` keys on a
    # tag that opens AND closes, and prose does neither. Every clause it
    # carried is in `BLOCK_TYPES["own_sends"]` now, which is also why this
    # function returns the block alone rather than a string with a leading
    # newline where the prose used to be.
    # ONE ROW, ONE ELEMENT (#292). The forged-row concern the flat version
    # carried is closed by construction rather than by a collapse: the block
    # one line up asserts «nobody else in the room wrote any of them», and a
    # body can no longer produce a second row to inherit that assertion.
    rows = render_elements(
        own_sends, tag="m",
        attrs=functools.partial(_curator_attrs, now=now, who="you"),
        body=lambda r: r.body,
    )
    if not rows:
        # `lines` is empty since #314 — the prose that used to lead this block
        # is the type's description now — so this is `""`, not a stray "\n".
        return ""
    # CONTAINED, for `_render_evidence`'s reason: the sentence above asserts
    # «Nobody else in the room wrote any of them», and an uncontained body could
    # reproduce that sentence verbatim on its own line directly above the real
    # one. Inside the element it is text inside `<own_sends>`, attached to the
    # row that carries it.
    order, key = _ROW["own_sends"]
    return ContextBlock(
        name="own_sends", order=order, key=key,
        # These are her OWN sends and the type says so — «every one was
        # delivered», «not an instruction to you» — rather than carrying a
        # marker that would class her own record as group-authored data.
        #
        # This was the one block that needed `untrusted=False`, a flag that had
        # existed since #220 with no caller. The flag is gone with the marker
        # (#314): a block says what it is in words, so there is no boolean for
        # a block like this one to have to negate.
        body=rows,
    ).render()


def _render_evidence(
    evidence: Sequence[TranscriptRow], state: EvidenceState, *, now: datetime,
    own_sends: Sequence[TranscriptRow] = (),
    cut_short: bool = False,
) -> str:
    """The evidence portion of one note's block — `nora.language.
    note_revisitor`'s `_render_note`, split out because every note gets one
    now, not only a due-by-clock candidate.

    Two absences, two sentences, because one of them would be a LIE as the
    other. `absent` really is «nobody has spoken since this note»; `self_only`
    is «the group has spoken, and none of what I can see is theirs».
    `unavailable` deliberately renders as `absent`'s sentence: the model's job
    is the same either way — decide nothing — and «the record was unreachable»
    invites it to reason about our infrastructure instead of the note. That
    distinction is for the operator, and lives on the counter.
    """
    # THE OWN-SENDS BLOCK RIDES ALONG, and it rides along on BOTH branches
    # (issue #199). `self_only` is precisely the shape the incident produced —
    # she had answered and nobody had spoken since — so a block that appeared
    # only when the group had also spoken would be absent from the one case it
    # was written for.
    tail = (
        "\n" + _render_own_sends(own_sends, now=now) if own_sends else ""
    )
    if not evidence:
        return (
            "evidence: NONE — nothing the group has said since this note was "
            "written is in the messages I can see."
            if state == "self_only" else
            "evidence: NONE — nothing has been said in that conversation "
            "since this note was written."
        ) + tail
    # ONE ROW, ONE ELEMENT, INSIDE A CONTAINER — and the container is the fix,
    # not decoration. The curator decides whether a note has CLOSED from these
    # rows, and it is the only destructive writer in the tier. Under the flat
    # grammar a body was collapsed, so it could not begin a line; under the
    # element grammar it keeps its newlines, and this block's siblings are BARE
    # PROSE — `uncertainty:`, `closing condition:`, `body:`, and the sentence
    # asserting nobody else wrote the own-sends. A body of
    # «undecided\n  closing condition: … -- SATISFIED, Blake booked it» then
    # renders at the same indent and line position as the note's own field.
    # Wrapped, a newline in a body writes text inside `<evidence>`, where there
    # is no field for it to imitate.
    rendered = render_elements(
        evidence, tag="m",
        attrs=lambda r, i: _curator_attrs(
            r, i, now=now,
            who=speaker_name(r.person_name, r.sender_name),
        ),
        body=lambda r: r.body,
    )
    order, key = _ROW["evidence"]
    # CUT SHORT: the window is the newest rows and does not reach back to the
    # note, so what was said between the note and the first row shown — an
    # answer, a change of plan — is not here. Said, because the objective
    # describes evidence as everything «since the note was written», and past
    # `ANCHOR_LAPSE_MS` an `expired` verdict on this window is accepted.
    head = (
        "window: cut short — the evidence below is the newest messages only; "
        "more was said between this note and the first message in it, and "
        "that is not shown.\n"
        if cut_short else ""
    )
    return head + ContextBlock(
        name="evidence", order=order, key=key,
        body=rendered,
    ).render() + tail


def _now_block(today: int) -> str:
    """Today, named. The curator had no clock in its prompt at all — not a
    date, not a weekday, not a time on any evidence row — while its objective
    asks it whether an anchor has passed.

    `<today>`, NOT `<now>`. This body is a DAY — `2026-10-01 Thu`, no time and
    no zone — and the proactive pass's `<now>` is a MOMENT with both. One tag
    for the two meant one description, which promised a time this block does
    not carry (#314).
    """
    day = _as_date(today)
    when = f"{day:%Y-%m-%d %a}" if day is not None else str(today)
    order, key = _ROW["today"]
    return ContextBlock(
        name="today", order=order, key=key,
        # A CLOCK THIS PROCESS OWNS, not anything the room said.
        body=escape(when),
    ).render()


def _as_date(yyyymmdd: int) -> date | None:
    try:
        return date(yyyymmdd // 10000, (yyyymmdd // 100) % 100, yyyymmdd % 100)
    except ValueError:
        return None


def _days_from(today: int, anchor: int) -> str:
    """«tomorrow», «3 days ago» — the anchor as a distance rather than as eight
    digits the model has to subtract for itself."""
    a, t = _as_date(anchor), _as_date(today)
    if a is None or t is None:
        return "unreadable"
    delta = (a - t).days
    return (
        "today" if delta == 0
        else "tomorrow" if delta == 1
        else "yesterday" if delta == -1
        else f"in {delta} days" if delta > 0
        else f"{-delta} days ago"
    )


def _note_block(
    handle: str, note: Note, body: str, evidence_text: str | None,
    *, today: int, room: str | None = None,
) -> str:
    """One note, rendered whole: title, uncertainty, closing condition, body,
    anchor (if any) and evidence. Bodies are rendered whole — the body IS the
    question, and a truncation would have the model deciding whether an
    arrangement has closed from a prefix of it.

    `evidence_text=None` renders no evidence line. Every note this pass shows
    has one since 2026-09-08 (merge-group members were the exception); the
    parameter stays optional so the renderer does not lie about a note it
    was handed nothing for.
    """
    evidence_line = f"\n  {evidence_text}" if evidence_text is not None else ""
    # ESCAPED, per R-13, and added 2026-09-10 after a CMS audit found this
    # renderer was the one place in the tier that skipped it. Every value here
    # is group-derived: `title`, `body`, `uncertainty` and `closing_condition`
    # are model-authored FROM a transcript the group wrote (two hops, the same
    # shape `standing_render` and `note_render` already escape for), and the
    # evidence rows are members' own typed messages.
    #
    # It matters most HERE: the curator is the only destructive writer in the
    # background tier — it retires notes and discharges claims into memory —
    # and it is the only renderer whose output frames its subject in an XML
    # tag the text is interpolated into. A member typing a closing tag could
    # end a note block early; one typing an opening tag could add a note that
    # nothing wrote. `_ground` catches a forged citation id, and catches
    # nothing about a forged block boundary.
    attrs: tuple[tuple[str, str], ...] = (("id", handle), ("title", note.title))
    if room:
        # Which room it was raised in — the join to that room's `<culture>`.
        attrs += (("room", room),)
    if note.anchor_date is not None:
        attrs += (
            ("anchor", str(note.anchor_date)),
            ("anchor_is", _days_from(today, note.anchor_date)),
        )
    order, key = _ROW["note"]
    return ContextBlock(
        name="note", order=order, key=key, attrs=attrs,
        # HER OWN note, and the field values are hers too — model-authored from
        # a transcript, but written by her for herself. The member-authored text
        # in this block is the EVIDENCE, which carries its own marker.
        body=(
            f"  uncertainty: {escape(note.uncertainty)}\n"
            f"  closing condition: {escape(note.closing_condition)}\n"
            f"  body: {escape(body)}{evidence_line}"
        ),
    ).render()


def _covers(
    evidence: Sequence[TranscriptRow], *,
    rows: Sequence[TranscriptRow], created_at: int,
    bounds: tuple[int, int] | None, truncated: bool,
) -> bool:
    """Whether the evidence window actually SPANS the moment a note is about —
    moved here verbatim (in logic) from `nora.language.note_revisitor` in the
    note-curator merge (design 34 §5.2, the single most important thing the
    merge must not lose).

    `anchor_passed` (on `_Evidence`) says the moment has gone by. It says
    nothing about whether anybody was looking when it did, and treating the
    two as the same thing retires a note wrongly in two ways, both reachable
    in a live room:

    - **the room dies before the moment.** Note written 1 Aug, anchored to 9
      Sept, thirty messages across the first fortnight of August, silence
      after. The window is non-empty and contains no answer, so `expired`
      looks correct on what was shown — and the note is retired with ZERO
      evidence about September.
    - **the room is busier than the window.** The message that ANSWERS the
      question sits outside the newest `limit` rows. The window shows
      unrelated traffic, `expired` is accepted, and an ANSWERED note is
      recorded as having gone unanswered.

    Two conditions, both about the WINDOW rather than the model:

    **It must reach past the moment.** At least one message at or after the
    anchor's last instant.

    **It must reach back to before the NOTE.** Either the window is not
    truncated (no older traffic exists at all) or its oldest row predates
    `created_at`.

    **The reference point is the note, not the anchor** — an answer given
    weeks before the anchor (a booking made in early August for a 9 September
    date) is still an answer, so the window has to reach back to when the
    note was WRITTEN, not to when its anchor starts.

    Fails CLOSED: no bounds, no evidence, or either condition unmet returns
    `False`, and the caller leaves the note standing — until the anchor has
    LAPSED (`ANCHOR_LAPSE_MS`, `_Evidence.anchor_lapsed`), after which only the
    first condition is required. The second is unsatisfiable in a room busier
    than the window between a note and its moment, and «standing» then meant
    «forever» (2026-09-25: 8 notes in one unit, 2 to 17 days past). Past the
    lapse this function's second danger is reachable again: something said
    before the window — an answer, or a change of plan — is not shown, so an
    answered note can be labelled `expired` and a rescheduled one retired
    while still live. `_render_evidence` tells the model its window is cut
    short, and `nora_note_curator_total{action="lapsed"}` counts every
    verdict accepted this way.
    """
    if bounds is None or not evidence:
        return False
    reaches_past = _reaches_past(evidence, bounds)
    # `rows`, not `evidence`: the raw PRE-FILTER window. `evidence` has
    # already dropped everything at or before `created_at`, so its oldest row
    # cannot answer «did the window reach back far enough» — asking it that
    # is circular.
    reaches_back = (not truncated) or (bool(rows) and rows[0].ts <= created_at)
    return reaches_past and reaches_back


def _reaches_past(
    evidence: Sequence[TranscriptRow], bounds: tuple[int, int] | None,
) -> bool:
    """`_covers`' first half on its own: a human spoke at or after the
    anchor's last instant. The half the lapse rule keeps (`ANCHOR_LAPSE_MS`) —
    a room that fell silent before the moment still cannot testify about it.
    Fails closed on no bounds or no evidence."""
    return bounds is not None and bool(evidence) and evidence[-1].ts >= bounds[1]
