"""Drive the existing public occasion's turn with a generated, already resolved context."""
from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from nora.agent import factory, tool_choice
from nora.agent.registry import ToolRegistry
from nora.app.agent_runner import LiveAgentRunner
from nora.app.summons import SummonsOccasion
from nora.domain.summons import SummonsContext
from nora.subject.obtain import Subject
from nora.testing import InMemorySummonsLedger


async def turn(subject: Subject, context: SummonsContext) -> tuple[tuple[str, ...], str | None]:
    """The public grant and no-room guard are owned by SummonsOccasion._turn.

    Bridge discovery/publication are outside this generated resolved-input case.
    No room reader or private registry is passed into the occasion.
    """
    from datetime import timedelta

    room_runner = subject.deps.engagement._runner
    models = room_runner._models
    runner = LiveAgentRunner(
        models=factory.ModelSource(client=models._client,
            spec=replace(models._spec, tool_choice=tool_choice.AFTER_SPEAKING)),
        retry=room_runner._retry, skills_dir=None,
        agent_name=subject.cfg.agent.name, metrics=subject.deps.metrics,
    )
    occasion = SummonsOccasion(
        client=None, ledger=InMemorySummonsLedger(), summoners=None,
        persons=subject.deps.persons, doctrine_store=subject.deps.doctrine_store,
        registry=ToolRegistry(content_checks=subject.cfg.content_checks.enabled),
        runner=runner, metrics=subject.deps.metrics, canary=subject.cfg.doctrine.canary,
        handle='noraexample', mode='dry_run', max_age=timedelta(hours=1),
        limits=subject.deps.engagement._limits,
        timeout_seconds=subject.cfg.llm.timeout_seconds, clock=lambda: context.now,
        content_checks=subject.cfg.content_checks.enabled,
    )
    return await occasion._turn(uuid4(), context=context, now=context.now)
