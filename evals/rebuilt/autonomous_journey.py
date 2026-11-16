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

The split owns three seams: `journey_manifest` validates the declared plan
and reconciles the wake census, `journey_wakes` finalizes each produced
wake, and `journey_turns` binds each declared send's turn to its own
causal evidence.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncEngine

from evals.measurement import PROTOCOL, Measurement, for_world
from nora.subject.circle import create_sql_engine
from nora.subject.effects import (
    effect_boundary,
    effect_scope,
    frozen_reads,
    journey_receipts,
    sent_message_bodies,
)
from nora.subject.eval_evidence import current, read_events, validate_events
from nora.subject.journey import (
    JourneyDriver,
    declared_envelope,
    declared_source,
    message_from_dict,
)
from scripts.cognition_loop import html as report_html
from scripts.cognition_loop import state as state_mod
from scripts.cognition_loop.accounting import write_report
from scripts.cognition_loop.cases import Expectation
from scripts.cognition_loop.egress import assert_no_egress
from scripts.cognition_loop.judges import judge_history_entry
from scripts.cognition_loop.verdicts import ERROR, FAIL, PASS, REVIEW, WAKE_ERRED, Rollup
from scripts.memory_graph_spike import Judges

from .domain import Case, Outcome
from .journey_manifest import (
    _bind_declared_wakes,
    _census_reconcile,
    _expectations,
    _plan_error,
    _wake_census,
)
from .journey_turns import _turn_error, observed_turn
from .journey_wakes import _await_wake, _finalize, _wake_error

_MODE = "autonomous-live-journey"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _report(case: Case, unit: str, roll: Rollup,
            wakes: list[dict[str, Any]], turns: list[dict[str, Any]],
            version: int) -> dict[str, Any]:
    for occasion in (*wakes, *turns):
        for expectation in occasion.get("expectations", []):
            expectation.pop("verdict_obj", None)
    return {
        "status": roll.verdict, "mode": _MODE,
        "version": version,
        "case": case.name, "unit": unit,
        "errors": roll.errors,
        "wakes": [
            {k: v for k, v in w.items() if not k.startswith("_")}
            for w in wakes
        ],
        "turns": [
            {k: v for k, v in t.items() if not k.startswith("_")}
            for t in turns
        ],
    }


async def _drain_pending(pending: list[dict[str, Any]], *, before: Any,
                         settle: Any) -> Any:
    """Finalize every settled pending ctx; the next `before` state."""
    for ctx in pending:
        if "wake" not in ctx:
            continue
        before = await settle(ctx, before)
    pending.clear()
    return before


def _input_ctx(step: dict[str, Any], *, sent: int, engine: Any,
               boundary: Any, cursor: Any) -> dict[str, Any]:
    """The turn ctx before admission: input text, instant, declaration."""
    declared = (declared_envelope(step["envelope"])
                if "envelope" in step else None)
    return {
        "index": sent - 1,
        "input": (str(step["send"]) if declared is None else
                  message_from_dict(declared.message).body),
        "event_id": None,
        "at_ms": (_now_ms() if declared is None
                  else int(declared.sent_ms)),
        "cursor": cursor, "boundary": boundary,
        "engine": engine, "event": None, "work": None,
        "envelope": declared,
        "source": (None if declared is None
                   else declared_source(declared)),
    }


async def _admit_input(driver: JourneyDriver, ctx: dict[str, Any], *,
                       sent: int, tagged: bool) -> None:
    """The ctx's declared input through real ingress."""
    declared = ctx["envelope"]
    if declared is None:
        incoming, event, work = await driver.ingress(
            ctx["input"], event_id=f"generated-journey-{sent}",
            tagged=tagged)
    else:
        incoming, event, work = await driver.ingress_declared(declared)
    ctx.update({
        "event_id": incoming.event_id,
        "at_ms": (int(incoming.sent_ms or _now_ms()) if declared is None
                  else int(declared.sent_ms)),
        "event": event, "work": work})


def _expected_turns(steps: list[dict[str, Any]]) -> list[Any]:
    """The original turn declarations, preserving each envelope's wire fields."""
    return [
        str(step["send"]) if "send" in step else {"envelope": step["envelope"]}
        for step in steps
        if isinstance(step, dict) and ("send" in step or "envelope" in step)]


