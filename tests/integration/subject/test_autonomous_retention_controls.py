"""Durable native-report and JSON/HTML agreement controls.

`retain_report` is the seam `python -m evals` uses to keep the SDK's own
report beside its expected-row accounting: the retained file must carry
the SDK rows, the coverage census and the three axes, not a shadow copy.
`report_html.render` must put the SAME report the JSON holds on the page
— status, axes, census and lifecycle effects — never a page that merely
names the case. Both run on frozen inputs; no runtime is needed.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from evals import backbone
from evals.accounting import evidence_outcome
from scripts.cognition_loop import html as report_html
from tests.integration.subject.test_sdk_recorded_caller import (
    sdk_evidence as sdk_evidence,
)


class _Report:
    """The report shape `retain_report` serializes."""

    def __init__(self) -> None:
        self.cases: list[dict[str, Any]] = []
        self.detailed_results = [[SimpleNamespace(label="PASS")]]

    def model_dump(self, *, mode: str | None = None) -> dict[str, Any]:
        assert mode == "json"
        return {"cases": [], "detailed_results": [[{"label": "PASS"}]]}


def _axes() -> dict[str, Any]:
    return {
        "execution": {"status": "PASS"},
        "validity": {"status": "PASS"},
        "behavior": {"status": "UNMEASURED"},
    }


def _report() -> dict[str, Any]:
    """A minimal retained cognition report: one case, one wake, one
    delivery and one settlement inside that wake's observed effects."""
    return {
        "status": "PASS", "mode": "autonomous-live-journey",
        "behavior": "UNMEASURED", "errors": [],
        "run": {"run_id": "generated", "mode": "autonomous-live-journey",
                "behavior": "UNMEASURED"},
        "evidence": {"status": "PASS", "causes": [], "ref": "capture"},
        "coverage": {"status": "PASS",
                     "expected": {"cases": 1, "wakes": 2, "criteria": 3,
                                  "judge_answers": 6},
                     "observed": {"cases": 1, "wakes": 2, "criteria": 3,
                                  "judge_answers": 6},
                     "errors": []},
        "outcome": _axes(),
        "cases": [{
            "case": "generated-draw", "status": "PASS", "errors": [],
            "wakes": [{
                "status": "PASS", "recorded_wake_id": 11, "wake_id": 11,
                "at_ms": 1234, "reason": "strong_cue", "session_id": 7,
                "expectations": [],
                "trajectory": {
                    "unit": "generated-unit",
                    "effects": {
                        "status": "observed", "unit": "generated-unit",
                        "since": "2026-01-01T00:00:00+00:00",
                        "items": [
                            {"kind": "wake", "source": "cognition_wake",
                             "at": "2026-01-01T00:00:01+00:00",
                             "ref": "wake:cognition_wake:11",
                             "row": {"id": 11, "unit": "generated-unit"}},
                            {"kind": "delivery",
                             "source": "bridge_action",
                             "at": "2026-01-01T00:00:02+00:00",
                             "ref": "delivery:bridge_action:a1",
                             "row": {"action_id": "a1"}},
                            {"kind": "wake",
                             "source": "cognition_wake_outcome",
                             "at": "2026-01-01T00:00:03+00:00",
                             "ref": "wake:cognition_wake_outcome:o1",
                             "row": {"wake_id": 11, "outcome": "closed",
                                     "session_id": 7,
                                     "unit": "generated-unit"}},
                        ],
                    },
                    "settlement": {"status": "observed"},
                    "final_response": {"status": "observed"},
                },
            }],
        }],
        "expected_cases": [{
            "name": "generated-draw", "unit": "generated-unit",
            "wakes": [11], "expectations": [], "read_probes": [],
        }],
    }


def test_retain_report_keeps_sdk_rows_census_and_axes(sdk_evidence):
    """Through the real CLI seam: the retained file carries the SDK's own
    report rows plus coverage/outcome — durable bytes in the run's
    evidence directory, identical to the payload returned."""
    coverage = {"status": "PASS",
                "expected": {"cases": 1}, "observed": {"cases": 1},
                "errors": []}
    payload = backbone.retain_report(
        sdk_evidence, "generated-draw", _Report(), coverage=coverage)
    path = sdk_evidence.path / "corpus-generated-draw.json"
    native = json.loads(path.read_text())
    assert native["corpus"] == "generated-draw"
    assert native["report"] == {"cases": [],
                                "detailed_results": [[{"label": "PASS"}]]}
    assert native["coverage"] == coverage
    for axis, status in (("execution", "PASS"), ("validity", "PASS"),
                         ("behavior", "UNMEASURED")):
        assert native["outcome"][axis]["status"] == status
        assert payload["outcome"][axis]["status"] == status
    sdk_evidence.record("run_finished")


def test_retained_html_renders_the_same_report(tmp_path: Path):
    """The HTML is the same report read, never re-derived: its status,
    every axis, the coverage census and the wake's lifecycle effects —
    including settlement and delivery — are all on the page."""
    report = _report()
    (tmp_path / "report.json").write_text(json.dumps(report))
    wake = report["cases"][0]["wakes"][0]
    (tmp_path / "wake-0-11.json").write_text(json.dumps(wake))
    rendered = report_html.render(tmp_path).read_text()
    assert '<span class="status-PASS">PASS</span>' in rendered
    for label, status in (("execution", "PASS"),
                          ("measurement validity", "PASS"),
                          ("behaviour", "UNMEASURED")):
        assert f"<th>{label}</th><td>{status}</td>" in rendered
    for name, count in report["coverage"]["expected"].items():
        assert f'&quot;{name}&quot;: {count}' in rendered
    assert "&quot;cognition_wake_outcome&quot;" in rendered
    assert "&quot;bridge_action&quot;" in rendered
    assert "generated-draw" in rendered
