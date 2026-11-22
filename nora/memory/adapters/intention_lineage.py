"""Follow intention revisions to the opening of each selected lineage."""

import sqlalchemy as sa

from nora.infra.sql.cognitive_graph_schema import INTENTION


def intention_lineages(unit: str, heads: sa.Select) -> sa.CTE:
    """Return each selected head and its ancestors within the unit."""
    i = INTENTION.c
    lineage = heads.cte("intention_lineage", recursive=True)
    return lineage.union_all(
        sa.select(lineage.c.head, i.replaces, i.asserted_at).where(
            i.unit == unit, i.id == lineage.c.replaces,
        )
    )
