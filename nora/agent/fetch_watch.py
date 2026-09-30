"""What a session FETCHED through its read tools, so a write may cite it
(`design/cognition-loop/30-cognition.md` §3 check 1: "Citations resolve to ids
this session was shown or fetched"; review of 2026-09-30, m4).

A note's citations were held to the ids the agenda's stimuli named, so a session
that read an older message with `record_query`, or the ids an existing note
already cites with `note_read`, could not write a line resting on them: the
validator refused `unknown_id` for a row the session had in front of it. This
module watches the three reads that hand out citable rows and keeps what they
returned:

- `note_read`: the ids the note's body cites (`cited_ids_in`), which a revision
  carries forward (`Shown.carried`: accepted without a re-read, because a row
  the audit pruner has since deleted must not turn into a refusal);
- `record_query`: each message row it shows with its number `n`, WITH its
  sender, so a line naming who said it is checked against the author exactly as
  it is for a message the agenda named (`ShownMessage`);
- `rollup_read`: each Rollup row's `(kind, subject, from, to)`, the key a note
  cites it by (`nora.domain.note_body.rollup_key`).

**Only what the model was handed is kept.** The watcher reads the tool's own
result, after the registry's lineage and the room's disclosure have shaped it,
so a row that was withheld was never fetched and cannot be cited. A refused,
unavailable or unparseable result keeps nothing.

**`record_query` does not yet print `n`.** Its rows carry `room`, `sender`,
`body` and `when` only (`nora.agent.tools._record_messages` does not ask
`_transcript_messages` for `with_ids`), so on today's tool this records nothing
for it; the reader is written to the shape `load_transcript` and `room_read`
already print on a Dreamer pass (`n`), and starts to count the day the row
carries it. `note_read` and `rollup_read` feed it now.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from strands.types.tools import AgentTool, ToolSpec, ToolUse

from nora.domain.note_body import cited_ids_in, rollup_key

#: The read tools whose results hand out citable rows.
WATCHED_TOOLS: frozenset[str] = frozenset({"note_read", "record_query", "rollup_read"})


@dataclass
class Fetched:
    """One session's fetched rows, filled as its read tools answer."""

    #: Ids a note head already cites (`note_read`): carried, not re-checked.
    carried_ids: set[int] = field(default_factory=set)
    #: Message number -> the sender as the result showed it (`record_query`).
    messages: dict[int, str] = field(default_factory=dict)
    #: `rollup_key(...)` of every Rollup row `rollup_read` returned.
    rollup_keys: set[str] = field(default_factory=set)

    def take(self, tool: str, result: Any) -> None:
        """Keep what `result`, the answer of read tool `tool`, handed out."""
        if not isinstance(result, dict) or result.get("status") != "ok":
            return
        if tool == "note_read":
            body = result.get("body")
            if isinstance(body, str):
                self.carried_ids |= cited_ids_in(body)
        elif tool == "record_query":
            for row in result.get("messages") or ():
                n = row.get("n") if isinstance(row, dict) else None
                sender = row.get("sender") if isinstance(row, dict) else None
                if isinstance(n, int) and not isinstance(n, bool) and isinstance(sender, str):
                    self.messages[n] = sender
        elif tool == "rollup_read":
            for row in result.get("rows") or ():
                if not isinstance(row, dict):
                    continue
                parts = [str(row[k]) for k in ("kind", "subject", "from", "to")
                         if isinstance(row.get(k), str) and row[k]]
                if len(parts) == 4:
                    self.rollup_keys.add(rollup_key(parts[0], parts[1], parts[2], parts[3]))


def _name_of(tool: Any) -> str | None:
    name = getattr(tool, "tool_name", None) or getattr(tool, "__name__", None)
    return name if isinstance(name, str) else None


def _result_of(event: Any) -> Any:
    """The JSON a Strands tool result carries, or None."""
    result = getattr(event, "tool_result", None)
    if not isinstance(result, dict):
        return None
    for block in result.get("content") or ():
        if not isinstance(block, dict):
            continue
        if isinstance(block.get("json"), dict):
            return block["json"]
        text = block.get("text")
        if isinstance(text, str):
            try:
                return json.loads(text)
            except ValueError:
                return None
    return None


class WatchedTool(AgentTool):
    """`inner`, unchanged for the model (same name, schema and answers), with
    what it answers kept in `fetched`."""

    def __init__(self, inner: Any, fetched: Fetched, name: str) -> None:
        super().__init__()
        self._inner = inner
        self._fetched = fetched
        self._name = name

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def tool_spec(self) -> ToolSpec:
        return self._inner.tool_spec

    @property
    def tool_type(self) -> str:
        return "python"

    async def stream(
        self, tool_use: ToolUse, invocation_state: dict[str, Any], **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        async for event in self._inner.stream(tool_use, invocation_state, **kwargs):
            answer = _result_of(event)
            if answer is not None:
                self._fetched.take(self._name, answer)
            yield event


def watch_reads(tools: list[Any], fetched: Fetched) -> list[Any]:
    """`tools` with each watched read wrapped; every other tool, and any tool
    that is not a Strands tool object, passes through as it is."""
    out: list[Any] = []
    for tool in tools:
        name = _name_of(tool)
        if name in WATCHED_TOOLS and hasattr(tool, "stream") and hasattr(tool, "tool_spec"):
            out.append(WatchedTool(tool, fetched, name))
        else:
            out.append(tool)
    return out


__all__ = ["WATCHED_TOOLS", "Fetched", "WatchedTool", "watch_reads"]
