"""`HistoricalRead` — the as-of guard the historical (backfill) lane wraps
every session read in, and the clock seam that binds a read's `now` to the
evidence instant.

No model and no Strands: the wrapped tool is a recording double, and each
assertion reads either the answer the wrapper emitted or the `tool_use` the
inner tool was (or was not) called with.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from strands.types._events import ToolResultEvent

from nora.agent.comprehend_tools import (
    HISTORICAL_READ_UNAVAILABLE,
    HistoricalRead,
    as_of_reads,
)
from nora.agent.tools import _HKT, NATIVE_TOOLS

#: The evidence instant every test reads as-of: 2014-06-01 20:00:00 HKT.
AS_OF = datetime(2014, 6, 1, 12, 0, tzinfo=UTC)


class _Inner:
    """A read-tool-shaped double: records the `tool_use` it was streamed
    with, answers `{"status": "ok"}` when run at all."""

    def __init__(self, name: str) -> None:
        self.tool_name = name
        self.tool_type = "python"
        self.tool_spec: ClassVar = {
            "name": name, "description": "generated read",
            "inputSchema": {"json": {"type": "object", "properties": {}}},
        }
        self.calls: list[dict[str, Any]] = []

    async def stream(self, tool_use: dict[str, Any], invocation_state: Any, **kw: Any) -> Any:
        self.calls.append(tool_use)
        yield ToolResultEvent({"toolUseId": tool_use["toolUseId"],
            "content": [{"json": {"status": "ok"}}], "status": "success"})


def _use(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"toolUseId": "t", "input": arguments}


async def _answer(tool: HistoricalRead, arguments: dict[str, Any]) -> dict[str, Any]:
    events = [e async for e in tool.stream(_use(arguments), {})]
    return events[-1].tool_result["content"][0]["json"]


# ---- the current-head table ------------------------------------------------

def test_every_unavailable_name_is_a_real_read_tool():
    """The table can only refuse what exists: a renamed or dropped tool must
    not linger here answering for a name nothing builds."""
    assert HISTORICAL_READ_UNAVAILABLE
    assert set(HISTORICAL_READ_UNAVAILABLE) <= set(NATIVE_TOOLS)
    assert all(NATIVE_TOOLS[n].read_only for n in HISTORICAL_READ_UNAVAILABLE)


@pytest.mark.parametrize("name", sorted(
    n for n in ("about", "memory_search", "memory_read", "note_read",
                "note_search", "memo_read", "social_read", "room_state_read",
                "poll_list", "poll_read", "lookup_person",
                "who_said", "group_members", "event_search", "rollup_read",
                "standing_read", "character_read", "routine_read", "where_been",
                "media_search", "ask_memory", "search_memory",
                "search_memory_workspace")
    if n in HISTORICAL_READ_UNAVAILABLE
))
async def test_a_current_head_read_answers_unavailable_and_the_inner_tool_never_runs(name):
    """Fail-closed, named, and nothing read: `unavailable` with the real
    limitation, never `nothing_found`, and the inner tool's store is never
    touched — a miss is not evidence the fold was empty."""
    inner = _Inner(name)
    answer = await _answer(HistoricalRead(inner, as_of=AS_OF), {})
    assert answer["status"] == "unavailable" and answer["note"]
    assert inner.calls == []


# ---- record_query's branches ------------------------------------------------

@pytest.mark.parametrize("arguments", [
    {"kind": "poll"},
    {"object": "poll:000000000000"},
    {"scope": "all_rooms", "start": "2014-05-01"},
])
async def test_record_query_branches_that_read_current_state_refuse(arguments):
    """The typed source-object branch folds and lists as they stand now, and
    `all_rooms` checks today's member list — neither can be read as of the
    evidence instant, so each answers `unavailable` and never runs."""
    inner = _Inner("record_query")
    answer = await _answer(HistoricalRead(inner, as_of=AS_OF), arguments)
    assert answer["status"] == "unavailable"
    assert inner.calls == []


async def test_record_query_message_reads_pass_through():
    """The messages branch stays: its interval is already bounded at the
    instant the session's clock is bound to, so the call runs untouched."""
    inner = _Inner("record_query")
    arguments = {"start": "2014-05-01", "end": "2014-05-31", "text": "dinner"}
    answer = await _answer(HistoricalRead(inner, as_of=AS_OF), arguments)
    assert answer["status"] == "ok"
    assert inner.calls[0]["input"] == arguments


# ---- the transcript reads' bound --------------------------------------------

def _expected_before(as_of: datetime) -> str:
    instant = datetime.fromtimestamp(
        (int(as_of.timestamp() * 1000) + 1) / 1000, _HKT)
    return instant.strftime("%Y-%m-%d %H:%M:%S.") + f"{instant.microsecond // 1000:03d}"


