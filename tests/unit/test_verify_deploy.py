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


def test_digest_readback_keeps_missing_and_stale_services_in_denominator(monkeypatch):
    vd = _load()
    expected = dict.fromkeys(('nora', 'presence', 'analytics'), 'sha256:new')
    rows = [dict(_row(), service='nora'),
            dict(_row(name='presence', image_id='old'), service='presence')]
    # The old image reference MUST NOT exclude presence from the reading.
    monkeypatch.setattr(vd, '_docker', lambda *a: __import__('json').dumps([
        {'Id': r['name'], 'Name': '/' + r['name'], 'Image': r['image_id'], 'RestartCount': 0,
         'Config': {'Image': 'old', 'Labels': {'com.docker.compose.project': 'nora',
                    'com.docker.compose.service': r['service']}},
         'State': {'Running': True, 'Health': {'Status': 'healthy'}}} for r in rows
    ]) if a[0] == 'container' else 'nora\npresence')
    actual = vd.containers_for_services('nora', set(expected))
    assert {r['service'] for r in actual} == {'nora', 'presence'}
    assert vd.service_errors(actual, expected) == [
        'analytics: expected exactly one container, found 0',
        'presence: image differs from release']


@pytest.mark.parametrize(('container_id', 'restarts', 'accepted'), [
    ('retained', 2, True),   # recovered before deployment: no new crash
    ('retained', 3, False),  # a fresh crash on a retained container
    ('recreated', 0, True),
    ('recreated', 1, False), # an old container's allowance cannot cover a new one
])
def test_selective_readback_binds_restart_allowance_to_container_identity(
        monkeypatch, container_id, restarts, accepted):
    vd = _load()
    monkeypatch.setattr(vd, 'image_id', lambda image: 'sha256:new')
    monkeypatch.setattr(vd, 'containers_for_services', lambda project, services: [
        dict(_row(restarts=restarts), service='nora', container_id=container_id)])
    if accepted:
        vd.verify_services('nora', {'nora': 'synthetic-image'}, settle=0, timeout=0,
                           baseline={'retained': 2})
    else:
        with pytest.raises(RuntimeError, match='new restarts'):
            vd.verify_services('nora', {'nora': 'synthetic-image'}, settle=0, timeout=0,
                               baseline={'retained': 2})


@pytest.mark.parametrize(("health", "warned"), [
    ({"platforms": {"signal": {"state": "live", "degraded": False},
                    "whatsapp": {"state": "degraded", "degraded": True,
                                 "detail": "logged out"}}}, ["whatsapp"]),
    ({"platforms": {"signal": {"state": "live", "degraded": False}}}, []),
])
def test_a_degraded_platform_is_a_warning_not_a_failure(monkeypatch, capsys, health, warned):
    """`verify_services` — the function `scripts/komodo_deploy.py` calls —
    passes a healthy release whose Nora reports a degraded platform, printing
    a WARNING naming it (docs/design/platform-bridges/platform-liveness.md
    «Visibility»). The container checks still decide pass or fail."""
    import json

    vd = _load()
    monkeypatch.setattr(vd, 'image_id', lambda image: 'sha256:new')
    monkeypatch.setattr(vd, 'containers_for_services', lambda project, services: [
        dict(_row(), service='nora', container_id='c1')])
    calls = []

    def docker(*args):
        calls.append(args)
        return json.dumps(health)

    monkeypatch.setattr(vd, '_docker', docker)
    vd.verify_services('nora', {'nora': 'synthetic-image'}, settle=0, timeout=0)

    out = capsys.readouterr().out
    assert "VERIFIED" in out
    assert [line.split()[2] for line in out.splitlines()
            if line.startswith("WARNING: platform")] == warned
    assert calls == [("exec", "c1", "curl", "-sS", "--max-time", "5",
                      "http://127.0.0.1:9100/health")]


@pytest.mark.parametrize(('state', 'match'), [
    ({'health': 'unhealthy'}, 'not healthy'),
    ({'restarts': 1}, 'new restarts'),
])
def test_an_unhealthy_or_restarted_nora_still_fails_with_platform_warnings_wired(
        monkeypatch, state, match):
    """The positive control: the warning path never turns a failed container
    into a pass — `/health` is not even read before the container checks pass."""
    vd = _load()
    monkeypatch.setattr(vd, 'image_id', lambda image: 'sha256:new')
    monkeypatch.setattr(vd, 'containers_for_services', lambda project, services: [
        dict(_row(**state), service='nora', container_id='c1')])
    monkeypatch.setattr(vd, '_docker', lambda *a: (_ for _ in ()).throw(
        AssertionError("health read before the container passed")))
    monkeypatch.setattr(vd.time, 'sleep', lambda s: None)
    with pytest.raises(RuntimeError, match=match):
        vd.verify_services('nora', {'nora': 'synthetic-image'}, settle=0, timeout=0)


def test_a_failed_health_read_through_the_komodo_wrapper_is_only_a_warning(
        monkeypatch, capsys):
    """Komodo replaces `_docker` with a wrapper that raises its own
    `CommandError` (`scripts/komodo_deploy.py`). An optional `/health` read
    that fails that way after `VERIFIED` must stay a warning, never fail the
    deploy."""
    spec = importlib.util.spec_from_file_location(
        "komodo_deploy", SCRIPT.with_name("komodo_deploy.py"))
    assert spec and spec.loader
    komodo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(komodo)
    vd = _load()
    monkeypatch.setattr(vd, 'image_id', lambda image: 'sha256:new')
    monkeypatch.setattr(vd, 'containers_for_services', lambda project, services: [
        dict(_row(), service='nora', container_id='c1')])

    def wrapper(*args):
        raise komodo.CommandError("readback", 124, None)

    monkeypatch.setattr(vd, '_docker', wrapper)
    vd.verify_services('nora', {'nora': 'synthetic-image'}, settle=0, timeout=0)
    out = capsys.readouterr().out
    assert "VERIFIED" in out and "WARNING: could not read Nora's /health" in out
