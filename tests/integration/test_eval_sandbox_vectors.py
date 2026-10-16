"""An eval sandbox's memory index holds the source's vectors where, and only
where, the embedded input is identical (`nora.infra.sql.memory_index.
adopt_worker_state`, `nora.subject.sandbox_index`).

Two real databases: the SOURCE stands for the deployment's clone, its index
built and embedded through the worker's own writes (`embed_store`,
`embed_failed`) with a fake vector per input; the TARGET stands for a
sandbox, holding rows as of an earlier instant, written through the stores.
Every text and file here is synthetic.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.media import MediaBlob
from nora.domain.models import NormalizedMessage
from nora.infra.embedder import fingerprint
from nora.infra.sql import memory_index
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.sql.schema import EMBEDDING_DIMENSIONS, MEMORY_NODE
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
PHOTO = "ab" * 32
BLANK = "cd" * 32


def _vector(i: int) -> list[float]:
    vec = [0.0] * EMBEDDING_DIMENSIONS
    vec[i] = 1.0
    return vec


def _message(body: str, *, ts: int, blobs: tuple[MediaBlob, ...] = ()) -> NormalizedMessage:
    return NormalizedMessage(
        "signal", "g-synthetic", "group", "+10000000001", "Member A", ts, body, {},
        media_blobs=blobs,
    )


async def _write(engine: Any, rows: list[tuple[str, tuple[MediaBlob, ...]]],
                 readings: dict[str, str]) -> None:
    record = SqlRecordStore(engine)
    for n, (body, blobs) in enumerate(rows):
        await record.append(message=_message(body, ts=1000 + n, blobs=blobs), person_id="member-a")
    for sha, reading in readings.items():
        await SqlMediaObjectStore(engine).comprehend(
            sha256=sha, title=reading, description="", content="", model="m",
        )


async def _embed(engine: Any, plan: dict[str, tuple[str, Any]]) -> None:
    """The worker's own writes, per node chosen by its text (or `sha:` and
    the file's digest): `("vector", (model, i, by_bytes))` or `("failed", error)`."""
    async with engine.begin() as conn:
        items = await memory_index.embed_pick(conn, limit=100, now_ms=0)
        for item in items:
            key = f"sha:{item.sha256}" if item.kind == "media" else item.text
            if key not in plan:
                continue
            what, how = plan[key]
            if what == "vector":
                model, i, by_bytes = how
                assert await memory_index.embed_store(
                    conn, item, vector=_vector(i), model=model, now_ms=1, by_bytes=by_bytes,
                )
            else:
                for _ in range(memory_index.EMBED_MAX_ATTEMPTS if how != "empty" else 1):
                    await memory_index.embed_failed(conn, item, error=how, now_ms=1, by_bytes=False)


async def _states(engine: Any) -> dict[str, tuple]:
    """Per node (keyed as `_embed` keys it): its vector's first set
    dimension, model, recorded input, attempts and error."""
    node = MEMORY_NODE.c
    async with engine.connect() as conn:
        rows = await conn.execute(sa.select(
            node.kind, node.text, node.sha256, sa.cast(node.embedding, sa.Text),
            node.embedding_model, node.embed_input, node.embed_attempts, node.embed_error,
        ).where(node.kind.in_(("message", "media"))))
        out = {}
        for kind, text, sha, vec, model, inp, attempts, error in rows:
            dims = None if vec is None else [float(x) for x in vec.strip("[]").split(",")]
            first = None if dims is None else dims.index(1.0)
            out[f"sha:{sha}" if kind == "media" else text] = (first, model, inp, attempts, error)
        return out


@pytest.fixture
async def source(pg_url):
    """The clone: rows as they stand NOW, the index built and embedded."""
    engine = create_sql_engine(pg_url)
    await _write(engine, [
        ("dinner on friday at the noodle place", ()),
        ("see you at eight", ()),          # edited after the instant (was seven)
        ("bring the board game", ()),
        ("", (MediaBlob(sha256=PHOTO, media_type="image/png", size=10),)),
        ("", (MediaBlob(sha256=BLANK, media_type="application/octet-stream", size=10),)),
    ], {PHOTO: "a later reading of the photo"})
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


@pytest.fixture
async def sandbox(source, pg_template):
    """The sandbox: the same rooms as of an earlier instant."""
    async with sandbox_database(source[0], template=pg_template) as url:
        engine = create_sql_engine(url)
        await _write(engine, [
            ("dinner on friday at the noodle place", ()),
            ("see you at seven", ()),
            ("bring the board game", ()),
            ("", (MediaBlob(sha256=PHOTO, media_type="image/png", size=10),)),
            ("", (MediaBlob(sha256=BLANK, media_type="application/octet-stream", size=10),)),
        ], {PHOTO: "the first reading of the photo"})
        try:
            yield url, engine
        finally:
            await engine.dispose()


async def _adopt(source_engine: Any, target_engine: Any, model: str = MODEL) -> dict[str, tuple]:
    async with source_engine.connect() as src, target_engine.begin() as dst:
        await memory_index.adopt_worker_state(src, dst, model=model)
    return await _states(target_engine)


