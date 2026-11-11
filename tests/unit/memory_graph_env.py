"""Shared generated-replay environment for the memory-graph spike tests."""

from __future__ import annotations

import contextlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import evals.backbone
import nora.subject
from nora.infra.sql import cognition_replay
from scripts import memory_graph_spike as spike


def _sql_fakes(source_url: str, calls: list):
    """The projected source engine and its canned row sets."""
    class FakeEngine:
        def __init__(self, url):
            self.url = url

        async def dispose(self):
            assert logging.root.manager.disable == logging.CRITICAL

    def create(url):
        assert logging.root.manager.disable == logging.CRITICAL
        return FakeEngine(url)

    async def generated_rows(engine, sql, params=None):
        if "FROM alembic_version" in sql:
            return [{"version_num": "generated-revision"}]
        calls.append((engine.url, sql))
        if "FROM cognition_wake" in sql:
            return [{"id": 1, "at_ms": 1}, {"id": 2, "at_ms": 2}]
        if "SELECT body FROM messages" in sql:
            body = "[image generated]" if params["id"] == 2 else "generated text"
            return [{"body": body}]
        if "is_nora" in sql:
            return [{"n": 0}]
        if "string_agg" in sql:
            return [{"digest": "generated-" + engine.url}]
        if "FROM tag_trace" in sql:
            return [{"messages": []}]
        if "FROM relation" in sql:
            # The projected source holds the relation population; the restored
            # sandbox holds none at the wake instant.
            return [
                {
                    "unit": "generated-unit",
                    "persons": ["generated-person"],
                    "src_kind": "event",
                    "src_id": "generated-edge",
                    "dst_kind": "topic",
                    "dst_id": "generated-topic",
                    "rel": "concerns",
                }
            ] if engine.url == source_url else []
        raise AssertionError(f"unexpected SQL on {engine.url}: {sql}")

    return create, generated_rows


def _session_fakes(sandbox_url: str):
    """Wake, sandbox, metrics, replay and obtain stand-ins for one session."""
    async def fake_wake(engine, wake_id):
        return (
            1_700_000_000_000 + wake_id,
            SimpleNamespace(
                unit="generated-unit",
                pending=[
                    SimpleNamespace(kind="message", ref=f"messages:{wake_id}")
                ],
            ),
        )

    @contextlib.asynccontextmanager
    async def fake_sandbox(database_url, at):
        yield sandbox_url, SimpleNamespace()

    class FakeMetrics:
        def __init__(self):
            self.values = iter([0, 7, 0])

        def get(self, name):
            return next(self.values, 0)

    async def replay(**kwargs):
        from nora.subject.eval_evidence import current
        evidence = current()
        evidence.record("event", name="nora.session.started", attributes={"body": "{}"})
        evidence.record("event", name="nora.model.request", attributes={
            "nora.model.request.body": json.dumps({"messages": [
                {"role": "system", "content": "generated"},
                {"role": "user", "content": "generated"}]})})
        evidence.record("transport_started")
        evidence.record("transport_finished", reasoning_availability="absent")
        evidence.record("event", name="nora.session.terminal", attributes={"body": "{}"})
        return SimpleNamespace(session_id="generated-session", stop_reason="end_turn")

    runner = SimpleNamespace(
        inspect=AsyncMock(return_value={"system": "generated", "agenda": "generated"}),
        replay=AsyncMock(side_effect=replay),
    )

    async def fake_obtain(spec):
        return SimpleNamespace(
            database_url=spec.database_url,
            cfg=SimpleNamespace(),
            deps=SimpleNamespace(cognition_runner=runner, metrics=FakeMetrics()),
        )

    return fake_wake, fake_sandbox, fake_obtain, runner


