"""ToolCallLog — the write path (R-48)."""

from datetime import UTC, datetime

from nora.agent.guards import ReplyGuard, ToolCallLog, _unwrap_tool_status
from nora.agent.ports import adapt
from nora.agent.registry import ToolRegistry
from nora.agent.room import Room
from nora.domain.models import PersonRef
from nora.obs.metrics import Metrics
from nora.testing import FakeAgentRunner, FakeMemoryStore, FakeToolCallLogStore, ScriptedTurn
from tests.fakes import SinkSender

CACHEABLE = frozenset({"place_info", "route", "web_search", "weather", "transit"})


def _wrapped(payload: dict) -> dict:
    """The Strands `ToolResult` shape a plain-dict-returning tool actually
    produces (`strands.tools.decorator.DecoratedFunctionTool._wrap_tool_result`):
    `content` carries `json.dumps(payload)`, not the payload itself."""
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


async def test_the_default_clock_produces_a_row_a_real_construction_would_write():
    """No test in this file omits `clock` — every one pins a fixed UTC value.
    That is how a naive `datetime.now` default would go unnoticed: the only
    construction site that matters (deliver.py, production) never passes
    `clock` either. This test constructs ToolCallLog the same way production
    does — no clock override — and proves a row actually lands."""
    store = FakeToolCallLogStore()
    log = ToolCallLog(
        conversation_id="conv-1", conversation_type="group", tag_id="tag-1",
        cacheable=CACHEABLE, store=store, metrics=Metrics(),
    )
    event = _Event(
        tool_use={"name": "place_info", "input": {"name": "Yoho Mall"}},
        result=_wrapped({"status": "ok", "rating": 4.2}),
    )
    await log._after_tool_async(event)
    rows = await store.recent(
        conversation_id="conv-1", since=datetime(2020, 1, 1, tzinfo=UTC), limit=10,
    )
    assert len(rows) == 1


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


async def test_a_successful_write_increments_the_writes_counter():
    metrics = Metrics()
    log = ToolCallLog(
        conversation_id="conv-1", conversation_type="group", tag_id="tag-1",
        cacheable=CACHEABLE, store=FakeToolCallLogStore(), metrics=metrics,
        clock=lambda: datetime(2026, 8, 14, tzinfo=UTC),
    )
    event = _Event(
        tool_use={"name": "place_info", "input": {"name": "Yoho Mall"}},
        result=_wrapped({"status": "ok", "rating": 4.2}),
    )
    await log._after_tool_async(event)
    assert metrics.get(
        "nora_tool_call_log_writes_total", tool="place_info", outcome="ok",
    ) == 1


async def test_a_failed_write_increments_the_writes_counter_as_failed():
    class _BrokenStore:
        async def append(self, entry):
            raise ConnectionError("db unreachable")

    metrics = Metrics()
    log = ToolCallLog(
        conversation_id="conv-1", conversation_type="group", tag_id="tag-1",
        cacheable=CACHEABLE, store=_BrokenStore(), metrics=metrics,
        clock=lambda: datetime(2026, 8, 14, tzinfo=UTC),
    )
    event = _Event(
        tool_use={"name": "place_info", "input": {"name": "Yoho Mall"}},
        result=_wrapped({"status": "ok", "rating": 4.2}),
    )
    await log._after_tool_async(event)
    assert metrics.get(
        "nora_tool_call_log_writes_total", tool="place_info", outcome="failed",
    ) == 1


# ---- end-to-end: the whole chain, through the REAL wired path -----------
#
# Everything above drives `ToolCallLog._after_tool_async` directly with a
# hand-built event — necessary for the fast, focused cases above, but it
# cannot catch a defect in the WIRING between the real tools, the real
# `FakeAgentRunner` double, and the real `ReplyGuard`. A double that
# hardcoded `content: [{"text": "True"}]` for every successful call would
# keep `ToolCallLog` from ever seeing a real payload or writing a real row,
# so `FakeAgentRunner` threads the tool's REAL return value into the
# after-tool hook. This test goes through `ToolRegistry.specs()` (the same
# call `nora.engagement.stages.deliver.build` makes), `adapt()` (the same
# Strands conversion production uses), and `FakeAgentRunner` — the one
# test that proves place_info → a written row → recall_lookups seeing it
# work TOGETHER.

CONVERSATION = "conv-e2e"
MILES = PersonRef(person_id="miles", name="Miles Novak", honcho_peer_id="miles")


class _FakeRoute:
    """The `route_source` collaborator `place_info` needs — just enough of
    `nora.infra.routes.RouteClient`'s surface for this scenario."""

    async def place_info(self, name: str) -> dict:
        return {"status": "ok", "rating": 4.2, "name": name}


class _FakeStore:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _FakePersons:
    def __init__(self, *refs: PersonRef) -> None:
        self.by_person = {ref.person_id: ref for ref in refs}

    def resolve(self, platform: str, sender_id: str):
        return None


def _e2e_room() -> Room:
    return Room(
        platform="signal", conversation_id=CONVERSATION, roster_source=None,
        persons=_FakePersons(MILES), tagger=MILES,
    )


