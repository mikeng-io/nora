"""The scoped walk must probe endpoint indexes, not preload a unit's graph."""

import json

import sqlalchemy as sa

from nora.domain.memory_graph import Walk
from nora.domain.memory_index import MemoryScope
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memory_graph import walk_parameters, walk_sql
from tests.contract.test_memory_graph_contract import _graph_db as _graph_db


async def test_scoped_walk_uses_endpoint_indexes(_graph_db):
    engine = create_sql_engine(_graph_db)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(
                sa.text("""
                INSERT INTO entity(id,kind,unit,name)
                SELECT n::text,'event','generated','Generated event '||n
                FROM generate_series(1,10000) n
            """)
            )
            await conn.execute(
                sa.text("""
                INSERT INTO relation(id,src_kind,src_id,rel,dst_kind,dst_id,layer,
                                     asserted_at,written_by,unit)
                SELECT 'generated-'||n,'event',n::text,'same_as','event',(n+1)::text,
                       'notebook',now(),'generated','generated'
                FROM generate_series(1,9999) n
            """)
            )
            await conn.execute(sa.text("ANALYZE entity"))
            await conn.execute(sa.text("ANALYZE relation"))
            scope = MemoryScope(
                ("generated",),
                (),
                frozenset(),
                frozenset(),
                True,
                frozenset(),
                social_off=frozenset(),
            )
            query = Walk("generated", frozenset(), depth=3, scope=scope)
            sql, params = walk_sql(query)
            params.update(walk_parameters((("event", "5000"),), query))
            plan = (
                await conn.execute(sa.text("EXPLAIN (FORMAT JSON) " + sql), params)
            ).scalar_one()
            rendered = json.dumps(plan)
            assert "idx_relation_src_walk" in rendered
            assert "idx_relation_dst_walk" in rendered
            assert "Index Scan" in rendered or "Bitmap Index Scan" in rendered
    finally:
        await engine.dispose()
