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
    # Restore original evidence before testing the independent readiness path.
    doc = census_input()
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


def pin_fixture(doc, tmp_path, monkeypatch):
    projection = copy.deepcopy(doc)
    for card in projection["cards"]:
        card.pop("absence_proof", None)
    for key in ("status", "adjudicated_by", "provenance"):
        projection["references"].pop(key, None)
    for row in projection["references"]["rows"]:
        row.pop("expected", None)
    for key in ("judge_id", "writer_id", "judge_actual", "writer_actual", "route_provenance"):
        projection["models"].pop(key, None)
    manifest = {
        "schema": 1,
        "provenance": doc["provenance"],
        "census": projection,
        "counts": {
            "all_rows": 179,
            "prelabels": {coverage.STABLE: 179},
            "stable_kinds": {"card": 140, "explicit": 39},
        },
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    path.chmod(0o400)
    monkeypatch.setenv("NORA_EVAL_PERSONA_COVERAGE_MANIFEST", str(path))
    monkeypatch.setenv("NORA_EVAL_PERSONA_COVERAGE_MANIFEST_HASH", coverage.digest(manifest))
    return path


@pytest.fixture(autouse=True)
def trusted_census(tmp_path, monkeypatch):
    pin_fixture(census_input(), tmp_path, monkeypatch)


@pytest.mark.parametrize("gold", [False, True])
@pytest.mark.parametrize("mutation", ["remove", "prelabel", "unit", "claim", "id"])
def test_load_rejects_rehashed_census_mutation(tmp_path, monkeypatch, gold, mutation):
    doc = census_input()
    row = doc["rows"][-1]  # outside the twenty references
    if mutation == "remove":
        doc["rows"].pop()
    elif mutation == "prelabel":
        row["frozen"]["prelabel"] = "nonstable"
    elif mutation == "unit":
        # Consistent one-in/one-out scope change, with all counts unchanged.
        row["account_index"] = 0
        row["frozen"].update(account_index=0, scope=doc["accounts"][0]["key"])
        row["sources"] = copy.deepcopy(doc["rows"][0]["sources"])
        row["heads"] = copy.deepcopy(doc["rows"][0]["heads"])
    elif mutation == "claim":
        row["claim"] = "A different claim"
    else:
        row["id"] = "replacement-id"
    doc["frozen"] = [r["frozen"] for r in doc["rows"]]
    doc["frozen_hash"] = coverage.digest(doc["frozen"])
    inp = tmp_path / "renamed-adjudicated.json"
    inp.write_text(json.dumps(doc))
    monkeypatch.setenv(coverage.ENV, str(inp))
    monkeypatch.setattr(Judge, "_client", lambda _: pytest.fail("preflight called model"))
    with pytest.raises(ValueError, match="pinned census"):
        coverage.load_input(gold=gold)
    from evals.__main__ import main

    argv = ["evals", "persona_coverage"]
    argv += ["--judge"] if gold else ["--print-input", "frozen_coverage"]
    monkeypatch.setattr("sys.argv", argv)
    assert main() == 2


def test_pin_required_and_root_attachments_allowed(tmp_path, monkeypatch):
    doc = census_input()
    doc["cards"] = [{"status": 404}]
    private = tmp_path / "card-pin"
    private.mkdir()
    pin_fixture(doc, private, monkeypatch)
    doc["models"]["route_provenance"] = "root physical route proof"
    doc["references"]["provenance"] = "root adjudication proof"
    doc["cards"] = [
        {
            "status": 404,
            "absence_proof": {
                "snapshot_hash": "independent",
                "peer_exists": False,
                "card_exists": False,
                "explicit_population": 0,
            },
        }
    ]
    inp = tmp_path / "attached.json"
    inp.write_text(json.dumps(doc))
    monkeypatch.setenv(coverage.ENV, str(inp))
    assert coverage.load_input() == doc
    monkeypatch.delenv("NORA_EVAL_PERSONA_COVERAGE_MANIFEST_HASH")
    with pytest.raises(ValueError, match="manifest"):
        coverage.load_input(gold=False)


def test_manifest_hash_permissions_and_read_errors(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    path.chmod(0o600)
    with pytest.raises(ValueError, match="read-only"):
        coverage.validate(census_input(), gold=False)
    path.chmod(0o400)
    monkeypatch.setenv(coverage.MANIFEST_HASH_ENV, "wrong-independent-pin")
    with pytest.raises(ValueError, match="hash mismatch"):
        coverage.validate(census_input(), gold=False)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        coverage.validate(census_input(), gold=False)


def test_freeze_reconciles_original_sources_and_refuses_overwrite(tmp_path):
    import hashlib

    from evals.persona_coverage_input import freeze_manifest

    doc = census_input()
    roster = []
    for account in doc["accounts"]:
        account["watermarks"] = [
            {
                "consolidator_name": "persona-person:history-boundary",
                "last_message_id": account["boundary"],
            }
        ]
        roster.append({**account, "rooms": [{"key": r} for r in account["rooms"]]})
        del roster[-1]["boundary"]
    denominator = {"invented": "original population"}
    denominator_raw = json.dumps(denominator).encode()
    pre = {
        "denominator_hash": hashlib.sha256(denominator_raw).hexdigest(),
        "timestamp": 100,
        "rows": [],
    }
    comparisons = []
    for row in doc["rows"]:
        f = row["frozen"]
        p = {
            "row_id": f["row_id"],
            "kind": f["kind"],
            "semantic_label": f["prelabel"],
            "excluded": False,
            "text": row["claim"],
        }
        pre["rows"].append(p)
        comparisons.append({"account_index": row["account_index"], "row_id": f["row_id"]})
        f["original"] = p
        row["id"] = f"A{row['account_index']:02d}-{f['row_id']}"
        row["claim_origin"] = {
            "kind": f["kind"],
            "row_id": f["row_id"],
            "conclusion_id": None,
            "snapshot_time": 100,
            "prelabel_is_truth": False,
        }
    doc["references"]["rows"] = [{"id": r["id"]} for r in doc["rows"][:20]]
    files = {
        "DENOMINATOR-DB-FROZEN.json": denominator,
        "prelabels.json": pre,
        "roster.json": {"accounts": roster},
        "cards.json": doc["cards"],
        "comparisons.json": comparisons,
        "reference20-for-independent-review.json": {
            "rows": [{"comparisons": [c]} for c in comparisons[:20]]
        },
    }
    doc["provenance"] = {}
    for name, value in files.items():
        path = tmp_path / name
        path.write_text(json.dumps(value))
        doc["provenance"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    doc.update(original_denominator=denominator, original_prelabels=pre)
    doc["frozen_hash"] = coverage.digest(doc["frozen"])
    manifest = tmp_path / "original.manifest.json"
    pin = freeze_manifest(doc, manifest)
    assert pin["all_rows"] == 179 and manifest.stat().st_mode & 0o777 == 0o400
    with pytest.raises(FileExistsError):
        freeze_manifest(copy.deepcopy(doc), manifest)
    changed = copy.deepcopy(doc)
    changed["rows"].pop()
    with pytest.raises(ValueError, match="original source census"):
        freeze_manifest(changed, tmp_path / "renamed.manifest.json")
    (tmp_path / "comparisons.json").write_text("[]")
    with pytest.raises(ValueError, match="source file hash"):
        freeze_manifest(doc, tmp_path / "other.manifest.json")


@pytest.mark.parametrize("gold", [False, True])
@pytest.mark.parametrize("mutation", [
    "body", "author", "role", "ts", "heads", "authored", "as_of",
    "remove_sources", "missing_sources", "extra_row_key", "extra_top_key",
    "reference_preliminary", "extra_model_key",
])
def test_full_evidence_pin_rejects_rehashed_mutation(tmp_path, monkeypatch, gold, mutation):
    doc = census_input()
    row = doc["rows"][-1]
    if mutation in {"body", "author", "role", "ts"}:
        row["sources"][0][mutation] = 999 if mutation == "ts" else "altered"
    elif mutation == "heads":
        row["heads"][0]["fact_value"] = ["Fabricated value"]
    elif mutation == "authored":
        row["authored"] = [{"id": "fabricated", "unit": "unit1", "person": "p1",
                            "lines": ["Fabricated identity"]}]
    elif mutation == "as_of":
        doc["as_of"] += 1
    elif mutation == "remove_sources":
        row["sources"] = []
    elif mutation == "missing_sources":
        del row["sources"]
    elif mutation == "extra_row_key":
        row["unexpected"] = "changed"
    elif mutation == "extra_top_key":
        doc["unexpected"] = "changed"
    elif mutation == "reference_preliminary":
        doc["references"]["rows"][0]["preliminary"] = "changed"
    else:
        doc["models"]["unexpected"] = "changed"
    doc["frozen_hash"] = coverage.digest(doc["frozen"])
    assert len(coverage.stable_rows(doc)) == 179
    inp = tmp_path / "mutated.json"
    inp.write_text(json.dumps(doc))
    monkeypatch.setenv(coverage.ENV, str(inp))
    with pytest.raises(ValueError, match="pinned census"):
        coverage.load_input(gold=gold)


@pytest.mark.parametrize("size", [4, 2, 1])
def test_complete_json_fence_preserves_strict_coverage_labels(size):
    rows = census_input()["rows"][:size]
    labels = [{"id": r["id"], "label": "covered",
               "evidence_ids": [r["heads"][0]["id"], r["sources"][0]["id"]],
               "why": "Invented full scoped grounding"} for r in rows]
    assert coverage.parse_labels("```json\n" + json.dumps(labels) + "\n```", rows) == labels


@pytest.mark.parametrize("raw", [
    '```json\n[]\n``` trailing', 'prefix ```json\n[]\n```',
    '```json\n[]', '```json\n[\n```', '[] []',
    '```json\n[]\n```\n```json\n[]\n```', '```json[]```', '```json\n[]```',
])
def test_coverage_rejects_incomplete_or_multiple_payloads(raw):
    with pytest.raises(ValueError):
        coverage.parse_labels(raw, [])


@pytest.mark.asyncio
async def test_coverage_requests_bounded_low_reasoning_without_changing_other_judges(monkeypatch):
    gateway = FakeGateway()
    original = gateway.create

    async def create(**request):
        assert request["reasoning_effort"] == "low"
        assert request["max_tokens"] == 4096
        return await original(**request)

    gateway.create = create
    monkeypatch.setattr(Judge, "_client", lambda _: gateway)
    doc = census_input()
    rows = coverage.batches(doc["rows"])[0][:4]
    result = await Judge(base_url="http://invalid", timeout_seconds=90).coverage_batch(
        coverage.prompt(doc, rows)
    )
    assert "error" not in result
    assert len(coverage.parse_labels(result["raw"], rows)) == 4
    assert coverage.plan(doc)["max_response_tokens"] == coverage.plan(doc)["planned_calls"] * 4096


def test_serialized_coverage_contract_preserves_claim_and_complete_provenance():
    """Render contract only: no fake verdict establishes semantic accuracy."""
    doc = census_input()
    row = doc["rows"][0]
    row["claim"] = "Reports being able to juggle and expects a bicycle back if repaired"
    row["closure"] = {"status": "REPAIRED", "annotation": "not-judge-evidence"}
    row["heads"][0]["citations"] = ["0", "3"]
    row["sources"].append(copy.deepcopy(doc["rows"][3]["sources"][0]))
    shown = coverage.prompt(doc, [row])
    rules, payload = shown.split('{"account":', 1)
    data = json.loads('{"account":' + payload)
    assert data["lines"][0]["claim"] == row["claim"]
    assert data["heads"][0]["citations"] == ["0", "3"]
    assert {s["id"] for s in data["sources"]} == {"record-0", "record-3"}
    assert "ALL original Record\nIDs" in rules and "at least one chosen head" in rules
    assert "Citation\ncompleteness is provenance, not semantic entailment" in rules
    assert "Reporting mode is part of the claim" in rules
    assert "SAME evidence standard" in rules
    assert "reports being able to\njuggle" in rules
    assert "has a bicycle and expects\nit back by Friday if repaired" in rules
    assert "stable portion alone is not covered" in rules
    assert "Read closure" not in rules
    assert "Maintenance annotations are not evidence" in rules
    assert "not-judge-evidence" not in shown and '"closure"' not in shown
    assert '"expected"' not in shown and "adjudicated_by" not in shown


@pytest.mark.parametrize("size,fenced", [(4, False), (2, True), (1, False)])
def test_coverage_accepts_complete_legacy_object_sequence(size, fenced):
    rows = census_input()["rows"][:size]
    labels = [{"id": r["id"], "label": "covered",
               "evidence_ids": [r["heads"][0]["id"], r["sources"][0]["id"]],
               "why": "Invented full scoped grounding"} for r in rows]
    raw = "\n".join(json.dumps(label, indent=2) for label in labels)
    if fenced:
        raw = "```json\n" + raw + "\n```"
    assert coverage.parse_labels(raw, rows) == labels


@pytest.mark.parametrize("mutation", [
    "wrong_ids", "duplicate", "missing", "prose", "truncated", "arrays",
    "trailing", "adjacent", "bad_record", "bad_evidence", "partial_fence",
])
def test_coverage_legacy_sequence_never_salvages_a_bad_batch(mutation):
    rows = census_input()["rows"][:2]
    labels = [{"id": r["id"], "label": "covered",
               "evidence_ids": [r["heads"][0]["id"], r["sources"][0]["id"]],
               "why": "Invented full scoped grounding"} for r in rows]
    if mutation == "wrong_ids":
        labels[1]["id"] = "other-batch"
    elif mutation == "duplicate":
        labels[1] = labels[0]
    elif mutation == "missing":
        labels = labels[:1]
    elif mutation == "bad_record":
        labels[1]["label"] = "invalid"
    elif mutation == "bad_evidence":
        labels[1]["evidence_ids"] = ["unavailable"]
    raw = "\n".join(json.dumps(label) for label in labels)
    if mutation == "prose":
        raw = "Here are the results:\n" + raw
    elif mutation == "truncated":
        raw = raw[:-1]
    elif mutation == "arrays":
        raw = json.dumps(labels) + "\n" + json.dumps(labels)
    elif mutation == "trailing":
        raw += "\nDone"
    elif mutation == "adjacent":
        raw = "".join(json.dumps(label) for label in labels)
    elif mutation == "partial_fence":
        raw = "```json\n" + raw
    with pytest.raises(ValueError):
        coverage.parse_labels(raw, rows)


def test_legacy_objects_are_coverage_only_and_array_prompt_is_explicit():
    from evals.rebuilt.judge import parse_json_response

    assert parse_json_response('{"id": "invented"}') == {"id": "invented"}
    with pytest.raises(ValueError):
        parse_json_response('{}\n{}')
    assert "ONE JSON array" in coverage.RULES
    assert "PER INPUT CLAIM inside that array" in coverage.RULES
    assert "one object per line" not in coverage.RULES
    assert coverage.plan(census_input())["rules_hash"] == coverage.digest(coverage.RULES)


def test_prompt_renders_source_calendar_time_without_rewriting_evidence():
    doc = census_input()
    row = doc["rows"][0]
    # Invented source near UTC midnight: the calendar day must be Hong Kong's.
    row["sources"][0].update(ts=1767198600000, body="Maybe next Friday if repaired")
    original = copy.deepcopy(doc)
    rendered = json.loads(coverage.prompt(doc, [row])[len(coverage.RULES) :])
    source = rendered["sources"][0]
    assert source["time_hkt"] == "2026-01-01T00:30:00+08:00"
    assert source == {**row["sources"][0], "time_hkt": "2026-01-01T00:30:00+08:00"}
    assert rendered["source_order"] == "oldest_first"
    assert rendered["lines"][0]["claim"] == row["claim"]
    assert doc == original