async def _run_the_chain(*, tool_call_log_clock=None) -> tuple[Metrics, FakeToolCallLogStore, dict]:
    """Builds the real tools + real guard + real ToolCallLog, exactly the
    shape `deliver.build` wires them, and replays place_info → recall_lookups
    through `FakeAgentRunner`. Returns the `Metrics` and the
    `FakeToolCallLogStore` so the caller can inspect the written row.

    `tool_call_log_clock` defaults to None — meaning `ToolCallLog` is
    constructed WITHOUT a `clock=` override, the same as the one production
    construction site (`deliver.build`). A caller may pass the naive
    `datetime.now` here to reproduce the naive-clock failure.
    """
    metrics = Metrics()
    store = FakeToolCallLogStore()
    registry = ToolRegistry(route=_FakeRoute(), tool_call_log_store=store)
    specs = registry.specs(
        store=_FakeStore(), conversation_id=CONVERSATION, conversation_type="group",
        persons=_FakePersons(MILES), honcho=FakeMemoryStore(), deliverer=SinkSender(),
        metrics=metrics, room=_e2e_room(),
    )
    specs_by_name = {s.name: s for s in specs}
    tools = [adapt(s) for s in specs]

    guard = ReplyGuard(
        conversation_id=CONVERSATION, budgets=registry.budgets(),
        terminal=registry.terminal_tools(), metrics=metrics,
    )
    tool_call_log_kwargs: dict = {
        "conversation_id": CONVERSATION, "conversation_type": "group",
        "tag_id": "tag-e2e", "cacheable": registry.cacheable_tools(),
        "store": store, "metrics": metrics,
    }
    if tool_call_log_clock is not None:
        tool_call_log_kwargs["clock"] = tool_call_log_clock
    tool_call_log = ToolCallLog(**tool_call_log_kwargs)

    runner = FakeAgentRunner([
        ScriptedTurn(tool_calls=[("place_info", {"name": "Yoho Mall"})], final_text=""),
        ScriptedTurn(tool_calls=[("recall_lookups", {})], final_text="on it"),
    ])
    await runner.invoke(
        doctrine=None, tools=tools, pre_fill=[], guard=guard,
        tool_call_log=tool_call_log, tag_message="", limits={}, ctx=None,
        trace_attributes={},
    )

    return metrics, store, specs_by_name


async def test_the_whole_chain_writes_and_reads_together():
    metrics, store, specs_by_name = await _run_the_chain()

    rows = await store.recent(
        conversation_id=CONVERSATION, since=datetime(2020, 1, 1, tzinfo=UTC), limit=10,
    )
    assert len(rows) == 1
    assert rows[0].tool_name == "place_info"
    assert metrics.get(
        "nora_tool_call_log_writes_total", tool="place_info", outcome="ok",
    ) == 1

    # Independently re-exercise `recall_lookups` against the SAME store the
    # scripted turn's `place_info` call wrote to — the real production
    # closure from `registry.specs()`, not a hand-rolled read. Proves the
    # READ half of the chain sees what the WRITE half (above) produced.
    recall_result = await specs_by_name["recall_lookups"].call()
    assert recall_result["status"] == "ok"
    assert recall_result["entries"][0]["tool_name"] == "place_info"


async def test_the_whole_chain_goes_red_if_fix_1s_bug_returns():
    """The mutation proof: passing a naive-clock default explicitly makes the
    write fail silently, behind `ToolCallLog`'s own swallow-and-log — so no row
    ever lands and this end-to-end chain cannot see one either."""
    metrics, store, _specs_by_name = await _run_the_chain(tool_call_log_clock=datetime.now)
    rows = await store.recent(
        conversation_id=CONVERSATION, since=datetime(2020, 1, 1, tzinfo=UTC), limit=10,
    )
    assert rows == []
    assert metrics.get(
        "nora_tool_call_log_writes_total", tool="place_info", outcome="ok",
    ) == 0


# ---- ADR 0015: recall_lookups' statuses carry their own note ----------------


class _RaisingToolCallLogStore(FakeToolCallLogStore):
    async def recent(self, **_kwargs):
        raise ConnectionError("tool call log unreachable")


async def test_every_non_ok_recall_lookups_status_carries_a_note():
    """The description only NAMES `nothing_found` and `unavailable`. That a
    failed read is not «I never checked» (the reason the tool exists) is the
    result's `note`, on each path, dash-free."""
    def _recall(store):
        specs = ToolRegistry(tool_call_log_store=store).specs(
            store=_FakeStore(), conversation_id=CONVERSATION, conversation_type="group",
            persons=_FakePersons(MILES), honcho=FakeMemoryStore(), deliverer=SinkSender(),
            metrics=Metrics(), room=_e2e_room(),
        )
        return next(s for s in specs if s.name == "recall_lookups").call

    cases = {
        "unavailable/not_wired": _recall(None),
        "unavailable/read_failed": _recall(_RaisingToolCallLogStore()),
        "nothing_found/empty": _recall(FakeToolCallLogStore()),
    }
    for case, call in cases.items():
        out = await call()
        assert out["status"] == case.split("/")[0], (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert "—" not in out["note"] and "–" not in out["note"], case
