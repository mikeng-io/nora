"""An eval sandbox's index built once and applied to the next sandbox seeded
the same way equals the index a build on that sandbox would have made
(`nora.subject.sandbox_index.IndexCache`, `memory_index.apply_index_image`).

Real databases: the SOURCE (the deployment's clone) from
`test_eval_sandbox_vectors`, and several sandboxes seeded through the stores,
so live projection writes their index as it writes production's. Every text
and file here is synthetic.
"""

from __future__ import annotations

import dataclasses
from contextlib import AsyncExitStack
from typing import Any

import pytest
import sqlalchemy as sa

from nora.domain.media import MediaBlob
from nora.infra.sql import memory_index
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import MEMORY_EDGE, MEMORY_NODE
from nora.subject.sandbox import sandbox_database
from nora.subject.sandbox_index import GraphIndexRefusedError, IndexCache, index_sandbox
from tests.integration.test_eval_sandbox_vectors import (
    BLANK,
    MODEL,
    MODEL_ID,
    PHOTO,
    _embed,
    _write,
)

#: The source's rows as they stood: every input identical, so a sandbox of
#: them holds every vector the source does and passes its floor.
ROWS = [
    ("dinner on friday at the noodle place", ()),
    ("see you at eight", ()),
    ("bring the board game", ()),
    ("", (MediaBlob(sha256=PHOTO, media_type="image/png", size=10),)),
    ("", (MediaBlob(sha256=BLANK, media_type="application/octet-stream", size=10),)),
]
READINGS = {PHOTO: "a later reading of the photo"}
KEY = ("signal", "g-synthetic", 2_000)


@pytest.fixture
async def source(pg_url):
    """The clone, its index built and every input embedded but the empty file."""
    engine = create_sql_engine(pg_url)
    await _write(engine, ROWS, READINGS)
    await memory_index.rebuild(engine)
    await _embed(engine, {
        "dinner on friday at the noodle place": ("vector", (MODEL, 1, False)),
        "see you at eight": ("vector", (MODEL, 2, False)),
        "bring the board game": ("vector", (MODEL, 3, False)),
        f"sha:{PHOTO}": ("vector", (MODEL, 4, True)),
        f"sha:{BLANK}": ("failed", "empty"),
    })
    try:
        yield pg_url, engine
    finally:
        await engine.dispose()


async def _sandbox(stack: AsyncExitStack, source_url: str, template: str,
                   rows: list[tuple[str, tuple[MediaBlob, ...]]] = ROWS) -> tuple[str, Any]:
    url = await stack.enter_async_context(sandbox_database(source_url, template=template))
    engine = create_sql_engine(url)
    stack.push_async_callback(engine.dispose)
    await _write(engine, rows, READINGS)
    return url, engine


async def _image(engine: Any) -> memory_index.IndexImage:
    async with engine.connect() as conn:
        return await memory_index.index_image(conn)


async def _digest(engine: Any) -> str:
    return memory_index.image_digest(await _image(engine))


async def _counts(engine: Any) -> tuple[int, int, int]:
    async with engine.connect() as conn:
        return (
            (await conn.execute(sa.select(sa.func.count()).select_from(MEMORY_NODE))).scalar_one(),
            (await conn.execute(sa.select(sa.func.count()).select_from(MEMORY_EDGE))).scalar_one(),
            (await conn.execute(sa.select(sa.func.count(MEMORY_NODE.c.embedding)))).scalar_one(),
        )


async def _owed(engine: Any) -> dict[tuple[str, str], Any]:
    """Of the nodes the worker still owes a vector (the ones `EmbedCounts.
    fresh` reads), when each became owed."""
    node = MEMORY_NODE.c
    async with engine.connect() as conn:
        return {(r.kind, r.source_id): r.owed_since for r in await conn.execute(
            sa.select(node.kind, node.source_id, node.owed_since).where(
                node.embedding.is_(None), node.embed_attempts == 0, node.text != "",
                node.state == "live",
            )
        )}


async def _build(source_engine: Any, url: str) -> None:
    """A per-draw build, as `index_sandbox` runs it."""
    target = create_sql_engine(url)
    try:
        await memory_index.rebuild(target)
        async with source_engine.connect() as src, target.begin() as dst:
            await memory_index.adopt_worker_state(src, dst, model=MODEL)
    finally:
        await target.dispose()


async def test_an_applied_image_owes_what_a_build_owes_and_since_when(source, pg_template):
    """With a node still owed a vector (edited after the instant, so nothing
    is adopted for it), an image applied to a second sandbox equals a build
    on it, and the wait the search reads (`owed_since`, `EmbedCounts.fresh`)
    is the node's own instant in both, never when the copy ran."""
    edited = [(("see you at seven" if body == "see you at eight" else body), blobs)
              for body, blobs in ROWS]
    async with AsyncExitStack() as stack:
        first = await _sandbox(stack, source[0], pg_template, rows=edited)
        applied = await _sandbox(stack, source[0], pg_template, rows=edited)
        built = await _sandbox(stack, source[0], pg_template, rows=edited)
        await _build(source[1], first[0])
        image = await _image(first[1])
        async with source[1].connect() as src, applied[1].begin() as dst:
            await memory_index.apply_index_image(src, dst, image, model=MODEL)
        await _build(source[1], built[0])

        assert await _digest(applied[1]) == await _digest(built[1])
        owed = await _owed(built[1])
        assert owed  # the edited line, at least: the comparison is not vacuous
        assert await _owed(applied[1]) == owed
        for engine in (applied[1], built[1]):
            async with engine.connect() as conn:
                counts = await memory_index.embed_counts(conn, MODEL, fresh_seconds=3600)
            assert counts.fresh == 0


