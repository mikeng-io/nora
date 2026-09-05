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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

#: The injected index carries one line per note on EVERY tag (§5.1), so these
#: are prompt-budget decisions, not cosmetic ones. They are enforced in
#: `__post_init__` rather than in `nora.language.note_render` because a renderer
#: that truncates loses content silently, and the whole tier is built on the
#: index being a complete listing of what exists.
TITLE_MAX_CHARS = 60
DESCRIPTION_MAX_CHARS = 200
#: 27 §5.2's OPERATING value — what a writer should produce. **Not
#: configurable, deliberately.** `[notes] max_body_chars` was declared with a
#: range validator and a stanza in `config.example.toml` and NOTHING EVER READ
#: IT — enforcement was this constant, in four places, the whole time. A
#: documented knob that changes nothing is the config-file form of a declared
#: metric with no call site: it reads as a control and is a decoration.
#:
#: It was deleted rather than threaded because the cap's own argument is that
#: it is enforced in the TYPE, where a refusal is reviewable and names the cap
#: — and `__post_init__` cannot see config. Threading it would have moved the
#: refusal out to a store boundary to make a knob true, which is the tail
#: wagging the dog. Enforced in `NoteRevision.__post_init__` and both proposal
#: types.
BODY_MAX_CHARS = 8000

#: 27 §5.2's ABSOLUTE ceiling — what the table will hold. NOT configurable, and
#: four times the operating value on purpose.
#:
#: The two numbers mean different things, which is why there are two: 8000 says
#: «no writer should produce that», 32000 says «this row is corrupt». A body
#: between them means something reached the table WITHOUT passing the domain
#: type — a bug to go and find, not a row to reject when somebody reads it. A
#: CHECK at the operating value would collapse the two meanings.
#:
#: **`nora/infra/sql/schema.py` reads this constant rather than repeating the
#: number**, on `STANDING_BODY_CEILING_CHARS`' precedent and for its stated
#: reason: a change to the ceiling must not leave the table and the type
#: disagreeing silently. The gate cannot catch that on its own —
#: `test_schema_parity` compares `schema.py` against the migrated database, so
#: two literals agree with each other and with no declared intent.
#:
#: Migration `0038` keeps its own literal, deliberately: a migration is a
#: snapshot of what was applied and must not import a constant that can move
#: underneath it.
NOTE_BODY_CEILING_CHARS = 32000

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
    def written_anchor_entities(self) -> tuple[str, ...]:
        """The entity strings the WRITER typed, sorted and de-duplicated.

        **This is not what merge compares** — `anchor_key` is, and it is fed
        RESOLVED entity ids out of `note_entity`. The distinction is the whole
        of §2: 「Grant 婚禮」, 「Gabe's wedding」 and 「婚禮」 are three
        different strings and one occasion, while two unrelated notes that both
        say 「wedding」 are one string and two occasions. A key built from these
        is wrong in both directions at once.

        Kept because `note_has_an_anchor_check` reads the column and a reader
        asking «what did somebody write here» deserves an answer that is not
        the merge key.
        """
        return tuple(sorted(set(self.anchor_entity_ids)))


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
        # 27 §5.2, and the reason it is a cap at all: the body is NOT injected
        # — it is read on demand — so the exposure is the CURATOR, which reads
        # bodies per tick against a budget counted in ROWS. A row budget is
        # only a budget if a row has a bounded size; one unbounded body can
        # consume a whole tick's allowance, and the pass then reports «nothing
        # to do» about the notes it never reached.
        #
        # TWO NUMBERS, deliberately. This is the operating value (8000, roughly
        # a long page); the database CHECK is 32000. The gap is not slack — it
        # is the difference between «this writer should not do that» and «this
        # row is corrupt». A body between them means a writer bypassed this
        # type, which is a bug to find rather than a row to reject at read
        # time, and a database that refused at 8000 would make the two
        # indistinguishable.
        if len(self.body) > BODY_MAX_CHARS:
            raise ValueError(
                f"a note body is at most {BODY_MAX_CHARS} characters (got "
                f"{len(self.body)}). Bodies are read whole by the curator "
                f"against a per-tick budget counted in rows, so an unbounded "
                f"one silently eats the allowance"
            )
        if not self.established_by or not self.established_by.strip():
            raise ValueError(
                "a revision needs established_by: the strike reset in §9.6 "
                "distinguishes a tag-path revision from the revisitor's own, "
                "and cannot without it"
            )


