"""The spike's generated questions use the real, scoped WALK_SQL contract."""

from dataclasses import replace

import sqlalchemy as sa

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memory_graph import SqlMemoryGraph
from scripts.memory_graph_spike import scope_edges, sql_answer
from tests.contract.test_memory_graph_contract import PERSON, REL, load
from tests.contract.test_memory_graph_contract import _graph_db as _graph_db


async def test_question_sql_cannot_cross_unit(_graph_db):
    engine = create_sql_engine(_graph_db)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("TRUNCATE relation_evidence, relation, entity CASCADE"))
        await load(SqlMemoryGraph(engine), replace(REL, persons=("p1",)))
        question = {"unit": "u1", "persons": ["p1"], "seed": list(PERSON.key)}
        assert await sql_answer(engine, question) == ["event:e1", "person:p1"]
        assert await sql_answer(engine, {**question, "unit": "foreign-generated"}) == []
        assert await sql_answer(engine, {**question, "persons": []}) == []
        edges = [{"unit": "u1", "persons": ["p1"]}]
        assert scope_edges(edges, question) == edges
        assert scope_edges(edges, {**question, "unit": "foreign-generated"}) == []
        assert scope_edges(edges, {**question, "persons": []}) == []
    finally:
        await engine.dispose()


async def test_age_container_comparison_when_explicitly_available(_graph_db, tmp_path, monkeypatch):
    """Needs the isolated AGE container; never discovers or contacts production."""
    import os
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import pytest

    from scripts.memory_graph_spike import age_comparison

    url = os.environ.get("NORA_SPIKE_AGE_TEST_URL")
    if not url:
        pytest.skip("requires AGE 1.8.0/PG18 container via NORA_SPIKE_AGE_TEST_URL")
    engine = create_sql_engine(_graph_db)
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("TRUNCATE relation_evidence, relation, entity CASCADE"))
        await load(SqlMemoryGraph(engine))

        async def write_query(model, instruction, payload):
            assert (tmp_path / "questions-manifest.json").exists()
            if "structured path" in instruction:
                return '{"relations":["attended"]}'
            return '{"path":"(a)-[:attended*0..3]-(b)"}'

        judges = SimpleNamespace(
            cfg=SimpleNamespace(llm=SimpleNamespace(model="generated-model")),
            call=AsyncMock(side_effect=write_query),
        )
        result = await age_comparison(engine, url, judges, tmp_path, 1)
        assert result["status"] == "PASS"
        assert result["correctness"]["sql"]["rate"] == 1
        assert result["correctness"]["cypher"]["rate"] == 1
    finally:
        await engine.dispose()
