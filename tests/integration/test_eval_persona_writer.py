"""The real composed writer and SQL store, with only the provider boundary scripted."""
import json
import os
import re
from datetime import UTC, datetime
from html import escape
from pathlib import Path
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
    ("quantity_repaired", PASS),
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
              "birthday_corrected": "birthday_corrected",
              "quantity_repaired": "height_age_stated"}.get(behavior, "residence_unknown")
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
        if behavior == "quantity_repaired":
            assert "both require a finite numeric value" in agent["system_prompt"]
            citations = re.findall(r'<m n="(\d+)"', prompt)
            assert "I am 178 cm tall and I am 34 years old." in prompt
            age = {"op": "add", "part": "identity", "kind": "attribute", "mode": "told",
                   "text": "source-reported age", "fact_key": "attribute",
                   "fact_value": {"name": "age", "value": 34},
                   "told_by": "Miles", "citations": [citations[-1]]}
            refused = await verbs["persona_write"](ops=[age])
            assert refused["status"] == "refused"
            assert "age and height require a numeric value and source unit" in str(refused)
            repaired = {**age, "fact_value": {"name": "age", "value": 34, "unit": "years"}}
            height = {**age, "text": "source-reported height",
                      "fact_value": {"name": "height", "value": 178, "unit": "cm"}}
            assert await verbs["persona_write"](ops=[repaired, height]) == {"status": "ok"}
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
    elif behavior == "quantity_repaired":
        position = json.loads(state_of(data, NORA_POSITION))
        assert position["samples_ok"] == 1 and position["settled_at"] is not None
        assert len(position["heads"]) == 2
        assert {h["fact_value"]["name"]: h["fact_value"] for h in position["heads"]} == {
            "age": {"name": "age", "value": 34, "unit": "years"},
            "height": {"name": "height", "value": 178, "unit": "cm"},
        }
        assert all(h["fact_key"] == "attribute" and h["as_of"] == private["sources"][0]["ts"]
                   for h in position["heads"])
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
    (tmp_path / f"{vector}.print-input.txt").write_text(shown)
    assert 'evidence_mode="recent"' in shown
    assert "2026-10-12" in shown
    if vector.startswith("birthday_"):
        assert "asserted 2026-05-01" in shown


async def test_restored_out_of_order_rows_do_not_enter_recent_writer_batch(
    pg_url, pg_template, tmp_path, monkeypatch,
):
    from evals.backbone import _close
    from nora.subject import Message, SubjectSpec, know, obtain
    from nora.subject.persona_writer import run_writer
    from tests.sandbox_fixtures import BEFORE_MS

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    subject = await obtain(SubjectSpec(
        database_url=pg_url, workdir=tmp_path,
        overrides={"dreamer.enabled": True, "persona.enabled": True},
        policy=('default = "deny"\n[[conversations]]\nplatform = "signal"\n'
                'id = "g1"\nlabel = "g1"\npersons = ["miles_novak"]\nunit = "nora"\n'),
    ))
    calls = []
    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: calls.append(kw))
    try:
        # IDs 1..10 are newer by timestamp than restored ID 11. Seed the
        # sandbox directly: snapshot reconstruction sorts rows by timestamp.
        await know(subject, Knowledge(transcript=tuple(
            Message(body=f"restored background {i}", person="miles_novak",
                    at_ms=200 if i < 11 else 100)
            for i in range(1, 12)
        )), now_ms=BEFORE_MS)
        result = await run_writer(
            subject, person="miles_novak",
            sources=[{"body": "invented arrival only", "ts": BEFORE_MS + 1000}],
            held_birthday=False, print_input=True, now_ms=BEFORE_MS + 2000,
        )
        shown = result["shown"]
        assert calls == []
        assert "invented arrival only" in shown
        assert re.findall(r'<m n="(\d+)"', shown) == ["12"]
        assert "restored background" not in shown
        assert 'evidence_mode="recent"' in shown
    finally:
        await _close(subject)


