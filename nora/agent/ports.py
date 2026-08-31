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

    `terminal` says whether calling this tool ENDS THE TURN, and it lives here
    for exactly the reason `budget` does (DEC-32). `ReplyGuard`'s
    `BeforeModelCallEvent` hook is the only thing that stops the agent loop —
    under `tool_choice="required"` the model is forced to call something on
    every pass, so it cannot stop by choosing not to (DEC-21). That hook used
    to decide "she has replied" by asking the DELIVERER whether it had
    delivered, which is terminality expressed as one specific tool's side
    effect: a second terminal verb (`design/proposed/15-gif-replies.md` §1's
    `send_gif`, or a distinct feed step on the tag path) could not be added
    without editing the guard. It is now declared here, read through
    `ToolRegistry.terminal_tools()`, so the next terminal verb is a table edit.
    It defaults to False — the conservative choice, since a tool wrongly marked
    terminal truncates the turn.

    `time_shiftable` says whether this tool can be asked about a moment other
    than now, and it is here for the reason `budget` and `terminal` are
    (design/14 §5.1). It is a THREE-valued field on purpose:

      * `None` — the tool has no time dimension at all. `1485/7` is the same
        number whenever it is asked, and `lookup_person` is not about a moment.
        This is the default because it is the answer for almost every tool, and
        because a tool wrongly marked live-only would have Nora refusing to use
        it for a perfectly ordinary question.
      * `False` — LIVE ONLY. The answer describes this instant and decays. A
        live ETA is worthless for a bus three hours out, and quoting «112
        seconds» for a 19:30 departure is a confident wrong answer of exactly
        the shape R-5 is about.
      * `True` — the tool accepts a target time. Nothing sets this yet;
        `route(origin, destination, mode, arrive_by)` (design/14 §3) is the
        first, and `arrive_by` is the point of it.

    The `False` set is the per-tool declaration that its answer describes this
    instant and decays. Its one renderer — `ToolRegistry.live_only_tools()`,
    which fed the generated capability block — was deleted with that block
    (DEC-150), but the declaration stays: it is the tool's own statement, read
    straight off the table, and
    `tests/unit/test_tool_registry.py::test_every_tool_declares_a_time_axis`
    pins the values.

    `side_effect_state`, `idempotency` and `retry` are the contract triple
    (ADR 0008, DEC-152), carried here for the reason `budget` and `terminal` are:
    they are declared once on `NativeTool` and copied by `_spec()`, so anything
    holding a spec can answer "what may an interrupted call of this have done"
    without reaching back into the registry table. They describe a FAILURE, not
    a success — `side_effect_state="unknown"` means a timeout leaves the write
    undecidable and the harness must assume it may have applied; `retry="never"`
    means it may not be replayed to find out. The full argument and the closed
    vocabularies (`SIDE_EFFECT_STATES` / `IDEMPOTENCY_VALUES` / `RETRY_POLICIES`)
    live on `nora.agent.tools`, which is where the declarations are; they are not
    re-stated here because two copies of a vocabulary is how one of them drifts.

    Their defaults are the same fail-closed ones the table takes —
    `none`/`non_idempotent`/`never` — so a spec built by hand rather than by
    `_spec()` is treated as un-replayable rather than as safe. **Nothing in this
    phase READS them off a `ToolSpec`**: the reader that ships with them is
    `tests/test_tool_contract_metadata.py`, which reads the declarations, and
    the output-contract validation (ADR 0008 §3) is a separate later pass. They
    are stated here so that pass has somewhere to read them from without another
    port change — the same posture `disclose_failure` takes above, and the ADR's
    own "each field ships with its reader" finding is what the enforcement test
    answers.
    """

    name: str
    description: str
    schema: dict
    call: Callable[..., Awaitable[Any]]
    budget: int = 1
    terminal: bool = False
    time_shiftable: bool | None = None
    disclose_failure: bool = False
    timeout_seconds: float | None = None
    side_effect_state: str = "none"
    idempotency: str = "non_idempotent"
    retry: str = "never"


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
