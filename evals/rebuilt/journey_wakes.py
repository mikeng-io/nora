"""The autonomous journey's production-wake finalization.

Each produced wake gets the same canonical finalization the
recorded-wake runner uses — session events, effects inside the case's
execution boundary, state diff — and the evidence gate every judge door
shares.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.subject.effects import (
    cognition_wake_outcomes,
    cognition_wakes,
    frozen_reads,
    observed_effects,
    offered_stimuli,
    read_effects,
)
from nora.subject.eval_evidence import read_events, validate_events
from scripts.cognition_loop import state as state_mod
from scripts.cognition_loop.accounting import _canonical_outcome
from scripts.cognition_loop.availability import availability
from scripts.cognition_loop.cases import Expectation
from scripts.cognition_loop.judges import evidence_gap, judge_expectation
from scripts.cognition_loop.trajectory import session_events, wake_trajectory
from scripts.cognition_loop.verdicts import ERROR, ExpectationVerdict


def _now_ms() -> int:
    return int(time.time() * 1000)


def _batch(offered: list[int], offered_self: list[int],
           had_session: bool | None) -> dict[str, Any]:
    """Offered-versus-recorded evidence for the report row."""
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
    """The production wake's private evidence file and its report row."""
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
                     session_id: Any,
                     earlier_wakes: tuple) -> list[str]:
    """Invalidations for one wake's canonical evidence, before any judge request."""
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
    if observed_effects(trajectory.get("effects")):
        state, _ref = _canonical_outcome(
            trajectory, unit=scope.unit,
            wake_id=wake.get("id") if isinstance(wake, dict) else None,
            session_id=session_id)
        if state != "closed":
            causes.append(f"settlement_{state}")
    # The one prerequisite every judge door shares — this wake's evidence
    # and the whole earlier history its judges would inherit.
    causes.extend(evidence_gap(trajectory, earlier_wakes))
    return causes


async def _finalize(index: int, ctx: dict[str, Any], *, subject: Any,
                    scope: Any, output: Path, judges: Any, models: tuple[str, str],
                    expectations: list[Expectation], earlier_wakes: tuple,
                    prior_links: set[str], egress: dict[str, Any],
                    evidence_path: Path, before: Any) -> dict[str, Any]:
    """Canonical finalization for one production wake, then validity and both judges."""
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
    # One cut: effects, snapshot and diff share a REPEATABLE READ
    # transaction, so a commit landing mid-measurement cannot tear the
    # image the judges read.
    async with frozen_reads(ctx["engine"]) as reads:
        trajectory["effects"] = await read_effects(
            reads, since=ctx["boundary"], unit=scope.unit,
            rooms=scope.rooms, profile_keys=scope.profile_keys, events=subset)
        after = await state_mod.snapshot(
            cast(AsyncEngine, reads), unit=scope.unit, rooms=scope.rooms,
            profile_keys=scope.profile_keys, now_ms=_now_ms(),
            metrics=subject.deps.metrics)
    diff = state_mod.diff(before, after)
    causes = _evidence_causes(
        ctx, subset, trajectory, scope=scope, session_id=session_id,
        earlier_wakes=earlier_wakes)
    step_error = ("evidence", "; ".join(causes)) if causes else None
    rows = ([] if step_error is not None else await _judge(
        judges, models, expectations, index, diff, after,
        trajectory, earlier_wakes))
    return _freeze_wake(
        output, index, wake, session_id, egress, diff, trajectory, rows,
        after, unit=scope.unit, batch=ctx["batch"], no_session=no_session,
        step_error=step_error, prior_links=prior_links)


async def _offered(engine: Any, unit: str, at_ms: int) -> tuple[list[int], list[int]]:
    return await offered_stimuli(engine, unit, at_ms)


async def _await_wake(driver: Any, *, engine: Any, scope: Any,
                      pending: list[dict[str, Any]], seen_wakes: int,
                      wanted: int, seconds: float) -> int:
    """Wait out the count, bind the one new wake, return rows seen."""
    await driver.wait_closed_wakes(wanted, seconds=seconds)
    wakes = await cognition_wakes(engine)
    outcomes = await cognition_wake_outcomes(engine)
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


__all__ = [
    "_await_wake",
    "_batch",
    "_evidence_causes",
    "_finalize",
    "_freeze_wake",
    "_judge",
    "_offered",
    "_wake_error",
]
