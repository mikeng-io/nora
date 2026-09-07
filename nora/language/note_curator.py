"""The note tier's housekeeping pass — `NoteCuratorAdaptor` (spec §9, merged
with the revisitor per design/dreamer-agent-loop/34-note-dreamer-consolidation
issue #99).

**The one rule that carries the design: merge is keyed on the ANCHOR, never on
topical similarity, so two weddings stay two notes.** That is enforced
structurally rather than by prompting — eligibility is
`nora.domain.notes.group_on_anchor_key`, a pure function over
`(anchor_date, RESOLVED entity set)` that BOTH stores call, so **the model is
never asked whether two notes should merge.** It is asked only what a body
SAYS, which is the one thing a key comparison cannot read.

**The entity half is `note_entity`, never `note.anchor_entity_ids`.** That
column is whatever the model handed `note_create`, verbatim and unresolved, and
a key over it is wrong in both directions at once: 「Grant 婚禮」/「Gabe's
wedding」/「婚禮」 are three keys and one occasion, while two unrelated notes
that both say 「wedding」 are one key and two occasions. **`note_entity` got a
writer with issue #100** (`d10093af`) — `SqlNoteStore.create`/`propose` now
resolve `anchor_entity_ids` against the shared entity registry and write it in
the same transaction — so merge is REACHABLE whenever entities resolve; a
group with nothing resolved is still refused and counted
(`no_entity_anchor`), but that is now a per-candidate outcome, not a
certainty for every group. The retire half is unaffected and is the half that
answers «the notebook only grows».

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
module still only merges and retires; it never speaks.

## Selection: SQL-only, THREE reads, and their union

`inputs` runs three cheap reads before any model call — `merge_groups`
(colliding anchors), `anchored_before` (expired anchors), and `due`
(`revisit_at` due, now-that-it-has-a-writer). A note in more than one of these
renders exactly once: a merge-group member is never also rendered standalone
(unchanged from before the merge), and a note selected by BOTH
`anchored_before` and `due_for_revisit` (an anchor that has passed, with a
stale `revisit_at` sitting behind it) is deduplicated the same way. **This is
what keeps the merge from turning into a full-notebook model-call sweep** —
the model is only ever asked about what the SQL selected, capped by
`max_groups_per_tick` + `max_expired_per_tick` + `max_revisits_per_tick`.

**`anchored_before`/`due_for_revisit` notes carry an evidence block**, gathered
in code from their own `established_in` (a workspace-keyed adaptor has no
room-scoped TOOL to do this with; §4.2's argument, inherited from the
revisitor) — the citation/`_covers` grounding applies to them, exactly the set
that inherits the revisitor's old logic.

**A note actually offered a merge THIS TICK is exempt**, a judgement call this
merge records rather than hides: eligibility for merging is entirely
code-decided (the anchor key), the model is asked only what a body SAYS, and
that has never needed evidence — a merge's `moot`/`closed` verdict on a
colliding pair is the pre-merge curator's own behaviour, unchanged. Requiring
a citation/`_covers` pass for the merge action itself would be a second,
unrelated strengthening riding on this issue, and would invalidate the whole
pre-existing merge test suite for no argued benefit.

**This is narrower than "is a candidate-group member" — deliberately, since
2026-09-06.** The exemption used to key on group MEMBERSHIP alone
(`note_id not in shown.due_or_anchored`, true of every member of every
candidate group whether or not that group's merge was ever offered this
tick). That was harmless only as long as `merge_groups` always returned empty
— `note_entity` had no writer (see above), so no note was ever actually a
group member in production. *The reading that would reopen this* was written
down at the time: "`note_entity` gaining a writer and merge becoming a live
path, at which point a group member's retirement risk is the same as any
other note's and the exemption should be re-examined." Issue #100 fired it —
merge groups are now reachable — and the re-examination found the bug this
paragraph now describes: a model returning an individual `closed`/`moot`
verdict for a note that IS in a candidate group, whose merge is NOT actually
offered/accepted that same tick (declined, or simply not answered with a
`merged_body`), retired via `supersede` with zero evidence or citation
grounding — exactly the "silence is not an answer" guarantee this tier
enforces everywhere else, silently bypassed for this one path.

**The fix**: `_ground`'s exemption and `commit`'s `untouchable` set are now
both computed from the SAME function, `_merged_away_ids` — every note id
belonging to a group whose survivor's id is in `offered` (the model named
that group with a `merged_body` at all this tick; see `_MergeOffered`'s own
docstring for why the store-level OUTCOME does not narrow this further). A
group member whose merge was not offered this tick falls into the ordinary
evidence-grounded path any other note takes — and since `inputs` never
gathers evidence for a merge-group member (immediately above), that path has
none to check against, so such a verdict is refused `no_evidence` rather than
guessed at. That is a real, if narrow, limitation of this fix: a note stuck in
a group whose merge the model keeps declining cannot retire via an individual
verdict either, until either the merge actually goes through or the group
stops colliding — never a wrong retirement, which is the property that
matters here. *The reading that would reopen THIS:* `inputs` gathering
evidence for a merge-group member too (the read this paragraph's predecessor
argued against paying for a genuinely inert path), at which point a declined
group member could be judged on real evidence instead of always refused for
having none.

**Scope: merge, retire, and — on retirement — discharge (issue #101).** Merge
and retire are also the pair that addresses the live problem — `config.toml`
said it plainly: *"No curator yet… the notebook only grows for now."* Growing
is what merge and retire stop. Discharge is what stops a CLOSED note's settled
content going nowhere: design 33 §7 names a note as a staging area and closing
as a discharge, not a bare retire — see `_run_discharge`'s own docstring for
the classification/routing shape and the judgement calls it records.

## Discharge (design 33 §7, 19 §1, issue #101)

**Only for a GROUNDED `closed` verdict that actually retires.** `expired`/
`moot` mean the room's moment passed with nothing answering it — there is no
settled content on that path, by construction, so discharge never runs for
them; that IS this tier's "nothing to discharge" case for those two verdicts,
and it costs nothing to state because it never reaches a model call or a
write. A merge-group member is likewise never discharged — merge eligibility
is code-decided and a merged note is not retired via `supersede` at all (see
`commit`'s `untouchable` set).

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
workspace-scoped background pass can reach). This is a real narrowing —
"Miles likes Yardbird for the jazz" only discharges when MILES's own message is
what closed the note, not when someone else's does — recorded here rather
than hidden. *The reading that would reopen this:* a name -> person resolver
reachable from this tier (the same kind of gap `note_entity` has for anchor
entities), at which point a person-claim could name a THIRD person instead of
only the citer.

**This module lives in `nora/language/` beside `note_render.py`** for the same
historical reason the Dreamer's watermark table does: the package predates the
tiers that use it. Recorded rather than fixed here — the extraction is its own
port change with its own migration.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, ClassVar, Literal, Protocol, get_args

from nora.domain.bounds import over_fetch
from nora.domain.gatherings import AmbiguousOpenGatheringsError
from nora.domain.models import TranscriptRow
from nora.domain.notes import (
    BODY_MAX_CHARS,
    MAX_DATELESS_STRIKES,
    Note,
    NoteSupersedeReason,
    StaleNoteHeadError,
    anchor_bounds_ms,
    default_revisit_at,
)
from nora.domain.ports import Proposal
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
#: is enforced at RUNTIME rather than by types because `nora/language` is
#: outside `make gate`'s mypy scope (#61) — a `Literal` alone would be checked
#: by nothing here.
CURATOR_ACTIONS: frozenset[str] = frozenset({
    "merge",           # a group was merged into its survivor
    "merge_refused",   # the survivor moved, or the merge was refused — NOT an
                       # error: the group is still a group next tick
    "merge_failed",    # the store refused the merge for a domain reason; the
                       # tick continues and earlier merges stand
    "retire_failed",   # supersede raised; the tick continues
    "retire_stale",    # the note moved between inputs and commit, so the
                       # reading the model judged no longer describes it
    "closed",          # retired: the question was answered
    "moot",            # retired: the occasion passed and nothing will settle it
    "expired",         # retired: the occasion passed with nothing answering it
                       # — the revisitor's word, merged in with its own
                       # grounding requirement (see `_ground`)
    "dateless_stale",  # OBSERVED, not acted on: a dateless note's strikes
                       # crossed MAX_DATELESS_STRIKES after a wake with no
                       # tag-path progress. Eligibility, not a retirement —
                       # see `commit`'s wake loop
})

#: Every value `nora_note_curator_dropped_total{reason}` can carry.
CURATOR_DROPS: frozenset[str] = frozenset({
    "malformed",                # a field missing, wrong type, verdict outside
                                # the four, or a body over the cap
    "unknown_note",             # a handle this run did not render
    "not_shown_in_group",       # a merge for a group SQL did not select
    "conflicting_verdict",      # two contradictory answers about one note
    "conflicting_merged_body",  # two different bodies for one survivor
    "body_too_long",            # a merged body over BODY_MAX_CHARS — the
                                # objective asked for more than the cap allows
    "shorter_than_inputs",      # a merged body shorter than the longest body
                                # it was meant to keep — a summary, on a verb
                                # whose whole purpose is that nothing is lost
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

#: Every value `nora_note_curator_groups_refused_total{reason}` can carry.
CURATOR_GROUP_REFUSALS: frozenset[str] = frozenset({"no_entity_anchor"})

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
})

#: Whether a note that actually retired `closed` had anything to discharge —
#: the ordinary, common answer is `empty` (R-27: a discharge finding nothing
#: is `nothing_found`, not a wiring gap), so this is what lets the operator
#: tell "discharge ran and found nothing" apart from "discharge never ran"
#: (the latter has NO sample under this metric for that note at all).
DISCHARGE_NOTE_RESULTS: frozenset[str] = frozenset({"items", "empty"})

#: How many due-by-`revisit_at` notes one tick will judge — the revisitor's own
#: default, carried across with the mechanism (design 33/34; spec §9.6).
DEFAULT_MAX_REVISITS_PER_TICK = 10

#: Messages of a note's own `established_in` rendered as its evidence. `recent`
#: returns the newest `limit` rows OLDEST-FIRST; filtered to rows that actually
#: postdate the note (a closing condition cannot have been met before the note
#: existed) and to rows that are not Nora's own. This cap is ALSO what
#: `_covers` measures the anchor window against — see that function.
DEFAULT_EVIDENCE_MESSAGES_PER_NOTE = 40

#: How many discharge items ONE closed note's classification may carry — a
#: settled note yields a handful of claims (a commitment, a venue, who likes
#: what), never a claim mine. Parsed items past this are simply not read;
#: `MAX_DISCHARGE_ITEMS_PER_NOTE + 1` would be the sign of a model padding the
#: field rather than classifying, and that is a prompt problem, not a runtime
#: one this cap needs to detect.
MAX_DISCHARGE_ITEMS_PER_NOTE = 5

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
#: reads `get_args` off this). Two enforcers, because `nora/language` is
#: outside `make gate`'s mypy scope (#61): the `Literal` is a type error here,
#: `_Evidence`'s construction is a runtime error in every test that builds one.
EvidenceState = Literal["present", "absent", "self_only", "unavailable"]
EVIDENCE_STATES: tuple[str, ...] = get_args(EvidenceState)


def _now_ms() -> int:
    """The default `clock` collaborator — a module-level function rather than
    a call, so it satisfies `Callable[[], int]` without a module-level mutable
    singleton (DEC-13)."""
    import time

    return int(time.time() * 1000)


class _NoteStore(Protocol):
    async def standing(self, *, workspace: str) -> tuple[Note, ...]: ...

    async def merge_groups(
        self, *, workspace: str, limit: int,
    ) -> tuple[
        tuple[tuple[tuple[Note, str], ...], ...],
        tuple[tuple[Note, ...], ...],
    ]: ...

    async def anchored_before(
        self, *, workspace: str, day: int, limit: int,
    ) -> tuple[tuple[Note, str], ...]: ...

    async def due_for_revisit(
        self, *, workspace: str, now_ms: int, limit: int,
    ) -> tuple[tuple[Note, str], ...]: ...

    async def read(
        self, *, workspace: str, note_id: str,
    ) -> tuple[Note, Any] | None: ...

    async def merge(
        self, *, workspace: str, survivor: str, survivor_parent: str,
        merged_body: str, retire: Sequence[str], established_by: str,
        instruction_sha256: str | None, model: str | None, now_ms: int,
    ) -> int: ...

    async def supersede(
        self, *, key: str, target: str, replaced_by: str | None,
        because: str, source: str | None, now_ms: int,
    ) -> int: ...

    async def record_wake(
        self, *, workspace: str, note_id: str, next_revisit_at: int,
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
    anywhere — the curator's OUTPUT is a merge or a retirement, and this is
    what the model said on the way there.
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
class _MergedBody:
    """The one body that keeps what every note in a group knew.

    Keyed on the SURVIVOR's id — the group's oldest note — because that is the
    handle `commit` merges into and the only one the model was given for the
    group. A separate type from `_Verdict` for the reason the payload shapes
    are separate one layer down: two things with different required fields
    should not be one thing with optional ones, or nothing can be refused.
    """

    note_id: str
    body: str


@dataclass(frozen=True)
class _MergeOffered:
    """The model ASKED for this group to be merged. Nothing else.

    **This is the whole of HIGH 1 and it is a claim, not an outcome.** A group
    the model answered with a merged body is a group it is telling us is ONE
    occasion written twice — and the objective tells it to return a verdict for
    every note it was shown, so `moot` on both members of a colliding group
    whose date has passed is the EXPECTED answer, not an edge.

    When the merge then does not land — an over-cap body, two different
    bodies, a body naming the wrong member, a lost CAS — `commit` used to fall
    through to the retirement loop and retire each member on its own verdict,
    with `replaced_by=None`. Both notes leave the index, neither points at
    anything, and the content the merge existed to preserve is reachable only
    by direct database query. That is content loss on the NORMAL path.

    So the claim is carried separately from the body: a `_MergedBody` is
    produced only when the body survives parsing, and this is produced
    whenever the group was named at all. `commit` protects the group on THIS,
    never on what became of the merge.

    `survivor` is the resolved note id of the group's survivor — the same
    handle-to-id resolution every other payload carries, done once in
    `accept`.
    """

    survivor: str


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


def _no_evidence() -> _Evidence:
    """The default for a note `_ground` must judge but `inputs` never gathered
    evidence for — every merge-group member whose merge is not offered/accepted
    this tick (see `_merged_away_ids`). Empty `message_ids` sends it straight
    into `_ground`'s existing `no_evidence` refusal, the same fail-closed
    outcome a real evidence read finding nothing produces — this is a stand-in
    for "never looked", not a new code path.

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
    #: group handles, survivor first — the same ordering `merge_groups`
    #: returned, so `commit` can still name the survivor without an id ever
    #: reaching the model.
    merge_groups: tuple[tuple[str, ...], ...]
    heads: Mapping[str, str]
    #: ANY handle in a group -> that group's SURVIVOR handle.
    group_of: Mapping[str, str]
    #: survivor handle -> the LONGEST body in that group, in characters.
    longest_body: Mapping[str, int]
    #: note id -> its gathered evidence, for every note `inputs` actually
    #: gathered evidence for (`anchored_before`/`due_for_revisit`). A
    #: merge-group member has NO entry here regardless of whether its merge is
    #: offered this tick — `_ground` reads `_NO_EVIDENCE` for the ones it must
    #: still judge on their own (see `_merged_away_ids`).
    evidence: Mapping[str, _Evidence]
    #: note id -> {message id -> row}, for the SAME evidence-bearing notes
    #: `evidence` covers (never populated for a merge-group member, which has
    #: no evidence block at all). This is what lets `commit`'s discharge step
    #: resolve a grounded `closed` verdict's `cited_message_id` back to the
    #: person who sent it (issue #101) without a second `RecordStore` read —
    #: `_gather_evidence` already fetched these rows once, for `_covers`.
    evidence_rows: Mapping[str, Mapping[int, TranscriptRow]]
    #: note id -> its anchor_date, for `commit`'s `record_wake` calls — carried
    #: here rather than re-read, so a note's own anchor shape at SELECTION time
    #: (not at commit time, when it may have moved) decides its next wake.
    anchor_date: Mapping[str, int | None]
    #: Notes selected by `anchored_before` or `due_for_revisit` this tick,
    #: EXCLUDING merge-group members — the set `commit` calls `record_wake` on
    #: after retirement, for every one of them still standing. Merge-group
    #: members are never in this set: merge eligibility is code-decided and
    #: has nothing to do with the wake/strike breaker (§5.4 — "not two
    #: schedulers", but the two modes still apply to disjoint note sets).
    due_or_anchored: frozenset[str]