def _patch_spike(monkeypatch, cfg, create, generated_rows, spy, quality,
                 fake_wake, fake_sandbox, fake_obtain):
    """Point the spike's seams at the generated fakes."""
    monkeypatch.setattr(spike, "age_comparison", spy)
    monkeypatch.setattr(spike, "session_quality", quality)
    monkeypatch.setattr(spike, "smoke_fact_support", AsyncMock(return_value={}))
    monkeypatch.setattr(spike, "index_replay_sandbox", AsyncMock(
        return_value=SimpleNamespace(line=lambda: "generated-index")))
    monkeypatch.setattr(spike, "sql_answer", AsyncMock(return_value=["event:generated-edge"]))
    monkeypatch.setattr(spike, "model_endpoint",
                        lambda path, purpose, model="": ("generated-url", "", model))
    monkeypatch.setattr(spike, "load", lambda path: cfg)
    monkeypatch.setattr(spike, "credentials", lambda: {
        "GENERATED_DB": "postgresql+asyncpg://127.0.0.1:9/generated-production"})
    monkeypatch.setattr(spike, "create_sql_engine", create)
    monkeypatch.setattr(spike, "rows", generated_rows)
    monkeypatch.setattr(spike, "load_policy", lambda path: SimpleNamespace(
        rooms=lambda: [SimpleNamespace(
            unit="generated-unit", platform="signal", primary_id="generated-room")]))
    monkeypatch.setattr(spike, "load_persons", lambda path: SimpleNamespace(by_person={}))
    monkeypatch.setattr(spike, "projection_context", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(cognition_replay, "recorded_wake", fake_wake)
    monkeypatch.setattr(nora.subject, "sandbox_as_of", fake_sandbox)
    monkeypatch.setattr(nora.subject, "obtain", fake_obtain)
    monkeypatch.setattr(evals.backbone, "_close", AsyncMock())



def generated_replay_environment(tmp_path, monkeypatch):
    """The engine comparison binds the frozen projected source once per run;
    the as-of sandbox — correctly holding no graph rows at the wake instant —
    serves the session replay and is never asked for the graph population."""
    source_url = "postgresql+asyncpg://127.0.0.1:9/generated-source"
    sandbox_url = "postgresql+asyncpg://127.0.0.1:9/generated-sandbox"
    calls = []
    create, generated_rows = _sql_fakes(source_url, calls)
    fake_wake, fake_sandbox, fake_obtain, runner = _session_fakes(sandbox_url)
    cfg = SimpleNamespace(
        store=SimpleNamespace(database_url_env="GENERATED_DB"),
        llm=SimpleNamespace(
            model="generated-session", timeout_seconds=1, model_dump=dict
        ),
        providers={},
        model_routes={},
        cognition=SimpleNamespace(model_dump=dict),
    )
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text("generated")
    identity_file = tmp_path / "identity.toml"
    identity_file.write_text("generated")
    bound = []
    real_age = spike.age_comparison

    async def spy(engine, age_url, judges, output_dir, minimum):
        from opentelemetry import trace
        assert trace.get_current_span().name == "eval.case"
        bound.append(engine.url)
        return await real_age(engine, age_url, judges, output_dir, minimum)

    quality = AsyncMock(
        return_value={"verdicts": {}, "population": {"committed": 0, "missing": 0}}
    )
    _patch_spike(monkeypatch, cfg, create, generated_rows, spy, quality,
                 fake_wake, fake_sandbox, fake_obtain)
    args = SimpleNamespace(
        config=tmp_path / "config.toml",
        policy=policy_file,
        identity=identity_file,
        database_url=source_url,
        snapshot=True,
        age_url=None,
        judge_a="generated-judge-a",
        judge_b="generated-judge-b",
        output=tmp_path / "out",
        per_stratum=1,
        minimum=1,
        seed=17,
        projection_rate=100,
        project_only=False,
        skip_projection=True,
        smoke=False,
        stop_after_tokens=100,
    )

    return SimpleNamespace(args=args, runner=runner, bound=bound, calls=calls,
                           quality=quality, source_url=source_url, cfg=cfg)