async def test_a_reused_index_equals_the_one_a_build_makes(source, pg_template):
    """Three sandboxes seeded alike: the first built into the cache, the
    second applied from it, the third built with no cache at all. The second
    and third hold the same index: nodes, edges, vectors, the worker's
    bookkeeping, the queue and the state (`memory_index.image_digest`)."""
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

        assert (a.reused, b.reused, c.reused) == (False, True, False)
        assert "reused in" in b.line() and "built in" in c.line()
        assert b.coverage == c.coverage
        counts = await _counts(built[1])
        assert counts[0] > 0 and counts[1] > 0 and counts[2] > 0
        assert await _counts(reused[1]) == counts
        assert await _digest(reused[1]) == await _digest(built[1])
        assert await _digest(first[1]) == await _digest(built[1])


async def test_a_sandbox_seeded_otherwise_is_built_not_reused(source, pg_template):
    """One more row than the first sandbox: its seeded index differs, so the
    key misses and it is built, and its index is not the first's."""
    cache = IndexCache()
    async with AsyncExitStack() as stack:
        first = await _sandbox(stack, source[0], pg_template)
        other = await _sandbox(stack, source[0], pg_template,
                               rows=[*ROWS, ("bring the board game", ())])
        await index_sandbox(first[0], source_url=source[0], model_id=MODEL_ID,
                            cache=cache, key=KEY)
        b = await index_sandbox(other[0], source_url=source[0], model_id=MODEL_ID,
                                cache=cache, key=KEY)
        assert not b.reused
        assert await _digest(other[1]) != await _digest(first[1])


async def test_another_declared_key_is_built_not_reused(source, pg_template):
    cache = IndexCache()
    async with AsyncExitStack() as stack:
        first = await _sandbox(stack, source[0], pg_template)
        second = await _sandbox(stack, source[0], pg_template)
        await index_sandbox(first[0], source_url=source[0], model_id=MODEL_ID,
                            cache=cache, key=KEY)
        b = await index_sandbox(second[0], source_url=source[0], model_id=MODEL_ID,
                                cache=cache, key=("signal", "g-synthetic", 3_000))
        assert not b.reused


def _only_entry(cache: IndexCache) -> tuple[Any, Any]:
    [(key, built)] = list(cache._entries.items())
    return key, built


async def test_a_reused_index_still_reads_its_coverage_back(source, pg_template):
    """The cached image stripped of its vectors: applied, the sandbox holds
    none, and the per-draw read-back refuses it under the floor. A reuse
    never skips the coverage check."""
    cache = IndexCache()
    async with AsyncExitStack() as stack:
        first = await _sandbox(stack, source[0], pg_template)
        second = await _sandbox(stack, source[0], pg_template)
        await index_sandbox(first[0], source_url=source[0], model_id=MODEL_ID,
                            cache=cache, key=KEY)
        key, built = _only_entry(cache)
        bare = tuple((*r[:11], None, None, 0, None, None, None) for r in built.image.nodes)
        cache.put(key, dataclasses.replace(
            built, image=dataclasses.replace(built.image, nodes=bare)))
        with pytest.raises(GraphIndexRefusedError, match="under its floor"):
            await index_sandbox(second[0], source_url=source[0], model_id=MODEL_ID,
                                cache=cache, key=KEY)
        async with second[1].connect() as conn:
            assert (await memory_index.embed_counts(conn, MODEL)).embedded == 0


async def test_an_applied_index_that_does_not_fit_the_sandbox_is_refused(source, pg_template):
    """The cached image names a message this sandbox does not hold: the dry
    rebuild after the apply would drop it, so the draw is refused rather than
    searched on a node with no source."""
    cache = IndexCache()
    async with AsyncExitStack() as stack:
        first = await _sandbox(stack, source[0], pg_template)
        second = await _sandbox(stack, source[0], pg_template)
        await index_sandbox(first[0], source_url=source[0], model_id=MODEL_ID,
                            cache=cache, key=KEY)
        key, built = _only_entry(cache)
        [message] = [r for r in built.image.nodes if r[0] == "message"][:1]
        ghost = (message[0], "999999", *message[2:])
        cache.put(key, dataclasses.replace(built, image=dataclasses.replace(
            built.image, nodes=(*built.image.nodes, ghost))))
        with pytest.raises(GraphIndexRefusedError, match="does not fit"):
            await index_sandbox(second[0], source_url=source[0], model_id=MODEL_ID,
                                cache=cache, key=KEY)


async def test_a_build_refused_under_its_floor_is_not_kept(source, pg_template):
    cache = IndexCache()
    async with AsyncExitStack() as stack:
        edited = [(("see you at seven" if body == "see you at eight" else body), blobs)
                  for body, blobs in ROWS]
        low = await _sandbox(stack, source[0], pg_template, rows=edited)
        with pytest.raises(GraphIndexRefusedError, match="under its floor"):
            await index_sandbox(low[0], source_url=source[0], model_id=MODEL_ID,
                                cache=cache, key=KEY)
        assert not cache._entries


def test_the_cache_keeps_the_most_recent_keys():
    cache = IndexCache(size=2)
    for k in ("a", "b", "c"):
        cache.put(k, k)  # type: ignore[arg-type]
    assert cache.get("a") is None and cache.get("c") == "c"
