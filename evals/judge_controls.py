"""Judge-only calibration using frozen generated controls and existing consumers.

Inspect by default. Execution needs an explicitly selected private job fingerprint;
that binding is not operator spending authority or reference-label validation.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from evals import grading_contract as binding
from evals.rebuilt.grading import Judge, criteria_for
from nora.domain.run_budget import active_run_budget
from nora.subject.eval_evidence import (
    EvidenceError,
    RunEvidence,
    evaluation_export_for,
    fingerprint,
    read_events,
    revision,
    validate_events,
)
from nora.subject.sandbox import EvalLockHeldError, eval_host_lock
from scripts import memory_graph_spike as spike
from scripts.memory_graph_facts import smoke_fact_support


def read_bound(ref: dict[str, str]) -> Any:
    path = Path(ref["path"])
    if path.stat().st_mode & 0o077:
        raise EvidenceError("calibration files must be private (0600)")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise EvidenceError("calibration artifact fingerprint mismatch")
    return json.loads(raw, object_pairs_hook=binding._pairs)


def memory_input(data: dict) -> tuple[dict, list[dict]]:
    """Same projection as the actual-consumer preflight, with no oracle fields."""
    inspection = {"system": "Generated subject instruction (not the normative judge policy)",
                  "agenda": json.dumps(data["source_rows"]),
                  "new_sources": {str(r["id"]): r for r in data["source_rows"]},
                  "context": data["context"]}
    statements = [{"what": s["text"], "cite": [{"source": n} for n in s["cite"]]}
                  for s in data["saved_statements"]]
    exchanges = [{"name": "memory_propose", "input": {"statements": statements},
                  "source_rows": inspection["new_sources"],
                  "result": {"statements": [{"index": i, "status": "saved"}
                                             for i in range(len(statements))]}}]
    return inspection, exchanges


def prepare(path: Path, expected: str, config: Path) -> tuple[dict, list, dict, dict]:
    job = read_bound({"path": str(path), "sha256": expected})
    if set(job) != {"version", "source_head", "source_diff_sha256", "config_sha256",
                    "judges", "controls",
                    "oracle", "contracts", "budget"} or job["version"] != 1:
        raise EvidenceError("invalid calibration job")
    source = revision(binding._ROOT)
    if (job["source_head"] != source["head"]
            or job["source_diff_sha256"] != source["diff_sha256"]
            or job["config_sha256"] != fingerprint(config)):
        raise EvidenceError("calibration source/config changed")
    judges = job["judges"]
    if (not isinstance(judges, list) or not judges or len(set(judges)) != len(judges)
            or any(not isinstance(j, str) or not j for j in judges)):
        raise EvidenceError("invalid selected judges")
    controls, oracle = read_bound(job["controls"]), read_bound(job["oracle"])
    if not isinstance(controls, list) or not controls:
        raise EvidenceError("empty calibration controls")
    keys = [c["key"] for c in controls]
    if len(set(keys)) != len(keys) or set(keys) != set(oracle):
        raise EvidenceError("calibration oracle/cohort mismatch")
    if set(job["contracts"]) != {"memory", "doctrine"}:
        raise EvidenceError("both calibration contracts are required")
    contracts = {}
    for lane, entry in (("memory", "rescore"), ("doctrine", "eval")):
        ref = job["contracts"][lane]
        contracts[lane] = binding.load_contract(Path(ref["path"]), ref["sha256"],
            entrypoint=entry, judges=tuple(judges),
            selection={"controls_sha256": job["controls"]["sha256"], "lane": lane})
        if contracts[lane].data["purpose"] != ("calibration" if lane == "memory" else "diagnosis"):
            raise EvidenceError("calibration purpose mismatch")
        if set(contracts[lane].data["cases"]) != {c["key"] for c in controls if c["lane"] == lane}:
            raise EvidenceError("calibration references do not cover the exact lane")
    calls = 0
    for c in controls:
        if set(c) != {"key", "lane", "input"} or c["lane"] not in contracts:
            raise EvidenceError("invalid calibration control")
        data, lane = c["input"], c["lane"]
        ref = contracts[lane].reference(c["key"])
        if lane == "memory":
            if set(data) != {"source_rows", "context", "required_units", "saved_statements"}:
                raise EvidenceError("invalid memory control projection")
            inspection, _ = memory_input(data)
            contracts[lane].memory_reference(c["key"], inspection)
            if ref["units"] != data["required_units"]:
                raise EvidenceError("memory coverage differs from frozen reference")
            e = oracle[c["key"]]
            if (set(e) != {"support", "eligibility", "coverage"}
                    or len(e["support"]) != len(data["saved_statements"])
                    or len(e["eligibility"]) != len(data["saved_statements"])
                    or set(e["coverage"]) != {u["id"] for u in ref["units"]}
                    or any(v not in {"supported", "unsupported"} for v in e["support"])
                    or any(v not in {"eligible", "ineligible"} for v in e["eligibility"])
                    or any(v not in {"covered", "missed"} for v in e["coverage"].values())):
                raise EvidenceError("invalid clear memory-control oracle")
            calls += len(judges) * (len(data["saved_statements"]) + 1)
        else:
            if (set(data) != {"answer", "scenario", "fixed_acceptance", "bound_criteria"}
                    or data["bound_criteria"] not in [
                        ["no_overreach_or_fabrication", "social_suitability"],
                        ["no_overreach_or_fabrication", "public_provenance"]]
                    or ref["input_sha256"] != binding.digest(data)
                    or ref["expected_output"] != data["fixed_acceptance"]
                    or oracle[c["key"]] not in {"PASS", "FAIL"}):
                raise EvidenceError("invalid doctrine control/reference")
            calls += len(judges)
    b = job["budget"]
    limit = b.get("per_call_output_tokens", 4096)
    if (set(b) not in ({"measured_tokens", "requests", "output_tokens", "seconds"},
                      {"measured_tokens", "requests", "output_tokens", "seconds",
                       "per_call_output_tokens"})
            or any(type(v) is not int or v <= 0 for v in b.values())
            or b["requests"] != calls or b["output_tokens"] != calls * limit):
        raise EvidenceError("calibration budget differs from planned calls")
    return job, controls, oracle, contracts


def compare_memory(rows: list[dict], alias: str, expected: dict) -> dict:
    actual = {
        "support": [r["verdict"] for r in rows if r["kind"] == "judge_verdict"
                    and r.get("alias") == alias],
        "eligibility": [r["verdict"] for r in rows if r["kind"] == "eligibility_verdict"
                        and r.get("alias") == alias],
        "coverage": {r["unit"]: r["verdict"] for r in rows if r["kind"] == "coverage_verdict"
                     and r.get("alias") == alias},
    }
    errors = any(r["kind"] == "judge_error" and r.get("alias") in {None, alias} for r in rows)
    complete = (len(actual["support"]) == len(expected["support"])
                and len(actual["eligibility"]) == len(expected["eligibility"])
                and set(actual["coverage"]) == set(expected["coverage"]))
    unsure = any(v == "unsure" for values in actual.values()
                 for v in (values.values() if isinstance(values, dict) else values))
    return {"actual": actual, "expected": expected,
            "status": "ERROR" if errors or not complete or unsure else
                      "PASS" if actual == expected else "FAIL"}


async def execute(job: dict, controls: list, oracle: dict, contracts: dict,
                  *, config: Path, output: Path, job_sha256: str) -> dict:
    from opentelemetry import trace

    from evals.backbone import _prefill_capture

    with eval_host_lock() as lock_fd:
        export = evaluation_export_for(config)
        _prefill_capture()
        output = spike.private_directory(output)
        evidence = RunEvidence(run_id=f"judge-controls-{uuid.uuid4().hex}",
            metadata={"purpose": "generated development judge discrimination", "subjects": 0,
                      "job_sha256": job_sha256, "job": job,
                      **revision(binding._ROOT)}, lock_fd=lock_fd, private_root=output)
        report: dict[str, Any] = {"status": "ERROR", "subjects": 0, "comparisons": []}
        try:
            with evidence.capture(trace.get_tracer_provider(), export=export):
                evidence.record("calibration_inputs", controls=controls, oracle=oracle)
                args = SimpleNamespace(stop_after_tokens=job["budget"]["measured_tokens"])
                with spike.measured_run_budget(args, evidence, output, report,
                        max_requests=job["budget"]["requests"],
                        max_output_tokens=job["budget"]["output_tokens"],
                        deadline=time.monotonic() + job["budget"]["seconds"]):
                    budget = active_run_budget.get()
                    assert budget is not None
                    judges = spike.Judges(spike.load(config), job["judges"], config,
                                          max_tokens=job["budget"].get(
                                              "per_call_output_tokens", 4096))
                    for c in controls:
                        budget.would_admit()
                        data, lane, key = c["input"], c["lane"], c["key"]
                        with evidence.case(key, input=data, metadata={"lane": lane}):
                            contract = contracts[lane]
                            contract.record()
                            start = len(read_events(evidence.path))
                            if lane == "memory":
                                inspection, exchanges = memory_input(data)
                                await smoke_fact_support(judges, inspection, exchanges,
                                    contract=contract, reference_key=key)
                                rows = read_events(evidence.path)[start:]
                                comparisons = [dict(alias=a, **compare_memory(rows, a, oracle[key]))
                                               for a in job["judges"]]
                            else:
                                name = data["bound_criteria"][1]
                                extra = criteria_for((name,), **(
                                    {"acceptance": contract.reference(key)["expected_output"]}
                                    if name == "social_suitability" else {}))
                                comparisons = []
                                for alias in job["judges"]:
                                    result = await Judge(base_url="", model=alias, samples=1,
                                        config_path=config, contract=contract,
                                        max_tokens=job["budget"].get(
                                            "per_call_output_tokens", 4096)).grade(
                                            said=data["answer"],
                                            asked=data["scenario"], source=data["scenario"],
                                            doctrine="", extra=extra)
                                    comparisons.append({"alias": alias, "actual": result.verdict,
                                        "expected": oracle[key], "status": "ERROR"
                                        if result.verdict == "REVIEW" or result.error else
                                        "PASS" if result.verdict == oracle[key] else "FAIL"})
                            for comparison in comparisons:
                                evidence.record("control_comparison", **comparison)
                                report["comparisons"].append({"case": key, **comparison})
                            if any(r["status"] == "ERROR" for r in comparisons):
                                raise EvidenceError("control measurement unavailable")
                    if budget.requests != job["budget"]["requests"]:
                        raise EvidenceError("calibration request count mismatch")
                    report["status"] = ("FAIL" if any(c["status"] == "FAIL"
                                        for c in report["comparisons"]) else "PASS")
                # Capture completion is not the final report outcome. A late file error
                # must not leave a successful terminal outcome in the trace.
                evidence.exit_code = None
        except Exception as exc:  # noqa: BLE001 - no private exception text in reports
            report.update(status="ERROR", error_type=type(exc).__name__)
        report["evidence"] = validate_events(read_events(evidence.path))
        if report["evidence"]["status"] != "PASS":
            report["status"] = "ERROR"
        spike.freeze(output / "report.json", report)
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--job-sha256", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--execute", action="store_true",
                        help="requires separate operator budget approval")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        job, controls, oracle, contracts = prepare(args.job, args.job_sha256, args.config)
        if not args.execute:
            print(json.dumps({"status": "INSPECTED_ZERO_DISPATCH", "controls": len(controls),
                              "judges": job["judges"], "subjects": 0, "budget": job["budget"]}))
            return 0
        if args.output is None:
            parser.error("--execute requires a fresh private --output directory")
        report = asyncio.run(execute(job, controls, oracle, contracts, config=args.config,
                                    output=args.output, job_sha256=args.job_sha256))
        print(json.dumps({"status": report["status"], "subjects": 0}))
        return {"PASS": 0, "FAIL": 1, "ERROR": 2}[report["status"]]
    except EvalLockHeldError:
        print("calibration refused: another evaluation holds the host lock")
        return 2
    except (OSError, ValueError, KeyError, TypeError, EvidenceError):
        print("calibration refused: invalid or changed job; no success claimed")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
