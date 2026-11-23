"""Functional settings and workspace boundaries survive subject acquisition."""

from __future__ import annotations

import tomllib

import tomlkit

from evals.subject_config import build, divergences
from nora.subject.obtain import SubjectSpec, _baseline, _with, build_config


def test_subject_baseline_preserves_enabled_features_modes_and_self_identity(
    example_config, tmp_path,
):
    cfg = example_config
    for path in ("heartbeat.enabled", "compute.enabled", "presence.enabled",
                 "participation.enabled", "relevance_gate.enabled"):
        cfg = _with(cfg, path, True)
    for platform in ("signal", "discord", "whatsapp", "instagram"):
        cfg = _with(cfg, f"platforms.{platform}.enabled", True)
        cfg = _with(cfg, f"platforms.{platform}.mode", "bridge")
    baseline = _baseline(cfg, tmp_path)
    assert baseline.platforms == cfg.platforms
    for name in ("heartbeat", "compute", "presence", "participation", "relevance_gate"):
        assert getattr(baseline, name) == getattr(cfg, name)
    assert baseline.credentials == cfg.credentials


def test_generated_config_preserves_functional_settings_and_reports_feature_drift(
    example_config_path,
):
    example = example_config_path.read_text(encoding="utf-8")
    deployment = tomlkit.parse(example)
    for name in ("heartbeat", "compute", "presence", "participation", "relevance_gate"):
        deployment[name]["enabled"] = True
    for platform in ("signal", "discord", "whatsapp", "instagram"):
        deployment["platforms"][platform]["enabled"] = True
        deployment["platforms"][platform]["mode"] = "bridge"
    source = tomlkit.dumps(deployment)
    generated = build(example, source)
    values = tomllib.loads(generated)
    for name in ("heartbeat", "compute", "presence", "participation", "relevance_gate"):
        assert values[name]["enabled"] is True
    assert values["platforms"] == tomllib.loads(source)["platforms"]
    assert not divergences(generated, source)
    broken = tomlkit.parse(generated)
    broken["platforms"]["discord"]["enabled"] = False
    assert "platforms.discord.enabled" in divergences(tomlkit.dumps(broken), source)


def test_image_oauth_issuers_and_state_reach_both_kept_lanes(
    example_config_path, tmp_path, monkeypatch,
):
    from pathlib import Path

    from nora.infra.image_gen.openai_codex_oauth import OPENAI_AUTH_ISSUER
    from nora.infra.image_gen.xai_oauth import XAI_OAUTH_ISSUER
    from nora.media.wire import images
    from nora.obs.metrics import Metrics

    for name in ("XAI_OAUTH_REFRESH_TOKEN", "OPENAI_CODEX_REFRESH_TOKEN"):
        monkeypatch.setenv(name, "generated-refresh-token")
    baseline = build_config(SubjectSpec(
        database_url="sqlite://", workdir=tmp_path / "baseline",
        config=example_config_path,
    ))
    assert baseline.image_gen.openai_codex.issuer_url == OPENAI_AUTH_ISSUER
    assert baseline.image_gen.xai.issuer_url == XAI_OAUTH_ISSUER
    assert baseline.image_gen.state_dir == str(tmp_path / "baseline" / "state")
    state = tmp_path / "owned-oauth"
    cfg = build_config(SubjectSpec(
        database_url="sqlite://", workdir=tmp_path / "configured",
        config=example_config_path,
        overrides={
            "image_gen.enabled": True,
            "image_gen.state_dir": str(state),
            "image_gen.xai.issuer_url": "http://xai.generated.invalid",
            "image_gen.openai_codex.issuer_url": "http://codex.generated.invalid",
        },
    ))
    wiring = images(cfg, Metrics())
    assert wiring.image_generator is not None
    assert set(wiring.oauth_keepalive.lanes) == {"xai", "openai_codex"}
    lanes = {lane.lane: lane for lane, _ in wiring.oauth_keepalive._lanes}
    assert lanes["xai"]._discovery_url() == (
        "http://xai.generated.invalid/.well-known/openid-configuration")
    assert lanes["openai_codex"]._token_url == (
        "http://codex.generated.invalid/oauth/token")  # noqa: S105
    assert lanes["xai"]._state_file == Path(state / "xai-oauth.json")
    assert lanes["openai_codex"]._state_file == Path(state / "openai-codex-oauth.json")
