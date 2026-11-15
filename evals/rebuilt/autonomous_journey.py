"""The autonomous-live-journey entry: one case through the composed `App`.

Every other entry point drives one production seam at a stated instant.
This one runs the deployment's own loop end to end: generated input enters
through real bridge ingress and admission, the app's own scheduling timers
wake Cognition (never a forced wake, never `wake_hook`), the fire path and
durable actions deliver, and a later correction rides the same ingress.
Per wake the same canonical finalization the recorded-wake runner uses —
session events, effects inside the case's execution boundary, state diff —
and both judges read the SAME journey, earlier wakes included. The retained
`report.json`/`report.html` beside the frozen wakes carry the verdict;
`Outcome` carries the error channel the SDK protocol needs.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nora.infra.sql.schema import (
    BRIDGE_ACTION,
    COGNITION_SESSION,
    COGNITION_WAKE,
    COGNITION_WAKE_OUTCOME,
    MESSAGES,
    NOTE,
    NOTE_REVISION,
    REMINDERS,
)
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.subject.effects import effect_boundary, effect_scope, read_effects
from nora.subject.eval_evidence import current, read_events, validate_events
from nora.subject.journey import JourneyDriver
from scripts.cognition_loop import html as report_html
from scripts.cognition_loop import state as state_mod
from scripts.cognition_loop.accounting import write_report
from scripts.cognition_loop.availability import availability
from scripts.cognition_loop.cases import Expectation
from scripts.cognition_loop.egress import assert_no_egress
from scripts.cognition_loop.judges import judge_expectation
from scripts.cognition_loop.trajectory import session_events, wake_trajectory
from scripts.cognition_loop.verdicts import (
    ERROR,
    WAKE_ERRED,
    ExpectationVerdict,
    Rollup,
)
from scripts.memory_graph_spike import Judges

from .domain import Case, Outcome

_MODE = "autonomous-live-journey"


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)


def _expectations(plan: dict[str, Any]) -> list[Expectation]:
    return [Expectation(wake=int(raw["wake"]), polarity=str(raw["polarity"]),
                        kind=str(raw["kind"]), text=str(raw["text"]))
            for raw in plan.get("expectations") or ()]


def _batch(offered: list[int], offered_self: list[int],
           had_session: bool | None) -> dict[str, Any]:
    """Offered-versus-recorded evidence; `recorded` is None — the production
    wake's own `from`/`through` range is on the wake row, not restated here."""
    return {
        "recorded": None, "offered": offered,
        "recorded_self": None, "offered_self": offered_self,
        "production_session": had_session,
    }


def _freeze_wake(output: Path, index: int, wake: Any, session_id: Any,
                 egress: dict[str, Any], diff: Any, trajectory: dict[str, Any],
                 expectations: list[dict[str, Any]], state_after: Any, *,
                 unit: str, batch: dict[str, Any] | None,
                 no_session: str | None, step_error: tuple[str, str] | None,
                 prior_links: set[str] | None) -> dict[str, Any]:
    """The production wake's private evidence file and its report row —
    the same shape `scripts.cognition_loop.run._freeze_wake` freezes, with
    the production wake id standing in both `recorded_wake_id` and
    `wake_id` (this journey replays nothing; every wake IS the wake)."""
    from scripts.cognition_loop.run import _freeze_wake as freeze_row

    return freeze_row(
        output, index, wake["id"],
        SimpleNamespace(wake_id=wake["id"], forced=None),
        int(wake["at_ms"]), str(wake["reason"]), session_id, egress, True,
        diff, trajectory, expectations, state_after, None,
        unit=unit, batch=batch, no_session=no_session,
        step_error=step_error, prior_links=prior_links)


def _wake_error(output: Path, index: int, wake: Any, *, step: str,
                message: str, unit: str, batch: dict[str, Any] | None = None,
                no_session: str | None = None,
                trajectory: dict[str, Any] | None = None) -> dict[str, Any]:
    from scripts.cognition_loop.run import _wake_error as error_row

    return error_row(
        output, index, int(wake["id"]), int(wake["at_ms"]), str(wake["reason"]),
        None, step=step, message=message, unit=unit, batch=batch,
        no_session=no_session, trajectory=trajectory)


