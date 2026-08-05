"""`ToolSpec` — the plain, framework-free description of a tool (spec §4.2).

Revision 1 put the equivalent type (`ToolProvider`) in `nora/domain/`, returning
a Strands `Tool` directly — that contradicts both §4.1's framework-free rule for
`domain/` and `tests/test_boundaries.py::test_strands_import_boundary` (only
`nora.agent.**` may import `strands`). `ToolSpec` lives here instead, and stays
plain; `adapt()` below is the one function in this module allowed to touch
Strands, and it is the ONLY place a `ToolSpec` becomes a Strands tool object.

Deliberately NOT here: `ToolProvider` and any MCP tool lifecycle (spec §7/§11
defer both to a later phase). This phase only needs a way to describe a native
tool and convert it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from strands import tool as _strands_tool


@dataclass(frozen=True)
class ToolSpec:
    """A model-callable tool, described independently of any agent framework.

    `budget` feeds `ReplyGuard` (DEC-10): `ToolRegistry.budgets()` reads it to
    build the per-tag call-count ceiling R-7 enforces. It defaults to 1, the
    conservative choice — a tool that legitimately needs more than one call
    per tag says so explicitly.

    `disclose_failure` exists because DEC-4 requires a per-tool opt-in for
    whether a `REQUESTED_TOOL` failure may be disclosed to the model instead
    of staying silent, and revision 1 declared that flag in config with
    nowhere on the tool's own port to carry it. **Nothing in this phase reads
    `disclose_failure`** — it is DEC-4's disclosure logic, deferred to its own
    phase, not dead code; it is stored here so that phase has somewhere to
    read it from without another port change.

    `description`/`schema` may be left empty (`""`/`{}`): for a tool whose
    `call` has a docstring and type hints Strands can introspect — every
    native tool in `nora.agent.tools` — that is what `adapt()` does, so the
    docstring stays the single source of the model-facing schema instead of
    being duplicated here where it could drift. A tool with no introspectable
    `call` (for example a future MCP-sourced tool, out of scope this phase —
    spec §7/§11) would set both explicitly.
    """

    name: str
    description: str
    schema: dict
    call: Callable[..., Awaitable[Any]]
    budget: int = 1
    disclose_failure: bool = False
    timeout_seconds: float | None = None


def adapt(spec: ToolSpec) -> Any:
    """Convert a `ToolSpec` into the Strands tool object `Agent(tools=...)` wants.

    The only call into Strands this module makes. `description`/`schema` are
    passed through only when non-empty, so an empty `ToolSpec.description`/
    `.schema` (every native tool today) leaves Strands to derive both from
    `spec.call`'s docstring and type hints — see the class docstring above.
    `spec.schema` is a plain JSON schema (`{"type": "object", ...}`); Strands'
    own `inputSchema` wraps that under a `"json"` key, so an explicit override
    is wrapped here to match what auto-derivation itself produces.
    """
    return _strands_tool(  # type: ignore[call-overload]
        # Strands' own stubs only declare `tool(func)` and `tool(**kwargs) ->
        # decorator` as overloads; calling it with BOTH a positional func and
        # keyword overrides is valid at runtime (`strands.tools.decorator.tool`
        # branches on `func is None`, not on whether kwargs were also given —
        # see its source) but matches neither declared overload.
        spec.call,
        name=spec.name,
        description=spec.description or None,
        inputSchema={"json": spec.schema} if spec.schema else None,
    )
