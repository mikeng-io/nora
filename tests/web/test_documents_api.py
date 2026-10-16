"""`/app/v1/documents`: what the portal may read, and to whom (ADR 0020).

The rooms callable is the real `rooms_named` over a real `Policy`, so these routes
exercise the actual rule (created it, or a room that names you; a `"*"` room names
nobody), not a stub that agrees with the test by construction.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from aiohttp import web

from nora.app.documents_service import rooms_named
from nora.domain.documents import RevisionIn, handle_of
from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.policy import Conversation, Policy
from nora.testing import FakeDocumentStore, FakeMemoryStore, FakeRecordStore
from nora.testing.fakes import FakeBlobStore
from nora.testing.reminders import FakeReminderStore
from nora.web.api import register_web_routes
from tests.conftest import build_test_doctrine_store
from tests.web.test_api import NO_SKILLS_DIR, _fake_events

KEN = PersonRef(person_id="ken", name="Ken", honcho_peer_id="ken")
MEI = PersonRef(person_id="mei", name="Mei", honcho_peer_id="mei")
JO = PersonRef(person_id="jo", name="Jo", honcho_peer_id="jo")
SAM = PersonRef(person_id="sam", name="Sam", honcho_peer_id="sam")
SHA = "a" * 64


def _rev(title: str, spec: dict[str, Any] | None = None, media: tuple[str, ...] = ()) -> RevisionIn:
    return RevisionIn(
        title=title,
        spec=spec
        or {"version": 1, "root": {"type": "Page", "props": {"title": title}, "children": []}},
        text=title,
        sha256=SHA,
        media=media,
        written_by="tag:1",
    )


def _policy(mei_in_r1: bool = True) -> Policy:
    r1 = frozenset({"ken", "mei"}) if mei_in_r1 else frozenset({"ken"})
    return Policy(
        conversations=(
            Conversation(platform="signal", ids=frozenset({"r1"}), persons=r1),
            Conversation(platform="signal", ids=frozenset({"open"}), persons=None),
            Conversation(platform="signal", ids=frozenset({"r3"}), persons=frozenset({"jo"})),
        )
    )


class _World:
    """One process's documents: the seeded ids, and a policy a test can change."""

    def __init__(self) -> None:
        self.policy = _policy()
        self.store = FakeDocumentStore()
        self.ids: dict[str, UUID] = {}

    def rooms_of(self, person_id: str) -> frozenset[tuple[str, str]]:
        return rooms_named(self.policy, person_id)


async def _seed(world: _World) -> None:
    for key, room, by, title in (
        ("a", "r1", "ken", "Lisbon"),
        ("b", "open", "ken", "Open room note"),
        ("c", "r3", "jo", "Jo's plan"),
    ):
        world.ids[key] = uuid4()
        await world.store.create(
            document_id=world.ids[key],
            platform="signal",
            conversation_id=room,
            created_by=by,
            revision=_rev(title, media=(SHA,) if key == "a" else ()),
        )


@pytest.fixture
async def world_client(aiohttp_client):
    world = _World()
    await _seed(world)
    app = web.Application()
    register_web_routes(
        app,
        store=FakeRecordStore(),
        honcho=FakeMemoryStore(),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"ken": KEN, "mei": MEI, "jo": JO, "sam": SAM}, index={}),
        ai_peer="nora",
        started_at=0.0,
        games_client=None,
        presence=None,
        presence_circles=(),
        skills_dir=NO_SKILLS_DIR,
        events=_fake_events(),
        doctrine_store=build_test_doctrine_store(),
        documents=world.store,
        rooms_of=world.rooms_of,
    )
    return await aiohttp_client(app), world


async def _titles(client, person: str) -> list[str]:
    resp = await client.get(f"/app/v1/documents?personId={person}")
    assert resp.status == 200
    return [d["title"] for d in (await resp.json())["documents"]]


