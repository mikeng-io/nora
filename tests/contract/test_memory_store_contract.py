"""The `MemoryStore` contract — one behavioural suite BOTH implementations pass
(CLAUDE.md: a port never gains a second implementation, including a test fake,
without a shared contract suite).

This is the write path Honcho rides, and the ONE write a backfill cannot undo
— Honcho keeps derived conclusions, not the source text. A fake that invents
a shape no live probe checked hides a dead endpoint (`ensure_session` POSTing
to a LIST endpoint, the dead `peer_card`, `parse_event` requiring `method`).
This suite binds `FakeMemoryStore` to `HonchoClient`'s real behaviour so that
class of drift turns a test red.

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
        self._search_hits: list[dict] = []
        self._search_ok = True

    async def start(self) -> None:
        async def handler(request: web.Request) -> web.Response:
            if request.path.endswith("/search"):
                if not self._search_ok:
                    # Honcho's real outage shape at this endpoint. The adapter
                    # swallows it, so the ONLY way the caller can tell this from
                    # a genuine miss is the bit `MessageHits` carries.
                    return web.json_response({"error": "boom"}, status=503)
                return web.json_response(self._search_hits)   # a LIST, not a dict
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

    def break_search(self) -> None:
        self._search_ok = False

    def landed(self) -> list[tuple[str, str]]:
        # (peer_id, text) — the session id is deterministic per conversation, so
        # the peer placement is the contract-relevant part.
        return [(m["peer_id"], m["content"]) for m in self._messages]

    def seed_reasoned_answer(self, text: str) -> None:
        self._chat_answer = text        # what the dialectic /chat will synthesise

    def seed_message(self, peer_id: str, content: str) -> None:
        self._search_hits.append({"peer_id": peer_id, "content": content})


class _FakeAdapter:
    def __init__(self) -> None:
        self.store = FakeMemoryStore()

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    def break_it(self) -> None:
        self.store.fail = True

    def break_search(self) -> None:
        self.store.fail = True

    def landed(self) -> list[tuple[str, str]]:
        return [(peer, text) for peer, text, _conv in self.store.fed]

    def seed_reasoned_answer(self, text: str) -> None:
        # The fake synthesises `chat` from the observed peer's conclusions.
        self.store.conclusions["miles"] = [text]

    def seed_message(self, peer_id: str, content: str) -> None:
        self.store.messages.append((peer_id, content))


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
    Honcho failure is a session that cannot be created."""
    adapter.break_it()
    result = await adapter.store.feed(peer_id="miles", text="x", conversation_id="g1")
    assert result is False
    assert adapter.landed() == []


async def test_feed_and_ensure_peer_are_keyword_only(adapter):
    """The port is keyword-only; a positional call must fail loudly, not bind the
    wrong argument. A fake that drifted positional would hide it — this pins
    both implementations to the port's shape."""
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
    """The dialectic verb: both implementations
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


async def test_search_returns_peer_content_pairs_and_is_empty_when_nothing_matches(adapter):
    """The raw-message search verb, on the port so both backends
    agree: seeded messages come back as (peer_id, content) pairs, and an unseeded
    search is empty (never None, never a raised error) — the caller's roster
    filter and R-18 screen run on top of that.

    A REACHED search that matched nothing is `reachable=True` with no hits. That
    is the half `search_memory` is entitled to report as «nothing was said»."""
    empty = await adapter.store.search(query="hotpot")
    assert empty.reachable is True
    assert empty.hits == ()

    adapter.seed_message("miles", "打邊爐 tonight?")
    found = await adapter.store.search(query="hotpot")
    assert found.reachable is True
    assert found.hits == (("miles", "打邊爐 tonight?"),)


async def test_a_search_that_could_not_run_is_distinguishable_from_one_that_found_nothing(
    adapter,
):
    """The bit that cannot be recovered downstream, pinned on the PORT so a
    backend cannot quietly drop it.

    `search` answers `[]` for a genuine miss only; a transport failure, a >=400
    and a malformed body must be distinguishable from it. Otherwise
    `search_memory` would report all four as `nothing_found` — «I couldn't find
    anything like that in what's been said here» — and count it `ok`. An ERROR
    reading as a PASS, which R-27 exists to forbid, invisible because "nothing
    matched" is a true-sounding sentence for a search nobody ran.

    An `except` in the CALLER cannot fix it: the adapter catches everything
    itself, so that handler would be unreachable code that reads as a fix. The
    signal has to come from the backend."""
    adapter.seed_message("miles", "打邊爐 tonight?")
    adapter.break_search()

    out = await adapter.store.search(query="hotpot")

    assert out.reachable is False, (
        "an unreachable search reports reachable=True, so an outage is "
        "indistinguishable from a miss and the caller will say «nothing was said»"
    )
    assert out.hits == ()
