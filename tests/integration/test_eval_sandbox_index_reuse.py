"""A `graph_search` index built once and applied to the next sandbox seeded
the same way equals the index a build there would make.

The cache key carries the caller's declared key and the measured digest of the
sandbox's searchable source rows; the applied image is then checked against
those rows exactly. Real databases throughout.
"""

from __future__ import annotations

import dataclasses
from contextlib import AsyncExitStack
from typing import Any

import pytest
import sqlalchemy as sa

from nora.infra.sql.cognitive_graph_schema import GRAPH_SEARCH
from nora.infra.sql.engine import create_sql_engine
from nora.subject.sandbox import sandbox_database
from nora.subject.sandbox_index import (
    GraphIndexRefusedError,
    IndexCache,
    image_digest,
    index_image,
    index_sandbox,
)
from tests.integration.test_eval_sandbox_vectors import MODEL, MODEL_ID, _embed, _write

ROWS = {
    "c-dinner": "dinner on friday at the noodle place",
    "c-time": "see you at eight",
    "c-game": "bring the board game",
}
KEY = ("signal", "g-synthetic", 2_000)


@pytest.fixture
async def source(clean_pg_url):
    engine = create_sql_engine(clean_pg_url)
    await _write(engine, ROWS)
    await _embed(engine, {
        "dinner on friday at the noodle place": ("vector", (MODEL, 1)),
        "see you at eight": ("vector", (MODEL, 2)),
        "bring the board game": ("vector", (MODEL, 3)),
    })
    try:
        yield clean_pg_url, engine
    finally:
        await engine.dispose()


async def _sandbox(
    stack: AsyncExitStack, source_url: str, template: str, rows: dict[str, str] | None = None,
) -> tuple[str, Any]:
    url = await stack.enter_async_context(sandbox_database(source_url, template=template))
    engine = create_sql_engine(url)
    stack.push_async_callback(engine.dispose)
    await _write(engine, ROWS if rows is None else rows)
    return url, engine


async def _digest(engine: Any) -> str:
    async with engine.connect() as conn:
        return image_digest(await index_image(conn))


async def _counts(engine: Any) -> tuple[int, int]:
    row = GRAPH_SEARCH.c
    async with engine.connect() as conn:
        return (
            await conn.scalar(sa.select(sa.func.count()).select_from(GRAPH_SEARCH)),
            await conn.scalar(sa.select(sa.func.count(row.embedding))),
        )


async def test_a_reused_index_equals_the_one_a_build_makes(source, pg_template):
    """Rows, worker state and coverage match exactly; only the cache path differs."""
    cache = IndexCache()
    async with AsyncExitStack() as stack:
        first = await _sandbox(stack, source[0], pg_template)
        reused = await _sandbox(stack, source[0], pg_template)
        built = await _sandbox(stack, source[0], pg_template)
        a = await index_sandbox(first[0], source_url=source[0], model_id=MODEL_ID,
                                cache=cache, key=KEY)
        b = await index_sandbox(reused[0], source_url=source[0], model_id=MODEL_ID,
                                cache=cache, key=KEY)
        c = await index_sandbox(built[0], source_url=source[0], model_id=MODEL_ID)
        assert not a.reused and b.reused and not c.reused
        assert await _digest(reused[1]) == await _digest(built[1])
        assert await _counts(reused[1]) == await _counts(built[1]) == (3, 3)
        assert b.coverage == c.coverage


async def test_a_sandbox_seeded_otherwise_is_built_not_reused(source, pg_template):
    cache = IndexCache()
    other_rows = {**ROWS, "c-extra": "a generated extra searchable claim"}
    async with AsyncExitStack() as stack:
        first = await _sandbox(stack, source[0], pg_template)
        other = await _sandbox(stack, source[0], pg_template, rows=other_rows)
        await index_sandbox(first[0], source_url=source[0], model_id=MODEL_ID,
                            cache=cache, key=KEY)
        with pytest.raises(GraphIndexRefusedError, match="under its floor"):
            await index_sandbox(other[0], source_url=source[0], model_id=MODEL_ID,
                                cache=cache, key=KEY)
        [built] = cache._entries.values()
        assert built.image.rows  # positive control: the first build was cached
        assert all(row[0] != "c-extra" for row in built.image.rows)


async def test_another_declared_key_is_built_not_reused(source, pg_template):
    cache = IndexCache()
    async with AsyncExitStack() as stack:
        first = await _sandbox(stack, source[0], pg_template)
        second = await _sandbox(stack, source[0], pg_template)
        await index_sandbox(first[0], source_url=source[0], model_id=MODEL_ID,
                            cache=cache, key=KEY)
        b = await index_sandbox(second[0], source_url=source[0], model_id=MODEL_ID,
                                cache=cache, key=(*KEY, "another"))
        assert not b.reused
        assert len(cache._entries) == 2  # positive control: both builds are present


async def test_a_reused_index_still_reads_its_coverage_back(source, pg_template):
    cache = IndexCache()
    async with AsyncExitStack() as stack:
        first = await _sandbox(stack, source[0], pg_template)
        second = await _sandbox(stack, source[0], pg_template)
        await index_sandbox(first[0], source_url=source[0], model_id=MODEL_ID,
                            cache=cache, key=KEY)
        reused = await index_sandbox(second[0], source_url=source[0], model_id=MODEL_ID,
                                     cache=cache, key=KEY)
        assert reused.reused
        assert reused.coverage.embedded == 3 and reused.coverage.owed == 0
        assert "reused" in reused.line()


async def test_an_applied_index_that_does_not_fit_the_sandbox_is_refused(source, pg_template):
    cache = IndexCache()
    async with AsyncExitStack() as stack:
        first = await _sandbox(stack, source[0], pg_template)
        second = await _sandbox(stack, source[0], pg_template)
        await index_sandbox(first[0], source_url=source[0], model_id=MODEL_ID,
                            cache=cache, key=KEY)
        [(key, built)] = cache._entries.items()
        cache._entries[key] = dataclasses.replace(
            built, image=dataclasses.replace(built.image, rows=built.image.rows[:-1]),
        )
        with pytest.raises(GraphIndexRefusedError, match="reuse must equal a build"):
            await index_sandbox(second[0], source_url=source[0], model_id=MODEL_ID,
                                cache=cache, key=KEY)
        # Rollback restores the rows the seeding triggers wrote, with no adopted vector.
        assert await _counts(second[1]) == (3, 0)


async def test_a_build_refused_under_its_floor_is_not_kept(source, pg_template):
    cache = IndexCache()
    rows = dict(ROWS)
    rows.update({f"c-extra-{n}": f"generated unembedded extra {n}" for n in range(10)})
    async with AsyncExitStack() as stack:
        low = await _sandbox(stack, source[0], pg_template, rows=rows)
        with pytest.raises(GraphIndexRefusedError, match="under its floor"):
            await index_sandbox(low[0], source_url=source[0], model_id=MODEL_ID,
                                cache=cache, key=KEY)
        assert not cache._entries
        assert await _counts(low[1]) == (13, 3)  # positive control: the build did happen


def test_the_cache_keeps_the_most_recent_keys():
    cache = IndexCache(size=2)
    for key in ("a", "b", "c"):
        cache.put(key, key)  # type: ignore[arg-type]
    assert cache.get("a") is None and cache.get("c") == "c"