async def _judge(judges: Any, models: tuple[str, str],
                 expectations: list[Expectation], index: int, diff: Any,
                 after: Any, trajectory: dict[str, Any],
                 earlier_wakes: tuple) -> list[dict[str, Any]]:
    rows = []
    for expectation in expectations:
        if expectation.wake != index:
            continue
        try:
            verdict_obj = await judge_expectation(
                judges, models, expectation, diff, after, trajectory, earlier_wakes)
        except Exception as exc:  # noqa: BLE001 — a judge failure is ERROR, not a crash
            verdict_obj = ExpectationVerdict(
                verdict=ERROR, answers=(), detail=f"judge: {exc!r}")
        rows.append({
            "expectation": {
                "polarity": expectation.polarity,
                "kind": expectation.kind,
                "text": expectation.text,
            },
            "verdict": verdict_obj.verdict,
            "detail": verdict_obj.detail,
            "verdict_obj": verdict_obj,
            "answers": [asdict(a) for a in verdict_obj.answers],
        })
    return rows


async def _finalize(index: int, ctx: dict[str, Any], *, subject: Any,
                    scope: Any, output: Path, judges: Any, models: tuple[str, str],
                    expectations: list[Expectation], earlier_wakes: tuple,
                    prior_links: set[str], egress: dict[str, Any],
                    evidence_path: Path, before: Any) -> dict[str, Any]:
    """Canonical finalization for one production wake: events → trajectory →
    effects → snapshot → diff → both judges → frozen file → report row."""
    wake, outcome = ctx["wake"], ctx["outcome"]
    session_id = outcome["session_id"]
    no_session = (outcome["detail"] or "empty agenda") if session_id is None else None
    events = read_events(evidence_path)
    subset = (session_events(events, session_id=session_id)
              if session_id is not None else [])
    before_tokens = ctx.get("tokens")
    tokens = (subject.deps.metrics.get("nora_cognition_unit_tokens_total")
              - before_tokens) if before_tokens is not None else None
    trajectory = wake_trajectory(
        subset, wake_id=int(wake["id"]), session_id=session_id,
        tokens=tokens or None)
    trajectory["at_ms"] = int(wake["at_ms"])
    trajectory["unit"] = scope.unit
    trajectory["unit_rooms"] = list(scope.rooms)
    trajectory["availability"] = availability(subject.cfg, trajectory)
    trajectory["effects"] = await read_effects(
        ctx["engine"], since=ctx["boundary"], unit=scope.unit,
        rooms=scope.rooms, profile_keys=scope.profile_keys, events=subset)
    after = ctx["after"]
    diff = state_mod.diff(before, after)
    rows = await _judge(judges, models, expectations, index, diff, after,
                        trajectory, earlier_wakes)
    return _freeze_wake(
        output, index, wake, session_id, egress, diff, trajectory, rows,
        after, unit=scope.unit, batch=ctx["batch"], no_session=no_session,
        step_error=None, prior_links=prior_links)


async def _offered(engine: Any, unit: str, at_ms: int) -> tuple[list[int], list[int]]:
    view = await SqlStimulusStore(engine).view(
        unit=unit, mode="live", now_ms=at_ms, limit=500)
    offered = sorted(s.id for s in view.pending)
    return offered, sorted(s.id for s in view.pending if s.kind == "self")


def _report(case: Case, unit: str, roll: Rollup,
            wakes: list[dict[str, Any]]) -> dict[str, Any]:
    for wake in wakes:
        for expectation in wake.get("expectations", []):
            expectation.pop("verdict_obj", None)
    return {
        "status": roll.verdict, "mode": _MODE, "behavior": "UNMEASURED",
        "case": case.name, "unit": unit,
        "errors": roll.errors,
        "wakes": [
            {k: v for k, v in w.items() if not k.startswith("_")}
            for w in wakes
        ],
    }


