"""The autonomous journey's plan validation and wake denominators.

The declared plan is the denominator: every actual wake must land and be
accounted, every declared send opens a turn row. `expected_wakes` is
produced by the runtime and reconciled against the census — never
trimmed to the report rows that survived.
"""

from __future__ import annotations

from typing import Any

from evals.cognition_loop.cases import BEHAVIOR_KIND, STATE_KINDS, Expectation
from evals.cognition_loop.verdicts import Rollup
from nora.subject.circle import create_sql_engine
from nora.subject.effects import wake_census
from nora.subject.journey import declared_envelope


def _expectations(plan: dict[str, Any], version: int) -> list[Expectation]:
    if version == 2:
        return [Expectation(
            wake=None, turn=int(raw["turn"]), polarity=str(raw["polarity"]),
            kind=str(raw["kind"]), text=str(raw["text"]),
            check=(str(raw["check"]) if raw.get("check") is not None else None))
            for raw in plan.get("expectations") or ()]
    return [Expectation(wake=int(raw["wake"]), polarity=str(raw["polarity"]),
                        kind=str(raw["kind"]), text=str(raw["text"]))
            for raw in plan.get("expectations") or ()]


def _plan_error(plan: dict[str, Any]) -> str | None:
    """Why this plan cannot bind a denominator, else None — checked before anything runs."""
    version = plan.get("version", 1)
    if version not in (1, 2):
        return "the journey plan declares an unknown version"
    expected = plan.get("expected_wakes")
    if (type(expected) is not int or expected < 0
            or (version == 1 and expected <= 0)):
        return "the journey plan declares no valid expected_wakes"
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        return "the journey plan declares no steps"
    reached = sent = 0
    for step in steps:
        if not isinstance(step, dict):
            return f"a journey step is not an object: {step!r}"
        if "envelope" in step:
            # One input alternative only: a step mixing a declared envelope
            # with send/tagged (or any other key) is refused before run, and
            # declared errors name fixed causes — never the private values.
            if set(step) != {"envelope"}:
                return ("a journey input step names both a declared "
                        "envelope and another input alternative")
            if version != 2:
                return ("a declared envelope step belongs to version-2 "
                        "journey inputs")
            try:
                declared_envelope(step["envelope"])
            except ValueError as exc:
                return str(exc)
            sent += 1
            continue
        if "send" in step:
            if not isinstance(step["send"], str):
                return f"a journey send is not text: {step!r}"
            if "tagged" in step and not isinstance(step["tagged"], bool):
                return f"a journey send's tagged flag is not bool: {step!r}"
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
        if version == 2:
            turn = raw.get("turn")
            if "wake" in raw:
                return (f"journey expectation {position} names a wake; a "
                        "version-2 claim anchors a turn")
            if type(turn) is not int or not 0 <= turn < sent:
                return (f"journey expectation {position} anchors turn {turn!r} "
                        f"outside the {sent} declared sends")
            if raw.get("kind") != BEHAVIOR_KIND:
                return (f"journey expectation {position} kind "
                        f"{raw.get('kind')!r} is not 'behavior'")
            if "check" in raw and not isinstance(raw.get("check"), str):
                return f"journey expectation {position} check is not text"
        else:
            wake = raw.get("wake")
            if type(wake) is not int or not 0 <= wake < expected:
                return (f"journey expectation {position} anchors wake {wake!r} "
                        f"outside the {expected} declared slots")
            if "turn" in raw or "check" in raw:
                return (f"journey expectation {position} names a turn; a "
                        "version-1 claim anchors a wake")
            if raw.get("kind") not in STATE_KINDS:
                return (f"journey expectation {position} kind {raw.get('kind')!r} "
                        "is not a state kind")
        if raw.get("polarity") not in ("must", "must_not"):
            return (f"journey expectation {position} polarity "
                    f"{raw.get('polarity')!r} is not must|must_not")
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
    """The declared wake slots with each produced wake bound at its position."""
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
    engine = create_sql_engine(subject.database_url)
    try:
        return await wake_census(engine)
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


__all__ = [
    "_bind_declared_wakes",
    "_census_reconcile",
    "_expectations",
    "_plan_error",
    "_wake_census",
]
