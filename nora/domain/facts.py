"""The fact tier's types. A fact is a TRIPLE with the source that warrants it,
scoped to a workspace — never a free-text statement (which no engine can ever
traverse) and never a claim about a person (which is Honcho's axis, not this
one). See docs/superpowers/specs/2026-08-20-fact-tier-design.md.

**`FactVerdict` is the point of this module.** R-53 states in doctrine that a
source which does not mention something has not denied it; that prose shipped
and its own corpus cannot show it works (spec §9.0). So the rule is expressed
here as a SHAPE instead: `not_covered` has no reachable `supersedes`, which
makes «the page omitted it, therefore drop the fact» unrepresentable rather
than discouraged. A model under social pressure can still be wrong about which
status to return; it cannot return a status that deletes a fact on silence.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args

from .provenance import INSTRUCTION_SHA256_HEX_LEN

FactStatus = Literal["supported", "contradicted", "not_covered"]
CandidateStatus = Literal["pending", "adjudicated", "not_covered"]

# The warrant axis (2026-08-21). WHAT kind of evidence backs a fact — a page
# (`source`, today's only writer-producible case), other standing facts
# (`premises`), a repeated observation (`observation`, the room_lexeme shape
# one tier down), or somebody simply deciding something (`stated` — its
# warrant is WHO decided, not evidence). Orthogonal to `FidelityStatus` below:
# a `source`-warranted fact can still be `tentative`, and a `stated` one can
# be `proven` the moment it is decided.
WarrantKind = Literal["source", "premises", "observation", "stated"]

# The fidelity axis (2026-08-21), epistemic status — "unconfirmed / confirmed
# / retracted" — orthogonal to `superseded_at`'s CURRENCY axis ("was true
# then, isn't now"): a fact can be high-fidelity + low-currency (true but
# outdated) or low-fidelity + high-currency (fresh but unconfirmed).
# Deliberately not a float: a numeric confidence invites false precision
# nobody in this tier can calibrate. `tentative` is where an unconfirmed
# claim lands — the state the adaptor will produce whether or not the schema
# admits it.
FidelityStatus = Literal["tentative", "proven", "deprecated"]

# Why a fact was superseded — paired with `superseded_at`/`superseded_by`.
# Captured at supersession time because it cannot be reconstructed from the
# row afterwards at any price.
SupersedeReason = Literal["stale", "wrong", "merged"]

_STATUSES: frozenset[str] = frozenset(get_args(FactStatus))
_CANDIDATE_STATUSES: frozenset[str] = frozenset(get_args(CandidateStatus))
_WARRANT_KINDS: frozenset[str] = frozenset(get_args(WarrantKind))
_FIDELITY_STATUSES: frozenset[str] = frozenset(get_args(FidelityStatus))
_SUPERSEDE_REASONS: frozenset[str] = frozenset(get_args(SupersedeReason))

# The DB CHECK constraint this column maps to is
# `instruction_sha256 ~ '^[0-9a-f]{64}$'` (see nora.domain.provenance); the
# domain must refuse everything the column would, not just the wrong length.
_INSTRUCTION_SHA256_RE = re.compile(rf"^[0-9a-f]{{{INSTRUCTION_SHA256_HEX_LEN}}}$")


@dataclass(frozen=True)
class FactVerdict:
    """One adjudication of one candidate claim against one source.

    The three states are R-27's, one layer up: `supported` and `contradicted`
    are findings, `not_covered` is «I could not check» — the same outcome a
    broken lookup gets, never «there is nothing».
    """

    subject: str
    predicate: str
    object: str
    status: FactStatus
    addresses_claim: bool
    source_url: str | None = None
    source_excerpt: str | None = None
    supersedes: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _STATUSES:
            raise ValueError(
                f"status must be one of {sorted(_STATUSES)}, got {self.status!r}"
            )
        if self.status == "not_covered":
            if self.supersedes is not None:
                raise ValueError(
                    "a not_covered verdict may not supersede a fact: a source that "
                    "does not reach the question has not denied it (R-53)"
                )
            # FORBIDDEN, not merely optional — and the asymmetry with the two
            # findings above is the point. `not_covered` writes nothing, so a
            # source attached to one has no destination; what it does have is a
            # reader. «Here is the page, and it did not cover the claim» is one
            # careless sentence away from «here is the page, and it says no»,
            # which is R-53's failure wearing evidence it never earned. Silence
            # stays structurally silent.
            #
            # `nora/dreamer/objectives/fact-adjudication.md` already tells the
            # model these fields are forbidden here; until 2026-08-21 nothing
            # enforced it, so the instruction and the type disagreed — a
            # promise no shape held, in the one tier whose whole thesis is that
            # the rule belongs in a shape (found in review).
            if self.source_url is not None or self.source_excerpt is not None:
                raise ValueError(
                    "a not_covered verdict may not carry a source: it reports "
                    "that nothing reached the question, and a source attached "
                    "to that reads as evidence for a denial nobody made"
                )
            return
        if not self.source_url or not self.source_excerpt:
            raise ValueError(
                f"a {self.status} verdict needs a source url and the excerpt that "
                "carries the claim"
            )
        if self.status == "supported":
            if self.supersedes is not None:
                raise ValueError(
                    "a supported verdict may not set supersedes: a source that "
                    "agrees with a claim does not thereby remove a different fact"
                )
            return
        if self.status == "contradicted":
            if self.supersedes is None:
                raise ValueError(
                    "a contradicted verdict must name the fact it supersedes"
                )
            if not self.addresses_claim:
                raise ValueError(
                    "a contradicted verdict needs addresses_claim: a source about "
                    "something else cannot deny this claim"
                )

    @property
    def removes_nothing(self) -> bool:
        """True unless this verdict supersedes a standing fact. Silence, and
        agreement, both remove nothing."""
        return self.supersedes is None


@dataclass(frozen=True)
class FactEntity:
    """A canonical subject or object. `aliases` is load-bearing at workspace
    scope: `big walk` / `Big Walk` / 大步行 must resolve to ONE entity or the
    tier fragments into near-duplicates that can never contradict each other."""

    id: str
    workspace: str
    canonical_name: str
    created_at: int
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateClaim:
    """What the extraction adaptor produces: one claim somebody asserted, and
    the message it was asserted in.

    **NOT a `FactCandidate`** — that is the ROW, with an id, timestamps, an
    occurrence count and a status, none of which an adaptor can know. This is
    the half the model actually returns. An adaptor handed the row type would
    have to invent a uuid and two timestamps it has no clock for, purely to
    satisfy fields the store then discards, which is the
    "invent-a-value-to-satisfy-a-schema" move this codebase refuses everywhere
    else — and it would make the payload lie about where those values came
    from.

    So the candidate tier is deliberately ASYMMETRIC: a narrow claim goes in
    through `DreamSink.propose`, and the full `FactCandidate` row comes back
    out of `FactCandidateReader.pending`. The next person will reach for the
    row type on both sides; the two are genuinely different things.
    """

    subject: str
    predicate: str
    object: str
    message_id: int


@dataclass(frozen=True)
class FactCandidate:
    """An extracted claim awaiting — or having failed — adjudication.

    `status="not_covered"` is the NEGATIVE CACHE: without it the same
    unprovable claim is re-extracted and re-adjudicated every tick forever,
    which is the difference between a bounded cost and an unbounded one.

    `instruction_sha256` and `model` are both NOT NULL here for the same
    reason they are on `Fact` and unlike the four Dreamer tables (migrations
    0023/0024): those hold rows written before the columns existed and this
    table never did (migration 0027 landed the pair on an empty table). Every
    `Proposal` that reaches `DreamSink.propose` carries both as non-optional
    `str`, so a candidate whose instruction or model is unknown cannot be
    written at all — and there is no unreachable "provenance unknown" state
    for a reader to branch on.

    Both are established ONCE, by the run that first sighted the claim, and a
    re-sighting deliberately leaves them where they are — the same rule
    `established_by` and `first_seen_at` follow (`SqlFactCandidateStore.
    propose`'s upsert)."""

    id: str
    workspace: str
    subject_id: str
    predicate: str
    object: str
    first_seen_at: int
    last_seen_at: int
    occurrence_count: int
    observed_in: tuple[str, ...]
    status: CandidateStatus
    established_by: str
    instruction_sha256: str
    model: str
    last_adjudicated_at: int | None = None
    attempts: int = 0

    def __post_init__(self) -> None:
        if self.status not in _CANDIDATE_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(_CANDIDATE_STATUSES)}, "
                f"got {self.status!r}"
            )
        # Mirrors `fact_candidate_instruction_sha256_check`, the way `Fact`
        # mirrors `fact_instruction_sha256_check` — the domain type refuses
        # what the column would already refuse, which is what lets
        # `InMemoryFactCandidateStore` behave like the SQL store instead of
        # accepting rows Postgres would reject.
        if not _INSTRUCTION_SHA256_RE.fullmatch(self.instruction_sha256):
            raise ValueError(
                "instruction_sha256 must be a full 64-character lowercase hex "
                "digest; a candidate whose instruction is unknown is not "
                "attributable and is refused"
            )
        # Mirrors `fact_candidate_model_is_named_check`. `''`/`'   '` are
        # non-NULL to every query asking "which rows are attributable", so an
        # unnamed model is the false `ok` R-27 refuses, not a missing value.
        # (`Fact` carries the same CHECK in the database and does NOT mirror
        # it here — a gap noted rather than widened; this type is not the
        # place to change that one.)
        if not self.model.strip():
            raise ValueError(
                "model must be a non-empty name: a blank stamp is not a "
                "missing model, it is an unattributable row that reads as an "
                "attributable one"
            )


