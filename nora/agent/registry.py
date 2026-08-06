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
from .tools import NATIVE_TOOL_BUDGETS, NATIVE_TOOLS, build_tool_specs


class ToolRegistry:
    """Owns which native tools exist and what each costs (DEC-10)."""

    def budgets(self) -> Mapping[str, int]:
        """The per-tool call-count ceiling `ReplyGuard` enforces under R-7."""
        return dict(NATIVE_TOOL_BUDGETS)

    def terminal_tools(self) -> frozenset[str]:
        """The verbs that END A TURN, for `ReplyGuard`'s model-call hook (DEC-32).

        Exposed exactly the way `budgets()` is, and for the same reason. Under
        `tool_choice="required"` the model must call something on every pass, so
        the guard's `BeforeModelCallEvent` cancellation is the ONLY thing that
        ends a turn (DEC-21). It used to end one by asking the `Deliverer`
        whether it had delivered — terminality expressed as a single tool's side
        effect, which is the pre-DEC-10 hardcoded `BUDGETS` mistake wearing a
        different hat. `design/proposed/15-gif-replies.md` §1 proposed fixing
        that by defining the verb class *in the guard*; this defines it on the
        tool instead, so `send_gif` — or any other verb that ends a turn —
        becomes one `terminal=True` in `NATIVE_TOOLS`.

        A `frozenset` rather than a mapping: membership is the whole question,
        and there is no per-tool value to carry.
        """
        return frozenset(name for name, spec in NATIVE_TOOLS.items() if spec.terminal)

    def tool_names(self) -> tuple[str, ...]:
        """Every registered tool's name, for the doctrine loader's catalogue gate.

        `nora.doctrine` sits BELOW `nora.agent` in the layer contract and cannot
        import the tool table, so the composition root hands it these instead
        (`nora.doctrine.loader.check_kind_boundaries`)."""
        return tuple(NATIVE_TOOLS)

    def capability(self) -> str:
        """The capability section, GENERATED (design/17 §5).

        The `kind: capability` body handed to `DoctrineStore`. There is no file
        and there must never be one: capability's source of truth is the
        registry, so any authored copy is a cache, and a cache with no
        invalidation is how `soul.md` spent eight weeks telling Nora she knew
        two things when she knew five.

        **Not a schema dump, and the distinction is the whole design.** The
        schemas are already in the request — measured at 1,222 tokens, 25% of
        the call — and they state, verbatim, what each tool does and what it
        takes. Repeating any of that here would be paid twice for nothing. This
        block adds exactly the one thing a schema has no slot for: when to reach
        for a tool *in this situation*. That is `NativeTool.when`, one clause
        each, and nothing else.

        Rendered from the same table `build_tool_specs` populates every
        `ToolSpec.when` from, so the block and the tools cannot describe
        different sets. `tests/unit/test_tool_registry.py` pins that in both
        directions against real specs."""
        lines = [f"- {name} — {spec.when}" for name, spec in NATIVE_TOOLS.items()]
        return (
            "These are the tools you were given. Their schemas say what each one "
            "takes; this says when to reach for it.\n" + "\n".join(lines) + "\n"
            "Nothing here reaches outside this conversation. When you cannot find "
            "something out, say so in one clause and move on."
        )

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
