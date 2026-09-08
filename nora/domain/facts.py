"""The two DDL ceilings the deleted knowledge tier's surviving TABLES still
read.

**What is left here after the knowledge tier was deleted, and why.** The
knowledge (fact) tier's types — `Fact`, `FactCandidate`, `FactVerdict`,
`CandidateClaim`, the render views — went with it on 2026-09-07 (issue
#115). `FACT_TERM_CEILING_CHARS` / `FACT_PREDICATE_CEILING_CHARS` did not:
they are DDL. The `fact`/`fact_candidate` TABLES survive the deletion
(dropping them destroys data and is a separate operator decision), so
`nora.infra.sql.schema` and migration 0041 still read these constants for
their CHECK constraints. A ceiling asserted in two places with two literals is
the exact defect the note tier shipped and the parity gate could not see.

`normalise_name` — the one normalisation for an entity name — lived here
until 2026-09-08. Its only consumer was `nora.infra.sql.entities.
SqlEntityStore`, the `fact_entity` registry's store, which was removed with
the note tier's entity-resolution chain: once the knowledge tier stopped
minting entities the registry held eight stale rows and no writer, so the
chain could resolve nothing. `FactEntity`, the registry's row dataclass, went
a day earlier with the tools that read it.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

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