async def test_an_identical_text_adopts_its_vector_and_a_changed_one_does_not(source, sandbox):
    states = await _adopt(source[1], sandbox[1])
    # Identical text: the vector, its model and what it was made from.
    assert states["dinner on friday at the noodle place"] == (1, MODEL, "text", 0, None)
    assert states["bring the board game"] == (3, MODEL, "text", 0, None)
    # Edited after the instant: the source's vector is the NEW text's.
    assert states["see you at seven"] == (None, None, None, 0, None)


async def test_another_models_vector_is_never_adopted(source, sandbox):
    states = await _adopt(source[1], sandbox[1], model=OTHER)
    assert states["dinner on friday at the noodle place"] == (None, None, None, 0, None)
    assert states[f"sha:{PHOTO}"] == (None, None, None, 0, None)


async def test_an_image_adopts_by_its_bytes_whatever_its_reading_says(source, sandbox):
    states = await _adopt(source[1], sandbox[1])
    # Embedded from its bytes: a different reading at the instant changes
    # nothing the vector was made from.
    assert states[f"sha:{PHOTO}"] == (4, MODEL, "bytes", 0, None)


async def test_a_reading_vector_moves_only_with_its_reading(source, sandbox):
    """The photo embedded from its READING instead: the sandbox's reading at
    the instant is another text, so it gets nothing."""
    async with source[1].begin() as conn:
        await memory_index.clear_embeddings(conn)
    async with source[1].begin() as conn:
        [item] = [i for i in await memory_index.embed_pick(conn, limit=100, now_ms=0)
                  if i.sha256 == PHOTO]
        await memory_index.embed_store(
            conn, item, vector=_vector(5), model=MODEL, now_ms=1, by_bytes=False,
        )
    states = await _adopt(source[1], sandbox[1])
    assert states[f"sha:{PHOTO}"] == (None, None, None, 0, None)


async def test_a_failure_the_worker_will_not_retry_is_adopted_for_the_same_file(source, sandbox):
    states = await _adopt(source[1], sandbox[1])
    assert states[f"sha:{BLANK}"] == (None, None, "text", 1, "empty")
    async with sandbox[1].connect() as conn:
        counts = await memory_index.embed_counts(conn, MODEL)
    # Not owed: the search counts it as production does.
    assert Coverage.of(counts).owed == counts.pending - 1


async def test_a_sandbox_is_indexed_and_read_back(source, sandbox):
    """`index_sandbox` projects the sandbox with production's rebuild, adopts,
    and reads the coverage back. One of the four nodes it owes a vector was
    edited after the instant, so it holds 3 of 4 (0.75) where the source holds
    every one: under the floor, refused, never graded."""
    with pytest.raises(GraphIndexRefusedError, match="under its floor"):
        await index_sandbox(sandbox[0], source_url=source[0], model_id=MODEL_ID)
    built = Coverage.of(await _counts(sandbox[1]))
    assert (built.embedded, built.owed, built.empty) == (3, 1, 1)
    # The same sandbox once its rows match the source's: at the floor's right side.
    async with sandbox[1].begin() as conn:
        await conn.execute(sa.text(
            "UPDATE messages SET body = 'see you at eight' WHERE body = 'see you at seven'"
        ))
    built = await index_sandbox(sandbox[0], source_url=source[0], model_id=MODEL_ID)
    assert (built.coverage.embedded, built.coverage.owed) == (4, 0)
    assert "coverage 1.000" in built.line()


async def _counts(engine: Any) -> memory_index.EmbedCounts:
    async with engine.connect() as conn:
        return await memory_index.embed_counts(conn, MODEL)


async def test_a_source_without_this_models_vectors_is_refused(source):
    with pytest.raises(GraphIndexRefusedError, match="no vector of"):
        await source_coverage(source[0], "never-embedded.gguf@1")
    assert (await source_coverage(source[0], MODEL_ID)).ratio == 1.0  # positive control


async def test_a_source_with_an_incomplete_backfill_is_refused(source):
    async with source[1].begin() as conn:
        await memory_index.clear_embeddings(conn)
    async with source[1].begin() as conn:
        [item] = [i for i in await memory_index.embed_pick(conn, limit=100, now_ms=0)
                  if i.text == "see you at eight"]
        await memory_index.embed_store(
            conn, item, vector=_vector(2), model=MODEL, now_ms=1, by_bytes=False,
        )
    with pytest.raises(GraphIndexRefusedError, match="backfill is incomplete"):
        await source_coverage(source[0], MODEL_ID)


async def test_a_source_older_than_the_dense_channel_is_refused(pg_admin):
    """A clone with no index at all (refreshed before 0106): refused, never a
    lexical run in its place."""
    name = "t_novec_" + uuid.uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = (f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
           f"@{pg_admin.host}:{pg_admin.port}/{name}")
    try:
        with pytest.raises(GraphIndexRefusedError, match="predates migration 0106"):
            await source_coverage(url, MODEL_ID)
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_the_floor_is_relative_to_the_source():
    source = Coverage(embedded=990, owed=10, given_up=0, empty=0)
    refuse_low_coverage(Coverage(embedded=95, owed=5, given_up=0, empty=0), source)
    with pytest.raises(GraphIndexRefusedError, match="under its floor"):
        refuse_low_coverage(Coverage(embedded=93, owed=7, given_up=0, empty=0), source)
    assert COVERAGE_FLOOR == 0.95
    # Nothing owed is no gap.
    refuse_low_coverage(Coverage(embedded=0, owed=0, given_up=0, empty=3), source)
