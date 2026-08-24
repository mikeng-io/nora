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

**`_final_text` used to live at the bottom of this file and is now
`nora.agent.factory.final_text`.** It reads the SDK's `AgentResult.message`
content blocks, which is a Strands shape, in a module that must not import
Strands — and the Dreamer needs the identical extraction while `nora.agent` may
not import `nora.app`. Promoting it was the alternative to a second copy of a
defensive parser; see that function's docstring for the full argument.
"""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Any

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from nora.agent.tag_trace_extract import extract_tag_trace
from nora.doctrine.loader import Doctrine
from nora.domain.models import TagContext
from nora.domain.tag_trace import TagTrace
from nora.obs.metrics import Metrics

# The default cap on how many characters of `agent.messages` a `TagTrace`
# captures (`nora.agent.tag_trace_extract.cap_messages`'s `max_chars`).
# Spec §6.5 leaves the number itself unspecified for Phase 1 (a real `[debrief]`
# config knob is future work, gated on §14) — this constant is that stand-in,
# sized the same as `nora.agent.tag_trace_extract`'s own test fixtures.
DEFAULT_MAX_TRACE_CHARS = 10_000


class LiveAgentRunner:
    """Satisfies `nora.engagement.stages.invoke.AgentRunner`."""

    def __init__(
        self, *, models: Any, retry: factory.RetryPolicy,
        skills_dir: Path | None = None,
        agent_name: str = factory.DEFAULT_AGENT_NAME,
        max_trace_chars: int = DEFAULT_MAX_TRACE_CHARS,
        metrics: Metrics | None = None,
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
        # `TagTrace` extraction (spec §6.1/§8.1) — cap and metrics sink for
        # `nora.agent.tag_trace_extract.extract_tag_trace`.
        self._max_trace_chars = max_trace_chars
        # Falls back to a private registry when the caller has none to share.
        # Every production caller (the composition root) passes the process
        # metrics so `nora_tagtrace_truncations_total` lands where `/metrics`
        # actually reads it; a fallback instance is only ever read by tests
        # and evals that do not assert on that counter.
        self._metrics = metrics if metrics is not None else Metrics()

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
    ) -> tuple[str | None, str, TagTrace]:
        """Run the turn and return Strands' `stop_reason` (R-23) plus a
        `TagTrace` of what happened.

        This method used to `await factory.invoke(...)` and DISCARD the
        `AgentResult` it returned, so `stop_reason` — the SDK's only signal
        that a turn was cut off by one of R-8's caps rather than finished —
        never left this line. Nothing in `nora/` read it, the loop stopping on
        `limit_total_tokens` raised nothing and logged nothing, and the turn
        was reported as `no_send_call`: identical to the model choosing not to
        speak. The value is returned as a plain `str | None` rather than the
        SDK's `StopReason` literal for the same reason `model` above is `Any`
        — this module does not import `strands`.

        `baseline` is captured BEFORE `factory.invoke` runs — `agent.messages`
        already holds the system prompt and pre-fill by the time
        `factory.create_agent` returns, and `extract_tag_trace` slices from
        `baseline` onward, so a baseline taken after the call would miss
        nothing structural but a baseline taken any later (e.g. after a retry
        rebuilt the agent) would. `transcript` is left empty here: this class
        has no access to the assembled window, only `nora.engagement.stages.
        invoke.run` does (via `ctx`), and fills it in. `outcome` is
        PROVISIONAL — `"send_reply"` — because this layer has no visibility
        into delivery, recovery or veto; that decision belongs to
        `nora.engagement.pipeline`, made after `deliver.check_outcome` runs
        downstream, and corrects this via `dataclasses.replace` once the real
        outcome is known — the same handoff Task 3 left for `created_at`,
        which IS corrected here (frozen dataclass, so `dataclasses.replace`
        rather than field assignment) because the real timestamp is already
        knowable at this point.
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
        baseline = len(agent.messages)
        result = await factory.invoke(agent, tag_message, invocation_state={}, limits=limits)
        # `isinstance`, not a cast: `factory.invoke` is typed `Any` to keep
        # `strands` out of this module, and an unchecked `Any` returned from a
        # `str | None` signature is precisely what mypy's `warn_return_any`
        # exists to stop. A shape that is not a string is not a stop reason.
        stop_reason = getattr(result, "stop_reason", None)
        stop_reason = stop_reason if isinstance(stop_reason, str) else None
        trace = extract_tag_trace(
            agent, baseline=baseline, tag_id=ctx.tag_id,
            conversation_id=ctx.message.conversation_id, transcript=(),
            outcome="send_reply", max_chars=self._max_trace_chars,
            metrics=self._metrics,
        )
        # `TagTrace` is frozen (`nora/domain/tag_trace.py`) — `dataclasses.
        # replace` produces a corrected instance rather than mutating a field
        # that does not allow it. Stamped here, not left at Task 3's `0`
        # placeholder: this is the earliest point the real clock is knowable
        # and the trace must carry it before going anywhere persistent.
        trace = dataclasses.replace(trace, created_at=int(time.time() * 1000))
        return (
            stop_reason,
            factory.final_text(result),
            trace,
        )
