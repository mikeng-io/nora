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

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import wraps
from typing import Any

from strands import tool as _strands_tool

log = logging.getLogger(__name__)

#: R-27's three states — the CORE of the output vocabulary (ADR 0009 §1), valid
#: on every `three_state` result and never interchangeable with each other: a
#: wiring gap is `unavailable` («I couldn't check»), never `nothing_found`
#: («there's nothing»).
R27_CORE_STATES: frozenset[str] = frozenset({"ok", "nothing_found", "unavailable"})

#: The DECLARED EXTENSIONS (ADR 0009 §1). The core is a core, not a ceiling: the
#: live tool surface carries these ten beside it, each one a distinction the
#: guards and the recall ladder already depend on, and each named here with its
#: meaning so an extension cannot be added silently. Collapsing `refused` into
#: `unavailable` would turn «I may not say» into «I couldn't check», which is
#: the exact conflation R-27 exists to prevent.
DECLARED_STATUS_EXTENSIONS: Mapping[str, str] = {
    "unresolved": "a proposed conclusion or action lacks a resolved evidence identity; "
                  "retained as a candidate, never accepted or scheduled",
    "refused": "disclosure veto (R-31) — may not say, not couldn't check",
    "withheld": "disclosure veto on the memory axis (R-31)",
    "ambiguous": "the subject did not resolve to ONE thing because there "
                 "are several candidates and no way to pick one, so nothing "
                 "was looked at — two people answering to a name, or a person "
                 "with several accounts on file. NOT «no entry at all»: that "
                 "sense rode this word on the Instagram rows until "
                 "2026-09-24 and now answers `nothing_found` with a note "
                 "saying no account is on file (design/instagram-adoption/18).",
    "not_found": "the thing does not exist (R-27b) — a place a route does not "
                 "serve is not «no such place», so this is not `nothing_found`",
    "nothing_known": "no durable knowledge yet (the recall ladder)",
    "not_an_image": "media modality refusal",
    "already_present": "the idempotent write had nothing left to do",
    "no_route": "the routing provider found no way between origin and destination",
    "held_for_review": "the send was complete and something OUTSIDE the turn "
                       "kept it — a review mode, or the room's send cooldown. "
                       "Not `refused` (nothing vetoed it) and not "
                       "`unavailable` (nothing was broken): the difference is "
                       "whether calling again could help, and here it cannot, "
                       "because the hold is not about the text. Added "
                       "2026-09-09 after a live participation turn read a hold "
                       "as a failure and rewrote the same reply four times.",
    "started": "the call launched work that is still running: a background job has "
               "an id and no result yet. Not `ok` (that reads as «done») and not "
               "`unavailable` (it did start). The result arrives later, by message "
               "(design/subagent §3).",
    "partial": "a search answered from an index still being rebuilt, or with "
               "sources waiting on its projection queue: what it found is real, "
               "and what it did not find may still exist. Not `nothing_found` "
               "(an unfinished index is never «you never said that») and not "
               "`ok` (25-memory-graph.md §4, design/unified-memory/60 S5).",
    "bad_arguments": "the call named an argument the tool does not take; "
                     "nothing ran; calling again with the listed arguments "
                     "will work. Not `unavailable` (R-99, design/"
                     "social-per-network/30-link-owner-registry.md, "
                     "2026-09-25): `unavailable` means «I could not check» "
                     "and stays true of the whole turn (conduct.md — «what "
                     "you could not check stays unchecked afterwards too»), "
                     "and `ReplyGuard`'s veto text reaches the debrief/"
                     "Standing fold unfiltered (`nora.engagement."
                     "debrief_render._block`'s `toolResult` branch) — a "
                     "malformed CALL read as a broken TOOL would fold a "
                     "false «I can't» into her self-knowledge. `refused` is "
                     "also wrong: nothing vetoed disclosure. This one names "
                     "a mistake in the call itself, correctable on the very "
                     "next turn.",
}

