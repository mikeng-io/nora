"""The autonomous journey's later-turn observation: causal binding.

A declared send's observation must prove its own causal path, never
borrow another occasion's effects out of a shared interval:

- ingress records the canonical `bridge_event` admission and its work
  rows;
- a `turn` work row's durable actions are bound by `owner_id = w-<id>`;
- a `stimulus` work row's `stimulus_id` leads to the `stimulus_claim`
  and the wake/session that consumed it;
- the spans stamping `nora.bridge.event_id` — the queue journal and the
  turn's own generation root — own every model call and tool act the
  work itself made.

Everything else in the interval — a reminder's fire, a background pass,
a foreign send — stays as window provenance, never as this turn's
output. A turn's outcome is `delivered` only on a bound confirmed
action, `intentional_non_delivery` only on an explicit production
decision (a `turn` work's disposition or its recorded skip), and
`recorded` when the admission explicitly declined engagement; anything
else is `unconfirmed` and fails closed before any judge.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.subject.effects import (
    frozen_reads,
    read_effects,
    stimulus_session,
)
from nora.subject.journey import (
    declared_envelope,
    declared_source,
    message_from_dict,
)
from scripts.cognition_loop import state as state_mod
from scripts.cognition_loop.availability import availability
from scripts.cognition_loop.judges import (
    judge_expectation,
    judge_history_entry,
    observation_gap,
)
from scripts.cognition_loop.trajectory import (
    bound_turn_events,
    turn_events,
    wake_trajectory,
)
from scripts.cognition_loop.verdicts import ERROR, ExpectationVerdict
from scripts.memory_graph_spike import freeze


def _now_ms() -> int:
    return int(time.time() * 1000)


#: The `turn` work dispositions that explicitly record "no delivery".
_NO_DELIVERY = ("silent", "vetoed")
_SKIP = ("rejected", "queue_full")


def _declared_input(ctx: dict[str, Any]) -> dict[str, Any]:
    """The turn's input as declared; a declared envelope adds its source."""
    declared = {"text": ctx["input"], "event_id": ctx["event_id"],
                "at_ms": ctx["at_ms"]}
    if ctx.get("source") is not None:
        declared["source"] = ctx["source"]
    return declared


def _turn_stimulus_id(work: list[dict[str, Any]]) -> int | None:
    """The stimulus row the send's own `stimulus` work wrote."""
    for w in work:
        if w.get("kind") == "stimulus" and w.get("state") == "done":
            result = w.get("result") or {}
            if type(result.get("stimulus_id")) is int:
                return result["stimulus_id"]
    return None


def _work_decision(w: dict[str, Any]) -> dict[str, Any] | None:
    """The explicit non-delivery decision a settled `turn` work recorded."""
    result = w.get("result") or {}
    if w.get("state") == "intentional_skip":
        return {"work_id": w.get("id"), "disposition": "intentional_skip",
                "reason": result.get("admission") or result.get("reason")}
    if w.get("state") == "done" and result.get("disposition") in _NO_DELIVERY:
        return {"work_id": w.get("id"),
                "disposition": result.get("disposition"),
                "reason": result.get("reason")}
    return None


def _turn_outcome(ctx: dict[str, Any], actions: list[dict[str, Any]],
                  effects: dict[str, Any]) -> dict[str, Any]:
    """The turn's typed outcome, bound to its declared input.

    `delivered` needs a confirmed action owned by this event's own `turn`
    work; `intentional_non_delivery` needs that work's recorded
    abstention; `recorded` needs the admission's own engage=False
    decision. An unowned action in the interval, a storage row, or mere
    quiet proves nothing — the middle is `unconfirmed`.
    """
    work = ctx.get("work") or []
    event = ctx.get("event") or {}
    owners = {f"w-{w.get('id')}" for w in work
              if w.get("kind") == "turn" and w.get("id") is not None}
    bound = [a for a in actions
             if a.get("owner_kind") == "work" and a.get("owner_id") in owners]
    confirmed = [a for a in bound if a.get("state") == "confirmed"]
    settled = [{"kind": w.get("kind"), "state": w.get("state")} for w in work]
    said = [a["request"]["text"] for a in confirmed
            if isinstance(a.get("request"), dict)
            and isinstance(a["request"].get("text"), str)]
    decisions = [d for d in (_work_decision(w) for w in work
                             if w.get("kind") == "turn") if d is not None]
    rows = [{"id": a.get("action_id"), "op": a.get("op"),
             "state": a.get("state"), "owner": a.get("owner_id")}
            for a in bound]
    if confirmed:
        if decisions or any(a.get("state") != "confirmed" for a in bound):
            return {"status": "unconfirmed", "said": said, "work": settled,
                    "actions": rows, "decisions": decisions,
                    "cause": "bound delivery conflicts with unsettled or "
                             "abstaining evidence"}
        ids = {a.get("action_id") for a in confirmed}
        deliveries = [item.get("ref") for item in effects.get("items") or []
                      if item.get("kind") == "delivery"
                      and (item.get("row") or {}).get("action_id") in ids]
        return {"status": "delivered", "said": said,
                "deliveries": deliveries, "actions": rows, "work": settled}
    if decisions:
        return {"status": "intentional_non_delivery", "said": said,
                "work": settled, "decisions": decisions}
    admission = event.get("admission") if isinstance(event, dict) else None
    if not owners and isinstance(admission, dict) \
            and admission.get("engage") is False:
        return {"status": "recorded", "said": said, "work": settled,
                "decision": {"engage": False,
                             "reason": admission.get("reason"),
                             "relevance_gate": admission.get("relevance_gate")}}
    return {"status": "unconfirmed", "said": said, "work": settled,
            "actions": rows, "decisions": decisions,
            "cause": "no bound delivery, no recorded abstention and no "
                     "admission decision to name"}