@pytest.mark.parametrize("vector,exposure", [
    ("profile_cached", "cached"), ("profile_missing", "cached"),
    ("profile_late", "cached"), ("profile_cached", "caption_only"),
    ("profile_endorsed_late", "cached"),
    ("profile_media_only_late", "cached"), ("profile_media_only_late", "caption_only"),
    ("profile_media_only_missing", "cached"), ("profile_media_only_missing_first", "cached"),
])
@pytest.mark.parametrize("print_only", [True, False])
async def test_source_lane_composed_input_and_storage(
    pg_url, tmp_path, monkeypatch, vector, exposure, print_only,
):
    from evals.backbone import _close
    from evals.rebuilt.persona_identity import check_identity
    from nora.subject import SubjectSpec, obtain
    from nora.subject.persona_writer import run_writer

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    monkeypatch.setenv("NORA_EVAL_PERSONA_MEDIA_EXPOSURE", exposure)
    v2 = vector.startswith(("profile_endorsed", "profile_media_only"))
    missing_first = vector == "profile_media_only_missing_first"
    if missing_first:
        vector = "profile_media_only_missing"
    private = case_file(source_lanes=not v2, source_lanes_v2=v2)[vector]
    if missing_first:
        sources = private["sources"]
        sources[1]["attachment"]["reading"] = "Profile registry: K7 belongs to Miles."
        sources[0]["attachment"]["reading"] = None
    media_only = vector.startswith("profile_media_only")
    preference_text = ("usual food preference is vegetarian ramen." if media_only else
                       "My usual food preference is vegetarian ramen.")
    subject = await obtain(SubjectSpec(
        database_url=pg_url, workdir=tmp_path,
        now=datetime.fromtimestamp(private["clock_ms"] / 1000, UTC),
        overrides={"dreamer.enabled": True, "persona.enabled": True},
        policy=('default = "deny"\n[[conversations]]\nplatform = "signal"\n'
                'id = "g1"\nlabel = "g1"\npersons = ["miles_novak"]\nunit = "nora"\n'),
    ))
    available = not vector.endswith("missing") and exposure == "cached"
    calls = []
    agents = []

    def create_agent(**kwargs):
        agents.append(kwargs)
        return kwargs

    monkeypatch.setattr(factory, "create_dream_agent", create_agent)

    async def invoke(agent, prompt, **kwargs):
        calls.append(prompt)
        assert (preference_text in prompt) == (
            exposure == "cached" and not missing_first
            and private["sources"][0]["attachment"]["reading"] is not None)
        verbs = {t.tool_name: t for t in agent["tools"]}
        if available:
            ids = re.findall(r'<m n="(\d+)"', prompt)
            if media_only:
                # Both roots are actual interpretation_only sources, even though
                # their Record author is Miles. The owning validator must refuse told.
                for root in dict.fromkeys(ids):
                    refused = await verbs["persona_write"](ops=[{
                        "op": "add", "part": "identity", "kind": "attribute", "mode": "told",
                        "text": "usually prefers vegetarian ramen", "fact_key": "food_preferences",
                        "fact_value": ["vegetarian ramen"], "told_by": "Miles",
                        "citations": [root],
                    }])
                    assert refused["status"] == "refused"
                assert "Profile registry: K7 belongs to Miles." in prompt
            result = await verbs["persona_write"](ops=[{
                "op": "add", "part": "identity", "kind": "attribute", "mode": "deduced",
                "text": "usually prefers vegetarian ramen", "fact_key": "food_preferences",
                "fact_value": ["vegetarian ramen"], "citations": list(dict.fromkeys(ids)),
            }])
        else:
            result = await verbs["persona_no_change"](why="no readable profile evidence")
        assert result == {"status": "ok"}
        return SimpleNamespace(stop_reason="end_turn")

    monkeypatch.setattr(factory, "invoke", invoke)
    try:
        args = {"subject": subject, "person": "miles_novak", "sources": private["sources"],
                "held_birthday": False, "now_ms": private["clock_ms"]}
        receipt = await run_writer(**args, print_input=print_only)
        if print_only:
            assert calls == [] and agents == []
            shown = receipt["shown"]
            assert (preference_text in shown) == (
                exposure == "cached" and not missing_first
                and private["sources"][0]["attachment"]["reading"] is not None)
            if media_only:
                captions = re.findall(r'"raw_caption": "([^"]*)"', shown)
                assert len(captions) == 2
                assert all(re.fullmatch(r"\s*\[image [a-f0-9]{6}\]\s*", c) for c in captions)
                assert set(re.findall(r'<m n="(\d+)"', shown)) == {"1", "2"}
            assert "2026-10-10" in shown and "shared_by_author" in shown
            assert '<about_person name="Miles" now="2026-10-12T08:00:00+08:00"' in shown
            assert '<m n="1" room="g1"' in shown
            evidence = Path(os.environ.get("NORA_WRITER_PROOF_DIR", str(tmp_path)))
            evidence.mkdir(exist_ok=True)  # noqa: ASYNC240 -- offline test artifact
            filename = f"{vector}{'-first' if missing_first else ''}-{exposure}-print.txt"
            (evidence / filename).write_text(shown)
            return
        assert len(calls) == len(agents) == 1
        assert "2026-10-10" in calls[0] and "shared_by_author" in calls[0]
        assert ("This is my own self-profile card" in calls[0]) == (not media_only)
        if not available:
            assert "no readable media evidence" in calls[0]
        assert bool(receipt["heads"]) == available
        if exposure == "cached":
            assert check_identity(vector, receipt) == ()
        else:
            assert check_identity(vector, receipt)
        # Save synthetic actual composed inputs for the offline receipt.
        evidence = Path(os.environ.get("NORA_WRITER_PROOF_DIR", str(tmp_path)))
        evidence.mkdir(exist_ok=True)
        filename = f"{vector}{'-first' if missing_first else ''}-{exposure}.txt"
        (evidence / filename).write_text(calls[0])
        (evidence / (filename + ".receipt.json")).write_text(json.dumps(receipt, default=str))
    finally:
        await _close(subject)