# ------------------------------------------------------------------------ list
async def test_a_person_a_room_names_sees_its_documents_and_no_others(world_client):
    client, _ = world_client
    assert await _titles(client, "mei") == ["Lisbon"]
    assert sorted(await _titles(client, "jo")) == ["Jo's plan"]
    assert await _titles(client, "sam") == []


async def test_a_creator_also_sees_what_they_made_in_a_room_that_admits_everyone(world_client):
    client, _ = world_client
    assert sorted(await _titles(client, "ken")) == ["Lisbon", "Open room note"]
    # The open room names nobody, so nobody else reads what was made there.
    assert "Open room note" not in await _titles(client, "mei")


async def test_a_listing_carries_what_the_portal_shows_and_no_chat_id(world_client):
    client, world = world_client
    body = await (await client.get("/app/v1/documents?personId=mei")).json()
    assert body["total"] == 1 and body["page"] == 1
    item = body["documents"][0]
    assert item["id"] == str(world.ids["a"])
    assert item["createdBy"] == {"personId": "ken", "name": "Ken"}
    assert item["revision"] == 1 and item["platform"] == "signal"
    assert item["createdAt"].endswith("Z") and item["updatedAt"].endswith("Z")
    assert "r1" not in str(body) and "conversation" not in str(body).lower()


async def test_a_listing_pages_and_caps_the_page_size(world_client):
    client, _ = world_client
    body = await (await client.get("/app/v1/documents?personId=ken&pageSize=1&page=2")).json()
    assert body["pageSize"] == 1 and body["page"] == 2 and body["total"] == 2
    assert len(body["documents"]) == 1
    huge = await (await client.get("/app/v1/documents?personId=ken&pageSize=100000")).json()
    assert huge["pageSize"] == 50
    assert (await client.get("/app/v1/documents?personId=ken&pageSize=x")).status == 400
    # A 30-digit page is capped, not a 500.
    far = await client.get(f"/app/v1/documents?personId=ken&page={'9' * 30}")
    assert far.status == 200
    body = await far.json()
    assert body["page"] == 100_000 and body["documents"] == [] and body["total"] == 2


async def test_the_list_needs_a_person_this_deployment_knows(world_client):
    client, _ = world_client
    for query in ("", "?personId=", "?personId=stranger"):
        resp = await client.get(f"/app/v1/documents{query}")
        assert resp.status == 400
        assert (await resp.json())["status"] == "refused"


# ---------------------------------------------------------------------- detail
async def test_a_reader_gets_the_spec_and_the_pictures_it_names(world_client):
    client, world = world_client
    resp = await client.get(f"/app/v1/documents/{world.ids['a']}?personId=mei")
    assert resp.status == 200
    body = await resp.json()
    assert body["title"] == "Lisbon"
    assert body["document"]["version"] == 1
    assert body["media"] == [SHA]
    assert body["createdBy"]["name"] == "Ken"


async def test_a_document_you_may_not_read_is_not_found_never_forbidden(world_client):
    client, world = world_client
    hidden = await client.get(f"/app/v1/documents/{world.ids['a']}?personId=jo")
    missing = await client.get(f"/app/v1/documents/{uuid4()}?personId=jo")
    assert hidden.status == missing.status == 404
    assert await hidden.json() == await missing.json() == {"status": "not_found"}
    open_room = await client.get(f"/app/v1/documents/{world.ids['b']}?personId=mei")
    assert open_room.status == 404


async def test_the_detail_refuses_a_bad_id_and_an_unknown_person(world_client):
    client, world = world_client
    assert (await client.get("/app/v1/documents/not-a-uuid?personId=mei")).status == 400
    assert (await client.get(f"/app/v1/documents/{world.ids['a']}")).status == 400
    assert (await client.get(f"/app/v1/documents/{world.ids['a']}?personId=stranger")).status == 400


