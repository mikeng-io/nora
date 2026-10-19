"""Invented census, real CLI/task/Experiment/Judge, realistic broken replies."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from evals.rebuilt import persona_coverage as coverage
from evals.rebuilt.grading import Judge


def census_input():
    accounts = [
        {
            "key": f"unit{i}#person:p{i}",
            "unit": f"unit{i}",
            "person": f"p{i}",
            "rooms": [f"room{i}"],
            "boundary": 1000,
        }
        for i in range(3)
    ]
    rows = []
    for i in range(179):
        a = i % 3
        f = {
            "row_id": f"R{i}",
            "account_index": a,
            "scope": accounts[a]["key"],
            "kind": "card" if i < 140 else "explicit",
            "prelabel": coverage.STABLE,
            "excluded": False,
        }
        rows.append(
            {
                "id": f"A{a}-R{i}",
                "account_index": a,
                "frozen": f,
                "claim": "Uses Cantonese in observed messages; proficiency unknown",
                "sources": [
                    {
                        "id": f"record-{i}",
                        "record_id": i,
                        "room": f"room{a}",
                        "unit": f"unit{a}",
                        "author": f"p{a}",
                        "ts": 100,
                        "age_ms": 100,
                        "body": "Invented observed use",
                        "role": "subject_assertion",
                    }
                ],
                "heads": [
                    {
                        "id": f"head{i}",
                        "unit": f"unit{a}",
                        "persona_key": accounts[a]["key"],
                        "state": "current",
                        "part": "identity",
                        "fact_key": "languages",
                        "citations": [str(i)],
                        "as_of": 100,
                        "fact_value": ["Cantonese (observed use only)"],
                    }
                ],
                "authored": [],
            }
        )
    frozen = [r["frozen"] for r in rows]
    return {
        "schema": 1,
        "as_of": 200,
        "provenance": {"invented": "test"},
        "frozen": frozen,
        "frozen_hash": coverage.digest(frozen),
        "rows": rows,
        "accounts": accounts,
        "cards": [{"status": 200}],
        "references": {
            "status": "ADJUDICATED",
            "adjudicated_by": "independent-test-reviewer",
            "provenance": "invented test adjudication",
            "rows": [{"id": r["id"], "expected": "covered"} for r in rows[:20]],
        },
        "models": {
            "judge_id": "nora-judge",
            "writer_id": "nora-dreamer",
            "judge_actual": "judge-physical",
            "writer_actual": "writer-physical",
            "route_provenance": "invented route receipt",
        },
    }


class FakeGateway:
    def __init__(self, *, disagree=0, broken=None):
        self.prompts = []
        self.disagree = disagree
        self.broken = broken
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **request):
        prompt = request["messages"][0]["content"]
        self.prompts.append(prompt)
        data = json.loads(prompt[len(coverage.RULES) :])
        assert len(data["lines"]) <= 16
        assert all(h["unit"] == data["account"]["unit"] for h in data["heads"])
        assert '"expected"' not in prompt and "adjudicated_by" not in prompt
        if self.broken == "outage":
            raise TimeoutError("provider unavailable")
        out = []
        for r in data["lines"]:
            wrong = self.disagree > 0
            self.disagree -= wrong
            out.append(
                {
                    "id": r["id"],
                    "label": "source_unresolved" if wrong else "covered",
                    "evidence_ids": [r["head_ids"][0], r["source_ids"][0]],
                    "why": "Full qualified claim grounded in this unit",
                }
            )
        if self.broken == "missing":
            out = out[:-1]
        if self.broken == "duplicate":
            out[-1] = out[0]
        raw = "not JSON" if self.broken == "malformed" else json.dumps(out)
        return SimpleNamespace(
            model="judge-physical",
            id="invented-response",
            choices=[SimpleNamespace(message=SimpleNamespace(content=raw), finish_reason="stop")],
            usage=SimpleNamespace(
                prompt_tokens=40, completion_tokens=20, prompt_tokens_details=None
            ),
        )

    async def close(self):
        pass


@pytest.mark.parametrize("disagree,expected", [(2, 0), (3, 1)])
def test_real_cli_roundtrip_calibrates_before_measurement(
    tmp_path, monkeypatch, capsys, disagree, expected
):
    from evals.__main__ import main
    from evals.backbone import drain_spend

    doc = census_input()
    inp, receipt = tmp_path / "private.json", tmp_path / "receipt.json"
    inp.write_text(json.dumps(doc))
    monkeypatch.setenv(coverage.ENV, str(inp))
    monkeypatch.setenv("NORA_EVAL_PERSONA_COVERAGE_RECEIPT", str(receipt))
    gateway = FakeGateway(disagree=disagree)
    monkeypatch.setattr(Judge, "_client", lambda _: gateway)
    monkeypatch.setattr("sys.argv", ["evals", "persona_coverage", "--judge", "--positions"])
    drain_spend()
    assert main() == expected
    terminal = capsys.readouterr().out
    if expected == 0:
        assert '"lines"' in terminal and '"verdict": "PASS"' in terminal
        assert '"trace_id"' in terminal and '"receipt_hash"' in terminal
    saved = json.loads(receipt.read_text())
    assert saved["calibration"]["agreements"] == 20 - disagree
    assert saved["verdict"] == ("PASS" if expected == 0 else "ERROR")
    assert len(gateway.prompts) == (
        coverage.plan(doc)["planned_calls"]
        if expected == 0
        else coverage.plan(doc)["calibration_calls"]
    )
    if expected == 0:
        assert len(saved["labels"]) == 179
        assert saved["coverage"] == {
            "card": {"total": 140, "covered": 140},
            "explicit": {"total": 39, "covered": 39},
        }
        assert all(b["trace_id"] and b["spend"] for b in saved["batches"])


@pytest.mark.parametrize("broken", ["outage", "missing", "duplicate", "malformed"])
async def test_broken_judge_never_reaches_measurement(tmp_path, monkeypatch, broken):
    gateway = FakeGateway(broken=broken)
    monkeypatch.setattr(Judge, "_client", lambda _: gateway)
    receipt = tmp_path / "receipt.json"
    monkeypatch.setenv("NORA_EVAL_PERSONA_COVERAGE_RECEIPT", str(receipt))
    out = await coverage.measure(
        census_input(), Judge(base_url="http://invalid", timeout_seconds=90)
    )
    assert out.meets("fold") is None
    saved = json.loads(receipt.read_text())
    assert saved["verdict"] == "ERROR"
    assert all(b["phase"] == "calibration" for b in saved["batches"])


def test_frozen_scope_gold_and_evidence_refusals(tmp_path, monkeypatch):
    doc = census_input()
    coverage.validate(doc)
    for mutate in (
        lambda d: d["rows"][0]["frozen"].update(excluded=True),
        lambda d: d["rows"][0]["sources"][0].update(unit="another-unit"),
        lambda d: d["rows"][0]["heads"][0].update(persona_key="other-account"),
        lambda d: d["references"].update(status="AGENT_PRELIMINARY"),
        lambda d: d.update(cards=[{"status": 404}]),
    ):
        changed = copy.deepcopy(doc)
        mutate(changed)
        with pytest.raises(ValueError):
            coverage.validate(changed)
    row = doc["rows"][0]
    label = {
        "id": row["id"],
        "label": "covered",
        "why": "qualified",
        "evidence_ids": ["head0", "record-0"],
    }
    row["heads"][0]["state"] = "retired"
    with pytest.raises(ValueError, match=r"evidence|grounding"):
        coverage.parse_labels(json.dumps([label]), [row])
    row["authored"] = [
        {
            "id": "authored-name",
            "person": "p0",
            "unit": "unit0",
            "of": "Invented Person",
            "lines": ["known as Invented Person (Example)"],
        }
    ]
    label["evidence_ids"] = ["authored-name"]
    assert coverage.parse_labels(json.dumps([label]), [row])[0]["label"] == "covered"
    # The prompt carries the source time, actual author, canonical qualifiers,
    # and chronology needed to distinguish observation from fluency/inference.
    shown = coverage.prompt(doc, [row])
    assert all(
        term in shown
        for term in (
            "as_of",
            "author",
            "age_ms",
            "newest LAST",
            "Decade is not exact age",
            "without units",
        )
    )
    # No-call CLI must print full context even while independent review is pending.
    from evals.__main__ import main

    doc["references"]["status"] = "PENDING_ROOT_REVIEW"
    inp = tmp_path / "pending.json"
    inp.write_text(json.dumps(doc))
    monkeypatch.setenv(coverage.ENV, str(inp))
    monkeypatch.setattr(Judge, "_client", lambda _: pytest.fail("print-input called model"))
    monkeypatch.setattr(
        "sys.argv", ["evals", "persona_coverage", "--print-input", "frozen_coverage"]
    )
    assert main() == 0


def test_thresholds_fixed_denominator_and_exact_five_percent():
    doc = census_input()
    labels = [{"id": r["id"], "label": "covered"} for r in doc["rows"]]
    # 112/140 = 80%; 24/39 exceeds 60%, and unresolved stays in denominator.
    for i in list(range(112, 140)) + list(range(164, 179)):
        labels[i]["label"] = "source_unresolved"
    assert coverage.aggregate(doc, labels)["verdict"] == "PASS"
    labels[111]["label"] = "source_unresolved"
    assert coverage.aggregate(doc, labels)["verdict"] == "FAIL"
    labels[111]["label"] = "covered"
    labels[163]["label"] = "source_unresolved"
    assert coverage.aggregate(doc, labels)["verdict"] == "FAIL"
    labels[163]["label"] = "derivable_missed"
    assert coverage.aggregate(doc, labels)["derivable_misses"]
    small = copy.deepcopy(doc)
    small["rows"] = small["rows"][:19] + small["rows"][140:141]
    exact = [{"id": r["id"], "label": "covered"} for r in small["rows"]]
    exact[0]["label"] = "error"
    assert coverage.aggregate(small, exact)["verdict"] == "ERROR"
    with pytest.raises(ValueError):
        coverage.aggregate(doc, labels[:-1])


async def test_unknown_or_same_actual_model_is_refused_before_client(tmp_path, monkeypatch):
    monkeypatch.setenv("NORA_EVAL_PERSONA_COVERAGE_RECEIPT", str(tmp_path / "unused.json"))
    monkeypatch.setattr(Judge, "_client", lambda _: pytest.fail("preflight paid a call"))
    for actual in (None, "writer-physical"):
        doc = census_input()
        doc["models"]["judge_actual"] = actual
        with pytest.raises(ValueError, match="model"):
            await coverage.measure(doc, Judge(base_url="http://invalid", timeout_seconds=90))
