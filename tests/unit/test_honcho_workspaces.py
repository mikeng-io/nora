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


async def test_a_new_session_is_created_bare_and_joined_to_its_unit_once(honcho):
    """The create carries NO `scopes`: get-or-create on an existing session
    with messages would enqueue a paid backfill on every restart. The join
    lists the scope first and adds the (empty, new) session once."""
    sent: list[tuple[str, dict]] = []
    real = HonchoClient._post

    async def _spy(self, url, payload, timeout):  # noqa: ASYNC109 -- `_post`'s own signature
        sent.append((url, payload))
        return await real(self, url, payload, timeout)

    client = _client(honcho.url)
    await client.ensure_workspace()
    honcho.requests.clear()        # startup's own join attempt is pinned below
    client._post = _spy.__get__(client)  # type: ignore[method-assign]
    await client.feed(peer_id="jordan", text="one", conversation_id="g-wedding")
    await client.feed(peer_id="jordan", text="two", conversation_id="g-wedding")

    creates = [p for u, p in sent if u.endswith("/v3/workspaces/nora/sessions")]
    assert creates == [{"id": _session("g-wedding")}]
    joins = [r for r in honcho.requests if r[1].endswith("/scopes/wedding/sessions")]
    assert len(joins) == 1, "membership asserted once per process, not per message"
    assert honcho.scopes[("nora", "wedding")] == {_session("g-wedding")}
    assert honcho.scope_backfills == [], "joined while empty: nothing to backfill"


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
        if request.path == "/v3/workspaces/list":
            # Both bound workspaces exist and carry Nora's owner marker, so
            # these tests are about routing, not ownership (tested below).
            return web.json_response({"items": [
                {"id": w, "metadata": {"nora_owner": "nora"}} for w in ("nora", "ridgeline")
            ], "pages": 1})
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
    to the default workspace UNSCOPED."""
    client = _client(recorder.url, strict=False)
    await client.representation(peer_id="nora", search_query="q", conversation_id="synthetic")
    rep = recorder.body("/v3/workspaces/nora/peers/nora/representation")
    assert "scope" not in rep
    assert json.dumps(rep)  # a real body was sent


# ── ownership: a bound Honcho workspace must be Nora's ───────────────────────


async def test_a_foreign_honcho_workspace_is_refused_and_named(honcho):
    """The deploy host's `ridgeline` Honcho workspace belongs to another
    application. Bound by name, it must get no peer, no scope and no speech,
    and the client must say which workspace it refused."""
    honcho.seed("ridgeline", created_at="2026-01-01T00:00:00Z")
    honcho.sessions["ridgeline"]["other-app-session-1"] = {
        "id": "other-app-session-1", "is_active": True, "workspace_id": "ridgeline",
    }
    client = _client(honcho.url)
    await client.ensure_workspace()

    assert client.foreign_workspaces() == ("ridgeline",)
    assert not await client.feed(peer_id="jordan", text="hi", conversation_id="g-ridgeline")
    assert ("ridgeline", _session("g-ridgeline")) not in honcho.messages
    assert ("ridgeline", "ridgeline") not in honcho.scopes
    assert "metadata" not in honcho.workspaces["ridgeline"] or not honcho.workspaces[
        "ridgeline"]["metadata"], "a foreign workspace is never marked"
    # The rest of the deployment still works.
    assert await client.feed(peer_id="jordan", text="hi", conversation_id="g-friends")


async def test_an_absent_workspace_is_created_marked_and_an_empty_one_is_marked(honcho):
    honcho.seed("ridgeline", created_at="2026-01-01T00:00:00Z", metadata={"note": "kept"})
    client = _client(honcho.url)
    await client.ensure_workspace()
    assert client.foreign_workspaces() == ()
    assert honcho.workspaces["ridgeline"]["metadata"] == {"note": "kept", "nora_owner": "nora"}
    assert await client.feed(peer_id="jordan", text="hi", conversation_id="g-ridgeline")

    fresh = FakeHoncho()
    await fresh.start()
    try:
        await _client(fresh.url).ensure_workspace()
        assert fresh.workspaces["ridgeline"]["metadata"] == {"nora_owner": "nora"}
    finally:
        await fresh.close()


async def test_the_default_workspace_is_nora_s_without_a_marker(honcho):
    """`honcho.workspace_id` holds every deployment's history, with probe
    sessions no naming rule would call Nora's. It is accepted as it is, and
    never re-marked."""
    honcho.seed("nora", created_at="2026-01-01T00:00:00Z")
    honcho.sessions["nora"]["probe-session-1"] = {
        "id": "probe-session-1", "is_active": True, "workspace_id": "nora",
    }
    client = _client(honcho.url)
    await client.ensure_workspace()
    assert "nora" not in client.foreign_workspaces()
    assert honcho.workspaces["nora"]["metadata"] == {}
    assert await client.feed(peer_id="jordan", text="hi", conversation_id="g-friends")


async def test_a_workspace_marked_by_another_nora_is_foreign(honcho):
    honcho.seed("ridgeline", created_at="2026-01-01T00:00:00Z", metadata={"nora_owner": "delia"})
    honcho.sessions["ridgeline"]["nora-conv-x"] = {
        "id": "nora-conv-x", "is_active": True, "workspace_id": "ridgeline",
    }
    client = _client(honcho.url)
    await client.ensure_workspace()
    assert client.foreign_workspaces() == ("ridgeline",)


async def test_a_workspace_read_is_unscoped_in_that_workspace(recorder):
    """The portal's read: a NORA workspace name, resolved to its Honcho
    workspace and read across every unit — no `scope`."""
    client = HonchoClient(recorder.url, "nora", workspace_router=WorkspaceRouter(
        "nora", unit_workspaces=_UNITS, conversation_units=_ROOMS,
        workspace_routes={"67": "nora", "ridgeline": "ridgeline"}, strict=True,
    ))
    assert (await client.representation(
        peer_id="nora", target="jordan", search_query="", workspace="ridgeline",
    )).reachable
    rep = recorder.body("/v3/workspaces/ridgeline/peers/nora/representation")
    assert "scope" not in rep
    assert not (await client.representation(
        peer_id="nora", search_query="", workspace="unbound",
    )).reachable


async def test_startup_joins_every_known_room_that_has_a_session(honcho):
    """A room that is quiet still joins its unit's scope —
    at startup, not on its next message. A room with no session in the
    workspace yet is skipped, not created."""
    client = _client(honcho.url)
    await client.ensure_workspace()                # creates the workspaces
    honcho.sessions["nora"][_session("g-friends")] = {
        "id": _session("g-friends"), "is_active": True, "workspace_id": "nora",
    }
    restarted = _client(honcho.url)
    await restarted.ensure_workspace()
    assert honcho.scopes[("nora", "friends")] == {_session("g-friends")}
    assert honcho.scopes[("nora", "wedding")] == set()
    assert _session("g-wedding") not in honcho.sessions["nora"]


async def test_without_units_startup_joins_nothing_and_reads_unscoped(recorder):
    client = _client(recorder.url, strict=False)
    await client.ensure_workspace()
    assert not [p for (_m, p, _b) in recorder.bodies if "/scopes" in p]
    await client.representation(peer_id="nora", search_query="q", conversation_id="g-friends")
    assert "scope" not in recorder.body("/v3/workspaces/nora/peers/nora/representation")


async def test_a_page_view_never_creates_or_marks_a_workspace(honcho):
    """The portal reads by WORKSPACE, read-only: a workspace that does not
    exist yet is not created by somebody opening a page."""
    client = HonchoClient(honcho.url, "nora", workspace_router=WorkspaceRouter(
        "nora", unit_workspaces=_UNITS, conversation_units=_ROOMS,
        workspace_routes={"67": "nora", "ridgeline": "ridgeline"}, strict=True,
    ))
    await client.representation(
        peer_id="nora", target="jordan", search_query="", workspace="ridgeline",
    )
    await client.peer_card(peer_id="jordan", workspace="ridgeline")
    assert "ridgeline" not in honcho.workspaces


@pytest.mark.parametrize("state", ["absent", "empty"])
async def test_a_claim_is_believed_only_from_honchos_marked_answer(aiohttp_server, state):
    """Get-or-create on a workspace someone else created a moment ago answers
    with THEIRS, unmarked; so does a metadata update that did not take. The
    client must not cache that as Nora's, and must route nothing there."""
    writes: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        path = request.path
        if path == "/v3/workspaces/list":
            items = [] if state == "absent" else [{"id": "ridgeline", "metadata": {}}]
            return web.json_response({"items": items, "pages": 1})
        if path == "/v3/workspaces/ridgeline/sessions/list":
            return web.json_response({"items": [], "total": 0, "pages": 0})
        if path in ("/v3/workspaces", "/v3/workspaces/ridgeline"):
            return web.json_response({"id": "ridgeline", "metadata": {}})   # unmarked
        writes.append(path)
        return web.json_response({"id": "x"})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = await aiohttp_server(app)
    client = _client(str(server.make_url("/")).rstrip("/"))
    assert not await client.feed(peer_id="jordan", text="hi", conversation_id="g-ridgeline")
    assert client._ownership.get("ridgeline") is not True
    assert not [p for p in writes if "/workspaces/ridgeline/" in p]


