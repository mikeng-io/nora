"""Closed-lifecycle wake census controls for the autonomous journey.

Each control drives the REAL `autonomous_journey` entry — the real step
collector, the real `wait_closed_wakes`/`rows` reads, the post-shutdown
census, the denominator binding and the retained `report.json` — over
REAL disposable-SQL wake rows. The production wakes are generated rows
written at an arranged instant through the driver's own seams (the
`produce` call before a wait, the judges' first dispatch inside
`_finalize`, the `stop` hook inside the owned teardown): a deterministic
arrangement of timing, never an autonomous model decision. A wake SQL
recorded between the last planned wait and the end of the owned App
lifecycle must invalidate the run, never report a complete census.

The driver runs `prepare_effect_reads`, which creates eval indexes —
DDL — so every case here owns a dedicated database.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa

import evals.rebuilt.autonomous_journey as journey_mod
from evals.cognition_loop.judges import JUDGE_INPUT_MAX_BYTES
from evals.rebuilt.autonomous_journey import autonomous_journey
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import (
    COGNITION_SESSION,
    COGNITION_WAKE,
    COGNITION_WAKE_OUTCOME,
)
from nora.policy import load_policy
from nora.subject.effects import prepare_effect_reads
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import read_events
from nora.subject.journey import JourneyDriver
from nora.subject.obtain import SubjectSpec, build_config, default_policy
from tests.integration.subject.test_autonomous_finalize_controls import (
    _session_events,
)
from tests.integration.subject.test_sdk_recorded_caller import (
    sdk_evidence as sdk_evidence,
)

pytestmark = pytest.mark.fresh_database

UNIT = "generated-unit"
ROOM = ("signal", "g1")
MODELS = ("generated-a", "generated-b")

PLAN = {
    "steps": [{"send": "generated hello"}, {"until_wakes": 1},
              {"send": "generated correction"}, {"until_wakes": 2}],
    "expected_wakes": 2,
    "expectations": [
        {"wake": 0, "polarity": "must", "kind": "note",
         "text": "a generated claim"},
        {"wake": 1, "polarity": "must", "kind": "note",
         "text": "a generated correction"},
    ],
    "judges": list(MODELS),
}

PLAN_ONE = {
    "steps": [{"send": "generated hello"}, {"until_wakes": 1}],
    "expected_wakes": 1,
    "expectations": [{"wake": 0, "polarity": "must", "kind": "note",
                      "text": "a generated claim"}],
    "judges": list(MODELS),
}


class _Driver(JourneyDriver):
    """Real SQL waits and reads over generated rows; the App itself never
    starts. What is under test is the collector and the census — the
    closed App lifecycle is the boundary the census reads after, and a
    `stop` that only writes the arranged late row and disposes the engine
    is that boundary deterministically."""

    def __init__(self, subject: Any, *, produce: Any = None,
                 on_stop: Any = None) -> None:
        super().__init__(subject)
        self._produce = produce
        self._on_stop = on_stop

    async def start(self) -> None:
        await prepare_effect_reads(self.engine, self.subject.environment)

    async def stop(self) -> None:
        try:
            if self._on_stop is not None:
                await self._on_stop(self.engine)
        finally:
            await self.engine.dispose()

    async def wait_closed_wakes(self, count: int, *,
                                seconds: float) -> list[Any]:
        if self._produce is not None:
            await self._produce(self.engine, count)
        return await super().wait_closed_wakes(count, seconds=seconds)

    async def ingress(self, body: str, *, event_id: str,
                      tagged: bool = False) -> Any:
        """The send's own settled admission: `done`, declined engagement
        — the generated turn records rather than fabricating a reply."""
        incoming = SimpleNamespace(body=body, event_id=event_id,
                                   sent_ms=1234)
        event = {"id": event_id, "state": "done",
                 "admission": {"engage": False, "reason": "generated",
                               "relevance_gate": None}}
        work = [{"id": 1, "kind": "ingest", "state": "done"}]
        return incoming, event, work


class _Judges:
    """A judge seam that counts dispatches; an optional hook fires at the
    first call — inside `_finalize`, between the last wait and shutdown,
    exactly the window the census owns."""

    input_budget = JUDGE_INPUT_MAX_BYTES

    def __init__(self, calls: list[str], hook: Any = None) -> None:
        self.calls = calls
        self.served: dict[str, list[dict[str, Any]]] = {}
        self._hook = hook

    async def call(self, model: str, instruction: Any, payload: Any) -> str:
        self.calls.append(model)
        if self._hook is not None:
            hook, self._hook = self._hook, None
            await hook()
        self.served.setdefault(model, []).append(
            {"x-omniroute-model": f"served-{model}"})
        return json.dumps({"verdict": "met", "reason": "generated",
                           "entry_ids": []})


async def _settled_rows(engine: Any, *, wake_id: int, session_id: Any,
                        unit: str = UNIT, outcome: str = "closed") -> None:
    """A real closed wake's generated rows: wake, session, outcome."""
    async with engine.begin() as conn:
        await conn.execute(sa.insert(COGNITION_WAKE).values(
            id=wake_id, unit=unit, mode="live", reason="strong_cue",
            from_stimulus_id=0, through_stimulus_id=0, stimulus_count=0,
            eligible_count=0, at_ms=1200 + wake_id, tracked=1))
        if session_id is not None:
            await conn.execute(sa.insert(COGNITION_SESSION).values(
                id=session_id, unit=unit, mode="live", wake_id=wake_id,
                started_at_ms=1200 + wake_id, from_stimulus_id=0,
                through_stimulus_id=0, situation={}, agenda={}))
        await conn.execute(sa.insert(COGNITION_WAKE_OUTCOME).values(
            wake_id=wake_id, unit=unit, session_id=session_id,
            outcome=outcome, at_ms=1300 + wake_id))


