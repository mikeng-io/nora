"""The `MemoryStore` contract — one behavioural suite BOTH implementations pass
(CLAUDE.md: a port never gains a second implementation, including a test fake,
without a shared contract suite; council 2026-08-07).

This is the write path Honcho v2 (design/24) rides, and the ONE write a backfill
cannot undo — Honcho keeps derived conclusions, not the source text. Its fakes
were the exact mechanism by which three dead Honcho endpoints shipped invisibly
(`ensure_session` POSTing to a LIST endpoint, the dead `peer_card`, `parse_event`
requiring `method`): each hid because a fake invented a shape no live probe
checked. This suite binds `FakeMemoryStore` to `HonchoClient`'s real behaviour so
that class of drift turns a test red.

The two are driven through a small adapter each exposes: the store, a `landed()`
view of what was fed as `(peer_id, text, conversation_id)`, and a `break_it()`
that makes the next feed fail the way that implementation fails (a session that
cannot be created for Honcho; the down-switch for the fake).
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.honcho import HonchoClient
from nora.testing.fakes import FakeMemoryStore


class _HonchoAdapter:
    """`HonchoClient` against a local server that records message bodies and can
    be switched to answer a session-create with no id (its real failure mode)."""

    def __init__(self) -> None:
        self._messages: list[dict] = []
        self._session_has_id = True
        self._chat_answer = ""

    async def start(self) -> None:
        async def handler(request: web.Request) -> web.Response:
            if request.path.endswith("/sessions"):
                return web.json_response({} if not self._session_has_id else {"id": "sess-1"})
            if request.path.endswith("/messages"):
                body = await request.json()
                # Honcho tags each message with its own peer_id; the session in
                # the path is the conversation's. Reconstruct the landed tuple.
                conv = request.path.split("/sessions/")[1].split("/")[0]
                for m in body.get("messages", []):
                    self._messages.append({**m, "_session": conv})
                return web.json_response({"ok": True})
            if request.path.endswith("/chat"):
                return web.json_response({"content": self._chat_answer})
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        self._server = TestServer(app)
        await self._server.start_server()
        self.store = HonchoClient(str(self._server.make_url("/")).rstrip("/"), "nora")

    async def stop(self) -> None:
        await self._server.close()

    def break_it(self) -> None:
        self._session_has_id = False   # session cannot be created → feed must be False

    def landed(self) -> list[tuple[str, str]]:
        # (peer_id, text) — the session id is deterministic per conversation, so
        # the peer placement is the contract-relevant part.
        return [(m["peer_id"], m["content"]) for m in self._messages]

    def seed_reasoned_answer(self, text: str) -> None:
        self._chat_answer = text        # what the dialectic /chat will synthesise


class _FakeAdapter:
    def __init__(self) -> None:
        self.store = FakeMemoryStore()

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    def break_it(self) -> None:
        self.store.fail = True

    def landed(self) -> list[tuple[str, str]]:
        return [(peer, text) for peer, text, _conv in self.store.fed]

    def seed_reasoned_answer(self, text: str) -> None:
        # The fake synthesises `chat` from the observed peer's conclusions.
        self.store.conclusions["miles"] = [text]


@pytest.fixture(params=["honcho", "fake"])
async def adapter(request):
    a = _HonchoAdapter() if request.param == "honcho" else _FakeAdapter()
    await a.start()
    yield a
    await a.stop()


async def test_feed_lands_the_message_under_the_given_peer(adapter):
    """The core contract: a message feeds under the SPEAKER's peer (DEC-36 at the
    message level). Both a human peer and the Nora peer into one conversation."""
    await adapter.store.ensure_peer(peer_id="miles")
    await adapter.store.ensure_peer(peer_id="nora")
    assert await adapter.store.feed(peer_id="miles", text="幾點食飯", conversation_id="g1") is True
    assert await adapter.store.feed(peer_id="nora", text="七點半", conversation_id="g1") is True
    assert adapter.landed() == [("miles", "幾點食飯"), ("nora", "七點半")]


async def test_feed_returns_false_when_it_cannot_land_and_never_raises(adapter):
    """Best-effort by contract (the caller counts a failure and moves on). The
    Honcho failure is a session that cannot be created — the exact shape that
    once returned False without posting for the deployment's whole life."""
    adapter.break_it()
    result = await adapter.store.feed(peer_id="miles", text="x", conversation_id="g1")
    assert result is False
    assert adapter.landed() == []


async def test_feed_and_ensure_peer_are_keyword_only(adapter):
    """The port is keyword-only; a positional call must fail loudly, not bind the
    wrong argument. `tests/fakes.py`'s older fake drifted positional and hid it —
    this pins both implementations to the port's shape."""
    with pytest.raises(TypeError):
        await adapter.store.feed("miles", "x", "g1")           # type: ignore[misc]
    with pytest.raises(TypeError):
        await adapter.store.ensure_peer("miles")               # type: ignore[misc]


async def test_metadata_rides_the_message_without_touching_the_text(adapter):
    """`{ts}`-style metadata is structured, never concatenated into the text —
    so it cannot inject into a derived conclusion. The text lands verbatim."""
    await adapter.store.feed(
        peer_id="miles", text="hello", conversation_id="g1", metadata={"ts": 123},
    )
    assert adapter.landed() == [("miles", "hello")]
    assert "123" not in json.dumps(adapter.landed(), ensure_ascii=False)


async def test_chat_returns_the_reasoned_string_when_there_is_something_to_say(adapter):
    """The dialectic verb, now on the port (design/24 §4): both implementations
    return a non-empty string when the store can reason, so `ask_memory` gets an
    answer. Without this in the shared suite, `chat` joins `representation` as a
    port method whose fake behaviour is bound to nothing real — the drift class
    this file exists to catch."""
    adapter.seed_reasoned_answer("Tin Shui Chi, from repeated 天恆 mentions")
    out = await adapter.store.chat(peer_id="miles", query="where does he live?")
    assert out == "Tin Shui Chi, from repeated 天恆 mentions"


async def test_chat_is_none_when_there_is_nothing_to_say(adapter):
    """The best-effort None contract: nothing seeded → the live instance answers
    200 with an empty `content` and the fake has no conclusions, and BOTH collapse
    that to None (never an empty string the tool would present as an answer). The
    caller says «I couldn't recall that», never guesses."""
    out = await adapter.store.chat(peer_id="miles", query="where does he live?")
    assert out is None