async def test_the_startup_joins_run_concurrently(aiohttp_server):
    """A slow Honcho costs startup one join's time, not one per room."""
    import asyncio
    import time

    async def handler(request: web.Request) -> web.Response:
        if request.path == "/v3/workspaces/list":
            return web.json_response({"items": [
                {"id": w, "metadata": {"nora_owner": "nora"}} for w in ("nora", "ridgeline")
            ], "pages": 1})
        if request.path.endswith("/sessions/list") and "/scopes/" in request.path:
            await asyncio.sleep(0.3)        # the member list is the slow read
            return web.json_response({"items": [], "pages": 0})
        if request.path.endswith("/sessions") and "/scopes/" in request.path:
            return web.Response(status=204)
        return web.json_response({"id": "x"})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = await aiohttp_server(app)
    client = _client(str(server.make_url("/")).rstrip("/"))
    started = time.monotonic()
    await client.ensure_workspace()
    assert time.monotonic() - started < 0.8, "three rooms' joins ran one after another"
    assert len(client._scoped_sessions) == 3


# ── a join never re-adds a member (Honcho re-enqueues a paid backfill) ───────


async def _room_with_history(honcho: FakeHoncho, cid: str) -> None:
    """A room that already spoke: its session exists and holds a message."""
    client = _client(honcho.url)
    await client.ensure_workspace()
    assert await client.feed(peer_id="jordan", text="hi", conversation_id=cid)