async def _run(subject: Any, case: Case, plan: dict[str, Any],
               world: Any, output: Path) -> tuple[Rollup, list[dict[str, Any]]]:
    """The steps through the live app; wakes finalized in order, judges
    bound to the SAME canonical evidence the frozen files carry."""
    roll = Rollup()
    wakes_report: list[dict[str, Any]] = []
    expectations = _expectations(plan)
    models = tuple(str(m) for m in plan.get("judges") or ())
    if len(models) != 2 or not all(models):
        roll.error("the journey plan declares not exactly two judge models")
        return roll, wakes_report
    judges = Judges(subject.cfg, list(models), Path(output))
    scope = effect_scope(subject)
    if subject.unit != scope.unit:
        roll.error("the composed unit and the case's scope disagree")
        return roll, wakes_report
    egress = assert_no_egress(subject)
    evidence = current()
    if evidence is None:
        roll.error("canonical capture unavailable mid-drive")
        return roll, wakes_report
    steps = plan.get("steps") or []
    if not any("send" in step for step in steps):
        roll.error("the journey plan sends nothing through real ingress")
        return roll, wakes_report
    wake_seconds = (subject.cfg.cognition.cue_quiet_seconds
                    + subject.cfg.cognition.tick_seconds + 300)
    fire_seconds = (subject.cfg.reminders.min_lead_seconds
                    + subject.cfg.heartbeat.tick_seconds * 2 + 60)
    pending: list[dict[str, Any]] = []
    earlier_wakes: tuple = ()
    prior_links: set[str] = set()
    index = 0
    seen_wakes = 0
    step_error: tuple[str, str] | None = None

    async def _settled(ctx: dict[str, Any], before: Any) -> Any:
        """Finalize one pending wake ctx into a report row."""
        nonlocal earlier_wakes, prior_links
        row = await _finalize(
            index, ctx, subject=subject, scope=scope, output=output,
            judges=judges, models=models, expectations=expectations,
            earlier_wakes=earlier_wakes, prior_links=prior_links,
            egress=egress, evidence_path=evidence.path, before=before)
        wakes_report.append(row)
        earlier_wakes += ((row["_diff"], row["trajectory"]),)
        prior_links = set((row.get("loop") or {}).get("link_ids") or ())
        return ctx["after"]

    async def _drive() -> None:
        nonlocal index, seen_wakes, step_error
        driver = JourneyDriver(subject)
        world.wire_actions(subject.deps.bridge_runtime, subject.environment)
        async with driver:
            engine = driver.engine
            boundary = await effect_boundary(engine)
            before = await state_mod.snapshot(
                engine, unit=scope.unit, rooms=scope.rooms,
                profile_keys=scope.profile_keys, now_ms=_now_ms(),
                metrics=subject.deps.metrics)
            sent = 0
            for step in steps:
                if "send" in step:
                    for ctx in pending:
                        if "wake" not in ctx:
                            continue
                        before = await _settled(ctx, before)
                        index += 1
                    pending.clear()
                    boundary = await effect_boundary(engine)
                    sent += 1
                    await driver.ingress(str(step["send"]),
                                         event_id=f"generated-journey-{sent}")
                    pending.append({
                        "boundary": boundary, "engine": engine,
                        "tokens": subject.deps.metrics.get(
                            "nora_cognition_unit_tokens_total"),
                    })
                elif "until_wakes" in step:
                    wanted = int(step["until_wakes"])
                    await driver.wait_closed_wakes(wanted, seconds=wake_seconds)
                    wakes = await driver.rows(COGNITION_WAKE)
                    outcomes = await driver.rows(COGNITION_WAKE_OUTCOME)
                    by_wake = {o["wake_id"]: o for o in outcomes}
                    if (len(wakes) < wanted
                            or any(w["id"] not in by_wake for w in wakes[:wanted])):
                        raise ValueError(
                            f"wake/outcome denominator disagree: {len(wakes)} wakes, "
                            f"{len(outcomes)} outcomes after {wanted} wanted")
                    new = wakes[seen_wakes:wanted]
                    seen_wakes = wanted
                    if not pending:
                        raise ValueError("a wake settled with no send window")
                    ctx = pending[-1]
                    if len(new) != 1 or "wake" in ctx:
                        raise ValueError(
                            f"the step expected exactly one new wake and "
                            f"{len(new)} appeared; one wake per wait is the "
                            "bound the state windows can prove")
                    latest = new[0]
                    if latest["unit"] != scope.unit:
                        raise ValueError("a wake in another unit appeared")
                    outcome = by_wake[latest["id"]]
                    offered, offered_self = await _offered(
                        engine, scope.unit, int(latest["at_ms"]))
                    ctx.update({
                        "wake": latest, "outcome": outcome,
                        "after": await state_mod.snapshot(
                            engine, unit=scope.unit, rooms=scope.rooms,
                            profile_keys=scope.profile_keys, now_ms=_now_ms(),
                            metrics=subject.deps.metrics),
                        "batch": _batch(offered, offered_self,
                                        outcome["session_id"] is not None),
                    })
                elif "until_delivery" in step:
                    await driver.wait_delivery(seconds=fire_seconds)
                else:
                    raise ValueError(f"unknown journey step {step!r}")
            for ctx in pending:
                if "wake" not in ctx:
                    continue
                before = await _settled(ctx, before)
                index += 1
            pending.clear()
            if not wakes_report:
                raise ValueError("the journey produced no wake to judge")

    try:
        await _drive()
    except Exception as exc:  # noqa: BLE001 — a failed step is ERROR, not a crash
        step_error = ("journey", f"{type(exc).__name__}: {exc}")
        roll.error(f"the journey failed: {step_error[1]}")
        # A wake that had already settled when a later step failed still
        # freezes an ERROR row rather than vanishing from the denominator.
        for ctx in pending:
            if "wake" not in ctx:
                continue
            wakes_report.append(_wake_error(
                output, index, ctx["wake"], step=step_error[0],
                message=step_error[1], unit=scope.unit, batch=ctx.get("batch"),
                trajectory=ctx.get("trajectory")))
            index += 1
        pending.clear()
    if not wakes_report and not roll.errors:
        roll.error("the journey produced no wake report")
    return roll, wakes_report


