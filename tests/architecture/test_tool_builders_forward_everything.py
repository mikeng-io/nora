"""`build_tools` forwards every parameter it accepts.

`build_tools` declares the same keyword-only parameters as `build_tool_specs`
and hands them on by name. A parameter accepted here and dropped there is a
caller configuring something that silently keeps its default — and it is not
hypothetical: `describe_max_bytes` (a per-modality byte ceiling) had been
accepted and dropped since it was added, and issue #116 then added the video
chunk geometry and the media join beside it, all three silent.

Production reaches `build_tool_specs` through `ToolRegistry.build`, so the
deployed path was never affected; every `build_tools` caller in the tree is a
test, which is precisely why nothing went red. The class of defect is what this
closes, not one instance of it.

A STATIC comparison, deliberately: calling both and diffing the tools would
prove they produce the same NAMES, which they do either way — the difference
only shows in behaviour one parameter at a time, which is eight tests instead
of one and still would not catch the ninth.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from nora.agent import tools as tools_module

ROOT = Path(__file__).resolve().parents[2]

#: Accepted by `build_tools` and deliberately NOT forwarded, with the reason.
#: Empty today; an entry here is a decision somebody has to write down.
_NOT_FORWARDED: dict[str, str] = {}


def _kwonly(name: str) -> set[str]:
    tree = ast.parse(inspect.getsource(tools_module))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return {a.arg for a in node.args.kwonlyargs}
    raise AssertionError(f"{name} not found")


def _forwarded_from_build_tools() -> set[str]:
    """The keywords `build_tools` passes into its `build_tool_specs(` call."""
    tree = ast.parse(inspect.getsource(tools_module))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "build_tools"):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "build_tool_specs"):
                return {kw.arg for kw in call.keywords if kw.arg}
    raise AssertionError("build_tools does not call build_tool_specs")


def test_build_tools_forwards_every_parameter_it_accepts() -> None:
    accepted = _kwonly("build_tools")
    forwarded = _forwarded_from_build_tools()

    dropped = accepted - forwarded - set(_NOT_FORWARDED)
    assert not dropped, (
        f"`build_tools` accepts {sorted(dropped)} and never passes them on, "
        f"so a caller setting one gets the default and no error. Forward it, "
        f"or add it to `_NOT_FORWARDED` with the reason it is accepted and "
        f"ignored."
    )


#: On `build_tool_specs` and deliberately NOT on the wrapper. `build_tools` is
#: the narrower, test-facing shape — production reaches the specs builder
#: through `ToolRegistry.build`, which is where these are threaded from. Pinned
#: as a LITERAL so a twelfth divergence is a decision somebody makes rather
#: than one that accumulates.
_SPECS_ONLY = frozenset({
    "clock", "compute_backend", "compute_jobs", "events", "fire_turn",
    "music", "relay", "reminder_caps", "reminders", "tool_call_log_store",
    "versions",
    # The second per-turn PROVENANCE beside `fire_turn` (design/dreamer-agent-
    # loop/39 D3): only `ToolRegistry.specs` passes it, from the Dreamer's
    # `RegistryToolSource`; `build_tools` has no caller that is a Dreamer.
    "dream_adaptor",
})


def test_the_two_signatures_have_not_drifted_apart() -> None:
    """THE POSITIVE CONTROL. The test above compares `build_tools` against its
    own call, and both would stay green if `build_tools` lost half its
    parameters — a smaller `accepted` set trivially satisfies a subset check.
    This is what says the wrapper still offers what the thing it wraps does,
    minus a list that has to be written down."""
    accepted = _kwonly("build_tools")
    specs = _kwonly("build_tool_specs")

    assert (specs - accepted) == _SPECS_ONLY, (
        f"the wrapper's coverage moved: it now omits "
        f"{sorted(specs - accepted - _SPECS_ONLY)} and no longer omits "
        f"{sorted(_SPECS_ONLY - (specs - accepted))}. Either forward it or "
        f"record it in `_SPECS_ONLY` with the reason."
    )
    assert len(accepted) > 30, f"the walk went thin: {len(accepted)} params"
