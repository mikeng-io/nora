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
    # A 30-digit `page` would reach the SQL store as an OFFSET it cannot take; both
    # implementations clamp it.
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
    got = await store.list_room(platform="signal", conversation_ids=frozenset({"room-1"}), limit=5,
    person_id="reader", rooms=frozenset({ROOM}))
    assert [d.id for d in got] == [first, second]
    assert got[0].title == "First, revised" and got[0].revision == 2
    assert got[1].title == "Second" and got[1].revision == 1


async def test_list_room_never_returns_another_rooms_document_whoever_made_it(store):
    mine = await _make(store, created_by="ken", room=ROOM, title="Here")
    await _make(store, created_by="ken", room=OTHER_ROOM, title="Elsewhere")
    await _make(store, created_by="ken", room=("discord", "room-1"), title="Other platform")
    got = await store.list_room(platform="signal", conversation_ids=frozenset({"room-1"}), limit=5,
    person_id="reader", rooms=frozenset({ROOM}))
    assert [d.id for d in got] == [mine]


async def test_list_room_reads_every_id_a_room_answers_to(store):
    a = await _make(store, room=("signal", "room-1"), title="A")
    await asyncio.sleep(0.02)
    b = await _make(store, room=("signal", "room-1-alias"), title="B")
    got = await store.list_room(
        platform="signal", conversation_ids=frozenset({"room-1", "room-1-alias"}), limit=5,
        person_id="reader", rooms=frozenset({ROOM, ("signal", "room-1-alias")})
    )
    assert {d.id for d in got} == {a, b}


async def test_list_room_is_bounded_by_its_limit_and_empty_for_nothing(store):
    for n in range(4):
        await _make(store, title=f"doc {n}")
        await asyncio.sleep(0.01)
    ids = frozenset({"room-1"})
    assert len(await store.list_room(platform="signal", conversation_ids=ids, limit=3,
    person_id="reader", rooms=frozenset({ROOM}))) == 3
    assert await store.list_room(platform="signal", conversation_ids=ids, limit=0,
    person_id="reader", rooms=frozenset({ROOM})) == ()
    assert await store.list_room(platform="signal", conversation_ids=frozenset(), limit=5,
    person_id="reader", rooms=frozenset({ROOM})) == ()


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
        platform="signal", conversation_ids=frozenset({"room-1"}), limit=5,
        person_id="reader", rooms=frozenset({ROOM})
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
        platform="signal", conversation_ids=frozenset({"room-1"}), limit=5,
        person_id="reader", rooms=frozenset({ROOM})
    )] == [doc]
    out = await store.add_revision(document_id=doc, base_revision=1, revision=_rev("v2"))
    assert out.status == "ok" and out.document.summary.revision == 2


async def test_list_room_applies_the_visibility_before_the_limit(store):
    """Aikido (medium, PR 203): a limit applied before the visibility filter drops the asker's own
    older document behind newer ones of others. The store filters first, so the limit counts
    only what the asker may read; a room that names the asker reads the whole list."""
    mine = await _make(store, created_by="mei", title="Mine")
    for n in range(4):
        await asyncio.sleep(0.01)
        await _make(store, created_by="ken", title=f"Theirs {n}")
    ids = frozenset({"room-1"})
    got = await store.list_room(
        platform="signal", conversation_ids=ids, limit=3, person_id="mei", rooms=frozenset(),
    )
    assert [d.id for d in got] == [mine]
    named = await store.list_room(
        platform="signal", conversation_ids=ids, limit=3, person_id="mei", rooms=frozenset({ROOM}),
    )
    assert len(named) == 3 and mine not in {d.id for d in named}


async def test_list_room_leaves_out_archived_and_deleted_documents(store):
    live = await _make(store, title="live")
    archived = await _make(store, title="archived")
    deleted = await _make(store, title="deleted")
    await store.set_state(document_id=archived, state="archived", set_by="ken")
    await store.set_state(document_id=deleted, state="deleted", set_by="ken")
    got = await store.list_room(platform="signal", conversation_ids=frozenset({"room-1"}), limit=5,
    person_id="reader", rooms=frozenset({ROOM}))
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


