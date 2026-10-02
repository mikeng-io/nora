"""The cognition loop's alerts and the series they read (the Cognition cut-over's
PR-7): NoraRestartLoop, NoraErrorsRising, CognitionWakeStalled, StimulusBacklog,
CognitionSessionsFailing.

A rule on a series nobody emits is silent for ever and reads exactly like a healthy
one (the Life360 rule's lesson, `tests/test_deploy_assets.py`), so the names and the
label values are read off the code here, and the behaviour is run through promtool
(`deploy/prometheus/tests/cognition.test.yml`: the binary when installed, the pinned
Prometheus image when docker is, a skip otherwise).

**No identifying label.** The series that carry a unit (`nora_stimulus_pending_age_
seconds{unit}`) are aggregated away: an alert says that something is stuck and
`nora cognition health` says where, so no alert text names a room, a person or a unit.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from nora.domain.stimuli import WAKE_MODES, WAKE_OUTCOMES
from nora.obs import metric_specs
from nora.obs.metrics import DECLARED_NAMES
from tests.unit.test_computer_alert_rules import PROM, ROOT, _promtool

ALERTS = {
    "NoraRestartLoop": "critical",
    "NoraErrorsRising": "warning",
    "CognitionWakeStalled": "critical",
    "StimulusBacklog": "warning",
    "CognitionSessionsFailing": "warning",
}

#: The only labels a cognition alert may select, group by or print. All are closed
#: vocabularies the code owns (`job` is the scrape target, `stage` a code constant,
#: `outcome`/`mode` derived from the domain's own tuples).
ALLOWED_LABELS = {"job", "stage", "outcome", "mode"}


def _rules() -> dict[str, dict]:
    doc = yaml.safe_load((PROM / "rules" / "nora.yml").read_text(encoding="utf-8"))
    return {r["alert"]: r for g in doc["groups"] for r in g["rules"]}


def _expr(alert: str) -> str:
    return " ".join(str(_rules()[alert]["expr"]).split())


def test_the_alerts_exist_with_the_severity_the_channel_routes_on():
    rules = _rules()
    for alert, severity in ALERTS.items():
        assert rules[alert]["labels"]["severity"] == severity, alert


def test_every_series_a_rule_reads_is_declared_by_the_code():
    reads = {
        "NoraRestartLoop": "nora_process_start_time_seconds",
        "NoraErrorsRising": "nora_errors_total",
        "CognitionWakeStalled": "nora_cognition_wake_last_tick_seconds",
        "StimulusBacklog": "nora_stimulus_pending_age_seconds",
        "CognitionSessionsFailing": "nora_cognition_session_ended_total",
    }
    for alert, series in reads.items():
        assert series in _expr(alert), alert
        assert series in DECLARED_NAMES, series


def test_the_outcome_labels_are_outcomes_the_session_runner_records():
    expr = _expr("CognitionSessionsFailing")
    named = {v for group in re.findall(r'outcome=~"([a-z|]+)"', expr) for v in group.split("|")}
    assert named == {"closed", "failed", "timeout"}
    assert named <= set(WAKE_OUTCOMES)
    assert "cancelled" not in expr, "a restart is not a verdict on a session"


def test_the_stages_the_errors_alert_names_are_stages_the_code_counts():
    text = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (ROOT / "nora" / "app").glob("*.py")
    )
    described = _rules()["NoraErrorsRising"]["annotations"]["description"]
    for stage in re.findall(r"`(cognition_[a-z]+)`", described):
        assert f'stage="{stage}"' in text, stage


def test_no_cognition_alert_selects_groups_by_or_prints_an_identifying_label():
    for alert in ALERTS:
        rule = _rules()[alert]
        expr = _expr(alert)
        selected = set(re.findall(r"([a-z_]+)\s*=~?\s*\"", expr))
        grouped = {
            label.strip() for group in re.findall(r"\bby\s*\(([^)]*)\)", expr)
            for label in group.split(",") if label.strip()
        }
        printed = set(re.findall(
            r"\$labels\.([A-Za-z_]+)",
            rule["annotations"]["summary"] + rule["annotations"].get("description", ""),
        ))
        used = selected | grouped | printed
        assert used <= ALLOWED_LABELS, (alert, used)
    # The one series that carries a unit is aggregated away, not selected by it.
    backlog = _expr("StimulusBacklog")
    assert backlog.startswith("max(") and "unit" not in backlog


def test_the_new_series_carry_no_identifying_label_and_the_loop_gauge_is_labelled_by_mode():
    """Cardinality: a unit, room, person or message is never a label. The wake gauge
    carries `mode` BECAUSE a labelled gauge has no sample until it is set: unlabelled it
    exports 0 from declaration and `time() - 0` would page a deployment with cognition
    off."""
    specs = {s.name: s for s in (*metric_specs._COUNTERS, *metric_specs._GAUGES)}
    assert specs["nora_cognition_wake_last_tick_seconds"].labelnames == ("mode",)
    assert specs["nora_process_start_time_seconds"].labelnames == ()
    assert specs["nora_cognition_wake_reclaimed_total"].labelnames == ()
    assert "shadow" in WAKE_MODES
    for name in ("nora_cognition_wake_last_tick_seconds", "nora_process_start_time_seconds",
                 "nora_cognition_wake_reclaimed_total"):
        assert set(specs[name].labelnames) <= ALLOWED_LABELS, name


def test_the_alerts_reach_the_channel_through_the_existing_route():
    routes = yaml.safe_load((ROOT / "deploy" / "alertmanager" / "alertmanager.yml").read_text())
    assert routes["route"]["receiver"] == "discord"
    assert "routes" not in routes["route"]


def test_the_alert_table_in_the_observability_doc_lists_every_rule_in_the_file():
    """The doc listed 9 of 16 when this was written. A rule with no row is an alert
    nobody was told about, so the table is held to the file."""
    doc = (ROOT / "docs" / "observability.md").read_text(encoding="utf-8")
    table = doc.split("| alert | fires on |", 1)[1].split("\n\n", 1)[0]
    listed = set(re.findall(r"^\| `([A-Za-z0-9]+)` \|", table, flags=re.M))
    assert listed == set(_rules())


def test_the_cognition_rules_pass_their_promtool_cases():
    result = _promtool("test", "rules", "cognition.test.yml")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_rules_file_still_passes_promtool_check():
    result = _promtool("check", "rules", "../rules/nora.yml")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_promtool_cases_cover_every_alert_this_step_added():
    cases = yaml.safe_load((PROM / "tests" / "cognition.test.yml").read_text(encoding="utf-8"))
    tested = {
        t["alertname"]
        for case in cases["tests"] for t in case.get("alert_rule_test", [])
    }
    assert set(ALERTS) <= tested
    assert Path(PROM / "tests" / "cognition.test.yml").exists()
