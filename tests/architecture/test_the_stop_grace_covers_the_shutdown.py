"""`docker stop` gives nora `stop_grace_period` between SIGTERM and SIGKILL, and what
nora does in that time has a number.

The default grace is 10 s. A stopping nora cancels its adapters and loops, lets a session
already running finish for `SESSION_SHUTDOWN_GRACE_SECONDS`, cancels what is left (each
records its wake `cancelled`, a database write), then closes the model client and the
store. If `stop_grace_period` is not above the session grace by the time the rest needs,
Docker SIGKILLs a process that is in the middle of releasing a claim, which is the very
thing the grace exists to avoid, and it looks like a handled shutdown on every deploy that
had no session running.

Also pinned: that the entry point runs `app.run()` only under the signal handler. A
`main()` that goes back to awaiting `app.run()` directly passes every other test and
quietly returns the process to dying on SIGTERM with no `finally`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

from nora.app.cognition_wiring import SESSION_SHUTDOWN_GRACE_SECONDS

ROOT = Path(__file__).resolve().parents[2]
#: What the rest of shutdown needs after the session grace: stopping the adapters, one
#: cancelled outcome write per session, closing the model client and the store. 30 is a
#: margin, not a measurement.
REST_OF_SHUTDOWN_SECONDS = 30.0
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


def test_the_stop_grace_period_covers_the_session_grace_and_the_rest_of_shutdown():
    assert _nora_grace() >= SESSION_SHUTDOWN_GRACE_SECONDS + REST_OF_SHUTDOWN_SECONDS


def test_the_session_grace_is_pinned_to_its_production_value():
    """Every test of `CognitionShutdown` injects its OWN grace, and the margin test
    above only checks `grace + 30 <= stop_grace_period`, which a grace of 0.0 satisfies
    with room to spare: the constant could be zeroed (every running session cancelled
    at once, never given its last moments) and nothing here would notice. The number is
    pinned to a literal on purpose, so changing it is a decision made in this file and
    in `docker-compose.yml` together."""
    assert SESSION_SHUTDOWN_GRACE_SECONDS == 30.0


def test_the_stop_grace_period_exceeds_the_pinned_session_grace_by_the_rest_of_shutdown():
    """The compose figure read from the file (75 s) against the PINNED grace, not the
    imported one: a constant that moves cannot drag its own margin with it."""
    assert _nora_grace() - 30.0 >= REST_OF_SHUTDOWN_SECONDS


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
