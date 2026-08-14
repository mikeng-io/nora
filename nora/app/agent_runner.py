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

Built once at process start around a `nora.agent.factory.ModelSource`, which
builds ONE MODEL PER TAG over the process-wide `AsyncOpenAI` client.

**It used to hold one already-constructed model for the whole process**, and
that was correct only while the model was immutable. Step 5b makes
`tool_choice` per-turn state — `required` until she has spoken, `auto`
after — and the engagement in-flight lock (`nora.engagement.slots`) keys on
`(platform, conversation_id)`, so two conversations run concurrently: one
model would have applied tag A's relaxation to tag B's FIRST call, which
design/16 §2.1 measured as sending nothing 5/6. Audit T2's actual guarantee,
one CLIENT per process, is unchanged — `ModelSource` is built around the same
single client. `tests/test_model_per_tag.py` is the probe.

`models` is typed `Any`, not `nora.agent.factory.ModelSource`, so that this
module does not need to import strands either — only `nora.app.composition`,
which builds the source, ever names the SDK type.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from nora.doctrine.loader import Doctrine
from nora.domain.models import TagContext


class LiveAgentRunner:
    """Satisfies `nora.engagement.stages.invoke.AgentRunner`."""

    def __init__(
        self, *, models: Any, retry: factory.RetryPolicy,
        skills_dir: Path | None = None,
        agent_name: str = factory.DEFAULT_AGENT_NAME,
    ) -> None:
        self._models = models
        # The agent's machine name (cfg.agent.name) — its Strands agent_id and
        # Langfuse trace span. Held per-runner so a cloned agent traces under its
        # own name; the default reproduces today's "nora" span.
        self._agent_name = agent_name
        # Resolved once, from the tier's invocation timeout, and reused for
        # every tag — the numbers are immutable, the strategy built from them
        # is not (see `factory.RetryPolicy`).
        self._retry = retry
        # The on-demand skill layer (DEC-50, activating DEC-48). None leaves it
        # INERT — the offline/eval runners never set it. When the composition
        # root wires it, `factory.create_agent` builds one `AgentSkills` plugin
        # per tag over this operator directory. Held as the resolved path so the
        # per-tag construction stays a pure hand-off, no config object below.
        self._skills_dir = skills_dir

    async def invoke(
        self,
        *,
        doctrine: Doctrine,
        tools: list[Any],
        pre_fill: list[dict[str, Any]],
        guard: ReplyGuard,
        tool_call_log: Any = None,
        tag_message: str,
        limits: dict[str, Any],
        ctx: TagContext,
        trace_attributes: dict[str, Any],
    ) -> tuple[str | None, str]:
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
            # ONE MODEL PER TAG. See this module's docstring: `tool_choice` is
            # per-turn state and two tags overlap.
            model=self._models.create(),
            system_prompt=doctrine.text,
            tools=tools,
            pre_fill=pre_fill,
            guard=guard,
            tool_call_log=tool_call_log,
            trace_attributes=trace_attributes,
            retry=self._retry,
            skills_dir=self._skills_dir,
            agent_name=self._agent_name,
        )
        result = await factory.invoke(agent, tag_message, invocation_state={}, limits=limits)
        # `isinstance`, not a cast: `factory.invoke` is typed `Any` to keep
        # `strands` out of this module, and an unchecked `Any` returned from a
        # `str | None` signature is precisely what mypy's `warn_return_any`
        # exists to stop. A shape that is not a string is not a stop reason.
        stop_reason = getattr(result, "stop_reason", None)
        return (
            stop_reason if isinstance(stop_reason, str) else None,
            _final_text(result),
        )


def _final_text(result: Any) -> str:
    """The model's last PROSE, or "" — the words it wrote instead of calling
    `send_reply` (DEC-93).

    Empty on every healthy turn: a turn that used the tool has already said
    what it had to say, and its final message carries a `toolUse` block rather
    than text. This exists for the turn that ends `end_turn` with a complete,
    group-addressed reply sitting in a `text` block that strict-silence then
    discards — measured live at 2 of 4 tags.

    Defensive throughout (`getattr`, `isinstance`) because this reads a
    third-party result shape on the reply path: a Strands version that renames
    `message` must degrade to "" — the pre-DEC-93 behaviour — never raise into
    a turn that otherwise succeeded.
    """
    message = getattr(result, "message", None)
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    parts = [
        block["text"] for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "\n".join(p for p in parts if p.strip()).strip()
