"""`HistoricalRead` — the as-of guard the historical (backfill) lane wraps
every session read in — and the temporal bound the reads themselves carry.

The wrapper's job shrank: the BOUND is the session's clock, which every built
read derives its as-of ceiling from (`ToolRegistry.with_clock` binds
`now_fn`; `load_transcript`/`room_read`/`record_query` then fold rows,
edits, reactions, deletes and source events at it — see the `_read_window`
test below, which proves the bound reaches the store seam, not merely a
rewritten argument string). What the wrapper still owns is the honest
boundary the record cannot give: the `HISTORICAL_READ_UNAVAILABLE` table of
current-head folds, answered `unavailable` before the inner tool runs.

No model and no Strands: the wrapped tool is a recording double, and each
assertion reads either the answer the wrapper emitted or the `tool_use` the
inner tool was (or was not) called with.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from strands.types._events import ToolResultEvent

from nora.agent.comprehend_tools import (
    HISTORICAL_READ_UNAVAILABLE,
    HistoricalRead,
    as_of_reads,
)
from nora.agent.tools import NATIVE_TOOLS, _read_window


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
                "media_search", "file_fetch", "ask_memory", "search_memory",
                "search_memory_workspace")
))
async def test_a_current_head_read_answers_unavailable_and_the_inner_tool_never_runs(name):
    """Fail-closed, named, and nothing read: `unavailable` with the real
    limitation, never `nothing_found`, and the inner tool's store is never
    touched — a miss is not evidence the fold was empty. `file_fetch` is in
    the table because a shared file's CONTENT is what it says at fetch time:
    nothing as-of can read it back."""
    inner = _Inner(name)
    answer = await _answer(HistoricalRead(inner), {})
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
    answer = await _answer(HistoricalRead(inner), arguments)
    assert answer["status"] == "unavailable"
    assert inner.calls == []


async def test_record_query_message_reads_pass_through():
    """The messages branch stays: its interval is already bounded at the
    instant the session's clock is bound to, so the call runs untouched."""
    inner = _Inner("record_query")
    arguments = {"start": "2014-05-01", "end": "2014-05-31", "text": "dinner"}
    answer = await _answer(HistoricalRead(inner), arguments)
    assert answer["status"] == "ok"
    assert inner.calls[0]["input"] == arguments


# ---- pass-through reads ------------------------------------------------------

@pytest.mark.parametrize("name,arguments", [
    ("record_query", {"start": "2014-05-01"}),
    ("load_transcript", {"limit": 5}),
    ("load_transcript", {"before": "2013-01-01"}),
    ("load_transcript", {"before": "2030-01-01"}),
    ("load_transcript", {"before": "next tuesday-ish"}),
    ("room_read", {"room": "generated-room", "before": "2013-01-01"}),
    ("web_search", {"query": "dinner"}),
    ("web_fetch", {"url": "https://example.invalid/"}),
    ("media_fetch", {"code": "generated-code"}),
    ("place_info", {"name": "generated-place"}),
    ("instagram_read", {"handle": "generated-handle", "what": "profile"}),
    ("x_search", {"query": "dinner"}),
])
async def test_reads_with_honest_as_of_semantics_pass_through_verbatim(name, arguments):
    """Every read the record CAN answer as-of passes through with its call
    untouched — the bound lives in the built tool's own clock, so a `before`
    (absent, earlier, later or malformed) reaches the native parser verbatim,
    where it is honored, bounded at the instant, or refused as ever."""
    inner = _Inner(name)
    answer = await _answer(HistoricalRead(inner), arguments)
    assert answer["status"] == "ok"
    assert inner.calls[0]["input"] == arguments


async def test_as_of_reads_wraps_tool_objects_and_leaves_functions_alone():
    """Only Strands-shaped tools are wrapped: a caller's plain-function
    stand-in keeps its own behavior (and its own `__name__` dispatch)."""
    async def fn(**kw: Any) -> dict[str, Any]:
        return {"status": "ok"}
    fn.__name__ = "record_query"  # type: ignore[attr-defined]
    wrapped = as_of_reads([_Inner("note_read"), fn])
    assert isinstance(wrapped[0], HistoricalRead)
    assert wrapped[1] is fn


# ---- the bound itself: the read's own instant reaches the store --------------

class _RecordingStore:
    """The `RecordStore` seam `_read_window` reads: records `recent`/
    `recent_before` calls verbatim so the bound the tool computed can be
    asserted on what the store actually saw, not on a rewritten argument."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def recent(self, **kwargs: Any) -> list[Any]:
        self.calls.append(("recent", kwargs))
        return []

    async def recent_before(self, **kwargs: Any) -> list[Any]:
        self.calls.append(("recent_before", kwargs))
        return []


async def test_the_read_window_bounds_the_store_at_the_callers_instant():
    """THE LEAK, AT THE SEAM: a session bound to T calling «the newest» used
    to reach `recent()` — the live newest, future speech in a replay. With
    `as_of_ms` the window is `recent_before(T+1)` and the projection bound
    rides with it, so the store folds edits/reactions/deletes as-of too."""
    store = _RecordingStore()
    t = 1_401_624_000_000  # 2014-06-01 20:00:00 HKT, epoch ms
    await _read_window(store, conversation_id="c", asked=5, before_ts=None,
                       as_of_ms=t)
    kind, call = store.calls[-1]
    assert kind == "recent_before"
    assert call["before_ts"] == t + 1 and call["as_of_ms"] == t


async def test_an_earlier_before_stays_the_windows_own_end():
    """A model's own `before` strictly earlier than the instant is honored —
    the as-of ceiling only ever narrows a bound, never widens one."""
    store = _RecordingStore()
    t = 1_401_624_000_000
    await _read_window(store, conversation_id="c", asked=5,
                       before_ts=t - 3_600_000, as_of_ms=t)
    kind, call = store.calls[-1]
    assert kind == "recent_before"
    assert call["before_ts"] == t - 3_600_000 and call["as_of_ms"] == t


async def test_a_live_read_uses_the_same_path_with_its_own_clock():
    """Nothing special about history: the live read's `as_of_ms` is the wall
    clock's instant — one bound, one path, and `recent()` is only ever the
    no-bound-at-all call."""
    store = _RecordingStore()
    await _read_window(store, conversation_id="c", asked=5, before_ts=None)
    assert store.calls[-1][0] == "recent"
    await _read_window(store, conversation_id="c", asked=5,
                       before_ts=None, as_of_ms=99)
    kind, call = store.calls[-1]
    assert kind == "recent_before" and call["before_ts"] == 100
