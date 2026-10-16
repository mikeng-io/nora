"""ADR 0009 enforcement: the R-27 three-state result is a VALIDATED contract.

ADR 0008 declared the output contract and ADR 0009 amended its vocabulary: the three
states are the CORE, the extensions the live tool surface uses are DECLARED, and
`load_transcript` keeps its deliberately status-free shape. This file is that decision's
reader, in three layers:

1. **The vocabulary** — pinned by value, so an extension cannot be added silently and the
   core cannot quietly grow a fourth member. `OUTPUT_STATUSES` is derived from the two
   halves rather than restated; that derivation is what the runtime validator reads, so
   pinning the halves pins the gate.
2. **The declarations** — read off the SOURCE, not off the constructed dataclass.
   `output_contract` defaults to `None`, and `None` means NOT VALIDATED: a dict-returning
   tool that forgot to declare and a tool that deliberately declines are byte-identical
   once `NativeTool` is built, so the direction that costs a check is exactly the one a
   runtime read cannot see.
3. **The seam** — `adapt()` wraps `spec.call`, so a malformed result is replaced with an
   honest `unavailable` before Strands hands it to the model, a valid one passes through
   untouched, and an exception still propagates.

**What this file does NOT test, because the harness cannot check it.** Whether a `refused`
was really a disclosure veto or a lazy `unavailable` is a SEMANTIC judgment; it stays with
the tool author and with the existing R-27/R-31 tests. The validation here is mechanical —
vocabulary and shape — and saying so is the point of ADR 0009 §1's last bullet.
"""

import ast
import logging
from pathlib import Path

import pytest

from nora.agent.ports import (
    DECLARED_STATUS_EXTENSIONS,
    MALFORMED_RESULT,
    OUTPUT_CONTRACTS,
    OUTPUT_STATUSES,
    R27_CORE_STATES,
    ToolSpec,
    adapt,
    contract_violation,
)
from nora.agent.tools import NATIVE_TOOLS, _spec

AGENT_PACKAGE = Path(__file__).parent.parent / "nora" / "agent"
TOOLS_PATH = AGENT_PACKAGE / "tools.py"


# ---------------------------------------------------------------- vocabulary


def test_the_core_is_r27s_three_states() -> None:
    """Pinned by VALUE, because ADR 0009 names the three and calls them a spine.

    Everything else in the vocabulary is an extension AROUND this set. A fourth
    member appearing here would not be an extension — it would be a change to
    what «I couldn't check» / «there's nothing» / «here it is» means, and that
    is a decision, not an edit.
    """
    assert set(R27_CORE_STATES) == {"ok", "nothing_found", "unavailable"}


def test_every_declared_extension_is_named_and_documented() -> None:
    """ADR 0009 §1: each extension is a NAMED, documented value — not a hole.

    The ten are pinned by value so one cannot be added in passing, and each
    one's meaning is asserted non-empty because a mapping whose values drift to
    `""` is a `frozenset` wearing a costume: the documentation is the whole
    reason this is a mapping rather than a set.
    """
    assert set(DECLARED_STATUS_EXTENSIONS) == {
        "refused", "withheld", "ambiguous", "not_found",
        "nothing_known", "not_an_image", "already_present", "no_route",
        # A HOLD is not a refusal and not a fault: nothing vetoed the send and nothing was
        # broken. What separates it from both is that calling again cannot help, because
        # the hold is not about the text.
        "held_for_review",
        # R-99: a call naming an argument the tool does not take. Deliberately NOT
        # `unavailable` — that stays true of the whole turn afterwards (conduct.md) and
        # this veto text reaches the debrief/Standing fold unfiltered — and NOT `refused`,
        # since nothing vetoed disclosure. This one names a mistake in the CALL,
        # correctable on the very next turn.
        "bad_arguments",
        # DEC-283: a background job was launched and has no result yet. Not `ok` (that
        # reads as done) and not `unavailable` (it did start).
        "started",
        # The memory index answered while a rebuild is incomplete or its queue is not
        # empty; what it did not find may still exist, so never `nothing_found`.
        "partial",
    }
    undocumented = sorted(k for k, v in DECLARED_STATUS_EXTENSIONS.items() if not v.strip())
    assert undocumented == [], f"declared with no meaning: {undocumented}"


