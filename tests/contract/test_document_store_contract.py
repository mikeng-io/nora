"""One suite, both implementations of `DocumentStore` (ADR 0020). The real-Postgres
`pg_url` fixture, migrated up before the SQL store is built, parametrized beside the
in-memory fake, as `test_compute_job_store_contract.py` does."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from nora.domain.documents import DocumentExistsError, RevisionIn
from nora.infra.sql.documents import SqlDocumentStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import FakeDocumentStore

SHA_A = "a" * 64
SHA_B = "b" * 64
ROOM = ("signal", "room-1")
OTHER_ROOM = ("signal", "room-2")


def _rev(title: str = "Weekend", sha: str = SHA_A, **overrides: Any) -> RevisionIn:
    base: dict[str, Any] = {
        "title": title,
        "spec": {"version": 1, "root": {"type": "Page", "props": {"title": title}, "children": []}},
        "text": title,
        "sha256": sha,
        "media": (),
        "written_by": "tag:1",
    }
    base.update(overrides)
    return RevisionIn(**base)


@pytest.fixture(params=["fake", "sql"])
async def store(request):
    if request.param == "fake":
        s = FakeDocumentStore()
    else:
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s = SqlDocumentStore(create_sql_engine(pg_url))
    yield s
    await s.aclose()


async def _make(store, *, created_by="ken", room=ROOM, title="Weekend", sha=SHA_A):
    doc_id = uuid4()
    await store.create(
        document_id=doc_id,
        platform=room[0],
        conversation_id=room[1],
        created_by=created_by,
        revision=_rev(title, sha),
    )
    return doc_id


async def test_create_then_get_round_trips_the_spec_and_the_room(store):
    spec = {"version": 1, "root": {"type": "Page", "props": {"title": "旅行 é"}, "children": []}}
    doc_id = uuid4()
    made = await store.create(
        document_id=doc_id,
        platform="signal",
        conversation_id="room-1",
        created_by="ken",
        revision=_rev("旅行 é", SHA_B, spec=spec, media=(SHA_A, SHA_B), text="旅行 é 食飯"),
    )
    got = await store.get(document_id=doc_id)
    assert got is not None and got == made
    assert got.spec == spec
    assert got.media == (SHA_A, SHA_B)
    assert got.text == "旅行 é 食飯"
    assert got.sha256 == SHA_B
    assert (got.summary.platform, got.summary.conversation_id) == ROOM
    assert got.summary.created_by == "ken"
    assert got.summary.revision == 1
    assert got.summary.created_at.utcoffset().total_seconds() == 0
    assert got.summary.updated_at >= got.summary.created_at


async def test_an_unknown_id_is_none_and_a_repeated_create_is_refused(store):
    assert await store.get(document_id=uuid4()) is None
    doc_id = await _make(store)
    with pytest.raises(DocumentExistsError):
        await store.create(
            document_id=doc_id,
            platform="signal",
            conversation_id="room-1",
            created_by="ken",
            revision=_rev(),
        )


async def test_a_revision_keeps_identity_and_becomes_current_while_history_keeps_both(store):
    doc_id = await _make(store, title="v1", sha=SHA_A)
    first = await store.get(document_id=doc_id)
    out = await store.add_revision(
        document_id=doc_id,
        base_revision=1,
        revision=_rev("v2", SHA_B, written_by="tag:2"),
    )
    assert out.status == "ok" and out.document is not None
    assert out.document.summary.revision == 2
    assert out.document.summary.title == "v2"
    assert out.document.sha256 == SHA_B
    assert out.document.written_by == "tag:2"
    assert out.document.summary.created_by == "ken"
    assert out.document.summary.created_at == first.summary.created_at
    assert out.document.summary.updated_at > first.summary.updated_at
    history = await store.history(document_id=doc_id)
    assert [(h.revision, h.title, h.sha256) for h in history] == [
        (1, "v1", SHA_A),
        (2, "v2", SHA_B),
    ]
    assert history[0].recorded_at < history[1].recorded_at


async def test_a_revision_from_a_stale_base_conflicts_and_an_unknown_id_is_not_found(store):
    doc_id = await _make(store)
    assert (
        await store.add_revision(
            document_id=doc_id,
            base_revision=1,
            revision=_rev("v2", SHA_B),
        )
    ).status == "ok"
    stale = await store.add_revision(document_id=doc_id, base_revision=1, revision=_rev("v3"))
    assert stale.status == "conflict" and stale.document is None
    ahead = await store.add_revision(document_id=doc_id, base_revision=5, revision=_rev("v3"))
    assert ahead.status == "conflict"
    assert (await store.get(document_id=doc_id)).summary.revision == 2
    assert (
        await store.add_revision(
            document_id=uuid4(),
            base_revision=1,
            revision=_rev(),
        )
    ).status == "not_found"


async def test_two_revisions_from_the_same_base_leave_exactly_one_winner(store):
    doc_id = await _make(store)
    results = await asyncio.gather(
        *(
            store.add_revision(
                document_id=doc_id,
                base_revision=1,
                revision=_rev(f"v{i}", SHA_B),
            )
            for i in range(6)
        )
    )
    assert sorted(r.status for r in results) == ["conflict"] * 5 + ["ok"]
    assert (await store.get(document_id=doc_id)).summary.revision == 2
    assert len(await store.history(document_id=doc_id)) == 2


async def test_a_creator_sees_their_own_document_with_no_rooms_at_all(store):
    doc_id = await _make(store, created_by="ken", room=OTHER_ROOM)
    page = await store.list_visible(person_id="ken", rooms=frozenset(), limit=10, offset=0)
    assert [d.id for d in page.items] == [doc_id]
    assert (
        await store.get_visible(
            document_id=doc_id,
            person_id="ken",
            rooms=frozenset(),
        )
    ) is not None


async def test_a_person_the_room_names_sees_it_and_nobody_else_does(store):
    doc_id = await _make(store, created_by="ken", room=ROOM)
    mei_rooms = frozenset({ROOM})
    assert (
        await store.get_visible(
            document_id=doc_id,
            person_id="mei",
            rooms=mei_rooms,
        )
    ) is not None
    # Not in the room, not the creator: the same answer as "no such document".
    assert (
        await store.get_visible(
            document_id=doc_id,
            person_id="jo",
            rooms=frozenset({OTHER_ROOM}),
        )
        is None
    )
    assert (
        await store.get_visible(
            document_id=doc_id,
            person_id="jo",
            rooms=frozenset(),
        )
        is None
    )
    page = await store.list_visible(
        person_id="jo",
        rooms=frozenset({OTHER_ROOM}),
        limit=10,
        offset=0,
    )
    assert page.items == () and page.total == 0


async def test_the_same_conversation_id_on_another_platform_is_another_room(store):
    doc_id = await _make(store, created_by="ken", room=("signal", "room-1"))
    assert (
        await store.get_visible(
            document_id=doc_id,
            person_id="mei",
            rooms=frozenset({("discord", "room-1")}),
        )
        is None
    )


async def test_a_listing_shows_the_current_revision_newest_first_and_pages(store):
    a = await _make(store, title="a")
    b = await _make(store, title="b")
    c = await _make(store, title="c")
    await store.add_revision(document_id=a, base_revision=1, revision=_rev("a2", SHA_B))
    rooms = frozenset({ROOM})
    page = await store.list_visible(person_id="mei", rooms=rooms, limit=10, offset=0)
    assert [d.title for d in page.items] == ["a2", "c", "b"]
    assert page.total == 3
    assert [d.revision for d in page.items] == [2, 1, 1]
    assert page.items[0].created_at < page.items[0].updated_at
    second = await store.list_visible(person_id="mei", rooms=rooms, limit=2, offset=2)
    assert [d.id for d in second.items] == [b] and second.total == 3
    none = await store.list_visible(person_id="mei", rooms=rooms, limit=0, offset=0)
    assert none.items == () and none.total == 3
    assert {a, b, c} == {d.id for d in page.items}


async def test_history_of_an_unknown_document_is_empty(store):
    assert await store.history(document_id=uuid4()) == ()


async def test_a_naive_clock_never_leaves_the_store(store):
    doc_id = await _make(store)
    got = await store.get(document_id=doc_id)
    for stamp in (got.summary.created_at, got.summary.updated_at):
        assert stamp.tzinfo is not None


async def test_a_page_past_the_end_or_out_of_bounds_is_an_empty_page_on_both_stores(store):
    # A 30-digit `page` reached the SQL store as an OFFSET it could not take and was a
    # 500, while the fake answered an empty page (review of PR 654). Both now clamp.
    await _make(store)
    rooms = frozenset({ROOM})
    for limit, offset in ((10, 10**30), (10, 2**63), (10**9, 0), (-5, 0), (10, -7)):
        page = await store.list_visible(person_id="mei", rooms=rooms, limit=limit, offset=offset)
        assert page.total == 1
    far = await store.list_visible(person_id="mei", rooms=rooms, limit=10, offset=10**30)
    assert far.items == ()
    assert (
        len((await store.list_visible(person_id="mei", rooms=rooms, limit=10**9, offset=0)).items)
        == 1
    )
    assert (
        len((await store.list_visible(person_id="mei", rooms=rooms, limit=10, offset=-7)).items)
        == 1
    )


async def test_the_table_refuses_a_bad_media_element_and_an_oversized_spec(request):
    """SQL only: the CHECKs are the table's own bound, for a writer that is not the store."""
    import sqlalchemy as sa
    from sqlalchemy.exc import IntegrityError

    from nora.infra.sql.schema import DOCUMENT_REVISION, DOCUMENTS

    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    doc_id = uuid4()
    base = {
        "document_id": doc_id,
        "revision": 1,
        "title": "t",
        "spec": {"a": 1},
        "text": "t",
        "sha256": SHA_A,
        "media": [SHA_A],
        "written_by": "tag",
    }
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.insert(DOCUMENTS).values(
                    id=doc_id,
                    platform="signal",
                    conversation_id="r",
                    created_by="ken",
                )
            )
            await conn.execute(sa.insert(DOCUMENT_REVISION).values(base))  # the good row passes
        for constraint, bad in (
            ("ck_document_revision_media", {"media": ["../../etc"]}),
            ("ck_document_revision_media", {"media": ["NOT A SHA"]}),
            ("ck_document_revision_media", {"media": ["abc"]}),
            ("ck_document_revision_media", {"media": [SHA_A, "x"]}),
            ("ck_document_revision_spec_size", {"spec": {"blob": "x" * 1_100_000}}),
            ("ck_document_revision_positive", {"revision": 0}),
            ("ck_document_revision_sha256", {"sha256": "abc"}),
        ):
            # Each row must be refused by ITS OWN constraint, not by some other fault.
            with pytest.raises(IntegrityError, match=constraint):
                async with engine.begin() as conn:
                    await conn.execute(
                        sa.insert(DOCUMENT_REVISION).values({**base, "revision": 2, **bad})
                    )
    finally:
        await engine.dispose()
