"""The eval run: one case, one sandbox, the case's wakes in order.

Restore at the first wake; for every later wake advance the Record —
never a derived row — then record the wake the production way, run the
deployment's `CognitionSessionRunner` in the live lane (`historical`
nowhere: the close — lesson settlement, Standing fold, carries,
watermark — is the production close), and diff every owned store around
it. Failures at any step are `ERROR`, never `PASS`.
"""

from __future__ import annotations

import contextlib
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nora.domain.culture import character_key, nora_character_key, unit_culture_key
from nora.domain.stimuli import Stimulus, UnitView, WakeReason, decide
from nora.infra.sql.cognition_replay import recorded_wake
from nora.infra.sql.schema import (
    COGNITION_SESSION,
    COGNITION_WAKE,
    COGNITION_WAKE_OUTCOME,
    STIMULUS,
)
from nora.infra.sql.stimuli import SqlStimulusStore, _stimulus
from nora.subject import SubjectSpec, obtain, sandbox_as_of
from nora.subject.eval_evidence import read_events
from nora.subject.memory_workspace import draw_workspace
from scripts.cognition_loop import state as state_mod
from scripts.cognition_loop.advance import advance_record, bump_sequences
from scripts.cognition_loop.egress import assert_no_egress
from scripts.cognition_loop.judges import judge_expectation
from scripts.cognition_loop.trajectory import session_events, wake_trajectory
from scripts.cognition_loop.verdicts import (
    ERROR,
    ExpectationVerdict,
    Rollup,
    rollup,
)
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


async def _recorded_session(source: AsyncEngine, wake_id: int) -> bool:
    """Whether the recorded production wake ran a `cognition_session` row."""
    async with source.connect() as conn:
        return bool((await conn.execute(sa.select(
            sa.func.count()).select_from(COGNITION_SESSION).where(
            COGNITION_SESSION.c.wake_id == wake_id)
        )).scalar_one())


async def _wake_outcome(
    engine: AsyncEngine, wake_id: int,
) -> dict[str, Any] | None:
    """The sandbox's settlement row for this wake, or None if unsettled."""
    async with engine.connect() as conn:
        row = (await conn.execute(sa.select(
            COGNITION_WAKE_OUTCOME.c.session_id,
            COGNITION_WAKE_OUTCOME.c.outcome,
            COGNITION_WAKE_OUTCOME.c.detail,
            COGNITION_WAKE_OUTCOME.c.cause,
        ).where(
            COGNITION_WAKE_OUTCOME.c.wake_id == wake_id)
        )).mappings().one_or_none()
    return dict(row) if row is not None else None


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


#: Overrides the run reserves: arm overrides cannot set these at all —
#: a clash refuses before composition. The measured surface stays
#: configurable (`llm.*`, doctrine, tools); the delivery surface does not.
def reserved_overrides(workspace: str) -> dict[str, Any]:
    """Every override the run owns outright."""
    return {
        # What is measured.
        "cognition.enabled": True,
        "memory.graph.enabled": True,
        "memory.graph.comprehend.enabled": True,
        # Nothing that can reach the world is composed: no fire path
        # (heartbeat, compute, participation, relevance), no social
        # poller or Instagram surface, no preflight or lifecycle loop.
        "computer.enabled": False,
        "computer.scoped.enabled": False,
        "heartbeat.enabled": False,
        "compute.enabled": False,
        "participation.enabled": False,
        "relevance_gate.enabled": False,
        "presence.enabled": False,
        "social.instagram.enabled": False,
        "social.instagram.summons.enabled": False,
        "social.poll.enabled": False,
        # A Discord or WhatsApp adapter resolves its own identity with a
        # live call at composition; off is the only inert shape.
        "platforms.discord.enabled": False,
        "platforms.whatsapp.enabled": False,
        # Every enabled adapter is direct: bridge mode is a delivery
        # path the landed transports cannot land.
        "platforms.signal.mode": "direct",
        "platforms.discord.mode": "direct",
        "platforms.whatsapp.mode": "direct",
        "platforms.instagram.mode": "direct",
        # The case's own Honcho draw, never the deployment's workspace.
        "honcho.workspace_id": workspace,
    }


