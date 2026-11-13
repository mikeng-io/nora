"""Execute scoped graph reads with JIT enabled and growing unrelated history."""

import asyncio
import json
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from nora.domain.memory_graph import Walk
from nora.domain.memory_index import MemoryScope
from nora.infra.sql.memory_graph import SqlMemoryGraph
from nora.infra.sql.schema import ENTITY, MEMORY_NODE, RELATION, RELATION_EVIDENCE


def plan_nodes(node):
    yield node
    for child in node.get("Plans", ()):
        yield from plan_nodes(child)


async def test_scoped_walk_avoids_compilation_and_unrelated_history(pg_module_url, tmp_path):
    engine = create_async_engine(pg_module_url, connect_args={"server_settings": {
        "jit": "on", "jit_above_cost": "0",
    }})
    at = datetime(2026, 1, 1, tzinfo=UTC)
    edge = {"id": "generated-edge", "src_kind": "topic", "src_id": "generated-a",
                "dst_kind": "topic", "dst_id": "generated-b", "rel": "concerns",
                "layer": "notebook", "unit": "generated-unit", "persons": ["generated-person"],
                "asserted_at": at, "written_by": "generated-test"}
    scope = MemoryScope(("generated-unit",), ("chat:generated",),
                        frozenset({"generated-person"}), frozenset({"generated-person"}),
                        False, frozenset(),
                        {"chat:generated": frozenset({"generated-person"})},
                        social_off=frozenset())
    plans = []

    def explain(conn, clause, multiparams, params, options, result):
        if str(clause).lstrip().startswith("WITH RECURSIVE"):
            plans.append(conn.execute(sa.text(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(clause)
            ), params).scalar_one()[0])

    try:
        async with engine.begin() as conn:
            await conn.execute(sa.insert(ENTITY), [
                {"id": f"generated-{s}", "kind": "topic", "unit": "generated-unit", "name": s}
                for s in ("a", "b")])
            await conn.execute(sa.insert(RELATION).values(**edge))
            await conn.execute(sa.insert(MEMORY_NODE).values(
                kind="message", source_id="1", text="Generated evidence",
                unit="generated-unit", origin_room="chat:generated",
                persons=["generated-person"]))
            await conn.execute(sa.insert(RELATION_EVIDENCE).values(
                relation_id=edge["id"], source_kind="message", source_id="1",
                role="supports"))
        sa.event.listen(engine.sync_engine, "after_execute", explain)
        visits = []
        for low, high in ((0, 100), (100, 3000)):
            async with engine.begin() as conn:
                await conn.execute(sa.insert(RELATION), [
                    {**edge, "id": f"generated-noise-{i}",
                     "src_id": f"generated-source-{i}", "dst_id": f"generated-target-{i}"}
                    for i in range(low, high)])
                await conn.execute(sa.text("ANALYZE relation"))
            graph = SqlMemoryGraph(engine)
            got = await graph.walk((("topic", "generated-a"),),
                                   Walk("generated-unit", scope.listed_here, scope=scope))
            assert got.status == "ok"
            assert {e.relation.id for e in got.relations} == {edge["id"]}
            assert {n.key for n in got.nodes} == {
                ("topic", "generated-a"), ("topic", "generated-b")}
            plan = plans[-1]
            (tmp_path / f"walk-{high}.json").write_text(json.dumps(plan))
            assert not plan.get("JIT"), "short scoped reads must not compile hundreds of functions"
            visits.append(sum(
                (n["Actual Rows"] + n.get("Rows Removed by Filter", 0)) * n["Actual Loops"]
                for n in plan_nodes(plan["Plan"]) if n.get("Relation Name") == "relation"))
            print("history", high, "relation visits", visits[-1],
                  "execution ms", plan["Execution Time"])
        assert 0 < visits[-1] <= visits[0] * 2
        concurrent = await asyncio.gather(*(
            graph.walk((("topic", "generated-a"),),
                       Walk("generated-unit", scope.listed_here, scope=scope))
            for _ in range(2)))
        assert all({e.relation.id for e in result.relations} == {edge["id"]}
                   and result.status == "ok" for result in concurrent)
        denied = await graph.walk((("topic", "generated-a"),),
                                  Walk("generated-other-unit", scope.listed_here, scope=scope))
        assert denied.status != "unavailable"
        assert not denied.nodes and not denied.relations
    finally:
        sa.event.remove(engine.sync_engine, "after_execute", explain)
        await engine.dispose()
