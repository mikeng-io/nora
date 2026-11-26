"""The retained legacy replay substrate's adoption keeps its own cover.

`scripts/memory_graph_spike.py` still builds the legacy `memory_node` index
for the recorded-wake replay runner (`_legacy_replay_substrate` calls
`memory_index.rebuild` and `memory_index.adopt_worker_state`). The graph
eval arm no longer reads this substrate, but the substrate itself has not
left: these probes pin its input-identity adoption rules — byte identity,
reading identity, terminal failure and the source's wait — against the
function the replay path actually calls.

Two real databases: the SOURCE stands for the deployment's clone, its index
built and embedded through the worker's own writes (`embed_store`,
`embed_failed`) with a fake vector per input; the TARGET stands for a
sandbox, holding rows as of an earlier instant, written through the stores.
Every text and file here is synthetic.
"""

from __future__ import annotations

import time
from typing import Any

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

MODEL_ID = "synthetic-embedder.gguf@0000feedbeef"
MODEL = fingerprint(MODEL_ID)
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
    """The worker's own writes, per node keyed by text (or `sha:` + digest)."""
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
    """Per node, keyed as `_embed` keys it: vector dimension, model, input, attempts, error."""
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
async def source(clean_pg_url):
    """The clone: rows as they stand NOW, the index built and embedded."""
    engine = create_sql_engine(clean_pg_url)
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
        yield clean_pg_url, engine
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
    # Not owed: the search counts it as production does — an `empty` input
    # waits for its input, not the worker (`EmbedCounts`).
    owed = counts.pending - counts.given_up - counts.empty
    assert owed == counts.pending - 1


async def test_a_sandbox_carries_the_sources_wait_so_it_is_never_kinder(source, sandbox):
    """`rebuild` writes every sandbox node now, so a node the deployment has
    owed a vector for an hour would read as fresh (`EmbedCounts.fresh`) in
    the sandbox and answer `ok` where production answers `partial`. The
    adoption carries the source's `owed_since` for an identical input, and
    bounds any other unadopted node by its own instant."""
    now_ms = int(time.time() * 1000)
    await _write(source[1], [("the waiting line", ())], {})
    await _write(sandbox[1], [("the waiting line", ())], {})
    async with source[1].begin() as conn:
        await conn.execute(sa.update(MEMORY_NODE).where(MEMORY_NODE.c.text == "the waiting line")
                           .values(owed_since=sa.func.clock_timestamp()
                                   - sa.text("interval '1 hour'")))
    async with sandbox[1].begin() as conn:
        # As written at the instant: said now, so its own bound keeps it fresh.
        await conn.execute(sa.update(MEMORY_NODE).where(MEMORY_NODE.c.text == "the waiting line")
                           .values(at_ms=now_ms))

    async def fresh(text: str) -> int:
        async with sandbox[1].connect() as conn:
            return (await memory_index.embed_counts(
                conn, MODEL, MEMORY_NODE.c.text == text, fresh_seconds=180)).fresh

    assert await fresh("the waiting line") == 1  # positive control: fresh before
    assert await fresh("see you at seven") == 1
    await _adopt(source[1], sandbox[1])
    assert await fresh("the waiting line") == 0   # the source's wait
    assert await fresh("see you at seven") == 0   # no match: its own instant