async def _compose(spec_args: Any, url: str, at_ms: int, room: Any,
                   policy_text: str, identity_text: str,
                   workspace: str) -> Any:
    reserved = reserved_overrides(workspace)
    arm = getattr(spec_args, "overrides", {}) or {}
    clash = sorted(set(arm) & set(reserved))
    if clash:
        raise ValueError(
            f"arm overrides cannot set the run's reserved keys: {clash}")
    spec = SubjectSpec(
        database_url=url, workdir=Path(spec_args.workdir),
        room=(room.platform, room.primary_id), now=_dt(at_ms),
        identity=identity_text, policy=policy_text,
        overrides={**arm, **reserved},
    )
    directory = getattr(spec_args, "channel_directory", None)
    if directory is None:
        return await obtain(spec)
    # The frozen directory stands in for the live reader everywhere a
    # composed subject or the spec's own room check asks for guild
    # channels — the same overlay shape `external_boundaries` uses.
    import sys
    from unittest.mock import patch

    from nora.app import composition_platforms
    from nora.policy import load_policy
    # `nora.subject.obtain` the name is the function; the module it lives in
    # is where `load_policy` is looked up.
    obtain_module = sys.modules["nora.subject.obtain"]
    from nora.platform.discord import roster as discord_roster
    from scripts.cognition_loop.rosters import FrozenRoster
    rosters = getattr(spec_args, "frozen_rosters", None) or {}
    with (patch.object(composition_platforms, "ClientChannelDirectory",
                       lambda *_a, **_k: directory),
          patch.object(discord_roster, "DiscordRoster",
                       lambda *_a, **_k: FrozenRoster("discord", rosters)),
          patch.object(obtain_module, "load_policy",
                       lambda path, **_k: load_policy(
                           path, channels=directory))):
        return await obtain(spec)


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
    #: Per wake index: the production batch the recorded wake covered
    #: and whether the recorded wake ran a session — the comparison the
    #: sandbox's own batch is shown against.
    recorded: dict[int, dict[str, Any]] = {}
    for i, wake_id in enumerate(case.wakes):
        at_ms, view = await recorded_wake(source, wake_id)
        records.append((wake_id, view.unit, at_ms))
        recorded_reasons[i] = await _wake_reason(source, wake_id)
        recorded[i] = {
            "stimulus_ids": sorted(s.id for s in view.pending),
            "self_ids": sorted(
                s.id for s in view.pending if s.kind == "self"),
            "had_session": await _recorded_session(source, wake_id),
        }
    problem = validate_recorded_wakes(records, case.unit)
    if problem is not None:
        roll.error(problem)
        return _report(case, roll, wakes_report)
    instants = {i: at_ms for i, (_, _, at_ms) in enumerate(records)}
    # A guild the unit expands through but the frozen directory cannot
    # answer for is the directory read's ERROR — never a silent deny
    # rendered as "no policy room".
    directory = getattr(args, "channel_directory", None)
    unread = sorted({
        c.guild for c in getattr(policy, "conversations", ())
        if c.unit == case.unit and c.guild
        and (directory is None
             or directory.guild_channels(guild_id=c.guild) is None)
    })
    if unread:
        roll.error(
            "the room directory could not be read for guild(s) "
            f"{unread}, which the case's unit {case.unit!r} expands")
        return _report(case, roll, wakes_report)
    room = next((c for c in policy.rooms() if c.unit == case.unit), None)
    if room is None:
        roll.error(f"case unit {case.unit!r} names no policy room")
        return _report(case, roll, wakes_report)

    # The whole case lives inside ONE Honcho draw — created with its
    # draw marker before any subject composes, deleted when the case
    # exits, whichever way that is.
    draw_ctx = draw_workspace(
        case.name, run_id=getattr(args, "run_id", "run"))
    try:
        draw = await draw_ctx.__aenter__()
    except Exception as exc:  # noqa: BLE001 — the draw's own failure is a case ERROR
        roll.error(f"the case's Honcho draw could not be made: {exc!r}")
        return _report(case, roll, wakes_report)
    try:
        await _wakes(
            args, case, instants, recorded_reasons, recorded, judges,
            evidence, output, room, draw.id, roll, wakes_report)
    finally:
        await draw_ctx.__aexit__(None, None, None)
    for wake in wakes_report:
        for verdict in wake.get("expectations", []):
            roll.add(verdict["verdict_obj"])
    if roll.verdict != ERROR and any(
        w.get("status") == ERROR for w in wakes_report
    ):
        roll.error("a wake ended in ERROR")
    return _report(case, roll, wakes_report)


