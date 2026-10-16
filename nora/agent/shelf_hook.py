"""`ShelfLoader` — a shelved tool called BY NAME is loaded on the spot.

With the catalogue in front of her, she often calls a shelved tool directly
from its catalogue line instead of calling `load_tools` first. Strands has no
such tool registered and answers «Unknown tool»; she then loads it and calls
again — one wasted model call per cold tool, and a prefix-cache miss with it.

The remedy is on the framework's own seam, not in her instructions: Strands
fires
`BeforeToolCallEvent` with `selected_tool=None` for a name it does not hold,
and `selected_tool` is one of the three fields a hook may write
(`BeforeToolCallEvent._can_write`). So when the name is on THIS turn's shelf,
this hook registers the tool on the running agent — exactly what `load_tools`
would have done — and hands it to the executor. The call she made goes through;
the next model call carries the schema natively.

Two properties it keeps:

- **The guard decides first.** Registered AFTER `ReplyGuard`, and it does
  nothing to a call the guard cancelled — a vetoed name is never registered.
- **Only the shelf.** A name that is not on this turn's shelf is left exactly
  as Strands found it («Unknown tool»), so R-7's default-deny and the scope's
  admission are untouched: this cannot reach a tool the turn was not given.

`load_tools` stays: it is still how she fetches several schemas in one call
before using them, and how she finds out what arguments a tool takes when the
catalogue line does not say.
"""

from __future__ import annotations

import logging
from typing import Any

from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

from .surface import ToolShelf

log = logging.getLogger("nora.agent.shelf_hook")


class ShelfLoader(HookProvider):
    """Loads a shelved tool at the moment it is called by name."""

    def __init__(self, shelf: ToolShelf) -> None:
        self._shelf = shelf

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self._before_tool)

    def _before_tool(self, event: Any) -> None:
        try:
            if getattr(event, "cancel_tool", None):
                return
            if getattr(event, "selected_tool", None) is not None:
                return
            name = (getattr(event, "tool_use", None) or {}).get("name", "")
            tool = self._shelf.get(name)
            if tool is None:
                return
            registry = event.agent.tool_registry
            if name not in registry.dynamic_tools and name not in registry.registry:
                registry.register_dynamic_tool(tool)
            event.selected_tool = tool
        except Exception:
            # Never raises into the loop: the worst case is the pre-hook
            # behaviour, an «Unknown tool» she recovers from with `load_tools`.
            log.exception("ShelfLoader could not load a shelved tool; leaving the call as it was")