async def test_find_by_handle_sees_every_state_and_no_visibility(store):
    live = await _make_id(store, _with_prefix("5"), created_by="ken")
    gone = await _make_id(store, _with_prefix("6"), created_by="mei", room=OTHER_ROOM)
    await store.set_state(document_id=gone, state="deleted", set_by="mei")
    found = await store.find_by_handle(handle=PREFIX)
    assert sorted(d.summary.id for d in found) == sorted([live, gone])
    assert {d.summary.id: d.summary.state for d in found}[gone] == "deleted"
    assert await store.find_by_handle(handle="not a handle") == ()


async def _locked_holder(engine, doc):
    """A second connection that takes the document's identity-row lock the way the store
    does, and holds it until the caller commits."""
    import sqlalchemy as sa

    from nora.infra.sql.schema import DOCUMENTS

    conn = await engine.connect()
    trans = await conn.begin()
    await conn.execute(
        sa.select(DOCUMENTS.c.id).where(DOCUMENTS.c.id == doc).with_for_update()
    )
    return conn, trans


async def test_a_revision_waits_for_a_delete_in_flight_and_then_refuses(request):
    """SQL only, two real connections. A delete holds the identity-row lock and has
    written its state row but not committed; a revision that starts now must wait, and
    when the delete commits it must see `deleted` and write nothing."""
    import sqlalchemy as sa

    from nora.infra.sql.schema import DOCUMENT_STATE

    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    s = SqlDocumentStore(engine)
    try:
        doc = await _make(s)
        conn, trans = await _locked_holder(engine, doc)
        await conn.execute(
            sa.insert(DOCUMENT_STATE).values(document_id=doc, state="deleted", set_by="ken")
        )
        revising = asyncio.create_task(
            s.add_revision(document_id=doc, base_revision=1, revision=_rev("v2"))
        )
        await asyncio.sleep(0.5)
        assert not revising.done()  # blocked on the lock, not racing past it
        await trans.commit()
        await conn.close()
        out = await asyncio.wait_for(revising, timeout=10)
        assert out.status == "not_active"
        assert [h.revision for h in await s.history(document_id=doc)] == [1]
    finally:
        await engine.dispose()


async def test_a_state_change_waits_for_a_revision_in_flight(request):
    """SQL only: the other direction. A revision holds the lock; `set_state` waits for it
    rather than writing alongside it."""
    pg_url = request.getfixturevalue("pg_url")
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    s = SqlDocumentStore(engine)
    try:
        doc = await _make(s)
        conn, trans = await _locked_holder(engine, doc)
        changing = asyncio.create_task(s.set_state(document_id=doc, state="deleted", set_by="ken"))
        await asyncio.sleep(0.5)
        assert not changing.done()
        await trans.commit()
        await conn.close()
        assert await asyncio.wait_for(changing, timeout=10) is True
        assert (await s.get(document_id=doc)).summary.state == "deleted"
    finally:
        await engine.dispose()


# --------------------------------------------------------------- one revision
async def test_one_revision_is_its_own_snapshot_and_never_the_current_one(store):
    doc_id = await _make(store, title="v1", sha=SHA_A)
    await store.add_revision(
        document_id=doc_id,
        base_revision=1,
        revision=_rev("v2", SHA_B, written_by="tag:2", media=(SHA_B,)),
    )
    old = await store.revision(document_id=doc_id, revision=1)
    assert old is not None
    assert (old.revision, old.title, old.sha256, old.written_by, old.media) == (
        1, "v1", SHA_A, "tag:1", (),
    )
    assert old.spec["root"]["props"]["title"] == "v1"
    assert old.text == "v1"
    assert old.recorded_at.tzinfo is not None
    new = await store.revision(document_id=doc_id, revision=2)
    assert new is not None and new.title == "v2" and new.media == (SHA_B,)
    # The history carries each revision's pictures, so an old one's can still be served.
    assert [h.media for h in await store.history(document_id=doc_id)] == [(), (SHA_B,)]


