"""The alerted delete outcomes exist at zero from the start (PR #711).

`OrphanDeleteLost` and `OrphanDeleteEvicted` (`deploy/prometheus/rules/nora.yml`) read
`increase()` of two outcomes of `nora_delete_withdrawals_total`. A labelled series does
not exist until its first increment and `increase()` cannot see that first increment,
and nora restarts several times a day, so a rare loss right after a restart would be
invisible. `prime_delete_metrics` creates every `(platform, outcome)` at 0.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from nora.app.delete_withdrawal import PRIMED_OUTCOMES, prime_delete_metrics
from nora.obs.metrics import Metrics

ROOT = Path(__file__).resolve().parents[2]


def _series(metrics: Metrics) -> set[tuple[str, str, str]]:
    found = set()
    for line in metrics.render().splitlines():
        m = re.match(r'nora_delete_withdrawals_total\{(.*)\} ([0-9.e+-]+)$', line)
        if m:
            labels = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
            found.add((labels["platform"], labels["outcome"], m.group(2)))
    return found


def test_an_unprimed_metric_has_no_series_and_a_primed_one_has_every_platform_at_zero():
    assert _series(Metrics()) == set()
    metrics = Metrics()
    prime_delete_metrics(metrics)
    assert _series(metrics) == {
        (p, o, "0.0") for p in ("signal", "whatsapp", "discord", "instagram")
        for o in ("orphan_lost", "orphan_evicted")
    }


def test_priming_does_not_move_a_counter_that_already_counted():
    metrics = Metrics()
    metrics.inc("nora_delete_withdrawals_total", platform="signal", outcome="orphan_lost")
    prime_delete_metrics(metrics)
    assert metrics.get("nora_delete_withdrawals_total", platform="signal",
                       outcome="orphan_lost") == 1


def test_the_primed_outcomes_are_the_ones_the_rules_read_and_the_touch_stage_emits():
    rules = {r["alert"]: r for g in yaml.safe_load(
        (ROOT / "deploy/prometheus/rules/nora.yml").read_text())["groups"]
        for r in g["rules"]}
    read = {m for a in ("OrphanDeleteLost", "OrphanDeleteEvicted")
            for m in re.findall(r'outcome="([a-z_]+)"', rules[a]["expr"])}
    assert read == set(PRIMED_OUTCOMES)
    emitted = set(re.findall(
        r'outcome="([a-z_]+)"', (ROOT / "nora/ingest/stages/touch.py").read_text()))
    assert set(PRIMED_OUTCOMES) <= emitted


def test_the_alert_table_in_the_observability_doc_names_both_rules():
    doc = (ROOT / "docs/observability.md").read_text()
    for alert in ("OrphanDeleteLost", "OrphanDeleteEvicted"):
        assert f"| `{alert}` |" in doc, alert
