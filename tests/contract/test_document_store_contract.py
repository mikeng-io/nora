"""One suite, both implementations of `DocumentStore` (ADR 0020). The real-Postgres
`pg_url` fixture, migrated up before the SQL store is built, parametrized beside the
in-memory fake, as `test_compute_job_store_contract.py` does."""

from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import UUID, uuid4

import pytest

from nora.domain.documents import DocumentExistsError, RevisionIn, handle_of, is_handle
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


# -- the room's own list (the `<documents>` block's read) ---------------------------------


async def _revise(store, doc_id, *, base, title):
    return await store.add_revision(
        document_id=doc_id, base_revision=base, revision=_rev(title, SHA_B)
    )


async def test_list_room_is_this_rooms_documents_newest_revision_first(store):
    first = await _make(store, title="First")
    await asyncio.sleep(0.02)
    second = await _make(store, title="Second")
    await asyncio.sleep(0.02)
    await _revise(store, first, base=1, title="First, revised")  # now the newest touch
    got = await store.list_room(platform="signal", conversation_ids=frozenset({"room-1"}), limit=5)
    assert [d.id for d in got] == [first, second]
    assert got[0].title == "First, revised" and got[0].revision == 2
    assert got[1].title == "Second" and got[1].revision == 1


async def test_list_room_never_returns_another_rooms_document_whoever_made_it(store):
    mine = await _make(store, created_by="ken", room=ROOM, title="Here")
    await _make(store, created_by="ken", room=OTHER_ROOM, title="Elsewhere")
    await _make(store, created_by="ken", room=("discord", "room-1"), title="Other platform")
    got = await store.list_room(platform="signal", conversation_ids=frozenset({"room-1"}), limit=5)
    assert [d.id for d in got] == [mine]


async def test_list_room_reads_every_id_a_room_answers_to(store):
    a = await _make(store, room=("signal", "room-1"), title="A")
    await asyncio.sleep(0.02)
    b = await _make(store, room=("signal", "room-1-alias"), title="B")
    got = await store.list_room(
        platform="signal", conversation_ids=frozenset({"room-1", "room-1-alias"}), limit=5
    )
    assert {d.id for d in got} == {a, b}


async def test_list_room_is_bounded_by_its_limit_and_empty_for_nothing(store):
    for n in range(4):
        await _make(store, title=f"doc {n}")
        await asyncio.sleep(0.01)
    ids = frozenset({"room-1"})
    assert len(await store.list_room(platform="signal", conversation_ids=ids, limit=3)) == 3
    assert await store.list_room(platform="signal", conversation_ids=ids, limit=0) == ()
    assert await store.list_room(platform="signal", conversation_ids=frozenset(), limit=5) == ()


# --------------------------------------------------------------------- the link's handle
PREFIX = "0123456789ab"  # the 12 hex digits two test documents share


def _with_prefix(tail: str) -> UUID:
    """A document id whose handle is PREFIX, the rest chosen by `tail` (20 hex digits)."""
    hex32 = PREFIX + tail.ljust(20, "0")
    return UUID(hex32)


async def _make_id(store, document_id, *, created_by="ken", room=ROOM, title="Weekend"):
    await store.create(
        document_id=document_id, platform=room[0], conversation_id=room[1],
        created_by=created_by, revision=_rev(title),
    )
    return document_id


async def test_a_handle_resolves_the_current_revision_of_a_document_the_person_reads(store):
    doc = await _make_id(store, _with_prefix("a"), title="First")
    await _revise(store, doc, base=1, title="Second")
    got = await store.get_visible_by_handle(
        handle=handle_of(doc), person_id="ken", rooms=frozenset()
    )
    assert got is not None and got.summary.id == doc
    assert got.summary.revision == 2 and got.summary.title == "Second"


