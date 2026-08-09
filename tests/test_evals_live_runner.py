"""D1: `evals.harness.run_case(live=True)` drives Nora's REAL model through the
real pipeline and returns a populated `Trajectory`.

Network-marked, so it is excluded by the default `addopts = "-m 'not network'"`
and CI stays offline. Run it deliberately, against a reachable gateway, with:

    OMNIROUTE_API_KEY=... uv run pytest -m network tests/test_evals_live_runner.py

The offline/scripted path (`live=False`) is exercised by the ordinary,
always-on suite (`tests/test_evals_gate.py`); this one proves the live branch
that path deliberately cannot reach.
"""

import os

import pytest

from evals.harness import Case, run_case

pytestmark = pytest.mark.network


async def test_live_run_produces_trajectory_with_real_tool_calls():
    assert os.environ.get("OMNIROUTE_API_KEY"), (
        "OMNIROUTE_API_KEY must be set to run the live eval runner"
    )
    # A plain triggered greeting: the one thing a well-behaved turn must do is
    # speak, so this is the minimal case that proves the real model ran, chose a
    # tool, and delivered through the real guard→adapter chain.
    case = Case(name="live_smoke_greeting", input="hi Nora, reply with one short line")

    traj = await run_case(case, live=True)

    # The model actually ran and reached for at least one tool — reconstructed
    # from `nora_tool_calls_total`, NOT from a script (there is none live).
    assert traj.tool_calls, f"expected real tool calls, got none: {traj}"
    assert "send_reply" in traj.tool_calls, (
        f"a well-behaved turn must call send_reply; got {traj.tool_calls}"
    )
    # And a real reply landed on the fake adapter through the real deliver chain.
    assert traj.sent, "expected at least one delivered message"
    assert traj.reply and traj.reply.strip(), "expected non-empty reply text"