async def autonomous_journey(subject: Any, case: Case, *, now_ms: int) -> Outcome:
    """The `App`'s own timers drive this journey; `now_ms` is unused — the
    live clock is the point, not a stated instant."""
    del now_ms
    evidence = current()
    world = getattr(subject.environment, "world", None)
    if evidence is None:
        return Outcome(admitted=None, source="fail_closed",
                       error="the autonomous journey requires canonical capture")
    if world is None or getattr(world, "wire_actions", None) is None:
        return Outcome(admitted=None, source="fail_closed",
                       error="the case's services are not a generated journey world")
    plan = dict((case.metadata or {}).get("journey") or {})
    expectations = plan.get("expectations") or []
    output = evidence.path / f"journey-{case.name}"
    output.mkdir(mode=0o700)
    roll = Rollup()
    wakes_report: list[dict[str, Any]] = []
    try:
        roll, wakes_report = await _run(subject, case, plan, world, output)
    except Exception as exc:  # noqa: BLE001 — the run itself is ERROR, not a crash
        roll.error(f"the journey run failed: {type(exc).__name__}: {exc}")
    for wake in wakes_report:
        for verdict in wake.get("expectations", []):
            roll.add(verdict["verdict_obj"])
    if roll.verdict != ERROR and any(w.get("status") == ERROR for w in wakes_report):
        roll.error(WAKE_ERRED)
    declared_wakes = plan.get("expected_wakes")
    if declared_wakes is not None and len(wakes_report) != int(declared_wakes):
        roll.error(
            f"the case declared {declared_wakes} wakes and produced "
            f"{len(wakes_report)}")
    case_report = _report(case, subject.unit, roll, wakes_report)
    expected = {
        "name": case.name or "unnamed",
        "unit": subject.unit,
        "wakes": [w.get("recorded_wake_id") for w in wakes_report],
        "expectations": [asdict(e) for e in _expectations(plan)],
        "read_probes": [],
    }
    report = {
        "status": roll.verdict,
        "mode": _MODE,
        "behavior": "UNMEASURED",
        "expected_cases": json.loads(json.dumps([expected])),
        "cases": [case_report],
        "errors": roll.errors,
        "run": {
            "mode": _MODE,
            "behavior": "UNMEASURED",
            "claim": "autonomous-live-journey",
            "execution": "live-clock",
            "judges": [str(m) for m in plan.get("judges") or ()],
            "judge_requests": len(getattr(world.judge, "requests", []) or []),
            "model_requests": len(getattr(world, "requests", []) or []),
        },
    }
    try:
        receipt = validate_events(read_events(evidence.path), require_terminal=False)
    except Exception:  # noqa: BLE001 — unreadable evidence is an ERROR receipt
        receipt = {"status": "ERROR", "causes": ["unreadable_record"]}
    report["evidence"] = {**receipt, "ref": evidence.path.name}
    report["receipts"] = await _receipts(subject)
    if report["receipts"].get("status") == "unavailable":
        report["errors"].append(
            {"step": "receipts", "message": report["receipts"]["cause"]})
        report["status"] = ERROR
    write_report(output, report)
    try:
        report_html.render(output)
    except Exception as exc:  # noqa: BLE001 — report.json stands; the error is named
        report["errors"].append({"step": "report_html", "message": repr(exc)})
        report["status"] = ERROR
        write_report(output, report)
    evidence.record("journey_report", status=report["status"], ref=output.name,
                    expectations=len(expectations),
                    wakes=len(wakes_report))
    said: tuple[str, ...] = ()
    error = None
    if report["status"] != "PASS":
        error = f"autonomous journey {report['status'].lower()}: " + (
            "; ".join(str(e) for e in roll.errors) or "see the retained report")
    else:
        said = tuple(await read_messages(subject))
    return Outcome(admitted=None, said=said, error=error,
                   source="model" if error is None else "fail_closed",
                   judged="" if error is not None else report["status"],
                   outcome_token=f"journey:{report['status'].lower()}")