def _record_window(evidence: Any, session_id: Any) -> None:
    """One complete canonical session window via the real `record` seam."""
    dropped = {"kind", "order", "event_id", "time_ns", "run_id", "case_id",
               "trace_id", "span_id"}
    for event in _session_events(session_id):
        evidence.record(event["kind"], **{
            key: value for key, value in event.items() if key not in dropped})


@contextlib.asynccontextmanager
async def _world(clean_pg_url: str, tmp_path: Path, monkeypatch):
    """The minimal real subject the journey entry reads; no App composition."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    policy_text = default_policy(ROOM, unit=UNIT)
    async with IsolatedServices(database_url=clean_pg_url,
                                applications={}) as env:
        spec = SubjectSpec(
            database_url=clean_pg_url, workdir=tmp_path / "subject",
            policy=policy_text, room=ROOM, environment=env,
            overrides={"cognition.enabled": True})
        cfg = build_config(spec)
        env.world = SimpleNamespace(
            wire_actions=lambda *args, **kwargs: None,
            judge=SimpleNamespace(requests=[]), requests=[])
        yield SimpleNamespace(
            cfg=cfg, unit=UNIT, database_url=clean_pg_url, room=ROOM,
            environment=env,
            deps=SimpleNamespace(
                bridge_runtime=SimpleNamespace(),
                metrics=SimpleNamespace(get=lambda *args, **kwargs: None),
                config_reload=SimpleNamespace(
                    policy=load_policy(spec.workdir / "policy.toml")),
                persons=SimpleNamespace(by_person={})))


def _case(plan: dict[str, Any]) -> Any:
    return SimpleNamespace(name="generated-wake-census",
                           metadata={"journey": dict(plan)})


def _patch(monkeypatch, *, calls: list[str], produce: Any = None,
           on_stop: Any = None, hook: Any = None) -> None:
    """Bind the census driver and counting judges onto the entry's names."""
    monkeypatch.setattr(
        journey_mod, "JourneyDriver",
        lambda subject: _Driver(subject, produce=produce, on_stop=on_stop))
    judges = _Judges(calls, hook)
    monkeypatch.setattr(journey_mod, "Judges", lambda *a, **k: judges)


def _report(sdk_evidence: Any, case: Any) -> dict[str, Any]:
    return json.loads((sdk_evidence.path / f"journey-{case.name}"
                       / "report.json").read_text())


def _census_record(sdk_evidence: Any) -> dict[str, Any]:
    records = [event for event in read_events(sdk_evidence.path)
               if event.get("kind") == "journey_wake_census"]
    assert records, "the census never recorded its population"
    return records[-1]


