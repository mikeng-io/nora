"""Tests for the best-effort Honcho v3 write path (nora/infra/honcho/client.py, spec §C.5).

Every scenario below is offline: a local aiohttp.test_utils.TestServer stands in for
Honcho, so no live network is required (and none of these carry the `network` marker).
"""

from __future__ import annotations

import asyncio
import re
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.honcho import HonchoClient, HonchoRoute, WorkspaceRouter


@pytest.fixture
async def server_factory():
    """Yield a factory that spins up a local aiohttp server for one test's handler."""
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


#: `acme` exists and carries Nora's owner marker: these tests are about
#: routing, and a bound workspace other than the default is only reachable
#: once it is known to be Nora's (`HonchoClient._owned`).
def _owned_acme() -> web.Response:
    return web.json_response(
        {"items": [{"id": "acme", "metadata": {"nora_owner": "nora"}}], "pages": 1},
    )


def test_workspace_router_maps_conversations_and_falls_back_to_the_default():
    """DEC-214: a conversation resolves through its unit to that unit's
    Honcho workspace and named scope. Without `[units]` (not strict) anything
    unknown falls back to the single default, UNSCOPED — the pre-hierarchy
    behaviour every harness relies on."""
    r = WorkspaceRouter(
        "nora", unit_workspaces={"work": "acme"},
        conversation_units={"g-work": "work"},
    )
    # Not strict (no `[units]`): the unit's workspace, but NO scope — reads
    # stay exactly as unscoped as before DEC-214 until `[units]` is written.
    assert r.for_conversation("g-work") == HonchoRoute("acme")
    assert r.for_unit("work") == HonchoRoute("acme")
    assert r.for_conversation("g-unmapped") == HonchoRoute("nora")
    assert r.unrouted() == HonchoRoute("nora")


def test_a_strict_router_refuses_what_it_was_not_built_with():
    """With `[units]` declared there is no default a caller could have meant:
    an unknown room, an unknown unit and a call naming neither have NO route,
    which the client turns into a refused call — never a widened one."""
    r = WorkspaceRouter(
        "nora", unit_workspaces={"work": "acme"},
        conversation_units={"g-work": "work"}, strict=True,
    )
    assert r.for_conversation("g-work") == HonchoRoute("acme", "work")
    assert r.for_conversation("g-unmapped") is None
    assert r.for_unit("nobody") is None
    assert r.unrouted() is None
    assert r.workspaces() == ("acme",)


async def test_feed_routes_every_call_to_the_conversations_workspace(server_factory):
    """The seam threaded through the whole feed path (DEC-214): a mapped
    conversation's session-create, peer-config, scope membership AND message
    POST all land under its own Honcho workspace — never the default."""
    paths: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        if request.path == "/v3/workspaces/list":
            return _owned_acme()
        paths.append(request.path)
        if request.path.endswith("/sessions") and "/scopes/" not in request.path:
            return web.json_response({"id": "s1"})
        if "/scopes/" in request.path:
            return web.Response(status=204)
        return web.json_response({"ok": True})

    base = await server_factory(handler)
    client = HonchoClient(
        base, "nora", workspace_router=WorkspaceRouter(
            "nora", unit_workspaces={"work": "acme"},
            conversation_units={"g-work": "work"}, strict=True,
        ),
    )
    assert await client.feed(peer_id="miles", text="hi", conversation_id="g-work") is True
    assert paths and all("/workspaces/acme/" in p for p in paths), paths
    assert not any("/workspaces/nora/" in p for p in paths)


