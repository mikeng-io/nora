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
from nora.subject.effects import (
    effect_boundary,
    effect_scope,
    observed_effects,
    read_effects,
)
from nora.subject.eval_evidence import current, read_events, validate_events
from nora.subject.journey import JourneyDriver
from scripts.cognition_loop import html as report_html
from scripts.cognition_loop import state as state_mod
from scripts.cognition_loop.accounting import _canonical_outcome, write_report
from scripts.cognition_loop.availability import availability
from scripts.cognition_loop.cases import STATE_KINDS, Expectation
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


def _plan_error(plan: dict[str, Any]) -> str | None:
    """Why this plan cannot bind a denominator, else None — checked before
    the App, a model call or a judge ever runs. `expected_wakes` is the
    declared census: a positive int whose slots every `until_wakes` step
    reaches in order and every expectation anchors inside. A malformed
    plan refuses the case outright; the declared slots and criteria then
    stay in the denominator whether or not the runtime produces them."""
    expected = plan.get("expected_wakes")
    if type(expected) is not int or expected <= 0:
        return "the journey plan declares no positive expected_wakes"
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return "the journey plan declares no steps"
    reached = sent = 0
    for step in steps:
        if not isinstance(step, dict):
            return f"a journey step is not an object: {step!r}"
        if "send" in step:
            if not isinstance(step["send"], str):
                return f"a journey send is not text: {step!r}"
            sent += 1
            continue
        if "until_delivery" in step:
            continue
        if "until_wakes" in step:
            wanted = step["until_wakes"]
            if type(wanted) is not int or wanted <= reached or wanted > expected:
                return (f"journey step until_wakes={wanted!r} does not reach "
                        f"a declared slot of expected_wakes={expected}")
            reached = wanted
            continue
        return f"unknown journey step {step!r}"
    if not sent:
        return "the journey plan sends nothing through real ingress"
    if reached != expected:
        return (f"journey steps reach {reached} wakes; expected_wakes "
                f"declares {expected}")
    for position, raw in enumerate(plan.get("expectations") or []):
        if not isinstance(raw, dict):
            return f"journey expectation {position} is not an object"
        wake = raw.get("wake")
        if type(wake) is not int or not 0 <= wake < expected:
            return (f"journey expectation {position} anchors wake {wake!r} "
                    f"outside the {expected} declared slots")
        if raw.get("polarity") not in ("must", "must_not"):
            return (f"journey expectation {position} polarity "
                    f"{raw.get('polarity')!r} is not must|must_not")
        if raw.get("kind") not in STATE_KINDS:
            return (f"journey expectation {position} kind {raw.get('kind')!r} "
                    "is not a state kind")
        if not str(raw.get("text") or "").strip():
            return f"journey expectation {position} text is empty"
    models = plan.get("judges")
    if (not isinstance(models, (list, tuple)) or len(models) != 2
            or not all(isinstance(m, str) and m for m in models)
            or len(set(models)) != 2):
        return "the journey plan declares not exactly two distinct judge models"
    return None


def _bind_declared_wakes(declared: Any, produced: list[Any],
                         roll: Rollup) -> list[Any]:
    """The expected manifest's wake slots: each produced wake binds to the
    declared slot at its position and slots never reached stay `None`, so
    missing, extra or duplicated wakes are denominator errors, never a
    silently smaller census."""
    if not (type(declared) is int and declared > 0):
        return produced
    repeated = sorted(i for i in set(produced) if produced.count(i) > 1)
    if repeated:
        roll.error(f"produced wake(s) occupied two declared slots: {repeated}")
    if len(produced) < declared:
        roll.error(f"the runtime produced only {len(produced)} of the "
                   f"{declared} declared wake slots")
    elif len(produced) > declared:
        roll.error(f"the runtime produced {len(produced) - declared} "
                   f"wakes beyond the {declared} declared slots: "
                   f"{list(produced[declared:])}")
    return produced[:declared] + [None] * (declared - len(produced))


async def _wake_census(subject: Any) -> dict[str, Any]:
    """Every wake/outcome row, read after the owned lifecycle closed."""
    from nora.infra.sql.engine import create_sql_engine

    engine = create_sql_engine(subject.database_url)
    try:
        async with engine.connect() as conn:
            import sqlalchemy as sa

            wakes = (await conn.execute(
                sa.select(COGNITION_WAKE))).mappings().all()
            outcomes = (await conn.execute(
                sa.select(COGNITION_WAKE_OUTCOME))).mappings().all()
        return {"wakes": [dict(row) for row in wakes],
                "outcomes": [dict(row) for row in outcomes]}
    finally:
        await engine.dispose()


