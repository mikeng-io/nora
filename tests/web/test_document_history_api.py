"""`/app/v1/documents/{id}/revisions`: a document's history, one old revision, and a
restore that publishes an old spec as a NEW revision.

The world is `test_documents_api`'s: the real `rooms_named` over a real `Policy`, so the
history is gated by the same rule as the document itself, not by a stub."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from aiohttp import web

from nora.documents.check import Checked
from nora.domain.documents import RevisionIn
from nora.persons import Persons
from nora.testing import FakeMemoryStore, FakeRecordStore
from nora.testing.fakes import FakeBlobStore
from nora.testing.reminders import FakeReminderStore
from nora.web.api import register_web_routes
from tests.conftest import build_test_doctrine_store
from tests.web.test_api import NO_SKILLS_DIR, _fake_events
from tests.web.test_documents_api import JO, KEN, MEI, PNG, SAM, _World

OLD_PICTURE = "c" * 64  # replaced by PNG's real sha in the fixture


def _spec(title: str, media: str | None = None) -> dict[str, Any]:
    children: list[dict[str, Any]] = [
        {"type": "Section", "props": {"title": title}, "children": [
            {"type": "Markdown", "props": {"text": f"text of {title}"}},
        ]},
    ]
    if media:
        children.append({"type": "Image", "props": {"media": media, "alt": "a", "caption": "c"}})
    return {"version": 1, "root": {"type": "Page", "props": {"title": title}, "children": children}}


def _rev(title: str, media: str | None = None, sha: str = "a" * 64) -> RevisionIn:
    return RevisionIn(
        title=title, spec=_spec(title, media), text=title, sha256=sha,
        media=(media,) if media else (), written_by="tag:7",
    )


class _HistoryWorld(_World):
    def __init__(self) -> None:
        super().__init__()
        self.blobs = FakeBlobStore()
        self.picture = ""


@pytest.fixture
async def history_client(aiohttp_client):
    """Ken's "Lisbon" in room r1 (Ken and Mei) at revision 3: revision 1 shows a picture
    the later revisions dropped. Jo's plan is in r3 (Jo only)."""
    world = _HistoryWorld()
    world.picture = await world.blobs.put(data=PNG)
    world.ids["a"] = uuid4()
    await world.store.create(
        document_id=world.ids["a"], platform="signal", conversation_id="r1",
        created_by="ken", revision=_rev("Lisbon v1", media=world.picture, sha="1" * 64),
    )
    for base, title, sha in ((1, "Lisbon v2", "2" * 64), (2, "Lisbon v3", "3" * 64)):
        out = await world.store.add_revision(
            document_id=world.ids["a"], base_revision=base, revision=_rev(title, sha=sha),
        )
        assert out.status == "ok"
    world.ids["c"] = uuid4()
    await world.store.create(
        document_id=world.ids["c"], platform="signal", conversation_id="r3",
        created_by="jo", revision=_rev("Jo's plan"),
    )
    # Made in r1 by Sam, whom r1 does not name: Sam reads it as its creator, Mei as r1's.
    world.ids["d"] = uuid4()
    await world.store.create(
        document_id=world.ids["d"], platform="signal", conversation_id="r1",
        created_by="sam", revision=_rev("Sam's note"),
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
        blobs=world.blobs,
        room_label=lambda platform, conversation_id: {"r1": "Trip room", "r3": "Jo's room"}.get(
            conversation_id, ""
        ),
    )
    return await aiohttp_client(app), world


def _h(world: _HistoryWorld, person: str, doc: str = "a") -> str:
    return f"/app/v1/documents/{world.ids[doc]}/revisions?personId={person}"


def _one(world: _HistoryWorld, n: int | str, person: str = "mei", doc: str = "a") -> str:
    return f"/app/v1/documents/{world.ids[doc]}/revisions/{n}?personId={person}"


async def _restore(client, world: _HistoryWorld, n: int, base: Any, person: str = "mei"):
    return await client.post(
        f"/app/v1/documents/{world.ids['a']}/revisions/{n}/restore?personId={person}",
        json={"baseRevision": base},
    )


async def _numbers(world: _HistoryWorld) -> list[tuple[int, str]]:
    return [(h.revision, h.title) for h in await world.store.history(document_id=world.ids["a"])]


# ------------------------------------------------------------------- the list
async def test_history_lists_every_revision_newest_first_to_a_reader(history_client):
    client, world = history_client
    resp = await client.get(_h(world, "mei"))
    assert resp.status == 200
    body = await resp.json()
    assert body["id"] == str(world.ids["a"]) and body["revision"] == 3
    assert body["state"] == "active" and body["title"] == "Lisbon v3"
    assert [(r["revision"], r["title"], r["sha256"]) for r in body["revisions"]] == [
        (3, "Lisbon v3", "3" * 64), (2, "Lisbon v2", "2" * 64), (1, "Lisbon v1", "1" * 64),
    ]
    assert all(r["recordedAt"].endswith("Z") for r in body["revisions"])
    # A revision Nora wrote records a tag, not a person: shown as hers, never a guess.
    assert {r["writtenBy"]["kind"] for r in body["revisions"]} == {"nora"}


