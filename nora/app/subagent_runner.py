"""`SubagentRunner` — runs ONE sub-agent job to a report.

Built on `nora.app.cognition_agent_runner.LiveCognitionAgentRunner`'s shape: one `DreamGuard`
per run (a ledger is spent state), `factory.create_subagent` (the only constructor allowed
to build the agent), `factory.invoke` under `factory.build_limits`, `turn_deadline` and
`asyncio.wait_for`. It differs in three ways:

- **its tools are its own** (`nora.agent.subagent_tools`): a closed list built from the job's
  recorded room facts, with the requester as tagger and no deliverer;
- **its guard has no call ceiling of any kind** (DEC-247): `per_tool_ceilings=False`,
  every tool budget 0 and no memory or pass-wide budget, so what the guard keeps is R-7 (a tool
  it does not hold), R-99 (an argument a tool does not declare) and the result size cap. The
  worker is bounded by the loop's turns and tokens and by a wall clock, and by nothing per tool;
- **its answer is a report**: the final assistant text. Its `document_ids` are the ids of the
  `document_publish` results that came back `ok` (`SubagentProgress`), never parsed out of
  the report, which is prose the model wrote and may name a document that was never stored.

A bound that stops the loop (`LIMIT_STOP_REASONS`) or the wall clock ends the job `failed` with
that reason, never as a normal finish: Strands RETURNS from a limit stop without raising, and a
caller that reads only "it returned" takes a loop that ran out of rounds for one that finished.

The limits live in code and in `[dreamer.budgets.subagent]` and are never written into the
prompt (a cap stated in words makes a model decline work instead of spending less).

**Cancellation propagates.** `asyncio.CancelledError` is not an outcome of this class; the
backend owns it and records the job `cancelled`.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nora.agent import factory
from nora.agent.dream_guard import DreamGuard
from nora.agent.reasoning_strip import ReasoningStripWatch
from nora.agent.subagent_progress import SubagentProgress
from nora.agent.subagent_tools import SKILLS_TOOL, SubagentToolsError, SubagentToolSource
from nora.app.cognition_session import AdaptorBudget
from nora.domain.errors import PartialReplyError
from nora.domain.ports import LaunchRequest
from nora.domain.reasoning_strip import dreamer_path
from nora.domain.stimuli import LIMIT_STOP_REASONS
from nora.domain.subagent import (
    SUBAGENT_KIND,
    SubagentBrief,
    SubagentLaunch,
    job_folder,
    job_title,
    workspace_path,
)
from nora.domain.turn_deadline import turn_deadline
from nora.instructions.frontmatter import parse_document
from nora.language.subagent_render import render_brief
from nora.obs.logging import bind_tag_id
from nora.obs.metrics import Metrics
from nora.obs.tracing import TRACE_KIND, annotate, current_trace_id, span

log = logging.getLogger("nora.app.subagent_runner")

#: What the room is told when a job fails, by reason. Nora composes the message; this is what
#: she is handed to say, so it states the fact and promises nothing.
REASON_TEXT: Mapping[str, str] = {
    "limit_turns": "it ran out of rounds before it finished",
    "limit_total_tokens": "it used up its token allowance before it finished",
    "limit_output_tokens": "it used up its output allowance before it finished",
    "limit_max_tokens": "one of its replies was cut off at the length limit before it finished",
    "timeout": "it ran out of time before it finished",
    "no_report": "it ended without writing a report",
    "tools": "its tools could not be set up",
    "bad_inputs": "an input path it was given was not a path inside the workspace",
    "inputs_unavailable": "its input files could not be copied into its working folder",
    "error": "it stopped on an error",
}


@dataclass(frozen=True)
class SubagentOutcome:
    """How one run ended. `failure_reason` is None for a report; `report` and `document_ids`
    are whatever the run had when it ended (a failed job may still have published)."""

    report: str = ""
    document_ids: tuple[str, ...] = ()
    failure_reason: str | None = None

    @property
    def error(self) -> str | None:
        if self.failure_reason is None:
            return None
        return REASON_TEXT.get(self.failure_reason, self.failure_reason)


def load_system_prompt(doctrine: Path, constraints: Path) -> str:
    """The worker's system prompt: its own doctrine, then `core/constraints.md` as is. Room
    doctrine (voice, belonging, conduct, participation) is about speaking in a room and does
    not apply. The frontmatter is dropped; both files must parse."""
    parts = [
        parse_document(doctrine, required=("kind",)).body.strip(),
        parse_document(constraints, required=("kind",)).body.strip(),
    ]
    return "\n\n".join(parts) + "\n"


class SubagentRunner:
    def __init__(
        self, *, models: Any, retry: factory.RetryPolicy, metrics: Metrics,
        tools: SubagentToolSource, system_prompt: str, skills_dir: Path,
        budget: AdaptorBudget, clock: Callable[[], datetime], tz: str,
        result_caps: Mapping[str, int] | None = None,
    ) -> None:
        self._models = models
        self._retry = retry
        self._metrics = metrics
        self._tools = tools
        self._system_prompt = system_prompt
        self._skills_dir = skills_dir
        self._budget = budget
        self._clock = clock
        self._tz = tz
        self._result_caps = dict(result_caps or {})

    async def run(
        self, *, job_id: str, request: LaunchRequest, requester_name: str,
        progress: SubagentProgress,
    ) -> SubagentOutcome:
        brief = request.payload
        if not isinstance(brief, SubagentBrief):
            return SubagentOutcome(failure_reason="error")
        # The launching turn's id on every log line and audit row this job writes, in THIS
        # task's context only.
        bind_tag_id(request.tag_id or "")
        trace = {
            "nora.subagent.id": job_id, "nora.subagent.kind": SUBAGENT_KIND,
            "nora.tag_id": request.tag_id or "",
        }
        with span(
            "subagent", **{TRACE_KIND: "generation", "nora.dreamer.adaptor": "subagent", **trace},
        ):
            progress.trace_id = current_trace_id()
            outcome = await self._run(
                job_id=job_id, request=request, brief=brief, requester_name=requester_name,
                progress=progress, trace=trace,
            )
            annotate(**{"nora.outcome": outcome.failure_reason or "done"})
            return outcome

    async def _run(
        self, *, job_id: str, request: LaunchRequest, brief: SubagentBrief,
        requester_name: str, progress: SubagentProgress, trace: Mapping[str, str],
    ) -> SubagentOutcome:
        inputs: list[str] = []
        for raw in brief.inputs:
            path = workspace_path(raw)
            if path is None:
                return SubagentOutcome(failure_reason="bad_inputs")
            inputs.append(path)
        room = {
            "platform": request.platform, "conversation_id": request.conversation_id,
            "conversation_type": request.conversation_type,
            "requester_person_id": request.requester_person_id,
        }
        progress.title = job_title({"title": brief.title}, brief.objective)
        progress.clock = self._clock
        try:
            tools = self._tools.build(job_id=job_id, checkpoint_sink=progress, **room)
        except SubagentToolsError:
            log.exception("sub-agent %s: its tools could not be built", job_id)
            return SubagentOutcome(failure_reason="tools")
        folder = job_folder(job_id)
        launch = SubagentLaunch(
            job_id=job_id, platform=request.platform, conversation_id=request.conversation_id,
            conversation_type=request.conversation_type,
            requester_person_id=request.requester_person_id, requester_name=requester_name,
            tag_id=request.tag_id, launched_at=self._clock(), tz=self._tz, folder=folder,
        )
        prompt = render_brief(brief, launch)
        guard = self._guard([getattr(t, "tool_name", "") for t in tools])
        # ONE wall clock over staging and the loop. `cm.expired()` is what says the clock fired:
        # a TimeoutError raised inside (a tool's own, a transport's) is an error, not a timeout.
        try:
            async with asyncio.timeout(self._budget.timeout_seconds) as clock:
                if not await self._stage(job_id, room, folder, inputs):
                    return SubagentOutcome(failure_reason="inputs_unavailable")
                result = await self._invoke(
                    tools=tools, guard=guard, progress=progress, prompt=prompt, trace=trace,
                )
        except TimeoutError:
            if clock.expired():
                log.error(
                    "sub-agent %s timed out after %.0fs", job_id, self._budget.timeout_seconds,
                )
                return self._ended(progress, failure_reason="timeout")
            log.exception("sub-agent %s failed on an inner timeout", job_id)
            return self._ended(progress, failure_reason="error")
        except PartialReplyError:
            log.error("sub-agent %s: a reply hit max_tokens", job_id)
            return self._ended(progress, failure_reason="limit_max_tokens")
        except Exception:
            log.exception("sub-agent %s failed", job_id)
            return self._ended(progress, failure_reason="error")
        stop = getattr(result, "stop_reason", None)
        if stop in LIMIT_STOP_REASONS:
            return self._ended(progress, failure_reason=str(stop))
        report = factory.final_text(result).strip()
        if not report:
            return self._ended(progress, failure_reason="no_report")
        return SubagentOutcome(report=report, document_ids=progress.document_ids)

    @staticmethod
    def _ended(progress: SubagentProgress, *, failure_reason: str) -> SubagentOutcome:
        return SubagentOutcome(
            document_ids=progress.document_ids, failure_reason=failure_reason,
        )

    async def _stage(
        self, job_id: str, room: Mapping[str, str], folder: str, inputs: list[str],
    ) -> bool:
        """Create the job's folder and copy the brief's inputs into it, through the worker's
        OWN `computer_run` (so it is audited as actor `subagent`, under the job's lease key).
        True when it ran and exited 0."""
        copies = "".join(
            f" && cp -a -- {shlex.quote(path)} {shlex.quote(folder)}/" for path in inputs
        )
        command = f"mkdir -p -- {shlex.quote(folder)}{copies}"
        try:
            out = await self._tools.run_computer(job_id=job_id, command=command, **room)
        except Exception:
            log.exception("sub-agent %s: staging its folder failed", job_id)
            return False
        return out.get("status") == "ok" and out.get("exit_code") == 0

    def _guard(self, tool_names: list[str]) -> DreamGuard:
        """One `DreamGuard` for a job, with NO call ceiling of any kind (DEC-247): not per tool,
        not over the memory cluster, not over every verb. The
        names are the membership set (R-7's unknown-tool veto and R-99 still run); `skills` is
        the activation tool the agent factory wires, so it is a member too."""
        names = [*tool_names, SKILLS_TOOL]
        return DreamGuard(
            adaptor="subagent",
            budgets=dict.fromkeys(names, 0),
            metrics=self._metrics,
            result_caps=self._result_caps,
            param_names=self._param_names(names),
            per_tool_ceilings=False,
        )

    def _param_names(self, names: list[str]) -> Mapping[str, frozenset[str]]:
        table = self._tools.tool_param_names()
        return {n: table[n] for n in names if n in table}

    async def _invoke(
        self, *, tools: list[Any], guard: DreamGuard, progress: SubagentProgress, prompt: str,
        trace: Mapping[str, str],
    ) -> Any:
        agent = factory.create_subagent(
            model=self._models.create(), system_prompt=self._system_prompt, tools=tools,
            guard=guard, progress=progress, skills_dir=self._skills_dir,
            reasoning_watch=ReasoningStripWatch(
                path=dreamer_path("subagent"), metrics=self._metrics,
            ),
            trace_attributes=dict(trace), retry=self._retry,
        )
        budget = self._budget
        with turn_deadline(budget.timeout_seconds):
            return await factory.invoke(
                agent, prompt, invocation_state={},
                limits=factory.build_limits(
                    output_tokens=budget.output_tokens, turns=budget.turns,
                    total_tokens=budget.total_tokens,
                ),
            )
