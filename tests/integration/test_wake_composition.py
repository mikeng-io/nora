"""App startup recovers and starts its graph wake, and builds no legacy evaluator."""

import asyncio
import tomllib

import tomli_w

from nora.__main__ import build_app
from nora.app import tick_grid
from nora.app.wake import WakeEvaluator
from nora.app.wake_poller import WakePoller
from tests.test_main import _run_briefly
from tests.test_main import env as env


async def test_app_recovers_and_starts_the_graph_poller_without_a_legacy_wake(env, monkeypatch):
    config = tomllib.loads(env.read_text())
    config["cognition"] = {"enabled": True}
    config["dreamer"]["enabled"] = True
    config["notes"] = {"enabled": True}
    config["dreamer"].setdefault("budgets", {})["cognition"] = {
        "turns": 200, "total_tokens": 10240000, "output_tokens": 120000,
        "timeout_seconds": 3600.0,
    }
    env.write_text(tomli_w.dumps(config))
    policy = env.parent / "policy.toml"
    policy.write_text(policy.read_text() + 'unit = "friends"\nhome_room = true\n')

    legacy_calls: list[str] = []
    lifecycle: list[tuple[str, WakePoller]] = []
    recovered = WakePoller.recover

    def legacy(*args, **kwargs):
        legacy_calls.append("legacy wake")
        raise AssertionError("App composed or spawned a legacy wake")

    async def recover(poller):
        await recovered(poller)
        lifecycle.append(("recovered", poller))

    async def run(poller):
        lifecycle.append(("started", poller))
        await asyncio.Event().wait()

    monkeypatch.setattr(WakeEvaluator, "__init__", legacy)
    monkeypatch.setattr(tick_grid, "wake_loop", legacy)
    monkeypatch.setattr(WakePoller, "recover", recover)
    monkeypatch.setattr(WakePoller, "run", run)

    app = await build_app(env)
    poller = app.deps.wake_poller
    assert isinstance(poller, WakePoller)
    assert app.deps.cognition_runner is not None
    assert lifecycle == []
    try:
        await _run_briefly(app, until=lambda: ("started", poller) in lifecycle)
    finally:
        await app.shutdown()
    assert lifecycle == [("recovered", poller), ("started", poller)]
    assert legacy_calls == []
