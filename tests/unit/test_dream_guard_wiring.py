"""R-99's PRODUCTION WIRING on a Dreamer pass (design/social-per-network/
30-link-owner-registry.md) — the Dreamer-side analogue of `tests/unit/
test_tool_unknown_arguments_wiring.py`'s tag/participation proof, one level
down.

WHY THIS FILE EXISTS, BESIDE `test_dream_guard.py`
---------------------------------------------------
Every `DreamGuard` in that file is built BY HAND, `param_names=` passed
directly to the constructor — which proves the RULE (shared with `ReplyGuard`
through `nora.agent.guards.unknown_arguments_veto`), but proves nothing about
`DreamerRuntime._sample` (`nora.agent.dreamer`), the one production call site
that has to thread `self._tools.tool_param_names()` into the guard it builds
for each sample. Reviewed and found silently missing on 2026-09-25, the exact
shortcut every existing test of `ReplyGuard`'s own wiring took before
`test_tool_unknown_arguments_wiring.py` closed it.

This drives the REAL `DreamerRuntime.run()` through the real Strands loop
(`_ScriptedModel`, `_Tools`, `_Adaptor`, `_runtime` — all from `tests.unit.
test_dream_memory_budget`, which already builds this exact rig for the
memory-ration property) with a scripted model that calls `place_info` naming
`bogus=`, a key `place_info` has never declared. `place_info` rather than a
memory verb (`about`/`ask_memory`/`search_memory_workspace`) on purpose: the
per-pass memory ceiling is a SEPARATE mechanism, and mixing it in here would
leave it unclear which one refused the call.
"""

from __future__ import annotations

from nora.agent import factory
from nora.agent.dreamer import AdaptorGrant, DreamerRuntime
from nora.obs.metrics import Metrics
from tests.unit.test_dream_memory_budget import (
    BUDGET,
    _Adaptor,
    _Instructions,
    _Models,
    _ScriptedModel,
    _Tools,
)


def _REMIND_GRANT() -> AdaptorGrant:
    """An adaptor that may hold `remind` past the read-only ceiling, in a real room."""
    return AdaptorGrant(home_room=True)


def _runtime(model, tools) -> tuple[DreamerRuntime, Metrics]:
    metrics = Metrics()
    return DreamerRuntime(
        models=_Models(model),
        instructions=_Instructions(),
        tools=tools,
        budgets={"proactive": BUDGET},
        default_budget=BUDGET,
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
        metrics=metrics,
        model_name="scripted",
        grants={"proactive": _REMIND_GRANT()},
        store_timeout_seconds=10.0,
    ), metrics


async def test_a_dreamer_pass_refuses_an_unknown_argument_and_never_runs_the_tool():
    """Falsified by deleting the `param_names=self._tools.tool_param_names(),`
    line in `DreamerRuntime._sample` (`nora.agent.dreamer`) — see
    `scripts/mutations/tool-unknown-arguments.json` for the recorded mutant.
    """
    model = _ScriptedModel(["place_info", None], inputs=['{"bogus": "x"}'])
    tools = _Tools(["place_info"])
    runtime, metrics = _runtime(model, tools)
    adaptor = _Adaptor(frozenset(tools.read_only()))

    await runtime.run(adaptor=adaptor, key="friends", batch=())

    assert tools.ran == [], "the tool body must never run"
    assert metrics.get(
        "nora_dream_guard_vetoes_total", adaptor="proactive", rule="R-99",
    ) == 1