async def test_a_revision_that_does_not_exist_is_none(store):
    doc_id = await _make(store)
    for n in (0, 2, -1):
        assert await store.revision(document_id=doc_id, revision=n) is None
    assert await store.revision(document_id=uuid4(), revision=1) is None


async def test_a_revision_of_an_archived_or_deleted_document_is_still_read_by_the_store(store):
    # The store has no visibility and no state rule here; the caller gates on get_visible.
    doc_id = await _make(store, title="kept")
    await store.set_state(document_id=doc_id, state="deleted", set_by="ken")
    kept = await store.revision(document_id=doc_id, revision=1)
    assert kept is not None and kept.title == "kept"


# ------------------------------------------------------------------------- search
async def _make_text(store, *, title, text, created_by="ken", room=ROOM) -> UUID:
    doc_id = uuid4()
    await store.create(
        document_id=doc_id,
        platform=room[0],
        conversation_id=room[1],
        created_by=created_by,
        revision=_rev(title, SHA_A, text=text),
    )
    return doc_id


async def _found(store, query, *, person="mei", rooms=frozenset({ROOM}), state="active"):
    page = await store.list_visible(
        person_id=person, rooms=rooms, limit=50, offset=0, state=state, query=query
    )
    return {d.id for d in page.items}, page.total


async def test_a_search_matches_the_current_titles_and_texts_ignoring_case(store):
    by_title = await _make_text(store, title="Lisbon weekend", text="flights")
    by_text = await _make_text(store, title="Packing", text="Pack a LISBON guide and a coat")
    other = await _make_text(store, title="Tokyo", text="ramen")

    found, total = await _found(store, "lisbon")
    assert found == {by_title, by_text} and total == 2 and other not in found
    # Inner whitespace is collapsed, an empty search is no search.
    assert (await _found(store, "  LISBON   weekend "))[0] == {by_title}
    assert (await _found(store, ""))[1] == 3 and (await _found(store, "   "))[1] == 3
    assert await _found(store, "nothing like it") == (set(), 0)


async def test_a_search_reads_the_current_revision_only(store):
    doc_id = await _make_text(store, title="Old title", text="first draft mentions tulips")
    out = await store.add_revision(
        document_id=doc_id, base_revision=1,
        revision=_rev("New title", SHA_B, text="second draft mentions roses"),
    )
    assert out.status == "ok"
    assert (await _found(store, "tulips"))[0] == set(), "a superseded revision is not searched"
    assert (await _found(store, "roses"))[0] == {doc_id}
    assert (await _found(store, "new title"))[0] == {doc_id}


async def test_a_query_is_text_never_a_pattern(store):
    plain = await _make_text(store, title="Plain", text="nothing special here")
    percent = await _make_text(store, title="Budget", text="saved 50% of it_all, a/b")
    assert (await _found(store, "%"))[0] == {percent}, "% matches a percent sign, not everything"
    assert (await _found(store, "_"))[0] == {percent}, "_ matches an underscore, not any letter"
    assert (await _found(store, "/"))[0] == {percent}, "the escape character is a literal too"
    assert (await _found(store, "50\\"))[0] == set(), "a backslash is a literal too"
    assert plain not in (await _found(store, "%"))[0]


