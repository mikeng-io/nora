"""`HonchoClient` routed by workspace > unit > room (DEC-214), against
`tests.fakes.FakeHoncho` — a server shaped after Honcho 3.1.2's own API,
including a 404 under a workspace nobody created, so a call routed to the
wrong workspace shows up as a failure rather than as a success somewhere else.

What these pin: every call for a room lands in its Nora workspace's Honcho
workspace; peers are per (Honcho workspace, peer); each unit is a NAMED
scope that its sessions are created in and its reads pass; the one endpoint
that cannot be scoped (the peer card) is not; and a room `policy.toml` does
not place is refused, never widened.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web

from nora.infra.honcho import HonchoClient, WorkspaceRouter
from tests.fakes import FakeHoncho

_UNITS = {"friends": "nora", "wedding": "nora", "ridgeline": "ridgeline"}
_ROOMS = {"g-friends": "friends", "g-wedding": "wedding", "g-ridgeline": "ridgeline"}


@pytest.fixture
async def honcho():
    fake = FakeHoncho()
    await fake.start()
    yield fake
    await fake.close()


def _client(url: str, *, strict: bool = True) -> HonchoClient:
    return HonchoClient(
        url, "nora", workspace_router=WorkspaceRouter(
            "nora", unit_workspaces=_UNITS, conversation_units=_ROOMS, strict=strict,
        ),
    )


def _session(cid: str) -> str:
    return f"nora-conv-{cid}"


async def test_ensure_workspace_ensures_every_bound_workspace_its_ai_peer_and_scopes(honcho):
    client = _client(honcho.url)
    await client.ensure_workspace()
    assert set(honcho.workspaces) == {"nora", "ridgeline"}
    peers = {(w, p) for (_m, p, w) in honcho.requests if p.endswith("/peers")}
    assert peers == {("nora", "/v3/workspaces/nora/peers"),
                     ("ridgeline", "/v3/workspaces/ridgeline/peers")}
    assert set(honcho.scopes) == {("nora", "friends"), ("nora", "wedding"),
                                  ("ridgeline", "ridgeline")}


async def test_two_workspaces_route_apart_and_each_room_is_in_its_units_scope(honcho):
    client = _client(honcho.url)
    await client.ensure_workspace()
    assert await client.feed(peer_id="jordan", text="hi", conversation_id="g-friends")
    assert await client.feed(peer_id="jordan", text="hi", conversation_id="g-ridgeline")

    assert [m["content"] for m in honcho.messages[("nora", _session("g-friends"))]] == ["hi"]
    assert [m["content"] for m in honcho.messages[("ridgeline", _session("g-ridgeline"))]] == ["hi"]
    assert ("nora", _session("g-ridgeline")) not in honcho.messages
    assert ("ridgeline", _session("g-friends")) not in honcho.messages
    assert honcho.scopes[("nora", "friends")] == {_session("g-friends")}
    assert honcho.scopes[("ridgeline", "ridgeline")] == {_session("g-ridgeline")}
    assert honcho.scopes[("nora", "wedding")] == set()


async def test_session_create_carries_the_unit_scope_and_joins_it_once(honcho):
    sent: list[tuple[str, dict]] = []
    real = HonchoClient._post

    async def _spy(self, url, payload, timeout):  # noqa: ASYNC109 -- `_post`'s own signature
        sent.append((url, payload))
        return await real(self, url, payload, timeout)

    client = _client(honcho.url)
    await client.ensure_workspace()
    client._post = _spy.__get__(client)  # type: ignore[method-assign]
    await client.feed(peer_id="jordan", text="one", conversation_id="g-wedding")
    await client.feed(peer_id="jordan", text="two", conversation_id="g-wedding")

    creates = [p for u, p in sent if u.endswith("/v3/workspaces/nora/sessions")]
    assert creates == [{"id": _session("g-wedding"), "scopes": ["wedding"]}]
    joins = [r for r in honcho.requests if r[1].endswith("/scopes/wedding/sessions")]
    assert len(joins) == 1, "membership asserted once per process, not per message"


async def test_peers_are_configured_per_workspace_not_per_id(honcho):
    """The same person in two workspaces is two peers. Keyed on the id alone,
    the second workspace would never get its add-peers call, and Nora would
    never model them there."""
    client = _client(honcho.url)
    await client.ensure_workspace()
    await client.ensure_peer(peer_id="jordan", conversation_id="g-friends")
    await client.ensure_peer(peer_id="jordan", conversation_id="g-ridgeline")
    await client.feed(peer_id="jordan", text="a", conversation_id="g-friends")
    await client.feed(peer_id="jordan", text="b", conversation_id="g-ridgeline")

    peer_creates = [
        (w, p) for (m, p, w) in honcho.requests
        if m == "POST" and p.endswith("/peers") and "/sessions/" not in p
    ]
    assert ("nora", "/v3/workspaces/nora/peers") in peer_creates
    assert ("ridgeline", "/v3/workspaces/ridgeline/peers") in peer_creates
    configured = {
        w for (m, p, w) in honcho.requests
        if m == "POST" and "/sessions/" in p and p.endswith("/peers")
    }
    assert configured == {"nora", "ridgeline"}
    assert ("nora", _session("g-friends"), "jordan") in client._configured_peers
    assert ("ridgeline", _session("g-ridgeline"), "jordan") in client._configured_peers


class _Recorder:
    """A Honcho that records each request body, for the payload assertions
    `FakeHoncho` does not keep."""

    def __init__(self) -> None:
        self.bodies: list[tuple[str, str, dict]] = []

    async def handler(self, request: web.Request) -> web.Response:
        body = await request.json() if request.can_read_body else {}
        self.bodies.append((request.method, request.path, body))
        if request.path.endswith("/representation"):
            return web.json_response({"representation": "- x"})
        if request.path.endswith("/chat"):
            return web.json_response({"content": "an answer"})
        if request.path.endswith("/search"):
            return web.json_response([])
        if request.path.endswith("/card"):
            return web.json_response({"peer_card": ["Name: Jordan"]})
        return web.json_response({"id": body.get("id", "x")})

    def body(self, suffix: str) -> dict:
        matches = [b for (_m, p, b) in self.bodies if p.endswith(suffix)]
        assert matches, (suffix, [p for (_m, p, _b) in self.bodies])
        return matches[-1]


@pytest.fixture
async def recorder(aiohttp_server):
    rec = _Recorder()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", rec.handler)
    server = await aiohttp_server(app)
    rec.url = str(server.make_url("/")).rstrip("/")  # type: ignore[attr-defined]
    return rec


async def test_every_scoped_read_passes_the_units_named_scope(recorder):
    client = _client(recorder.url)
    recall = await client.representation(
        peer_id="nora", target="jordan", search_query="q", conversation_id="g-friends",
    )
    assert recall.reachable
    rep = recorder.body("/v3/workspaces/nora/peers/nora/representation")
    assert rep["scope"] == "friends" and rep["target"] == "jordan"
    assert "session_id" not in rep and "filters" not in rep, "scope excludes both"

    assert await client.chat(peer_id="nora", target="jordan", query="q", unit="ridgeline")
    chat = recorder.body("/v3/workspaces/ridgeline/peers/nora/chat")
    assert chat["scope"] == "ridgeline"

    assert (await client.search(query="q", unit="wedding")).reachable
    search = recorder.body("/v3/workspaces/nora/search")
    assert search["scope"] == "wedding"


async def test_the_reads_that_cannot_be_scoped_are_not(recorder):
    """The peer card endpoint takes no `scope` (honcho v3.1.2), and a room's
    session search is narrower than any scope and takes none either. Both
    still land in the room's own Honcho workspace."""
    client = _client(recorder.url)
    assert await client.peer_card(
        peer_id="jordan", conversation_id="g-ridgeline",
    ) == ["Name: Jordan"]
    card = [p for (_m, p, _b) in recorder.bodies if p.endswith("/card")]
    assert card == ["/v3/workspaces/ridgeline/peers/jordan/card"]

    assert (await client.search(query="q", conversation_id="g-ridgeline")).reachable
    session_search = recorder.body(f"/sessions/{_session('g-ridgeline')}/search")
    assert "scope" not in session_search
    assert any(
        p == f"/v3/workspaces/ridgeline/sessions/{_session('g-ridgeline')}/search"
        for (_m, p, _b) in recorder.bodies
    )


