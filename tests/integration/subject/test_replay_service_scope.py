"""Replay scope and retained SQL use real subjects and owned HTTP, without inference."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from evals.cognition_loop import run as module
from evals.cognition_loop.verdicts import Rollup
from nora.domain.models import NormalizedMessage
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.engine import create_sql_engine
from nora.subject import close_private_blank_templates
from nora.subject.obtain import SubjectSpec, build_config, default_policy
from nora.subject.recorded_services import RecordedServices

NOW = 2_000_000_000_000


def recording(cfg, wakes):
    bindings = {"llm.base_url": "model", "platforms.signal.base_url": "bridge"}
    bindings.update({f"providers.{name}.base_url": "model" for name in cfg.providers})
    return RecordedServices({"version": 1, "bindings": bindings, "services": {
        "model": [], "bridge": [{"method": "GET", "path": "/v1/self",
            "response": {"status": 200, "json": {"primary": "generated-self", "aliases": []}}}
            for _ in range(wakes)],
    }})


@pytest.mark.parametrize("failure", [None, "advance", "wake", "cancel"])
async def test_replay_owns_one_scope_and_retains_candidate_sql(
    clean_pg_url, evaluation_config, tmp_path, monkeypatch, failure,
):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    policy = default_policy(("signal", "g1"), unit="generated-unit")
    policy_path, identity_path = tmp_path / "policy.toml", tmp_path / "identity.toml"
    identity = ('[persons.miles_novak]\nname="Miles"\n'
                '[[persons.miles_novak.signal]]\nphone="+852111"\n')
    policy_path.write_text(policy)
    identity_path.write_text(identity)
    overrides = {"platforms.signal.mode": "bridge", "platforms.signal.enabled": True}
    cfg = build_config(SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / "declared",
                                  overrides=overrides, policy=policy, identity=identity))
    args = SimpleNamespace(database_url=clean_pg_url, policy=policy_path, identity=identity_path,
                           overrides=overrides)
    case = SimpleNamespace(name="generated", unit="generated-unit", wakes=(11, 22))
    subjects, histories, environments = [], [], []
    entered = asyncio.Event()

    async def wake(args, case, index, wake_id, at_ms, url, reason, judges, evidence, output,
                   room, **kwargs):
        subject = await module._compose(args, url, at_ms, room, policy, identity)
        subjects.append(subject)
        environments.append(subject.environment)
        engine = create_sql_engine(url)
        try:
            store = SqlRecordStore(engine)
            history = await store.recent(conversation_id="g1", limit=10)
            histories.append([row.body for row in history])
            if failure == "cancel":
                entered.set()
                await asyncio.Event().wait()
            if failure == "wake" and index == 1:
                return module._wake_error(output, index, wake_id, at_ms, reason, None,
                                          step="wake", message="generated failed wake",
                                          unit=case.unit)
            await store.append(message=NormalizedMessage(
                "signal", "g1", "group", "+852111", "Miles", at_ms,
                f"Generated candidate record {index}", {}), person_id="miles_novak")
            return {"status": "PASS", "expectations": []}
        finally:
            await engine.dispose()
            await subject.aclose()

    monkeypatch.setattr(module, "_run_wake", wake)
    if failure == "advance":
        async def unavailable(*args, **kwargs):
            raise RuntimeError("generated advance failure")
        monkeypatch.setattr(module, "advance_record", unavailable)
    count = 1 if failure in {"advance", "cancel"} else 2
    roll, rows = Rollup(), []
    pending = asyncio.create_task(module._wakes(
        args, case, {0: NOW, 1: NOW + 1000}, {0: "time", 1: "time"},
        {i: {"stimulus_ids": [], "self_ids": [], "had_session": False} for i in range(2)},
        None, SimpleNamespace(case=lambda *a, **k: contextlib.nullcontext()), tmp_path,
        ("signal", "g1"), recording(cfg, count), roll, rows))
    signal = asyncio.create_task(entered.wait()) if failure == "cancel" else None
    try:
        if signal is not None:
            done, _ = await asyncio.wait({pending, signal}, timeout=15,
                                         return_when=asyncio.FIRST_COMPLETED)
            assert signal in done and signal.result(), (
                roll.errors, rows, pending.exception() if pending.done() else "not settled")
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            try:
                await pending
            except Exception as exc:
                raise AssertionError((str(exc), roll.errors, rows)) from exc
        assert len(subjects) == count
        assert environments and all(env is environments[0] for env in environments)
        assert all(not env.active for env in environments)
        assert all(not subject.deps.background._tasks for subject in subjects)
        assert args.environment is None
        assert histories[0] == []
        if count == 2:
            assert histories[1] == ["Generated candidate record 0"]
        if failure in {"advance", "wake"}:
            assert roll.verdict == "ERROR"
            assert rows[-1]["status"] == "ERROR"
            assert rows[-1]["step"] == failure
            assert (tmp_path / "wake-1-22.json").is_file()
        elif failure is None:
            assert roll.verdict == "PASS" and len(rows) == 2
    finally:
        if not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending
        if signal is not None and not signal.done():
            signal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await signal
        await close_private_blank_templates()


async def test_missing_replay_recording_stops_before_source_reads(tmp_path, monkeypatch):
    from evals.cognition_loop.cases import generated_case

    calls = []
    case = generated_case()

    async def recorded(source, wake_id):
        calls.append(wake_id)
        return NOW + wake_id, SimpleNamespace(unit=case.unit, pending=())

    async def reason(*args):
        return "time"

    async def session(*args):
        return False

    async def wakes(*args):
        calls.append("dispatch")

    monkeypatch.setattr(module, "recorded_wake", recorded)
    monkeypatch.setattr(module, "_wake_reason", reason)
    monkeypatch.setattr(module, "_recorded_session", session)
    monkeypatch.setattr(module, "_wakes", wakes)
    policy = SimpleNamespace(rooms=lambda: [SimpleNamespace(
        unit=case.unit, platform="signal", primary_id="g1")])
    result = await module.run_case(SimpleNamespace(), case, None, None, policy, None, tmp_path)
    assert calls == []
    assert result["status"] == "ERROR"
    assert result["errors"] == ["external_responses_missing: " + case.name]
    assert result["behavior"] == "UNMEASURED"
