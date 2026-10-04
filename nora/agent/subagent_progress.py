"""What a running sub-agent worker has done, counted as it happens (design/subagent §4b, §8).

One `HookProvider` per job, registered AFTER the guard so it sees the guard's verdicts:

- `rounds`: model calls so far, and `last_tool`: the last tool the model asked for. Held in
  memory and read by `subagent_read` while the job runs; reading Langfuse at runtime is not
  a dependency Nora takes.
- `document_ids`: the `document_id`s of the `document_publish` calls whose RESULT came back
  `status: ok`, in order, once each. This is the only source of a worker's document ids. The
  report is prose the model wrote and may name a document that was never stored; a result the
  tool returned is evidence. A vetoed or failed call has no `ok` result and adds nothing.

It registers no `BeforeModelCallEvent` cancel and never ends a turn: the bounds belong to
the loop's limits and the runner's wall clock.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from strands.hooks import (
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

log = logging.getLogger("nora.agent.subagent_progress")

PUBLISH_TOOL = "document_publish"


def _payloads(result: Any) -> list[dict[str, Any]]:
    """The dicts a tool result carries: Strands JSON-encodes a returned dict into one text
    block on a `success` result, and a `json` block holds it directly."""
    content = result.get("content") if isinstance(result, dict) else None
    out: list[dict[str, Any]] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        value = block.get("json")
        if value is None and isinstance(block.get("text"), str):
            try:
                value = json.loads(block["text"])
            except ValueError:
                continue
        if isinstance(value, dict):
            out.append(value)
    return out


class SubagentProgress(HookProvider):
    def __init__(self) -> None:
        self.rounds = 0
        self.last_tool: str | None = None
        #: The job's Langfuse trace id, set by the runner inside its span (no row column is
        #: written from it yet: `compute_jobs.trace_id` has no writer on the poller's path).
        self.trace_id: str | None = None
        self._document_ids: list[str] = []

    @property
    def document_ids(self) -> tuple[str, ...]:
        return tuple(self._document_ids)

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self._before_model)
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _before_model(self, event: Any) -> None:
        self.rounds += 1

    def _before_tool(self, event: Any) -> None:
        name = (getattr(event, "tool_use", None) or {}).get("name")
        if isinstance(name, str) and name:
            self.last_tool = name

    def _after_tool(self, event: Any) -> None:
        try:
            if (getattr(event, "tool_use", None) or {}).get("name") != PUBLISH_TOOL:
                return
            if getattr(event, "exception", None) is not None:
                return
            if getattr(event, "cancel_message", None) is not None:
                return
            for payload in _payloads(getattr(event, "result", None)):
                doc = payload.get("document_id")
                if (
                    payload.get("status") == "ok" and isinstance(doc, str) and doc
                    and doc not in self._document_ids
                ):
                    self._document_ids.append(doc)
        except Exception:
            log.exception("could not read a document_publish result")
