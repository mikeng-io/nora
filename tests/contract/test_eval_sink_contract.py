"""One suite, both implementations of `EvalSink` — mirrors
`tests/contract/test_audit_store_contract.py` (the project's rule since Phase 2:
two implementations of one port need a shared contract suite both pass).

`EvalSink`'s one load-bearing behavioural invariant, shared by `NullEvalSink`
AND `LangfuseEvalSink` alike: calling any of its four methods must never raise,
no matter what — that's what makes "the eval gate's verdict must never depend
on Langfuse being reachable" (the sharp edge in the Phase 7 plan) true
structurally, not just true of one implementation by convention.
`LangfuseEvalSink` is parametrized against an unreachable host (`127.0.0.1:1`,
same pattern `tests/test_honcho.py` uses for `HonchoClient`) so this suite runs
fully offline — no live Langfuse required, none contacted.
"""

from __future__ import annotations

import pytest

from evals.sink import EvalSink, LangfuseEvalSink, NullEvalSink


@pytest.fixture(params=["null", "langfuse_unreachable"])
def sink(request) -> EvalSink:
    if request.param == "null":
        return NullEvalSink()
    return LangfuseEvalSink(host="http://127.0.0.1:1", headers={}, timeout=0.3)


async def test_upsert_dataset_never_raises(sink: EvalSink):
    await sink.upsert_dataset(name="nora-trigger")


async def test_upsert_item_never_raises(sink: EvalSink):
    await sink.upsert_item(
        dataset_name="nora-trigger", item_id="trigger/x", input="hi", expected_output=None
    )


async def test_start_run_never_raises_and_returns_a_string(sink: EvalSink):
    run_id = await sink.start_run(dataset_name="nora-trigger", run_name="run-1")
    assert isinstance(run_id, str)


async def test_record_score_never_raises_for_a_boolean_score(sink: EvalSink):
    run_id = await sink.start_run(dataset_name="nora-trigger", run_name="run-1")
    await sink.record_score(
        run_id=run_id, item_id="trigger/x", name="exactly_once_send_reply",
        value=1.0, data_type="BOOLEAN",
    )


async def test_record_score_never_raises_for_a_categorical_score(sink: EvalSink):
    run_id = await sink.start_run(dataset_name="nora-adversarial", run_name="run-1")
    await sink.record_score(
        run_id=run_id, item_id="injections/injection_dan", name="gate_verdict",
        value="PASS", data_type="CATEGORICAL",
    )


def test_both_implementations_satisfy_the_port():
    """`EvalSink` is `@runtime_checkable`, so this only checks method NAMES —
    not signatures, not keyword-only-ness. Static conformance is out of scope:
    `evals/` is not inside mypy's strict override (`nora.domain`/`config.py`/
    `app`/`infra` only), so this `isinstance` check is the only mechanical
    conformance check either implementation gets."""
    assert isinstance(NullEvalSink(), EvalSink)
    assert isinstance(LangfuseEvalSink(host="http://x", headers={}), EvalSink)