async def test_history_is_the_same_404_as_an_unknown_id_for_whoever_may_not_read_it(
    history_client,
):
    client, world = history_client
    hidden = await client.get(_h(world, "jo"))
    nobody = await client.get(_h(world, "sam"))
    missing = await client.get(f"/app/v1/documents/{uuid4()}/revisions?personId=mei")
    assert hidden.status == nobody.status == missing.status == 404
    assert await hidden.json() == await missing.json() == {"status": "not_found"}
    # And the room's own member, and its creator, read it.
    assert (await client.get(_h(world, "ken"))).status == 200
    assert (await client.get(_h(world, "jo", doc="c"))).status == 200


async def test_history_of_a_deleted_document_is_404_and_of_an_archived_one_is_readable(
    history_client,
):
    client, world = history_client
    await world.store.set_state(document_id=world.ids["a"], state="archived", set_by="ken")
    archived = await client.get(_h(world, "mei"))
    assert archived.status == 200 and (await archived.json())["state"] == "archived"
    assert (await client.get(_one(world, 1))).status == 200
    await world.store.set_state(document_id=world.ids["a"], state="deleted", set_by="ken")
    assert (await client.get(_h(world, "mei"))).status == 404
    assert (await client.get(_one(world, 1))).status == 404


async def test_history_needs_a_known_person_and_a_uuid(history_client):
    client, world = history_client
    assert (await client.get(f"/app/v1/documents/{world.ids['a']}/revisions")).status == 400
    assert (await client.get(_h(world, "stranger"))).status == 400
    assert (await client.get("/app/v1/documents/nope/revisions?personId=mei")).status == 400


# ---------------------------------------------------------------- one revision
async def test_an_old_revision_is_its_own_spec_and_reading_it_changes_nothing(history_client):
    client, world = history_client
    resp = await client.get(_one(world, 1))
    assert resp.status == 200
    body = await resp.json()
    assert body["revision"] == 1 and body["currentRevision"] == 3
    assert body["title"] == "Lisbon v1"
    assert body["document"]["root"]["props"]["title"] == "Lisbon v1"
    assert body["media"] == [world.picture]
    assert body["writtenBy"] == {"kind": "nora"}
    # Read-only: the document is still at revision 3, with revision 3's spec.
    current = await (await client.get(f"/app/v1/documents/{world.ids['a']}?personId=mei")).json()
    assert current["revision"] == 3 and current["title"] == "Lisbon v3"
    assert await _numbers(world) == [(1, "Lisbon v1"), (2, "Lisbon v2"), (3, "Lisbon v3")]


async def test_a_revision_the_document_never_had_is_404_and_a_bad_number_is_400(history_client):
    client, world = history_client
    assert (await client.get(_one(world, 4))).status == 404
    assert (await client.get(_one(world, 1, person="jo"))).status == 404
    for bad in ("0", "-1", "x", "1.5", "99999999999"):
        assert (await client.get(_one(world, bad))).status == 400, bad


async def test_a_picture_only_an_old_revision_names_is_served_to_a_reader_only(history_client):
    client, world = history_client
    url = f"/app/v1/documents/{world.ids['a']}/media/{world.picture}"
    ok = await client.get(f"{url}?personId=mei")
    assert ok.status == 200 and await ok.read() == PNG
    assert (await client.get(f"{url}?personId=jo")).status == 404
    # A picture no revision of THIS document names is still refused.
    other = f"/app/v1/documents/{world.ids['c']}/media/{world.picture}?personId=jo"
    assert (await client.get(other)).status == 404


# --------------------------------------------------------------------- restore
async def test_restore_publishes_the_old_spec_as_a_new_revision_and_keeps_every_old_one(
    history_client,
):
    client, world = history_client
    resp = await _restore(client, world, 1, base=3)
    assert resp.status == 200
    assert await resp.json() == {"status": "ok", "revision": 4}
    assert await _numbers(world) == [
        (1, "Lisbon v1"), (2, "Lisbon v2"), (3, "Lisbon v3"), (4, "Lisbon v1"),
    ]
    old = await world.store.revision(document_id=world.ids["a"], revision=1)
    new = await world.store.revision(document_id=world.ids["a"], revision=4)
    assert old is not None and new is not None
    assert new.spec == old.spec and new.media == old.media
    assert new.written_by == "portal:mei"
    # The history names the person who restored it.
    body = await (await client.get(_h(world, "ken"))).json()
    assert body["revisions"][0]["writtenBy"] == {"kind": "person", "personId": "mei", "name": "Mei"}
    current = await (await client.get(f"/app/v1/documents/{world.ids['a']}?personId=mei")).json()
    assert current["revision"] == 4 and current["title"] == "Lisbon v1"


