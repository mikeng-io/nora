"""ToolCallLog — the write path (R-46, tool-recall-log spec §3)."""

from datetime import UTC, datetime

from nora.agent.guards import ToolCallLog, _unwrap_tool_status
from nora.obs.metrics import Metrics
from nora.testing import FakeToolCallLogStore

CACHEABLE = frozenset({"place_info", "route", "web_search", "weather", "transit"})


def _wrapped(payload: dict) -> dict:
    """The Strands `ToolResult` shape a plain-dict-returning tool actually
    produces (`strands.tools.decorator.DecoratedFunctionTool._wrap_tool_result`
    — verified: `content` carries `json.dumps(payload)`, not the payload
    itself)."""
    import json
    return {"status": "success", "content": [{"text": json.dumps(payload)}], "toolUseId": "x"}


class _Event:
    def __init__(self, *, tool_use, result, cancel_message=None, exception=None):
        self.tool_use = tool_use
        self.result = result
        self.cancel_message = cancel_message
        self.exception = exception


def _log(store=None, clock=None):
    return ToolCallLog(
        conversation_id="conv-1", conversation_type="group", tag_id="tag-1",
        cacheable=CACHEABLE, store=store or FakeToolCallLogStore(),
        metrics=Metrics(), clock=clock or (lambda: datetime(2026, 8, 14, tzinfo=UTC)),
    )


def test_unwrap_tool_status_reads_the_real_status_not_strands_own():
    assert _unwrap_tool_status(_wrapped({"status": "ok", "rating": 4.2})) == "ok"


def test_unwrap_tool_status_handles_a_malformed_result():
    assert _unwrap_tool_status({"status": "success"}) is None
    assert _unwrap_tool_status({}) is None


async def test_a_cacheable_ok_call_is_logged():
    store = FakeToolCallLogStore()
    log = _log(store=store)
    event = _Event(
        tool_use={"name": "place_info", "input": {"name": "Yoho Mall"}},
        result=_wrapped({"status": "ok", "rating": 4.2}),
    )
    await log._after_tool_async(event)
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2020, 1, 1, tzinfo=UTC), limit=10,
    )
    assert len(rows) == 1
    assert rows[0].tool_name == "place_info"
    assert rows[0].tool_result == {"status": "ok", "rating": 4.2}


async def test_a_non_cacheable_tool_is_never_logged():
    store = FakeToolCallLogStore()
    log = _log(store=store)
    event = _Event(
        tool_use={"name": "load_transcript", "input": {"limit": 40}},
        result=_wrapped({"messages": []}),
    )
    await log._after_tool_async(event)
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2020, 1, 1, tzinfo=UTC), limit=10,
    )
    assert rows == []


async def test_a_refused_call_is_never_logged():
    store = FakeToolCallLogStore()
    log = _log(store=store)
    event = _Event(
        tool_use={"name": "place_info", "input": {"name": "陳生"}},
        result=_wrapped({"status": "refused", "note": "..."}),
    )
    await log._after_tool_async(event)
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2020, 1, 1, tzinfo=UTC), limit=10,
    )
    assert rows == []


async def test_a_vetoed_call_is_never_logged():
    store = FakeToolCallLogStore()
    log = _log(store=store)
    event = _Event(
        tool_use={"name": "place_info", "input": {"name": "Yoho Mall"}},
        result=_wrapped({"status": "ok"}),
        cancel_message="R-7: budget exceeded",
    )
    await log._after_tool_async(event)
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2020, 1, 1, tzinfo=UTC), limit=10,
    )
    assert rows == []


async def test_a_write_failure_is_swallowed_not_raised():
    class _BrokenStore:
        async def append(self, entry):
            raise ConnectionError("db unreachable")

        async def recent(self, **kwargs):
            return []

    log = _log(store=_BrokenStore())
    event = _Event(
        tool_use={"name": "place_info", "input": {"name": "Yoho Mall"}},
        result=_wrapped({"status": "ok", "rating": 4.2}),
    )
    await log._after_tool_async(event)  # must not raise
