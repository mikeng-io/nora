"""The service has time to close resources and installs its SIGTERM handler."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKER_DEFAULT_GRACE_SECONDS = 10.0


def _seconds(text: str) -> float:
    """A compose duration: `75s`, `1m30s`, `2m`."""
    total, matched = 0.0, False
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)(h|m|s)", text):
        matched = True
        total += float(amount) * {"h": 3600, "m": 60, "s": 1}[unit]
    assert matched, f"not a compose duration: {text!r}"
    return total


def _nora_grace() -> float:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    nora = compose["services"]["nora"]
    assert "stop_grace_period" in nora, "the nora service takes docker's 10 s default"
    return _seconds(str(nora["stop_grace_period"]))


def test_the_stop_grace_period_is_longer_than_dockers_default():
    assert _nora_grace() > DOCKER_DEFAULT_GRACE_SECONDS


def test_a_compose_duration_is_read_the_way_compose_reads_it():
    assert _seconds("75s") == 75 and _seconds("1m30s") == 90 and _seconds("2m") == 120


def test_the_entry_point_runs_the_app_only_under_the_signal_handler():
    tree = ast.parse((ROOT / "nora" / "__main__.py").read_text(encoding="utf-8"))
    main = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    run = next(
        n for n in ast.walk(main)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run"
    )
    calls = [
        n for n in ast.walk(run) if isinstance(n, ast.Call)
    ]
    named = {
        c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", "") for c in calls
    }
    assert "run_until_stopped" in named
    assert "run" not in named, "`app.run()` awaited bare: SIGTERM kills it with no `finally`"


def test_the_signal_handler_covers_what_docker_and_a_terminal_send():
    import signal

    from nora.__main__ import STOP_SIGNALS

    assert set(STOP_SIGNALS) == {signal.SIGTERM, signal.SIGINT}
