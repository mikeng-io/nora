"""In-memory `SqlTagTraceStore` mirror: the same `save`/`recent`/`get`/
`turn_facts` contract, in-process and `await`-free like the other fakes."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from nora.domain.tag_trace import (
    FactItem,
    TagTrace,
    TurnFacts,
    reduce_turn_facts,
)
from nora.domain.turn_record import turn_items


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

    async def turn_facts(
        self, *, unit: str, tool_names: Sequence[str],
        open_cap: int = 200,
    ) -> TurnFacts:
        """The fake mirrors the structural projection: `turn_items` pairs
        use/result by `toolUseId` in message order — the same semantics the
        SQL's lateral expansion + `DISTINCT ON` computes."""
        if not tool_names:
            return TurnFacts()
        names = set(tool_names)
        items: list[FactItem] = []
        for t in self._rows:
            if t.unit != unit:
                continue
            for idx, item in enumerate(turn_items(t.messages, t.requirements)):
                if item.name not in names:
                    continue
                items.append(FactItem(
                    name=item.name, call_id=item.call_id,
                    arguments=item.arguments, result=item.result,
                    requirements=item.requirements,
                    turn_id=t.turn_id, handle=t.handle,
                    tag_id=t.tag_id, occasion=t.occasion,
                    created_at=t.created_at,
                    ordinal=(t.created_at, t.turn_id or 0, 0, idx),
                ))
        return reduce_turn_facts(items, open_cap=open_cap)

    async def get(self, *, turn_id: int, unit: str) -> TagTrace | None:
        row = self._turn_ids.get(turn_id)
        return row if row is not None and row.unit == unit else None
