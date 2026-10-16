"""What a running sub-agent worker has done, counted as it happens.

One `HookProvider` per job, registered AFTER the guard so it sees the guard's verdicts:

- `rounds`: model calls so far, and `last_tool`: the last tool the model asked for. Held in
  memory and read by `subagent_read` while the job runs; reading Langfuse at runtime is not
  a dependency Nora takes.
- `document_ids`: the `document_id`s of the `document_publish` calls whose RESULT came back
  `status: ok`, in order, once each. This is the only source of a worker's document ids. The
  report is prose the model wrote and may name a document that was never stored; a result the
  tool returned is evidence. A vetoed or failed call has no `ok` result and adds nothing.

- the CHECKPOINT: the worker's own `checkpoint_update` lists, held here
  and persisted through `persist` on every update; and `kept_files`, the paths a successful
  `computer_export` to `keep` stored. Like `document_ids` they come from tool results, never
  from the checkpoint's words.
- the ANCHOR: the worker's history is a sliding window (`factory.create_subagent`), so the
  brief and the early steps scroll away. Once the window has trimmed, a `BeforeModelCallEvent`
  callback writes the latest rendered checkpoint and code's outputs into the pinned first
  message, so the worker always sees where it stands. It registers AFTER the conversation
  manager's own callback (the agent registers the manager first), so it sees the trimmed list.

It registers no `BeforeModelCallEvent` cancel and never ends a turn: the bounds belong to
the loop's limits and the runner's wall clock.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from strands.hooks import (
    AfterToolCallEvent,
    BeforeModelCallEvent,
    BeforeToolCallEvent,
    HookProvider,
    HookRegistry,
)

from nora.domain.checkpoint import Checkpoint, Outputs, row_payload
from nora.language.checkpoint_render import render_checkpoint

log = logging.getLogger("nora.agent.subagent_progress")

PUBLISH_TOOL = "document_publish"
EXPORT_TOOL = "computer_export"

#: What the anchor says before the block, so a worker that finds it knows why it is there.
ANCHOR_LEAD = (
    "Your earlier steps were trimmed from your history to keep it short. This is where you "
    "stand, as you recorded it and as code recorded what you made:\n"
)
#: The anchor is one text block of the pinned first message, found again by this prefix.
ANCHOR_MARK = "Your earlier steps were trimmed"

#: `persist(payload, at)`: write the checkpoint to the job's row. The backend supplies it.
Persist = Callable[[Mapping[str, Any], datetime], Awaitable[None]]


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
    def __init__(self, *, persist: Persist | None = None) -> None:
        self.rounds = 0
        self.last_tool: str | None = None
        #: The worker's last `checkpoint_update`, and the paths it kept (code's, from results).
        self.checkpoint: Checkpoint | None = None
        self._kept_files: list[str] = []
        self._persist = persist
        #: Set by the runner before the loop: the job's title and the clock the anchor ages by.
        self.title = ""
        self.clock: Callable[[], datetime] = lambda: datetime.now(UTC)
        # What the pinned message's anchor was last written from; rewritten when either moves.
        self._anchored: tuple[int, int] | None = None
        self._version = 0
        #: The job's Langfuse trace id, set by the runner inside its span; the backend returns it
        #: and the compute poller writes it to the row when it marks the job.
        self.trace_id: str | None = None
        self._document_ids: list[str] = []

    @property
    def document_ids(self) -> tuple[str, ...]:
        return tuple(self._document_ids)

    @property
    def kept_files(self) -> tuple[str, ...]:
        return tuple(self._kept_files)

    def outputs(self, *, with_rounds: bool = False) -> Outputs:
        """What code recorded: document ids (no titles: a reader looks those up), kept files."""
        return Outputs(
            documents=tuple((d, "") for d in self._document_ids),
            kept_files=self.kept_files, rounds=self.rounds if with_rounds else None,
        )

    def checkpoint_payload(self) -> dict[str, Any]:
        """The row's `checkpoint` value: the worker's lists, and the kept files from code."""
        return row_payload(self.checkpoint, self.kept_files)

    def stamp(self) -> datetime:
        """When the row's checkpoint was last written: the worker's own, else now."""
        return (self.checkpoint.at if self.checkpoint is not None else None) or self.clock()

    async def update_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Replace the checkpoint (the whole of it) and write it to the row. A failed write
        is logged and left: the checkpoint is held here, and the next update writes again."""
        self.checkpoint = checkpoint
        self._version += 1
        await self._flush()

    async def _flush(self) -> None:
        if self._persist is None:
            return
        try:
            await self._persist(self.checkpoint_payload(), self.stamp())
        except Exception:
            log.exception("could not persist the sub-agent checkpoint")

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self._before_model)
        registry.add_callback(BeforeToolCallEvent, self._before_tool)
        registry.add_callback(AfterToolCallEvent, self._after_tool)

    def _before_model(self, event: Any) -> None:
        self.rounds += 1
        try:
            self._anchor(event.agent)
        except Exception:
            log.exception("could not write the checkpoint anchor")

    def _anchor(self, agent: Any) -> None:
        """After the window has trimmed anything, keep the latest checkpoint and outputs in
        the pinned first message (the brief), replacing the previous anchor in place. Nothing
        before the first trim: the whole history is still there to read."""
        removed = int(getattr(agent.conversation_manager, "removed_message_count", 0) or 0)
        if not removed or self._anchored == (removed, self._version):
            return
        text = render_checkpoint(
            self.checkpoint, self.outputs(), title=self.title, now=self.clock(), mode="anchor",
        )
        messages = agent.messages
        if not messages or messages[0].get("role") != "user":
            return
        content = messages[0].setdefault("content", [])
        content[:] = [
            b for b in content
            if not (isinstance(b, dict) and str(b.get("text", "")).startswith(ANCHOR_MARK))
        ]
        # the lead is written even with nothing recorded: the worker must know it was trimmed
        content.append({"text": ANCHOR_LEAD + text if text else ANCHOR_LEAD.rstrip("\n")})
        self._anchored = (removed, self._version)

    def _before_tool(self, event: Any) -> None:
        name = (getattr(event, "tool_use", None) or {}).get("name")
        if isinstance(name, str) and name:
            self.last_tool = name

    async def _after_tool(self, event: Any) -> None:
        try:
            use = getattr(event, "tool_use", None) or {}
            name = use.get("name")
            if name not in (PUBLISH_TOOL, EXPORT_TOOL):
                return
            if getattr(event, "exception", None) is not None:
                return
            if getattr(event, "cancel_message", None) is not None:
                return
            kept_new = False
            for payload in _payloads(getattr(event, "result", None)):
                if payload.get("status") != "ok":
                    continue
                if name == PUBLISH_TOOL:
                    doc = payload.get("document_id")
                    if isinstance(doc, str) and doc and doc not in self._document_ids:
                        self._document_ids.append(doc)
                elif payload.get("kept") is True:
                    # the path is the call's input: an `ok` result is what makes it true
                    path = (use.get("input") or {}).get("path")
                    if isinstance(path, str) and path and path not in self._kept_files:
                        self._kept_files.append(path)
                        kept_new = True
            if kept_new:
                await self._flush()
        except Exception:
            log.exception("could not read a tool result")