def _admission_declared(ctx: dict[str, Any], event: dict[str, Any]) -> str | None:
    """The admitted payload matches the complete declared input, or a fixed cause."""
    payload = event.get("payload")
    fields = payload.get("envelope") if isinstance(payload, dict) else None
    if not isinstance(fields, dict):
        return "admission_declared_missing"
    try:
        admitted = declared_envelope(fields)
    except (TypeError, ValueError):
        return "admission_declared_foreign"
    if (json.dumps(admitted.to_fields(), sort_keys=True, allow_nan=False)
            != json.dumps(ctx["envelope"].to_fields(), sort_keys=True, allow_nan=False)
            or declared_source(admitted) != ctx.get("source")
            or message_from_dict(admitted.message or {}).body
            != ctx.get("input")):
        return "admission_declared_foreign"
    return None


def _turn_causes(ctx: dict[str, Any], trajectory: dict[str, Any],
                 earlier_wakes: tuple) -> list[str]:
    """Invalidations for one turn's observation, before any judge request."""
    causes: list[str] = []
    event = ctx.get("event")
    if not isinstance(event, dict) or event.get("state") != "done":
        causes.append("admission_not_done")
    elif event.get("id") != ctx.get("event_id"):
        # Duplicate suppression admits the declared envelope under a
        # different canonical id: the settled rows then belong to another
        # occasion, never to this declared input.
        causes.append("admission_identity_foreign")
    elif ctx.get("envelope") is not None:
        cause = _admission_declared(ctx, event)
        if cause is not None:
            causes.append(cause)
    work = ctx.get("work")
    if (not isinstance(work, list) or not work
            or any(not isinstance(w, dict) or w.get("state")
                   not in ("done", "intentional_skip") for w in work)):
        causes.append("admission_work_unsettled")
    bound = trajectory.get("bound")
    if not isinstance(bound, dict) or bound.get("event_id") != ctx.get("event_id"):
        causes.append("turn_binding_foreign")
    causes.extend(observation_gap(trajectory, earlier_wakes))
    return causes


async def _judge_turn(judges: Any, models: tuple[str, str],
                      expectations: list[Any], index: int, diff: Any,
                      after: Any, trajectory: dict[str, Any],
                      earlier_wakes: tuple) -> list[dict[str, Any]]:
    rows = []
    for expectation in expectations:
        if expectation.turn != index:
            continue
        try:
            verdict_obj = await judge_expectation(
                judges, models, expectation, diff, after, trajectory, earlier_wakes)
        except Exception as exc:  # noqa: BLE001 — a judge failure is ERROR, not a crash
            verdict_obj = ExpectationVerdict(
                verdict=ERROR, answers=(), detail=f"judge: {exc!r}")
        rows.append({
            "expectation": {
                "turn": expectation.turn,
                "polarity": expectation.polarity,
                "kind": expectation.kind,
                "text": expectation.text,
                "check": expectation.check,
            },
            "verdict": verdict_obj.verdict,
            "detail": verdict_obj.detail,
            "verdict_obj": verdict_obj,
            "answers": [asdict(a) for a in verdict_obj.answers],
        })
    return rows


def _freeze_turn(output: Path, ctx: dict[str, Any],
                 trajectory: dict[str, Any], diff: Any,
                 expectations: list[dict[str, Any]], state_after: Any, *,
                 unit: str, step_error: tuple[str, str] | None) -> dict[str, Any]:
    """One turn's private observation file and its report row."""
    from scripts.cognition_loop.run import _wake_status

    index = ctx["index"]
    declared = _declared_input(ctx)
    freeze(output / f"turn-{index}.json", {
        "turn": index, "unit": unit, "at_ms": ctx["at_ms"],
        "declared_input": declared,
        "declared_envelope": (
            ctx["envelope"].to_fields()
            if ctx.get("envelope") is not None else None),
        "error": ({"step": step_error[0], "message": step_error[1]}
                  if step_error is not None else None),
        "trajectory": trajectory,
        "diff": {
            k: {s: [asdict(e) for e in entries]
                for s, entries in (
                    ("created", d.created), ("revised", d.revised),
                    ("retired", d.retired), ("unchanged", d.unchanged))}
            for k, d in diff.kinds.items()
        },
        "expectations": [
            {k: v for k, v in e.items() if k != "verdict_obj"}
            for e in expectations
        ],
    })
    outcome = (trajectory.get("turn_outcome") or {}).get("status")
    return {
        "turn": index, "input": ctx["input"], "at_ms": ctx["at_ms"],
        "declared_input": declared,
        "status": _wake_status(step_error, expectations),
        "step": step_error[0] if step_error is not None else None,
        "error": step_error[1] if step_error is not None else None,
        "outcome": outcome,
        "session_id": trajectory.get("session_id"),
        "trajectory": trajectory,
        "expectations": expectations,
        "_state_before": state_after,
        "_diff": diff,
    }


