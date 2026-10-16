"""The social stream's projection into the memory index (design/cognition-loop/
22 §9, the operator's rulings): one `social` node per item and one
`social_media` node per medium, `attached_to` it, both their author's
(`persons`, no unit, no room); projected by every write of `social_item`,
`social_item_state` and `social_perception` in its own transaction, and by a
reading landing on the bytes; dropped by a gone or hidden state; rebuilt to
the same nodes; and `story_frame_embedded`, what `StoryMediaExpiry` reads.

Every value here is synthetic. Module-scoped database, truncated between
tests (`test_memory_projection_record.py`'s pattern)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.media import MediaObject
from nora.domain.social_stream import MediaItem, SocialItem
from nora.infra.sql import memory_index
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.media_objects import SqlMediaObjectStore
from nora.infra.sql.memory_index import EMBED_MAX_ATTEMPTS, Embeddable
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.schema import EMBEDDING_DIMENSIONS, MEMORY_EDGE, MEMORY_NODE
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.obs.metrics import Metrics

pytestmark = pytest.mark.asyncio

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
A, B, C = "a1" * 32, "b2" * 32, "c3" * 32
PERSON = "member-a"
LOCATION = "Placeholder Square"


@pytest.fixture(scope="module")
def _db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_memory_social_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture
async def engine(_db: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(_db)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            for table in (
                "social_item", "social_item_state", "social_perception", "media_objects",
                "memory_node", "memory_edge", "memory_projection_queue",
            ):
                await conn.execute(sa.text(f'TRUNCATE "{table}" CASCADE'))
        await eng.dispose()


def _item(item_id: str = "9001", *, kind: str = "post", caption: str | None = "picnic plans",
          alt: str | None = "two cups on a blanket", collab: bool = False) -> SocialItem:
    return SocialItem(
        network="instagram", item_id=item_id, account="10000001", kind=kind,  # type: ignore[arg-type]
        taken_at=T0, expires_at=T0 + timedelta(hours=24) if kind == "story_frame" else None,
        caption=caption, alt_text=alt, media=(MediaItem("image", "https://example.invalid/x"),),
        collab=collab,
    )


async def _insert(store: SqlSocialStreamStore, item: SocialItem, shas: tuple[str, ...]) -> None:
    assert await store.insert_item(
        item, person=PERSON, origin="poll", seen_at=T0, media_ref=",".join(shas),
        location=LOCATION, tagged=("@tagged_one",), mentioned=("@mentioned_one",),
    )


async def _nodes(engine: Any) -> dict[tuple[str, str], Any]:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(MEMORY_NODE))).all()
    return {(r.kind, r.source_id): r for r in rows}


async def _edges(engine: Any) -> set[tuple[str, ...]]:
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(
            MEMORY_EDGE.c.src_kind, MEMORY_EDGE.c.src_id, MEMORY_EDGE.c.rel,
            MEMORY_EDGE.c.dst_kind, MEMORY_EDGE.c.dst_id,
        ))).all()
    return {tuple(r) for r in rows}


def _media_id(item: str, sha: str) -> str:
    return f"{item}:{sha}"


async def test_a_carousel_is_one_item_node_and_a_media_node_per_medium(engine):
    metrics = Metrics()
    store = SqlSocialStreamStore(engine, metrics=metrics)
    await _insert(store, _item(), (A, B))
    sid = "instagram:9001"

    nodes = await _nodes(engine)
    item = nodes[("social", sid)]
    assert item.text == "Instagram post\npicnic plans", "never the alt text"
    assert (item.persons, item.unit, item.origin_room) == ([PERSON], None, None)
    assert item.at_ms == int(T0.timestamp() * 1000)
    for sha in (A, B):
        media = nodes[("social_media", _media_id(sid, sha))]
        assert (media.persons, media.unit, media.origin_room, media.sha256, media.text) == (
            [PERSON], None, None, sha, "",
        )
    assert await _edges(engine) == {
        ("social", sid, "authored_by", "person", PERSON),
        ("social_media", _media_id(sid, A), "attached_to", "social", sid),
        ("social_media", _media_id(sid, B), "attached_to", "social", sid),
        ("social_media", _media_id(sid, A), "shared_by", "person", PERSON),
        ("social_media", _media_id(sid, B), "shared_by", "person", PERSON),
    }, "the author is the only person: tagged and mentioned handles get no edge"
    assert metrics.get("nora_memory_projection_total", kind="social", outcome="ok") == 1


async def test_location_alt_text_tags_and_mentions_are_never_in_the_text(engine):
    """The network's alt text is phrased with the place and the tagged
    handles, so it is left out with them."""
    store = SqlSocialStreamStore(engine)
    await _insert(store, _item(kind="story_frame", collab=True), (A,))
    texts = [n.text for n in (await _nodes(engine)).values()]
    assert any(t.startswith("Instagram story, co-authored") for t in texts)
    for t in texts:
        assert LOCATION not in t and "tagged_one" not in t and "mentioned_one" not in t
        assert "two cups" not in t


async def test_a_reading_landing_reprojects_the_item_and_its_medium(engine):
    """Custody's row, then perception's reading (`comprehend`, which
    reprojects the bytes), then the perception row: the item's text carries
    the reading in its media's order, and the medium's text is its reading."""
    store = SqlSocialStreamStore(engine)
    objects = SqlMediaObjectStore(engine)
    await _insert(store, _item(), (A, B))
    sid = "instagram:9001"
    for sha in (A, B):
        assert await objects.save(obj=MediaObject(
            sha256=sha, media_type="image/jpeg", size=10, source="social", description="",
            created_at_ms=1,
        ))
    assert await objects.comprehend(
        sha256=B, title="a blanket", description="cups on a blanket in a park",
        content="", model="m",
    )
    nodes = await _nodes(engine)
    assert nodes[("social_media", _media_id(sid, B))].text == (
        "a blanket\ncups on a blanket in a park"
    )
    assert nodes[("social", sid)].text.endswith("a blanket\ncups on a blanket in a park")

    await objects.comprehend(sha256=A, title="a basket", description="", content="", model="m")
    await store.record_perception(
        network="instagram", item_id="9001", person=PERSON, units=("u1",), origin="poll",
        outcome="ok", calls=2, at=T0,
    )
    text = (await _nodes(engine))[("social", sid)].text
    assert text.index("a basket") < text.index("cups on a blanket in a park"), (
        "readings in media order"
    )


async def test_a_perception_row_projects_an_item_whose_projection_was_lost(engine):
    store = SqlSocialStreamStore(engine)
    await _insert(store, _item(), (A,))
    async with engine.begin() as conn:
        await conn.execute(sa.delete(MEMORY_NODE))
        await conn.execute(sa.delete(MEMORY_EDGE))
    await store.record_perception(
        network="instagram", item_id="9001", person=PERSON, units=("u1",), origin="poll",
        outcome="failed", calls=0, at=T0, reason="expired",
    )
    assert ("social", "instagram:9001") in await _nodes(engine)


@pytest.mark.parametrize("state", ["gone", "hidden"])
async def test_a_gone_or_hidden_item_drops_its_nodes_and_edges(engine, state):
    store = SqlSocialStreamStore(engine)
    await _insert(store, _item(), (A, B))
    await _insert(store, _item("9002"), (C,))
    await store.record_state(network="instagram", item_id="9001", state=state, reason="test")

    nodes = await _nodes(engine)
    assert not [k for k in nodes if k[1].startswith("instagram:9001")]
    assert ("social", "instagram:9002") in nodes
    assert not [e for e in await _edges(engine) if "instagram:9001" in e[1] + e[4]]
    assert ("person", PERSON) in nodes, "the other item still reaches its author"

    # A later perception of the hidden item re-projects nothing back.
    await store.record_perception(
        network="instagram", item_id="9001", person=PERSON, units=("u1",), origin="poll",
        outcome="ok", calls=1, at=T0,
    )
    assert ("social", "instagram:9001") not in await _nodes(engine)


async def test_reprojecting_an_unchanged_item_keeps_its_vectors(engine):
    store = SqlSocialStreamStore(engine)
    await _insert(store, _item(), (A,))
    sid = "instagram:9001"
    async with engine.begin() as conn:
        node = (await _nodes(engine))[("social", sid)]
        stored = await memory_index.embed_store(
            conn, Embeddable(node.id, "social", sid, node.text, None),
            vector=[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1), model="m", now_ms=1,
            by_bytes=False,
        )
        assert stored
        await memory_index.project(conn, "social", sid)
    after = (await _nodes(engine))[("social", sid)]
    assert after.embedding_model == "m" and after.text == node.text


async def _embed(engine: Any, key: tuple[str, str], *, model: str = "m") -> None:
    node = (await _nodes(engine))[key]
    async with engine.begin() as conn:
        assert await memory_index.embed_store(
            conn, Embeddable(node.id, node.kind, node.source_id, node.text, node.sha256),
            vector=[1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1), model=model, now_ms=1,
            by_bytes=True,
        )


async def _fail(engine: Any, key: tuple[str, str], error: str, times: int) -> None:
    node = (await _nodes(engine))[key]
    item = Embeddable(node.id, node.kind, node.source_id, node.text, node.sha256)
    async with engine.begin() as conn:
        for _ in range(times):
            await memory_index.embed_failed(conn, item, error=error, now_ms=1, by_bytes=False)


async def _embedded(engine: Any, *frames: str, model: str = "m") -> set[tuple[str, str]]:
    async with engine.connect() as conn:
        return await memory_index.story_frame_embedded(
            conn, [("instagram", f) for f in frames], model=model,
        )


async def test_a_frame_is_embedded_when_every_medium_is_embedded_or_terminal(engine):
    store = SqlSocialStreamStore(engine)
    await _insert(store, _item("f1", kind="story_frame"), (A, B))
    await _insert(store, _item("f2", kind="story_frame"), (C,))
    await _insert(store, _item("f3", kind="story_frame"), ())
    f1 = "instagram:f1"

    assert await _embedded(engine, "f1", "f2", "f3") == set(), "nothing embedded yet"
    await _embed(engine, ("social_media", _media_id(f1, A)))
    assert await _embedded(engine, "f1") == set(), "one medium of two is not the frame"
    await _fail(engine, ("social_media", _media_id(f1, B)), "rejected:x", EMBED_MAX_ATTEMPTS - 1)
    assert await _embedded(engine, "f1") == set(), "still being retried is not terminal"
    await _fail(engine, ("social_media", _media_id(f1, B)), "rejected:x", 1)
    assert await _embedded(engine, "f1") == {("instagram", "f1")}, "given up is terminal"
    assert await _embedded(engine, "f1", model="other") == set(), "another model's vector"

    await _fail(engine, ("social_media", _media_id("instagram:f2", C)), "empty", 1)
    assert await _embedded(engine, "f2") == {("instagram", "f2")}, "empty is terminal"
    assert await _embedded(engine, "f3") == set(), "a frame with no medium node is not"

    await store.record_state(network="instagram", item_id="f1", state="gone")
    assert await _embedded(engine, "f1") == set(), "a gone frame's nodes are dropped"


async def test_a_rebuild_projects_social_items_like_live_and_is_idempotent(engine):
    store = SqlSocialStreamStore(engine)
    await _insert(store, _item("9001"), (A, B))
    await _insert(store, _item("9002", kind="reel"), (C,))
    await _insert(store, _item("9003"), (A,))
    await store.record_state(network="instagram", item_id="9003", state="hidden")
    live_nodes = {k: (n.text, n.persons, n.sha256) for k, n in (await _nodes(engine)).items()}
    live_edges = await _edges(engine)

    async with engine.begin() as conn:
        await conn.execute(sa.delete(MEMORY_EDGE))
        await conn.execute(sa.delete(MEMORY_NODE))
        # A node left from before the hide must go too (the final pass).
        await conn.execute(sa.insert(MEMORY_NODE).values(
            kind="social", source_id="instagram:9003", persons=[PERSON], text="stale",
        ))
    report = await memory_index.rebuild(engine)
    assert report.projected["social"] == 3 and report.unprojected == 0
    rebuilt = {k: (n.text, n.persons, n.sha256) for k, n in (await _nodes(engine)).items()}
    assert rebuilt == live_nodes and await _edges(engine) == live_edges

    await memory_index.rebuild(engine)
    assert {k: (n.text, n.persons, n.sha256) for k, n in (await _nodes(engine)).items()} == (
        live_nodes
    )


async def test_a_social_medium_is_typed_by_its_custody_row(engine):
    """The embedding worker's `media_types`: a social medium has no message,
    so an image is known from `media_objects`."""
    objects = SqlMediaObjectStore(engine)
    await objects.save(obj=MediaObject(
        sha256=A, media_type="image/png", size=1, source="social", description="",
        created_at_ms=1,
    ))
    async with engine.connect() as conn:
        types = await memory_index.media_types(
            conn, [], social=[("instagram:1:" + A, A), ("instagram:1:" + B, B)],
        )
    assert types == {"instagram:1:" + A: "image/png", "instagram:1:" + B: ""}