async def _wakes(
    args: Any, case: Any, instants: dict[int, int],
    recorded_reasons: dict[int, str],
    recorded: dict[int, dict[str, Any]], judges: Any | None,
    evidence: Any, output: Path, room: Any, workspace: str, roll: Rollup,
    wakes_report: list[dict[str, Any]],
) -> None:
    """The recorded sequence in the sandbox, each step naming itself."""
    first = datetime.fromtimestamp(instants[0] / 1000, UTC)
    with TemporaryDirectory(prefix="nora-cognition-loop-") as workdir:
        args.workdir = workdir
        sandbox_ctx = sandbox_as_of(args.database_url, first)
        try:
            url, _seed = await sandbox_ctx.__aenter__()
        except Exception as exc:  # noqa: BLE001 — a restore failure is a case ERROR
            roll.error(
                f"the sandbox restore at wake 0 could not be made: {exc!r}")
            return
        try:
            source_engine = create_async_engine(args.database_url)
            sandbox = create_async_engine(url)
            try:
                try:
                    await bump_sequences(source_engine, sandbox)
                except Exception as exc:  # noqa: BLE001 — ERROR, not a crash
                    roll.error(
                        "the Record sequences could not be bumped "
                        f"past the source: {exc!r}")
                    return
                for i, wake_id in enumerate(case.wakes):
                    at_ms = instants[i]
                    advance = None
                    if i:
                        try:
                            advance = await advance_record(
                                source_engine, sandbox,
                                after_ms=instants[i - 1], at_ms=at_ms,
                            )
                        except Exception as exc:  # noqa: BLE001 — ERROR
                            wakes_report.append(_wake_error(
                                output, i, wake_id, at_ms,
                                recorded_reasons[i], None,
                                step="advance", message=f"{exc!r}",
                                batch=_batch(recorded[i], None)))
                            roll.error(
                                f"advance before wake {i} "
                                f"(recorded {wake_id}) failed: {exc!r}")
                            break
                        if not advance.wrote_only_record():
                            why = (
                                f"advance before wake {i} wrote a "
                                f"derived store: {advance.written}")
                            wakes_report.append(_wake_error(
                                output, i, wake_id, at_ms,
                                recorded_reasons[i], advance,
                                step="advance", message=why,
                                batch=_batch(recorded[i], None)))
                            roll.error(why)
                            break
                    try:
                        with evidence.case(
                            f"wake:{case.unit}:{wake_id}",
                            input={"recorded_wake_id": wake_id},
                            metadata={"wake_id": wake_id, "unit": case.unit,
                                      "at_ms": at_ms},
                        ):
                            report_row = await _run_wake(
                                args, case, i, wake_id, at_ms, url,
                                recorded_reasons[i], judges,
                                evidence, output, room, workspace,
                                advance=advance,
                                recorded_meta=recorded[i],
                            )
                        wakes_report.append(report_row)
                    except Exception as exc:  # noqa: BLE001 — a wake error is ERROR, not a crash
                        roll.error(
                            f"wake {i} (recorded {wake_id}): {exc!r}")
                        break
                    if report_row.get("status") == ERROR:
                        roll.error(
                            f"wake {i} (recorded {wake_id}) failed "
                            f"at {report_row.get('step', 'wake')}: "
                            f"{report_row.get('error')}")
                        break
            finally:
                await sandbox.dispose()
                await source_engine.dispose()
        finally:
            await sandbox_ctx.__aexit__(None, None, None)