async def test_removing_a_person_from_a_room_revokes_access_on_the_next_read(world_client):
    # The rooms are read from the policy object at the moment of each request, never
    # cached. The object itself is loaded at startup (policy.toml needs a restart), so in
    # production a removal takes effect at the next restart.
    client, world = world_client
    url = f"/app/v1/documents/{world.ids['a']}?personId=mei"
    assert (await client.get(url)).status == 200
    world.policy = _policy(mei_in_r1=False)
    assert (await client.get(url)).status == 404
    assert await _titles(client, "mei") == []


async def test_a_reader_sees_the_current_revision(world_client):
    client, world = world_client
    outcome = await world.store.add_revision(
        document_id=world.ids["a"],
        base_revision=1,
        revision=_rev("Lisbon v2"),
    )
    assert outcome.status == "ok"
    body = await (await client.get(f"/app/v1/documents/{world.ids['a']}?personId=mei")).json()
    assert body["title"] == "Lisbon v2" and body["revision"] == 2
    assert body["updatedAt"] > body["createdAt"]


# -------------------------------------------------------------------- unwired
async def test_a_deployment_without_documents_answers_503_not_an_empty_list(aiohttp_client):
    app = web.Application()
    register_web_routes(
        app,
        store=FakeRecordStore(),
        honcho=FakeMemoryStore(),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"ken": KEN}, index={}),
        ai_peer="nora",
        started_at=0.0,
        games_client=None,
        presence=None,
        presence_circles=(),
        skills_dir=NO_SKILLS_DIR,
        events=_fake_events(),
        doctrine_store=build_test_doctrine_store(),
    )
    client = await aiohttp_client(app)
    for path in ("/app/v1/documents?personId=ken", f"/app/v1/documents/{uuid4()}?personId=ken"):
        resp = await client.get(path)
        assert resp.status == 503
        assert (await resp.json())["status"] == "unavailable"


# ---------------------------------------------------------------------- picture bytes
# A real 1x1 PNG, GIF, JPEG header and WebP header: the route reads the type off the bytes.
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0"
    b"\xa0\x00\x00\x00\x00IEND\xaeB`\x82"
)
GIF = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 \x0e\x00\x00\x000\x01\x00\x9d\x01*\x01\x00\x01\x00"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<!doctype html><script>alert(1)</script>"


class _BrokenBlobs:
    """A `BlobStore` whose disk is unreachable: `get` raises, like the real one would."""

    async def put(self, *, data: bytes) -> str:
        raise RuntimeError("disk")

    async def get(self, *, key: str) -> bytes | None:
        raise RuntimeError("disk")


class _MediaWorld(_World):
    def __init__(self) -> None:
        super().__init__()
        self.blobs = FakeBlobStore()
        self.shas: dict[str, str] = {}


async def _media_app(aiohttp_client, world: _MediaWorld, *, blobs: Any = "world"):
    for name, data in (
        ("png", PNG), ("gif", GIF), ("jpeg", JPEG), ("webp", WEBP), ("svg", SVG),
        ("html", HTML), ("unnamed", PNG + b"\x00"),
    ):
        world.shas[name] = await world.blobs.put(data=data)
    named = tuple(world.shas[n] for n in ("png", "gif", "jpeg", "webp", "svg", "html"))
    world.ids["m"] = uuid4()
    await world.store.create(
        document_id=world.ids["m"], platform="signal", conversation_id="r1", created_by="ken",
        revision=_rev("Pictures", media=named),
    )
    app = web.Application()
    register_web_routes(
        app,
        store=FakeRecordStore(),
        honcho=FakeMemoryStore(),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"ken": KEN, "mei": MEI, "jo": JO, "sam": SAM}, index={}),
        ai_peer="nora",
        started_at=0.0,
        games_client=None,
        presence=None,
        presence_circles=(),
        skills_dir=NO_SKILLS_DIR,
        events=_fake_events(),
        doctrine_store=build_test_doctrine_store(),
        documents=world.store,
        rooms_of=world.rooms_of,
        blobs=world.blobs if blobs == "world" else blobs,
    )
    return await aiohttp_client(app)


