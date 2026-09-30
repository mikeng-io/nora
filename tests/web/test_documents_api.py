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
from nora.domain.documents import RevisionIn
from nora.domain.models import PersonRef
from nora.persons import Persons
from nora.policy import Conversation, Policy
from nora.testing import FakeDocumentStore, FakeMemoryStore, FakeRecordStore
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
    # production a removal takes effect at the next restart: design/artifact §25.
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
