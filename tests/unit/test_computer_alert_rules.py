"""The computer's alert rules (DEC-261) name series that exist, in the words the
code emits, and fire as the cases in `deploy/prometheus/tests/computer.test.yml`
say they do.

A rule on a label nobody emits is silent forever and looks exactly like a healthy
one (see the Life360 rule in `tests/test_deploy_assets.py`). So the names and
the status word are read off the code here, and the behaviour is run through
promtool: the binary when it is installed, the pinned Prometheus image when docker
is, and a skip otherwise (the same posture as the presence rules' test).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from nora.domain.computer import SandboxStatus
from nora.obs.metrics import DECLARED_NAMES

ROOT = Path(__file__).resolve().parents[2]
PROM = ROOT / "deploy" / "prometheus"


def _rules() -> dict[str, dict]:
    doc = yaml.safe_load((PROM / "rules" / "nora.yml").read_text(encoding="utf-8"))
    return {r["alert"]: r for g in doc["groups"] for r in g["rules"]}


def test_the_rules_exist_and_read_series_the_code_declares() -> None:
    rules = _rules()
    for alert, series in (
        ("ComputerDown", "nora_computer_up"),
        ("ComputerCallsFailing", "nora_computer_calls_total"),
        ("ComputerAuditFailing", "nora_computer_audit_failures_total"),
    ):
        assert series in rules[alert]["expr"], alert
        assert series in DECLARED_NAMES, series


def test_the_failure_status_is_one_the_runtime_emits() -> None:
    expr = _rules()["ComputerCallsFailing"]["expr"]
    statuses = set(re.findall(r'status="([a-z_]+)"', expr))
    assert statuses == {"unavailable"}
    assert statuses <= {s.value for s in SandboxStatus}


def test_computer_down_is_not_an_absence_rule() -> None:
    """With the computer off the series has no sample. A rule on `absent()` (or
    `or vector(0)`) would read that as down and page a deployment that has no
    computer. This is the property the 'not applicable' promtool case runs."""
    expr = _rules()["ComputerDown"]["expr"]
    assert "absent" not in expr and "vector(" not in expr
    assert expr.strip() == "nora_computer_up == 0"


def test_the_alerts_reach_the_channel_through_the_existing_route() -> None:
    """Nothing here adds a route: the rules carry the same severity labels as
    the rest and Alertmanager's one receiver, the bridge, takes them all."""
    rules = _rules()
    assert rules["ComputerDown"]["labels"]["severity"] == "critical"
    assert rules["ComputerCallsFailing"]["labels"]["severity"] == "warning"
    routes = yaml.safe_load((ROOT / "deploy" / "alertmanager" / "alertmanager.yml").read_text())
    assert routes["route"]["receiver"] == "discord"
    assert "routes" not in routes["route"]


def test_the_generic_unavailable_rule_leaves_the_computer_series_to_its_own() -> None:
    expr = _rules()["UnavailableOutcomes"]["expr"]
    assert '__name__!="nora_computer_calls_total"' in expr


def _pinned_prometheus_image() -> str:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]["prometheus"]["image"]


def _promtool(*args: str) -> subprocess.CompletedProcess[str]:
    tests = PROM / "tests"
    if shutil.which("promtool"):
        cmd = ["promtool", *args]
        cwd: Path | None = tests
    elif shutil.which("docker"):
        probe = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, check=False, timeout=60,
        )
        if probe.returncode != 0:
            pytest.skip("neither promtool nor a running docker")
        cmd = ["docker", "run", "--rm", "--entrypoint", "promtool", "-v", f"{PROM}:/p:ro",
               "-w", "/p/tests", _pinned_prometheus_image(), *args]
        cwd = None
    else:
        pytest.skip("neither promtool nor docker")
    return subprocess.run(  # noqa: S603
        cmd, cwd=cwd, capture_output=True, text=True, check=False, timeout=600,
    )


def test_the_rules_file_passes_promtool_check() -> None:
    result = _promtool("check", "rules", "../rules/nora.yml")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_computer_rules_pass_their_promtool_cases() -> None:
    result = _promtool("test", "rules", "computer.test.yml", "presence.test.yml", "music.test.yml")
    assert result.returncode == 0, result.stdout + result.stderr
