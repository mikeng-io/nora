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
