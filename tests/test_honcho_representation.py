"""`HonchoClient.representation` — durable recall, curated to one question.

Every shape asserted here was read off the LIVE instance on 2026-08-06, not out
of documentation. That matters more than usual for this endpoint: its two
sibling reads shipped dead against invented shapes (`ensure_session` POSTing to
a list endpoint, `peer_card` reading a `content` key Honcho has never returned),
and both had a passing test at the time.

The local server below therefore answers like the real one, including for the
requests this code gets WRONG — an unknown peer is 200 with an empty string,
never a 404.
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.honcho import HonchoClient

# The exact body the live instance returned for peer `miles_novak` on 2026-08-06.
LIVE_BODY = (
    "## Explicit Observations\n\n"
    "[2026-08-05 17:18:04] miles_novak is named Miles\n"
    "[2026-08-05 17:18:04] miles_novak asked who was in the group on August 5, 2026\n"
    "[2026-08-05 17:18:04] miles_novak is from Ridgeline\n"
)


@pytest.fixture
async def server_factory():
    servers: list[TestServer] = []

    async def _make(handler) -> str:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        return str(server.make_url("/")).rstrip("/")

    yield _make

    for server in servers:
        await server.close()


def _honcho_like(bodies: dict[str, str]):
    """A server that behaves like Honcho, including for peers it does not know.

    `bodies` maps peer id to its representation. **A peer that is absent gets
    200 and an empty string, not a 404** — that is what the live instance does,
    and a fake that 404'd instead would let a client which cannot tell "no such
    peer" from "nothing known" pass this whole file.
    """

    async def handler(request: web.Request) -> web.Response:
        peer = request.path.rstrip("/").split("/")[-2]
        return web.json_response({"representation": bodies.get(peer, "")})

    return handler


async def test_a_live_shaped_body_becomes_one_conclusion_per_line(server_factory):
    base = await server_factory(_honcho_like({"miles_novak": LIVE_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="miles_novak", search_query="who is he",
    )
    assert got.reachable is True
    assert got.conclusions == (
        "miles_novak is named Miles",
        "miles_novak asked who was in the group on August 5, 2026",
        "miles_novak is from Ridgeline",
    )


async def test_the_markdown_heading_is_not_a_conclusion(server_factory):
    """`## Explicit Observations` is the store's own filing structure. Returning
    it would hand the model a section title to repeat back to the group."""
    base = await server_factory(_honcho_like({"miles_novak": LIVE_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="miles_novak", search_query="q",
    )
    assert not any(c.startswith("#") for c in got.conclusions)


async def test_the_timestamp_prefix_is_stripped(server_factory):
    base = await server_factory(_honcho_like({"miles_novak": LIVE_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="miles_novak", search_query="q",
    )
    assert not any(c.startswith("[") for c in got.conclusions)


async def test_an_unknown_peer_is_reachable_and_empty_never_unreachable(server_factory):
    """THE distinction this return type exists for. Honcho answers 200 with an
    empty string for a peer it has never heard of, so the transport cannot tell
    that from "I know nothing about them" — but it CAN tell both from "the
    request failed", and that is the bit the caller cannot recover afterwards.
    """
    base = await server_factory(_honcho_like({"miles_novak": LIVE_BODY}))
    got = await HonchoClient(base, "nora").representation(
        peer_id="nobody_at_all", search_query="q",
    )
    assert got.reachable is True
    assert got.conclusions == ()


async def test_a_server_error_is_unreachable_not_empty(server_factory):
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({}, status=500)

    base = await server_factory(handler)
    got = await HonchoClient(base, "nora").representation(peer_id="miles_novak", search_query="q")
    assert got.reachable is False


async def test_honcho_being_down_is_unreachable_and_never_raises():
    got = await HonchoClient("http://127.0.0.1:1", "nora").representation(
        peer_id="miles_novak", search_query="q",
    )
    assert got.reachable is False


async def test_a_timeout_is_unreachable_and_bounded(server_factory):
    async def handler(request: web.Request) -> web.Response:
        await asyncio.sleep(0.5)
        return web.json_response({"representation": LIVE_BODY})

    base = await server_factory(handler)
    client = HonchoClient(base, "nora", representation_timeout=0.05)
    got = await client.representation(peer_id="miles_novak", search_query="q")
    assert got.reachable is False


async def test_it_posts_the_search_query_to_the_representation_endpoint(server_factory):
    """The URL, the verb and the body, pinned. `peer_card` was dead because
    nothing checked which endpoint it actually spoke to."""
    seen: list[tuple[str, str, dict]] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append((request.method, request.path, await request.json()))
        return web.json_response({"representation": LIVE_BODY})

    base = await server_factory(handler)
    await HonchoClient(base, "nora").representation(
        peer_id="miles_novak", search_query="邊個係阿豹", max_conclusions=3,
    )
    method, path, body = seen[0]
    assert method == "POST"
    assert path == "/v3/workspaces/nora/peers/miles_novak/representation"
    assert body["search_query"] == "邊個係阿豹"
    assert body["max_conclusions"] == 3


async def test_it_never_sends_a_target(server_factory):
    """`target=` is theory-of-mind and 404s unless the target peer exists —
    Nora is not a peer, so passing one would turn every recall into a failure
    (design/11 §1.3)."""
    seen: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(await request.json())
        return web.json_response({"representation": ""})

    base = await server_factory(handler)
    await HonchoClient(base, "nora").representation(peer_id="miles_novak", search_query="q")
    assert "target" not in seen[0]


async def test_a_body_of_the_wrong_shape_is_reachable_with_nothing_in_it(server_factory):
    """An upstream shape change must not read as an outage: the request
    demonstrably succeeded, and saying otherwise would send an operator after
    the wrong service."""
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"representation": None})

    base = await server_factory(handler)
    got = await HonchoClient(base, "nora").representation(peer_id="miles_novak", search_query="q")
    assert got.reachable is True
    assert got.conclusions == ()
