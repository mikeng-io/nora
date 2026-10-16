"""Every call Nora makes to instagram-bridge carries her trace context.

Without it each bridge call is its own unlinked trace, and "why did this send
take four seconds" cannot be answered from Nora's side. The stand-in bridge
RECORDS THE HEADERS each request arrived with, and every assertion is about
the `traceparent` that reached the wire, matched against the span that was
current when the call was made. A header that is merely present could come
from anywhere; one carrying THIS span's trace id is propagation.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from opentelemetry.sdk.trace import TracerProvider

from nora.domain.models import ConversationTarget
from nora.domain.ports import OutboundAttachment
from nora.infra.social.instagram_client import InstagramSocialClient
from nora.infra.trace_propagation import propagation_headers
from nora.platform.instagram.react import InstagramReactor
from nora.platform.instagram.roster import InstagramRoster
from nora.platform.instagram.send import InstagramSender

ROOM = "1183780976338188"
GROUP = ConversationTarget("instagram", ROOM, "group")

# A local provider, not the global one: installing a global provider in a unit
# test would leak into every other test in the process.
_tracer = TracerProvider().get_tracer("test")


@pytest.fixture
async def bridge():
    """`(base_url, seen)`: `seen` is `[(path, traceparent or None), …]`."""
    seen: list[tuple[str, str | None]] = []

    async def record(request: web.Request) -> web.Response:
        seen.append((request.path, request.headers.get("traceparent")))
        if request.path.endswith("/members"):
            return web.json_response({"room_id": ROOM, "title": "g",
                                      "is_group": True, "members": []})
        if request.path.startswith("/accounts/"):
            return web.json_response({"igid": "10000001", "found": False, "items": []})
        if request.path.endswith("/reaction"):
            return web.json_response({"confirmed": True})
        return web.json_response({"id": "mid.$x", "timestamp_ms": 1})

    # Only the bridge's current routes: a client still on a deleted verb path
    # gets aiohttp's 404 and records nothing, so its test fails.
    app = web.Application()
    app.router.add_post("/threads/{thread_fbid}/messages", record)
    app.router.add_put("/threads/{thread_fbid}/messages/{message_id}/reaction", record)
    app.router.add_get("/threads/{thread_fbid}/members", record)
    app.router.add_get("/accounts/{igid}", record)
    app.router.add_get("/accounts/{igid}/story", record)
    app.router.add_get("/accounts", record)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("")).rstrip("/"), seen
    await server.close()


def _trace_id(span) -> str:
    return format(span.get_span_context().trace_id, "032x")


def test_no_current_span_propagates_nothing():
    """Outside a span there is nothing to continue, and an empty dict is the
    answer, never a header naming a trace that does not exist."""
    assert "traceparent" not in propagation_headers()


def test_the_header_names_the_current_span():
    with _tracer.start_as_current_span("turn") as span:
        header = propagation_headers()["traceparent"]
    ctx = span.get_span_context()
    version, trace_id, span_id, flags = header.split("-")
    assert (version, trace_id, span_id) == ("00", f"{ctx.trace_id:032x}", f"{ctx.span_id:016x}")
    # Sampled. The other bits (W3C level 2's "random trace id") are the SDK's.
    assert int(flags, 16) & 0x01


async def test_a_send_carries_the_turns_trace(bridge):
    base_url, seen = bridge
    with _tracer.start_as_current_span("turn") as span:
        await InstagramSender(base_url).send(GROUP, "hello")
    assert seen and seen[0][0] == f"/threads/{ROOM}/messages"
    assert seen[0][1] is not None and _trace_id(span) in seen[0][1]


async def test_a_media_send_carries_the_turns_trace(bridge):
    base_url, seen = bridge
    png = OutboundAttachment(content=b"x", media_type="image/png", filename="a.png")
    with _tracer.start_as_current_span("turn") as span:
        await InstagramSender(base_url).send(GROUP, "", attachments=(png,))
    assert seen and seen[0][0] == f"/threads/{ROOM}/messages"
    assert seen[0][1] is not None and _trace_id(span) in seen[0][1]


async def test_a_reaction_carries_the_turns_trace(bridge):
    base_url, seen = bridge
    with _tracer.start_as_current_span("turn") as span:
        await InstagramReactor(base_url).react(target=GROUP, emoji="❤️", to_message_id="mid.$m")
    assert seen and seen[0][0] == f"/threads/{ROOM}/messages/mid.$m/reaction"
    assert seen[0][1] is not None and _trace_id(span) in seen[0][1]


async def test_a_roster_read_carries_the_turns_trace(bridge):
    base_url, seen = bridge
    with _tracer.start_as_current_span("turn") as span:
        await InstagramRoster(base_url).roster(conversation_id=ROOM)
    assert seen and seen[0][0].endswith("/members")
    assert seen[0][1] is not None and _trace_id(span) in seen[0][1]


@pytest.mark.parametrize(
    ("what", "path"),
    [
        ("story", "/accounts/10000001/story"),
        ("profile", "/accounts/10000001"),
        # The account search rides the same `_get`, so the same header.
        ("search", "/accounts"),
    ],
)
async def test_a_social_read_carries_the_turns_trace(bridge, what, path):
    base_url, seen = bridge
    client = InstagramSocialClient(base_url=base_url)
    with _tracer.start_as_current_span("turn") as span:
        await client.read(platform="instagram", what=what, target="10000001", limit=1)
    assert seen and seen[0][0] == path
    assert seen[0][1] is not None and _trace_id(span) in seen[0][1]
