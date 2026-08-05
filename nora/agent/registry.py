"""`ToolRegistry` — the tool-extension axis DEC-10 exists to unblock (spec §9).

Before this phase, `ReplyGuard.evaluate` (`nora/agent/guards.py`) read a
hardcoded three-entry `BUDGETS` class attribute and vetoed anything absent
from it under R-7 — silently rejecting every tool not in that literal table,
including a fourth native tool or `end_episode`. `ToolRegistry.budgets()` is
what a `ReplyGuard` is now constructed with instead (`nora.engagement.stages
.deliver.build`, threaded from `nora.app.composition`/`nora.app.offline`), so
a new tool's budget comes from wherever it is registered, not from a table a
future author has to remember to also edit in `guards.py`.

It registers whatever `nora.agent.tools` builds, at the budgets that module
declares — `NATIVE_TOOL_BUDGETS` is the single table both this class and
`build_tool_specs` read, so the number `ReplyGuard` enforces and the number a
tool is declared to cost can never drift apart. That is the property DEC-10
exists for, and adding the three room-scoped tools (`group_members`,
`lookup_person`, `recall`) exercised it: they became callable by appearing in
that one table, with no edit to `guards.py` at all.

Deliberately NOT here: a `ToolProvider` protocol, MCP tool discovery/lifecycle,
or anything dynamic — spec §7/§11 defer all of that. `ToolRegistry` is a plain,
constructed collaborator (no module-level singleton — CLAUDE.md's test-seam
policy) built once per process by the composition root.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .ports import ToolSpec, adapt
from .room import Room
from .tools import NATIVE_TOOL_BUDGETS, build_tool_specs


class ToolRegistry:
    """Owns which native tools exist and what each costs (DEC-10)."""

    def budgets(self) -> Mapping[str, int]:
        """The per-tool call-count ceiling `ReplyGuard` enforces under R-7."""
        return dict(NATIVE_TOOL_BUDGETS)

    def specs(
        self,
        *,
        store: Any,
        conversation_id: str,
        conversation_type: str,
        persons: Any,
        honcho: Any,
        deliverer: Any,
        metrics: Any,
        room: Room,
        question: str = "",
        context_window: int = 40,
    ) -> tuple[ToolSpec, ...]:
        """The registered tools as plain `ToolSpec`s, closed over this tag's
        dependencies — before any Strands adaptation."""
        return build_tool_specs(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
            room=room, question=question, context_window=context_window,
        )

    def build(
        self,
        *,
        store: Any,
        conversation_id: str,
        conversation_type: str,
        persons: Any,
        honcho: Any,
        deliverer: Any,
        metrics: Any,
        room: Room,
        question: str = "",
        context_window: int = 40,
    ) -> list[Any]:
        """The registered tools, adapted to what `Agent(tools=...)` wants —
        what `nora.engagement.pipeline.EngagementPipeline` calls in place of
        the old direct `nora.agent.tools.build_tools` import."""
        specs = self.specs(
            store=store, conversation_id=conversation_id, conversation_type=conversation_type,
            persons=persons, honcho=honcho, deliverer=deliverer, metrics=metrics,
            room=room, question=question, context_window=context_window,
        )
        return [adapt(spec) for spec in specs]
