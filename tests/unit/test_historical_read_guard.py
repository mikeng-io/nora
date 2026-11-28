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

from strands.types._events import ToolResultEvent

from nora.agent.toolkit.transcript import _read_window


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




# ---- the current-head table ------------------------------------------------





# ---- record_query's branches ------------------------------------------------





# ---- pass-through reads ------------------------------------------------------





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
    t = 1_401_624_000_000  # evidence instant, epoch ms
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
