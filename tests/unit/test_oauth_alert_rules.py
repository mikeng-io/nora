"""The OAuth keepalive's alert rules (DEC-307) read series the code declares and
publishes, and fire as `deploy/prometheus/tests/oauth.test.yml` says they do.

A rule on a series nobody publishes is silent forever and looks exactly like a
healthy one, so the names are read off the metric specs AND off the keeper that
sets them; the behaviour runs through promtool (binary, pinned image, or skip),
the computer rules' posture.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from nora.app import oauth_keepalive
from nora.obs.metrics import DECLARED_NAMES
from tests.unit.test_computer_alert_rules import _promtool

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "deploy" / "prometheus" / "rules" / "nora.yml"


def _rules() -> dict[str, dict]:
    doc = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    return {r["alert"]: r for g in doc["groups"] for r in g["rules"]}


def test_the_rules_read_series_the_keeper_declares_and_sets() -> None:
    keeper_source = Path(oauth_keepalive.__file__).read_text(encoding="utf-8")
    rules = _rules()
    for alert in ("OAuthRefreshStale", "OAuthRefreshRefused", "OAuthAccessTokenExpired"):
        for series in re.findall(r"nora_[a-z_]+", rules[alert]["expr"]):
            assert series in DECLARED_NAMES, (alert, series)
            assert f'"{series}"' in keeper_source, (alert, series)


def test_staleness_is_on_the_timestamp_not_a_counter() -> None:
    """Prometheus loses a rare counter across this process's daily restarts;
    the stale rule must read the timestamp the boot republishes."""
    expr = _rules()["OAuthRefreshStale"]["expr"]
    assert "nora_oauth_last_refresh_timestamp_seconds" in expr
    assert "_total" not in expr and "absent" not in expr


def test_the_alerts_reach_the_channel_through_the_existing_route() -> None:
    rules = _rules()
    assert rules["OAuthRefreshStale"]["labels"]["severity"] == "critical"
    assert rules["OAuthRefreshRefused"]["labels"]["severity"] == "critical"
    assert rules["OAuthAccessTokenExpired"]["labels"]["severity"] == "critical"
    routes = yaml.safe_load((ROOT / "deploy" / "alertmanager" / "alertmanager.yml").read_text())
    assert routes["route"]["receiver"] == "discord"


def test_the_oauth_rules_pass_their_promtool_cases() -> None:
    result = _promtool("test", "rules", "oauth.test.yml")
    assert result.returncode == 0, result.stdout + result.stderr
