"""Functional settings and workspace boundaries survive subject acquisition."""

from __future__ import annotations

import tomllib

import tomlkit

from evals.subject_config import build, divergences
from nora.persons import load_persons
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


def test_workspace_isolation_preserves_separation_and_intentional_shared_units(
    tmp_path, monkeypatch,
):
    from nora.app.memory_routing import build_workspace_router
    from nora.policy import load_policy

    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    policy = 'default = "deny"\n'
    for unit, workspace in (("alpha", "first"), ("beta", "second"), ("gamma", "first")):
        policy += (
            f'[units.{unit}]\nworkspace = "{workspace}"\n'
            f'[[conversations]]\nplatform = "signal"\nid = "generated-{unit}"\n'
            f'label = "Generated {unit}"\npersons = "*"\nunit = "{unit}"\n'
        )
    identity = ('[workspaces.first]\nhoncho_workspace = "generated-a"\n'
                '[workspaces.second]\nhoncho_workspace = "generated-b"\n')
    cfg = build_config(SubjectSpec(database_url="sqlite://", workdir=tmp_path,
                                  room=("signal", "generated-alpha"),
                                  identity=identity, policy=policy))
    bindings = load_persons(tmp_path / "identity.toml").honcho_workspaces
    assert set(bindings) == {"first", "second"}
    assert bindings["first"] != bindings["second"]
    assert bindings == {"first": "generated-a", "second": "generated-b"}
    router = build_workspace_router(policy=load_policy(tmp_path / "policy.toml"),
                                    bindings=bindings, default_workspace=cfg.honcho.workspace_id)
    assert router.for_unit("alpha").workspace == "generated-a"
    assert router.for_unit("gamma").workspace == "generated-a"
    assert router.for_unit("beta").workspace == "generated-b"
    assert router.for_unit("unknown") is None
    assert (tmp_path / "policy.toml").read_text() == policy