#: The whole vocabulary, DERIVED from the two above rather than hand-listed —
#: CLAUDE.md's label rule, the same shape `SIDE_EFFECT_STATES` takes in
#: `nora.agent.tools`. This is the single source: the validator reads it, the
#: structural test reads it, and nothing restates it.
OUTPUT_STATUSES: frozenset[str] = R27_CORE_STATES | frozenset(DECLARED_STATUS_EXTENSIONS)

#: contract name -> the keys a result declaring it MUST carry.
#:
#: `three_state` is the DEFAULT for a tool whose result carries the status axis.
#: `messages` is `load_transcript`'s deliberately status-free shape (ADR 0009)
#: — the recall ladder's middle rung answers `{"messages": [...]}` on both
#: its paths, and validating it against `required: ["status"]` would turn every
#: successful call into `unavailable`. A tool whose contract does not require
#: `status` is validated against its own shape; the three-state axis is the
#: default for data tools, not a universal requirement.
OUTPUT_CONTRACTS: Mapping[str, frozenset[str]] = {
    "three_state": frozenset({"status"}),
    "messages": frozenset({"messages"}),
}

#: What the model sees instead of a result that fails its contract (ADR 0008 §3,
#: ADR 0009 §3). Fail-closed and honest: an `unavailable` says «I couldn't
#: check», which is true of a tool whose answer we could not read. `reason` is
#: stable and greppable on purpose — it is the string an operator searches the
#: log for.
MALFORMED_RESULT: Mapping[str, str] = {
    "status": "unavailable",
    "reason": "malformed_result",
}


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
    `tests/test_tool_contract_metadata.py`, which reads the declarations. They
    are stated here so a later phase has somewhere to read them from without
    another port change — the same posture `disclose_failure` takes above, and
    the ADR's own "each field ships with its reader" finding is what the
    enforcement test answers.

    `output_contract` is the fourth (ADR 0008 §3 as amended by ADR 0009), and
    it is the one contract field with a RUNTIME reader: `adapt()` below wraps
    `call` so every result is checked against it before Strands hands the result
    to the model. It names a key of `OUTPUT_CONTRACTS` above — `three_state` for
    a tool whose result carries the R-27 status axis, `messages` for
    `load_transcript`'s deliberately status-free shape.

    `None` means NOT VALIDATED, and it is the default because it is the honest
    answer for a tool whose result is not an object with a declared shape at
    all: `now` returns a formatted string, `send_reply` returns a bool. It is
    also what a `plugin=True` entry takes — this registry does not build that
    callable, so there is no result of ours to check. The direction is the
    opposite of `side_effect_state`'s: a missing declaration here loses a check,
    it does not grant a permission, and `tests/test_output_contracts.py` is what
    stops a dict-returning tool inheriting it by accident.
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
    output_contract: str | None = None


def contract_violation(contract: str, result: Any) -> str | None:
    """`None` when `result` satisfies `contract`; else a short, CONTENT-FREE reason.

    **Mechanical only, and the limit is the point.** This checks the vocabulary
    and the shape: is the result an object, does it carry the keys its contract
    requires, is its `status` a value `OUTPUT_STATUSES` declares. It cannot and
    must not judge whether a `refused` was really a disclosure veto or a lazy
    `unavailable` — that judgment stays with the tool author and with the
    existing R-27/R-31 tests (ADR 0009 §1). Nobody should later read this
    function as enforcing semantics it has no way to see.

    The reason string names the axis and never the value. A malformed result is
    the one case where the payload is not known to be this module's own prose,
    so quoting it into a log line is how a phone number or a room's contents
    reach the operator's terminal by accident; the caller logs the KEYS beside
    this reason and that is enough to find the tool that did it.
    """
    required = OUTPUT_CONTRACTS.get(contract)
    if required is None:
        return "undeclared contract"
    if not isinstance(result, dict):
        return "not an object"
    if not required <= set(result):
        return "missing required key"
    if "status" in required and (
        not isinstance(result["status"], str)
        or result["status"] not in OUTPUT_STATUSES
    ):
        return "undeclared status"
    return None