def test_ambiguous_declares_the_sense_it_is_actually_used_in() -> None:
    """A declared meaning that has fallen behind the use is worse than none.

    `ambiguous` means "several candidates, cannot pick one". The person index returns
    every igid on file, and NONE is `nothing_found` with a note saying no account is on
    file. So the declaration names the one sense in use and says, in so many words, that
    the other (no index entry AT ALL) is not this word, so a reviewer reading an old trace
    that used it can tell a retired sense from a drift.
    """
    meaning = DECLARED_STATUS_EXTENSIONS["ambiguous"]
    assert "several candidates" in meaning, "the sense in use is gone"
    assert "NOT «no entry at all»" in meaning, "the retired sense is not named as retired"
    assert "nothing_found" in meaning, "it must say which word that sense answers now"


def test_the_vocabulary_is_derived_from_the_core_and_the_extensions() -> None:
    """One definition, per CLAUDE.md's label rule — never a hand-copied third list.

    `OUTPUT_STATUSES` is what the validator actually checks against, so if it
    could drift from the two halves above then pinning the halves would prove
    nothing about the gate. The disjointness assertion is the other direction: a
    value in both would let an extension be quietly promoted into the core.
    """
    assert set(OUTPUT_STATUSES) == R27_CORE_STATES | set(DECLARED_STATUS_EXTENSIONS)
    assert not R27_CORE_STATES & set(DECLARED_STATUS_EXTENSIONS)


def test_the_contract_table_pins_what_each_shape_requires() -> None:
    """ADR 0009 §2's whole point: `messages` must NOT require a status.

    This is the assertion that would have caught the superseded §3 enum. Under a
    universal `required: ["status"]`, `load_transcript`'s every SUCCESSFUL call
    became `{"status": "unavailable", "reason": "malformed_result"}` — the recall
    ladder's middle rung dying silently rather than loudly.
    """
    assert set(OUTPUT_CONTRACTS) == {"three_state", "messages"}
    assert OUTPUT_CONTRACTS["three_state"] == {"status"}
    assert OUTPUT_CONTRACTS["messages"] == {"messages"}
    assert "status" not in OUTPUT_CONTRACTS["messages"]


def test_the_malformed_replacement_is_an_honest_unavailable() -> None:
    """Fail-closed, and `reason` is stable so an operator can grep for it.

    `unavailable` and not `nothing_found`: a result we could not read is «I
    couldn't check», never «there's nothing» — the same R-27 distinction the
    validator exists to defend, applied to the validator's own failure.
    """
    assert MALFORMED_RESULT["status"] == "unavailable"
    assert MALFORMED_RESULT["reason"] == "malformed_result"
    assert MALFORMED_RESULT["status"] in R27_CORE_STATES


# -------------------------------------------------------------- declarations


def _built_tools() -> dict[str, str | None]:
    """{tool name: its callable's return annotation} for every tool BUILT here.

    Read off the source rather than off `NATIVE_TOOLS`, because the question
    this answers — "does this tool's result have a shape to check" — is a
    property of the callable, and the table entry cannot see it. `skills` is
    absent by construction: it is `plugin=True`, so `build_tool_specs` never
    names it in a `_spec(...)` call and there is no body of ours to classify.
    """
    tree = ast.parse(TOOLS_PATH.read_text())
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            funcs[node.name] = node

    out: dict[str, str | None] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", "") != "_spec" or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue  # the `def _spec(name, call)` definition itself has no literal
        fn = funcs[first.value]
        out[first.value] = ast.unparse(fn.returns) if fn.returns else None
    return out


BUILT = _built_tools()