def _turn_error(output: Path, index: int, ctx: dict[str, Any], *, step: str,
                message: str, unit: str,
                trajectory: dict[str, Any] | None = None) -> dict[str, Any]:
    """An ERROR turn row: the step is named, never folded into another's."""
    declared = _declared_input(ctx)
    freeze(output / f"turn-{index}.json", {
        "turn": index, "unit": unit, "at_ms": ctx["at_ms"],
        "declared_input": declared,
        "declared_envelope": (
            ctx["envelope"].to_fields()
            if ctx.get("envelope") is not None else None),
        "error": {"step": step, "message": message},
        "trajectory": trajectory,
    })
    return {
        "turn": index, "input": ctx["input"], "at_ms": ctx["at_ms"],
        "declared_input": declared,
        "status": ERROR, "step": step, "error": message,
        "outcome": (trajectory or {}).get("turn_outcome", {}).get("status"),
        "session_id": (trajectory or {}).get("session_id"),
        "trajectory": trajectory, "expectations": [],
    }


async def observed_turn(ctx: dict[str, Any], *, subject: Any, driver: Any,
                        evidence: Any, scope: Any, output: Path,
                        judges: Any, models: tuple[str, str],
                        expectations: list[Any], earlier_wakes: tuple,
                        fire_seconds: float, before: Any) -> Any:
    """Close one open send's turn observation into a report row.

    The frozen cut is taken first, so the stimulus claim/session binding,
    the effects and the after-state all read one instant; the bound
    window — this event's own work traces plus its session — is what the
    trajectory and judges see. The whole interval stays in `bound` as
    provenance.
    """
    actions = await driver.wait_turn_settled(
        int(ctx["at_ms"]), seconds=fire_seconds)
    window = turn_events(evidence.since(ctx["cursor"]))
    async with frozen_reads(ctx["engine"]) as reads:
        stimulus_id = _turn_stimulus_id(ctx["work"])
        binding = {"event_id": ctx["event_id"], "stimulus_id": stimulus_id,
                   "wake_id": None, "session_id": None, "wake_outcome": None,
                   "session_conflict": None}
        if stimulus_id is not None:
            binding.update(await stimulus_session(reads, stimulus_id))
        bound, traces = bound_turn_events(
            window, event_id=ctx["event_id"],
            session_id=binding["session_id"])
        effects = await read_effects(
            reads, since=ctx["boundary"], unit=scope.unit,
            rooms=scope.rooms, profile_keys=scope.profile_keys,
            events=bound)
        after = await state_mod.snapshot(
            cast(AsyncEngine, reads), unit=scope.unit, rooms=scope.rooms,
            profile_keys=scope.profile_keys, now_ms=_now_ms(),
            metrics=subject.deps.metrics)
    trajectory = wake_trajectory(
        bound, wake_id=binding["wake_id"],
        session_id=binding["session_id"], tokens=None)
    trajectory["turn"] = ctx["index"]
    trajectory["at_ms"] = int(ctx["at_ms"])
    trajectory["unit"] = scope.unit
    trajectory["unit_rooms"] = list(scope.rooms)
    trajectory["declared_input"] = _declared_input(ctx)
    trajectory["session_settlement"] = trajectory.pop("settlement")
    trajectory["settlement"] = {
        "status": "observed", "event": (ctx["event"] or {}).get("state"),
        "work": [{"kind": w.get("kind"), "state": w.get("state")}
                 for w in ctx["work"]]}
    trajectory["bound"] = {**binding, "attempt_traces": sorted(traces),
                           "window": {
                               "events": len(window),
                               "orders": ([window[0].get("order"),
                                           window[-1].get("order")]
                                          if window else [])}}
    trajectory["availability"] = availability(subject.cfg, trajectory)
    trajectory["effects"] = effects
    trajectory["turn_outcome"] = _turn_outcome(ctx, actions, effects)
    diff = state_mod.diff(before, after)
    causes = _turn_causes(ctx, trajectory, earlier_wakes)
    step_error = ("evidence", "; ".join(causes)) if causes else None
    rows = ([] if step_error is not None else await _judge_turn(
        judges, models, expectations, ctx["index"], diff, after,
        trajectory, earlier_wakes))
    row = _freeze_turn(
        output, ctx, trajectory, diff, rows, after,
        unit=scope.unit, step_error=step_error)
    return row, judge_history_entry(row.get("_diff"), trajectory)


__all__ = [
    "_declared_input",
    "_freeze_turn",
    "_judge_turn",
    "_turn_causes",
    "_turn_error",
    "_turn_outcome",
    "observed_turn",
    "turn_events",
]
