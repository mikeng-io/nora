"""The eval run: one case, one sandbox, the case's wakes in order.

Restore at the first wake; for every later wake advance the Record —
never a derived row — then record the wake the production way, run the
retained `CognitionSessionRunner` in the sandbox's live lane (`historical`
nowhere: the close — lesson settlement, Standing fold, carries,
watermark — is the production close), and diff every owned store around
it. Failures at any step are `ERROR`, never `PASS`.
"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from nora.domain.stimuli import Stimulus, UnitView, WakeReason
from nora.infra.sql.cognition_replay import recorded_wake
from nora.infra.sql.schema import (
    COGNITION_SESSION,
    COGNITION_WAKE,
    COGNITION_WAKE_OUTCOME,
    STIMULUS,
)
from nora.infra.sql.stimuli import SqlStimulusStore, _stimulus
from nora.subject import Subject, SubjectSpec, obtain, sandbox_as_of
from nora.subject.effects import (
    effect_boundary,
    effect_scope,
    frozen_reads,
    prepare_effect_reads,
    read_effects,
)
from nora.subject.eval_evidence import read_events
from nora.subject.recorded_services import RecordedServices
from scripts.cognition_loop import state as state_mod
from scripts.cognition_loop.advance import advance_record, bump_sequences
from scripts.cognition_loop.availability import availability
from scripts.cognition_loop.egress import assert_no_egress
from scripts.cognition_loop.judges import (
    evidence_gap,
    judge_expectation,
    judge_history_entry,
)
from scripts.cognition_loop.trajectory import (
    loop_counts,
    session_events,
    wake_trajectory,
)
from scripts.cognition_loop.verdicts import (
    ERROR,
    WAKE_ERRED,
    ExpectationVerdict,
    Rollup,
    rollup,
)
from scripts.cognition_loop.wakes import record_eval_wake
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


#: Reads the loop depends on, open unless an arm closes them (design: the loop is open).
OPEN_LOOP: dict[str, Any] = {
    "memory.index.read": True,
    "memory.profile.read": True,
}


async def _compose(spec_args: Any, url: str, at_ms: int, room: tuple[str, str],
                   policy_text: str, identity_text: str) -> Subject:
    return await obtain(SubjectSpec(
        database_url=url, workdir=Path(spec_args.workdir), room=room, now=_dt(at_ms),
        identity=identity_text, policy=policy_text,
        overrides=getattr(spec_args, "overrides", {}) or None,
        environment=getattr(spec_args, "environment", None),
    ), legacy_replay=True)


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
    if getattr(case, "version", 1) != 1:
        roll.error(
            "the recorded replay measures version-1 state expectations; a "
            "version-2 behavior claim anchors a produced turn the replay "
            "never delivers — the autonomous journey measures it")
        return _report(case, roll, wakes_report)
    inputs = getattr(args, "service_inputs", None)
    recording = inputs.corpora.get("cognition-loop", {}).get(case.name) if inputs else None
    if recording is None:
        roll.error("external_responses_missing: " + case.name)
        return _report(case, roll, wakes_report)
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
    selected = next((c for c in policy.rooms() if c.unit == case.unit), None)
    room = (selected.platform, selected.primary_id) if selected is not None else None
    if room is None:
        _, first_view = await recorded_wake(source, case.wakes[0])
        room = next(((s.platform, s.conversation_id) for s in first_view.pending
                     if s.platform and s.conversation_id), None)
    if room is None:
        roll.error("historical room directory or source unavailable for unit " + case.unit)
        return _report(case, roll, wakes_report)
    try:
        await _wakes(
            args, case, instants, recorded_reasons, recorded, judges,
            evidence, output, room, recording, roll, wakes_report)
    except Exception as exc:  # noqa: BLE001 -- dependency failure invalidates the case
        roll.error(f"case services: {type(exc).__name__}: {exc}")
    for wake in wakes_report:
        for verdict in wake.get("expectations", []):
            roll.add(verdict["verdict_obj"])
    if roll.verdict != ERROR and any(
        w.get("status") == ERROR for w in wakes_report
    ):
        roll.error(WAKE_ERRED)
    return _report(case, roll, wakes_report)


async def _wakes(
    args: Any, case: Any, instants: dict[int, int],
    recorded_reasons: dict[int, str],
    recorded: dict[int, dict[str, Any]], judges: Any | None,
    evidence: Any, output: Path, room: tuple[str, str], recording: RecordedServices, roll: Rollup,
    wakes_report: list[dict[str, Any]],
) -> None:
    """The recorded sequence in the sandbox, each step naming itself."""
    prior_links: set[str] = set()
    earlier_wakes: tuple[tuple[Any, dict[str, Any]], ...] = ()
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
            async with recording.open(url, redis=True) as environment:
                args.environment = environment
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
                                    unit=case.unit,
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
                                    unit=case.unit,
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
                                    evidence, output, room,
                                    advance=advance,
                                    recorded_meta=recorded[i],
                                    prior_links=prior_links,
                                    earlier_wakes=earlier_wakes,
                                )
                            wakes_report.append(report_row)
                            loop = report_row.get("loop") or {}
                            prior_links = set(loop.get("link_ids") or ())
                            earlier_wakes += (judge_history_entry(
                                report_row.get("_diff"),
                                report_row.get("trajectory")),)
                        except Exception as exc:  # noqa: BLE001 — a wake error is ERROR, not a crash
                            roll.error(
                                f"wake {i} (recorded {wake_id}): {exc!r}")
                            break
                        if stops_chain(report_row):
                            roll.error(
                                f"wake {i} (recorded {wake_id}) failed "
                                f"at {report_row.get('step', 'wake')}: "
                                f"{report_row.get('error')}")
                            break
                finally:
                    await sandbox.dispose()
                    await source_engine.dispose()
        finally:
            args.environment = None
            await sandbox_ctx.__aexit__(None, None, None)


async def _run_wake(
    args: Any, case: Any, index: int, wake_id: int, at_ms: int, url: str,
    reason: str, judges: Any | None, evidence: Any, output: Path,
    room: tuple[str, str], advance: Any = None,
    recorded_meta: dict[str, Any] | None = None,
    prior_links: set[str] | None = None,
    earlier_wakes: tuple[tuple[Any, dict[str, Any]], ...] = (),
) -> dict[str, Any]:
    """Compose, verify isolation, replay a recorded wake, diff and judge."""
    subject = None
    try:
        try:
            subject = await _compose(
                args, url, at_ms, room, args.policy.read_text(),
                args.identity.read_text(),
            )
            if subject.unit != case.unit:
                raise ValueError("the composed policy assigns the source room to another unit")
            if judges is not None:
                judges = judges.bind(subject.cfg)
            environment = subject.environment
            if environment is None:
                raise ValueError("the replay subject needs an isolated environment")
            egress = assert_no_egress(subject)
            await index_replay_sandbox(subject, source_url=args.database_url)
            runner = subject.deps.cognition_runner
            if runner is None:
                raise ValueError("the subject composes no legacy cognition runner")
        except Exception as exc:  # noqa: BLE001 — a failed step is ERROR, not a crash
            return _wake_error(
                output, index, wake_id, at_ms, reason, advance,
                step="compose", message=f"{exc!r}", unit=case.unit,
                batch=_batch(recorded_meta, None))
        offered: list[int] | None = None
        try:
            engine = create_async_engine(url)
            try:
                await prepare_effect_reads(engine, environment)
                scope = effect_scope(subject)
                stimuli = SqlStimulusStore(engine)
                config = subject.cfg.cognition
                view = await stimuli.view(
                    unit=case.unit, mode="live", now_ms=at_ms,
                    limit=config.batch_max,
                    stale_before_ms=at_ms - int(config.session_timeout_seconds * 1000),
                )
                if recorded_meta is not None:
                    view, missing = await _batch_view(
                        engine, view, recorded_meta)
                    if missing:
                        return _wake_error(
                            output, index, wake_id, at_ms, reason, advance,
                            step="advance", batch=_batch(recorded_meta, None),
                            unit=case.unit,
                            message="the recorded batch is absent from the "
                                    f"sandbox: stimulus ids {missing}")
                #: Stimulus ids the sandbox session was offered.
                offered = sorted(s.id for s in view.pending)
                offered_self = sorted(
                    s.id for s in view.pending if s.kind == "self")
                wake = await record_eval_wake(
                    stimuli, view, cast(WakeReason, reason), at_ms,
                    config=config, metrics=subject.deps.metrics,
                )
                before = await state_mod.snapshot(
                    engine, unit=scope.unit, rooms=scope.rooms,
                    profile_keys=scope.profile_keys, now_ms=at_ms,
                    metrics=subject.deps.metrics,
                )
                tokens_before = subject.deps.metrics.get(
                    "nora_cognition_unit_tokens_total")
                boundary = await effect_boundary(engine)
                session_id, inspection, no_session, step_error = await _settle(
                    runner, judges, engine, wake.wake_id,
                    unit=case.unit, view=view, at_ms=at_ms,
                    recorded_meta=recorded_meta)
                if judges is None and inspection is not None:
                    from scripts.cognition_loop.reads import preflight_reads

                    inspection["read_probes"] = await preflight_reads(
                        runner, case, index, inspection, at_ms)
                got: dict[str, Any] = {"trajectory": {}, "expectations": []}
                try:
                    await _observe(
                        got, args, case, index, judges, engine, subject, scope,
                        before, tokens_before, evidence, session_id, wake,
                        inspection, at_ms, earlier_wakes, step_error, boundary)
                except Exception as exc:  # noqa: BLE001 — the session settled; only its report failed
                    step_error = got.get("step_error") or step_error or (
                        "report", f"{exc!r}")
                    if got.get("diff") is None:
                        return _wake_error(
                            output, index, wake_id, at_ms, reason, advance,
                            step=step_error[0], message=step_error[1],
                            unit=case.unit,
                            batch=_batch(recorded_meta, offered, offered_self),
                            no_session=no_session, trajectory=got["trajectory"])
                return _freeze_wake(
                    output, index, wake_id, wake, at_ms, reason, session_id,
                    egress, judges is not None, got["diff"], got["trajectory"],
                    got["expectations"], got.get("after"), advance,
                    unit=case.unit,
                    batch=_batch(recorded_meta, offered, offered_self),
                    no_session=no_session,
                    step_error=got.get("step_error") or step_error,
                    prior_links=prior_links,
                )
            finally:
                await engine.dispose()
        except Exception as exc:  # noqa: BLE001 — a failed step is ERROR, not a crash
            return _wake_error(
                output, index, wake_id, at_ms, reason, advance,
                step="wake", message=f"{exc!r}", unit=case.unit,
                batch=_batch(recorded_meta, offered))
    finally:
        if subject is not None:
            await _close_subject(subject)


async def _observe(
    got: dict[str, Any], args: Any, case: Any, index: int, judges: Any,
    engine: Any, subject: Any, scope: Any, before: Any, tokens_before: float,
    evidence: Any, session_id: Any, wake: Any, inspection: Any, at_ms: int,
    earlier_wakes: tuple[tuple[Any, dict[str, Any]], ...] = (),
    step_error: tuple[str, str] | None = None,
    boundary: Any = None,
) -> None:
    """Fill `got` step by step, so a later failure keeps what was built."""
    tokens = (subject.deps.metrics.get(
        "nora_cognition_unit_tokens_total") - tokens_before)
    events = session_events(
        read_events(evidence.path), session_id=session_id, wake_id=wake.wake_id)
    trajectory = wake_trajectory(
        events, wake_id=wake.wake_id, session_id=session_id,
        tokens=tokens or None)
    trajectory["at_ms"] = at_ms
    trajectory["unit"] = case.unit
    trajectory["unit_rooms"] = list(scope.rooms)
    trajectory["availability"] = availability(
        getattr(subject, "cfg", None), trajectory)
    if inspection is not None:
        # The inspection assembled the wake's context through the same
        # `_session_input` a session runs; only the model call is skipped.
        trajectory["blocks"] = {k: inspection.get(k) for k in (
            "system", "agenda", "items", "stimuli", "instructions")}
        trajectory["read_probes"] = inspection.get("read_probes", [])
    got["trajectory"] = trajectory
    async with frozen_reads(engine) as reads:
        # One cut for effects and after-state: a commit landing between
        # them could otherwise pair effects from one instant with a state
        # read from another — the judges' one shared image must not tear.
        trajectory["effects"] = await read_effects(
            reads, since=boundary, unit=scope.unit, rooms=scope.rooms,
            profile_keys=scope.profile_keys, events=events,
        )
        got["after"] = after = await state_mod.snapshot(
            cast(AsyncEngine, reads), unit=scope.unit, rooms=scope.rooms,
            profile_keys=scope.profile_keys, now_ms=at_ms,
            metrics=subject.deps.metrics,
        )
    if not subject.environment.guarded:
        subject.environment.check()
    got["diff"] = diff = state_mod.diff(before, after)
    subject.environment.check()
    # A wake whose step failed before it settled, or whose canonical
    # evidence — its own or an inherited antecedent's — is incomplete:
    # the partial evidence is frozen; no judge call is spent.
    gap = evidence_gap(trajectory, earlier_wakes)
    got["step_error"] = step_error or (
        ("evidence", "; ".join(gap)) if gap else None)
    got["expectations"] = (
        [] if got["step_error"] is not None
        else await _judge(
            args, case, index, judges, diff, after, trajectory,
            earlier_wakes))


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
    # Replay hands the admitted wake to the session runner and reads its outcome.
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
    earlier_wakes: tuple[tuple[Any, dict[str, Any]], ...] = (),
) -> list[dict[str, Any]]:
    expectations: list[dict[str, Any]] = []
    if judges is None:
        return expectations
    for expectation in case.expectations:
        if expectation.wake != index:
            continue
        try:
            verdict_obj = await judge_expectation(
                judges, (args.judge_a, args.judge_b),
                expectation, diff, after, trajectory, earlier_wakes,
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
            "detail": verdict_obj.detail,
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


def stops_chain(row: dict[str, Any]) -> bool:
    """A failed step before the session settled stops the later wakes; a
    report-side or judge ERROR leaves the sandbox fit and still rolls up."""
    return row.get("step") not in (None, "report")


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


async def _close_subject(subject: Any) -> None:
    """Close through the subject's production shutdown owner."""
    await subject.aclose()


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


def _report(case: Any, roll: Rollup, wakes: list[dict[str, Any]]) -> dict[str, Any]:
    for wake in wakes:
        for expectation in wake.get("expectations", []):
            expectation.pop("verdict_obj", None)
    return {
        "status": roll.verdict, "mode": "recorded-wake-protocol", "behavior": "UNMEASURED",
        "case": case.name, "unit": case.unit,
        "errors": roll.errors,
        "wakes": [
            {k: v for k, v in w.items() if not k.startswith("_")}
            for w in wakes
        ],
    }