def _census_reconcile(census: dict[str, Any], *, scope: Any,
                      reported: list[Any], after: int,
                      ) -> tuple[list[Any], list[str], list[dict[str, Any]]]:
    """Census vs report rows: (observed ids, errors, unaccounted rows)."""
    errors: list[str] = []
    foreign = [row.get("id") for row in census["wakes"]
               if row.get("unit") != scope.unit]
    if foreign:
        errors.append(f"the wake census found wake(s) in a foreign unit: "
                      f"{foreign}")
    by_wake: dict[Any, list[dict[str, Any]]] = {}
    foreign_outcomes: list[Any] = []
    for row in census["outcomes"]:
        if row.get("unit") != scope.unit:
            foreign_outcomes.append(row.get("wake_id"))
        else:
            by_wake.setdefault(row.get("wake_id"), []).append(row)
    if foreign_outcomes:
        errors.append(f"the wake census found wake outcome(s) in a foreign "
                      f"unit: {foreign_outcomes}")
    scoped = sorted(
        (row for row in census["wakes"]
         if row.get("unit") == scope.unit and type(row.get("id")) is int),
        key=lambda row: row["id"])
    stale = [row["id"] for row in scoped if row["id"] <= after]
    if stale:
        errors.append(f"the wake census found wake(s) predating the "
                      f"journey's execution boundary: {stale}")
    scoped = [row for row in scoped if row["id"] > after]
    observed = [row["id"] for row in scoped]
    orphans = sorted(wake_id for wake_id in by_wake
                     if wake_id not in set(observed))
    if orphans:
        errors.append(f"the wake census found wake outcome(s) naming no "
                      f"scoped wake: {orphans}")
    unsettled = sorted(wake_id for wake_id in observed
                       if not any(row.get("outcome") == "closed"
                                  for row in by_wake.get(wake_id, ())))
    if unsettled:
        errors.append(f"the wake census found wake(s) never closed: "
                      f"{unsettled}")
    repeated = sorted(wake_id for wake_id, rows in by_wake.items()
                      if len(rows) > 1)
    if repeated:
        errors.append(f"the wake census found repeated wake outcome(s): "
                      f"{repeated}")
    if len(reported) != len(set(reported)):
        errors.append("a report row repeated a produced wake id")
    unaccounted = [row for row in scoped if row["id"] not in set(reported)]
    if unaccounted:
        errors.append(f"the wake census found produced wake(s) absent from "
                      f"the report: {[row['id'] for row in unaccounted]}")
    vanished = sorted((wake_id for wake_id in set(reported)
                       if wake_id not in set(observed)), key=repr)
    if vanished:
        errors.append(f"the wake census lacks wake(s) the report claims: "
                      f"{vanished}")
    return observed, errors, unaccounted


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


def _evidence_causes(ctx: dict[str, Any], subset: list[dict[str, Any]],
                     trajectory: dict[str, Any], *, scope: Any,
                     session_id: Any) -> list[str]:
    """Named invalidations for one wake's canonical evidence, computed
    before any judge request: its own typed closed wake/unit/session, the
    captured event window's custody, the observed complete effects and
    lifecycle settlement. Any cause freezes the wake ERROR with zero
    judge traffic — torn or foreign evidence never reaches a judge."""
    causes: list[str] = []
    wake, outcome = ctx["wake"], ctx["outcome"]
    if (not isinstance(wake, dict) or type(wake.get("id")) is not int
            or type(wake.get("at_ms")) is not int
            or not isinstance(wake.get("reason"), str)):
        causes.append("wake_identity_invalid")
    elif wake.get("unit") != scope.unit:
        causes.append("foreign_wake_unit")
    if not isinstance(outcome, dict) or outcome.get("outcome") != "closed":
        causes.append("wake_outcome_not_closed")
    receipt = validate_events(subset, require_terminal=False)
    if receipt["status"] != "PASS":
        causes.extend(f"capture_{cause}" for cause in receipt["causes"])
    if not observed_effects(trajectory.get("effects")):
        causes.append("effects_unobserved")
    else:
        state, _ref = _canonical_outcome(
            trajectory, unit=scope.unit,
            wake_id=wake.get("id") if isinstance(wake, dict) else None,
            session_id=session_id)
        if state != "closed":
            causes.append(f"settlement_{state}")
    if session_id is not None:
        for name in ("settlement", "final_response"):
            if (trajectory.get(name) or {}).get("status") != "observed":
                causes.append(f"{name}_unobserved")
    return causes


