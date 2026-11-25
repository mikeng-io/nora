"""Host-only confirmation. Private inputs and model output never leave this process.

It obtains the model input through the real session context assembly, on the
same subject an eval draw composes, restored at the wake's own instant and
bound to case-owned recorded external services: every external call the
subject or the judge makes crosses a listener this run owns, and an
unrecorded, unmatched or missing response is a named `unavailable`, never a
guess. The judge is the eval layer's own `Judge` bound to the OBTAINED
subject's config, so the verdict rides the same capture and run-budget path
an eval run's does — one call, no retries. It confirms the input situation,
not the later tool trajectory.
Run it on the host with the deployment's own config.toml: endpoint rewrites for
the host's reachability are the subject layer's own (`nora.subject.obtain`).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.find_cognition_anchors import clone_url, room_aliases

if TYPE_CHECKING:
    from nora.config import Config
    from nora.subject.recorded_services import RecordedServices

ROOT = Path(__file__).resolve().parents[1]


#: The scenarios a model judges (7 and 9 are code tests, never anchored).
JUDGED = frozenset({1, 2, 3, 4, 5, 6, 8, 10, 11, 12, 13})


def scenarios() -> dict[int, str]:
    """Use the scenario spec itself, including both must and must-not columns.

    Only the spec's table is read, and its scenario set must be exactly JUDGED
    plus the code-test rows, so a stray table elsewhere can never rewrite a
    spec.
    """
    result = {}
    doc = (ROOT / "evals/COGNITION_SCENARIOS.md").read_text()
    section = doc.split("## The thirteen scenarios", 1)[1].split("\n## ", 1)[0]
    for line in section.splitlines():
        columns = [c.strip() for c in line.split("|")]
        if len(columns) == 7 and columns[1].isdigit():
            result[int(columns[1])] = (f"Scenario: {columns[2]}\nMust: {columns[3]}\n"
                                       f"Must not: {columns[4]}")
    if set(result) != JUDGED | {7, 9}:
        raise ValueError("scenario table changed")
    return result


def candidate(value: object, aliases: dict[str, str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("invalid candidate")
    room, wake, scenario = (value.get(k) for k in ("room", "wake", "scenario"))
    if (not isinstance(room, str) or not re.fullmatch(r"room_[a-z][a-z0-9_]*", room)
            or room not in aliases.values() or type(wake) is not int or wake <= 0
            or type(scenario) is not int or scenario not in JUDGED):
        raise ValueError("invalid candidate")
    return {"room": room, "wake": wake, "scenario": scenario}


def load_recordings(path: Path) -> dict[int, RecordedServices]:
    """`--services`: one recorded-services document per wake it confirms.

    Validating every document BEFORE the first restore means a malformed or
    half-written recording is a run error, not a mid-run `unavailable` that
    reads like the wake's own failure.
    """
    from nora.subject.recorded_services import RecordedServices

    data = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("invalid JSON constant")),
    )
    if not isinstance(data, dict) or not data:
        raise ValueError("--services must map each wake id to its recorded services")
    recordings: dict[int, RecordedServices] = {}
    for key, value in data.items():
        # One canonical ASCII decimal per wake: `int()` alone also accepts
        # "0", "09" and Unicode digits, collapsing distinct strings onto one
        # wake and silently replacing its recording.
        if (not isinstance(key, str)
                or not re.fullmatch(r"[1-9][0-9]*", key)):
            raise ValueError("--services keys must be canonical wake ids")
        recordings[int(key)] = RecordedServices(value)
    return recordings


async def judge(bound, llm: dict, scenario: str, moment: dict) -> str:
    """One verdict through the bound judge's canonical path: the eval's own
    gated, captured `Judge` client — one call, `max_retries=0`, the final wire
    body admitted against the run budget, request and response recorded.
    A retried verdict is a second, different judgement of the same moment.
    """
    from nora.language.confirmation_render import confirmation_prompts

    system, user = confirmation_prompts(
        scenario, json.dumps(moment, ensure_ascii=False, default=str))
    client = bound._client()
    try:
        answer = await bound._complete(client, {
            "model": bound._endpoint()[2],
            "max_tokens": llm["max_tokens"],
            # The production model, at temperature 0: a yes/no verdict
            # with no retry must not flip between runs.
            "temperature": 0,
            "reasoning_effort": llm.get("reasoning_effort", "low"),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}],
            # No response_format: the deployment's route answers 503 to any
            # (json_schema or json_object), so the answer's form is named in
            # the prompt and checked strictly below.
        })
    finally:
        await client.close()
    choice = answer.choices[0] if answer.choices else None
    if (choice is None or choice.finish_reason != "stop"
            or getattr(choice.message, "refusal", None)):
        return "unavailable"
    parsed = json.loads(choice.message.content or "")
    if (not isinstance(parsed, dict) or set(parsed) != {"match"}
            or type(parsed["match"]) is not bool):
        return "unavailable"
    return "match" if parsed["match"] else "no_match"


def _overrides(memory_index: bool) -> dict[str, object]:
    """The corpus's own subject overrides, as `evals.scenarios.experiment_for`
    composes them for `cognition_session` — the deployment's own workspace,
    never a shared eval one."""
    from evals.scenarios import COGNITION_SESSION_OVERRIDES, MEMORY_INDEX_OVERRIDES
    from evals.subject_config import FULL_RESTORE_OVERRIDES

    return {
        **COGNITION_SESSION_OVERRIDES,
        **FULL_RESTORE_OVERRIDES,
        **(dict(MEMORY_INDEX_OVERRIDES) if memory_index else {}),
    }


async def wake_input(sandbox_url: str, at_ms: int, view, room: str,
                     config_dir: Path, subject_config: Path, memory_index: bool,
                     environment) -> tuple[dict, Config]:
    """The session's model input for this room inside the restored sandbox,
    and the bound config a judge routes through. The subject is obtained
    inside the case-owned environment: its configuration, identity, policy
    and workspace are the deployment's own, and every external read is a
    recorded response."""
    import tempfile
    from datetime import UTC, datetime

    from evals.backbone import _close
    from nora.subject import SubjectSpec, obtain

    platforms = {s.platform for s in view.pending if s.conversation_id == room and s.platform}
    if len(platforms) != 1:
        raise ValueError("wake does not identify this room")
    platform = platforms.pop()
    now = datetime.fromtimestamp(at_ms / 1000, UTC)
    with tempfile.TemporaryDirectory() as directory:
        subject = await obtain(SubjectSpec(
            database_url=sandbox_url, workdir=Path(directory), room=(platform, room),
            config=subject_config,
            overrides=_overrides(memory_index), now=now,
            identity=(config_dir / "identity.toml").read_text(),
            policy=(config_dir / "policy.toml").read_text(),
            environment=environment), legacy_replay=True)
        try:
            runner = subject.deps.cognition_runner
            if runner is None or subject.unit != view.unit:
                raise ValueError("session unavailable")
            inspection = await runner.inspect(view=view, now_ms=at_ms)
            if not inspection:
                raise ValueError("empty session")
            engine = create_async_engine(sandbox_url)
            try:
                async with engine.connect() as conn:
                    memory = await memory_state(conn, view.unit, room=f"{platform}:{room}")
            finally:
                await engine.dispose()
            return {"at_ms": at_ms, "room": room, "platform": platform,
                    "system": inspection["system"], "agenda": inspection["agenda"],
                    "memory": memory}, subject.cfg
        finally:
            await _close(subject)


async def memory_state(conn, unit: str, *, room: str) -> dict:
    """The sandbox is already restored as of at_ms; never read today's heads."""
    from nora import memory
    from nora.domain.gatherings import OPEN_STATUSES

    result = {}
    for label, query in {
        "open_notes": "SELECT n.*, r.body FROM note n JOIN note_revision r "
                      "ON r.id = n.head_revision_id "
                      "WHERE n.unit = :unit AND n.retired_at IS NULL",
        "open_events": "SELECT * FROM events WHERE unit_id = :unit "
                       "AND status = ANY(:open_statuses)",
    }.items():
        params = {"unit": unit, "open_statuses": sorted(OPEN_STATUSES)}
        result[label] = [dict(row) for row in
                         (await conn.execute(text(query), params)).mappings()]
    result["profile_claims"] = await memory.wire.profile_snapshot(conn, unit=unit, rooms=(room,))
    return result


