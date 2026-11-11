"""The eval run: one case, one sandbox, the case's wakes in order.

Restore at the first wake; for every later wake advance the Record —
never a derived row — then record the wake the production way, run the
deployment's `CognitionSessionRunner` in the live lane (`historical`
nowhere: the close — lesson settlement, Standing fold, carries,
watermark — is the production close), and diff every owned store around
it. Failures at any step are `ERROR`, never `PASS`.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nora.domain.culture import character_key, nora_character_key, unit_culture_key
from nora.domain.stimuli import WakeReason, decide
from nora.infra.sql.cognition_replay import recorded_wake
from nora.infra.sql.schema import COGNITION_WAKE
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.subject import SubjectSpec, obtain, sandbox_as_of
from nora.subject.eval_evidence import read_events
from nora.subject.memory_workspace import draw_workspace_id
from nora.subject.sandbox_signal import landed_signal_sends
from scripts.cognition_loop import state as state_mod
from scripts.cognition_loop.advance import advance_record, bump_sequences
from scripts.cognition_loop.egress import assert_no_egress
from scripts.cognition_loop.judges import judge_expectation
from scripts.cognition_loop.trajectory import session_events, wake_trajectory
from scripts.cognition_loop.verdicts import ERROR, Rollup
from scripts.cognition_loop.wakes import eval_verdict, record_eval_wake
from scripts.memory_graph_spike import freeze, index_replay_sandbox


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


async def _wake_reason(source: AsyncEngine, wake_id: int) -> str:
    async with source.connect() as conn:
        row = (await conn.execute(sa.select(
            COGNITION_WAKE.c.reason).where(COGNITION_WAKE.c.id == wake_id)
        )).scalar_one_or_none()
    if row is None:
        raise ValueError(f"case wake {wake_id} is not in the source")
    return str(row)


def _profile_keys(subject: Any, unit: str) -> list[str]:
    """The unit's profile keys: every room's people, Nora, culture."""
    allowlist = subject.deps.config_reload.policy
    persons: set[str] = set()
    for convo in allowlist.rooms():
        if convo.unit != unit:
            continue
        persons |= (set(subject.deps.persons.by_person)
                    if convo.persons is None else set(convo.persons))
    return [
        nora_character_key(unit), unit_culture_key(unit),
        *(character_key(unit, p) for p in sorted(persons)),
    ]


async def _compose(spec_args: Any, url: str, at_ms: int, room: Any,
                   policy_text: str, identity_text: str,
                   workspace: str = "") -> Any:
    overrides = {
        "cognition.enabled": True,
        "memory.graph.enabled": True,
        "memory.graph.comprehend.enabled": True,
        # Workers that could reach the world are never composed; the
        # no-egress assertion refuses anything that still is.
        "computer.enabled": False,
        "computer.scoped.enabled": False,
    }
    if workspace:
        # Each case writes its own Honcho workspace (a draw inside the
        # eval namespace), never the deployment's or a shared sibling's.
        overrides["honcho.workspace_id"] = workspace
    overrides.update(getattr(spec_args, "overrides", {}))
    return await obtain(SubjectSpec(
        database_url=url, workdir=Path(spec_args.workdir),
        room=(room.platform, room.primary_id), now=_dt(at_ms),
        identity=identity_text, policy=policy_text,
        overrides=overrides,
    ))


def validate_recorded_wakes(
    records: list[tuple[int, str, int]], unit: str,
) -> str | None:
    """Every recorded wake is the case's unit and strictly later than the
    previous — a case that names another unit's wake, or an order the
    source contradicts, is an error, never a run."""
    previous_ms: int | None = None
    for wake_id, wake_unit, at_ms in records:
        if wake_unit != unit:
            return (
                f"recorded wake {wake_id} belongs to unit {wake_unit!r}, "
                f"not the case's {unit!r}"
            )
        if previous_ms is not None and at_ms <= previous_ms:
            return (
                f"recorded wake {wake_id} at {at_ms} does not follow "
                f"the previous wake at {previous_ms}"
            )
        previous_ms = at_ms
    return None


async def run_case(
    args: Any, case: Any, source: AsyncEngine, judges: Any | None,
    policy: Any, evidence: Any, output: Path,
) -> dict[str, Any]:
    """One case end to end. `judges` is None under `--inspect-only`."""
    roll = Rollup()
    wakes_report: list[dict[str, Any]] = []
    records: list[tuple[int, str, int]] = []
    recorded_reasons: dict[int, str] = {}
    for i, wake_id in enumerate(case.wakes):
        at_ms, view = await recorded_wake(source, wake_id)
        records.append((wake_id, view.unit, at_ms))
        recorded_reasons[i] = await _wake_reason(source, wake_id)
    problem = validate_recorded_wakes(records, case.unit)
    if problem is not None:
        roll.error(problem)
        return _report(case, roll, wakes_report)
    instants = {i: at_ms for i, (_, _, at_ms) in enumerate(records)}
    room = next((c for c in policy.rooms() if c.unit == case.unit), None)
    if room is None:
        roll.error(f"case unit {case.unit!r} names no policy room")
        return _report(case, roll, wakes_report)

    deployment = getattr(args, "deployment_workspace", "")
    # One draw workspace per CASE — its writes carry into its later wakes;
    # the nonce is drawn once, not per wake.
    workspace = (
        draw_workspace_id(deployment, run_id=getattr(args, "run_id", "run"),
                          case=case.name)
        if deployment else ""
    )
    first = datetime.fromtimestamp(instants[0] / 1000, UTC)
    with TemporaryDirectory(prefix="nora-cognition-loop-") as workdir:
        args.workdir = workdir
        async with sandbox_as_of(args.database_url, first) as (url, _seed):
            source_engine = create_async_engine(args.database_url)
            sandbox = create_async_engine(url)
            try:
                await bump_sequences(source_engine, sandbox)
                for i, wake_id in enumerate(case.wakes):
                    at_ms = instants[i]
                    if i:
                        advance = await advance_record(
                            source_engine, sandbox,
                            after_ms=instants[i - 1], at_ms=at_ms,
                        )
                        if not advance.wrote_only_record():
                            roll.error(
                                f"advance before wake {i} wrote a derived "
                                f"store: {advance.written}"
                            )
                            break
                    try:
                        with evidence.case(
                            f"wake:{case.unit}:{wake_id}",
                            input={"recorded_wake_id": wake_id},
                            metadata={"wake_id": wake_id, "unit": case.unit,
                                      "at_ms": at_ms},
                        ):
                            wakes_report.append(await _run_wake(
                                args, case, i, wake_id, at_ms, url,
                                recorded_reasons[i], judges, evidence, output,
                                room, workspace,
                            ))
                    except Exception as exc:  # noqa: BLE001 — a wake error is ERROR, not a crash
                        roll.error(f"wake {i} (recorded {wake_id}): {exc!r}")
                        break
            finally:
                await sandbox.dispose()
                await source_engine.dispose()
    for wake in wakes_report:
        for verdict in wake.get("expectations", []):
            roll.add(verdict["verdict_obj"])
    if roll.verdict != ERROR and any(
        w.get("status") == ERROR for w in wakes_report
    ):
        roll.error("a wake ended in ERROR")
    return _report(case, roll, wakes_report)


async def _run_wake(
    args: Any, case: Any, index: int, wake_id: int, at_ms: int, url: str,
    reason: str, judges: Any | None, evidence: Any, output: Path,
    room: Any, workspace: str,
) -> dict[str, Any]:
    """One wake: compose, assert no egress, record, run live, diff, judge."""
    with landed_signal_sends():
        subject = await _compose(
            args, url, at_ms, room, args.policy.read_text(),
            args.identity.read_text(), workspace=workspace,
        )
    egress = assert_no_egress(subject)
    await index_replay_sandbox(subject, source_url=args.database_url)
    runner = subject.deps.cognition_runner
    evaluator = subject.deps.wake_evaluator
    if runner is None or evaluator is None:
        raise ValueError("the subject composes no cognition runner/evaluator")
    engine = create_async_engine(url)
    try:
        rooms = _rooms(subject, case.unit)
        stimuli = SqlStimulusStore(engine)
        view = await stimuli.view(
            unit=case.unit, mode="live", now_ms=at_ms,
            limit=evaluator._policy.batch_max,
            stale_before_ms=at_ms - int(
                evaluator._policy.session_timeout_seconds * 1000),
        )
        decided = decide(view, evaluator._policy, at_ms)
        recorded_reason = cast(WakeReason, reason)
        verdict, forced = eval_verdict(view, decided, reason=recorded_reason)
        wake = await record_eval_wake(
            evaluator, view, verdict,
            verdict.reason or recorded_reason, at_ms,
            forced=forced,
        )
        before = await state_mod.snapshot(
            engine, unit=case.unit, rooms=rooms,
            profile_keys=_profile_keys(subject, case.unit), now_ms=at_ms,
            metrics=subject.deps.metrics,
        )
        tokens_before = subject.deps.metrics.get(
            "nora_cognition_unit_tokens_total")
        session_id = None
        if judges is not None:
            outcome = await runner.on_wake(
                unit=case.unit, view=view, now_ms=at_ms,
                wake_id=wake.wake_id,
            )
            if outcome is not None:
                session_id = outcome.session_id
        else:
            await runner.inspect(view=view, now_ms=at_ms)
        tokens = (subject.deps.metrics.get("nora_cognition_unit_tokens_total")
                  - tokens_before)
        after = await state_mod.snapshot(
            engine, unit=case.unit, rooms=rooms,
            profile_keys=_profile_keys(subject, case.unit), now_ms=at_ms,
            metrics=subject.deps.metrics,
        )
        diff = state_mod.diff(before, after)
        events = session_events(
            read_events(evidence.path), session_id=session_id,
            wake_id=wake.wake_id,
        )
        trajectory = wake_trajectory(
            events, wake_id=wake.wake_id, session_id=session_id,
            tokens=tokens or None,
        )
        expectations = []
        if judges is not None:
            for expectation in case.expectations:
                if expectation.wake != index:
                    continue
                verdict_obj = await judge_expectation(
                    judges, (args.judge_a, args.judge_b), expectation,
                    diff, after, trajectory,
                )
                expectations.append({
                    "expectation": expectation.text,
                    "verdict": verdict_obj.verdict,
                    "verdict_obj": verdict_obj,
                    "answers": [asdict(a) for a in verdict_obj.answers],
                })
        return _freeze_wake(
            output, index, wake_id, wake, at_ms, reason, session_id,
            egress, judges is not None, diff, trajectory, expectations, after,
        )
    finally:
        try:
            from evals.backbone import _close
            await _close(subject)
        finally:
            await engine.dispose()


def _freeze_wake(
    output: Path, index: int, wake_id: int, wake: Any, at_ms: int,
    reason: str, session_id: str | None, egress: dict[str, Any],
    model_calls: bool, diff: Any, trajectory: dict[str, Any],
    expectations: list[dict[str, Any]], state_after: Any,
) -> dict[str, Any]:
    """The wake's private evidence file and its report row."""
    freeze(output / f"wake-{index}-{wake_id}.json", {
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
    return {
        "recorded_wake_id": wake_id, "wake_id": wake.wake_id,
        "at_ms": at_ms, "reason": reason, "forced": wake.forced,
        "status": "PASS",
        "session_id": session_id, "egress": egress,
        "model_calls": model_calls,
        "diff_counts": {
            k: {"created": len(d.created), "revised": len(d.revised),
                "retired": len(d.retired), "unchanged": len(d.unchanged)}
            for k, d in diff.kinds.items()
        },
        "trajectory": trajectory,
        "expectations": expectations,
        "_state_before": state_after,
    }


def _rooms(subject: Any, unit: str) -> list[tuple[str, str]]:
    from nora.app.composition import social_unit_rooms
    allowlist = subject.deps.config_reload.policy
    return list(social_unit_rooms(allowlist).get(unit, ()))


def _report(case: Any, roll: Rollup, wakes: list[dict[str, Any]]) -> dict[str, Any]:
    for wake in wakes:
        for expectation in wake.get("expectations", []):
            expectation.pop("verdict_obj", None)
    return {
        "status": roll.verdict,
        "case": case.name, "unit": case.unit,
        "errors": roll.errors,
        "wakes": [
            {k: v for k, v in w.items() if not k.startswith("_")}
            for w in wakes
        ],
    }
