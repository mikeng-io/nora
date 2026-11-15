"""Generated startup and teardown control for the isolated production app."""

import asyncio
import socket

import pytest

from nora.__main__ import App
from nora.subject.environment import IsolatedServices
from nora.subject.obtain import SubjectSpec, build_config, default_policy, obtain
from tests.integration.subject.test_recorded_wake_runtime import services


@pytest.mark.asyncio
async def test_isolated_app_cancellation_closes_listener(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    policy = default_policy(("signal", "g1"), unit="generated-unit") + "home_room = true\n"
    overrides = {"platforms.signal.mode": "bridge", "platforms.signal.enabled": True,
                 "cognition.enabled": True, "obs.metrics.port": port}
    spec = SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / "subject",
                       policy=policy, overrides=overrides)
    cfg = build_config(spec)
    requests = []
    async with IsolatedServices(database_url=clean_pg_url,
                                applications=services(cfg, requests), redis=True) as env:
        from dataclasses import replace

        subject = await obtain(replace(spec, environment=env))
        app = App(cfg=subject.cfg, deps=subject.deps)
        task = asyncio.create_task(app.run())
        try:
            async with asyncio.timeout(15):
                while app._health_runner is None:
                    if task.done():
                        await task
                        pytest.fail("app stopped before listening")
                    await asyncio.sleep(0.01)
            assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await app.shutdown()
        assert not requests
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))
    assert not env.active