def test_the_structural_reader_sees_the_whole_built_table() -> None:
    """The positive control for every source-read assertion below.

    An AST reader that silently stops matching — `_spec` renamed, the calls
    moved behind a loop — would turn the declaration tests into green no-ops
    that never look at a tool. This pins the reader against the live table: it
    must find every entry except the one plugin, and every one it finds must
    carry a return annotation it could actually classify.
    """
    plugins = {n for n, s in NATIVE_TOOLS.items() if s.plugin}
    assert plugins, "no plugin tools found — the flag moved"
    assert set(BUILT) == set(NATIVE_TOOLS) - plugins
    assert len(BUILT) > 40
    assert all(BUILT.values()), f"unannotated: {sorted(n for n, r in BUILT.items() if not r)}"


@pytest.mark.parametrize("tool", sorted(n for n, r in BUILT.items() if r == "dict"))
def test_every_dict_returning_tool_declares_an_output_contract(tool: str) -> None:
    """The rule, applied: a result with a shape is a result the harness checks.

    ADR 0008 as amended: every tool whose result carries the status axis declares one,
    DATA tools and action verbs alike. The set is derived from the RETURN TYPE, not from
    `source_tier`, which misses the action verbs (`send_to`, the events family) where the
    honesty axis matters most: a tool handing the model a dict is handing it a shape,
    whatever kind of verb it is.
    """
    assert NATIVE_TOOLS[tool].output_contract is not None, (
        f"{tool} returns a dict to the model and declares no output contract"
    )


@pytest.mark.parametrize("tool", sorted(NATIVE_TOOLS))
def test_a_declared_contract_names_one_this_module_can_validate(tool: str) -> None:
    """The vocabulary is READ from `ports`, never restated here.

    A declaration naming a contract the table does not define is worse than no
    declaration: `contract_violation` answers "undeclared contract", so EVERY
    call of that tool would fail closed into `unavailable` — a tool silently
    switched off by a typo.
    """
    declared = NATIVE_TOOLS[tool].output_contract
    assert declared is None or declared in OUTPUT_CONTRACTS, declared


def test_only_a_shaped_result_declares_a_contract() -> None:
    """The other direction, derived rather than name-listed.

    `now` returns a formatted string and `send_reply` returns a bool; neither
    has an object to validate, and declaring `three_state` on one would make
    every successful call fail closed. `skills` is `plugin=True` — this registry
    does not build it, so there is no result of ours to check at all.
    """
    unshaped = {n for n, ret in BUILT.items() if ret != "dict"}
    unshaped |= {n for n, s in NATIVE_TOOLS.items() if s.plugin}
    assert unshaped, "no unshaped tools found — the reader stopped classifying"
    offenders = {n for n in unshaped if NATIVE_TOOLS[n].output_contract is not None}
    assert offenders == set(), f"declares a contract with no object to check: {sorted(offenders)}"


def test_load_transcript_declares_the_status_free_contract() -> None:
    """ADR 0009 §2, pinned by value because the ADR decides it by name.

    Both its returns are `_memory_result({"messages": ...})` and neither carries
    a `status` key — the empty list IS the answer to a failed read. It is the
    one tool on the table whose contract is not `three_state`, and the reason it
    is written down here is that "everything is three_state" is the wrong
    generalisation to reach for the next time a tool is added.
    """
    assert NATIVE_TOOLS["load_transcript"].output_contract == "messages"
    assert "status" not in OUTPUT_CONTRACTS[NATIVE_TOOLS["load_transcript"].output_contract]


@pytest.mark.parametrize("tool", sorted(NATIVE_TOOLS))
def test_spec_carries_the_contract_rather_than_the_default(tool: str) -> None:
    """`_spec()` is the plumbing, and this field has the worst version of its bug.

    A dropped copy of `side_effect_state` produces a wrong DECLARATION that some
    later test can still catch. A dropped copy here silently switches the
    runtime validation OFF for that tool — `adapt()` reads `spec.output_contract`
    and `None` means "do not check" — and nothing downstream looks any
    different.
    """
    async def _call() -> dict:
        return {}

    assert _spec(tool, _call).output_contract == NATIVE_TOOLS[tool].output_contract