async def test_a_handle_is_chosen_among_what_the_person_may_read_only(store):
    # Two documents share the 12-hex handle. Ken reads only the first: the prefix is
    # resolved AFTER visibility, so Ken gets his own and Mei's never collides with it.
    mine = await _make_id(store, _with_prefix("1"), created_by="ken", room=ROOM)
    theirs = await _make_id(store, _with_prefix("2"), created_by="mei", room=OTHER_ROOM)
    handle = handle_of(mine)
    assert handle == handle_of(theirs)
    ken = await store.get_visible_by_handle(handle=handle, person_id="ken", rooms=frozenset())
    mei = await store.get_visible_by_handle(handle=handle, person_id="mei", rooms=frozenset())
    assert ken is not None and ken.summary.id == mine
    assert mei is not None and mei.summary.id == theirs
    # Somebody who reads neither learns nothing: the same None as no such handle.
    nobody = await store.get_visible_by_handle(handle=handle, person_id="zoe", rooms=frozenset())
    nothing = await store.get_visible_by_handle(handle="f" * 12, person_id="zoe", rooms=frozenset())
    assert nobody is None and nothing is None


async def test_a_handle_two_readable_documents_share_is_refused_never_picked(store):
    await _make_id(store, _with_prefix("1"), created_by="ken", room=ROOM)
    await _make_id(store, _with_prefix("2"), created_by="mei", room=ROOM)
    # Ken reads both (a room that names him, and he made one): ambiguous, so none.
    got = await store.get_visible_by_handle(
        handle=PREFIX, person_id="ken", rooms=frozenset({ROOM})
    )
    assert got is None


async def test_a_handle_in_a_room_the_person_no_longer_reads_is_not_found(store):
    doc = await _make_id(store, _with_prefix("3"), created_by="mei", room=ROOM)
    assert (await store.get_visible_by_handle(
        handle=handle_of(doc), person_id="ken", rooms=frozenset({ROOM})
    )) is not None
    assert (await store.get_visible_by_handle(
        handle=handle_of(doc), person_id="ken", rooms=frozenset()
    )) is None


@pytest.mark.parametrize(
    "bad",
    [
        "", "0123456789a", "0123456789abc", "0123456789AB", "0123456789a%", "0123456789a_",
        "%", "_" * 12, "0123456789-b", "' OR 1=1 --", "0123456789ab\n", "０１２３４５６７８９ａｂ",
    ],
)
async def test_what_is_not_a_handle_never_reaches_the_query_and_matches_nothing(store, bad):
    await _make_id(store, _with_prefix("4"), created_by="ken")
    assert not is_handle(bad)
    got = await store.get_visible_by_handle(handle=bad, person_id="ken", rooms=frozenset())
    assert got is None


# ------------------------------------------------------------- lifecycle (document_state)
# Archive, delete and restore are appended rows; the latest one is the state, and no row
# is `active`. These are the read-side guards, each named for the exclusion it holds.


async def test_an_archived_document_leaves_the_default_list_and_shows_under_archived(store):
    kept = await _make(store, title="kept")
    shelved = await _make(store, title="shelved")
    assert await store.set_state(document_id=shelved, state="archived", set_by="mei")
    rooms = frozenset({ROOM})
    default = await store.list_visible(person_id="mei", rooms=rooms, limit=10, offset=0)
    assert [d.id for d in default.items] == [kept] and default.total == 1
    archived = await store.list_visible(
        person_id="mei", rooms=rooms, limit=10, offset=0, state="archived"
    )
    assert [d.id for d in archived.items] == [shelved] and archived.total == 1
    assert archived.items[0].state == "archived"
    # Still opens by its link: the detail and the handle find it.
    got = await store.get_visible(document_id=shelved, person_id="mei", rooms=rooms)
    assert got is not None and got.summary.state == "archived"
    assert (
        await store.get_visible_by_handle(handle=handle_of(shelved), person_id="mei", rooms=rooms)
    ) is not None


async def test_a_deleted_document_is_in_no_list_and_reads_like_an_unknown_id(store):
    gone = await _make(store, title="gone", created_by="ken")
    assert await store.set_state(document_id=gone, state="deleted", set_by="ken")
    rooms = frozenset({ROOM})
    for state in ("active", "archived"):
        page = await store.list_visible(
            person_id="ken", rooms=rooms, limit=10, offset=0, state=state
        )
        assert page.items == () and page.total == 0
    # The creator, a room member, by id and by handle: all the same None as a miss.
    assert await store.get_visible(document_id=gone, person_id="ken", rooms=frozenset()) is None
    assert await store.get_visible(document_id=gone, person_id="mei", rooms=rooms) is None
    assert (
        await store.get_visible_by_handle(handle=handle_of(gone), person_id="ken", rooms=rooms)
    ) is None
    assert await store.list_room(
        platform="signal", conversation_ids=frozenset({"room-1"}), limit=5
    ) == ()
    # Nothing was erased: the service's own read still finds it, with every revision.
    raw = await store.get(document_id=gone)
    assert raw is not None and raw.summary.state == "deleted" and raw.summary.title == "gone"
    assert [h.revision for h in await store.history(document_id=gone)] == [1]