async def test_census_exact_population_passes(clean_pg_url, tmp_path,
                                              monkeypatch, evaluation_config,
                                              sdk_evidence, capsys):
    """Positive control: the census records exactly the produced
    population and the retained report reconciles — ids, slots and
    receipts all agree, judges ran for both wakes."""
    produced = iter([(1, 7), (2, 8)])

    async def produce(engine, wanted):
        wake_id, session_id = next(produced)
        await _settled_rows(engine, wake_id=wake_id, session_id=session_id)
        _record_window(sdk_evidence, session_id)

    calls: list[str] = []
    _patch(monkeypatch, calls=calls, produce=produce)
    async with _world(clean_pg_url, tmp_path, monkeypatch) as subject:
        outcome = await autonomous_journey(subject, _case(PLAN), now_ms=0)
    assert outcome.error is None
    assert sorted(calls) == sorted([*MODELS, *MODELS])
    retained = _report(sdk_evidence, _case(PLAN))
    assert retained["status"] == "PASS"
    assert retained["coverage"]["status"] == "PASS", \
        retained["coverage"]["errors"]
    prefix = "  journey accounting: "
    lines = [line.removeprefix(prefix) for line in capsys.readouterr().out.splitlines()
             if line.startswith(prefix)]
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "coverage": retained["coverage"], "outcome": retained["outcome"],
    }
    assert retained["expected_cases"][0]["wakes"] == [1, 2]
    assert retained["run"]["wake_census"] == {
        "observed": [1, 2], "declared": 2}
    receipts = retained["receipts"]
    assert [row["id"] for row in receipts["wakes"]] == [1, 2]
    assert all(row["outcome"] == "closed" for row in receipts["outcomes"])
    record = _census_record(sdk_evidence)
    assert record["observed"] == [1, 2] == record["reported"]
    sdk_evidence.record("run_finished")


async def test_extra_wake_at_the_wait_fails_the_census(
        clean_pg_url, tmp_path, monkeypatch, evaluation_config, sdk_evidence):
    """A second settled wake already present when the plan asks for one:
    the wait itself names the disagreement, ZERO judge requests are made,
    and the census keeps both real ids as unaccounted evidence — the run
    and its coverage cannot PASS."""
    async def produce(engine, wanted):
        await _settled_rows(engine, wake_id=1, session_id=7)
        await _settled_rows(engine, wake_id=2, session_id=8)

    calls: list[str] = []
    _patch(monkeypatch, calls=calls, produce=produce)
    async with _world(clean_pg_url, tmp_path, monkeypatch) as subject:
        outcome = await autonomous_journey(subject, _case(PLAN_ONE),
                                           now_ms=0)
    assert outcome.error is not None
    assert calls == []
    retained = _report(sdk_evidence, _case(PLAN_ONE))
    assert retained["status"] == "ERROR"
    assert retained["coverage"]["status"] == "ERROR"
    assert retained["run"]["wake_census"] == {
        "observed": [1, 2], "declared": 1}
    errors = json.dumps(retained["errors"])
    assert "expected exactly one new wake" in errors
    assert "absent from the report: [1, 2]" in errors
    assert "beyond the 1 declared slots: [2]" in errors
    wakes = retained["cases"][0]["wakes"]
    assert [row["recorded_wake_id"] for row in wakes] == [1, 2]
    assert all(row["step"] == "census" and row["expectations"] == []
               for row in wakes)
    record = _census_record(sdk_evidence)
    assert record["observed"] == [1, 2] and record["reported"] == []
    sdk_evidence.record("run_finished")