async def _drive(subject: Any, *, world: Any, steps: list[dict[str, Any]],
                 scope: Any, judges: Any, models: tuple[str, str],
                 expectations: list[Expectation], egress: dict[str, Any],
                 evidence: Any, output: Path, wake_seconds: float,
                 fire_seconds: float, wakes_report: list[dict[str, Any]],
                 turns_report: list[dict[str, Any]],
                 pending: list[dict[str, Any]], expected_wakes: int,
                 open_turn: list[dict[str, Any]]) -> Any:
    """The plan through the owned App; the journey's opening boundary."""
    driver = JourneyDriver(subject)
    world.wire_actions(subject.deps.bridge_runtime, subject.environment)
    earlier_wakes: tuple = ()
    prior_links: set[str] = set()
    seen_wakes = 0
    journey_boundary: Any = None

    async def _settled(ctx: dict[str, Any], before: Any) -> Any:
        """Finalize one pending wake ctx into a report row."""
        nonlocal earlier_wakes, prior_links
        row = await _finalize(
            len(wakes_report), ctx, subject=subject, scope=scope,
            output=output, judges=judges, models=models,
            expectations=expectations, earlier_wakes=earlier_wakes,
            prior_links=prior_links, egress=egress,
            evidence_path=evidence.path, before=before)
        wakes_report.append(row)
        earlier_wakes += (judge_history_entry(
            row.get("_diff"), row.get("trajectory")),)
        prior_links = set((row.get("loop") or {}).get("link_ids") or ())
        return row["_state_before"]

    async def _observed(ctx: dict[str, Any], before: Any) -> Any:
        """Close one open send's turn observation into a report row."""
        nonlocal earlier_wakes
        row, entry = await observed_turn(
            ctx, subject=subject, driver=driver, evidence=evidence,
            scope=scope, output=output, judges=judges, models=models,
            expectations=expectations, earlier_wakes=earlier_wakes,
            fire_seconds=fire_seconds, before=before)
        turns_report.append(row)
        earlier_wakes += (entry,)
        return row["_state_before"]

    async def _close_open(before: Any) -> Any:
        nonlocal earlier_wakes
        if not open_turn:
            return before
        (ctx,) = open_turn
        open_turn.clear()
        try:
            return await _observed(ctx, before)
        except Exception as exc:  # noqa: BLE001 — an unobserved turn is ERROR
            turns_report.append(_turn_error(
                output, ctx["index"], ctx, step="observe",
                message=f"{type(exc).__name__}: {exc}", unit=scope.unit))
            earlier_wakes += ((None, {
                "invalid": f"turn {ctx['index']} observation failed: "
                           f"{type(exc).__name__}"}),)
            return before

    async with driver:
        engine = driver.engine
        async with frozen_reads(engine) as reads:
            boundary = await effect_boundary(reads)
            journey_boundary = boundary
            before = await state_mod.snapshot(
                cast(AsyncEngine, reads), unit=scope.unit, rooms=scope.rooms,
                profile_keys=scope.profile_keys, now_ms=_now_ms(),
                metrics=subject.deps.metrics)
        sent = 0
        for step in steps:
            if "send" in step or "envelope" in step:
                before = await _drain_pending(pending, before=before,
                                              settle=_settled)
                before = await _close_open(before)
                boundary = await effect_boundary(engine)
                cursor = evidence.cursor()
                sent += 1
                # The turn is registered before admission: an ingress
                # failure still freezes its ERROR row in `_run`, keeping
                # the declared turn in the denominator.
                ctx = _input_ctx(step, sent=sent, engine=engine,
                                 boundary=boundary, cursor=cursor)
                open_turn.append(ctx)
                await _admit_input(
                    driver, ctx, sent=sent,
                    tagged=bool(step.get("tagged")))
                pending.append({
                    "boundary": boundary, "engine": engine,
                    "tokens": subject.deps.metrics.get(
                        "nora_cognition_unit_tokens_total")})
            elif "until_wakes" in step:
                seen_wakes = await _await_wake(
                    driver, engine=engine, scope=scope, pending=pending,
                    seen_wakes=seen_wakes, wanted=int(step["until_wakes"]),
                    seconds=wake_seconds)
            elif "until_delivery" in step:
                await driver.wait_delivery(seconds=fire_seconds)
            else:
                raise ValueError(f"unknown journey step {step!r}")
        await _drain_pending(pending, before=before, settle=_settled)
        await _close_open(before)
        if not wakes_report and expected_wakes > 0:
            raise ValueError("the journey produced no wake to judge")
    return journey_boundary