async def test_a_second_startup_adds_nothing_and_backfills_nothing(honcho):
    """Honcho v3.1.2 enqueues a `scope_backfill` (and so an omni dream per
    touched collection) for every named session with messages, members
    included, so a restart that re-added every known room would pay it each
    time. After the first start, a restart must add no session."""
    await _room_with_history(honcho, "g-friends")
    honcho.scope_backfills.clear()
    honcho.requests.clear()

    restarted = _client(honcho.url)
    await restarted.ensure_workspace()
    assert await restarted.feed(peer_id="jordan", text="again", conversation_id="g-friends")

    # No re-add of the member. (Rooms with no session yet are still offered to
    # their scopes and refused 404 by Honcho — that enqueues nothing.)
    adds = [r for r in honcho.requests
            if r[0] == "POST" and r[1].endswith("/scopes/friends/sessions")]
    assert adds == [], adds
    assert honcho.scope_backfills == []
    assert _session("g-friends") in honcho.scopes[("nora", "friends")]
    lists = [r for r in honcho.requests if r[1].endswith("/scopes/friends/sessions/list")]
    assert len(lists) == 1, "membership is listed once per scope per process"


async def test_a_session_that_is_not_a_member_is_added(honcho):
    """The positive control: a room with history that is NOT in its unit's
    scope (a session from before scopes) is added — once — and that is the
    one backfill it should cost."""
    await _room_with_history(honcho, "g-friends")
    honcho.scopes[("nora", "friends")].clear()
    honcho.scope_backfills.clear()

    restarted = _client(honcho.url)
    await restarted.ensure_workspace()
    await restarted.feed(peer_id="jordan", text="again", conversation_id="g-friends")
    assert honcho.scope_backfills == [("nora", "friends", _session("g-friends"))]


