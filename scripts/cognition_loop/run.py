"""The wake report rows the autonomous journey freezes: a wake's private evidence
file, its report row and its derived status. A failed step is `ERROR`, never `PASS`.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from scripts.cognition_loop.trajectory import loop_counts
from scripts.cognition_loop.verdicts import ERROR, rollup
from scripts.memory_graph_spike import freeze


def _batch(
    meta: dict[str, Any] | None, offered: list[int] | None,
    offered_self: list[int] | None = None,
) -> dict[str, Any]:
    """Recorded-versus-offered batch evidence; `None` means unread, never empty."""
    return {
        "recorded": meta["stimulus_ids"] if meta is not None else None,
        "offered": offered,
        "recorded_self": (
            meta.get("self_ids") if meta is not None else None),
        "offered_self": offered_self,
        "production_session": (
            meta["had_session"] if meta is not None else None),
    }


def _wake_status(
    step_error: tuple[str, str] | None,
    expectations: list[dict[str, Any]],
) -> str:
    """Derived wake status: a failed step is ERROR, else the rollup."""
    if step_error is not None:
        return ERROR
    return rollup(*(e["verdict"] for e in expectations))


def _wake_error(
    output: Path, index: int, wake_id: int, at_ms: int, reason: str,
    advance: Any, *, step: str, message: str, unit: Any,
    batch: dict[str, Any] | None = None, no_session: str | None = None,
    trajectory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An ERROR wake entry: the step is named, never folded into another's."""
    freeze(output / f"wake-{index}-{wake_id}.json", {
        "error": {"step": step, "message": message},
        "recorded_wake_id": wake_id, "unit": unit,
        "at_ms": at_ms,
        "advance": advance.written if advance is not None else None,
        "batch": batch, "no_session": no_session,
        "trajectory": trajectory,
    })
    # A step error after the session settled freezes its real candidate
    # identity; the row repeats what the artifact proves, never claims it.
    return {
        "recorded_wake_id": wake_id,
        "wake_id": (trajectory or {}).get("wake_id"),
        "at_ms": at_ms, "reason": reason, "forced": None,
        "status": ERROR, "step": step, "error": message,
        "session_id": (trajectory or {}).get("session_id"),
        "egress": None, "model_calls": None,
        "no_session": no_session, "batch": batch,
        "diff_counts": None, "trajectory": trajectory, "expectations": [],
        "loop": None,
        "advance": advance.written if advance is not None else None,
    }


def _freeze_wake(
    output: Path, index: int, wake_id: int, wake: Any, at_ms: int,
    reason: str, session_id: Any, egress: dict[str, Any],
    model_calls: bool, diff: Any, trajectory: dict[str, Any],
    expectations: list[dict[str, Any]], state_after: Any, advance: Any, *,
    unit: Any,
    batch: dict[str, Any] | None = None,
    no_session: str | None = None,
    step_error: tuple[str, str] | None = None,
    prior_links: set[str] | None = None,
) -> dict[str, Any]:
    """The wake's private evidence file and its report row."""
    loop = loop_counts(trajectory, diff, prior_links or set())
    freeze(output / f"wake-{index}-{wake_id}.json", {
        "recorded_wake_id": wake_id, "unit": unit, "at_ms": at_ms,
        "loop": loop,
        "error": (
            {"step": step_error[0], "message": step_error[1]}
            if step_error is not None else None),
        "no_session": no_session,
        "batch": batch,
        "trajectory": trajectory,
        "advance": advance.written if advance is not None else None,
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
    return {
        "recorded_wake_id": wake_id, "wake_id": wake.wake_id,
        "at_ms": at_ms, "reason": reason, "forced": wake.forced,
        "status": _wake_status(step_error, expectations),
        "step": step_error[0] if step_error is not None else None,
        "error": step_error[1] if step_error is not None else None,
        "no_session": no_session, "batch": batch,
        "session_id": session_id, "egress": egress,
        "model_calls": model_calls,
        "diff_counts": {
            k: {"created": len(d.created), "revised": len(d.revised),
                "retired": len(d.retired), "unchanged": len(d.unchanged)}
            for k, d in diff.kinds.items()
        },
        "trajectory": trajectory,
        "expectations": expectations,
        "loop": loop,
        "advance": advance.written if advance is not None else None,
        "_state_before": state_after,
        "_diff": diff,
    }