async def _post_drive_census(subject: Any, *, scope: Any, expected_wakes: Any,
                             wakes_report: list[dict[str, Any]],
                             journey_boundary: Any, output: Path,
                             roll: Rollup, evidence: Any
                             ) -> list[Any] | None:
    """Closed-lifecycle census: observed scoped ids, `None` if unreadable."""
    # The owned App lifecycle is closed on every path out of `_drive`, so
    # the census reads the whole production population at one instant.
    try:
        census = await _wake_census(subject)
    except Exception as exc:  # noqa: BLE001 — an unreadable census is an ERROR
        roll.error(f"the wake census is unreadable: "
                   f"{type(exc).__name__}: {exc}")
        return None
    reported = [row.get("recorded_wake_id") for row in wakes_report]
    observed, census_errors, unaccounted = _census_reconcile(
        census, scope=scope, reported=reported,
        after=(journey_boundary.positions.get("cognition_wake", 0)
               if journey_boundary is not None else 0))
    for why in census_errors:
        roll.error(why)
    for wake in unaccounted:
        wakes_report.append(_wake_error(
            output, len(wakes_report), wake, step="census",
            message="a produced wake never entered the planned waits "
                    "and stays ungraded", unit=scope.unit))
    evidence.record("journey_wake_census", observed=observed,
                    reported=reported, declared=expected_wakes)
    return observed


