"""A confirmed bridge send's `platform_ref` must reach the audited own row.

The production gap this guards: a reaction to one of Nora's OWN replies on
WhatsApp or Instagram names the platform's handle (`touch.target.message_id`)
and the durable action row held that handle (`bridge_action.platform_ref`),
but `Deliverer` recorded the row BEFORE the send and the confirmed reply's
`platform_ref` was dropped on the floor — `messages.platform_message_id`
stayed NULL, `find_edited` answered nothing, and the touch orphaned. The
handle now rides `SendResult` → `set_delivery_outcome` → the same row, so a
later same-room reaction, edit or quote resolves through the reader that
already existed.
"""

from __future__ import annotations

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from nora.agent.guards import Deliverer
from nora.doctrine.loader import Doctrine
from nora.domain.actions import ActionOwner
from nora.domain.models import (
    ConversationTarget,
    DeliveryStatus,
    EditTarget,
    NormalizedMessage,
    PersonRef,
    Touch,
)
from nora.ingest.stages import touch as touch_stage
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.platform.bridge.client import BridgeAdapter, BridgeEndpoint
from nora.testing import FakeRecordStore

ROOM = "synthetic-ig-thread"
TARGET = ConversationTarget("instagram", ROOM, "group")
REF = "mid.$synthetic-handle"


def _doctrine() -> Doctrine:
    return Doctrine(
        sections=(), text="<nora_doctrine>be helpful</nora_doctrine>",
        doctrine_text="be helpful", sha256="deadbeef",
        runtime_context="", loaded_at=0.0,
    )


def _deliverer(store: FakeRecordStore, adapter: BridgeAdapter) -> Deliverer:
    return Deliverer(
        store=store, adapter=adapter, target=TARGET, doctrine=_doctrine(),
        canary="CANARY", tag_id="tag-1", nora_sender_id="nora-ig",
        message_cap=5, audience="tag", metrics=Metrics(),
    )


def _persons() -> Persons:
    return Persons(
        by_person={"miles": PersonRef(
            person_id="miles", name="Miles", honcho_peer_id="miles")},
        index={("instagram", "+852111"): "miles"},
    )


async def _server(reply: dict) -> TestServer:
    async def post(request: web.Request) -> web.Response:
        body = await request.json()
        action = body.get("request") or {}
        target = action.get("target") or {}
        owner = body.get("owner") or {}
        if not (
            request.headers.get("Authorization") == "Bearer synthetic"
            and body.get("op") == "send"
            and body.get("action_id")
            and target.get("kind") == "group"
            and target.get("id") == ROOM
            and action.get("text") == "see you at eight"
            and owner.get("kind") == "live_invocation"
            and owner.get("id") == "1"
        ):
            return web.json_response({"state": "rejected"}, status=400)
        return web.json_response(reply)

    app = web.Application()
    app.router.add_post("/v1/actions", post)
    server = TestServer(app)
    await server.start_server()
    return server


async def _deliver_against(server: TestServer, store: FakeRecordStore) -> bool:
    adapter = BridgeAdapter(
        "instagram",
        BridgeEndpoint(str(server.make_url("")).rstrip("/"), "synthetic"))
    try:
        return await _deliverer(store, adapter).deliver("see you at eight")
    finally:
        await adapter.close()


@pytest.mark.parametrize("wrong_field", ["op", "room", "text", "owner"])
async def test_the_fake_bridge_rejects_wrong_action_requests(wrong_field: str):
    server = await _server({"state": "confirmed", "platform_ref": REF})
    body = {
        "action_id": "synthetic-send",
        "owner": {"kind": "live_invocation", "id": "1"},
        "op": "send",
        "request": {
            "target": {"kind": "group", "id": ROOM},
            "text": "see you at eight",
        },
    }
    if wrong_field == "op":
        body["op"] = "react"
    elif wrong_field == "room":
        body["request"]["target"]["id"] = "other-room"
    elif wrong_field == "text":
        body["request"]["text"] = "different message"
    else:
        body["owner"]["id"] = "9999"
    try:
        async with (
            ClientSession(headers={"Authorization": "Bearer synthetic"}) as client,
            client.post(server.make_url("/v1/actions"), json=body) as response,
        ):
            assert response.status == 400
            assert (await response.json())["state"] == "rejected"
    finally:
        await server.close()


def _reaction(target_id: str) -> tuple[NormalizedMessage, Touch]:
    touch = Touch(kind="reaction", emoji="\U0001f44d",
                  target=EditTarget(message_id=target_id))
    message = NormalizedMessage(
        platform="instagram", conversation_id=ROOM, conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=2, body="",
        raw={}, touch=touch)
    return message, touch