_RECEIPT_TABLES = {
    "wakes": COGNITION_WAKE, "outcomes": COGNITION_WAKE_OUTCOME,
    "sessions": COGNITION_SESSION, "notes": NOTE,
    "note_revisions": NOTE_REVISION, "actions": BRIDGE_ACTION,
    "messages": MESSAGES, "reminders": REMINDERS,
}


async def _receipts(subject: Any) -> dict[str, Any]:
    """The SQL receipts the case's claims rest on, captured inside the drive —
    the case sandbox is dropped at drive teardown, so the durable record is
    the only place the rows can be checked against later."""
    from nora.infra.sql.engine import create_sql_engine

    engine = create_sql_engine(subject.database_url)
    try:
        out: dict[str, Any] = {}
        async with engine.connect() as conn:
            import sqlalchemy as sa

            for name, table in _RECEIPT_TABLES.items():
                rows = (await conn.execute(sa.select(table))).mappings().all()
                out[name] = [
                    json.loads(json.dumps(dict(row), default=str)) for row in rows
                ]
        return out
    except Exception as exc:  # noqa: BLE001 — an unreadable receipt is named
        return {"status": "unavailable", "cause": f"{type(exc).__name__}: {exc}"}
    finally:
        await engine.dispose()


async def read_messages(subject: Any) -> list[Any]:
    """The delivery receipts she actually wrote: sent `is_nora` audit rows."""
    from nora.infra.sql.engine import create_sql_engine

    engine = create_sql_engine(subject.database_url)
    try:
        async with engine.connect() as conn:
            import sqlalchemy as sa

            rows = (await conn.execute(
                sa.select(MESSAGES.c.body).where(
                    MESSAGES.c.is_nora == 1,
                    MESSAGES.c.delivery_status == "sent"))).scalars().all()
        return [str(row) for row in rows]
    finally:
        await engine.dispose()