async def test_v2_missing_reading_cannot_pass_with_missing_fixture_root(
    pg_url, tmp_path, monkeypatch,
):
    from evals.backbone import _close
    from nora.agent.dreamer import DreamerRuntime
    from nora.subject import SubjectSpec, obtain
    from nora.subject.persona_writer import run_writer

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    private = case_file(source_lanes_v2=True)["profile_media_only_missing"]
    subject = await obtain(SubjectSpec(
        database_url=pg_url, workdir=tmp_path,
        overrides={"dreamer.enabled": True, "persona.enabled": True},
        policy=('default = "deny"\n[[conversations]]\nplatform = "signal"\n'
                'id = "g1"\nlabel = "g1"\npersons = ["miles_novak"]\nunit = "nora"\n'),
    ))
    agents = []
    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: agents.append(kw))
    monkeypatch.setattr(DreamerRuntime, "batch_limit_for", lambda *_: 1)
    try:
        with pytest.raises(ValueError, match="declared synthetic sources missing"):
            await run_writer(subject, person="miles_novak", sources=private["sources"],
                             held_birthday=False, print_input=False, now_ms=private["clock_ms"])
        assert agents == []
    finally:
        await _close(subject)


@pytest.mark.parametrize("vector", [
    "categorical_report", "observed_sharing", "observed_participation",
])
@pytest.mark.parametrize("print_only", [True, False])
@pytest.mark.parametrize("judge_mode", ["pass", "fail", "missing"])
async def test_representation_sources_use_composed_writer_and_store(
    pg_url, pg_template, tmp_path, monkeypatch, vector, print_only, judge_mode,
):
    from nora.subject.obtain import _DEFAULT_IDENTITY

    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    policy = ('default = "deny"\n[[conversations]]\nplatform = "signal"\n'
              'id = "g1"\nlabel = "Common room"\npersons = ["miles_novak"]\nunit = "nora"\n'
              '[[conversations]]\nplatform = "discord"\nid = "d1"\nlabel = "Common room"\n'
              'persons = ["miles_novak"]\nunit = "nora"\n')
    identity = _DEFAULT_IDENTITY + '[[persons.miles_novak.discord]]\nuser_id = "12345"\n'
    source = await seed_source(pg_url, tmp_path, Knowledge(transcript=("background",)),
                               template=pg_template, policy=policy, identity=identity)
    private = case_file(representation=True)[vector]
    calls = []
    monkeypatch.setattr(factory, "create_dream_agent", lambda **kw: kw)

    async def invoke(agent, prompt, **kwargs):
        calls.append(prompt)
        citations = re.findall(r'<m n="(\d+)"', prompt)
        op = {"op": "add", "part": "character", "kind": "trait", "mode": "induced",
              "citations": citations, "text": (
                  "Observed sharing Instagram reels; creator account ownership "
                  "and usual frequency unknown"
                  if vector == "observed_sharing" else
                  "Observed participating on Signal and Discord; account ownership "
                  "and usual frequency unknown")}
        if vector == "categorical_report":
            op.update(part="identity", kind="attribute", mode="told", told_by="Miles",
                      text="source-reported categorical statement", fact_key="attribute",
                      fact_value={"name": "statement", "value":
                          "Self-reported being in their forties, including unspecified peers"})
        verbs = {t.tool_name: t for t in agent["tools"]}
        assert await verbs["persona_write"](ops=[op]) == {"status": "ok"}
        return SimpleNamespace(stop_reason="end_turn")

    monkeypatch.setattr(factory, "invoke", invoke)
    case = Case(name=vector, input="", metadata={
        "expect": "identity", "snapshot": source.snapshot, "person": "miles_novak",
        "synthetic_evidence": True, "private_persona_file": True,
        "persona_input": {"sources": private["sources"], "clock_ms": private["clock_ms"]},
        "print_input": print_only,
    })
    from evals.rebuilt.grading import Judgement

    class ScriptedJudge:
        async def grade_identity(self, *, heads, evidence):
            payload = json.loads(heads)
            source = json.loads(evidence)
            assert source["sources"] == private["sources"]
            assert source["subject"] == "miles_novak"
            assert payload["samples_ok"] == 1 and payload["samples_failed"] == 0
            # A wrong/missing canonical claim is not accepted by this fixture.
            good = judge_mode == "pass" and bool(payload["heads"]) and all(
                "Owns" not in h["text"] for h in payload["heads"])
            return Judgement("identity_representation", "PASS" if good else "FAIL", (),
                             receipt={"actual_model": "scripted-judge", "usage_known": False,
                                      "response_id": "response-1", "trace_id": "trace-1"})

    result = await _drive(case, Spec(source_url=source.url, entry="persona_identity",
        judge=None if judge_mode == "missing" else ScriptedJudge(),
        policy=policy, identity=identity, template=source.template,
        overrides={"llm.base_url": "http://127.0.0.1:9/v1"}))
    data = EvaluationData(input="", actual_output=result["output"],
        actual_environment_state=result["environment_state"], metadata={"expect": "identity"})
    if print_only:
        assert calls == []
        shown = state_of(data, NORA_SHOWN)
        assert shown, state_of(data, NORA_ERROR)
        for item in private["sources"]:
            assert escape(item["body"], quote=False) in shown
        (tmp_path / f"{vector}.print-input.txt").write_text(shown)
        if vector == "observed_participation":
            assert 'platform="signal"' in shown and 'platform="discord"' in shown
    else:
        [graded] = MeetsGrader().evaluate(data)
        expected = {"pass": PASS, "fail": FAIL, "missing": ERROR}[judge_mode]
        assert graded.label == expected, (graded.reason, state_of(data, NORA_ERROR))
        assert len(calls) == (0 if judge_mode == "missing" else 1)
        if judge_mode != "missing":
            judged = json.loads(state_of(data, NORA_POSITION))["judge"]
            assert judged["usage_known"] is False
            assert judged["actual_model"] == "scripted-judge"
            assert judged["response_id"] == "response-1" and judged["trace_id"] == "trace-1"
