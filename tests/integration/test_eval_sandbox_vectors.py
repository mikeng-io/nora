"""An eval sandbox's `graph_search` index adopts the source's worker state
where, and only where, the indexed text is identical.

`graph_search` embeds text only (`embed_input = 'text'`), so exact text is the
whole input identity. Two real databases stand for the deployment's clone and
one sandbox; every row is synthetic.
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa

from nora.infra.embedder import fingerprint
from nora.infra.sql import graph_search_embed
from nora.infra.sql.cognitive_graph_schema import CLAIM, GRAPH_SEARCH
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.embed_schedule import EMBED_MAX_ATTEMPTS
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import EMBEDDING_DIMENSIONS
from nora.subject.sandbox import sandbox_database
from nora.subject.sandbox_index import (
    COVERAGE_FLOOR,
    Coverage,
    GraphIndexRefusedError,
    index_sandbox,
    refuse_low_coverage,
    source_coverage,
)

MODEL_ID = "synthetic-embedder.gguf@0000feedbeef"
MODEL = fingerprint(MODEL_ID)
OTHER = fingerprint("another-embedder.gguf@0000deadbeef")
UNIT = "u"


def _vector(i: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIMENSIONS
    vec[i] = 1.0
    return vec


def _claim(row_id: str, text: str) -> dict:
    return {
        "id": row_id, "unit": UNIT, "kind": "claim", "basis": "inferred",
        "reasoning": "deduced", "confidence": "medium", "state": "live",
        "exposure": "internal", "writer": "generated", "part": "fact",
        "content": {"text": text},
    }


async def _write(engine: Any, rows: dict[str, str]) -> None:
    writer = SqlCognitiveGraph(engine)
    for row_id, text in rows.items():
        await writer.create(CLAIM, _claim(row_id, text))


async def _embed(engine: Any, plan: dict[str, tuple[str, Any]]) -> None:
    """The worker's own writes, keyed by indexed text."""
    async with engine.begin() as conn:
        items = await graph_search_embed.pick(conn, limit=100, now_ms=0)
        assert items  # positive control: the fixture owes vectors
        for item in items:
            if item.text not in plan:
                continue
            what, how = plan[item.text]
            if what == "vector":
                model, index = how
                assert await graph_search_embed.store(
                    conn, item, vector=_vector(index), model=model, now_ms=1,
                )
            elif what == "failed":
                error, attempts = how
                for _ in range(attempts):
                    await graph_search_embed.failed(
                        conn, item, error=error, now_ms=123, counts=True,
                    )


async def _states(engine: Any) -> dict[str, tuple]:
    row = GRAPH_SEARCH.c
    async with engine.connect() as conn:
        rows = await conn.execute(sa.select(
            row.text, sa.cast(row.embedding, sa.Text), row.embedding_model,
            row.embedded_at, row.embed_attempts, row.embed_error, row.embed_input,
        ))
    out = {}
    for text, vector, model, embedded_at, attempts, error, embed_input in rows:
        dims = None if vector is None else [float(x) for x in vector.strip("[]").split(",")]
        first = None if dims is None else dims.index(1.0)
        out[text] = (first, model, embedded_at, attempts, error, embed_input)
    return out


