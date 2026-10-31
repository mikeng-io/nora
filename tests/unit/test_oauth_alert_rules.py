"""OAuth alert inputs and boundaries survive the move to native SigNoz rules."""
from pathlib import Path

import yaml

from nora.app import oauth_keepalive
from nora.obs.metrics import DECLARED_NAMES
from scripts.signoz_provision import _rule_payload

ROOT = Path(__file__).resolve().parents[2]


def _rules():
    doc = yaml.safe_load((ROOT / 'deploy/signoz/alerts/rules.yaml').read_text())
    return {r['name']: r for r in doc['rules']}


def test_the_rules_read_series_the_keeper_declares_and_sets():
    import re

    source = Path(oauth_keepalive.__file__).read_text()
    for name in ('OAuthRefreshStale', 'OAuthRefreshRefused', 'OAuthAccessTokenExpired'):
        for metric in re.findall(r'nora_[a-z_]+', _rules()[name]['query']):
            assert metric in DECLARED_NAMES
            assert f'"{metric}"' in source


def test_the_oauth_comparison_boundaries_are_preserved():
    rules = _rules()
    assert rules['OAuthRefreshStale']['query'] == (
        '(time() - nora_oauth_last_refresh_timestamp_seconds) '
        '> bool (2 * nora_oauth_refresh_interval_seconds)')
    assert rules['OAuthAccessTokenExpired']['query'] == (
        '(nora_oauth_access_expiry_timestamp_seconds > bool 0) '
        '* (nora_oauth_access_expiry_timestamp_seconds < bool time())')
    assert rules['OAuthRefreshRefused']['query'] == 'nora_oauth_refresh_refused'
    assert rules['OAuthRefreshRefused']['op'] == 'equal'
    assert rules['OAuthRefreshRefused']['target'] == 1
    for name, window in (('OAuthRefreshStale', '15m'), ('OAuthAccessTokenExpired', '30m')):
        payload = _rule_payload(name, rules[name], {}, 'nora-alerts')
        assert payload['evaluation']['spec']['evalWindow'] == window
        threshold = payload['condition']['thresholds']['spec'][0]
        assert threshold['matchType'] == 'all_the_times'
        assert threshold['channels'] == ['nora-alerts']
