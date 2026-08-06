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
from nora.doctrine.loader import Doctrine
from nora.domain.models import TagContext


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
        doctrine: Doctrine,
        tools: list[Any],
        pre_fill: list[dict[str, Any]],
        guard: ReplyGuard,
        tag_message: str,
        limits: dict[str, Any],
        ctx: TagContext,
        trace_attributes: dict[str, Any],
    ) -> str | None:
        """Run the turn and return Strands' `stop_reason` (R-23).

        This method used to `await factory.invoke(...)` and DISCARD the
        `AgentResult` it returned, so `stop_reason` — the SDK's only signal
        that a turn was cut off by one of R-8's caps rather than finished —
        never left this line. Nothing in `nora/` read it, the loop stopping on
        `limit_total_tokens` raised nothing and logged nothing, and the turn
        was reported as `no_send_call`: identical to the model choosing not to
        speak. The value is returned as a plain `str | None` rather than the
        SDK's `StopReason` literal for the same reason `model` above is `Any`
        — this module does not import `strands`.
        """
        agent = factory.create_agent(
            model=self._model,
            system_prompt=doctrine.text,
            tools=tools,
            pre_fill=pre_fill,
            guard=guard,
            trace_attributes=trace_attributes,
            retry=self._retry,
        )
        result = await factory.invoke(agent, tag_message, invocation_state={}, limits=limits)
        # `isinstance`, not a cast: `factory.invoke` is typed `Any` to keep
        # `strands` out of this module, and an unchecked `Any` returned from a
        # `str | None` signature is precisely what mypy's `warn_return_any`
        # exists to stop. A shape that is not a string is not a stop reason.
        stop_reason = getattr(result, "stop_reason", None)
        return stop_reason if isinstance(stop_reason, str) else None