async def test_conclude_writes_where_the_scoped_read_will_look(recorder):
    """A named-scope read swaps the observer to the scope's peer, so a fact
    written only under Nora would never be served back. One batch, two rows."""
    client = _client(recorder.url)
    assert await client.conclude(
        peer_id="nora", target="jordan", content="likes tea", unit="friends",
    )
    rows = recorder.body("/v3/workspaces/nora/conclusions")["conclusions"]
    assert [(r["observer_id"], r["observed_id"]) for r in rows] == [
        ("nora", "jordan"), ("scope.friends", "jordan"),
    ]


async def test_an_unrouted_call_is_refused_without_a_request(recorder):
    """With `[units]`, a room or unit the policy does not place has no
    workspace, and the call must not be sent to any."""
    client = _client(recorder.url)
    assert not await client.feed(peer_id="jordan", text="x", conversation_id="g-unlisted")
    assert not (await client.representation(
        peer_id="nora", search_query="q", unit="nobody",
    )).reachable
    assert await client.chat(peer_id="nora", query="q") is None
    assert not (await client.search(query="q")).reachable
    assert await client.peer_card(peer_id="jordan") == []
    assert not await client.conclude(peer_id="nora", target="jordan", content="x")
    # And a call whose room and unit disagree is refused rather than routed to either.
    assert not (await client.representation(
        peer_id="nora", search_query="q", unit="ridgeline", conversation_id="g-friends",
    )).reachable
    assert recorder.bodies == []


async def test_without_units_an_unknown_room_keeps_the_old_single_workspace(recorder):
    """The back-compat half: not strict, so a harness's synthetic room goes
    to the default workspace UNSCOPED, exactly as before the hierarchy."""
    client = _client(recorder.url, strict=False)
    await client.representation(peer_id="nora", search_query="q", conversation_id="synthetic")
    rep = recorder.body("/v3/workspaces/nora/peers/nora/representation")
    assert "scope" not in rep
    assert json.dumps(rep)  # a real body was sent
