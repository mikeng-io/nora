"""`LiveAgentRunner` — the real `nora.engagement.stages.invoke.AgentRunner`.

Per spec §10's test-seam policy, faking the model for tests is a fake
`AgentRunner` passed to `EngagementPipeline`'s constructor (see
`tests/integration/harness.py`'s `FakeAgentRunner` and
`tests/unit/test_engagement_pipeline.py`'s `ScriptedRunner`) — never an
`_invoke_fn` hook the way the deleted `Handler` did it. This class is the
production side of that seam: it wraps `nora.agent.factory`'s Strands
plumbing so `nora.engagement.stages.invoke` never has to import `strands`
itself (only `nora.agent.**` may — `tests/test_boundaries.py::
test_strands_import_boundary`).

Built once at process start around an already-constructed model provider
(never per tag — see `nora.agent.factory`'s own docstring on why the
underlying `AsyncOpenAI` client is a process-wide singleton). `model` is
typed `Any`, not `strands.models.openai.OpenAIModel`, so that this module
does not need to import strands either — only `nora.app.composition`, which
builds one via `nora.agent.factory.create_model_provider`, ever constructs
one.
"""

from __future__ import annotations

from typing import Any

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from nora.domain.models import TagContext
from nora.persona.loader import Persona


class LiveAgentRunner:
    """Satisfies `nora.engagement.stages.invoke.AgentRunner`."""

    def __init__(self, *, model: Any, retry: factory.RetryPolicy) -> None:
        self._model = model
        # Resolved once, from the tier's invocation timeout, and reused for
        # every tag — the numbers are immutable, the strategy built from them
        # is not (see `factory.RetryPolicy`).
        self._retry = retry

    async def invoke(
        self,
        *,
        persona: Persona,
        tools: list[Any],
        pre_fill: list[dict[str, Any]],
        guard: ReplyGuard,
        tag_message: str,
        limits: dict[str, Any],
        ctx: TagContext,
        trace_attributes: dict[str, Any],
    ) -> None:
        agent = factory.create_agent(
            model=self._model,
            system_prompt=persona.text,
            tools=tools,
            pre_fill=pre_fill,
            guard=guard,
            trace_attributes=trace_attributes,
            retry=self._retry,
        )
        await factory.invoke(agent, tag_message, invocation_state={}, limits=limits)