@dataclass(frozen=True)
class Fact:
    """An adjudicated triple with its warrant.

    `instruction_sha256` and `model` are both NOT NULL here, unlike the four
    Dreamer tables (migrations 0023/0024), because those hold rows written
    before the columns existed and this table never will. A fact whose
    instruction or model is unknown cannot be written at all.

    **Why `model` as well as the instruction hash.** «Which instruction» does
    not fully answer «what produced this row»: `[language].model` has changed
    before, and the same instruction run through a different model is a
    different act. The Dreamer is pinned to `deepseek-v4-flash` today, which is
    a config value and not a guarantee — which is exactly why it is recorded
    rather than assumed.

    **The warrant axis (2026-08-21).** `warrant_kind` names WHAT kind of
    evidence backs the row. `source_url`/`source_excerpt` moved from always
    required to required exactly when `warrant_kind == "source"` — a fact
    warranted by other facts (`premises`), a repeated observation
    (`observation`), or a decision (`stated`) has no page behind it, and
    demanding a URL for it would be demanding evidence of the wrong kind. A
    `premises`-warranted fact's premises live in `FactPremise` rows, not on
    this dataclass — `Fact` mirrors one `fact` row, and premises are a
    separate relation (`fact_premise`) precisely so "which facts depend on
    this one" is a query, not a scan (see `FactPremise`'s own docstring).
    That means THIS type cannot enforce "premises ⇒ at least one premise
    exists" any more than the database can: it is a cross-table invariant,
    unenforced until a writer exists to hold it (see the migration). `fidelity`
    is the orthogonal epistemic axis: `superseded_at` says whether a fact is
    still CURRENT, `fidelity` says how well-supported it is regardless of
    currency. Its default is **`tentative`**, not `proven` — a default is what
    a value gets when nobody thought about it, and that value must underclaim,
    never overclaim (2026-08-21 correction; see the migration's docstring for
    the full argument)."""

    id: str
    workspace: str
    subject_id: str
    predicate: str
    # An ADDITIVE, nullable resolution pointer as of 2026-08-28 (migration
    # 0038, `21-substrate-contract.md` §10). Never the object on its own —
    # `object_literal` always carries the words, so promotion RESOLVES a row
    # instead of rewriting it, and nothing downstream has to branch on which
    # half is present.
    object_entity_id: str | None
    # The object, always. NOT NULL in the schema and non-optional here: the
    # old exactly-one CHECK made an entity-object fact's literal NULL, which
    # broke the negative cache (`NULL = 'x'` is NULL, so the claim was never
    # deduped) and made two render paths fall back to printing a bare uuid
    # into the prompt.
    object_literal: str
    valid_from: int
    created_at: int
    instruction_sha256: str
    model: str
    established_by: str
    warrant_kind: WarrantKind = "source"
    source_url: str | None = None
    source_excerpt: str | None = None
    valid_to: int | None = None
    superseded_at: int | None = None
    superseded_by: str | None = None
    supersede_reason: SupersedeReason | None = None
    fidelity: FidelityStatus = "tentative"
    # `DreamSink.supersede`'s `source` argument, landed 2026-08-21 alongside
    # `SqlFactStore`. A WARRANT, not a row id — `replaced_by`/`superseded_by`
    # already carries the successor, so this is never that. Unlike
    # `supersede_reason`, it is OPTIONAL even when superseded (the port's own
    # docstring: "`because` is required, `source` is not"), so the invariant
    # below is a one-directional implication, not the biconditional the other
    # supersession columns use.
    supersede_source: str | None = None

    def __post_init__(self) -> None:
        if not _INSTRUCTION_SHA256_RE.fullmatch(self.instruction_sha256):
            raise ValueError(
                "instruction_sha256 must be a full 64-character lowercase hex "
                "digest; a fact whose instruction is unknown is not "
                "attributable and is refused"
            )
        # Not "exactly one" any more (migration 0038): the literal is the
        # object and the entity id is an optional pointer beside it. What is
        # still refused is an EMPTY literal — `""` and `"   "` are non-NULL to
        # every query asking «what does this fact say», which turns R-27's
        # `unavailable` into a false `ok`, and the column's NOT NULL cannot
        # see it.
        if not self.object_literal.strip():
            raise ValueError(
                "a fact's object_literal is the object and cannot be blank: "
                "the entity id is an additional pointer, never a replacement "
                "for the words the room used"
            )
        # The DB CHECK is `(superseded_by is null) = (superseded_at is
        # null)` — a biconditional, not an implication. `superseded_at` set
        # alone would hide the row from `is_standing` (below) with no record
        # of what replaced it; `superseded_by` set alone names a successor
        # with no record of when. Both are the unauditable merge Graphiti
        # issue #1771 warns against.
        if (self.superseded_by is None) != (self.superseded_at is None):
            raise ValueError(
                "superseded_at and superseded_by must be set together or "
                "not at all: a fact is either standing or superseded, and a "
                "superseded fact needs both the time and the successor"
            )
        if self.warrant_kind not in _WARRANT_KINDS:
            raise ValueError(
                f"warrant_kind must be one of {sorted(_WARRANT_KINDS)}, "
                f"got {self.warrant_kind!r}"
            )
        # Mirrors the DB's two separate CHECKs
        # (fact_source_url_warrant_check / fact_source_excerpt_warrant_check):
        # each column is set if and only if warrant_kind == "source" —
        # "forbidden otherwise", not merely optional, so a non-`source`
        # warrant can never carry an undefined stray URL or excerpt.
        is_source = self.warrant_kind == "source"
        if (self.source_url is not None) != is_source:
            raise ValueError(
                "source_url must be set when warrant_kind is 'source' and "
                "unset otherwise: a fact not warranted by a page cannot "
                "carry one"
            )
        if (self.source_excerpt is not None) != is_source:
            raise ValueError(
                "source_excerpt must be set when warrant_kind is 'source' "
                "and unset otherwise: a fact not warranted by a page cannot "
                "carry the excerpt that would have carried its claim"
            )
        if self.fidelity not in _FIDELITY_STATUSES:
            raise ValueError(
                f"fidelity must be one of {sorted(_FIDELITY_STATUSES)}, "
                f"got {self.fidelity!r}"
            )
        # Same biconditional shape as the superseded_at/superseded_by check
        # above, and for the same reason: the reason a fact was superseded
        # cannot be reconstructed from the row afterwards at any price.
        if (self.supersede_reason is None) != (self.superseded_at is None):
            raise ValueError(
                "supersede_reason must be set together with superseded_at "
                "and not at all otherwise"
            )
        if (
            self.supersede_reason is not None
            and self.supersede_reason not in _SUPERSEDE_REASONS
        ):
            raise ValueError(
                f"supersede_reason must be one of {sorted(_SUPERSEDE_REASONS)}, "
                f"got {self.supersede_reason!r}"
            )
        if self.supersede_source is not None and self.superseded_at is None:
            raise ValueError(
                "supersede_source may only be set on a superseded fact: a "
                "standing fact has no supersession to warrant"
            )

    @property
    def is_standing(self) -> bool:
        return self.superseded_at is None


@dataclass(frozen=True)
class FactPremise:
    """One edge of a `premises`-warranted `Fact`'s deduction: `fact_id` was
    deduced FROM `premise_fact_id`. A separate relation from `Fact` itself
    (2026-08-21, replacing an earlier `Fact.premise_ids UUID[]` column) so
    that "which facts depend on this one" — the reverse direction §13.5's
    transitive-supersession problem needs — is an indexed lookup rather than
    an array-containment scan with no useful index. Referential integrity
    that `premise_fact_id` names a fact that is not just real but STANDING
    is not enforced here or in the database; only existence is (via the DB's
    FK). The one cycle cheap to rule out — a fact cannot be its own premise —
    is enforced by `__post_init__` below, mirroring the DB's
    `fact_premise_no_self_reference_check`; longer cycles are not detected
    anywhere yet."""

    fact_id: str
    premise_fact_id: str

    def __post_init__(self) -> None:
        if self.fact_id == self.premise_fact_id:
            raise ValueError(
                "a fact cannot be its own premise: fact_id and "
                "premise_fact_id must differ"
            )