async def test_a_confirmed_send_lands_its_native_id_for_a_later_reaction():
    """bridge send response → audited own row → same-room touch resolution."""
    server = await _server({
        "state": "confirmed", "platform_ref": REF,
        "platform_ms": 1754300000000})
    store = FakeRecordStore()
    try:
        assert await _deliver_against(server, store) is True
    finally:
        await server.close()

    # The audited own row carries the platform's handle — the reader the
    # touch stage resolves a target through finds it.
    found = await store.find_edited(
        conversation_id=ROOM, target=EditTarget(message_id=REF), sender_id="")
    assert found is not None and found.is_nora
    assert found.platform_message_id == REF
    # A handle must never resolve across a conversation.
    assert await store.find_edited(
        conversation_id="other-room", target=EditTarget(message_id=REF),
        sender_id="") is None

    # A reaction naming that handle records against the row — not orphaned.
    message, touch = _reaction(REF)
    result = await touch_stage.run(
        message=message, touch=touch, audit=store,
        persons=_persons(), metrics=Metrics())
    assert result.ending == "reaction_recorded"
    assert store.reactions[-1]["message_id"] == found.id


async def test_a_confirmed_send_without_a_ref_preserves_the_prior_behaviour():
    """A bridge that answers confirmed without `platform_ref` stamps no
    handle: the row is SENT and nothing resolves by id, exactly as before."""
    server = await _server({"state": "confirmed", "platform_ms": 1754300000000})
    store = FakeRecordStore()
    try:
        assert await _deliver_against(server, store) is True
    finally:
        await server.close()

    row = (await store.recent(conversation_id=ROOM, limit=1))[0]
    assert row.delivery_status is DeliveryStatus.SENT
    assert await store.find_edited(
        conversation_id=ROOM, target=EditTarget(message_id=REF),
        sender_id="") is None


async def test_a_multi_post_send_lands_every_native_id_for_a_later_reaction():
    """One send that produced several confirmed posts (text + captionless
    media, carousel, multi-part) carries `platform_refs` end to end: the
    audited own row keeps EVERY handle, and a touch naming ANY of them
    resolves to it — while `platform_message_id` stays the last (compat)
    handle."""
    refs = ["mid.$synthetic-1", "mid.$synthetic-2"]
    server = await _server({
        "state": "confirmed", "platform_ref": refs[-1],
        "platform_refs": refs, "platform_ms": 1754300000000})
    store = FakeRecordStore()
    try:
        assert await _deliver_against(server, store) is True
    finally:
        await server.close()

    # Every confirmed handle resolves to the same audited own row.
    row = await store.find_edited(
        conversation_id=ROOM, target=EditTarget(message_id=refs[0]),
        sender_id="")
    assert row is not None and row.is_nora
    assert row.platform_message_id == refs[-1]
    second = await store.find_edited(
        conversation_id=ROOM, target=EditTarget(message_id=refs[1]),
        sender_id="")
    assert second is not None and second.id == row.id
    # The list never leaks into another conversation.
    assert await store.find_edited(
        conversation_id="other-room", target=EditTarget(message_id=refs[0]),
        sender_id="") is None

    # A reaction naming the SECOND post's handle — not the compat one —
    # records against the row.
    message, touch = _reaction(refs[0])
    result = await touch_stage.run(
        message=message, touch=touch, audit=store,
        persons=_persons(), metrics=Metrics())
    assert result.ending == "reaction_recorded"
    assert store.reactions[-1]["message_id"] == row.id


@pytest.mark.parametrize("state", ["rejected", "uncertain"])
async def test_an_unconfirmed_reply_exposes_no_native_handles(state: str):
    """The real client enforces the send-result contract before delivery."""
    server = await _server({
        "state": state, "platform_ref": REF,
        "platform_refs": [REF, "mid.$other"]})
    adapter = BridgeAdapter(
        "instagram",
        BridgeEndpoint(str(server.make_url("")).rstrip("/"), "synthetic"),
        owner=ActionOwner(kind="live_invocation", id="1"))
    try:
        result = await adapter.send(target=TARGET, text="see you at eight")
        assert result.ok is False
        assert result.uncertain is (state == "uncertain")
        assert result.platform_message_id is None
        assert result.platform_message_ids == ()
    finally:
        await adapter.close()
        await server.close()


@pytest.mark.parametrize("state", ["rejected", "uncertain"])
async def test_an_unconfirmed_send_writes_no_handle_list(state: str):
    """A bridge that answers rejected/uncertain while still naming ids
    mints nothing: no single ref, no list — the send was not confirmed."""
    server = await _server({
        "state": state, "platform_ref": REF,
        "platform_refs": [REF, "mid.$other"]})
    store = FakeRecordStore()
    try:
        assert await _deliver_against(server, store) is False
    finally:
        await server.close()

    for ref in (REF, "mid.$other"):
        assert await store.find_edited(
            conversation_id=ROOM, target=EditTarget(message_id=ref),
            sender_id="") is None


@pytest.mark.parametrize("state", ["rejected", "uncertain"])
async def test_an_unconfirmed_send_writes_no_handle_and_stays_honest(state: str):
    """A rejected or uncertain send never minted the id it would claim —
    no `platform_message_id` lands, and the status says what happened."""
    server = await _server({"state": state})
    store = FakeRecordStore()
    try:
        assert await _deliver_against(server, store) is False
    finally:
        await server.close()

    row = (await store.recent(conversation_id=ROOM, limit=1))[0]
    assert row.delivery_status is (
        DeliveryStatus.UNCERTAIN if state == "uncertain"
        else DeliveryStatus.FAILED)
    assert await store.find_edited(
        conversation_id=ROOM, target=EditTarget(message_id=REF),
        sender_id="") is None
