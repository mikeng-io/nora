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

    def __post_init__(self) -> None:
        if not self.body or not self.body.strip():
            raise ValueError("a revision needs a body")
        if not self.established_by or not self.established_by.strip():
            raise ValueError(
                "a revision needs established_by: the strike reset in §9.6 "
                "distinguishes a tag-path revision from the revisitor's own, "
                "and cannot without it"
            )