async def confirm(source_url: str, config: Path, inputs: list[dict[str, object]],
                  aliases: dict[str, str], recordings: dict[int, RecordedServices],
                  ) -> list[dict[str, object]]:
    url = clone_url(source_url)  # same refusal as finder, before connecting
    import tempfile
    from datetime import UTC, datetime

    from opentelemetry import trace

    from evals.backbone import _prefill_capture
    from evals.rebuilt.grading import Judge
    from evals.subject_config import materialise
    from nora.config import load
    from nora.domain.run_budget import RunBudget, active_run_budget
    from nora.subject.cognition import wake_from_snapshot
    from nora.subject.eval_evidence import RunEvidence, fingerprint, revision
    from nora.subject.sandbox import eval_host_lock, sandbox_as_of

    # The file every subject composes, rebuilt into this run's own private
    # directory — the checkout's shared `config.evaluation.toml` is never
    # one confirmation's to write.
    with tempfile.TemporaryDirectory() as private:
        generated = Path(private) / "config.evaluation.toml"
        await asyncio.to_thread(
            materialise, example=ROOT / "config.example.toml",
            deployment=config, target=generated)
        cfg = await asyncio.to_thread(load, config, require_secrets=False)
        llm = cfg.llm.model_dump()
        memory_index = bool(cfg.memory.index.read)
        descriptions = scenarios()
        rooms = {alias: real for real, alias in aliases.items()}
        # One restore per wake: every candidate on the same wake shares its
        # sandbox and its recorded-services environment.
        by_wake: dict[int, list[dict[str, object]]] = {}
        for item in inputs:
            by_wake.setdefault(cast(int, item["wake"]), []).append(item)
        output = []
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn, conn.begin():
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                actual = await conn.scalar(text("SELECT current_database()"))
                if not str(actual).endswith("_eval"):
                    raise ValueError("not an eval clone")
            with eval_host_lock() as lock_fd:
                evidence = RunEvidence(run_id="confirm-cognition-anchors",
                                       metadata={"candidates": len(inputs),
                                                 "deployment_config_sha256": fingerprint(config),
                                                 **revision(ROOT)},
                                       lock_fd=lock_fd)
                _prefill_capture()
                # One stated bound for the whole run: at most ONE judge call per
                # candidate — an extra or oversized dispatch is a run error.
                budget = RunBudget(
                    max_requests=len(inputs),
                    max_output_tokens=max(1, len(inputs)) * int(llm["max_tokens"]))
                token = active_run_budget.set(budget)
                try:
                    with evidence.capture(trace.get_tracer_provider()):
                        evidence.record("run_budget_started", **budget.as_metadata())
                        for wake_id, items in by_wake.items():
                            # A wake-level failure — no recording, an unrestorable
                            # snapshot or a violated boundary — makes the whole
                            # wake unavailable, including any already judged.
                            verdicts = []
                            try:
                                recording = recordings.get(wake_id)
                                if recording is None:
                                    raise ValueError(
                                        f"wake {wake_id} has no recorded services")
                                at_ms, view = await wake_from_snapshot(url, wake_id)
                                now = datetime.fromtimestamp(at_ms / 1000, UTC)
                                async with (
                                    sandbox_as_of(url, now) as (sandbox_url, _),
                                    recording.open(
                                        sandbox_url, redis=True) as environment,
                                ):
                                    for item in items:
                                        status = "unavailable"
                                        try:
                                            moment, bound_cfg = await wake_input(
                                                sandbox_url, at_ms, view,
                                                rooms[str(item["room"])],
                                                config.parent, generated,
                                                memory_index, environment)
                                            bound = Judge(
                                                base_url="", model="",
                                                timeout_seconds=float(
                                                    llm["timeout_seconds"]),
                                            ).bind(bound_cfg)
                                            status = await judge(
                                                bound, llm,
                                                descriptions[
                                                    int(str(item["scenario"]))],
                                                moment)
                                        except Exception:  # noqa: BLE001 -- private errors stay local
                                            status = "unavailable"
                                        verdicts.append({**item, "result": status})
                            except Exception:  # noqa: BLE001 -- an unrestorable wake is unavailable
                                verdicts = [{**item, "result": "unavailable"}
                                            for item in items]
                            output.extend(verdicts)
                        evidence.record("run_budget_final", **budget.as_metadata())
                        budget.finished()
                finally:
                    active_run_budget.reset(token)
        finally:
            await engine.dispose()
        return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True, help="finder JSONL on this host")
    parser.add_argument("--services", type=Path, required=True,
                        help="JSON mapping each wake id to its recorded-services document")
    args = parser.parse_args()
    previous = logging.root.manager.disable
    try:
        # Dependencies can print/log private bodies, ids or exception strings. Only
        # the validated public projection is emitted after this boundary closes.
        with (open(os.devnull, "w") as sink,
              contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink)):
            logging.disable(sys.maxsize)
            aliases = room_aliases()
            inputs = [candidate(json.loads(line), aliases)
                      for line in args.candidates.read_text().splitlines() if line.strip()]
            recordings = load_recordings(args.services)
            output = asyncio.run(
                confirm(args.source_url, args.config, inputs, aliases, recordings))
    except Exception:  # noqa: BLE001 -- no private exception detail, including configuration
        print("anchor confirmation unavailable; check inputs and config privately",
              file=sys.stderr)
        return 2
    finally:
        logging.disable(previous)
    for row in output:
        print(json.dumps(row, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