async def test_add_revision_is_refused_on_an_archived_or_deleted_document(store):
    for state in ("archived", "deleted"):
        doc = await _make(store, title=f"was {state}")
        assert await store.set_state(document_id=doc, state=state, set_by="ken")
        out = await store.add_revision(document_id=doc, base_revision=1, revision=_rev("v2"))
        assert out.status == "not_active" and out.document is None
        assert [h.revision for h in await store.history(document_id=doc)] == [1]


async def test_restore_returns_a_document_to_every_read_and_lets_it_be_revised(store):
    doc = await _make(store, title="back")
    rooms = frozenset({ROOM})
    for state in ("archived", "deleted", "active"):
        assert await store.set_state(document_id=doc, state=state, set_by="mei")
    page = await store.list_visible(person_id="mei", rooms=rooms, limit=10, offset=0)
    assert [d.id for d in page.items] == [doc] and page.items[0].state == "active"
    assert await store.get_visible(document_id=doc, person_id="mei", rooms=rooms) is not None
    assert [d.id for d in await store.list_room(
        platform="signal", conversation_ids=frozenset({"room-1"}), limit=5
    )] == [doc]
    out = await store.add_revision(document_id=doc, base_revision=1, revision=_rev("v2"))
    assert out.status == "ok" and out.document.summary.revision == 2


async def test_list_room_leaves_out_archived_and_deleted_documents(store):
    live = await _make(store, title="live")
    archived = await _make(store, title="archived")
    deleted = await _make(store, title="deleted")
    await store.set_state(document_id=archived, state="archived", set_by="ken")
    await store.set_state(document_id=deleted, state="deleted", set_by="ken")
    got = await store.list_room(platform="signal", conversation_ids=frozenset({"room-1"}), limit=5)
    assert [d.id for d in got] == [live]


async def test_set_state_on_an_unknown_id_is_false_and_writes_nothing(store):
    assert await store.set_state(document_id=uuid4(), state="deleted", set_by="ken") is False


async def test_the_state_table_is_append_only_and_its_check_names_every_state(request):
    """SQL only: every change is a row (none updated), the latest wins, and the CHECK on
    `state` admits exactly `DOCUMENT_STATES` and refuses anything else."""
    import sqlalchemy as sa
    from sqlalchemy.exc import IntegrityError

    from nora.domain.documents import DOCUMENT_STATES
    from nora.infra.sql.schema import DOCUMENT_STATE

    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    s = SqlDocumentStore(engine)
    try:
        doc = await _make(s)
        for state in ("archived", "deleted", "active"):
            await s.set_state(document_id=doc, state=state, set_by="ken")
        async with engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(DOCUMENT_STATE.c.state, DOCUMENT_STATE.c.set_by)
                .where(DOCUMENT_STATE.c.document_id == doc)
                .order_by(DOCUMENT_STATE.c.id)
            )).all()
            check = (await conn.execute(sa.text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_document_state_state'"
            ))).scalar_one()
        assert [tuple(r) for r in rows] == [
            ("archived", "ken"), ("deleted", "ken"), ("active", "ken"),
        ]
        assert sorted(re.findall(r"'([a-z]+)'::text", check)) == sorted(DOCUMENT_STATES)
        with pytest.raises(IntegrityError, match="ck_document_state_state"):
            async with engine.begin() as conn:
                await conn.execute(sa.insert(DOCUMENT_STATE).values(
                    document_id=doc, state="purged", set_by="ken"))
        with pytest.raises(IntegrityError, match="ck_document_state_set_by"):
            async with engine.begin() as conn:
                await conn.execute(sa.insert(DOCUMENT_STATE).values(
                    document_id=doc, state="archived", set_by=""))
    finally:
        await engine.dispose()
