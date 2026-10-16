"""Two unalike entry points go through ONE runner, driven rather than argued.

`RelevanceGate.decide` drives a component and no turn; `FirePath.speak` drives
a whole turn and no gate. They are chosen for being maximally unalike, and both
go through `evals.backbone._drive`, with one `Case` shape and one outcome shape.

ONE PATH. Neither case states what she knows: each names a moment in a
SOURCE that a room's history was seeded into, and the runner reconstructs it
into a sandbox of its own — the same thing it does for the deployment's
clone. `tests/sandbox_fixtures.seed_source` is the history; the case is the
anchor.

**No model is reachable here**, and that is the point of the second half: both
entry points must come back as ERROR, never as a verdict. A suite that maps an
unreachable provider onto a verdict reports behaviour that never happened, and
for `expect: silence` cases it reports it as a PASS.
"""
from __future__ import annotations

import pytest
from strands_evals import Case
from strands_evals.types.evaluation import EvaluationData

from evals.backbone import ERROR, NORA_ERROR, MeetsGrader, Spec, _drive, state_of
from nora.subject import Knowledge
from tests.sandbox_fixtures import seed_source

#: Point the subject at a port nothing answers on, so the model call fails
#: rather than reaching a real provider from a test run. Configuration, which
#: is the legal seam — there is no way here to pass a collaborator.
#: Every draw creates a Honcho workspace of its own.
pytestmark = pytest.mark.usefixtures("draw_honcho")

_NO_PROVIDER = {"llm.base_url": "http://127.0.0.1:9/v1"}

_HISTORY = {
    "gate": Knowledge(transcript=("今日好似好凍", "深水埗而家幾多度？")),  # type: ignore[arg-type]
    "participation": Knowledge(
        transcript=("when is the next Raft update?", "anyway 聽日 lunch 食咩"),  # type: ignore[arg-type]
        standing="The room asked about the Raft update and nobody answered.",
    ),
}
_EXPECT = {"gate": "no_go", "participation": "silence"}
_INPUT = {"gate": "", "participation": "when is the next Raft update?"}


async def _driven(entry: str, pg_url: str, pg_template: str, tmp_path) -> dict:
    source = await seed_source(pg_url, tmp_path, _HISTORY[entry], template=pg_template)
    case = Case(
        name=f"probe/{entry}",
        input=_INPUT[entry],
        metadata={"expect": _EXPECT[entry], "snapshot": source.snapshot, "category": "world_fact"},
    )
    spec = Spec(
        source_url=source.url, entry=entry, overrides=_NO_PROVIDER,
        policy=source.policy, identity=source.identity, template=source.template,
    )
    return await _drive(case, spec)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["gate", "participation"])
async def test_one_runner_drives_both(entry, pg_url, pg_template, tmp_path, monkeypatch) -> None:
    """One call, two maximally unalike entry points."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    result = await _driven(entry, pg_url, pg_template, tmp_path)
    data = EvaluationData(input="", actual_environment_state=result["environment_state"])
    error = str(state_of(data, NORA_ERROR) or "")
    # It got far enough to TRY. A composition failure is a different error and
    # would mean the lifecycle does not in fact hold for both.
    assert "could not obtain the subject" not in error, error
    # AND the entry point was actually THERE: the committed template ships
    # `[participation] enabled = false` and `[relevance_gate] enabled = false`,
    # so without this pin nothing would run and the errors that appeared to prove
    # "it fails closed" would be "there is no gate" and "there is no fire path".
    assert "on the graph" not in error, (
        f"{entry}: the entry point does not exist on the composed graph "
        f"({error}). The suite would then be measuring its own configuration, not Nora."
    )
    assert error, f"{entry}: expected the model call to be REACHED and to fail closed"


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["gate", "participation"])
async def test_an_unreachable_model_is_an_error_and_never_a_verdict(
    entry, pg_url, pg_template, tmp_path, monkeypatch,
) -> None:
    """ERROR is not FAIL and is never PASS — on BOTH entry points.

    The gate fails closed to `no_go`; the turn fails to `failed_*` and writes no
    row, which reads as silence. Both tokens are indistinguishable from a
    correct refusal, and both cases above expect exactly the token their own
    failure produces — so a suite without this rule would score two PASSes
    having reached no model at all.
    """
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    result = await _driven(entry, pg_url, pg_template, tmp_path)
    data = EvaluationData(
        input="", actual_output=result["output"],
        actual_environment_state=result["environment_state"],
        metadata={"expect": _EXPECT[entry]},
    )
    [out] = MeetsGrader().evaluate(data)
    assert out.label == ERROR, (
        f"{entry}: no model was reachable and the case graded {out.label} "
        f"({out.reason}). That is ERROR scored as a reading."
    )
