"""`design/evaluation/01-boundaries.md` §7, driven rather than argued.

Two entry points chosen for being maximally unalike — `RelevanceGate.decide`
drives a component and no turn; `FirePath.speak` drives a whole turn and no
gate — go through ONE runner, with one `Case` shape and one `Outcome` shape. If
they could not, §2's four things would really be five or six.

**No model is reachable here**, and that is the point of the second half: both
entry points must come back as ERROR, never as a verdict. A suite that maps an
unreachable provider onto a verdict reports behaviour that never happened, and
for `expect: silence` cases it reports it as a PASS.
"""
from __future__ import annotations

import pytest

from evals.rebuilt.domain import Case
from evals.rebuilt.runner import run_once

#: Point the subject at a port nothing answers on, so the model call fails
#: rather than reaching a real provider from a test run. Configuration, which
#: is the legal seam — there is no way here to pass a collaborator.
_NO_PROVIDER = {"llm.base_url": "http://127.0.0.1:9/v1"}

GATE_CASE = Case(
    name="probe/gate",
    entry="gate",
    expect="no_go",
    knows={"transcript": ["今日好似好凍", "深水埗而家幾多度？"]},
    category="world_fact",
)

PARTICIPATION_CASE = Case(
    name="probe/participation",
    entry="participation",
    expect="silence",
    input="when is the next Raft update?",
    knows={
        "transcript": ["when is the next Raft update?", "anyway 聽日 lunch 食咩"],
        "standing": "The room asked about the Raft update and nobody answered.",
    },
)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [GATE_CASE, PARTICIPATION_CASE], ids=lambda c: c.entry)
async def test_one_runner_drives_both(case, pg_url, monkeypatch) -> None:
    """§7's reading. One call, two maximally unalike entry points."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    outcome = await run_once(case, database_url=pg_url, overrides=_NO_PROVIDER)
    # It got far enough to TRY. A composition failure is a different error and
    # would mean the lifecycle does not in fact hold for both.
    assert "could not obtain the subject" not in (outcome.error or ""), outcome.error
    # AND the entry point was actually THERE. This pin exists because the first
    # version of this file passed while both were `None`: the committed template
    # ships `[participation] enabled = false` and `[relevance_gate] enabled =
    # false`, so nothing ran and the errors that proved "it fails closed" were
    # "there is no gate" and "there is no fire path". A test that cannot tell an
    # absent subsystem from a refusing one is the defect it was written against.
    assert "on the graph" not in (outcome.error or ""), (
        f"{case.entry}: the entry point does not exist on the composed graph "
        f"({outcome.error}). The suite would then be measuring its own "
        "configuration, not Nora."
    )
    assert outcome.source == "fail_closed", (
        f"{case.entry}: expected the model call to be REACHED and to fail "
        f"closed; got source={outcome.source!r}, error={outcome.error!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [GATE_CASE, PARTICIPATION_CASE], ids=lambda c: c.entry)
async def test_an_unreachable_model_is_an_error_and_never_a_verdict(
    case, pg_url, monkeypatch,
) -> None:
    """ERROR is not FAIL and is never PASS — on BOTH entry points.

    The gate fails closed to `no_go`; the turn fails to `failed_*` and writes no
    row, which reads as silence. Both tokens are indistinguishable from a
    correct refusal, and both cases above expect exactly the token their own
    failure produces — so a suite without this rule would score two PASSes
    having reached no model at all.
    """
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    outcome = await run_once(case, database_url=pg_url, overrides=_NO_PROVIDER)
    assert outcome.error is not None, (
        f"{case.entry}: no model was reachable and the outcome still carries a "
        f"verdict: {outcome}. That is ERROR scored as a reading."
    )
    assert outcome.meets(case.expect) is None, (
        f"{case.entry}: an un-run case graded as {outcome.meets(case.expect)} "
        f"against expect={case.expect!r} — the third answer exists so this "
        "cannot happen."
    )
