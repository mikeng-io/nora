"""`scripts/verify-deploy.py` must fail a deploy whose container is crash
looping, even when the read lands on a moment it is `running`."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "verify-deploy.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_deploy", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(**state):
    base = {"name": "nora", "image_id": "sha256:new", "declared": "nora:latest",
            "running": True, "health": "healthy", "restarts": 0}
    return {**base, **state}


@pytest.mark.parametrize(("state", "code"), [
    ({}, 0),
    # A restart loop reads as not ok, even between restarts.
    ({"health": "starting", "restarts": 2}, 1),
    ({"health": "healthy", "restarts": 1}, 1),
    ({"health": "unhealthy"}, 1),
    ({"running": False}, 1),
    ({"image_id": "sha256:old"}, 1),
    ({"health": "none"}, 0),  # no healthcheck: running with no restarts is up
])
def test_a_crash_loop_fails_the_deploy_even_when_read_while_running(monkeypatch, state, code):
    vd = _load()
    monkeypatch.setattr(vd, "image_id", lambda image: "sha256:new")
    monkeypatch.setattr(vd, "containers_for", lambda image: [_row(**state)])
    monkeypatch.setattr(vd.time, "sleep", lambda s: None)
    assert vd.main(["--settle", "0", "--healthy-timeout", "0"]) == code