async def test_an_unmapped_conversation_and_a_unit_recall_route_apart(server_factory):
    """The behaviour-preserving half: an unmapped room (no `[units]`) goes to
    the single default workspace, exactly as before the hierarchy. And, once
    `[units]` exists, a recall naming a UNIT goes to that unit's workspace,
    scoped to it."""
    paths: list[str] = []
    bodies: dict[str, dict] = {}

    async def handler(request: web.Request) -> web.Response:
        if request.path == "/v3/workspaces/list":
            return _owned_acme()
        paths.append(request.path)
        if request.method == "POST" and request.can_read_body:
            bodies[request.path] = await request.json()
        if request.path.endswith("/sessions"):
            return web.json_response({"id": "s1"})
        if request.path.endswith("/representation"):
            return web.json_response({"representation": "x"})
        return web.json_response({"ok": True})

    base = await server_factory(handler)
    implicit = HonchoClient(
        base, "nora", workspace_router=WorkspaceRouter(
            "nora", unit_workspaces={"work": "acme"},
            conversation_units={"g-work": "work"},
        ),
    )
    await implicit.feed(peer_id="miles", text="hi", conversation_id="g-other")   # unmapped
    assert all("/workspaces/nora/" in p for p in paths) and paths
    paths.clear()
    client = HonchoClient(
        base, "nora", workspace_router=WorkspaceRouter(
            "nora", unit_workspaces={"work": "acme"},
            conversation_units={"g-work": "work"}, strict=True,
        ),
    )
    await client.representation(
        peer_id="nora", target="miles", search_query="q", unit="work",
    )
    rep = "/v3/workspaces/acme/peers/nora/representation"
    assert rep in paths, paths
    assert bodies[rep]["scope"] == "work"


