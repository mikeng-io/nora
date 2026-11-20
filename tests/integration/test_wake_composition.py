"""App startup recovers and starts its graph wake, and builds no legacy evaluator."""

import asyncio
import json
import tomllib
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
import tomli_w

from nora.__main__ import build_app
from nora.agent.factory import ModelSource
from nora.app import tick_grid
from nora.app.wake import WakeEvaluator
from nora.app.wake_poller import WakePoller
from nora.domain.models import NormalizedMessage
from nora.infra.sql.cognitive_graph_schema import CLAIM
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from tests.infra.test_graph_propose import claim_payload
from tests.test_main import _run_briefly
from tests.test_main import env as env
from tests.unit.test_dream_memory_budget import _ScriptedModel


@pytest.fixture
def wake_config(env):
    config = tomllib.loads(env.read_text())
    config["cognition"] = {"enabled": True}
    config["dreamer"]["enabled"] = True
    config["notes"] = {"enabled": True}
    config["dreamer"].setdefault("budgets", {})["cognition"] = {
        "turns": 200, "total_tokens": 10240000, "output_tokens": 120000,
        "timeout_seconds": 3600.0,
    }
    policy = env.parent / "policy.toml"
    policy.write_text(policy.read_text() + 'unit = "friends"\nhome_room = true\n')
    return config


async def test_app_recovers_and_starts_the_graph_poller_without_a_legacy_wake(
    env, monkeypatch, wake_config,
):
    env.write_text(tomli_w.dumps(wake_config))

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


@pytest.mark.parametrize("route_model", [None, "fixture-routed-model"])
async def test_wake_admitted_rows_record_the_resolved_model(
    env, monkeypatch, clean_pg_url, wake_config, route_model,
):
    wake_config["cognition"]["quiet_seconds"] = 0
    wake_config["dreamer"]["model"] = "fixture-dreamer-model"
    if route_model is not None:
        wake_config["model_routes"] = {"dreamer": {"model": route_model}}
    env.write_text(tomli_w.dumps(wake_config))
    policy = env.parent / "policy.toml"
    policy.write_text(policy.read_text().replace('persons = "*"', 'persons = ["miles"]'))
    expected_model = route_model or "fixture-dreamer-model"

    app = await build_app(env)
    engine = create_sql_engine(clean_pg_url)
    try:
        timestamp = datetime.now(UTC) - timedelta(seconds=1)
        await app.store.append(message=NormalizedMessage(
            "signal", "Z3JvdXA=", "group", "+852111", None, int(timestamp.timestamp() * 1000),
            "Generated wake evidence.", {},
        ), person_id="miles")
        [stimulus] = await SqlGraphStimuli(engine).read("friends")
        payload = claim_payload(citations=[stimulus.graph_id])
        script = _ScriptedModel(
            ["graph_propose", None], inputs=[json.dumps({"operations": [payload]})],
        )

        def scripted(source):
            assert source.model_id == expected_model
            return script

        monkeypatch.setattr(ModelSource, "create", scripted)
        poller = app.deps.wake_poller
        assert isinstance(poller, WakePoller)
        await poller.poll()
        await poller.drain()

        async with engine.connect() as conn:
            model = (await conn.execute(sa.select(CLAIM.c.model).where(
                CLAIM.c.unit == "friends", CLAIM.c.writer == "reflection",
            ))).scalar_one()
        uses_credential = model in app.cfg.redaction_values()
        assert not uses_credential, "A wake wrote a configured credential into model provenance"
        matches_model = model == expected_model
        assert matches_model, "Wake provenance must match the model used by Dreamer"
    finally:
        await engine.dispose()
        await app.shutdown()