async def test_restore_from_a_stale_base_conflicts_and_writes_nothing(history_client):
    client, world = history_client
    resp = await _restore(client, world, 1, base=2)
    assert resp.status == 409 and await resp.json() == {"status": "conflict"}
    assert len(await _numbers(world)) == 3


async def test_restore_is_refused_on_an_archived_document_and_404_on_a_deleted_one(
    history_client,
):
    client, world = history_client
    await world.store.set_state(document_id=world.ids["a"], state="archived", set_by="ken")
    resp = await _restore(client, world, 1, base=3)
    assert resp.status == 409 and await resp.json() == {"status": "not_active"}
    await world.store.set_state(document_id=world.ids["a"], state="deleted", set_by="ken")
    assert (await _restore(client, world, 1, base=3)).status == 404
    assert len(await _numbers(world)) == 3


async def test_restore_by_a_person_who_may_not_read_it_is_404_and_writes_nothing(history_client):
    client, world = history_client
    for person in ("jo", "sam"):
        assert (await _restore(client, world, 1, base=3, person=person)).status == 404
    assert len(await _numbers(world)) == 3


async def test_restore_needs_a_base_and_refuses_the_current_revision(history_client):
    client, world = history_client
    for base in (None, "3", 0, True):
        assert (await _restore(client, world, 1, base=base)).status == 400, base
    assert (await _restore(client, world, 3, base=3)).status == 400
    assert (await _restore(client, world, 9, base=3)).status == 404
    assert len(await _numbers(world)) == 3


async def test_restore_of_a_spec_this_build_refuses_is_refused_not_stored(history_client):
    client, world = history_client
    await world.store.add_revision(
        document_id=world.ids["a"], base_revision=3,
        revision=RevisionIn(
            title="broken", spec={"version": 9, "root": {"type": "Page"}}, text="",
            sha256="9" * 64, media=(), written_by="tag:8",
        ),
    )
    await world.store.add_revision(
        document_id=world.ids["a"], base_revision=4, revision=_rev("Lisbon v5", sha="5" * 64),
    )
    resp = await _restore(client, world, 4, base=5)
    assert resp.status == 409 and await resp.json() == {"status": "invalid"}
    assert len(await _numbers(world)) == 5


# ------------------------------------------------- the room label, restore races, the checker
async def test_history_carries_the_room_label_only_for_a_viewer_in_that_room(history_client):
    # The detail payload's rule (`_room_label`): a creator outside the room reads the
    # document and its history, but is not told what the room is called.
    client, world = history_client
    mei = await (await client.get(_h(world, "mei", doc="d"))).json()
    sam = await (await client.get(_h(world, "sam", doc="d"))).json()
    assert mei["roomLabel"] == "Trip room"
    assert sam["roomLabel"] == ""
    one_mei = await (await client.get(_one(world, 1, person="mei", doc="d"))).json()
    one_sam = await (await client.get(_one(world, 1, person="sam", doc="d"))).json()
    assert (one_mei["roomLabel"], one_sam["roomLabel"]) == ("Trip room", "")
    assert "r1" not in str(sam) and "conversation" not in str(sam).lower()


async def test_a_restore_that_races_a_delete_is_a_404_never_a_reason(history_client):
    # Readable when the restore read it, deleted before add_revision took
    # its lock: the viewer gets the unreadable document's 404, not "archived".
    client, world = history_client
    store = world.store
    real = store.add_revision

    async def racing(**kwargs: Any):
        await store.set_state(
            document_id=world.ids["a"], state="deleted", set_by="ken"
        )
        return await real(**kwargs)

    store.add_revision = racing  # type: ignore[method-assign]
    resp = await _restore(client, world, 1, base=3)
    assert resp.status == 404 and await resp.json() == {"status": "not_found"}
    assert len(await _numbers(world)) == 3


async def test_restoring_the_current_revision_of_an_archived_document_says_archived(
    history_client,
):
    client, world = history_client
    await world.store.set_state(document_id=world.ids["a"], state="archived", set_by="ken")
    resp = await _restore(client, world, 3, base=3)
    assert resp.status == 409 and await resp.json() == {"status": "not_active"}


async def test_a_checker_that_fails_is_unavailable_and_stores_nothing(history_client, monkeypatch):
    client, world = history_client
    monkeypatch.setattr(
        "nora.web.document_history.check_document", lambda _spec: Checked(status="error")
    )
    resp = await _restore(client, world, 1, base=3)
    assert resp.status == 503 and (await resp.json())["status"] == "unavailable"
    assert len(await _numbers(world)) == 3