@pytest.fixture
async def media_client(aiohttp_client):
    world = _MediaWorld()
    return await _media_app(aiohttp_client, world), world


def _url(world: _MediaWorld, sha: str, person: str = "mei", doc: str = "m") -> str:
    return f"/app/v1/documents/{world.ids[doc]}/media/{sha}?personId={person}"


@pytest.mark.parametrize(
    ("name", "data", "media_type"),
    [
        ("png", PNG, "image/png"),
        ("gif", GIF, "image/gif"),
        ("jpeg", JPEG, "image/jpeg"),
        ("webp", WEBP, "image/webp"),
    ],
)
async def test_a_member_reads_each_raster_picture_the_document_names(
    media_client, name, data, media_type
):
    client, world = media_client
    resp = await client.get(_url(world, world.shas[name]))
    assert resp.status == 200
    assert resp.content_type == media_type
    assert await resp.read() == data


async def test_a_picture_is_served_so_that_it_can_never_run(media_client):
    client, world = media_client
    resp = await client.get(_url(world, world.shas["png"]))
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in resp.headers["Content-Security-Policy"]
    assert resp.headers["Cache-Control"] == "private, no-store"


async def test_a_person_the_room_does_not_name_cannot_read_its_pictures(media_client):
    client, world = media_client
    for person in ("sam", "jo"):
        resp = await client.get(_url(world, world.shas["png"], person=person))
        assert resp.status == 404, person
    # Not by id alone: the creator still can.
    assert (await client.get(_url(world, world.shas["png"], person="ken"))).status == 200


async def test_a_picture_the_document_does_not_name_is_not_served(media_client):
    client, world = media_client
    # The bytes exist in the store, and the person may read this document: still no.
    resp = await client.get(_url(world, world.shas["unnamed"]))
    assert resp.status == 404


@pytest.mark.parametrize("name", ["svg", "html"])
async def test_bytes_that_are_not_a_raster_picture_are_never_served(media_client, name):
    client, world = media_client
    # Named by the document and readable by the viewer; the bytes themselves refuse.
    resp = await client.get(_url(world, world.shas[name]))
    assert resp.status == 404
    assert b"alert" not in await resp.read()


async def test_the_address_must_be_the_whole_sha_in_lower_case(media_client):
    client, world = media_client
    sha = world.shas["png"]
    for bad in (sha[:12], sha.upper(), sha + "0", "../" + sha, ""):
        resp = await client.get(f"/app/v1/documents/{world.ids['m']}/media/{bad}?personId=mei")
        assert resp.status in (404, 400), bad
        assert resp.content_type != "image/png"


async def test_an_unreadable_document_and_an_unknown_one_answer_alike(media_client):
    client, world = media_client
    hidden = await client.get(_url(world, world.shas["png"], person="sam"))
    unknown = await client.get(
        f"/app/v1/documents/{uuid4()}/media/{world.shas['png']}?personId=mei"
    )
    assert hidden.status == unknown.status == 404
    assert await hidden.json() == await unknown.json()


async def test_a_blob_store_that_cannot_be_read_is_unavailable_never_not_found(aiohttp_client):
    world = _MediaWorld()
    client = await _media_app(aiohttp_client, world, blobs=_BrokenBlobs())
    resp = await client.get(_url(world, world.shas["png"]))
    assert resp.status == 503


async def test_with_no_blob_store_a_picture_is_unavailable(aiohttp_client):
    world = _MediaWorld()
    client = await _media_app(aiohttp_client, world, blobs=None)
    assert (await client.get(_url(world, world.shas["png"]))).status == 503