async def _run(subject: Any, case: Case, plan: dict[str, Any],
               world: Any, output: Path,
               ) -> tuple[Rollup, list[dict[str, Any]], list[dict[str, Any]],
                          list[Any] | None]:
    """Steps through the live App; (roll, wake rows, turn rows, census ids)."""
    roll = Rollup()
    wakes_report: list[dict[str, Any]] = []
    turns_report: list[dict[str, Any]] = []
    manifest_error = _plan_error(plan)
    if manifest_error is not None:
        roll.error(manifest_error)
        return roll, wakes_report, turns_report, None
    version = plan.get("version", 1)
    expectations = _expectations(plan, version)
    models = (str(plan["judges"][0]), str(plan["judges"][1]))
    judges = Judges(subject.cfg, list(models), Path(output))
    scope = effect_scope(subject)
    if subject.unit != scope.unit:
        roll.error("the composed unit and the case's scope disagree")
        return roll, wakes_report, turns_report, None
    egress = assert_no_egress(subject)
    evidence = current()
    if evidence is None:
        roll.error("canonical capture unavailable mid-drive")
        return roll, wakes_report, turns_report, None
    steps = plan.get("steps") or []
    if not any("send" in step or "envelope" in step for step in steps):
        roll.error("the journey plan sends nothing through real ingress")
        return roll, wakes_report, turns_report, None
    wake_seconds = (subject.cfg.cognition.cue_quiet_seconds
                    + subject.cfg.cognition.tick_seconds + 300)
    fire_seconds = (subject.cfg.reminders.min_lead_seconds
                    + subject.cfg.heartbeat.tick_seconds * 2 + 60)
    pending: list[dict[str, Any]] = []
    open_turn: list[dict[str, Any]] = []
    journey_boundary: Any = None
    try:
        journey_boundary = await _drive(
            subject, world=world, steps=steps, scope=scope, judges=judges,
            models=models, expectations=expectations, egress=egress,
            evidence=evidence, output=output, wake_seconds=wake_seconds,
            fire_seconds=fire_seconds, wakes_report=wakes_report,
            turns_report=turns_report, pending=pending, open_turn=open_turn,
            expected_wakes=int(plan["expected_wakes"]))
    except Exception as exc:  # noqa: BLE001 — a failed step is ERROR, not a crash
        roll.error(f"the journey failed: {type(exc).__name__}: {exc}")
        # A wake or turn settled when a later step failed still freezes
        # an ERROR row rather than vanishing from the denominator.
        for ctx in pending:
            if "wake" not in ctx:
                continue
            wakes_report.append(_wake_error(
                output, len(wakes_report), ctx["wake"], step="journey",
                message=f"{type(exc).__name__}: {exc}", unit=scope.unit,
                batch=ctx.get("batch"), trajectory=ctx.get("trajectory")))
        pending.clear()
        for ctx in open_turn:
            turns_report.append(_turn_error(
                output, ctx["index"], ctx, step="journey",
                message=f"{type(exc).__name__}: {exc}", unit=scope.unit))
        open_turn.clear()
    observed = await _post_drive_census(
        subject, scope=scope, expected_wakes=plan.get("expected_wakes"),
        wakes_report=wakes_report, journey_boundary=journey_boundary,
        output=output, roll=roll, evidence=evidence)
    if not wakes_report and not turns_report and not roll.errors:
        roll.error("the journey produced no wake report")
    return roll, wakes_report, turns_report, observed


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
    measurement = for_world(world, Measurement(
        mode=evidence.metadata.get("measurement", PROTOCOL.mode)))
    plan = dict((case.metadata or {}).get("journey") or {})
    expectations = plan.get("expectations") or []
    output = evidence.path / f"journey-{case.name}"
    output.mkdir(mode=0o700)
    roll = Rollup()
    wakes_report: list[dict[str, Any]] = []
    turns_report: list[dict[str, Any]] = []
    observed: list[Any] | None = None
    try:
        roll, wakes_report, turns_report, observed = await _run(
            subject, case, plan, world, output)
    except Exception as exc:  # noqa: BLE001 — the run itself is ERROR, not a crash
        roll.error(f"the journey run failed: {type(exc).__name__}: {exc}")
    for occasion in (*wakes_report, *turns_report):
        for verdict in occasion.get("expectations", []):
            roll.add(verdict["verdict_obj"])
    if roll.verdict != ERROR and any(
            w.get("status") == ERROR for w in (*wakes_report, *turns_report)):
        roll.error(WAKE_ERRED)
    # The denominator is the whole scoped production population the census
    # recorded — never the report rows that survived to be bound.
    produced = (observed if observed is not None
                else [w.get("recorded_wake_id") for w in wakes_report])
    bound = _bind_declared_wakes(plan.get("expected_wakes"), produced, roll)
    version = plan.get("version", 1)
    case_report = _report(case, subject.unit, roll, wakes_report,
                          turns_report, version)
    expected = {
        "name": case.name or "unnamed",
        "unit": subject.unit,
        "wakes": bound,
        "turns": _expected_turns(plan.get("steps") or []),
        "expectations": [asdict(e) for e in _expectations(plan, version)],
        "read_probes": [],
        "version": version,
    }
    report = {
        "status": roll.verdict,
        "mode": _MODE,
        "version": version,
        "expected_cases": json.loads(json.dumps([expected])),
        "cases": [case_report],
        "errors": roll.errors,
        "run": {
            "mode": _MODE,
            "measurement": measurement.mode,
            "claim": "autonomous-live-journey",
            "execution": "live-clock",
            "judges": [str(m) for m in plan.get("judges") or ()],
            "judge_requests": len(getattr(world.judge, "requests", []) or []),
            "model_requests": len(getattr(world, "requests", []) or []),
            "wake_census": {"observed": produced,
                            "declared": plan.get("expected_wakes")},
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
    said = (tuple(await read_messages(subject))
            if report["status"] in (PASS, FAIL, REVIEW) else ())
    return _journey_result(report, said=said)


def _journey_result(report: dict[str, Any], *, said: tuple[str, ...]) -> Outcome:
    """Valid semantic FAIL/REVIEW is a judgment, not a task execution error."""
    error = None
    if report["status"] not in (PASS, FAIL, REVIEW):
        error = f"autonomous journey {report['status'].lower()}: " + (
            "; ".join(str(e) for e in report.get("errors") or [])
            or "see the retained report")
    return Outcome(admitted=None, said=said, error=error,
                   source="model" if error is None else "fail_closed",
                   judged="" if error is not None else report["status"],
                   outcome_token=f"journey:{report['status'].lower()}")


async def _receipts(subject: Any) -> dict[str, Any]:
    """The SQL receipts the case's claims rest on, captured inside the drive."""
    engine = create_sql_engine(subject.database_url)
    try:
        return await journey_receipts(engine)
    except Exception as exc:  # noqa: BLE001 — an unreadable receipt is named
        return {"status": "unavailable", "cause": f"{type(exc).__name__}: {exc}"}
    finally:
        await engine.dispose()


async def read_messages(subject: Any) -> list[Any]:
    """The delivery receipts she actually wrote: sent `is_nora` audit rows."""
    engine = create_sql_engine(subject.database_url)
    try:
        return await sent_message_bodies(engine)
    finally:
        await engine.dispose()
