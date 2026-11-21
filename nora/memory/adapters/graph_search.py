"""Rebuild the searchable projection from a unit's stored graph rows."""

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.infra.sql.schema import METADATA
from nora.memory.domain.registration import SEARCHABLE_SOURCES


def _projection(name: str, fields: tuple[str, ...]) -> str:
    table = METADATA.tables[name]
    identity = "graph_id" if "graph_id" in table.c else "id"
    columns = ", ".join(f"'{field}'" for field in fields)
    return (
        f"SELECT {identity} AS graph_id, unit, "  # noqa: S608 — closed source map
        f"nora_graph_search_text(to_jsonb(source), ARRAY[{columns}]) AS text "
        f"FROM {name} AS source WHERE source.unit = :unit AND {identity} IS NOT NULL"
    )


async def rebuild_graph_search(conn: AsyncConnection, unit: str) -> None:
    """Reconcile one unit in the caller's transaction, keeping vectors for unchanged text."""
    names = ", ".join(SEARCHABLE_SOURCES)
    await conn.execute(sa.text(f"LOCK TABLE {names} IN SHARE ROW EXCLUSIVE MODE"))
    projection = " UNION ALL ".join(
        _projection(name, fields) for name, fields in SEARCHABLE_SOURCES.items()
    )
    statement = sa.text(f"""
        MERGE INTO graph_search AS indexed
        USING (SELECT * FROM ({projection}) AS source WHERE text IS NOT NULL) AS expected
        ON indexed.graph_id = expected.graph_id AND indexed.unit = expected.unit
        WHEN MATCHED AND indexed.text IS DISTINCT FROM expected.text THEN
            UPDATE SET text = expected.text, embedding = NULL, embedding_model = NULL,
                embedded_at = NULL, embed_attempts = 0, embed_error = NULL, embed_input = NULL
        WHEN NOT MATCHED BY TARGET THEN
            INSERT (graph_id, unit, text) VALUES (expected.graph_id, expected.unit, expected.text)
        WHEN NOT MATCHED BY SOURCE AND indexed.unit = :unit THEN DELETE
        """)  # noqa: S608 — table and column names come from the closed source map
    await conn.execute(statement, {"unit": unit})
