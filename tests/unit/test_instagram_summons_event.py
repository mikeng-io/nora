"""The bridge's `summons` SSE event: parsed
ONCE into a `SummonsCard`, handed to the summons sink, and NEVER to room
ingest.

Parsing fails closed field by field: a card with no usable message id, no
believable time, or a `resolved` that is not a JSON boolean is refused (no
row, no answer, counted `malformed`); a `resolved: true` card naming no
addressable post is read as UNRESOLVED; a sender igid that is not decimal
matches nobody. The adapter half is driven through a real SSE stream, so
"never reaches room ingest" is measured on the adapter's own `listen`.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.app.summons_inbox import SummonsInbox
from nora.domain.summons import SummonsCard
from nora.obs.metrics import Metrics
from nora.platform.instagram.adapter import InstagramAdapter
from nora.platform.instagram.summons import parse_summons

ROOM = "340282366841710300949128114321096419"
RECEIVED_MS = 1790150400000  # 2026-09-23T08:00:00Z


def _event(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": "summons",
        "message_id": "mid.$gAASG4nvQLWim9MCLrWgw3tChgAAq",
        "room_id": ROOM,
        "sender": {"fbid": "100000000000001", "igid": "10000001", "username": "miles_example"},
        "text": "@noraexample is this true",
        "shortcode": "CmiJBr2LAAB",
        "media_id": "3100000000000000001",
        "received_at_ms": RECEIVED_MS,
        "resolved": True,
    }
    base.update(over)
    return base


# ---- parsing -------------------------------------------------------------------


def test_a_resolved_card_is_read_field_by_field():
    card = parse_summons(_event())
    assert card == SummonsCard(
        message_id="mid.$gAASG4nvQLWim9MCLrWgw3tChgAAq", room_id=ROOM,
        sender_igid="10000001", sender_username="miles_example",
        text="@noraexample is this true", shortcode="CmiJBr2LAAB",
        media_id="3100000000000000001",
        received_at=datetime(2026, 9, 23, 8, 0, tzinfo=UTC), resolved=True,
    )


def test_an_unresolved_card_is_kept_and_says_so():
    card = parse_summons(_event(resolved=False, shortcode="", media_id=""))
    assert card is not None
    assert (card.resolved, card.shortcode, card.media_id) == (False, "", "")


def test_an_unresolved_card_never_carries_a_post_even_if_the_wire_named_one():
    card = parse_summons(_event(resolved=False))
    assert card is not None and not card.resolved
    assert (card.shortcode, card.media_id) == ("", "")


@pytest.mark.parametrize("over", [
    {"shortcode": ""},
    {"shortcode": "../accounts"},
    {"media_id": ""},
    {"media_id": "12ab"},
    {"shortcode": None},
])
def test_a_resolved_card_naming_no_addressable_post_is_unresolved(over):
    card = parse_summons(_event(**over))
    assert card is not None and card.resolved is False
    assert (card.shortcode, card.media_id) == ("", "")


@pytest.mark.parametrize("over", [
    {"message_id": None},
    {"message_id": ""},
    {"message_id": "mid $has space"},
    {"message_id": 12345},
    {"message_id": "x" * 300},
    {"resolved": None},
    {"resolved": "true"},
    {"resolved": 1},
    {"received_at_ms": None},
    {"received_at_ms": "1790150400000"},
    {"received_at_ms": True},
    {"received_at_ms": 1790150400},  # seconds, not ms: not a time we stamped
    {"received_at_ms": 0},
    {"received_at_ms": 10**30},
    {"kind": "message"},
])
def test_a_card_with_a_garbage_field_is_refused(over):
    assert parse_summons(_event(**over)) is None


@pytest.mark.parametrize("event", [None, "summons", [], 7])
def test_a_card_that_is_not_an_object_is_refused(event):
    assert parse_summons(event) is None


def test_a_sender_that_is_not_an_id_matches_nobody():
    card = parse_summons(_event(sender={"igid": "12_34", "username": "../x"}))
    assert card is not None and card.sender_igid == "" and card.sender_username == ""
    card = parse_summons(_event(sender="not an object"))
    assert card is not None and card.sender_igid == ""


# ---- the adapter: a summons goes to the sink, never to room ingest -------------


def _message() -> dict[str, Any]:
    return {
        "kind": "message", "room_id": ROOM,
        "message_id": "mid.$gAASG4nvQLWim9MCLrWgw3tChgAAq",
        "sender": {"fbid": "100000000000001", "igid": "10000001",
                   "username": "miles_example"},
        "from_me": False, "timestamp_ms": RECEIVED_MS, "text": "a room message",
        "mentions": [], "reply_to": None, "media": None,
    }


async def _listen(adapter: InstagramAdapter, events: list[dict[str, Any] | str],
                  until) -> list:
    """Serve `events` on a real SSE stream, run the adapter's own `listen`
    until `until()` holds, and return what reached `on_message`."""
    async def handler(request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        for event in events:
            line = event if isinstance(event, str) else json.dumps(event)
            await resp.write(f"data: {line}\n\n".encode())
        await asyncio.sleep(30)
        return resp

    app = web.Application()
    app.router.add_get("/events", handler)
    server = TestServer(app)
    await server.start_server()
    adapter._listener._url = f"{str(server.make_url('')).rstrip('/')}/events"
    got: list = []

    async def on_message(message) -> None:
        got.append(message)

    task = asyncio.create_task(adapter.listen(on_message=on_message))
    try:
        for _ in range(200):
            await asyncio.sleep(0.01)
            if until(got):
                break
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await server.close()
    return got


async def test_a_summons_reaches_the_sink_and_never_room_ingest():
    metrics = Metrics()
    inbox = SummonsInbox(enabled=True, metrics=metrics)
    adapter = InstagramAdapter("http://unused", summons=inbox)
    got = await _listen(
        adapter,
        [_event(), _event(message_id="mid.$second", resolved=False), _message()],
        until=lambda got: len(got) >= 1 and len(inbox) >= 2,
    )
    # Positive control: the ordinary message event beside it IS ingested.
    assert [m.body for m in got] == ["a room message"]
    assert len(inbox) == 2
    first = await inbox.get(wait_seconds=0)
    second = await inbox.get(wait_seconds=0)
    assert (first.resolved, second.resolved) == (True, False)
    assert metrics.get("nora_summons_intake_total", outcome="queued") == 2


async def test_a_malformed_summons_is_counted_and_never_ingested():
    metrics = Metrics()
    inbox = SummonsInbox(enabled=True, metrics=metrics)
    adapter = InstagramAdapter("http://unused", summons=inbox)
    got = await _listen(
        adapter, [_event(resolved="yes"), _message()],
        until=lambda got: len(got) >= 1,
    )
    assert [m.body for m in got] == ["a room message"]
    assert len(inbox) == 0
    assert metrics.get("nora_summons_intake_total", outcome="malformed") == 1


async def test_with_the_summons_off_a_card_is_counted_dropped_and_never_ingested():
    metrics = Metrics()
    inbox = SummonsInbox(enabled=False, metrics=metrics)
    adapter = InstagramAdapter("http://unused", summons=inbox)
    got = await _listen(adapter, [_event(), _message()], until=lambda got: len(got) >= 1)
    assert [m.body for m in got] == ["a room message"]
    assert len(inbox) == 0
    assert metrics.get("nora_summons_intake_total", outcome="disabled") == 1


async def test_an_adapter_with_no_sink_drops_a_summons_and_ingests_nothing_of_it():
    adapter = InstagramAdapter("http://unused")
    got = await _listen(adapter, [_event(), _message()], until=lambda got: len(got) >= 1)
    assert [m.body for m in got] == ["a room message"]


async def test_the_registry_hands_the_inbox_to_the_instagram_adapter():
    """Composition builds the inbox and the registry threads it: the adapter
    the registry builds (here degraded, since nothing answers) holds the SAME
    sink, so a card it parses reaches the consumer's inbox."""
    from nora.platform.registry import AdapterRegistry
    from tests.unit.test_instagram_degradation import _InstagramCfg, _PlatformsCfg

    inbox = SummonsInbox(enabled=True, metrics=Metrics())
    cfg = _PlatformsCfg(instagram=_InstagramCfg(enabled=True, base_url="http://127.0.0.1:1"))
    registry = await AdapterRegistry.from_config(
        cfg, metrics=Metrics(), instagram_summons=inbox,
    )
    adapter = registry.get("instagram")
    assert isinstance(adapter, InstagramAdapter)
    assert adapter._summons is inbox


async def test_a_summons_never_reaches_the_room_parser_even_if_it_would_accept_it(
    monkeypatch,
):
    """The adapter's early return, pinned directly. `parse_event` answers None for `kind:
    "summons"`, so the positive tests above would stay green without the early return. Here the
    room parser is replaced by one that ACCEPTS every event and records what it was handed: a
    summons must never be among them, and so never reaches `on_message`. Without the early return
    the summons is parsed and ingested as a room
    message."""
    import nora.platform.instagram.adapter as adapter_module

    real = adapter_module.parse_event
    handed: list[str] = []

    def accepting(event):
        handed.append(event.get("kind"))
        return real(_message())

    monkeypatch.setattr(adapter_module, "parse_event", accepting)
    inbox = SummonsInbox(enabled=True, metrics=Metrics())
    adapter = InstagramAdapter("http://unused", summons=inbox)
    got = await _listen(
        adapter, [_event(), _message()],
        until=lambda got: len(got) >= 1 and len(inbox) >= 1,
    )
    assert handed == ["message"]
    assert len(got) == 1
    assert len(inbox) == 1
