"""The real composed writer and SQL store, with only the provider boundary scripted."""
import re
from html import escape
from types import SimpleNamespace

import pytest
from strands_evals import Case
from strands_evals.types.evaluation import EvaluationData

from evals.backbone import (
    ERROR,
    FAIL,
    NORA_ERROR,
    NORA_POSITION,
    NORA_SHOWN,
    PASS,
    MeetsGrader,
    Spec,
    _drive,
    state_of,
)
from evals.persona_cases import case_file
from nora.agent import factory
from nora.subject import Knowledge
from tests.sandbox_fixtures import seed_source

pytestmark = pytest.mark.usefixtures("draw_honcho")


@pytest.mark.parametrize("behavior,label", [
    ("no_change", PASS), ("wrong_residence", FAIL),
    ("provider_failure", ERROR), ("unsettled", ERROR), ("print", ERROR),
    ("birthday_no_change", PASS), ("birthday_corrected", PASS), ("missing_source", ERROR),
])
async def test_writer_runtime_receipt_and_fail_closed_paths(
    pg_url, pg_template, tmp_path, monkeypatch, behavior, label,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    policy = ('default = "deny"\n[[conversations]]\nplatform = "signal"\n'
              'id = "g1"\nlabel = "g1"\npersons = ["miles_novak"]\nunit = "nora"\n')
    source = await seed_source(
        pg_url, tmp_path,
        Knowledge(transcript=tuple(f"restored background {i}" for i in range(101))),
        template=pg_template, policy=policy,
    )
    vector = {"birthday_no_change": "birthday_retained",
              "birthday_corrected": "birthday_corrected"}.get(behavior, "residence_unknown")
    private = case_file()[vector]
    calls = []
    if behavior == "missing_source":
        from nora.agent.dreamer import DreamerRuntime
        monkeypatch.setattr(DreamerRuntime, "batch_limit_for", lambda *_: 1)
    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: kw)

    async def invoke(agent, prompt, **kwargs):
        calls.append(prompt)
        if behavior == "provider_failure":
            raise ConnectionError("scripted provider unavailable")
        verbs = {t.tool_name: t for t in agent["tools"]}
        if behavior in ("no_change", "birthday_no_change"):
            assert await verbs["persona_no_change"](why="no residence was established") == {
                "status": "ok"}
        if behavior == "birthday_corrected":
            citations = re.findall(r'<m n="(\d+)"', prompt)
            assert await verbs["persona_write"](ops=[{
                "op": "revise", "line": "P1", "fact_key": "birthday",
                "fact_value": {"month": 6, "day": 9}, "citations": [citations[-1]],
            }]) == {"status": "ok"}
        if behavior == "wrong_residence":
            # Plausible but unsupported output: same author and a valid source,
            # exactly the semantic gap the deterministic planner cannot detect.
            citations = re.findall(r'<m n="(\d+)"', prompt)
            assert citations
            answer = await verbs["persona_write"](ops=[{
                "op": "add", "part": "identity", "kind": "attribute", "mode": "told",
                "text": "lives in Kowloon", "fact_key": "residence", "fact_value": "Kowloon",
                "told_by": "Miles", "citations": [citations[-1]],
            }])
            assert answer == {"status": "ok"}
        return SimpleNamespace(stop_reason="end_turn")

    monkeypatch.setattr(factory, "invoke", invoke)
    case = Case(name=vector, input="", metadata={
        "expect": "identity", "snapshot": source.snapshot, "person": "miles_novak",
        "synthetic_evidence": True, "private_persona_file": True,
        "persona_input": {"sources": private["sources"], "clock_ms": private["clock_ms"]},
        "print_input": behavior == "print",
    })
    result = await _drive(case, Spec(
        source_url=source.url, entry="persona_identity", policy=source.policy,
        identity=source.identity, template=source.template,
        overrides={"llm.base_url": "http://127.0.0.1:9/v1"},
    ))
    data = EvaluationData(input="", actual_output=result["output"],
                          actual_environment_state=result["environment_state"],
                          metadata={"expect": "identity"})
    [graded] = MeetsGrader().evaluate(data)
    assert graded.label == label, (graded.reason, state_of(data, NORA_ERROR))
    if behavior == "missing_source":
        assert calls == []
        assert "declared synthetic sources missing" in state_of(data, NORA_ERROR)
    elif behavior == "print":
        assert calls == []
        shown = state_of(data, NORA_SHOWN)
        (tmp_path / "shown.txt").write_text(shown)
        assert "persona-person" in shown and "2026-10-12" in shown
        assert "heading out from home" in shown
    elif label != ERROR:
        assert calls and '"settled_at":' in state_of(data, NORA_POSITION)
        assert '"samples_ok": 1' in state_of(data, NORA_POSITION)


@pytest.mark.parametrize("vector", list(case_file()))
async def test_all_declared_sources_reach_writer_after_restored_backlog(
    pg_url, pg_template, tmp_path, monkeypatch, vector,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    source = await seed_source(
        pg_url, tmp_path,
        Knowledge(transcript=tuple(f"restored background {i}" for i in range(101))),
        template=pg_template,
        policy=('default = "deny"\n[[conversations]]\nplatform = "signal"\n'
                'id = "g1"\nlabel = "g1"\npersons = ["miles_novak"]\nunit = "nora"\n'),
    )
    private = case_file()[vector]
    calls = []
    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: calls.append(kw))
    case = Case(name=vector, input="", metadata={
        "expect": "identity", "snapshot": source.snapshot, "person": "miles_novak",
        "synthetic_evidence": True, "private_persona_file": True,
        "persona_input": {"sources": private["sources"], "clock_ms": private["clock_ms"]},
        "print_input": True,
    })
    result = await _drive(case, Spec(
        source_url=source.url, entry="persona_identity", policy=source.policy,
        identity=source.identity, template=source.template,
        overrides={"llm.base_url": "http://127.0.0.1:9/v1"},
    ))
    data = EvaluationData(input="", actual_output=result["output"],
                          actual_environment_state=result["environment_state"], metadata={})
    shown = state_of(data, NORA_SHOWN)
    assert calls == []
    for item in private["sources"]:
        assert escape(item["body"], quote=False) in shown, state_of(data, NORA_ERROR)
    assert 'evidence_mode="recent"' in shown
    assert "2026-10-12" in shown
    if vector.startswith("birthday_"):
        assert "asserted 2026-05-01" in shown