def test_no_agent_module_returns_a_status_outside_the_vocabulary() -> None:
    """ADR 0009 §5: the constant is the single source, scanned not hand-listed.

    Every `{"status": "<literal>"}` in `nora.agent.**` — the tools, the relay,
    the whereis ladder, the arithmetic refusals — is a status the model can
    actually be handed, so each one has to be a value the validator will accept.
    A literal outside the vocabulary is a tool that fails closed on its own
    honest answer, and it would reach production as a silent `unavailable`
    rather than as a red test.

    Scanned across the package rather than pinned to `tools.py` because the
    statuses are NOT all written there; a hand-copied list of the modules that
    happen to contain one is the second definition this test exists to avoid.

    `no_route` is the one vocabulary member with no literal in this package: it
    is minted in `nora.infra.routes` and reaches the model verbatim through
    `route`'s pass-through of the provider's own dict. It is subtracted by NAME
    below rather than by loosening the assertion to an inequality, so the day it
    grows a call site here — or the day a second value goes the same way — this
    test says which one and why.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(AGENT_PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Dict):
                continue
            # A Strands `ToolResult` (`status` beside a `content` LIST) speaks
            # Strands' own two words, `success` and `error`, to a sub-agent's
            # model, not R-27 to Nora's: the browse sub-agent returns one to
            # hand its model a screenshot (nora/agent/browse.py `page_look`).
            if any(isinstance(k, ast.Constant) and k.value == "content"
                   and isinstance(v, ast.List)
                   for k, v in zip(node.keys, node.values, strict=True)):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if not (isinstance(key, ast.Constant) and key.value == "status"):
                    continue
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    found.setdefault(value.value, []).append(f"{path.name}:{value.lineno}")

    # Positive control FIRST: an empty `outside` below is also true of a scan
    # that parsed nothing. Asserted as an EQUALITY so it works both ways — no
    # literal here is outside the vocabulary, and no vocabulary member is dead.
    assert set(found) | {"no_route"} == set(OUTPUT_STATUSES), sorted(found)
    assert sum(len(v) for v in found.values()) > 100

    outside = {s: sites for s, sites in found.items() if s not in OUTPUT_STATUSES}
    assert outside == {}, f"status literals outside the vocabulary: {outside}"


# --------------------------------------------------------------- the seam


def _adapted(contract: str | None, result: object) -> ToolSpec:
    """A one-argument tool returning `result`, declaring `contract`."""
    async def probe(thing: str) -> dict:
        """Probe a thing.

        Args:
            thing: What to probe.
        """
        return result  # type: ignore[return-value]

    return ToolSpec(
        name="probe", description="", schema={}, call=probe, output_contract=contract,
    )


async def test_a_valid_result_passes_through_untouched() -> None:
    """The wrapper is a gate, not a transform — identity, not equality.

    A validator that rebuilt the dict would be free to drop a key nobody
    happened to assert on, so this pins the OBJECT rather than its contents.
    """
    payload = {"status": "ok", "answer": 42}
    tool = adapt(_adapted("three_state", payload))
    assert await tool._tool_func(thing="x") is payload


async def test_a_status_free_result_passes_its_own_contract() -> None:
    """ADR 0009 §2 at runtime — the assertion the superseded enum would fail."""
    payload = {"messages": []}
    tool = adapt(_adapted("messages", payload))
    assert await tool._tool_func(thing="x") is payload


@pytest.mark.parametrize(
    ("contract", "result"),
    [
        ("three_state", {"status": "sort_of"}),      # outside the vocabulary
        ("three_state", {"status": None}),           # not even a string
        ("three_state", {"answer": 42}),             # no status at all
        ("three_state", {"messages": []}),           # the OTHER contract's shape
        ("messages", {"status": "ok"}),              # ditto, reversed
        ("three_state", ["ok"]),                     # not an object
        ("three_state", None),                       # nothing at all
        ("three_state", "ok"),                       # a bare string
    ],
)
async def test_a_result_that_fails_its_contract_is_replaced(
    contract: str, result: object,
) -> None:
    """Fail-closed: never passed through, never a PASS (ADR 0008 §3).

    The model sees an honest «I couldn't check» instead of a payload it would
    have to guess at. Both directions of the two-contract mix-up are in here on
    purpose — a tool declaring `three_state` and answering `{"messages": ...}`
    is exactly as wrong as the reverse, and a validator that only checked for
    the presence of SOME known key would pass both.
    """
    tool = adapt(_adapted(contract, result))
    assert await tool._tool_func(thing="x") == dict(MALFORMED_RESULT)


async def test_a_tool_declaring_no_contract_is_not_validated() -> None:
    """`None` means NOT VALIDATED, and it has to mean it at runtime too.

    `send_reply` returns a bool and `now` returns a string; a wrapper that
    validated them anyway would replace every successful call with
    `unavailable`. Asserted with a value that WOULD fail `three_state`, so a
    wrapper quietly validating everything cannot pass this by luck.
    """
    tool = adapt(_adapted(None, True))
    assert await tool._tool_func(thing="x") is True


async def test_an_exception_still_propagates() -> None:
    """The wrapper validates the RETURNED VALUE and nothing else (decision 6).

    A raise is the invoke stage's business — its timeout and `llm_error`
    handling — and swallowing one here would turn a crash into a quiet
    `unavailable` with nothing left to diagnose it from.
    """
    async def boom(thing: str) -> dict:
        """Boom.

        Args:
            thing: Anything.
        """
        raise RuntimeError("kaboom")

    spec = ToolSpec(
        name="boom", description="", schema={}, call=boom, output_contract="three_state",
    )
    with pytest.raises(RuntimeError, match="kaboom"):
        await adapt(spec)._tool_func(thing="x")


async def test_the_log_names_the_keys_and_never_the_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The operator gets the SHAPE; the payload stays out of the log.

    A malformed result is the one case where the payload is not known to be this
    codebase's own prose — it is whatever the tool actually built — so quoting
    it is how a phone number or a room's contents reach a terminal by accident
    (R-18's concern, arriving through the diagnostic rather than the reply). The
    keys are enough to find the tool that did it.
    """
    result = {"status": "invented_state", "note": "0912345678 lives at Flat 4B"}
    tool = adapt(_adapted("three_state", result))
    with caplog.at_level(logging.ERROR, logger="nora.agent.ports"):
        assert await tool._tool_func(thing="x") == dict(MALFORMED_RESULT)

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "probe" in message and "three_state" in message
    assert "'note'" in message and "'status'" in message
    assert "invented_state" not in message
    assert "0912345678" not in message and "Flat 4B" not in message


