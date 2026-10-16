"""The two DDL ceilings the `fact` and `fact_candidate` tables read.

`FACT_TERM_CEILING_CHARS` / `FACT_PREDICATE_CEILING_CHARS` are DDL. The
`fact`/`fact_candidate` tables remain though the knowledge tier's types are
gone, so `nora.infra.sql.schema` and migration 0041 read these constants for
their CHECK constraints. A ceiling asserted in two places with two literals is
a defect the parity gate cannot see, so every site reads the constant.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# The CEILINGS. A ceiling is DDL: it is asserted here, in the schema's CHECK,
# and in the migration, and all three read THESE constants rather than
# repeating a literal. Two copies of the same wrong literal pass the parity
# gate.

#: `subject_id` and `object_literal` on the `fact` table.
FACT_TERM_CEILING_CHARS = 800

#: `predicate` on the `fact` table.
FACT_PREDICATE_CEILING_CHARS = 200
