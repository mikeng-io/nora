"""Measure the actual scoped graph port at depth three; no model or config reads.

Projected DB: GRAPH_BENCH_DATABASE_URL=... uv run python scripts/measure_memory_walk.py
  --unit UNIT --seed event:ID [--seed person:ID ...] --listed PERSON
Generated: uv run python scripts/measure_memory_walk.py --generated
Only --generated writes, into a disposable local testcontainer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from time import perf_counter

import sqlalchemy as sa

from nora.domain.memory_graph import Walk
from nora.domain.memory_index import MemoryScope
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memory_graph import SqlMemoryGraph


async def measure(url: str, args: argparse.Namespace) -> None:
    engine = create_sql_engine(url)
    try:
        if args.generated:
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
                                         asserted_at,written_by,unit,persons)
                    SELECT n||'-'||hop,'event',n::text,'same_as','event',
                           ((n+hop*hop)%10000+1)::text,'notebook',to_timestamp(1700000000),
                           'generated','generated',ARRAY['generated-person']
                    FROM generate_series(1,10000) n CROSS JOIN generate_series(1,3) hop
                """)
                )
                await conn.execute(
                    sa.text("""
                    INSERT INTO memory_node(kind,source_id,unit,origin_room,persons,text,at_ms)
                    SELECT 'message',n::text,'generated','signal:generated',
                           ARRAY['generated-person'],'Generated evidence for the event.',
                           1700000000000
                    FROM generate_series(1,10000) n
                """)
                )
                await conn.execute(
                    sa.text("""
                    INSERT INTO relation_evidence(relation_id,source_kind,source_id,span,role)
                    SELECT n||'-'||hop,'message',((n+shift)%10000+1)::text,
                           'Generated evidence','supports'
                    FROM generate_series(1,10000) n CROSS JOIN generate_series(1,3) hop
                    CROSS JOIN generate_series(1,3) shift
                """)
                )
                await conn.execute(sa.text("ANALYZE memory_node"))
                await conn.execute(sa.text("ANALYZE relation_evidence"))
                await conn.execute(sa.text("ANALYZE entity"))
                await conn.execute(sa.text("ANALYZE relation"))
        rooms = {
            item.partition("=")[0]: frozenset(item.partition("=")[2].split(","))
            for item in args.room
        }
        scope = MemoryScope(
            (args.unit,),
            tuple(rooms),
            frozenset(args.listed),
            frozenset(args.present or args.listed),
            not args.roster_complete,
            frozenset(args.presence),
            rooms,
            social_off=frozenset(args.social_off),
        )
        seeds = tuple(tuple(s.split(":", 1)) for s in args.seed)
        query = Walk(args.unit, scope.listed_here, depth=3, scope=scope)
        graph = SqlMemoryGraph(engine)
        samples = []
        statuses: dict[str, int] = {}
        for i in range(args.runs + 5):
            start = perf_counter()
            result = await graph.walk(seeds, query)
            elapsed = (perf_counter() - start) * 1000
            if result.status == "unavailable" or not result.nodes:
                raise RuntimeError("walk unavailable or empty; no latency claim")
            if i >= 5:
                samples.append(elapsed)
                statuses[result.status] = statuses.get(result.status, 0) + 1
        ordered = sorted(samples)
        p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
        print(
            json.dumps(
                {
                    "runs": len(samples),
                    "depth": 3,
                    "p95_ms": round(p95, 2),
                    "median_ms": round(ordered[len(ordered) // 2], 2),
                    "statuses": statuses,
                    "generated_entities": 10000 if args.generated else None,
                    "generated_relations": 30000 if args.generated else None,
                    "generated_evidence": 90000 if args.generated else None,
                    "generated_source_nodes": 10000 if args.generated else None,
                    "under_200_ms": p95 < 200,
                }
            )
        )
        if p95 >= 200:
            raise SystemExit(1)
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--generated", action="store_true")
    p.add_argument("--unit", default="generated")
    p.add_argument("--seed", action="append")
    p.add_argument("--listed", action="append", default=[])
    p.add_argument("--room", action="append", default=[], help="platform:id=person,person")
    p.add_argument("--present", action="append", default=[])
    p.add_argument("--presence", action="append", default=[])
    p.add_argument("--social-off", action="append", default=[])
    p.add_argument("--roster-complete", action="store_true")
    p.add_argument("--runs", type=int, default=50)
    args = p.parse_args()
    args.seed = args.seed or ["event:5000"]
    if args.runs < 1:
        p.error("--runs must be positive")
    if args.generated:
        args.listed = ["generated-person"]
        args.room = ["signal:generated=generated-person"]
        args.roster_complete = True
        from testcontainers.postgres import PostgresContainer

        from nora.infra.sql.migrations_runner import run_migrations

        with PostgresContainer("pgvector/pgvector:pg18") as pg:
            url = pg.get_connection_url().replace("postgresql+psycopg2:", "postgresql+asyncpg:")
            run_migrations(url)
            asyncio.run(measure(url, args))
    else:
        asyncio.run(measure(os.environ["GRAPH_BENCH_DATABASE_URL"], args))


if __name__ == "__main__":
    main()
