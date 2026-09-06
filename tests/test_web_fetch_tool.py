"""`web_fetch`'s own closure (DEC-116) — the egress screen, the unwired/raising
degradations, and that a refused/unwired call never reaches the provider.

Mirrors `tests/test_reminder_tools.py`'s lightweight `_tools` pattern: built
through `ToolRegistry.specs(...)`, no database, no real Tavily. `web_search`'s
provider-call path is covered by `tests/test_tavily.py`; this file is about the
closure IN `nora.agent.tools` that sits in front of it — the part
`tests/unit/test_tool_registry.py`/`tests/test_tools.py` (name, budget, tier)
and `tests/test_tavily.py` (provider behaviour) do not reach.
"""

from __future__ import annotations

from typing import Any

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.domain.models import PersonRef
from nora.obs.metrics import Metrics
from nora.testing import FakeMemoryStore
from tests.fakes import SinkSender

MILES = PersonRef(person_id="miles", name="Miles Novak", honcho_peer_id="miles")


class _FakeStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _FakePersons:
    def __init__(self, *refs: PersonRef) -> None:
        self.by_person = {ref.person_id: ref for ref in refs}

    def resolve(self, platform: str, sender_id: str):
        return None


class _FakeSearchProvider:
    """`SearchProvider` double that records every `fetch` call it actually
    receives — the probe a screen that refuses BEFORE the call needs, the same
    shape `web_search`'s own `calls == []` comment describes."""

    def __init__(self, *, raises: bool = False) -> None:
        self.fetch_calls: list[str] = []
        self._raises = raises

    async def search(self, *, query: str) -> dict[str, Any]:
        return {"status": "ok", "results": []}

    async def fetch(self, *, url: str) -> dict[str, Any]:
        self.fetch_calls.append(url)
        if self._raises:
            raise RuntimeError("boom")
        # `text`, NOT `content` — same Strands-envelope invariant as the real
        # providers (a dict carrying both `status` and `content` passes
        # through `_wrap_tool_result` unwrapped and reads as "empty").
        return {"status": "ok", "url": url, "text": "the page"}


def _room() -> Room:
    return Room(
        platform="signal", conversation_id="g1", roster_source=None,
        persons=_FakePersons(MILES), tagger=MILES,
    )


def _tools(*, search=None, metrics: Metrics | None = None) -> dict:
    registry = ToolRegistry(search=search)
    specs = registry.specs(
        store=_FakeStore(), conversation_id="g1", conversation_type="group",
        persons=_FakePersons(MILES), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=metrics or Metrics(), room=_room(),
    )
    return {spec.name: spec.call for spec in specs}


async def test_unwired_answers_unavailable():
    tools = _tools(search=None)
    out = await tools["web_fetch"](url="https://example.com/a")
    assert out["status"] == "unavailable"


async def test_a_non_http_scheme_is_refused_before_the_provider_is_called():
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="file:///etc/passwd")
    assert out["status"] == "nothing_found"
    assert fake.fetch_calls == []


async def test_a_bare_string_with_no_scheme_is_refused():
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="example.com/a")
    assert out["status"] == "nothing_found"
    assert fake.fetch_calls == []


async def test_an_identifier_in_the_url_is_refused_before_the_provider_is_called():
    """The egress concern is not SSRF (Tavily connects, never Nora) — it is that
    the URL itself leaves to a THIRD PARTY, model-chosen, and can carry an
    identifier the same way a `web_search` query can (design/14 §2.2)."""
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="https://example.com/?n=+85212345678")
    assert out["status"] == "nothing_found"
    assert fake.fetch_calls == []


async def test_a_percent_encoded_identifier_in_the_url_is_also_refused():
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="https://example.com/?n=%2B85212345678")
    assert out["status"] == "nothing_found"
    assert fake.fetch_calls == []


async def test_a_coordinate_in_the_url_is_refused_before_the_provider_is_called():
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="https://example.com/?at=22.31,114.16")
    assert out["status"] == "nothing_found"
    assert fake.fetch_calls == []


async def test_an_ordinary_url_reaches_the_provider():
    fake = _FakeSearchProvider()
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="https://example.com/a")
    assert out["status"] == "ok"
    assert fake.fetch_calls == ["https://example.com/a"]


async def test_a_raising_provider_degrades_to_unavailable():
    fake = _FakeSearchProvider(raises=True)
    tools = _tools(search=fake)
    out = await tools["web_fetch"](url="https://example.com/a")
    assert out["status"] == "unavailable"