def _merged_away_ids(
    shown: _Shown, *, offered: frozenset[str],
) -> frozenset[str]:
    """Every note id belonging to a merge group whose SURVIVOR's resolved id
    is in `offered` — the model named that group with a `merged_body` at all
    this tick, whether or not the store-level `merge` call actually lands
    (see `_MergeOffered`'s own docstring: the claim is what protects the
    group, not the outcome).

    **The one definition, shared by `_ground` and `commit`.** `_ground` uses
    it to decide whether a note's individual verdict may skip evidence
    grounding; `commit` uses the identical set, under the name `untouchable`,
    to decide whether an individual verdict may retire the note at all. Before
    this was split out, `_ground` answered a DIFFERENT, wider question
    (`note_id not in shown.due_or_anchored`, true of every member of every
    CANDIDATE group regardless of whether that group's merge was ever offered)
    — the correctness bug the module docstring's "Merge-GROUP members" section
    now documents. One function used by both call sites is what makes it
    structurally impossible for them to drift apart again.
    """
    return frozenset(
        shown.by_handle[h]
        for g in shown.merge_groups
        if shown.by_handle[g[0]] in offered
        for h in g
    )


class NoteCuratorAdaptor:
    """One `DreamAdaptor`: read a workspace's notebook, decide what has closed
    and what is the same occasion twice, and write merges and retirements.

    `key_kind = "workspace"` — the notebook is workspace-scoped (§3/DEC-105),
    so both `friends` rooms tick ONE notebook rather than two. That is what
    `Dependencies.workspace_scopes` exists for.

    `uses_watermark = False`, and this is the interesting one. The other
    adaptors read an append-only log through a cursor. This one reads LIVE
    STATE: which notes collide on an anchor, which anchors have passed, and
    which notes' `revisit_at` has come due. None of those questions has a
    "since when" — a duplicate is a duplicate however long it has been one,
    and **retirement is TIME-driven, not evidence-driven** (§9.1). A watermark
    would make the pass stop noticing an expired note the moment the notebook
    went quiet, which is exactly when expiry matters.

    **The pass is self-limiting FOR THE ACTIONS IT TAKES, and not otherwise.**
    A merge removes its group; a retirement removes its note. But a group the
    model DECLINES to merge is re-selected every tick, forever, and DOES
    occupy a `max_groups_per_tick` slot — it is eligible, so it is inside the
    limit. The same is true of a due note the model leaves `open`: it stays
    due (its `revisit_at` does not advance without tag-path progress, see
    `record_wake`) and is re-selected until either progress resets its clock
    or its strikes exhaust it (dateless) or its anchor passes (anchored).
    """

    name = "note-curation"
    key_kind = "workspace"
    uses_watermark = False
    objective = "note-curation"
    envelope_key = "verdicts"
    #: No tools. The curator reasons about bodies and evidence this process
    #: selected and handed it; it has nothing to look up, and `read_only_tools`
    #: being a ceiling rather than a grant means an empty subset is the honest
    #: declaration rather than a missing one.
    tools: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        notes: _NoteStore,
        record: _TranscriptSource,
        today: Callable[[], int],
        max_groups_per_tick: int,
        max_expired_per_tick: int,
        max_revisits_per_tick: int = DEFAULT_MAX_REVISITS_PER_TICK,
        evidence_messages_per_note: int = DEFAULT_EVIDENCE_MESSAGES_PER_NOTE,
        metrics: Metrics | None = None,
        clock: Callable[[], int] = _now_ms,
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
    ) -> None:
        self._notes = notes
        self._record = record
        # A callable, not a date: the pass compares against a YYYYMMDD calendar
        # day and the clock is the caller's to own — a store or an adaptor that
        # reads the wall clock cannot be tested for the boundary case that
        # matters (a note anchored to TODAY is not expired).
        self._today = today
        self._max_groups = max_groups_per_tick
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
        "nora_note_curator_groups_refused_total": (
            "reason", CURATOR_GROUP_REFUSALS,
        ),
        "nora_note_curator_candidates_total": ("evidence", frozenset(EVIDENCE_STATES)),
        "nora_note_retirements_total": ("because", frozenset(NoteSupersedeReason.ALL)),
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
        # TRUNCATION read BEFORE either filter — a property of what `recent`
        # COULD return, not of what survived. `is_nora` rows are excluded HERE
        # because `recent` does not exclude them; her own words are an
        # intention she stated, not the group settling anything, and closing a
        # note on them would be self-attestation deciding a retirement.
        truncated = len(rows) >= self._evidence_limit
        since = [row for row in rows if row.ts > note.created_at]
        evidence = tuple(row for row in since if not row.is_nora)
        if state != "unavailable" and not evidence:
            state = "self_only" if since else "absent"
        bounds = (
            anchor_bounds_ms(note.anchor_date)
            if note.anchor_date is not None else None
        )
        self._inc("nora_note_curator_candidates_total", evidence=state)
        ev = _Evidence(
            message_ids=frozenset(row.id for row in evidence),
            has_anchor=bounds is not None,
            anchor_passed=bounds is not None and now_ms > bounds[1],
            anchor_covered=_covers(
                evidence, rows=rows, created_at=note.created_at,
                bounds=bounds, truncated=truncated,
            ),
        )
        return ev, _render_evidence(evidence, state), evidence

    # -- inputs -----------------------------------------------------------

    async def inputs(self, *, key: str, batch: Sequence[Any]) -> str:
        """The colliding groups, the expired notes, and the due-by-`revisit_at`
        notes, rendered with their evidence.

        `batch` is always empty (`uses_watermark = False`) and is accepted and
        ignored — the port's shape rather than a surprise.

        **Returns `""` when there is nothing, and that is load-bearing.**
        `DreamAdaptor` has no `should_fire` verb; the runtime's fire condition
        is "did `inputs` render anything to reason about", so an adaptor that
        renders a polite empty form instead of nothing buys a model call every
        tick per workspace forever.
        """
        groups, refused = await self._notes.merge_groups(
            workspace=key, limit=self._max_groups,
        )
        for _ in refused:
            self._inc(
                "nora_note_curator_groups_refused_total",
                reason="no_entity_anchor",
            )
        now_ms = self._clock()
        expired_all = await self._notes.anchored_before(
            workspace=key, day=self._today(), limit=self._max_expired,
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
            workspace=key, now_ms=now_ms, limit=over_fetch(self._max_revisits),
        )
        if len(due_over) > self._max_revisits:
            log.info(
                "note curator: more than %d notes due for revisit in "
                "workspace %s this tick; judging %d and deferring the rest",
                self._max_revisits, key, self._max_revisits,
            )
            self._inc("nora_note_curator_deferred_total")
        due_over = due_over[: self._max_revisits]

        # A note in a merge group is not also rendered as expired or due — the
        # group wins (merging keeps both bodies; retiring one keeps neither).
        in_a_group = {n.id for g in groups for n, _ in g}
        expired = tuple(
            (note, body) for note, body in expired_all
            if note.id not in in_a_group
        )
        expired_ids = {n.id for n, _ in expired}
        # A note due by BOTH `anchored_before` and `due_for_revisit` (an
        # anchor that has passed, with a stale `revisit_at` sitting behind it)
        # renders once, under `expired` — the same de-duplication principle.
        due = tuple(
            (note, body) for note, body in due_over
            if note.id not in in_a_group and note.id not in expired_ids
        )

        standing_count = len(await self._notes.standing(workspace=key))
        # PUBLISHED UNCONDITIONALLY, INCLUDING ZERO, and BEFORE the early
        # return — an idle tick that never wrote these would leave the gauges
        # at whatever the last busy tick reported, which is a high-water mark
        # wearing a level's help text.
        self._gauge("nora_note_curator_standing", float(standing_count), workspace=key)
        self._gauge(
            "nora_note_curator_bodies_read",
            float(sum(len(g) for g in groups) + len(expired_all) + len(due_over)),
            workspace=key,
        )

        # ORDINAL HANDLES, NOT IDS — see the pre-merge module's own war story:
        # the Dreamer's egress screen drops any item containing a hyphenated
        # uuid, so a curator or revisitor that asked about ids got every
        # answer silently rejected. `nora.language.note_revisitor`'s own
        # comment on this survives only in git history now; the handle scheme
        # is unchanged.
        by_handle: dict[str, str] = {}
        group_handles: list[tuple[str, ...]] = []
        group_of: dict[str, str] = {}
        longest_body: dict[str, int] = {}
        evidence_by_note: dict[str, _Evidence] = {}
        evidence_rows_by_note: dict[str, dict[int, TranscriptRow]] = {}
        anchor_date_by_note: dict[str, int | None] = {}
        due_or_anchored: set[str] = set()
        ordinal = itertools.count(1)
        parts: list[str] = []

        for g in groups:
            handles: list[str] = []
            member_blocks: list[str] = []
            for note, body in g:
                handle = str(next(ordinal))
                by_handle[handle] = note.id
                handles.append(handle)
                # NO EVIDENCE for a merge-group member — deliberately, see the
                # module docstring's "Merge-GROUP members are deliberately
                # exempt". `due_or_anchored` does not gain this note's id, so
                # `_ground` treats any verdict about it as ungrounded-exempt
                # (accepted as-is, the pre-merge curator's own behaviour).
                member_blocks.append(_note_block(handle, note, body, None))
            group_handles.append(tuple(handles))
            for handle in handles:
                group_of[handle] = handles[0]
            longest_body[handles[0]] = max(len(body) for _, body in g)
            parts.append(f'<merge_group survivor="{handles[0]}">')
            parts.extend(member_blocks)
            parts.append("</merge_group>")

        for note, body in expired:
            handle = str(next(ordinal))
            by_handle[handle] = note.id
            ev, ev_text, ev_rows = await self._gather_evidence(note, now_ms=now_ms)
            evidence_by_note[note.id] = ev
            evidence_rows_by_note[note.id] = {row.id: row for row in ev_rows}
            anchor_date_by_note[note.id] = note.anchor_date
            due_or_anchored.add(note.id)
            parts.append(_note_block(handle, note, body, ev_text))

        for note, body in due:
            handle = str(next(ordinal))
            by_handle[handle] = note.id
            ev, ev_text, ev_rows = await self._gather_evidence(note, now_ms=now_ms)
            evidence_by_note[note.id] = ev
            evidence_rows_by_note[note.id] = {row.id: row for row in ev_rows}
            anchor_date_by_note[note.id] = note.anchor_date
            due_or_anchored.add(note.id)
            parts.append(_note_block(handle, note, body, ev_text))

        self._shown[key] = _Shown(
            by_handle=by_handle,
            merge_groups=tuple(group_handles),
            heads={
                n.id: n.head_revision_id
                for n in [x for g in groups for x, _ in g]
                + [n for n, _ in expired] + [n for n, _ in due]
            },
            group_of=group_of,
            longest_body=longest_body,
            evidence=evidence_by_note,
            evidence_rows=evidence_rows_by_note,
            anchor_date=anchor_date_by_note,
            due_or_anchored=frozenset(due_or_anchored),
        )
        if not groups and not expired and not due:
            return ""
        return "\n".join(parts)

    # -- accept -------------------------------------------------------------

    async def accept(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> Sequence[object]:
        """Typed parse into `_Verdict`, `_MergedBody` and `_MergeOffered`.
        Drop, log, count — never guess.

        The drop reasons are all counted under
        `nora_note_curator_dropped_total{reason}` and all named in that
        metric's help text (a parameterised test asserts the two agree):

        - `malformed` — a field missing, of the wrong type, or a `verdict`
          outside the four the objective defines.
        - `unknown_note` — names a note this RUN did not show.
        - `not_shown_in_group` — a `merged_body` whose handle is not the
          SURVIVOR of a group this run selected.
        - `body_too_long` / `shorter_than_inputs` / `conflicting_merged_body`
          — the merged body itself; see `_merged_bodies`.
        - `no_evidence` / `citation_missing` / `citation_ungrounded` /
          `anchor_not_passed` / `anchor_not_covered` — the grounding checks a
          `closed`/`expired`/`moot` claim must pass; see `_ground`. A claim
          that fails one of these is still returned as an UNGROUNDED
          `_Verdict` so it can collide with a contradicting grounded claim in
          `commit` (a model saying both «closed, uncited» and «open» about one
          note has said «I cannot tell which»), but it can never itself cause
          an action.

        **Every merge item still emits `_MergeOffered` for the group**, even a
        refused one — see `_MergeOffered`'s own docstring.
        """
        shown = self._shown.get(key) or _Shown({}, (), {}, {}, {}, {}, {}, {}, frozenset())
        out: list[object] = self._merged_bodies(key=key, items=items)
        # Every group whose SURVIVOR the model named with a `merged_body` this
        # tick — `_merged_bodies` has already scanned the whole of `items` by
        # this point, so this is complete before the per-item loop below reads
        # it. `merged_away` is `commit`'s own `untouchable`, computed once and
        # shared (`_merged_away_ids`) rather than derived twice: see the
        # module docstring's "Merge-GROUP members" section for the bug two
        # different definitions produced.
        offered = frozenset(
            p.survivor for p in out if isinstance(p, _MergeOffered)
        )
        merged_away = _merged_away_ids(shown, offered=offered)
        for item in items:
            if "verdict" not in item:
                # A merge-only answer is not malformed.
                continue
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
                merged_away=merged_away,
            )
            # Discharge classification (issue #101) — only for a verdict that
            # is ACTUALLY closed and grounded, and only for a note evidence
            # grounding applies to at all (never a merge-group member, which
            # has no evidence block and no citation to ground against — see
            # the module docstring). `self._discharge_enabled` gates this at
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
        merged_away: frozenset[str],
    ) -> _Verdict:
        """The grounding a `closed`/`expired`/`moot` claim must pass before it
        may ACT — never whether it was MADE (a claim that fails still returns
        a `_Verdict(grounded=False)`, exactly as `nora.language.
        note_revisitor`'s own `_ungrounded` did, for the same conflict-check
        reason).

        `open` is always grounded — there is nothing to check, and it is the
        ordinary answer.

        **A note actually offered a merge this tick is EXEMPT entirely**
        (`note_id in merged_away`, `_merged_away_ids` — see the module
        docstring's "A note actually offered a merge THIS TICK is exempt").
        Its verdict is accepted as-is, the pre-merge curator's own behaviour:
        eligibility for merging is code-decided and the model is asked only
        what a body SAYS, which has never needed evidence. This is narrower
        than "is a candidate-group member" — a group member whose merge is
        NOT offered/accepted this tick falls through to the ordinary check
        below like any other note.

        For a note `anchored_before`/`due_for_revisit` selected, OR a
        merge-group member whose merge this tick did not offer, `closed`,
        `expired` and `moot` all require the note's evidence window to be
        non-empty (`no_evidence`): a room that has said nothing since the note
        was written establishes that nobody has spoken, never that a question
        went answered OR unanswered — and a merge-group member has no
        evidence window at all (`inputs` never gathers one for a candidate
        group; see its own docstring), so this refusal is exactly what such a
        note gets: refused for lack of evidence, never guessed at. Past that:

        - `closed` needs a `cited_message_id` that is actually one of the ids
          this run showed for THAT note (`citation_missing` /
          `citation_ungrounded`).
        - `expired`/`moot` need the CLOCK to agree the anchor has passed
          (`anchor_not_passed` — also the refusal for a dateless note, which
          has no anchor to have passed) and the evidence window to actually
          SPAN that moment (`anchor_not_covered`, `_covers`'s own rule: the
          room fell silent before the anchor, or is busier than the window).
        """
        if verdict == "open" or note_id in merged_away:
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
            self._inc(
                "nora_note_curator_dropped_total", reason="anchor_not_covered")
            return _Verdict(note_id=note_id, verdict=verdict, why=why, grounded=False)
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

    def _merged_bodies(
        self, *, key: str, items: Sequence[Mapping[str, object]]
    ) -> list[object]:
        """The merge half of `accept` — unchanged by the note-revisitor merge.
        See the historical note in git for the "first version had no way to
        produce one" story; it is preserved verbatim here because merge
        eligibility and its refusals are entirely code-decided and evidence
        plays no part in them.
        """
        shown = self._shown.get(key) or _Shown({}, (), {}, {}, {}, {}, {}, {}, frozenset())
        survivors = {g[0] for g in shown.merge_groups}
        out: list[object] = []
        bodies: list[_MergedBody] = []
        offered: set[str] = set()
        conflicted: set[str] = set()

        def _offer(raw_handle: object) -> None:
            if not isinstance(raw_handle, str):
                return
            survivor_handle = shown.group_of.get(raw_handle)
            if survivor_handle is None:
                return
            offered.add(shown.by_handle[survivor_handle])

        for item in items:
            if "merged_body" not in item:
                continue
            body = item.get("merged_body")
            note_id = item.get("note_id")
            _offer(note_id)
            if isinstance(body, str) and len(body) > BODY_MAX_CHARS:
                self._inc(
                    "nora_note_curator_dropped_total", reason="body_too_long")
                log.warning(
                    "note curator returned a merged body over the cap; the "
                    "group is left alone")
                continue
            if (
                not isinstance(note_id, str)
                or not isinstance(body, str)
                or not body.strip()
            ):
                self._inc(
                    "nora_note_curator_dropped_total", reason="malformed")
                log.warning("note curator dropped a malformed merged_body")
                continue
            if note_id not in survivors:
                self._inc(
                    "nora_note_curator_dropped_total",
                    reason="not_shown_in_group")
                log.warning(
                    "note curator proposed a merge for a group this run did "
                    "not select; dropped")
                continue
            resolved = shown.by_handle[note_id]
            if len(body) < shown.longest_body.get(note_id, 0):
                self._inc(
                    "nora_note_curator_dropped_total",
                    reason="shorter_than_inputs")
                log.warning(
                    "note curator returned a merged body shorter than the "
                    "longest body it was meant to keep; the group is left "
                    "alone")
                continue
            if resolved in conflicted:
                continue
            seen = next((m for m in bodies if m.note_id == resolved), None)
            if seen is not None:
                if seen.body != body:
                    bodies.remove(seen)
                    conflicted.add(resolved)
                    self._inc(
                        "nora_note_curator_dropped_total",
                        reason="conflicting_merged_body")
                    log.warning(
                        "note curator returned two different merged bodies "
                        "for one survivor; the group is left alone")
                continue
            bodies.append(_MergedBody(note_id=resolved, body=body))
        out.extend(bodies)
        out.extend(_MergeOffered(survivor=s) for s in sorted(offered))
        return out

    # -- commit -------------------------------------------------------------

    async def commit(
        self, *, key: str, proposals: Sequence[Proposal], now_ms: int
    ) -> int:
        """The tier's write sequence:

        1. **Merges**, through `SqlNoteStore.merge` — unchanged.
        2. **Retirements**, for a GROUNDED verdict of `closed`, `expired` or
           `moot`. `open` and an ungrounded claim both write nothing.
        3. **Wakes** (NEW with the merge) — for every note `inputs` selected
           via `anchored_before`/`due_for_revisit` (never a merge-group
           member) that is STILL STANDING after step 2, `record_wake` is
           called with a freshly computed `next_revisit_at`. This is what
           gives `revisit_at`/`strikes` their production writer (§5.3) and
           what keeps a note that never changes from being due forever with
           no signal that it is.

        Returns rows written — for this pass, notes merged-away plus notes
        retired.
        """
        merged_bodies = {
            p.payload.note_id: p.payload
            for p in proposals
            if isinstance(p.payload, _MergedBody)
        }
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

        shown = self._shown.get(key) or _Shown({}, (), {}, {}, {}, {}, {}, {}, frozenset())
        provenance = proposals[0] if proposals else None
        retired = 0

        offered = {
            p.payload.survivor
            for p in proposals
            if isinstance(p.payload, _MergeOffered)
        }

        for group in shown.merge_groups:
            survivor = shown.by_handle[group[0]]
            body = merged_bodies.get(survivor)
            if body is None:
                continue
            try:
                count = await self._notes.merge(
                    workspace=key, survivor=survivor,
                    survivor_parent=shown.heads[survivor],
                    merged_body=body.body,
                    retire=[shown.by_handle[h] for h in group[1:]],
                    established_by=(
                        provenance.established_by if provenance else "curator"
                    ),
                    instruction_sha256=(
                        provenance.instruction_sha256 if provenance else None
                    ),
                    model=provenance.model if provenance else None,
                    now_ms=now_ms,
                )
            except StaleNoteHeadError:
                log.info("note merge refused; the survivor moved under it")
                self._inc("nora_note_curator_total", action="merge_refused")
                continue
            except ValueError:
                log.exception("note merge failed; continuing with the tick")
                self._inc("nora_note_curator_total", action="merge_failed")
                continue
            retired += count
            self._inc("nora_note_curator_total", action="merge")

        untouchable = _merged_away_ids(shown, offered=frozenset(offered))
        retired_this_tick: set[str] = set()
        for note_id, verdict in verdicts.items():
            if (
                verdict.verdict == "open"
                or not verdict.grounded
                or note_id in untouchable
            ):
                continue
            because = (
                NoteSupersedeReason.RETIRED_CLOSED
                if verdict.verdict == "closed"
                else NoteSupersedeReason.RETIRED_EXPIRED
            )
            try:
                current = await self._notes.read(
                    workspace=key, note_id=note_id)
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
                count = await self._notes.supersede(
                    key=key, target=note_id, replaced_by=None,
                    because=because, source=None, now_ms=now_ms,
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
                    await self._run_discharge(
                        note=current[0], verdict=verdict,
                        evidence_rows=shown.evidence_rows.get(note_id, {}),
                        now_ms=now_ms, provenance=provenance,
                    )

        # -- wakes: record_wake's first production caller (§5.3) -------------
        #
        # Every note `inputs` selected via `anchored_before`/`due_for_revisit`
        # that is still standing gets its wake recorded, REGARDLESS of what
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
                    workspace=key, note_id=note_id, next_revisit_at=next_at,
                    now_ms=now_ms,
                )
            except Exception:
                log.exception(
                    "note curator: record_wake failed for note %s; "
                    "continuing", note_id,
                )
                continue
            if outcome is None:
                # Not reachable from this workspace any more — retired by
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
        delivery `platform` for the conversation — this workspace-scoped
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
            written = await self._conclusions.conclude(
                peer_id=self._ai_peer, target=cited_row.person_id,
                content=claim.content, workspace_id=note.workspace,
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


def _render_evidence(
    evidence: Sequence[TranscriptRow], state: EvidenceState,
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
    if not evidence:
        return (
            "evidence: NONE — nothing the group has said since this note was "
            "written is in the messages I can see."
            if state == "self_only" else
            "evidence: NONE — nothing has been said in that conversation "
            "since this note was written."
        )
    lines = ["evidence, newest last:"]
    for row in evidence:
        speaker = row.person_name or row.sender_name or row.sender_id
        lines.append(f"[{row.id}] {speaker}: {row.body or ''}")
    return "\n".join(lines)


def _note_block(
    handle: str, note: Note, body: str, evidence_text: str | None,
) -> str:
    """One note, rendered whole: title, uncertainty, closing condition, body,
    anchor (if any) and, for an `anchored_before`/`due_for_revisit` note,
    evidence. Bodies are rendered whole — the body IS the question, and a
    truncation would have the model deciding whether an arrangement has
    closed from a prefix of it.

    `evidence_text=None` renders EXACTLY the pre-merge curator's group-member
    shape (title/uncertainty/closing condition/body, no evidence line) — the
    module docstring's "Merge-GROUP members are deliberately exempt".
    """
    anchor_attr = f' anchor="{note.anchor_date}"' if note.anchor_date is not None else ""
    evidence_line = f"\n  {evidence_text}" if evidence_text is not None else ""
    return (
        f'<note id="{handle}" title="{note.title}"{anchor_attr}>\n'
        f"  uncertainty: {note.uncertainty}\n"
        f"  closing condition: {note.closing_condition}\n"
        f"  body: {body}{evidence_line}\n"
        f"</note>"
    )


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
    `False`, and the caller leaves the note standing.
    """
    if bounds is None or not evidence:
        return False
    end = bounds[1]
    reaches_past = evidence[-1].ts >= end
    # `rows`, not `evidence`: the raw PRE-FILTER window. `evidence` has
    # already dropped everything at or before `created_at`, so its oldest row
    # cannot answer «did the window reach back far enough» — asking it that
    # is circular.
    reaches_back = (not truncated) or (bool(rows) and rows[0].ts <= created_at)
    return reaches_past and reaches_back