async def test_the_picture_route_needs_a_known_person_and_a_uuid(media_client):
    client, world = media_client
    sha = world.shas["png"]
    assert (await client.get(f"/app/v1/documents/{world.ids['m']}/media/{sha}")).status == 400
    assert (await client.get(_url(world, sha, person="nobody"))).status == 400
    bad_id = await client.get(f"/app/v1/documents/not-a-uuid/media/{sha}?personId=mei")
    assert bad_id.status == 400


async def test_a_removed_picture_is_not_found_not_an_error(media_client):
    client, world = media_client
    del world.blobs.blobs[world.shas["png"]]
    assert (await client.get(_url(world, world.shas["png"]))).status == 404


# ------------------------------------------------------------------ a link's handle
async def _seed_prefixed(world: _World, tail: str, room: str, by: str, title: str) -> UUID:
    document_id = UUID("0123456789ab" + tail.ljust(20, "0"))
    await world.store.create(
        document_id=document_id, platform="signal", conversation_id=room,
        created_by=by, revision=_rev(title),
    )
    return document_id


async def test_a_link_handle_opens_the_document_the_person_reads(world_client):
    client, world = world_client
    handle = world.ids["a"].hex[:12]
    resp = await client.get(f"/app/v1/documents/{handle}?personId=mei")
    assert resp.status == 200
    body = await resp.json()
    assert body["id"] == str(world.ids["a"]) and body["title"] == "Lisbon"


async def test_a_handle_is_resolved_among_what_the_person_reads_so_it_is_no_oracle(world_client):
    client, world = world_client
    mine = await _seed_prefixed(world, "1", "r1", "ken", "Mine")  # mei's room r1 reads it
    theirs = await _seed_prefixed(world, "2", "r3", "jo", "Theirs")  # only jo reads it
    assert mine.hex[:12] == theirs.hex[:12]
    handle = mine.hex[:12]
    for person, expected in (("mei", mine), ("jo", theirs)):
        resp = await client.get(f"/app/v1/documents/{handle}?personId={person}")
        assert (await resp.json())["id"] == str(expected)
    # A person who reads neither, and a handle that matches nothing, answer identically.
    miss = await client.get("/app/v1/documents/ffffffffffff?personId=sam")
    hidden = await client.get(f"/app/v1/documents/{handle}?personId=sam")
    assert miss.status == hidden.status == 404
    assert await miss.json() == await hidden.json() == {"status": "not_found"}


async def test_a_handle_two_readable_documents_share_is_the_same_404_never_picked(world_client):
    client, world = world_client
    await _seed_prefixed(world, "1", "r1", "ken", "One")
    await _seed_prefixed(world, "2", "r1", "ken", "Two")  # mei reads both (room r1)
    clash = await client.get("/app/v1/documents/0123456789ab?personId=mei")
    miss = await client.get("/app/v1/documents/ffffffffffff?personId=mei")
    assert clash.status == miss.status == 404
    assert await clash.json() == await miss.json() == {"status": "not_found"}


async def test_what_is_neither_an_id_nor_a_handle_is_refused_before_any_lookup(world_client):
    client, _ = world_client
    for bad in ("0123456789AB", "0123456789a", "0123456789abc", "%25", "_" * 12, "0123456789a%25"):
        resp = await client.get(f"/app/v1/documents/{bad}?personId=mei")
        assert resp.status == 400, bad


# ------------------------------------------------------------------- lifecycle
async def _set_state(client, doc: UUID, state: Any, person: str = "mei"):
    return await client.post(
        f"/app/v1/documents/{doc}/state?personId={person}", json={"state": state}
    )