@pytest.mark.parametrize("name", ["load_transcript", "room_read"])
async def test_a_transcript_read_without_before_is_bound_to_the_evidence_instant(name):
    """An unbounded `load_transcript`/`room_read` reads the store's NEWEST
    rows — future speech in a replay — so the guard rewrites `before` to the
    evidence instant (one millisecond up: `_before_bound`'s exclusive bound
    lands on it exactly)."""
    inner = _Inner(name)
    await _answer(HistoricalRead(inner, as_of=AS_OF), {"limit": 5})
    assert inner.calls[0]["input"]["before"] == _expected_before(AS_OF) == \
        "2014-06-01 20:00:00.001"
    assert inner.calls[0]["input"]["limit"] == 5


@pytest.mark.parametrize("before", ["2030-01-01", "2014-06-02", ""])
async def test_a_bound_at_or_after_the_instant_is_rebound_not_read_live(before):
    """`before` at-or-after `now` is `read the newest` to the tool — the same
    hole as an absent bound — so a model's own value cannot widen the read."""
    inner = _Inner("load_transcript")
    await _answer(HistoricalRead(inner, as_of=AS_OF), {"before": before})
    assert inner.calls[0]["input"]["before"] == "2014-06-01 20:00:00.001"


async def test_a_bound_before_the_instant_is_honored():
    inner = _Inner("load_transcript")
    await _answer(HistoricalRead(inner, as_of=AS_OF), {"before": "2013-01-01"})
    assert inner.calls[0]["input"]["before"] == "2013-01-01"


async def test_an_unreadable_bound_is_left_for_the_tool_to_refuse_itself():
    """The guard does not adjudicate shapes: `load_transcript` reports its own
    unreadable `before`, the same answer a live call gives."""
    inner = _Inner("load_transcript")
    await _answer(HistoricalRead(inner, as_of=AS_OF), {"before": "next tuesday-ish"})
    assert inner.calls[0]["input"]["before"] == "next tuesday-ish"


# ---- pass-through reads ------------------------------------------------------

@pytest.mark.parametrize("name,arguments", [
    ("record_query", {"start": "2014-05-01"}),
    ("web_search", {"query": "dinner"}),
    ("web_fetch", {"url": "https://example.invalid/"}),
    ("media_fetch", {"code": "generated-code"}),
    ("document_read", {"document_id": "generated-doc"}),
    ("file_fetch", {"code": "generated-code"}),
    ("place_info", {"name": "generated-place"}),
    ("instagram_read", {"handle": "generated-handle", "what": "profile"}),
    ("x_search", {"query": "dinner"}),
])
async def test_reads_with_honest_as_of_semantics_pass_through_verbatim(name, arguments):
    """Bounded-by-interval reads, stored immutable objects and public fetches
    keep their call AND their own semantics — public fetches keep their real
    fetch time; a stored object is the same artifact at every instant."""
    inner = _Inner(name)
    answer = await _answer(HistoricalRead(inner, as_of=AS_OF), arguments)
    assert answer["status"] == "ok"
    assert inner.calls[0]["input"] == arguments


async def test_as_of_reads_wraps_tool_objects_and_leaves_functions_alone():
    """Only Strands-shaped tools are wrapped: a caller's plain-function
    stand-in keeps its own behavior (and its own `__name__` dispatch)."""
    async def fn(**kw: Any) -> dict[str, Any]:
        return {"status": "ok"}
    fn.__name__ = "record_query"  # type: ignore[attr-defined]
    wrapped = as_of_reads([_Inner("note_read"), fn], as_of=AS_OF)
    assert isinstance(wrapped[0], HistoricalRead)
    assert wrapped[1] is fn
    assert wrapped[0].tool_name == "note_read"
    assert wrapped[0].tool_type == "python"


# ---- the clock seam ----------------------------------------------------------

def test_the_source_binds_the_clock_through_the_registrys_own_seam():
    """`clock` is not pushed into tools: `RegistryToolSource` swaps the
    registry's injected clock by `with_clock` — the same copy-seam
    `with_memory_search` uses — so every built tool's `now_fn` reads the
    session instant and a live build (`clock=None`) touches nothing."""
    from nora.agent.dreamer import RegistryToolSource
    from nora.obs.metrics import Metrics

    class _Registry:
        def __init__(self) -> None:
            self.swapped: list[Any] = []
            self.surfaces = 0

        def read_only_tools(self) -> frozenset[str]:
            return frozenset({"record_query"})

        def with_clock(self, clock: Any) -> _Registry:
            self.swapped.append(clock)
            return self

        def surface(self, **kw: Any) -> Any:
            self.surfaces += 1
            return SimpleNamespace(tools=())

    registry = _Registry()
    source = RegistryToolSource(
        registry=registry, store=None, persons=None, honcho=None,  # type: ignore[arg-type]
        metrics=Metrics())
    source.build(key="no-such-room", names=frozenset({"record_query"}))
    assert registry.swapped == [] and registry.surfaces == 1
    source.build(key="no-such-room", names=frozenset({"record_query"}), clock=lambda: AS_OF)
    assert len(registry.swapped) == 1 and registry.surfaces == 2
