"""The entity-name substrate the note tier and the shared `fact_entity`
registry are built on.

**What is left here after the knowledge tier was deleted, and why.** The
knowledge (fact) tier's types — `Fact`, `FactCandidate`, `FactVerdict`,
`CandidateClaim`, the render views — went with it. Three things did not, and
each has a live consumer outside that tier:

- `FACT_TERM_CEILING_CHARS` / `FACT_PREDICATE_CEILING_CHARS` are DDL. The
  `fact`/`fact_candidate` TABLES survive the deletion (dropping them destroys
  data and is a separate operator decision), so `nora.infra.sql.schema` and
  migration 0041 still read these constants for their CHECK constraints. A
  ceiling asserted in two places with two literals is the exact defect the
  note tier shipped and the parity gate could not see.
- `normalise_name` is the ONE normalisation for an entity name. The
  `fact_entity` table is a tier-neutral name registry — an entity is a shared
  name, not knowledge-tier property — and `note_entity.entity_id` is a foreign
  key into it, so the note tier resolves through `nora.infra.sql.entities`,
  which normalises through this function and nothing else.
  The name keeps its `fact_` prefix because the TABLE does; renaming it is a
  separate pass.
- `FactEntity`, the registry's row dataclass, was deleted on 2026-09-07: it was
  constructed only by `SqlEntityStore.lookup`, whose only consumers were the
  deleted `get_knowledge`/`knowledge_entity` tools. The note tier resolves with
  `resolve`, which answers with an id.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

import unicodedata

# ---------------------------------------------------------------------------
# The surviving CEILINGS. A ceiling is DDL: it is asserted here, in the
# schema's CHECK, and in the migration, and all three read THESE constants
# rather than repeating a literal — the note tier shipped `32000` hardcoded in
# both its migration and its schema, and the parity gate could not see it
# because the two carried the same wrong number.

#: `subject_id` and `object_literal` on the surviving `fact` table.
FACT_TERM_CEILING_CHARS = 800

#: `predicate` on the surviving `fact` table.
FACT_PREDICATE_CEILING_CHARS = 200


def normalise_name(value: str) -> str:
    """The tier's ONE normalisation for an entity name (21 §6.1).

    **NFKC, strip, casefold — and nothing fuzzy.** 21 §6.1: matching is exact
    after normalisation, never fuzzy, "for the reason 16 §4.1 already measured:
    embeddings rank a literal misreading NEARER than a true alias, so no
    threshold separates them. Under-merge, per §5." Two entities wrongly kept
    apart is a missing edge — visible as a thin neighbourhood, fixable by a
    later merge. Two wrongly merged is a corrupted node that has already
    absorbed both sets of edges, and unmerging needs to know which edge came
    from where.

    NFKC because a code-switching room's IME emits full-width Latin, and
    a full-width `Big Walk` and an ASCII one are the same name written on
    two keyboards. `casefold`, not `lower`: `lower` leaves ß alone and casefold
    does not, and the whole point is that two spellings collapse.

    Internal whitespace collapses too — `house  house` and `house house` are
    one name, and the vendored matcher's only real yield on this corpus is
    exactly this (25 §4.1: the two "resolved" rows are exact-branch hits,
    `lower()` plus whitespace collapse).

    Exported from the DOMAIN because the sink, the promotion pass and any
    future resolver must all use the same one. Two normalisations is two
    definitions of identity.
    """
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


