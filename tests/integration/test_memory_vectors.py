"""The memory index's vectors, against Postgres and a fake llama-server
(`tests/unit/test_embedder.py`'s `FakeLlamaServer`, which refuses what the real
server refuses).

- the upsert clears a vector when what the node is embedded from changes (a
  text, or an image's bytes) and keeps it otherwise;
- the worker fills NULL vectors and records the model's fingerprint, and
  never touches a hub or a lexical-only kind;
- an embedder error leaves the node NULL, counted and backed off, never
  retried on the next tick;
- a node holding another model's vector reads as unembedded and is
  re-embedded;
- the images throttle leaves images for later and never holds up a text;
- the HNSW index and the worker's partial index are usable by the planner;
- `clear_embeddings` clears every vector, or only another model's.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import pytest
import sqlalchemy as sa
from aiohttp.test_utils import TestServer
from sqlalchemy.dialects import postgresql

from nora.domain.media import MediaBlob
from nora.domain.memory_index import HUB_KINDS
from nora.domain.models import NormalizedMessage
from nora.infra.embedder import LlamaEmbedder, fingerprint, model_file
from nora.infra.sql import memory_index
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.sql.memory_embed import MemoryEmbedWorker
from nora.infra.sql.schema import MEMORY_NODE
from nora.obs.metrics import Metrics
from nora.testing.fakes import FakeBlobStore
from tests.unit.test_embedder import FakeLlamaServer, png_bytes

pytestmark = pytest.mark.asyncio

MODEL_ID = "model-a.gguf@0000"
MODEL = fingerprint(MODEL_ID)


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def llama() -> AsyncIterator[tuple[FakeLlamaServer, str]]:
    fake = FakeLlamaServer()
    server = TestServer(fake.app())
    await server.start_server()
    try:
        yield fake, str(server.make_url("")).rstrip("/")
    finally:
        await server.close()


def _message(body: str, *, ts: int = 1000, blobs: tuple[MediaBlob, ...] = ()) -> NormalizedMessage:
    return NormalizedMessage(
        "signal", "g1", "group", "+10000000001", "Member A", ts, body, {}, media_blobs=blobs,
    )


def worker(
    engine: Any, url: str, *, blobs: Any = None, model_id: str = MODEL_ID, batch: int = 16,
    images_per_hour: int = 100, metrics: Any = None, clock: Any = None,
) -> MemoryEmbedWorker:
    return MemoryEmbedWorker(
        engine, LlamaEmbedder(url), blobs, model=fingerprint(model_id),
        model_file=model_file(model_id), batch=batch,
        images_per_hour=images_per_hour, metrics=metrics, clock=clock or Clock(),
    )


async def _nodes(engine: Any) -> dict[tuple[str, str], Any]:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(MEMORY_NODE))).all()
    return {(r.kind, r.source_id): r for r in rows}


async def test_an_edit_clears_the_vector_and_an_unchanged_reprojection_keeps_it(engine, llama):
    _fake, url = llama
    store = SqlRecordStore(engine)
    metrics = Metrics()
    mid = await store.append(message=_message("the lantern festival plan"))
    key = ("message", str(mid))

    assert await worker(engine, url, metrics=metrics).run_once() == 1
    node = (await _nodes(engine))[key]
    assert node.embedding is not None and len(list(node.embedding)) == 2048
    assert (node.embedding_model, node.embed_attempts, node.embed_error) == (MODEL, 0, None)
    assert metrics.get("nora_memory_embed_total", outcome="ok") == 1
    first = list(node.embedding)

    # A re-projection that does not change the text keeps the vector.
    await store.set_person_id(row_id=mid, person_id="member-a")
    node = (await _nodes(engine))[key]
    assert node.embedding is not None and node.embedding_model == MODEL

    # An edit changes what the node is embedded from: the vector goes.
    await store.append_edit(message_id=mid, message=_message("the lantern plan, moved", ts=2000))
    node = (await _nodes(engine))[key]
    assert (node.embedding, node.embedding_model, node.embedded_at) == (None, None, None)

    assert await worker(engine, url).run_once() == 1
    node = (await _nodes(engine))[key]
    assert node.embedding is not None and list(node.embedding) != first

    # Hubs never get a vector; the worker never picked one.
    for (kind, _), row in (await _nodes(engine)).items():
        if kind in HUB_KINDS:
            assert row.embedding is None and row.embed_attempts == 0


async def test_a_wait_is_owed_from_the_last_input_change_never_from_a_reprojection(engine):
    """`owed_since` restarts only when the input a vector is made from
    changes. A re-projection of the same text (a later write to the row)
    keeps the wait, so a node waiting since long ago never reads as fresh
    again; an edit owes a new vector from now."""
    store = SqlRecordStore(engine)
    mid = await store.append(message=_message("the lantern festival plan"))
    key = ("message", str(mid))
    where = (MEMORY_NODE.c.kind == "message", MEMORY_NODE.c.source_id == str(mid))

    async def fresh() -> int:
        async with engine.connect() as conn:
            return (await memory_index.embed_counts(
                conn, MODEL, *where, fresh_seconds=180)).fresh

    assert await fresh() == 1
    async with engine.begin() as conn:
        await conn.execute(sa.update(MEMORY_NODE).where(*where).values(
            owed_since=sa.func.clock_timestamp() - sa.text("interval '1 hour'"),
            projected_at=sa.func.clock_timestamp() - sa.text("interval '1 hour'"),
        ))
    before = (await _nodes(engine))[key]

    # The same text re-projected: projected_at moves, the wait does not.
    await store.set_person_id(row_id=mid, person_id="member-a")
    after = (await _nodes(engine))[key]
    assert after.projected_at > before.projected_at
    assert after.owed_since == before.owed_since
    assert await fresh() == 0

    # An edit changes the input: a new vector is owed from now.
    await store.append_edit(message_id=mid, message=_message("the lantern plan, moved", ts=2000))
    assert (await _nodes(engine))[key].owed_since > before.owed_since
    assert await fresh() == 1


async def test_an_image_keeps_its_vector_when_its_reading_lands_and_a_video_does_not(
    engine, llama,
):
    fake, url = llama
    blobs = FakeBlobStore()
    image = await blobs.put(data=png_bytes())
    video = await blobs.put(data=b"\x00\x00\x00\x18ftypmp42")
    store = SqlRecordStore(engine)
    media = SqlMediaObjectStore(engine)
    mid = await store.append(message=_message("look", blobs=(
        MediaBlob(sha256=image, media_type="image/png", size=10),
        MediaBlob(sha256=video, media_type="video/mp4", size=10),
    )))
    img_key, vid_key = ("media", f"{mid}:{image}"), ("media", f"{mid}:{video}")
    clock = Clock()

    await worker(engine, url, blobs=blobs, clock=clock).run_once()
    nodes = await _nodes(engine)
    assert nodes[img_key].embedding is not None, "an image is embedded from its bytes"
    assert any("multimodal_data" in c for c in fake.accepted)
    # A video with no reading yet has nothing to embed: counted, backed off.
    assert nodes[vid_key].embedding is None
    assert (nodes[vid_key].embed_attempts, nodes[vid_key].embed_error) == (1, "empty")

    for sha in (image, video):
        assert await media.comprehend(
            sha256=sha, title="Lantern", description="A red lantern.", content="", model="m",
        ) is True
    nodes = await _nodes(engine)
    assert nodes[img_key].embedding is not None, "a reading does not change an image's bytes"
    assert nodes[vid_key].embed_attempts == 0, "the video's input changed: it is fresh again"

    await worker(engine, url, blobs=blobs, clock=clock).run_once()
    assert (await _nodes(engine))[vid_key].embedding is not None

    # The message's own re-projection copies a changed reading in (a queued
    # `reproject_media`, or a rebuild): the same rule holds through the upsert.
    async with engine.begin() as conn:
        await conn.execute(sa.text("UPDATE media_objects SET content = 'FESTIVAL'"))
        await memory_index.project(conn, "message", mid)
    nodes = await _nodes(engine)
    assert nodes[img_key].text.endswith("FESTIVAL") and nodes[vid_key].text.endswith("FESTIVAL")
    assert nodes[img_key].embedding is not None
    assert nodes[vid_key].embedding is None


async def test_an_embedder_error_leaves_the_node_null_counted_and_backed_off(
    engine, llama, caplog,
):
    fake, url = llama
    store = SqlRecordStore(engine)
    first = await store.append(message=_message("a private sentence one", ts=1000))
    await store.append(message=_message("a private sentence two", ts=1100))
    metrics, clock = Metrics(), Clock()
    w = worker(engine, url, metrics=metrics, clock=clock)

    fake.status, fake.healthy = 503, False
    with caplog.at_level(logging.DEBUG):
        assert await w.run_once() == 1, "an unavailable embedder ends the tick"
    assert fake.calls == 1
    assert metrics.get("nora_memory_embed_total", outcome="unavailable") == 1
    nodes = await _nodes(engine)
    failed = [r for (k, _), r in nodes.items() if k == "message" and r.embed_attempts]
    assert len(failed) == 1 and failed[0].embedding is None
    assert failed[0].embed_error.startswith("unavailable:")
    assert "private" not in caplog.text and "private" not in failed[0].embed_error

    # The failed node waits out its backoff; the next tick tries the other.
    assert await w.run_once() == 1
    assert fake.calls == 2
    assert await w.run_once() == 0, "both backed off: no call, no hot loop"
    assert fake.calls == 2

    # A refused input is the node's own: counted, and the batch goes on.
    fake.status, fake.healthy = 400, True
    clock.now += 3600
    assert await w.run_once() == 2
    assert metrics.get("nora_memory_embed_total", outcome="rejected") == 2

    fake.status = 200
    assert await w.run_once() == 0, "refused twice: the backoff has grown"
    clock.now += 7 * 3600
    assert await w.run_once() == 2
    row = (await _nodes(engine))[("message", str(first))]
    assert row.embedding is not None and (row.embed_attempts, row.embed_error) == (0, None)


async def test_a_node_of_another_model_reads_as_unembedded_and_is_re_embedded(engine, llama):
    """The worker's first tick clears every other model's vector, so the
    ordinary pick re-embeds it, images included."""
    fake, url = llama
    store = SqlRecordStore(engine)
    mid = await store.append(message=_message("the lantern festival plan"))
    fake.model_path = "/models/model-b.gguf"
    await worker(engine, url, model_id="model-b.gguf@1111").run_once()
    fake.model_path = "/models/model-a.gguf"

    async with engine.connect() as conn:
        counts = await memory_index.embed_counts(conn, MODEL)
    assert (counts.embedded, counts.pending) == (0, 1)

    assert await worker(engine, url).run_once() == 1
    row = (await _nodes(engine))[("message", str(mid))]
    assert row.embedding_model == MODEL
    async with engine.connect() as conn:
        assert await memory_index.embed_counts(conn, MODEL) == (1, 0, 0, 0, 0, 0)


async def test_the_image_throttle_leaves_images_for_later_and_never_holds_up_a_text(
    engine, llama,
):
    fake, url = llama
    blobs = FakeBlobStore()
    store = SqlRecordStore(engine)
    shas = [await blobs.put(data=png_bytes((i * 40, 0, 0))) for i in range(3)]
    for i, sha in enumerate(shas):
        await store.append(message=_message(
            "", ts=1000 + i, blobs=(MediaBlob(sha256=sha, media_type="image/png", size=10),),
        ))
    text = await store.append(message=_message("dinner saturday?", ts=2000))
    clock = Clock()
    w = worker(engine, url, blobs=blobs, images_per_hour=1, clock=clock)

    await w.run_once()
    nodes = await _nodes(engine)
    images = [r for (k, _), r in nodes.items() if k == "media"]
    assert sum(r.embedding is not None for r in images) == 1
    assert all(r.embed_attempts == 0 for r in images), "a throttled image is not a failure"
    assert nodes[("message", str(text))].embedding is not None
    # A message with no words has nothing to embed and is never picked.
    empty = [r for (k, _), r in nodes.items() if k == "message" and r.text == ""]
    assert empty and all(r.embed_attempts == 0 and r.embedding is None for r in empty)

    calls = fake.calls
    assert await w.run_once() == 0, "spent: attachments are left out of the pick"
    assert fake.calls == calls
    clock.now += 3601
    await w.run_once()
    images = [r for (k, _), r in (await _nodes(engine)).items() if k == "media"]
    assert sum(r.embedding is not None for r in images) == 2


async def test_a_rebuild_keeps_a_vector_whose_input_is_unchanged(engine, llama):
    _fake, url = llama
    store = SqlRecordStore(engine)
    mid = await store.append(message=_message("the lantern festival plan"))
    await worker(engine, url).run_once()

    await memory_index.rebuild(engine)

    assert (await _nodes(engine))[("message", str(mid))].embedding_model == MODEL


async def test_clearing_takes_every_vector_or_only_another_models(engine, llama):
    _fake, url = llama
    store = SqlRecordStore(engine)
    for i in range(3):
        await store.append(message=_message(f"plan number {i}", ts=1000 + i))
    await worker(engine, url).run_once()
    async with engine.begin() as conn:
        # One vector is another model's (as if `embedder_model` changed).
        await conn.execute(sa.update(MEMORY_NODE).where(
            MEMORY_NODE.c.id == sa.select(sa.func.min(MEMORY_NODE.c.id)).where(
                MEMORY_NODE.c.embedding.isnot(None),
            ).scalar_subquery(),
        ).values(embedding_model=fingerprint("model-b@1111")))

    async with engine.begin() as conn:
        assert await memory_index.clear_embeddings(conn, keep_model=MODEL) == 1
    async with engine.connect() as conn:
        assert await memory_index.embed_counts(conn, MODEL) == (2, 1, 0, 0, 0, 0)
    async with engine.begin() as conn:
        assert await memory_index.clear_embeddings(conn) == 2
    async with engine.connect() as conn:
        assert await memory_index.embed_counts(conn, MODEL) == (0, 3, 0, 0, 0, 0)


async def test_the_planner_can_use_the_hnsw_index_and_the_pick_index(engine, llama):
    _fake, url = llama
    store = SqlRecordStore(engine)
    for i in range(20):
        await store.append(message=_message(f"plan number {i}", ts=1000 + i))
    await worker(engine, url, batch=10).run_once()
    query = "[" + ",".join(["0.1"] * 256) + "]"
    async with engine.connect() as conn:
        # Tiny tables favour a scan; switch it off to ask whether the index
        # CAN serve each query, which is the property the migration owns.
        await conn.execute(sa.text("SET enable_seqscan = off"))
        dense = "\n".join((await conn.execute(sa.text(
            "EXPLAIN SELECT id FROM memory_node "
            "ORDER BY (subvector(embedding, 1, 256)::halfvec(256)) <=> "
            "CAST(:q AS halfvec(256)) LIMIT 50"
        ), {"q": query})).scalars())
        # The worker's own query, as the code builds it.
        stmt = memory_index._pick_statement(
            limit=16, now_ms=0, media=True,
        ).compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        pick = "\n".join((await conn.exec_driver_sql(f"EXPLAIN {stmt}")).scalars())
    assert "idx_memory_node_embedding_256" in dense
    assert "idx_memory_node_unembedded" in pick
    # The index serves the pick's order: no sort step on top of it.
    assert "Sort" not in pick, pick


async def test_an_image_embedded_from_its_reading_loses_the_vector_when_the_reading_changes(
    engine, llama,
):
    """An image whose bytes Nora no longer holds is embedded from its reading,
    and `embed_input` records it, so a new reading clears it; an image
    embedded from its bytes keeps its vector through the same change."""
    _fake, url = llama
    blobs = FakeBlobStore()
    held = await blobs.put(data=png_bytes())
    gone = "ef" * 32  # in media_json, never in the blob store
    store = SqlRecordStore(engine)
    media = SqlMediaObjectStore(engine)
    for sha in (held, gone):
        assert await media.comprehend(
            sha256=sha, title="Lantern", description="A red lantern.", content="", model="m",
        ) is True
    mid = await store.append(message=_message("look", blobs=(
        MediaBlob(sha256=held, media_type="image/png", size=10),
        MediaBlob(sha256=gone, media_type="image/jpeg", size=10),
    )))
    await worker(engine, url, blobs=blobs).run_once()
    nodes = await _nodes(engine)
    held_key, gone_key = ("media", f"{mid}:{held}"), ("media", f"{mid}:{gone}")
    assert (nodes[held_key].embed_input, nodes[gone_key].embed_input) == ("bytes", "text")
    assert nodes[gone_key].embedding is not None

    for sha in (held, gone):
        assert await media.comprehend(
            sha256=sha, title="Lantern", description="A blue lantern.", content="", model="m",
        ) is True
    nodes = await _nodes(engine)
    assert nodes[held_key].embedding is not None
    assert nodes[gone_key].embedding is None and nodes[gone_key].embed_input is None


async def test_an_unexpected_failure_is_recorded_and_a_node_is_given_up_after_the_cap(
    engine, llama, monkeypatch,
):
    """Any exception one node raises is recorded on that node, never escaping
    the tick, and after `EMBED_MAX_ATTEMPTS` the node is not picked again,
    counted `given_up`."""
    _fake, url = llama
    store = SqlRecordStore(engine)
    bad = await store.append(message=_message("a node that always breaks", ts=1000))
    good = await store.append(message=_message("a node that embeds", ts=1100))
    real = LlamaEmbedder.embed_document

    async def _breaks(self: Any, text: str) -> Any:
        if "breaks" in text:
            raise KeyError("boom")
        return await real(self, text)

    monkeypatch.setattr(LlamaEmbedder, "embed_document", _breaks)
    metrics, clock = Metrics(), Clock()
    w = worker(engine, url, metrics=metrics, clock=clock)

    assert await w.run_once() == 2, "the failure did not stop the batch"
    nodes = await _nodes(engine)
    assert nodes[("message", str(good))].embedding is not None
    row = nodes[("message", str(bad))]
    assert (row.embed_attempts, row.embed_error) == (1, "error:KeyError")
    assert metrics.get("nora_memory_embed_total", outcome="error") == 1

    for _ in range(memory_index.EMBED_MAX_ATTEMPTS - 1):
        clock.now += 7 * 3600
        assert await w.run_once() == 1
    assert metrics.get("nora_memory_embed_total", outcome="given_up") == 1
    clock.now += 7 * 3600
    assert await w.run_once() == 0, "given up: never picked again"
    async with engine.connect() as conn:
        counts = await memory_index.embed_counts(conn, MODEL)
    assert (counts.given_up, counts.failing) == (1, 1)

    # A change to its input frees it.
    await store.append_edit(message_id=bad, message=_message("now it embeds", ts=2000))
    assert await w.run_once() == 1
    assert (await _nodes(engine))[("message", str(bad))].embedding is not None


async def test_a_poisoned_image_is_one_nodes_rejection_not_a_wedge(engine, llama):
    """A crafted file never reaches the server and never stops the batch."""
    fake, url = llama
    blobs = FakeBlobStore()
    poison = await blobs.put(data=b"GIF89a\x01\x00\x01\x00\x80\x00\x00garbage")
    store = SqlRecordStore(engine)
    mid = await store.append(message=_message("look", blobs=(
        MediaBlob(sha256=poison, media_type="image/gif", size=10),
    )))
    metrics = Metrics()
    assert await worker(engine, url, blobs=blobs, metrics=metrics).run_once() == 2
    row = (await _nodes(engine))[("media", f"{mid}:{poison}")]
    assert row.embed_attempts == 1 and row.embed_error.startswith("rejected:undecodable")
    assert metrics.get("nora_memory_embed_total", outcome="rejected") == 1
    assert all("multimodal_data" not in c for c in fake.accepted)


async def test_the_worker_embeds_nothing_while_the_server_serves_another_model(engine, llama):
    fake, url = llama
    store = SqlRecordStore(engine)
    await store.append(message=_message("the lantern festival plan"))
    fake.model_path = "/models/some-other-model.gguf"
    w = worker(engine, url)
    assert await w.run_once() == 0
    assert fake.calls == 0
    fake.model_path = "/models/model-a.gguf"
    assert await w.run_once() == 1


async def test_a_text_cut_to_fit_is_stored_and_counted_by_kind(engine, llama):
    """No chunking yet; a text embedded from its head only is counted,
    in addition to `ok`, so the counter says when chunking starts to matter."""
    _fake, url = llama
    store = SqlRecordStore(engine)
    long_id = await store.append(message=_message("x" * 9000, ts=1000))
    await store.append(message=_message("a short message", ts=1100))
    metrics = Metrics()
    assert await worker(engine, url, metrics=metrics).run_once() == 2
    assert (await _nodes(engine))[("message", str(long_id))].embedding is not None
    assert metrics.get("nora_memory_embed_total", outcome="ok") == 2
    assert metrics.get("nora_memory_embed_truncated_total", kind="message") == 1


async def test_an_unavailable_embedder_never_gives_a_node_up(engine, llama):
    """Only a failure of the input itself counts toward the give-up cap. An
    outage backs the node off, never past the cap, so it is embedded when
    the server is back."""
    fake, url = llama
    store = SqlRecordStore(engine)
    mid = await store.append(message=_message("a message during an outage", ts=1000))
    metrics, clock = Metrics(), Clock()
    w = worker(engine, url, metrics=metrics, clock=clock)
    assert await w.run_once() == 1  # ready, and the node embedded once
    await store.append_edit(message_id=mid, message=_message("edited in the outage", ts=2000))
    fake.status, fake.healthy = 503, False
    for _ in range(memory_index.EMBED_MAX_ATTEMPTS + 2):
        clock.now += 7 * 3600
        assert await w.run_once() == 1, "still picked: never given up"
    row = (await _nodes(engine))[("message", str(mid))]
    assert row.embed_attempts < memory_index.EMBED_MAX_ATTEMPTS
    assert metrics.get("nora_memory_embed_total", outcome="given_up") == 0
    fake.status, fake.healthy = 200, True
    clock.now += 7 * 3600
    assert await w.run_once() == 1
    assert (await _nodes(engine))[("message", str(mid))].embedding is not None


async def test_a_cut_text_whose_vector_was_not_stored_is_not_counted(
    engine, llama, monkeypatch,
):
    """The truncation counter counts stored vectors only. A node edited
    while it was embedded is not stored (it is embedded again, and counted
    then if it is still cut)."""
    _fake, url = llama
    store = SqlRecordStore(engine)
    long_id = await store.append(message=_message("x" * 9000, ts=1000))
    real = LlamaEmbedder.embed_document

    async def _edited_meanwhile(self: Any, text: str) -> Any:
        out = await real(self, text)
        await store.append_edit(message_id=long_id, message=_message("short now", ts=2000))
        return out

    monkeypatch.setattr(LlamaEmbedder, "embed_document", _edited_meanwhile)
    metrics = Metrics()
    assert await worker(engine, url, metrics=metrics).run_once() == 1
    assert (await _nodes(engine))[("message", str(long_id))].embedding is None
    assert metrics.get("nora_memory_embed_truncated_total", kind="message") == 0


async def test_a_social_item_holding_only_its_label_is_empty_until_a_reading_lands(
    engine, llama,
):
    """No caption, no reading: the text is «Instagram story» alone, and every
    such item would get the same vector. It is recorded `empty`, not
    embedded; a reading landing changes its text, and it is embedded then.
    One with a caption is embedded at once."""
    from datetime import UTC, datetime, timedelta

    from nora.domain.media import MediaObject
    from nora.domain.social_stream import MediaItem, SocialItem
    from nora.infra.sql.social_stream import SqlSocialStreamStore

    _fake, url = llama
    store = SqlSocialStreamStore(engine)
    t0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    sha = "d4" * 32
    for item_id, caption in (("bare", None), ("said", "picnic plans")):
        assert await store.insert_item(SocialItem(
            network="instagram", item_id=item_id, account="10000001", kind="story_frame",
            taken_at=t0, expires_at=t0 + timedelta(hours=24), caption=caption,
            alt_text=None, media=(MediaItem("video", "https://example.invalid/x"),),
        ), person="member-a", origin="poll", seen_at=t0,
            media_ref=sha if item_id == "bare" else None)

    clock = Clock()
    await worker(engine, url, clock=clock).run_once()
    nodes = await _nodes(engine)
    bare, said = nodes[("social", "instagram:bare")], nodes[("social", "instagram:said")]
    assert (bare.embedding, bare.embed_error) == (None, "empty")
    assert said.embedding is not None and said.embedding_model == MODEL

    objects = SqlMediaObjectStore(engine)
    await objects.save(obj=MediaObject(
        sha256=sha, media_type="video/mp4", size=1, source="social", description="",
        created_at_ms=1,
    ))
    await objects.comprehend(
        sha256=sha, title="a picnic", description="a blanket in a park", content="", model="m",
    )
    bare = (await _nodes(engine))[("social", "instagram:bare")]
    assert (bare.embedding, bare.embed_attempts, bare.embed_error) == (None, 0, None)
    await worker(engine, url, clock=clock).run_once()
    bare = (await _nodes(engine))[("social", "instagram:bare")]
    assert bare.embedding is not None and bare.embedding_model == MODEL
