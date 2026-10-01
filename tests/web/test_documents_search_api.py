"""`GET /app/v1/documents?q=`: search is the same list, narrowed (DEC-260), and a row
carries its room's label for the viewers whose rooms include it.

The store-level rules (current revision only, a query is text, a deleted document never
matches) are in the document store's contract suite on both implementations. This file is
the route: it reuses the list's visibility, bounds the query, and labels rooms.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from aiohttp import web

from nora.app.documents_service import room_label, rooms_named
from nora.domain.documents import DOCUMENT_QUERY_MAX, RevisionIn
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
SHA = "a" * 64


def _policy(ken_in_trip: bool = True) -> Policy:
    trip = frozenset({"ken", "mei"}) if ken_in_trip else frozenset({"mei"})
    return Policy(
        conversations=(
            Conversation(
                platform="signal", ids=frozenset({"trip"}), persons=trip, label="Trip planning"
            ),
            Conversation(
                platform="signal", ids=frozenset({"work"}), persons=frozenset({"jo"}),
                label="Work chat",
            ),
        )
    )


class _World:
    def __init__(self) -> None:
        self.policy = _policy()
        self.store = FakeDocumentStore()

    def rooms_of(self, person_id: str) -> frozenset[tuple[str, str]]:
        return rooms_named(self.policy, person_id)

    def label_of(self, platform: str, conversation_id: str) -> str:
        return room_label(self.policy, platform, conversation_id)

    async def make(self, title: str, text: str, *, room: str = "trip", by: str = "ken"):
        document_id = uuid4()
        await self.store.create(
            document_id=document_id, platform="signal", conversation_id=room, created_by=by,
            revision=RevisionIn(
                title=title,
                spec={"version": 1, "root": {"type": "Page", "props": {"title": title},
                                              "children": []}},
                text=text, sha256=SHA, media=(), written_by="tag:1",
            ),
        )
        return document_id


@pytest.fixture
async def world_client(aiohttp_client):
    world = _World()
    app = web.Application()
    register_web_routes(
        app,
        store=FakeRecordStore(),
        honcho=FakeMemoryStore(),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"ken": KEN, "mei": MEI, "jo": JO}, index={}),
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
        room_label=world.label_of,
    )
    return await aiohttp_client(app), world


async def _ids(client, person: str, query: str = "", extra: str = "") -> list[str]:
    resp = await client.get("/app/v1/documents", params={"personId": person, "q": query} | (
        {"state": extra} if extra else {}
    ))
    assert resp.status == 200
    return [d["id"] for d in (await resp.json())["documents"]]


async def test_search_narrows_the_list_by_title_and_text_ignoring_case(world_client):
    client, world = world_client
    a = await world.make("Lisbon trip", "flights and hotels")
    b = await world.make("Packing list", "bring a LISBON guidebook")
    await world.make("Tokyo", "ramen")
    assert sorted(await _ids(client, "mei", "lisbon")) == sorted([str(a), str(b)])
    body = await (await client.get("/app/v1/documents?personId=mei&q=%20LISBON%20")).json()
    assert body["query"] == "LISBON" and body["total"] == 2


async def test_search_reaches_only_what_the_person_may_read(world_client):
    client, world = world_client
    mine = await world.make("Budget", "the vault", room="trip")
    secret = await world.make("Budget work", "the vault", room="work", by="jo")
    # Mei is in the trip room only: Jo's work document is never a match for her.
    assert await _ids(client, "mei", "vault") == [str(mine)]
    assert await _ids(client, "jo", "vault") == [str(secret)]
    # Ken made `mine` and is in the trip room: still only what he reads.
    assert await _ids(client, "ken", "vault") == [str(mine)]


async def test_a_deleted_document_never_matches_and_archived_only_under_its_filter(world_client):
    client, world = world_client
    live = await world.make("Garden plan", "tomatoes")
    shelved = await world.make("Garden archive", "tomatoes")
    gone = await world.make("Garden gone", "tomatoes")
    await world.store.set_state(document_id=shelved, state="archived", set_by="mei")
    await world.store.set_state(document_id=gone, state="deleted", set_by="mei")
    assert await _ids(client, "mei", "tomatoes") == [str(live)]
    assert await _ids(client, "mei", "tomatoes", extra="archived") == [str(shelved)]
    assert str(gone) not in await _ids(client, "ken", "garden") + await _ids(
        client, "ken", "garden", extra="archived"
    )


async def test_a_blank_search_is_the_whole_list_and_a_long_one_is_refused(world_client):
    client, world = world_client
    await world.make("One", "a")
    await world.make("Two", "b")
    assert len(await _ids(client, "mei", "   ")) == 2
    too_long = await client.get(
        "/app/v1/documents", params={"personId": "mei", "q": "x" * (DOCUMENT_QUERY_MAX + 1)}
    )
    assert too_long.status == 400
    just_fits = await client.get(
        "/app/v1/documents", params={"personId": "mei", "q": "x" * DOCUMENT_QUERY_MAX}
    )
    assert just_fits.status == 200 and (await just_fits.json())["documents"] == []


async def test_a_row_carries_its_rooms_label_for_a_viewer_in_that_room(world_client):
    client, world = world_client
    await world.make("Lisbon", "x")
    row = (await (await client.get("/app/v1/documents?personId=mei")).json())["documents"][0]
    assert row["roomLabel"] == "Trip planning"
    assert "trip" not in str(row).lower().replace("trip planning", "")  # no chat id


async def test_a_creator_who_left_the_room_is_not_told_what_it_is_called(world_client):
    client, world = world_client
    doc = await world.make("Lisbon", "x", by="ken")
    world.policy = _policy(ken_in_trip=False)
    listing = (await (await client.get("/app/v1/documents?personId=ken")).json())["documents"]
    assert [d["id"] for d in listing] == [str(doc)] and listing[0]["roomLabel"] == ""
    detail = await (await client.get(f"/app/v1/documents/{doc}?personId=ken")).json()
    assert detail["roomLabel"] == ""
    inside = await (await client.get(f"/app/v1/documents/{doc}?personId=mei")).json()
    assert inside["roomLabel"] == "Trip planning"


@pytest.mark.parametrize("bad", ["a\x00b", "\x00", "tab\there", "line\nbreak", "\x1f", "x\x7f"])
async def test_a_control_character_in_q_is_a_400_never_a_500(world_client, bad):
    """A NUL sent straight to Nora's API (not through the portal's proxy) reached Postgres,
    which refuses it in text: a 500. A control character is as invalid as an over-long q."""
    client, world = world_client
    await world.make("One", "a")
    resp = await client.get("/app/v1/documents", params={"personId": "mei", "q": bad})
    assert resp.status == 400 and (await resp.json()) == {"error": "invalid q"}
