"""In-memory `SqlTagTraceStore` mirror: the same `save`/`recent`/`get`/
`recent_unit` contract, in-process and `await`-free like the other fakes."""

from __future__ import annotations

from nora.domain.tag_trace import TagTrace


class FakeTagTraceStore:
    def __init__(self) -> None:
        self._rows: list[TagTrace] = []
        self._turn_ids: dict[int, TagTrace] = {}

    async def save(self, trace: TagTrace) -> None:
        self._rows.append(trace)
        if trace.turn_id is not None:
            self._turn_ids[trace.turn_id] = trace

    async def recent(
        self, *, conversation_id: str, limit: int,
    ) -> tuple[TagTrace, ...]:
        if limit <= 0:
            return ()
        rows = sorted(
            (t for t in self._rows if t.conversation_id == conversation_id),
            key=lambda t: t.created_at, reverse=True,
        )
        return tuple(rows[:limit])

    async def recent_unit(
        self, *, unit: str, limit: int,
    ) -> tuple[TagTrace, ...]:
        if limit <= 0:
            return ()
        rows = sorted(
            (t for t in self._rows if t.unit == unit),
            key=lambda t: t.created_at, reverse=True,
        )
        return tuple(rows[:limit])

    async def get(self, *, turn_id: int, unit: str) -> TagTrace | None:
        row = self._turn_ids.get(turn_id)
        return row if row is not None and row.unit == unit else None