def anchor_key(
    anchor_date: int | None, entity_ids: Sequence[str],
) -> tuple[int | None, tuple[str, ...]]:
    """What merge eligibility compares, and **the only thing it compares**
    (§7.1).

    Entities are sorted and de-duplicated so the key is a property of *which
    entities* rather than of the order somebody happened to list them — without
    that, a merge candidate is missed for an artifact of insertion order.

    Two weddings differ in both components, so they are structurally ineligible
    to merge however alike their prose reads. That is the point: topical
    similarity is precisely the signal that would destroy the venue-to-groom
    mapping, so it is not the signal used.

    **A FUNCTION over ids the caller resolved, not a property of `Note`.** It
    was a property reading `Note.anchor_entity_ids` until 2026-08-28, which
    made the key a comparison of MODEL-AUTHORED FREE TEXT — see
    `group_on_anchor_key` for why that is wrong in both directions at once.
    `Note` has no resolved-entity field on purpose: `standing` and `read` do
    not join `note_entity`, and a field that is populated on one read path and
    empty on the others is where two implementations quietly disagree.
    """
    return (anchor_date, tuple(sorted(set(entity_ids))))


def group_on_anchor_key(
    notes: Sequence[tuple[Note, str]],
    *,
    resolved_entities: Mapping[str, Sequence[str]],
    limit: int,
) -> tuple[
    tuple[tuple[tuple[Note, str], ...], ...],
    tuple[tuple[Note, ...], ...],
]:
    """The grouping itself, shared by `SqlNoteStore` and its in-memory double.

    Returns **(eligible, refused)** — groups that may merge WITH their bodies,
    and groups that collide on a date alone and therefore may not, as NOTES
    ALONE.

    **`resolved_entities` maps a note id to the entity ids `note_entity` holds
    for it, and it is the merge key's second half.** Not `Note.anchor_entity_
    ids`, which is what `note_create` stored verbatim from the model with no
    resolution and no validation. That distinction is the finding this
    signature exists for:

    - 「Grant 婚禮」, 「Gabe's wedding」 and 「婚禮」 are three raw strings and
      ONE occasion, so a raw-string key misses the collision §7.1 exists for.
    - two unrelated notes that both say 「wedding」 are one raw string and TWO
      occasions, so a raw-string key MERGES THEM — the two-weddings failure,
      entered through the guard rather than around it.

    A key wrong in both directions is not a weaker key; it is a different rule
    wearing this one's name. So eligibility joins the entity registry, and a
    note with no `note_entity` row resolves to `()` — which is what
    `no_entity_anchor` counts, and it now means «unresolved» rather than «the
    model typed nothing».

    **The consequence, stated rather than discovered later: `note_entity` has
    no writer, so merge is INERT in production today.** 27 §2 already decided
    that — populating `note_entity` is "the curator's real precondition, NOT
    its flag" — and `nora_note_curator_groups_refused_total` is the number that
    says what building the writer would unlock. The retire half is unaffected
    and is the half that answers "the notebook only grows".

    **The refused half carries no bodies, and the asymmetry is deliberate.**
    Nothing reads a refused group's body — it is counted and discarded — so a
    body there is a value with no consumer, and a value with no consumer is
    where two implementations quietly disagree. They did: `SqlNoteStore` ran
    the grouping over a placeholder-body pass and returned `''`, while the
    double returned the real text. Harmless, and therefore exactly the kind
    that survives.

    Fixed by removing the field rather than by making the two agree on it,
    because agreement has to be maintained and a type does not. Two return
    values rather than one filtered list because the refused ones are the ONLY
    evidence of what entity resolution would unlock, and a function that
    silently dropped them would make "merge does nothing" indistinguishable
    from "there is nothing to merge".

    **In the DOMAIN, and imported by both**, rather than the same rule written
    twice. This is the comparison that decides whether two notes get merged, so
    a double that grouped differently from the store would make the contract
    suite compare two definitions of "same occasion" and pass.

    It lives here rather than in either store because `lint-imports` refused
    the alternative and was right to: `nora.testing` and `nora.infra` are
    siblings, so a helper shared by both belongs in the layer beneath them —
    and "which notes are the same occasion" is a rule about notes, not about
    Postgres. `anchor_key` is one function up for the same reason.

    Ordering matches what both stores promise elsewhere: most-collided first,
    then by anchor date, and within a group by `(created_at, id)` — which is
    what makes the SURVIVOR (the first entry) stable across ticks. A retried
    merge that picked a different survivor would produce two merged notes.
    """
    if limit <= 0:
        return ((), ())
    buckets: dict[tuple[int | None, tuple[str, ...]], list[tuple[Note, str]]] = {}
    for note, body in notes:
        if note.anchor_date is None:
            continue
        key = anchor_key(
            note.anchor_date, resolved_entities.get(note.id, ()),
        )
        buckets.setdefault(key, []).append((note, body))
    collided = [(k, v) for k, v in buckets.items() if len(v) > 1]
    collided.sort(key=lambda kv: (-len(kv[1]), kv[0][0] or 0))

    # A GROUP WITH NO RESOLVED ENTITIES IS REFUSED, NOT MERGED — and counted,
    # so that refusal is a reading rather than a silence.
    #
    # An anchor is `(anchor_date, entity_set)` and BOTH halves are what make it
    # an occasion. A group whose entity half is empty is "everything that
    # happens on the 9th" — «Dinner, 9 Sept» beside «Ada's flight to Tokyo».
    # Merging those is the two-weddings failure with a different label, and it
    # is unrecoverable in the direction that matters.
    #
    # The entity half is EMPTY FOR EVERY NOTE IN PRODUCTION, because
    # `note_entity` has no writer anywhere in the system. So this branch
    # refuses every group today. 27 §2 already decided that: populating
    # `note_entity` is "the curator's real precondition, NOT its flag".
    #
    # **The alternative that was rejected**: key on `Note.anchor_entity_ids`,
    # which IS populated — with whatever free text the model handed
    # `note_create`. That makes merge fire, and fire wrongly in both
    # directions, and it makes the guard's own name a lie: `no_entity_anchor`
    # would mean «the model typed nothing» rather than «nothing resolved».
    #
    # **Relying on the model to decline is not the alternative either.** Since
    # the bodies are rendered it probably WOULD decline — and that is
    # self-attestation on a destructive operation, which is exactly what
    # keeping eligibility in code exists to avoid.
    #
    # THE REVERSAL CONDITION: this comes out when `note_entity` gains a writer
    # and entities resolve. The refused count is what says whether that is
    # worth doing, measured against the real notebook rather than estimated.
    eligible = [(k, v) for k, v in collided if k[1]]
    refused = [(k, v) for k, v in collided if not k[1]]
    return (
        tuple(tuple(v) for _, v in eligible[:limit]),
        tuple(tuple(n for n, _ in v) for _, v in refused),
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
        if len(self.body) > BODY_MAX_CHARS:
            raise ValueError(
                f"a proposed revision's body is at most {BODY_MAX_CHARS} "
                f"characters (got {len(self.body)})"
            )
        if not self.note_id or not self.note_id.strip():
            raise ValueError("a proposed revision needs the note it revises")
        if not self.parent_revision_id or not self.parent_revision_id.strip():
            raise ValueError(
                "a proposed revision needs parent_revision_id: the curator "
                "acts across notes that already exist, so it always has a head "
                "to name, and the CAS in §6.3 is what stops a background pass "
                "and the tag path overwriting each other"
            )


@dataclass(frozen=True, slots=True)
class NoteCreationProposal:
    """A note that does not exist yet — the debrief's shape, and the OTHER half
    of what `DreamSink.propose` accepts for this tier.

    **Two shapes with a discriminant, never one shape with optional fields**
    (spec §7). A single type covering create and revise would make every field
    optional, and a sink whose payload has no required field can refuse
    nothing: `isinstance` would pass on a half-built value and the store would
    discover the gap at the INSERT, as a database error on a background clock
    rather than a refusal at the boundary. Two frozen types with disjoint
    required fields is what makes the refusal possible at all.

    **Why the debrief needs its own shape rather than reusing
    `NoteRevisionProposal`.** That type's docstring says it plainly: *the
    curator never writes a note's first revision — it acts ACROSS notes that
    already exist.* The debrief's job is the opposite one. It reviews a
    finished turn, and the valuable thing it can see is an uncertainty NOBODY
    tracked, which is by definition a note that is not there. Revising an
    existing note is the tag path's job in-turn (R-55's latch) and the
    curator's across notes (§2).

    **The anchor is required, and in practice it is a DATE.** `Note`
    `__post_init__` refuses a note anchored to nothing — without a referent
    there is nothing to look it up by later, nothing to compare for a merge,
    and no way to retire it mechanically. `anchor_entity_ids` needs a populated
    entity registry, and `note_entity` is empty; a type that accepted only
    entity anchors would be a shape that cannot be satisfied in production. So
    both are offered and at least one is required, which is `Note`'s own rule
    stated one layer earlier, where the proposal can be refused instead of
    raising inside a background tick.

    **A create proposal never names a note, so it cannot collide with one — and
    the sink does NOT check whether a similar note already exists.** That is
    deliberate. Deduplication is anchor-keyed and belongs to the curator (§7.1),
    which is the only thing permitted to act across notes; a sink that
    reinterpreted a create as a revision because it found a plausible match
    would be doing cross-note reasoning at the storage boundary, silently, on
    a guess. Two notes about the same occasion is a state the curator is built
    to resolve. One note silently absorbing another's content is not
    recoverable in the direction that matters.
    """

    title: str
    description: str
    uncertainty: str
    closing_condition: str
    established_in: str
    body: str
    anchor_date: int | None = None
    anchor_entity_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for value, what in (
            (self.title, "title"),
            (self.description, "description"),
            (self.uncertainty, "uncertainty"),
            (self.closing_condition, "closing_condition"),
            (self.established_in, "the conversation it was raised in"),
            (self.body, "body"),
        ):
            if not value or not value.strip():
                raise ValueError(f"a proposed note needs {what}")
        if len(self.body) > BODY_MAX_CHARS:
            raise ValueError(
                f"a proposed note's body is at most {BODY_MAX_CHARS} "
                f"characters (got {len(self.body)})"
            )
        # THE SAME CAPS `Note` ENFORCES, mirrored here on purpose. This type's
        # docstring argues that a refusal at the boundary is reviewable while a
        # raise inside a tick is a log line nobody reads — and until 2026-08-27
        # it copied only the anchor rule, so a 61-character title constructed
        # fine and blew up at `_new_note` INSIDE `engine.begin()`, on the
        # background clock, discarding the whole batch. That is exactly the
        # failure this type exists to prevent, and the type did not implement
        # it. A debrief model emitting a slightly long title is the likely
        # case, not an exotic one.
        if len(self.title) > TITLE_MAX_CHARS:
            raise ValueError(
                f"a note title is at most {TITLE_MAX_CHARS} characters (got "
                f"{len(self.title)}) — the index is injected on EVERY tag, so "
                f"this is a prompt-budget cap and not a style rule"
            )
        if len(self.description) > DESCRIPTION_MAX_CHARS:
            raise ValueError(
                f"a note description is at most {DESCRIPTION_MAX_CHARS} "
                f"characters (got {len(self.description)})"
            )
        if self.anchor_date is not None and self.anchor_date < 10000000:
            raise ValueError(
                f"anchor_date is a YYYYMMDD calendar day, not epoch ms or a "
                f"bare year (got {self.anchor_date})"
            )
        if self.anchor_date is None and not self.anchor_entity_ids:
            raise ValueError(
                "a proposed note needs an anchor — a date, entities, or both. "
                "Without one there is no referent to look it up by later, "
                "nothing to compare for a merge, and no way to retire it "
                "mechanically. In practice this is a date: the entity registry "
                "is not populated yet"
            )


#: The prefix `nora.agent.tools` stamps on a revision it writes for a person.
#: **The one thing §9.6's strike reset turns on**, and it is a prefix rather
#: than a flag column because `established_by` already had to distinguish the
#: writers for provenance — a second column saying the same thing is a second
#: thing to keep in step.
TAG_PATH_PREFIX = "tag:"


def is_tag_path_revision(established_by: str) -> bool:
    """Whether a revision was written by a person on the tag path.

    **§9.6's trap, and the whole reason this predicate exists rather than a
    bare `head != last_wake` comparison:** the strike reset must be «head moved
    BY THE TAG PATH», not «head moved». If a background pass's own writes
    counted, a note the revisitor keeps rewriting would reset its own strike
    forever and the breaker would be decorative — a self-referential reset is
    not a reset.

    The spec states this plainly and says why: *"the mechanism reads correct
    without the qualifier and loops forever with it missing."*
    """
    return established_by.startswith(TAG_PATH_PREFIX)


def tag_path_progressed_since_wake(
    *,
    head_revision_id: str,
    last_wake_revision_id: str | None,
    chain: Mapping[str, tuple[str | None, str]],
) -> bool:
    """Whether the TAG PATH wrote anything since the last wake.

    `chain` maps a revision id to `(parent_revision_id, established_by)` for
    every revision of the note. The walk goes head → parent until it reaches
    `last_wake_revision_id`, so what it asks is «is there a tag-path revision
    in the span this wake has not seen».

    **It used to ask who wrote the CURRENT HEAD**, and that is a different
    question with a different answer on the sequence that matters:

        wake at R5 → the room revises (R6, `tag:miles`) → the curator merges or
        the debrief appends (R7, a run id)

    The head is R7, background-authored, so the old test said «no progress»,
    charged a strike, and LEFT THE WATERMARK AT R5 — so the same R6 could never
    count on any later tick either. A note the room is actively working, that a
    background pass also touches each cycle, never resets and marches to the
    strike ceiling. `merge` writes exactly such a revision, so a merged
    survivor's head is background-authored BY CONSTRUCTION.

    Both implementations agreed on the wrong answer, which is why the contract
    suite could not see it: they shared a belief, not a check.

    A `last_wake_revision_id` of `None` is a note that has never been woken, so
    the whole chain is unseen and any tag-path revision in it counts.

    The walk terminates on a revision it has already visited. An append-only
    chain cannot cycle; a walk over rows it did not write must not assume that.
    """
    seen: set[str] = set()
    current: str | None = head_revision_id
    while current is not None and current != last_wake_revision_id:
        if current in seen:
            break
        seen.add(current)
        entry = chain.get(current)
        if entry is None:
            break
        parent, established_by = entry
        if is_tag_path_revision(established_by):
            return True
        current = parent
    return False


@dataclass(frozen=True, slots=True)
class WakeOutcome:
    """What a wake did — returned so the CALLER decides what follows.

    `strikes` is the count AFTER this wake. `reset` says the head had moved on
    the tag path, so the note is making progress and the counter went back to
    zero.

    **The store does not retire at the ceiling, and that is deliberate.**
    §9.6 says a note past K strikes "stops waking and becomes eligible for
    dateless retirement" — ELIGIBLE, which is a judgement about what to do
    next, not a fact about the row. Retirement is a `supersede` with a reason
    and a `replaced_by`; hiding one inside a bookkeeping verb would make one
    call do two things, and the destructive half would be the invisible one. So
    the wake reports and the revisitor decides, which is also where K lives.
    """

    strikes: int
    reset: bool


#: `note_candidate.status` for a `kind='create'` row (migration `0049`,
#: design/dreamer-agent-loop/33-note-dreamer-schedule.md §4). `pending` is the
#: staged proposal before the deterministic gate has run (or re-run after a
#: transient failure); `created` is a proposal the gate materialized, as a new
#: note OR as a revision to an existing one sharing its anchor key — the
#: store's `status` column does not distinguish the two, because both are
#: "this occasion is now represented in the notebook", which is the property
#: `nora.language.note_candidates` needs to avoid reopening a settled
#: candidate, not which verb did it; `refused` is a proposal the gate declined
#: — a duplicate that added nothing, or a malformed candidate the domain type
#: itself rejected.
CANDIDATE_PENDING = "pending"
CANDIDATE_CREATED = "created"
CANDIDATE_REFUSED = "refused"
CANDIDATE_STATUSES: tuple[str, ...] = (
    CANDIDATE_PENDING, CANDIDATE_CREATED, CANDIDATE_REFUSED,
)


@dataclass(frozen=True, slots=True)
class NoteCreateCandidate:
    """One `kind='create'` row of `note_candidate` — a full note proposal the
    `note-candidates` adaptor (`nora.language.note_candidates`) read off a
    room's transcript, staged for a deterministic gate to decide create,
    update or refuse over. Migration `0049`; see its own docstring for the
    column shapes and the two uniqueness domains this table now holds.

    **In the SAME shape `NoteCreationProposal` requires**, deliberately: this
    row exists only because there is no note yet to build one against, and it
    is turned into exactly that proposal (or a `NoteRevisionProposal`, if the
    gate finds a standing note sharing this occasion's anchor key) once the
    gate has decided. A row that could not become one of those two proposals
    would not have been accepted into this table to begin with.

    `anchor_entity_ids` is stored SORTED and DE-DUPLICATED — never as the
    model wrote it — because it is half of `note_candidate_create_anchor_key`;
    see that index's own comment in `nora/infra/sql/schema.py`.
    """

    id: str
    workspace: str
    title: str
    description: str
    body: str
    closing_condition: str
    anchor_date: int
    anchor_entity_ids: tuple[str, ...]
    message_ids: tuple[int, ...]
    status: str
    attempts: int
    first_seen_at: int
    last_seen_at: int


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
