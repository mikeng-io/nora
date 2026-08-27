"""The note tier's types. A note is TRACKED UNCERTAINTY — never a settled claim
(which is a fact, with a warrant) and never a conclusion about a person (which
is Honcho's `(observer, observed)` axis). See
`docs/superpowers/specs/2026-08-21-note-tier-design.md`.

**The four fields are the point of this module**, and three of them are only
load-bearing if the wrong thing is *unrepresentable* rather than discouraged —
the move `nora.domain.facts.FactVerdict` makes with `not_covered`:

- **`closing_condition`** — what would settle this. Retirement (§7.4),
  promotion-readiness (§7.3) and a revisit's success criterion (§10) are all
  read off this one field, so a note without one has three mechanisms with no
  input. A thing nothing would resolve is not an uncertainty being tracked; it
  is a fact, or it is ignorance.
- **the anchor** (`anchor_date` and/or `anchor_entity_ids`) — merge eligibility
  and mechanical retirement are computed from it. "We should hang out sometime"
  has no referent anything could later be looked up by; it dies as a candidate
  row nobody sees.
- **`title` / `description`** — the injected index entry (§5.1). Capped HERE,
  because truncating in the renderer is silent loss and a constructor that
  refuses is not.

**Where this tier deliberately differs from the fact tier, and why the port has
the shape it does.** `fact` carries a biconditional —
`(superseded_by is null) = (superseded_at is null)` — because a contradicted
fact is always replaced by the corrected one. **A note is not.** Retirement has
no successor: a dinner that happened is replaced by nothing. So this module
asserts the *implication* only (a replacement implies a retirement, never the
converse), and that asymmetry is exactly why `DreamSink.supersede` takes
`replaced_by: str | None` rather than a required id (DEC-130, spec §15.5).
Collapsing this into the fact tier's biconditional would force this tier to mint
a placeholder id to satisfy a signature — the invent-a-value-to-satisfy-a-schema
move §7.3 forbids elsewhere.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The injected index carries one line per note on EVERY tag (§5.1), so these
#: are prompt-budget decisions, not cosmetic ones. They are enforced in
#: `__post_init__` rather than in `nora.language.note_render` because a renderer
#: that truncates loses content silently, and the whole tier is built on the
#: index being a complete listing of what exists.
TITLE_MAX_CHARS = 60
DESCRIPTION_MAX_CHARS = 200

#: Anchor dates are `YYYYMMDD` integers, not epoch milliseconds. The anchor is
#: compared for EQUALITY when deciding merge eligibility (§7.1) and against
#: "today" when deciding expiry (§7.4) — both are calendar-day questions, and a
#: millisecond timestamp makes two notes about the same day unequal for a reason
#: that has nothing to do with the day. Every other timestamp in this module is
#: epoch-ms, matching the fact tier.
_MIN_ANCHOR_DATE = 1_000_00_00


def _required(value: str, what: str) -> str:
    """Non-empty after stripping, or refuse. `'   '` is non-empty to `if not x`,
    which is the hole `fact_model_is_named_check` closes with `btrim` — the same
    hole, one layer up."""
    if not value or not value.strip():
        raise ValueError(f"a note needs a {what}")
    return value


@dataclass(frozen=True, slots=True)
class Note:
    """One tracked uncertainty, scoped to a workspace.

    `established_in` is the conversation it was born in. It is a **routing key,
    never a filter** (§3): the workspace already is the disclosure boundary
    (DEC-105/R-49 — rooms sharing a workspace are one social unit), so this
    never decides who sees a note. What it decides is where a promoted `Event`
    lands, since `Event` is conversation-scoped and a note is not (§7.3).

    `head_revision_id` is what §6.3's compare-and-swap compares. It is on the
    note rather than derived by "latest revision" because a CAS needs one value
    both sides can name, and "whatever is newest right now" is the thing being
    guarded against.
    """

    id: str
    workspace: str
    title: str
    description: str
    uncertainty: str
    closing_condition: str
    established_in: str
    created_at: int
    head_revision_id: str
    anchor_date: int | None = None
    anchor_entity_ids: tuple[str, ...] = ()
    retired_at: int | None = None
    replaced_by: str | None = None
    # WHY it was retired — `NoteSupersedeReason`'s vocabulary, written by
    # `DreamSink.supersede`. A closed vocabulary rather than free text because
    # "how many notes did the curator merge this week" is the reading that
    # justifies the curator existing, and prose does not aggregate. `None`
    # until retired; the `retired_at is None` invariant below is what pairs
    # them.
    retire_reason: str | None = None
    revisit_at: int | None = None
    strikes: int = 0
    last_wake_revision_id: str | None = None
    event_id: str | None = None
    reminder_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.title, "title")
        _required(self.description, "description")
        _required(self.uncertainty, "statement of what is uncertain")
        _required(self.closing_condition, "closing condition")
        if len(self.title) > TITLE_MAX_CHARS:
            raise ValueError(
                f"a note title is at most {TITLE_MAX_CHARS} characters "
                f"(got {len(self.title)}) — it is injected on every tag"
            )
        if len(self.description) > DESCRIPTION_MAX_CHARS:
            raise ValueError(
                f"a note description is at most {DESCRIPTION_MAX_CHARS} "
                f"characters (got {len(self.description)}) — it is injected on "
                "every tag"
            )
        if self.anchor_date is None and not self.anchor_entity_ids:
            raise ValueError(
                "a note needs an anchor — a date, entities, or both. Without "
                "one there is no referent to look it up by later, nothing to "
                "compare for a merge, and no way to retire it mechanically"
            )
        if self.anchor_date is not None and self.anchor_date < _MIN_ANCHOR_DATE:
            raise ValueError(
                f"anchor_date is a YYYYMMDD calendar day, got {self.anchor_date}"
            )
        if self.replaced_by is not None and self.retired_at is None:
            raise ValueError(
                "a note replaced by another must also be retired: a standing "
                "note would render in the index beside its own replacement"
            )
        if self.strikes < 0:
            raise ValueError(f"strikes cannot be negative, got {self.strikes}")

    @property
    def is_standing(self) -> bool:
        """True until retired. Unlike `Fact.is_standing` this reads only
        `retired_at`, because a note may retire with no successor (see the
        module docstring)."""
        return self.retired_at is None

    @property
    def anchor_key(self) -> tuple[int | None, tuple[str, ...]]:
        """What merge eligibility compares, and **the only thing it compares**
        (§7.1).

        Entities are sorted and de-duplicated so the key is a property of *which
        entities* rather than of the order somebody happened to list them —
        without that, a merge candidate is missed for an artifact of insertion
        order.

        Two weddings differ in both components, so they are structurally
        ineligible to merge however alike their prose reads. That is the point:
        topical similarity is precisely the signal that would destroy the
        venue-to-groom mapping, so it is not the signal used.
        """
        return (self.anchor_date, tuple(sorted(set(self.anchor_entity_ids))))


@dataclass(frozen=True, slots=True)
class NoteRevision:
    """One append to a note's body. **Never an edit** (§6.1).

    `parent_revision_id` is the head the writer read, which is what makes
    §6.3's compare-and-swap possible: a revision that cannot name what it was
    written against cannot be refused when the head moved under it. `None` is
    the first revision and nothing else.

    `established_by` is load-bearing beyond provenance. §9.6's strike resets
    **only on a tag-path revision** — if the revisitor's own writes reset it,
    the loop breaker resets itself forever and is decorative. That distinction
    is unmakeable without knowing who wrote each revision, so this field is
    required rather than optional.

    Frozen because append-only has to be true of the object as well as the
    table: an editable revision makes "append a revision" one of two available
    verbs instead of the only one.
    """

    id: str
    note_id: str
    body: str
    parent_revision_id: str | None
    written_at: int
    established_by: str
    entity_ids: tuple[str, ...] = field(default_factory=tuple)
    # DEC-125/DEC-126, and `None` is a REAL state rather than an absence: this
    # table has two writers and only one of them is an adaptor. A curator
    # revision arrives as a `Proposal` and carries both; a tag-path revision has
    # no `Proposal` and no instruction digest to carry. `established_by` already
    # tells the two apart (`tag:<person_id>` versus a run id) and is required
    # for that reason, so these say only what it cannot: WHICH instruction
    # produced an adaptor's revision. See `0036_note_revision_provenance` for
    # why this is nullable where `fact_candidate`'s equivalent is not.
    instruction_sha256: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if not self.body or not self.body.strip():
            raise ValueError("a revision needs a body")
        if not self.established_by or not self.established_by.strip():
            raise ValueError(
                "a revision needs established_by: the strike reset in §9.6 "
                "distinguishes a tag-path revision from the revisitor's own, "
                "and cannot without it"
            )


@dataclass(frozen=True, slots=True)
class NoteRevisionProposal:
    """What the curator hands `DreamSink.propose` — a revision to append, named
    against the head it was written from.

    **This type exists so the note sink has something to `isinstance` against.**
    `Proposal.payload` is `object` at the port, deliberately: the runtime never
    names an adaptor's own value type, which is what keeps `FactVerdict` out of
    `nora/domain/ports.py` and makes "one runtime, many adaptors" true. The cost
    of an opaque field is that a wrong value travels a long way quietly, and the
    rule that pays it is that **the sink refuses anything else by `isinstance`
    and never duck-types** (spec §15.4). A sink that accepts whatever arrives is
    `unavailable` reported as `ok` at the storage boundary.

    It is NOT `NoteRevision`. A `NoteRevision` is a row that exists — it has an
    `id` and a `written_at`, both of which the STORE assigns. A proposal is a
    request that one be written, so it carries neither, and using the row type
    here would force the curator to mint an id the store then ignores or
    honours, and neither answer is good. `written_at` in particular belongs to
    `propose`'s `now_ms`, on the verb rather than the payload, because one tick
    writes many proposals at a single instant and a per-proposal clock invites a
    batch whose rows disagree about when the batch happened.

    `parent_revision_id` is REQUIRED here, unlike on `NoteRevision` where `None`
    means the first revision. The curator never writes a note's first revision —
    it acts ACROSS notes that already exist (§2), so it always has a head to
    name, and §6.3's compare-and-swap is the only thing standing between a
    background pass and the tag path silently overwriting each other. Typing it
    optional would make the one case that must never happen expressible.
    """

    note_id: str
    body: str
    parent_revision_id: str
    entity_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.body or not self.body.strip():
            raise ValueError("a proposed revision needs a body")
        if not self.note_id or not self.note_id.strip():
            raise ValueError("a proposed revision needs the note it revises")
        if not self.parent_revision_id or not self.parent_revision_id.strip():
            raise ValueError(
                "a proposed revision needs parent_revision_id: the curator "
                "acts across notes that already exist, so it always has a head "
                "to name, and the CAS in §6.3 is what stops a background pass "
                "and the tag path overwriting each other"
            )


class NoteSupersedeReason:
    """The `because` values `SqlNoteStore.supersede` accepts, and the shape of
    the tier's retirement vocabulary.

    A class of constants rather than an enum on the port: `DreamSink.supersede`
    types `because` as `str` on purpose, because the fact tier's reasons
    (`contradicted`) and this tier's have nothing in common, and a shared enum
    would make the port fact-shaped or note-shaped depending on who wrote it
    last. The contract suite's normalised row view keeps `because` opaque for
    the same reason.
    """

    MERGED = "merged"
    RETIRED_EXPIRED = "retired-expired"
    RETIRED_CLOSED = "retired-closed"

    ALL: tuple[str, ...] = (MERGED, RETIRED_EXPIRED, RETIRED_CLOSED)


class StaleNoteHeadError(Exception):
    """The compare-and-swap in §6.3 refused: the head this revision was written
    against is not the head any more.

    **Why a refusal and not a merge, an overwrite, or a silent no-op.** A note's
    value is the *arrangement* — the writer read one body, reasoned over it, and
    is now appending prose that only makes sense against that body. If the head
    moved (the curator merged the note mid-turn, or a parallel turn in the other
    room of the same workspace revised it), the text being appended is an answer
    to a question that has changed. Writing it on top destroys the intervening
    revision's arrangement with nothing recording that it happened; returning 0
    and carrying on reports «nothing to do» for what was actually «I could not
    do it» — R-27's `unavailable`-as-`nothing_found` failure, one layer down.
    Raising is the only one of the three the caller cannot mistake for success.

    `observed_head` is what the head actually is, so the tool layer's message can
    be «this note changed under you, read it again» rather than a bare failure.
    **`None` means the note is not there at all** *from the caller's workspace* —
    absent, retired out of existence, or in someone else's workspace, which this
    store deliberately cannot tell apart (a caller must not be able to confirm a
    note exists in a workspace it may not read). Both cases have the same remedy,
    which is why they are one exception rather than two: read it again.

    A plain `Exception` rather than `nora.domain.errors.NoraError`: `NoraError`
    carries a `FailureClass`, and how a refused write is *surfaced to the room*
    is the tool layer's decision (`note_update` reports `unavailable`), not this
    type's. Baking INFRASTRUCTURE in here would default the refusal to silence.
    """

    def __init__(self, message: str, *, observed_head: str | None) -> None:
        super().__init__(message)
        self.observed_head = observed_head
