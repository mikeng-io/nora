"""Startup smoke test for `nora.analytics.composition.build_rollup` — the
disabled-by-default path (DEC-115) must build nothing and touch neither
identity.toml nor the database."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from nora.analytics.composition import build_rollup
from nora.config import load

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def minimal_config(tmp_path):
    # analytics.enabled defaults False in config.example.toml (DEC-115's
    # inert posture), so this never touches identity.toml, policy.toml, or a
    # database — config.load() itself does not require those paths to exist,
    # only later consumers (load_persons, load_policy) do, and build_rollup's
    # disabled branch calls neither.
    cfg_path = tmp_path / "config.toml"
    shutil.copy(ROOT / "config.example.toml", cfg_path)
    return load(cfg_path, require_secrets=False)


def test_disabled_analytics_builds_nothing(minimal_config):
    deps = build_rollup(minimal_config)
    assert deps.rollup is None
    assert deps.persons is None
    assert deps.subjects == ()
    assert deps.metrics_port == minimal_config.analytics.metrics_port
    assert deps.tick_seconds == minimal_config.analytics.tick_seconds