async def _run_wake(
    args: Any, case: Any, index: int, wake_id: int, at_ms: int, url: str,
    reason: str, judges: Any | None, evidence: Any, output: Path,
    room: Any, workspace: str, advance: Any = None,
    recorded_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One wake: compose, assert inert, record, run live, diff, judge."""
    subject = None
    try:
        try:
            subject = await _compose(
                args, url, at_ms, room, args.policy.read_text(),
                args.identity.read_text(), workspace=workspace,
            )
            egress = assert_no_egress(subject)
            await index_replay_sandbox(subject, source_url=args.database_url)
            runner = subject.deps.cognition_runner
            evaluator = subject.deps.wake_evaluator
            if runner is None or evaluator is None:
                raise ValueError(
                    "the subject composes no cognition runner/evaluator")
        except Exception as exc:  # noqa: BLE001 — a failed step is ERROR, not a crash
            return _wake_error(
                output, index, wake_id, at_ms, reason, advance,
                step="compose", message=f"{exc!r}",
                batch=_batch(recorded_meta, None))
        offered: list[int] | None = None
        try:
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
                if recorded_meta is not None:
                    view, missing = await _batch_view(
                        engine, view, recorded_meta)
                    if missing:
                        return _wake_error(
                            output, index, wake_id, at_ms, reason, advance,
                            step="advance", batch=_batch(recorded_meta, None),
                            message="the recorded batch is absent from the "
                                    f"sandbox: stimulus ids {missing}")
                #: The stimulus ids the sandbox session was offered —
                #: shown beside the recorded wake's batch in the report.
                offered = sorted(s.id for s in view.pending)
                offered_self = sorted(
                    s.id for s in view.pending if s.kind == "self")
                decided = decide(view, evaluator._policy, at_ms)
                recorded_reason = cast(WakeReason, reason)
                verdict, forced = eval_verdict(
                    view, decided, reason=recorded_reason)
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
                session_id, inspection, no_session, step_error = await _settle(
                    runner, judges, engine, wake.wake_id,
                    unit=case.unit, view=view, at_ms=at_ms,
                    recorded_meta=recorded_meta)
                tokens = (subject.deps.metrics.get(
                    "nora_cognition_unit_tokens_total") - tokens_before)
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
                if inspection is not None:
                    # The inspection assembled the wake's context through the
                    # same `_session_input` a session runs; only the model
                    # call is skipped, so the blocks are real, never None.
                    trajectory["blocks"] = {
                        "system": inspection.get("system"),
                        "agenda": inspection.get("agenda"),
                        "items": inspection.get("items"),
                        "stimuli": inspection.get("stimuli"),
                        "instructions": inspection.get("instructions"),
                    }
                expectations = await _judge(
                    args, case, index, judges, diff, after, trajectory)
                return _freeze_wake(
                    output, index, wake_id, wake, at_ms, reason, session_id,
                    egress, judges is not None, diff, trajectory, expectations,
                    after, advance,
                    batch=_batch(recorded_meta, offered, offered_self),
                    no_session=no_session,
                    step_error=step_error,
                )
            finally:
                await engine.dispose()
        except Exception as exc:  # noqa: BLE001 — a failed step is ERROR, not a crash
            return _wake_error(
                output, index, wake_id, at_ms, reason, advance,
                step="wake", message=f"{exc!r}",
                batch=_batch(recorded_meta, offered))
    finally:
        if subject is not None:
            await _close_subject(subject)


async def _stimuli_by_id(
    engine: Any, unit: str, ids: list[int],
) -> dict[int, Stimulus]:
    """The sandbox's own copies of the recorded batch's stimuli."""
    if not ids:
        return {}
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(STIMULUS).where(
            STIMULUS.c.unit == unit, STIMULUS.c.id.in_(ids),
        ))).all()
    return {int(r.id): _stimulus(r) for r in rows}


async def _batch_view(
    engine: Any, view: UnitView, meta: dict[str, Any],
) -> tuple[UnitView, list[int]]:
    """The recorded batch plus due self stimuli; missing recorded ids second."""
    self_ids = set(meta.get("self_ids") or ())
    batch_ids = [i for i in meta["stimulus_ids"] if i not in self_ids]
    existing = await _stimuli_by_id(engine, view.unit, batch_ids)
    missing = sorted(set(batch_ids) - set(existing))
    if missing:
        return view, missing
    pending = {s.id: s for s in view.pending}
    offered = [pending.get(i, existing[i]) for i in batch_ids]
    offered.extend(s for s in view.pending if s.kind == "self")
    offered.sort(key=lambda s: s.id)
    return replace(view, pending=tuple(offered)), []


async def _settle(
    runner: Any, judges: Any | None, engine: Any, wake_id: int, *,
    unit: str, view: Any, at_ms: int,
    recorded_meta: dict[str, Any] | None,
) -> tuple[Any, dict[str, Any] | None, str | None, tuple[str, str] | None]:
    """Run the wake's session or inspect it, then read its settlement."""
    if judges is None:
        inspection = await runner.inspect(view=view, now_ms=at_ms)
        if inspection is None:
            # No session: honest `no_session` — and under inspect-only a
            # sandbox-miss where production ran one is a wake-step ERROR.
            step_error = (
                ("wake", "the sandbox ran no session: empty agenda "
                 "(production ran one)")
                if recorded_meta and recorded_meta["had_session"] else None)
            return None, None, "empty agenda", step_error
        return None, inspection, None, None
    # The executor's `wake_hook` — the hook the evaluator hands an
    # admitted wake — runs the session on the Dreamer lane.
    await runner.wake_hook(unit=unit, view=view, now_ms=at_ms, wake_id=wake_id)
    outcome = await _wake_outcome(engine, wake_id)
    if outcome is None:
        return (None, None, None,
                ("wake", "the wake recorded no outcome: the Dreamer lane "
                 "never ran it"))
    session_id = outcome["session_id"]
    if outcome["outcome"] != "closed":
        return (session_id, None, None,
                ("wake", f"the session ended {outcome['outcome']}: "
                 f"{outcome['detail'] or 'no detail'}"))
    no_session = (
        outcome["detail"] or "empty agenda") if session_id is None else None
    return session_id, None, no_session, None


async def _judge(
    args: Any, case: Any, index: int, judges: Any | None, diff: Any,
    after: Any, trajectory: dict[str, Any],
) -> list[dict[str, Any]]:
    expectations = []
    if judges is None:
        return expectations
    for expectation in case.expectations:
        if expectation.wake != index:
            continue
        try:
            verdict_obj = await judge_expectation(
                judges, (args.judge_a, args.judge_b),
                expectation, diff, after, trajectory,
            )
        except Exception as exc:  # noqa: BLE001 — a judge failure is ERROR, not a crash
            verdict_obj = ExpectationVerdict(
                verdict=ERROR, answers=(), detail=f"judge: {exc!r}")
        expectations.append({
            "expectation": {
                "polarity": expectation.polarity,
                "kind": expectation.kind,
                "text": expectation.text,
            },
            "verdict": verdict_obj.verdict,
            "verdict_obj": verdict_obj,
            "answers": [asdict(a) for a in verdict_obj.answers],
        })
    return expectations


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
    advance: Any, *, step: str, message: str,
    batch: dict[str, Any] | None = None, no_session: str | None = None,
) -> dict[str, Any]:
    """An ERROR wake entry: the step is named, never folded into another's."""
    freeze(output / f"wake-{index}-{wake_id}.json", {
        "error": {"step": step, "message": message},
        "advance": advance.written if advance is not None else None,
        "batch": batch, "no_session": no_session,
    })
    return {
        "recorded_wake_id": wake_id, "wake_id": None,
        "at_ms": at_ms, "reason": reason, "forced": None,
        "status": ERROR, "step": step, "error": message,
        "session_id": None, "egress": None, "model_calls": None,
        "no_session": no_session, "batch": batch,
        "diff_counts": None, "trajectory": None, "expectations": [],
        "advance": advance.written if advance is not None else None,
    }


