"""Subject room authority comes from the production graph and real bridge responses."""

from __future__ import annotations

import importlib
from dataclasses import replace

import pytest
from aiohttp import web

from nora.policy import load_policy
from nora.subject import SubjectSpec, obtain
from nora.subject.environment import IsolatedServices
from nora.subject.obtain import RoomNotInPolicyError, build_config
from tests.integration.test_the_subject_is_the_deployed_graph import (
    _bridge_app,
    _empty_honcho_app,
    _model_app,
)

GUILD = "100000000000000001"
CHANNEL = "100000000000000002"


@pytest.mark.parametrize("available", [True, False])
async def test_subject_uses_composed_policy_with_real_guild_directory(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, available,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    policy = ('default = "deny"\n[[conversations]]\nplatform = "discord"\n'
              f'guild = "{GUILD}"\nlabel = "Generated guild"\npersons = "*"\n'
              'unit = "generated-unit"\n')
    spec = SubjectSpec(database_url=clean_pg_url, workdir=tmp_path,
                       room=("discord", f"{GUILD}/{CHANNEL}"), policy=policy, overrides={
                           "platforms.signal.enabled": True,
                           "platforms.signal.mode": "bridge",
                           "platforms.discord.enabled": True,
                           "platforms.discord.mode": "bridge"})
    cfg = build_config(spec)
    # A standalone policy has no observed directory and cannot authorize the room.
    assert load_policy(tmp_path / "policy.toml").conversation(*spec.room) is None
    requests, reads = [], []
    bridge = _bridge_app([])

    async def directory(request):
        body = await request.json()
        reads.append(body)
        if body != {"op": "channels", "params": {"guild_id": GUILD}}:
            return web.json_response({"available": False}, status=400)
        return web.json_response({"available": available, "value": {
            "channels": {CHANNEL: "generated-channel"}, "threads": {}}})

    bridge.router.add_post("/v1/reads", directory)
    apps = {"platforms.discord.base_url": bridge, "platforms.signal.base_url": bridge,
            "honcho.base_url": _empty_honcho_app(),
            "llm.base_url": _model_app(requests)}
    apps.update({f"providers.{name}.base_url": _model_app(requests) for name in cfg.providers})
    module = importlib.import_module("nora.subject.obtain")
    compose = module.build_dependencies
    owned = []

    async def observe(*args, **kwargs):
        deps = await compose(*args, **kwargs)
        control = deps.discord_control
        session = control._session
        assert session is not None and not session.closed
        owned.append((deps, session, session.connector))
        return deps

    monkeypatch.setattr(module, "build_dependencies", observe)
    environment = IsolatedServices(database_url=clean_pg_url, applications=apps, redis=True)
    async with environment:
        if available:
            subject = await obtain(replace(spec, environment=environment))
            actual = subject.deps.config_reload.policy.conversation(*spec.room)
            assert actual is not None
            assert (subject.unit, subject.culture_key) == (actual.unit, actual.primary_id)
            assert subject.unit == "generated-unit"
            assert subject.deps.config_reload.policy.rooms() == (actual,)
            await subject.aclose()
            assert not subject.deps.background._tasks
        else:
            with pytest.raises(RoomNotInPolicyError, match="names no conversation"):
                await obtain(replace(spec, environment=environment))
        assert reads == [{"op": "channels", "params": {"guild_id": GUILD}}]
        assert requests == []
        assert owned and all(not deps.background._tasks for deps, _, _ in owned)
        assert all(session.closed and connector.closed for _, session, connector in owned)
        environment.check()
    assert not environment.active
