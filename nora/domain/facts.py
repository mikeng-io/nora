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

_STATUSES: frozenset[str] = frozenset(get_args(FactStatus))
_CANDIDATE_STATUSES: frozenset[str] = frozenset(get_args(CandidateStatus))

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
class FactCandidate:
    """An extracted claim awaiting — or having failed — adjudication.

    `status="not_covered"` is the NEGATIVE CACHE: without it the same
    unprovable claim is re-extracted and re-adjudicated every tick forever,
    which is the difference between a bounded cost and an unbounded one."""

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
    last_adjudicated_at: int | None = None
    attempts: int = 0

    def __post_init__(self) -> None:
        if self.status not in _CANDIDATE_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(_CANDIDATE_STATUSES)}, "
                f"got {self.status!r}"
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
    rather than assumed."""

    id: str
    workspace: str
    subject_id: str
    predicate: str
    object_entity_id: str | None
    object_literal: str | None
    source_url: str
    source_excerpt: str
    valid_from: int
    created_at: int
    instruction_sha256: str
    model: str
    established_by: str
    valid_to: int | None = None
    superseded_at: int | None = None
    superseded_by: str | None = None

    def __post_init__(self) -> None:
        if not _INSTRUCTION_SHA256_RE.fullmatch(self.instruction_sha256):
            raise ValueError(
                "instruction_sha256 must be a full 64-character lowercase hex "
                "digest; a fact whose instruction is unknown is not "
                "attributable and is refused"
            )
        has_entity = self.object_entity_id is not None
        has_literal = self.object_literal is not None
        if has_entity == has_literal:
            raise ValueError(
                "a fact needs exactly one object: an entity id or a literal, "
                "never both and never neither"
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

    @property
    def is_standing(self) -> bool:
        return self.superseded_at is None