async def _finalize(index: int, ctx: dict[str, Any], *, subject: Any,
                    scope: Any, output: Path, judges: Any, models: tuple[str, str],
                    expectations: list[Expectation], earlier_wakes: tuple,
                    prior_links: set[str], egress: dict[str, Any],
                    evidence_path: Path, before: Any) -> dict[str, Any]:
    """Canonical finalization for one production wake: events → trajectory →
    effects → state cut → diff — all at THIS instant, so fire/delivery
    between the wake's close and finalization lands inside the window —
    then validity, and only then both judges and the frozen file."""
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
    # One cut for all three projections: the effects read, the state
    # snapshot and the diff all measure the same "now" — delivery rows
    # that landed since `until_wakes` are inside, never dropped between
    # two windows.
    trajectory["effects"] = await read_effects(
        ctx["engine"], since=ctx["boundary"], unit=scope.unit,
        rooms=scope.rooms, profile_keys=scope.profile_keys, events=subset)
    after = await state_mod.snapshot(
        ctx["engine"], unit=scope.unit, rooms=scope.rooms,
        profile_keys=scope.profile_keys, now_ms=_now_ms(),
        metrics=subject.deps.metrics)
    diff = state_mod.diff(before, after)
    causes = _evidence_causes(
        ctx, subset, trajectory, scope=scope, session_id=session_id)
    step_error = ("evidence", "; ".join(causes)) if causes else None
    rows = ([] if step_error is not None else await _judge(
        judges, models, expectations, index, diff, after,
        trajectory, earlier_wakes))
    return _freeze_wake(
        output, index, wake, session_id, egress, diff, trajectory, rows,
        after, unit=scope.unit, batch=ctx["batch"], no_session=no_session,
        step_error=step_error, prior_links=prior_links)


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


async def _drain_pending(pending: list[dict[str, Any]], *, before: Any,
                         settle: Any) -> Any:
    """Finalize every settled pending ctx; the next `before` state."""
    for ctx in pending:
        if "wake" not in ctx:
            continue
        before = await settle(ctx, before)
    pending.clear()
    return before


async def _await_wake(driver: Any, *, engine: Any, scope: Any,
                      pending: list[dict[str, Any]], seen_wakes: int,
                      wanted: int, seconds: float) -> int:
    """Wait out the count, bind the one new wake, return rows seen."""
    await driver.wait_closed_wakes(wanted, seconds=seconds)
    wakes = await driver.rows(COGNITION_WAKE)
    outcomes = await driver.rows(COGNITION_WAKE_OUTCOME)
    by_wake = {o["wake_id"]: o for o in outcomes}
    if (len(wakes) < wanted
            or any(w["id"] not in by_wake for w in wakes)):
        raise ValueError(
            f"wake/outcome denominator disagree: {len(wakes)} wakes, "
            f"{len(outcomes)} outcomes after {wanted} wanted")
    # Every observed wake counts: a second settled wake is an error, never
    # a quietly sliced-off tail; the census reconciles the whole set.
    new = wakes[seen_wakes:]
    if not pending:
        raise ValueError("a wake settled with no send window")
    ctx = pending[-1]
    if len(new) != 1 or "wake" in ctx:
        raise ValueError(
            f"the step expected exactly one new wake and {len(new)} "
            "appeared; one wake per wait is the bound the state windows "
            "can prove")
    latest = new[0]
    if latest["unit"] != scope.unit:
        raise ValueError("a wake in another unit appeared")
    outcome = by_wake[latest["id"]]
    offered, offered_self = await _offered(engine, scope.unit,
                                           int(latest["at_ms"]))
    # Plain dicts: `rows` yields RowMappings, which the identity/typing
    # checks downstream must not confuse for malformed evidence.
    ctx.update({"wake": dict(latest), "outcome": dict(outcome),
                "batch": _batch(offered, offered_self,
                                outcome["session_id"] is not None)})
    return len(wakes)


