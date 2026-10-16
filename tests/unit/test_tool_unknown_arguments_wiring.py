"""R-99's PRODUCTION WIRING,
pinned end to end rather than only at `ReplyGuard.evaluate` itself.

WHY THIS FILE EXISTS, BESIDE `test_tool_unknown_arguments.py`
---------------------------------------------------------------
That file's guards are all built BY HAND, `param_names=ToolRegistry().
tool_param_names()` passed directly to `ReplyGuard(...)` — which proves the
RULE, but proves nothing about the four production call sites that have to
thread that table there (`nora.engagement.pipeline.EngagementPipeline`'s tag
path, and the three in `nora.app.fire.FirePath`). Deleting all four
`param_names=` lines is invisible to that file, because every guard in it is
built by hand.

This file drives the REAL `EngagementPipeline` (tag path) and the REAL
`FirePath` (participation path) through `tests.offline_eval_harness.run_case`
— the same real pipeline graph, real guards and real `ToolRegistry` the
`test_participation_false_speak.py` suite already uses, with only the model
scripted. `media_fetch(file=...)` names a key `media_fetch` has never declared.

WHAT "ASSERTS THE BAD_ARGUMENTS RESULT" MEANS HERE. `Trajectory` carries no
raw tool-result text (by design — see its own docstring), so the observable
this file reads is `nora_guard_vetoes_total{rule="R-99"}`: that counter is
incremented from EXACTLY ONE call site (`ReplyGuard._veto("R-99", ...)`,
`nora/agent/guards.py`), and that call site ALWAYS constructs its message
through `_unknown_argument_result`, which ALWAYS sets `"status":
"bad_arguments"` — there is no other way this counter increments. So `rule=
"R-99"` firing once IS the bad_arguments result firing once; the literal JSON
shape is `test_tool_unknown_arguments.py`'s own job, proven there against the
guard directly.

THE OTHER HALF — the tool body never ran — is `nora_tool_calls_total{tool=
"media_fetch"}` staying at zero: that counter increments from exactly one
place too (`nora.agent.tools._record`), called only from inside the tool's own
body, never from the guard.
"""

from __future__ import annotations

import pytest

from nora.testing import ScriptedTurn
from tests.offline_eval_harness import Case, run_case


@pytest.fixture(autouse=True)
def _omniroute_key(monkeypatch):
    """`run_case` loads the real config, which is fail-closed on the LLM key.
    Nothing here reaches a provider — the model is scripted."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")


_UNKNOWN_ARGUMENT_SCRIPT = [ScriptedTurn(
    tool_calls=[
        ("media_fetch", {"file": "https://example.com/photo.jpg"}),
        ("send_reply", {"text": "ok"}),
    ],
    final_text="ok",
)]


async def test_the_tag_path_refuses_media_fetch_called_with_file_and_never_runs_it():
    """THE TAG PATH, through the real `EngagementPipeline`
    (`nora.engagement.pipeline`). Guarded by
    `scripts/mutations/tool-unknown-arguments-wiring.json`: deleting the ONE
    `param_names=` line in that module's `deliver.build(...)` call turns this
    test red.
    """
    case = Case.from_dict({
        "name": "tag-unknown-argument",
        "input": "nora look at this",
        "metadata": {"trigger": True},
        "seed": {"transcript": ["look at this"]},
    })
    trajectory = await run_case(case, script=_UNKNOWN_ARGUMENT_SCRIPT)

    assert trajectory.metrics is not None
    assert trajectory.metrics.get("nora_guard_vetoes_total", rule="R-99") == 1
    assert trajectory.metrics.get("nora_tool_calls_total", tool="media_fetch") == 0
    assert trajectory.sent == ["ok"]


async def test_the_participation_path_refuses_media_fetch_called_with_file_and_never_runs_it():
    """THE PARTICIPATION PATH, through the real `FirePath.speak`
    (`nora.app.fire`). Falsified by deleting the `param_names=` line in the
    FIRST `deliver.build(...)` call in that module — the one the ordinary
    occasion turn (participation included) runs through, as opposed to the
    two shadow-mirror announce paths further down the same file.

    `media_fetch` is a lookup tool (`ToolRegistry.lookup_tools()`), so R-97
    (a participation turn that went looking and came back empty stays silent)
    is the one other rule that could plausibly interfere here — it does not,
    because a call R-99 vetoes never reaches `_after_tool`'s `record()`/lookup
    counters at all (DEC-33's "a cancelled call did not run" branch, the same
    one this whole change relies on for the budget claim), so `_lookups_made`
    stays at 0 and R-97's condition (`_lookups_made > 0`) is false. The reply
    goes through.
    """
    case = Case.from_dict({
        "name": "participation-unknown-argument",
        "input": "look at this",
        "expect": "speak",
        "seed": {"transcript": ["look at this", "check the photo"]},
    })
    trajectory = await run_case(case, script=_UNKNOWN_ARGUMENT_SCRIPT)

    assert trajectory.metrics is not None
    assert trajectory.metrics.get("nora_guard_vetoes_total", rule="R-99") == 1
    assert trajectory.metrics.get("nora_tool_calls_total", tool="media_fetch") == 0
    assert trajectory.sent == ["ok"]