async def test_an_archived_document_is_off_the_default_list_and_under_archived(world_client):
    client, world = world_client
    resp = await _set_state(client, world.ids["a"], "archived")
    assert resp.status == 200
    body = await resp.json()
    assert body["state"] == "archived" and body["id"] == str(world.ids["a"])
    assert await _titles(client, "mei") == []
    shelf = await (await client.get("/app/v1/documents?personId=mei&state=archived")).json()
    assert [d["title"] for d in shelf["documents"]] == ["Lisbon"]
    assert shelf["state"] == "archived" and shelf["documents"][0]["state"] == "archived"
    # Still opens by its link, and says it is archived.
    detail = await client.get(f"/app/v1/documents/{world.ids['a']}?personId=mei")
    assert detail.status == 200 and (await detail.json())["state"] == "archived"
    # Deleted is never a listing.
    assert (await client.get("/app/v1/documents?personId=mei&state=deleted")).status == 400


async def test_a_deleted_document_is_404_on_read_by_id_handle_and_list(world_client):
    client, world = world_client
    assert (await _set_state(client, world.ids["a"], "deleted", person="ken")).status == 200
    missing = await client.get(f"/app/v1/documents/{uuid4()}?personId=mei")
    for person in ("mei", "ken"):  # a member and the creator alike
        by_id = await client.get(f"/app/v1/documents/{world.ids['a']}?personId={person}")
        by_handle = await client.get(
            f"/app/v1/documents/{handle_of(world.ids['a'])}?personId={person}"
        )
        assert by_id.status == by_handle.status == 404
        assert await by_id.json() == await missing.json() == {"status": "not_found"}
    assert await _titles(client, "mei") == []
    archived = await (await client.get("/app/v1/documents?personId=mei&state=archived")).json()
    assert archived["documents"] == []
    # The portal cannot restore it: it reads like a document that never existed.
    restore = await _set_state(client, world.ids["a"], "active", person="ken")
    assert restore.status == 404
    # Nothing was erased.
    assert (await world.store.get(document_id=world.ids["a"])).summary.title == "Lisbon"


async def test_restore_from_archived_returns_it_to_the_default_list(world_client):
    client, world = world_client
    await _set_state(client, world.ids["a"], "archived")
    back = await _set_state(client, world.ids["a"], "active")
    assert back.status == 200 and (await back.json())["state"] == "active"
    assert await _titles(client, "mei") == ["Lisbon"]


async def test_a_person_who_may_not_read_a_document_may_not_change_its_state(world_client):
    client, world = world_client
    # Jo is in no room that made "a"; Mei is not named by the open room "b".
    for doc, person in ((world.ids["a"], "jo"), (world.ids["b"], "mei"), (uuid4(), "mei")):
        resp = await _set_state(client, doc, "deleted", person=person)
        assert resp.status == 404 and await resp.json() == {"status": "not_found"}
    assert (await world.store.get(document_id=world.ids["a"])).summary.state == "active"
    assert (await world.store.get(document_id=world.ids["b"])).summary.state == "active"
    assert world.store._states == {}


async def test_the_state_route_refuses_a_bad_state_a_bad_body_and_an_unknown_person(
    world_client,
):
    client, world = world_client
    doc = world.ids["a"]
    for state in ("purged", "", None, 3):
        assert (await _set_state(client, doc, state)).status == 400
    bad_body = await client.post(
        f"/app/v1/documents/{doc}/state?personId=mei", data=b"not json",
        headers={"content-type": "application/json"},
    )
    assert bad_body.status == 400
    assert (await _set_state(client, doc, "archived", person="stranger")).status == 400
    no_person = await client.post(f"/app/v1/documents/{doc}/state", json={"state": "archived"})
    assert no_person.status == 400
    assert (await client.post(
        "/app/v1/documents/not-a-uuid/state?personId=mei", json={"state": "archived"}
    )).status == 400
    assert world.store._states == {}


async def test_a_deleted_documents_pictures_are_404(media_client):
    client, world = media_client
    sha = world.shas["png"]
    assert (await client.get(_url(world, sha))).status == 200
    await world.store.set_state(document_id=world.ids["m"], state="deleted", set_by="ken")
    resp = await client.get(_url(world, sha))
    assert resp.status == 404 and await resp.json() == {"status": "not_found"}