async def test_extra_wake_during_finalization_fails_the_census(
        clean_pg_url, tmp_path, monkeypatch, evaluation_config, sdk_evidence):
    """A wake SQL writes while the first wake's judges run — after the
    last wait, before shutdown: the judged wake keeps its verdict, the
    extra stays ungraded as a census row, and the run cannot PASS."""

    async def produce(engine, wanted):
        await _settled_rows(engine, wake_id=1, session_id=7)
        _record_window(sdk_evidence, 7)

    async def extra() -> None:
        engine = create_sql_engine(clean_pg_url)
        try:
            await _settled_rows(engine, wake_id=2, session_id=None)
        finally:
            await engine.dispose()

    calls: list[str] = []
    _patch(monkeypatch, calls=calls, produce=produce, hook=extra)
    async with _world(clean_pg_url, tmp_path, monkeypatch) as subject:
        outcome = await autonomous_journey(subject, _case(PLAN_ONE),
                                           now_ms=0)
    assert outcome.error is not None
    assert sorted(calls) == sorted(MODELS)
    retained = _report(sdk_evidence, _case(PLAN_ONE))
    assert retained["status"] == "ERROR"
    assert retained["coverage"]["status"] == "ERROR"
    assert retained["run"]["wake_census"] == {
        "observed": [1, 2], "declared": 1}
    errors = json.dumps(retained["errors"])
    assert "absent from the report: [2]" in errors
    assert "beyond the 1 declared slots: [2]" in errors
    wakes = retained["cases"][0]["wakes"]
    assert [row["recorded_wake_id"] for row in wakes] == [1, 2]
    assert wakes[0]["status"] == "PASS"
    assert wakes[1]["step"] == "census" and wakes[1]["expectations"] == []
    assert json.loads(
        (sdk_evidence.path / "journey-generated-wake-census"
         / "wake-1-2.json").read_text())["error"]["step"] == "census"
    record = _census_record(sdk_evidence)
    assert record["observed"] == [1, 2] and record["reported"] == [1]
    sdk_evidence.record("run_finished")


async def test_late_unsettled_wake_at_shutdown_fails_the_census(
        clean_pg_url, tmp_path, monkeypatch, evaluation_config, sdk_evidence):
    """A wake row outstanding at the end of the owned App lifecycle —
    written during `stop`, never closed: the census names it unsettled
    and unaccounted, and the run cannot PASS."""

    async def produce(engine, wanted):
        await _settled_rows(engine, wake_id=1, session_id=7)
        _record_window(sdk_evidence, 7)

    async def on_stop(engine) -> None:
        async with engine.begin() as conn:
            await conn.execute(sa.insert(COGNITION_WAKE).values(
                id=2, unit=UNIT, mode="live", reason="strong_cue",
                from_stimulus_id=0, through_stimulus_id=0,
                stimulus_count=0, eligible_count=0, at_ms=1400, tracked=1))

    calls: list[str] = []
    _patch(monkeypatch, calls=calls, produce=produce, on_stop=on_stop)
    async with _world(clean_pg_url, tmp_path, monkeypatch) as subject:
        outcome = await autonomous_journey(subject, _case(PLAN_ONE),
                                           now_ms=0)
    assert outcome.error is not None
    assert sorted(calls) == sorted(MODELS)
    retained = _report(sdk_evidence, _case(PLAN_ONE))
    assert retained["status"] == "ERROR"
    assert retained["coverage"]["status"] == "ERROR"
    assert retained["run"]["wake_census"] == {
        "observed": [1, 2], "declared": 1}
    errors = json.dumps(retained["errors"])
    assert "never closed: [2]" in errors
    assert "absent from the report: [2]" in errors
    assert "beyond the 1 declared slots: [2]" in errors
    record = _census_record(sdk_evidence)
    assert record["observed"] == [1, 2] and record["reported"] == [1]
    sdk_evidence.record("run_finished")


async def test_foreign_wake_at_shutdown_is_named_not_counted(
        clean_pg_url, tmp_path, monkeypatch, evaluation_config, sdk_evidence):
    """A wake in another unit found at the census: named as foreign
    evidence, never bound to this unit's declared slots — the run cannot
    PASS while the in-scope population itself still reconciles."""

    async def produce(engine, wanted):
        await _settled_rows(engine, wake_id=1, session_id=7)
        _record_window(sdk_evidence, 7)

    async def on_stop(engine) -> None:
        await _settled_rows(engine, wake_id=9, session_id=None,
                            unit="foreign-unit")

    calls: list[str] = []
    _patch(monkeypatch, calls=calls, produce=produce, on_stop=on_stop)
    async with _world(clean_pg_url, tmp_path, monkeypatch) as subject:
        outcome = await autonomous_journey(subject, _case(PLAN_ONE),
                                           now_ms=0)
    assert outcome.error is not None
    retained = _report(sdk_evidence, _case(PLAN_ONE))
    assert retained["status"] == "ERROR"
    assert retained["run"]["wake_census"] == {
        "observed": [1], "declared": 1}
    assert retained["expected_cases"][0]["wakes"] == [1]
    errors = json.dumps(retained["errors"])
    assert "foreign unit" in errors
    record = _census_record(sdk_evidence)
    assert record["observed"] == [1] and record["reported"] == [1]
    sdk_evidence.record("run_finished")
