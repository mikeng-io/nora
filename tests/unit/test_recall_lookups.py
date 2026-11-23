"""`recall_lookups` — the tool-call log read back as evidence.

Built through `ToolRegistry.specs(...)`, so the store arrives as a registry
collaborator exactly as production wires it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.domain.tool_call_log import ToolCallLogEntry
from nora.obs.metrics import Metrics
from nora.testing import FakeToolCallLogStore
from tests.fakes import SinkSender

CONVERSATION = "conv-1"


class _FakeStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _FakePersons:
    def __init__(self) -> None:
        self.by_person: dict = {}

    def resolve(self, platform: str, sender_id: str):
        return None


def _tools(*, tool_call_log_store=None) -> dict:
    """Every native tool, closed over one turn, keyed by name."""
    registry = ToolRegistry(tool_call_log_store=tool_call_log_store)
    room = Room(platform="signal", conversation_id=CONVERSATION, roster_source=None,
                persons=_FakePersons(), tagger=None)
    specs = registry.specs(
        store=_FakeStore(), conversation_id=CONVERSATION, conversation_type="group",
        persons=_FakePersons(), deliverer=SinkSender(), metrics=Metrics(), room=room,
    )
    return {spec.name: spec.call for spec in specs}


def _log_entry(**kw) -> ToolCallLogEntry:
    base = {
        "id": uuid.uuid4(), "conversation_id": CONVERSATION, "conversation_type": "group",
        "tag_id": "tag-0", "tool_name": "place_info", "tool_input": {"name": "Yoho Mall"},
        "tool_result": {"status": "ok", "rating": 4.2}, "created_at": datetime.now(UTC),
    }   # a dict LITERAL: ruff's C4 ruleset is enabled and C408 rejects dict()
    base.update(kw)
    return ToolCallLogEntry(**base)


async def test_recall_lookups_returns_recent_entries():
    store = FakeToolCallLogStore()
    await store.append(_log_entry())
    tools = _tools(tool_call_log_store=store)

    result = await tools["recall_lookups"]()

    assert result["status"] == "ok"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["tool_name"] == "place_info"
    assert result["entries"][0]["tool_result"] == {"status": "ok", "rating": 4.2}
    assert "age_seconds" in result["entries"][0]


async def test_recall_lookups_is_nothing_found_when_empty():
    tools = _tools(tool_call_log_store=FakeToolCallLogStore())
    result = await tools["recall_lookups"]()
    assert result["status"] == "nothing_found"


async def test_recall_lookups_unavailable_when_not_configured():
    """No store wired is an honest wiring gap, not a crash and not a
    guess."""
    tools = _tools()  # no tool_call_log_store
    result = await tools["recall_lookups"]()
    assert result["status"] == "unavailable"


async def test_recall_lookups_respects_a_smaller_limit():
    store = FakeToolCallLogStore()
    for _ in range(5):
        await store.append(_log_entry(tool_name="weather", tool_input={}))
    tools = _tools(tool_call_log_store=store)
    result = await tools["recall_lookups"](limit=2)
    assert len(result["entries"]) == 2


async def test_recall_lookups_is_unavailable_when_the_store_read_fails():
    """A store read failure is `unavailable`, never an uncaught
    exception (which Strands would wrap into an error `ToolResult` outside
    the ok/nothing_found/unavailable contract) and never `nothing_found`
    (an empty result and a failed read are different answers)."""
    class _BrokenStore:
        async def recent(self, **kwargs):
            raise ConnectionError("db unreachable")

    tools = _tools(tool_call_log_store=_BrokenStore())
    result = await tools["recall_lookups"]()
    assert result["status"] == "unavailable"
    assert "note" in result


async def test_recall_lookups_clamps_a_limit_above_the_max():
    store = FakeToolCallLogStore()
    for _ in range(40):
        await store.append(_log_entry(tool_name="weather", tool_input={}))
    tools = _tools(tool_call_log_store=store)
    result = await tools["recall_lookups"](limit=999)
    assert len(result["entries"]) == 30  # RECALL_LOOKUPS_MAX


async def test_recall_lookups_has_no_truncated_field_for_a_small_response():
    store = FakeToolCallLogStore()
    await store.append(_log_entry())
    tools = _tools(tool_call_log_store=store)
    result = await tools["recall_lookups"]()
    assert "truncated" not in result


async def test_recall_lookups_truncates_once_the_char_budget_is_spent():
    """`RECALL_LOOKUPS_MAX` (30 rows) caps count, not size — a handful of
    large `web_search`-shaped results can still blow well past a sane byte
    budget. Rows come back newest-first, so the clamp must drop the OLDEST
    entries and keep the newest, and it must say so via `truncated`."""
    store = FakeToolCallLogStore()
    big_result = {"status": "ok", "results": "x" * 3000}
    base = datetime.now(UTC)
    for i in range(5):
        await store.append(_log_entry(
            tool_name="web_search", tool_input={"query": f"q{i}"},
            tool_result={**big_result, "marker": i},
            created_at=base + timedelta(seconds=i),
        ))
    tools = _tools(tool_call_log_store=store)

    result = await tools["recall_lookups"]()

    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert len(result["entries"]) < 5
    # Newest-first, and the clamp keeps the newest — so the highest markers
    # (the ones appended last) are the ones still present.
    kept_markers = [e["tool_result"]["marker"] for e in result["entries"]]
    assert kept_markers == sorted(kept_markers, reverse=True)
    assert kept_markers[0] == 4
