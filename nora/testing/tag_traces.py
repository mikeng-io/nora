"""In-memory `SqlTagTraceStore` mirror: the same `save`/`recent`/`get`/
`recent_unit` contract, in-process and `await`-free like the other fakes."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence

from nora.domain.tag_trace import TagTrace


class FakeTagTraceStore:
    def __init__(self) -> None:
        self._rows: list[TagTrace] = []
        self._turn_ids: dict[int, TagTrace] = {}
        self._next_turn_id = 1

    async def save(self, trace: TagTrace) -> None:
        if trace.turn_id is None:
            # The SQL column is `Identity()` — ids are assigned on save, so
            # the fake mirrors that or `get` can never find its own row.
            trace = dataclasses.replace(
                trace, turn_id=self._next_turn_id)
            self._next_turn_id += 1
        self._rows.append(trace)
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

    async def recent_unit_turns(
        self, *, unit: str, tool_names: Sequence[str], limit: int,
        offset: int = 0,
    ) -> tuple[TagTrace, ...]:
        if limit <= 0 or not tool_names:
            return ()
        names = tuple(tool_names)
        rows = sorted(
            (t for t in self._rows
             if t.unit == unit
             and any(f'"{name}"' in json.dumps(t.messages)
                     for name in names)),
            key=lambda t: (t.created_at, t.tag_id), reverse=True,
        )
        return tuple(rows[offset:offset + limit])

    async def get(self, *, turn_id: int, unit: str) -> TagTrace | None:
        row = self._turn_ids.get(turn_id)
        return row if row is not None and row.unit == unit else None