async def test_the_wrapper_keeps_the_schema_strands_derives_from_the_callable() -> None:
    """`functools.wraps` is load-bearing here, not cosmetic.

    Strands builds the model-facing description and input schema by introspecting
    the callable's own docstring, signature and annotations (`adapt`'s whole
    reason for passing `description=None`). A plain `async def (*args, **kwargs)`
    wrapper would hand every validated tool an empty schema and a missing
    description — a silent, total regression of the ADR 0007 surface that no
    result-shape assertion in this file would notice. Reverting the `@wraps`
    turns this red.
    """
    tool = adapt(_adapted("three_state", {"status": "ok"}))
    assert "Probe a thing" in tool.tool_spec["description"]
    assert "thing" in tool.tool_spec["inputSchema"]["json"]["properties"]


def test_contract_violation_reports_the_axis_and_never_the_value() -> None:
    """The reason strings are a closed, content-free set — that is why they are short.

    They are the greppable half of the log line; the keys are the other half.
    Asserted directly rather than only through the log so that a future caller
    cannot start interpolating the offending value into one.
    """
    assert contract_violation("three_state", {"status": "ok"}) is None
    assert contract_violation("three_state", {"status": "nope"}) == "undeclared status"
    assert contract_violation("three_state", {}) == "missing required key"
    assert contract_violation("three_state", 7) == "not an object"
    assert contract_violation("no_such_contract", {"status": "ok"}) == "undeclared contract"