async def _drive(subject: Any, *, world: Any, steps: list[dict[str, Any]],
                 scope: Any, judges: Any, models: tuple[str, str],
                 expectations: list[Expectation], egress: dict[str, Any],
                 evidence: Any, output: Path, wake_seconds: float,
                 fire_seconds: float, wakes_report: list[dict[str, Any]],
                 pending: list[dict[str, Any]]) -> Any:
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
        earlier_wakes += ((row["_diff"], row["trajectory"]),)
        prior_links = set((row.get("loop") or {}).get("link_ids") or ())
        return row["_state_before"]

    async with driver:
        engine = driver.engine
        boundary = await effect_boundary(engine)
        journey_boundary = boundary
        before = await state_mod.snapshot(
            engine, unit=scope.unit, rooms=scope.rooms,
            profile_keys=scope.profile_keys, now_ms=_now_ms(),
            metrics=subject.deps.metrics)
        sent = 0
        for step in steps:
            if "send" in step:
                before = await _drain_pending(pending, before=before,
                                              settle=_settled)
                boundary = await effect_boundary(engine)
                sent += 1
                await driver.ingress(str(step["send"]),
                                     event_id=f"generated-journey-{sent}")
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
        if not wakes_report:
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
               ) -> tuple[Rollup, list[dict[str, Any]], list[Any] | None]:
    """Steps through the live App; (roll, wake rows, census ids)."""
    roll = Rollup()
    wakes_report: list[dict[str, Any]] = []
    manifest_error = _plan_error(plan)
    if manifest_error is not None:
        roll.error(manifest_error)
        return roll, wakes_report, None
    expectations = _expectations(plan)
    models = (str(plan["judges"][0]), str(plan["judges"][1]))
    judges = Judges(subject.cfg, list(models), Path(output))
    scope = effect_scope(subject)
    if subject.unit != scope.unit:
        roll.error("the composed unit and the case's scope disagree")
        return roll, wakes_report, None
    egress = assert_no_egress(subject)
    evidence = current()
    if evidence is None:
        roll.error("canonical capture unavailable mid-drive")
        return roll, wakes_report, None
    steps = plan.get("steps") or []
    if not any("send" in step for step in steps):
        roll.error("the journey plan sends nothing through real ingress")
        return roll, wakes_report, None
    wake_seconds = (subject.cfg.cognition.cue_quiet_seconds
                    + subject.cfg.cognition.tick_seconds + 300)
    fire_seconds = (subject.cfg.reminders.min_lead_seconds
                    + subject.cfg.heartbeat.tick_seconds * 2 + 60)
    pending: list[dict[str, Any]] = []
    journey_boundary: Any = None
    try:
        journey_boundary = await _drive(
            subject, world=world, steps=steps, scope=scope, judges=judges,
            models=models, expectations=expectations, egress=egress,
            evidence=evidence, output=output, wake_seconds=wake_seconds,
            fire_seconds=fire_seconds, wakes_report=wakes_report,
            pending=pending)
    except Exception as exc:  # noqa: BLE001 — a failed step is ERROR, not a crash
        roll.error(f"the journey failed: {type(exc).__name__}: {exc}")
        # A wake settled when a later step failed still freezes an ERROR
        # row rather than vanishing from the denominator.
        for ctx in pending:
            if "wake" not in ctx:
                continue
            wakes_report.append(_wake_error(
                output, len(wakes_report), ctx["wake"], step="journey",
                message=f"{type(exc).__name__}: {exc}", unit=scope.unit,
                batch=ctx.get("batch"), trajectory=ctx.get("trajectory")))
        pending.clear()
    observed = await _post_drive_census(
        subject, scope=scope, expected_wakes=plan.get("expected_wakes"),
        wakes_report=wakes_report, journey_boundary=journey_boundary,
        output=output, roll=roll, evidence=evidence)
    if not wakes_report and not roll.errors:
        roll.error("the journey produced no wake report")
    return roll, wakes_report, observed



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
    observed: list[Any] | None = None
    try:
        roll, wakes_report, observed = await _run(
            subject, case, plan, world, output)
    except Exception as exc:  # noqa: BLE001 — the run itself is ERROR, not a crash
        roll.error(f"the journey run failed: {type(exc).__name__}: {exc}")
    for wake in wakes_report:
        for verdict in wake.get("expectations", []):
            roll.add(verdict["verdict_obj"])
    if roll.verdict != ERROR and any(w.get("status") == ERROR for w in wakes_report):
        roll.error(WAKE_ERRED)
    # The denominator is the whole scoped production population the census
    # recorded — never the report rows that survived to be bound.
    produced = (observed if observed is not None
                else [w.get("recorded_wake_id") for w in wakes_report])
    bound = _bind_declared_wakes(plan.get("expected_wakes"), produced, roll)
    case_report = _report(case, subject.unit, roll, wakes_report)
    expected = {
        "name": case.name or "unnamed",
        "unit": subject.unit,
        "wakes": bound,
        "expectations": list(plan.get("expectations") or []),
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