async def test_a_search_never_matches_a_deleted_document_and_keeps_the_state_filter(store):
    live = await _make_text(store, title="Garden plan", text="tomatoes")
    shelved = await _make_text(store, title="Garden archive", text="tomatoes")
    gone = await _make_text(store, title="Garden deleted", text="tomatoes")
    assert await store.set_state(document_id=shelved, state="archived", set_by="mei")
    assert await store.set_state(document_id=gone, state="deleted", set_by="mei")

    assert await _found(store, "garden") == ({live}, 1)
    assert await _found(store, "garden", state="archived") == ({shelved}, 1)
    # A deleted document matches under no filter, not even by its text, and for no one.
    for state in ("active", "archived"):
        for person in ("mei", "ken"):
            found, _ = await _found(store, "tomatoes", person=person, state=state)
            assert gone not in found
    # Restored, it matches again: nothing was erased.
    assert await store.set_state(document_id=gone, state="active", set_by="mei")
    assert (await _found(store, "tomatoes"))[0] == {live, gone}


async def test_a_search_never_matches_a_document_the_person_may_not_read(store):
    mine = await _make_text(store, title="Secret plan", text="the vault", room=ROOM)
    elsewhere = await _make_text(
        store, title="Secret plan two", text="the vault", room=OTHER_ROOM, created_by="jo"
    )
    own_elsewhere = await _make_text(
        store, title="Secret plan three", text="the vault", room=OTHER_ROOM, created_by="mei"
    )
    rooms = frozenset({ROOM})
    # Mei reads ROOM and her own; the other room's, made by Jo, is not hers.
    found, total = await _found(store, "vault", person="mei", rooms=rooms)
    assert found == {mine, own_elsewhere} and total == 2 and elsewhere not in found
    # Nobody in no room, who made nothing, finds anything.
    assert await _found(store, "vault", person="stranger", rooms=frozenset()) == (set(), 0)


async def test_a_search_pages_and_counts_its_matches(store):
    made = [await _make_text(store, title=f"Trip {i}", text="beach") for i in range(5)]
    await _make_text(store, title="Work", text="spreadsheet")
    page = await store.list_visible(
        person_id="mei", rooms=frozenset({ROOM}), limit=2, offset=2, query="beach"
    )
    assert page.total == 5 and len(page.items) == 2
    assert {d.id for d in page.items} <= set(made)


# ------------------------------------------- who wrote the state
async def test_a_summary_says_who_wrote_the_latest_state_row(store):
    doc = await _make(store)
    assert (await store.get(document_id=doc)).summary.state_by == ""
    await store.set_state(document_id=doc, state="deleted", set_by="mei")
    assert (await store.get(document_id=doc)).summary.state_by == "mei"
    await store.set_state(document_id=doc, state="active", set_by="operator")
    assert (await store.get(document_id=doc)).summary.state_by == "operator"


# ----------------------------------------------- search: folding and whitespace parity
async def test_search_folds_case_the_way_lower_does_on_both_stores(store):
    """Postgres ILIKE folds with lower(); Python's casefold() maps "ß" to "ss", which lower
    does not, so a casefold fake would match a query the real store refuses."""
    strasse = await _make_text(store, title="Straße", text="a street")
    assert (await _found(store, "STRAßE"))[0] == {strasse}
    assert (await _found(store, "strasse"))[0] == set(), "ß is not ss under lower()"
    assert (await _found(store, "STRASSE"))[0] == set()
    assert (await _found(store, "A STREET"))[0] == {strasse}


async def test_search_compares_stored_text_and_query_under_the_same_whitespace(store):
    """The query is collapsed to single spaces; the stored text is collapsed the same way
    before it is compared, so a document whose parts are joined by a newline (the checker
    joins them so) or that carries a doubled space is found by the words as typed."""
    parts = await _make_text(store, title="Parts", text="alpha\nbeta gamma")
    spaced = await _make_text(store, title="Two  spaces", text="delta   epsilon")
    assert (await _found(store, "alpha beta"))[0] == {parts}
    assert (await _found(store, "beta  gamma"))[0] == {parts}
    assert (await _found(store, "delta epsilon"))[0] == {spaced}
    assert (await _found(store, "two spaces"))[0] == {spaced}
