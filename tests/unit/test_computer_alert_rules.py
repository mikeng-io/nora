"""Computer alert inputs, opt-out and failure thresholds in the SigNoz port."""
from pathlib import Path

import yaml

from nora.domain.computer import SandboxStatus
from nora.obs.metrics import DECLARED_NAMES
from scripts.signoz_provision import _rule_payload

ROOT = Path(__file__).resolve().parents[2]


def _rules():
    doc = yaml.safe_load((ROOT / 'deploy/signoz/alerts/rules.yaml').read_text())
    return {r['name']: r for r in doc['rules']}


def test_the_rules_exist_and_read_series_the_code_declares():
    rules = _rules()
    for name, metric in (
        ('ComputerDown', 'nora_computer_up'),
        ('ComputerCallsFailing', 'nora_computer_calls_total'),
        ('ComputerAuditFailing', 'nora_computer_audit_failures_total'),
    ):
        assert metric in rules[name]['query']
        assert metric in DECLARED_NAMES


def test_computer_down_is_not_an_absence_rule():
    rule = _rules()['ComputerDown']
    assert rule['query'] == 'nora_computer_up'
    assert rule['op'] == 'equal' and rule['target'] == 0
    assert rule['window'] == '5m' and rule['match'] == 'all_the_times'


def test_the_failure_status_is_one_the_runtime_emits():
    rule = _rules()['ComputerCallsFailing']
    assert 'status="unavailable"' in rule['query']
    assert 'unavailable' in {s.value for s in SandboxStatus}
    payload = _rule_payload(rule['name'], rule, {}, 'nora-alerts')
    threshold = payload['condition']['thresholds']['spec'][0]
    assert threshold['op'] == 'above_or_equal' and threshold['target'] == 5
    assert threshold['channels'] == ['nora-alerts']
    assert 'absentFor' not in payload['condition']


def test_the_generic_unavailable_rule_leaves_the_computer_series_to_its_own():
    assert '__name__!="nora_computer_calls_total"' in _rules()['UnavailableOutcomes']['query']