async def test_feed_creates_a_session_then_posts_the_message_batch(server_factory):
    calls: list[tuple[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append((request.method, request.path))
        if request.path.endswith("/sessions"):
            return web.json_response({"id": "sess-1"})
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.feed(peer_id="miles", text="hello", conversation_id="c1") is True
    # The session is created at the WORKSPACE level. This test previously
    # asserted `/peers/miles/sessions`, which is a LIST endpoint — it answers
    # 200 with a pagination envelope carrying no `id`, so `ensure_session`
    # returned None, `feed` returned False before posting anything, and Honcho
    # received nothing for the entire life of the deployment. The test passed
    # throughout, because it was written against the implementation rather
    # than the API and its fake server answered whatever was asked of it.
    #
    # Two `.../peers` POSTs precede the message: the observer config (design/24
    # §4) that makes Honcho DERIVE Nora's model of the speaker — Nora joins the
    # session as an observer at creation, the human as observable on their feed.
    # Without them the (nora → person) pair never derives and every recall reads
    # empty (proven live 2026-08-07).
    assert calls == [
        ("POST", "/v3/workspaces/nora/sessions"),
        ("POST", "/v3/workspaces/nora/sessions/sess-1/peers"),      # nora: observe_others
        ("POST", "/v3/workspaces/nora/sessions/sess-1/peers"),      # miles: observe_me
        ("POST", "/v3/workspaces/nora/sessions/sess-1/messages"),
    ]


async def test_feed_sends_the_correct_message_batch_body(server_factory):
    bodies: dict[str, object] = {}

    async def handler(request: web.Request) -> web.Response:
        body = await request.json()
        if request.path.endswith("/messages"):
            bodies["messages"] = body
        if request.path.endswith("/sessions"):
            return web.json_response({"id": "sess-1"})
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.feed(peer_id="miles", text="hello", conversation_id="c1") is True
    assert bodies["messages"] == {"messages": [{"content": "hello", "peer_id": "miles"}]}


async def test_feed_reuses_the_cached_session_for_the_same_peer(server_factory):
    calls: list[tuple[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append((request.method, request.path))
        if request.path.endswith("/sessions"):
            return web.json_response({"id": "sess-1"})
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.feed(peer_id="miles", text="a", conversation_id="c1") is True
    assert await client.feed(peer_id="miles", text="b", conversation_id="c1") is True

    session_calls = [c for c in calls if c[1].endswith("/sessions")]
    message_calls = [c for c in calls if c[1].endswith("/messages")]
    assert len(session_calls) == 1
    assert len(message_calls) == 2


async def test_ensure_peer_is_idempotent(server_factory):
    calls: list[tuple[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append((request.method, request.path))
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    await client.ensure_peer(peer_id="miles")
    await client.ensure_peer(peer_id="miles")

    # Careful not to over-match /peers/miles/sessions — filter for paths ending exactly
    # in "/peers".
    peer_calls = [c for c in calls if c[1].endswith("/peers")]
    assert len(peer_calls) == 1


async def test_ensure_workspace_is_called_once(server_factory):
    calls: list[tuple[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append((request.method, request.path))
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    await client.ensure_workspace()
    await client.ensure_workspace()

    ws_calls = [c for c in calls if c[1] == "/v3/workspaces"]
    assert len(ws_calls) == 1


async def test_feed_returns_false_when_honcho_is_down():
    client = HonchoClient("http://127.0.0.1:1", "nora", feed_timeout=0.3)
    assert await client.feed(peer_id="miles", text="hello", conversation_id="c1") is False


async def test_feed_never_raises_on_a_server_error(server_factory):
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"error": "boom"}, status=500)

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.feed(peer_id="miles", text="hello", conversation_id="c1") is False


async def test_feed_times_out_without_blocking_the_caller(server_factory):
    async def handler(request: web.Request) -> web.Response:
        await asyncio.sleep(5)
        return web.json_response({"id": "sess-1"})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora", feed_timeout=0.3)

    start = time.monotonic()
    result = await client.feed(peer_id="miles", text="hello", conversation_id="c1")
    elapsed = time.monotonic() - start

    assert result is False
    assert elapsed < 2.0


async def test_ensure_peer_never_raises_when_honcho_is_down():
    client = HonchoClient("http://127.0.0.1:1", "nora", feed_timeout=0.2)
    await client.ensure_peer(peer_id="miles")  # must not raise


async def test_ensure_peer_is_idempotent_under_concurrency(monkeypatch):
    """Two concurrent misses must create ONE peer, not two. Person-centric
    identity means Miles's Signal and Telegram messages hit the same peer id at
    the same moment, and a split peer splits his card."""
    client = HonchoClient("http://127.0.0.1:8000", "nora")
    creates = []

    async def _fake_post(url, payload, timeout):  # noqa: ASYNC109 -- `_post`'s own signature
        await asyncio.sleep(0.01)      # widen the check-then-act window
        if url.endswith("/peers"):
            creates.append(payload["id"])
        return {"id": payload.get("id")}

    monkeypatch.setattr(client, "_post", _fake_post)

    await asyncio.gather(*(client.ensure_peer(peer_id="miles") for _ in range(5)))
    assert creates == ["miles"]


async def test_different_peers_are_not_serialised_behind_one_lock(monkeypatch):
    """A single global lock would make this correct but slow; the lock is
    per-peer, so two different peers proceed concurrently."""
    client = HonchoClient("http://127.0.0.1:8000", "nora")
    assert client._peer_lock("a") is not client._peer_lock("b")
    assert client._peer_lock("a") is client._peer_lock("a")


async def test_the_session_is_created_at_the_workspace_level(server_factory):
    """The bug that made Honcho a no-op for the deployment's entire life.

    `POST /peers/{id}/sessions` is a LIST endpoint on the real server: it
    answers **200** with a pagination envelope and no `id`. `ensure_session`
    read `.get("id")` off that, got None, and `feed` returned False before
    posting anything — silently, because feeding is best-effort by contract
    and the caller only counts a failure. Both feed counters sat at 0.0 and
    nothing else noticed.

    The old test could not have caught it: its fake answered every path with a
    created session, so it asserted the implementation back to itself. This
    fake answers the way the LIVE server does — verified against it — which is
    what makes the URL load-bearing here.
    """
    calls: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append(request.path)
        if request.path.endswith("/peers/miles/sessions"):
            # what the real server returns: a list, not a creation
            return web.json_response({"items": [], "total": 0, "page": 1, "pages": 0})
        if request.path.endswith("/v3/workspaces/nora/sessions"):
            return web.json_response({"id": "nora-miles"})
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.feed(peer_id="miles", text="hello", conversation_id="c1") is True
    assert "/v3/workspaces/nora/sessions" in calls
    assert "/v3/workspaces/nora/sessions/nora-miles/messages" in calls


async def test_a_list_shaped_session_response_is_not_mistaken_for_a_created_one(

    server_factory,
):
    """The bug that made Honcho a no-op for the deployment's entire life.

    `POST /peers/{id}/sessions` is a LIST endpoint: it answers **200** with a
    pagination envelope and no `id`. `ensure_session` read `.get("id")` off it,
    got None, and `feed` returned False before posting anything — silently,
    because feeding is best-effort by contract and the caller just counts a
    failure. Both feed counters sat at 0.0 and nothing else noticed.

    A 200 that is not a created session must NOT be treated as one.
    """
    calls: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        calls.append(request.path)
        return web.json_response({"items": [], "total": 0, "page": 1, "pages": 0})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")

    assert await client.ensure_session("miles") is None
    assert await client.feed(peer_id="miles", text="hello", conversation_id="c1") is False
    assert not any("/messages" in c for c in calls), (
        "no message may be posted when the session was never created"
    )


async def test_chat_returns_the_synthesised_content(server_factory):
    """The DIALECTIC verb (Honcho v2, design/24 §4): `POST /peers/{id}/chat`
    with `{query, reasoning_level}` → `{"content": "<answer>"}`. Distinct from
    `representation`, which returns raw conclusions; chat REASONS. Probe-verified
    endpoint + fields against the live instance 2026-08-07."""
    bodies: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        if request.path.endswith("/chat"):
            bodies.append(await request.json())
            return web.json_response({"content": "Tin Shui Chi, from 天恆/天澤 mentions."})
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")
    out = await client.chat(peer_id="miles_novak", query="where does he live?")
    assert out == "Tin Shui Chi, from 天恆/天澤 mentions."
    assert bodies == [{"query": "where does he live?", "reasoning_level": "low"}]


async def test_a_base64_group_id_becomes_a_honcho_legal_session_id(server_factory):
    """live 2026-08-08: a Signal group id is standard base64 (`+` `/` `=`), and
    Honcho's session-create rejects anything but `^[a-zA-Z0-9_-]+$` with a 422 —
    which silently broke the ENTIRE feed/recall path on the real group (offline
    tests used clean ids like "c1" and never saw it). The session key is now
    sanitised base64→url-safe; reverting the sanitiser sends the raw `+/=` and
    turns this red."""
    session_ids: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        if request.path.endswith("/sessions"):
            body = await request.json()
            session_ids.append(body["id"])
            return web.json_response({"id": body["id"]})
        return web.json_response({"ok": True})

    base = await server_factory(handler)
    client = HonchoClient(base, "nora")
    group_id = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="   # a real Signal group id
    assert await client.feed(peer_id="miles_novak", text="hi", conversation_id=group_id) is True
    assert session_ids, "the session must be created, not 422'd"
    assert re.fullmatch(r"[a-zA-Z0-9_-]+", session_ids[0]), session_ids[0]


async def test_the_observer_config_retries_after_a_transient_failure(server_factory):
    """review C1: the ai_peer observe-config must RETRY on a dropped add-peers
    POST, like the human-speaker config does — not run once at session creation
    and cache the session as done. A single transient failure otherwise leaves
    Nora a non-observer of the session for the whole process, and every recall
    there reads empty. So the config re-fires on the cache-hit path (idempotent
    once it lands); reverting that turns this red."""
    peers_posts: list[dict] = []
    nora_should_fail = {"v": True}

    async def handler(request: web.Request) -> web.Response:
        if request.path.endswith("/sessions"):
            return web.json_response({"id": "sess-1"})
        if request.path.endswith("/peers"):
            body = await request.json()
            peers_posts.append(body)
            if "nora" in body and nora_should_fail["v"]:
                nora_should_fail["v"] = False       # fail once, then succeed
                return web.Response(status=500)
            return web.json_response({"ok": True})
        return web.json_response({"ok": True})

    base = await server_factory(handler)
    client = HonchoClient(base, "nora")
    await client.feed(peer_id="miles", text="a", conversation_id="c1")   # nora config fails
    await client.feed(peer_id="miles", text="b", conversation_id="c1")   # nora config retried
    nora_posts = [b for b in peers_posts if "nora" in b]
    assert len(nora_posts) >= 2, "the ai_peer observer-config must retry after a failure"


async def test_search_parses_the_message_list_and_drops_media_placeholders(server_factory):
    """`POST /search` returns a LIST (not the dict every other endpoint returns);
    `search` has its own parse → (peer_id, content) pairs, dropping media-only
    (`￼`) and blank lines. design/24 §S.3."""
    async def handler(request: web.Request) -> web.Response:
        if request.path.endswith("/search"):
            body = await request.json()
            assert body == {"query": "hotpot", "limit": 8}
            return web.json_response([
                {"peer_id": "miles_novak", "content": "打邊爐"},
                {"peer_id": "miles_novak", "content": "￼"},          # media only → dropped
                {"peer_id": "tom", "content": "   "},             # blank → dropped
            ])
        return web.json_response({"ok": True})

    base = await server_factory(handler)
    out = await HonchoClient(base, "nora").search(query="hotpot")
    assert out.reachable is True
    assert out.hits == (("miles_novak", "打邊爐"),)


async def test_a_failed_search_reports_unreachable_and_never_raises(server_factory):
    async def handler(request: web.Request) -> web.Response:
        return web.Response(status=500)

    base = await server_factory(handler)

    out = await HonchoClient(base, "nora").search(query="x")

    # NOT `== []`. A 500 means the search did not run, and that has to be
    # distinguishable from a search that ran and matched nothing — otherwise
    # `search_memory` tells the group «nothing was said here» when Honcho is
    # down (external review, 2026-08-11).
    assert out.reachable is False
    assert out.hits == ()


async def test_search_scopes_to_the_conversations_session_not_the_workspace(server_factory):
    """Kimi audit 2026-08-08: a conversation_id session-scopes the search to THIS
    channel — `/sessions/nora-conv-<safe>/search`, NOT the workspace-wide `/search`
    — so a roster member's PRIVATE DM words can't surface into this room. The
    base64 group id is sanitised into the session key on the way."""
    paths: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        paths.append(request.path)
        return web.json_response([])

    base = await server_factory(handler)
    gid = "Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34="   # a real base64 group id
    await HonchoClient(base, "nora").search(query="x", conversation_id=gid)
    assert paths and paths[0].endswith(
        "/sessions/nora-conv-Lf2L9zqeNUahRsOz6BA-sTB8aWfbuvqu9vOhpvmJi34/search"
    ), paths
    assert "/workspaces/nora/search" not in paths[0]


async def test_chat_sends_the_observed_peer_as_target_and_the_chosen_depth(server_factory):
    """`peer_id`=observer, `target`=observed (design/24 §4): `ask_memory` reads
    Nora's model of a person via `chat(peer_id="nora", target="miles_novak", …)`, and
    the model's chosen `reasoning_level` rides the body. Pins the WIRE — reverting
    the client's `if target is not None` or the reasoning_level pass-through turns
    this red (review I2, 2026-08-08)."""
    bodies: list[dict] = []

    async def handler(request: web.Request) -> web.Response:
        if request.path.endswith("/chat"):
            bodies.append(await request.json())
            return web.json_response({"content": "answer"})
        return web.json_response({"ok": True})

    base_url = await server_factory(handler)
    client = HonchoClient(base_url, "nora")
    await client.chat(
        peer_id="nora", query="does he like hotpot?", reasoning_level="high",
        target="miles_novak",
    )
    assert bodies == [
        {"query": "does he like hotpot?", "reasoning_level": "high", "target": "miles_novak"},
    ]


async def test_chat_is_none_on_empty_or_unreachable(server_factory):
    """Best-effort: empty content is «nothing to say» (None), and a non-200 is
    «could not ask» (None) — the caller never guesses a recollection."""
    async def empty(request: web.Request) -> web.Response:
        return web.json_response({"content": "  "})
    client = HonchoClient(await server_factory(empty), "nora")
    assert await client.chat(peer_id="miles_novak", query="q") is None

    async def down(request: web.Request) -> web.Response:
        return web.json_response({}, status=500)
    client2 = HonchoClient(await server_factory(down), "nora")
    assert await client2.chat(peer_id="miles_novak", query="q") is None