def _validated(spec: ToolSpec) -> Callable[..., Awaitable[Any]]:
    """`spec.call`, with its RETURNED VALUE checked against its declared contract.

    Wrapped here, inside `adapt()`, because this is the single choke point every
    native tool result passes through on its way to the model — one seam, not a
    second one in `ToolRegistry.specs()`/`build()` that could disagree with it.
    MCP tool groups are appended by the registry AFTER `adapt()` runs and are
    deliberately NOT validated in this pass (ADR 0008 §5 defers structured error
    bodies to that boundary).

    **Fail-closed**: a result that fails validation is REPLACED with
    `MALFORMED_RESULT`, never passed through. R-27's "an ERROR is never a PASS"
    becomes mechanical — the model sees an honest «I couldn't check» instead of
    a payload it would have to guess at.

    **Exceptions propagate.** This validates the returned value and nothing
    else; a tool that raises still raises, because a raise is the invoke stage's
    business (its timeout/`llm_error` handling), and swallowing one here would
    turn a crash into a quiet `unavailable` with nothing left to diagnose it
    from. `functools.wraps` is load-bearing rather than cosmetic: Strands
    derives the model-facing schema from the callable's own signature,
    annotations and docstring, so an unwrapped closure would silently ship every
    tool with an empty schema.
    """
    contract = spec.output_contract
    if contract is None:
        return spec.call
    call = spec.call

    @wraps(call)
    async def validated(*args: Any, **kwargs: Any) -> Any:
        result = await call(*args, **kwargs)
        violation = contract_violation(contract, result)
        if violation is None:
            return result
        log.error(
            "%s returned a result its %r contract rejects (%s); keys=%s",
            spec.name, contract, violation,
            sorted(result, key=str) if isinstance(result, dict) else type(result).__name__,
        )
        return dict(MALFORMED_RESULT)

    return validated


def adapt(spec: ToolSpec) -> Any:
    """Convert a `ToolSpec` into the Strands tool object `Agent(tools=...)` wants.

    The only call into Strands this module makes. `description`/`schema` are
    passed through only when non-empty, so an empty `ToolSpec.description`/
    `.schema` (every native tool today) leaves Strands to derive both from
    `spec.call`'s docstring and type hints — see the class docstring above.
    `spec.schema` is a plain JSON schema (`{"type": "object", ...}`); Strands'
    own `inputSchema` wraps that under a `"json"` key, so an explicit override
    is wrapped here to match what auto-derivation itself produces.

    The callable handed over is `_validated(spec)` rather than `spec.call`
    itself — the output-contract seam (ADR 0008 §3 / ADR 0009 §3). For a spec
    declaring no contract the two are the same object, so nothing is wrapped
    that has nothing to check.
    """
    # A tool that must act on its OWN agent — `load_tools`, which registers
    # shelf tools on the running agent's registry — declares a `tool_context`
    # parameter, and Strands injects the context there (and leaves it out of
    # the model-facing schema) only when told to. Read off the signature
    # rather than declared on `ToolSpec` because the parameter IS the
    # declaration: a closure without it could not use the context anyway.
    wants_context = "tool_context" in inspect.signature(spec.call).parameters
    return _strands_tool(  # type: ignore[call-overload]
        # Strands' own stubs only declare `tool(func)` and `tool(**kwargs) ->
        # decorator` as overloads; calling it with BOTH a positional func and
        # keyword overrides is valid at runtime (`strands.tools.decorator.tool`
        # branches on `func is None`, not on whether kwargs were also given —
        # see its source) but matches neither declared overload.
        _validated(spec),
        name=spec.name,
        description=spec.description or None,
        inputSchema={"json": spec.schema} if spec.schema else None,
        context=wants_context,
    )