@pytest.fixture
async def source(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    await _write(engine, {
        "c-dinner": "dinner on friday at the noodle place",
        "c-time": "see you at eight",
        "c-game": "bring the board game",
        "c-failing": "a generated input the worker cannot embed",
    })
    await _embed(engine, {
        "dinner on friday at the noodle place": ("vector", (MODEL, 1)),
        "see you at eight": ("vector", (MODEL, 2)),
        "bring the board game": ("vector", (MODEL, 3)),
        "a generated input the worker cannot embed": (
            "failed", ("rejected:generated", EMBED_MAX_ATTEMPTS),
        ),
    })
    try:
        yield clean_pg_url, engine
    finally:
        await engine.dispose()


@pytest.fixture
async def sandbox(source, pg_template):
    async with sandbox_database(source[0], template=pg_template) as url:
        engine = create_sql_engine(url)
        await _write(engine, {
            "t-dinner": "dinner on friday at the noodle place",
            "t-time": "see you at seven",
            "t-game": "bring the board game",
            "t-failing": "a generated input the worker cannot embed",
        })
        try:
            yield url, engine
        finally:
            await engine.dispose()


async def test_an_identical_text_adopts_its_vector_and_a_changed_one_does_not(source, sandbox):
    built = await index_sandbox(
        sandbox[0], source_url=source[0], model_id=MODEL_ID, require_vectors=False,
    )
    states = await _states(sandbox[1])
    assert states["dinner on friday at the noodle place"][:2] == (1, MODEL)
    assert states["bring the board game"][:2] == (3, MODEL)
    assert states["see you at seven"][:2] == (None, None)
    assert states["a generated input the worker cannot embed"][3:] == (
        EMBED_MAX_ATTEMPTS, "rejected:generated", "text",
    )
    assert built.coverage.embedded == 2 and built.coverage.owed == 1
    assert built.coverage.given_up == 1


async def test_another_models_vector_is_never_adopted(source, sandbox):
    async with source[1].begin() as conn:
        await conn.execute(sa.update(GRAPH_SEARCH).where(
            GRAPH_SEARCH.c.text == "bring the board game",
        ).values(embedding_model=OTHER))
    await index_sandbox(sandbox[0], source_url=source[0], model_id=MODEL_ID,
                        require_vectors=False)
    assert (await _states(sandbox[1]))["bring the board game"][:2] == (None, None)
    assert (await _states(sandbox[1]))["dinner on friday at the noodle place"][:2] == (1, MODEL)


async def test_a_pending_inputs_backoff_is_adopted_for_the_same_text(source, sandbox):
    async with source[1].begin() as conn:
        await conn.execute(sa.update(GRAPH_SEARCH).where(
            GRAPH_SEARCH.c.text == "see you at eight",
        ).values(embedding=None, embedding_model=None, embedded_at=123,
                 embed_attempts=3, embed_error="unavailable:generated", embed_input="text"))
    # Make the sandbox text identical; the state follows the input, not the id.
    async with sandbox[1].begin() as conn:
        await conn.execute(sa.update(CLAIM).where(CLAIM.c.id == "t-time").values(
            content={"text": "see you at eight"},
        ))
    await index_sandbox(sandbox[0], source_url=source[0], model_id=MODEL_ID,
                        require_vectors=False)
    assert (await _states(sandbox[1]))["see you at eight"][2:] == (
        123, 3, "unavailable:generated", "text",
    )


async def test_a_sandbox_is_indexed_and_read_back(source, pg_template):
    async with sandbox_database(source[0], template=pg_template) as url:
        engine = create_sql_engine(url)
        try:
            await _write(engine, {
                "x-dinner": "dinner on friday at the noodle place",
                "x-time": "see you at eight",
                "x-game": "bring the board game",
            })
            built = await index_sandbox(url, source_url=source[0], model_id=MODEL_ID)
        finally:
            await engine.dispose()
    assert built.coverage == Coverage(embedded=3, owed=0, given_up=0)
    assert built.source.ratio == 1.0
    assert "coverage 1.000" in built.line()


async def test_a_source_without_this_models_vectors_is_refused(clean_pg_url, pg_template):
    engine = create_sql_engine(clean_pg_url)
    try:
        await _write(engine, {"c-one": "generated unembedded text"})
        with pytest.raises(GraphIndexRefusedError, match="no graph-search vector"):
            await source_coverage(clean_pg_url, MODEL_ID)
        measured = await source_coverage(clean_pg_url, MODEL_ID, require_vectors=False)
        assert measured.embedded == 0 and measured.owed == 1  # positive control
    finally:
        await engine.dispose()


async def test_a_source_with_an_incomplete_backfill_is_refused(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        await _write(engine, {
            "c-one": "generated embedded text", "c-two": "generated owed text",
        })
        await _embed(engine, {"generated embedded text": ("vector", (MODEL, 1))})
        with pytest.raises(GraphIndexRefusedError, match="backfill is incomplete"):
            await source_coverage(clean_pg_url, MODEL_ID)
        measured = await source_coverage(clean_pg_url, MODEL_ID, require_vectors=False)
        assert measured.ratio == 0.5  # positive control
    finally:
        await engine.dispose()


@pytest.mark.fresh_database
async def test_a_source_older_than_the_graph_index_is_refused(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.begin() as conn:
            assert await conn.scalar(sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = 'graph_search'"
            )) == 1  # positive control: the fixture starts migrated
            await conn.execute(sa.text("DROP TABLE graph_search"))
        with pytest.raises(
            GraphIndexRefusedError, match="predates migration 0175_graph_search",
        ):
            await source_coverage(clean_pg_url, MODEL_ID, require_vectors=False)
    finally:
        await engine.dispose()


def test_the_floor_is_relative_to_the_source():
    source_cov = Coverage(embedded=19, owed=1, given_up=0)
    passing = Coverage(embedded=18, owed=1, given_up=0)
    failing = Coverage(embedded=17, owed=2, given_up=0)
    refuse_low_coverage(passing, source_cov)
    with pytest.raises(GraphIndexRefusedError, match="under its floor"):
        refuse_low_coverage(failing, source_cov)
    assert COVERAGE_FLOOR == 0.95