async def _close_subject(subject: Any) -> None:
    """The composed subject's teardown, the app's own order. Never raises."""
    deps = subject.deps
    lifecycle = getattr(deps, "computer_lifecycle", None)
    if lifecycle is not None:
        with contextlib.suppress(Exception):
            await lifecycle.close()
    shuts = []
    for name in ("background", "audit", "client"):
        part = getattr(deps, name, None)
        if part is not None:
            shuts.append(
                part.cancel_all() if name == "background"
                else part.aclose() if name == "audit"
                else part.close())
    shuts.extend(c.close() for c in getattr(deps, "model_clients", ()) or ())
    for shut in shuts:
        with contextlib.suppress(Exception):
            await shut


def _freeze_wake(
    output: Path, index: int, wake_id: int, wake: Any, at_ms: int,
    reason: str, session_id: Any, egress: dict[str, Any],
    model_calls: bool, diff: Any, trajectory: dict[str, Any],
    expectations: list[dict[str, Any]], state_after: Any, advance: Any, *,
    batch: dict[str, Any] | None = None,
    no_session: str | None = None,
    step_error: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """The wake's private evidence file and its report row."""
    freeze(output / f"wake-{index}-{wake_id}.json", {
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
        "advance": advance.written if advance is not None else None,
        "_state_before": state_after,
    }


def _rooms(subject: Any, unit: str) -> list[tuple[str, str]]:
    from nora.app.composition_readers import social_unit_rooms
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