async def test_an_unreadable_member_list_skips_the_join_instead_of_adding_blind(honcho):
    """A failed list adds nothing, is not asked again on every message, and is
    asked again once the retry window has passed."""
    await _room_with_history(honcho, "g-friends")
    honcho.scopes[("nora", "friends")].clear()
    honcho.scope_backfills.clear()
    honcho.fail_member_lists = True
    now = [1000.0]

    restarted = HonchoClient(honcho.url, "nora", clock=lambda: now[0], workspace_router=(
        WorkspaceRouter("nora", unit_workspaces=_UNITS, conversation_units=_ROOMS,
                        strict=True)
    ))
    await restarted.ensure_workspace()
    assert honcho.scope_backfills == []
    assert _session("g-friends") not in honcho.scopes[("nora", "friends")]

    honcho.fail_member_lists = False
    honcho.requests.clear()
    now[0] += 30                           # inside the window: not asked again
    await restarted.feed(peer_id="jordan", text="again", conversation_id="g-friends")
    assert not [r for r in honcho.requests if r[1].endswith("/scopes/friends/sessions/list")]
    assert _session("g-friends") not in honcho.scopes[("nora", "friends")]

    now[0] += 31                           # past it: listed again, then joined
    await restarted.feed(peer_id="jordan", text="later", conversation_id="g-friends")
    assert _session("g-friends") in honcho.scopes[("nora", "friends")]
    assert honcho.scope_backfills == [("nora", "friends", _session("g-friends"))]


async def test_a_scope_missing_at_the_first_message_is_created_then_joined(honcho):
    """A Honcho that was down at startup leaves the scope uncreated, and the
    session create names no scopes: the join creates it first."""
    client = _client(honcho.url)
    await client.ensure_workspace()
    del honcho.scopes[("nora", "friends")]
    fresh = _client(honcho.url)
    fresh._workspaces_ready = set(client._workspaces_ready)   # skip startup: Honcho was down
    assert await fresh.feed(peer_id="jordan", text="hi", conversation_id="g-friends")
    assert honcho.scopes[("nora", "friends")] == {_session("g-friends")}


async def test_every_page_of_the_member_list_is_read(honcho):
    """Past one page (Honcho pages at most 100): a member on page 2 is still a
    member, and must not be re-added (a re-add is a paid backfill)."""
    await _room_with_history(honcho, "g-friends")
    fillers = {f"a-filler-{i:04d}" for i in range(150)}      # sort BEFORE ours
    honcho.scopes[("nora", "friends")] |= fillers
    honcho.scope_backfills.clear()
    honcho.requests.clear()

    restarted = _client(honcho.url)
    await restarted.ensure_workspace()
    await restarted.feed(peer_id="jordan", text="again", conversation_id="g-friends")
    assert honcho.scope_backfills == []
    assert not [r for r in honcho.requests
                if r[0] == "POST" and r[1].endswith("/scopes/friends/sessions")]


async def test_a_scope_is_listed_once_per_process_for_all_its_rooms(honcho):
    """Two rooms in one unit: one member list, not one per room."""
    rooms = {"g-friends": "friends", "g-friends-discord": "friends"}
    client = HonchoClient(honcho.url, "nora", workspace_router=WorkspaceRouter(
        "nora", unit_workspaces={"friends": "nora"}, conversation_units=rooms, strict=True,
    ))
    await client.ensure_workspace()
    for cid in rooms:
        assert await client.feed(peer_id="jordan", text="hi", conversation_id=cid)
    lists = [r for r in honcho.requests if r[1].endswith("/scopes/friends/sessions/list")]
    assert len(lists) == 1
    assert honcho.scopes[("nora", "friends")] == {_session(c) for c in rooms}


async def test_two_concurrent_joins_of_one_session_add_it_once(honcho):
    """The startup gather and a room's first message can join the same session
    at the same moment; the per-session lock makes it one add, one backfill."""
    import asyncio

    await _room_with_history(honcho, "g-friends")
    client = _client(honcho.url)
    await client.ensure_workspace()
    # The room is NOT a member again, and this client has cached nothing.
    honcho.scopes[("nora", "friends")].clear()
    honcho.scope_backfills.clear()
    client._scoped_sessions.clear()
    client._members.clear()
    route = client._router.for_conversation("g-friends")
    assert route is not None
    await asyncio.gather(*(
        client._join_scope(route, _session("g-friends")) for _ in range(2)
    ))
    assert honcho.scope_backfills == [("nora", "friends", _session("g-friends"))]
