"""The ONLY module that constructs Strands objects (spec C.7).

One `AsyncOpenAI` client for the whole process (audit T2) — never one per tag.
**One `OpenAIModel` PER TAG, though** (DEC-42, `ModelSource` below): the client
is what pools connections and is the thing audit T2 is actually about, while
the model is a config dict whose `tool_choice` is per-turn state. Sharing the
model would apply one tag's relaxation to a concurrent tag's first call.

The client guarantee lives in the composition root's *call
discipline*, not in a module-level singleton (DEC-13): `create_client` is a
plain constructor, called exactly once by
`nora.app.composition.build_dependencies`, whose result is threaded through
`Dependencies.client` to every consumer. `nora.__main__.App.shutdown` closes
that same object directly (`await self.deps.client.close()`) — there is
nothing left in this module to reset between tests, and nothing left for a
test to leak into another one via import-time state.

Takes explicit scalars, never `nora.config` objects — this used to be the one
documented exception to "only app and `__main__` read config"
(`pyproject.toml`'s `ignore_imports`, deleted by the pipelines phase). The
composition root (Task 8) resolves `LLMConfig`/`LLMTierConfig`/`LimitsConfig`
down to the scalars below and hands them over already resolved, including the
API key itself — this module no longer reads the environment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from strands import Agent, ModelRetryStrategy
from strands.agent.conversation_manager import NullConversationManager
from strands.hooks import HookCallback, HookProvider
from strands.models.openai import OpenAIModel
from strands.plugins import Plugin
from strands.vended_plugins.skills import AgentSkills

from .guards import ReplyGuard, ToolCallLog
from .tool_choice import FIRST_CALL

log = logging.getLogger("nora.factory")

# The agent's name in a trace, and the thing that makes a Langfuse span readable.
# Strands names the agent span `f"invoke_agent {agent.name}"` and defaults
# `name` to "Strands Agents" / `agent_id` to "default", so every trace this
# process ever produced was called `invoke_agent Strands Agents` — correct,
# and useless for finding anything. This is the DEFAULT; the composition root
# passes `cfg.agent.name` (2026-08-08), so a cloned agent gets its own span.
# The span name is what a saved Langfuse view filters on, so the default holds
# "nora" exactly — a rename is a deliberate new-agent act, not a casual edit.
DEFAULT_AGENT_NAME = "nora"

# How much of one invocation's wall clock may be spent sleeping between retries.
# The rest has to cover the model calls themselves, of which there may be
# `max_attempts` — a quarter leaves three quarters for up to four attempts.
RETRY_BACKOFF_FRACTION = 0.25


def create_client(*, base_url: str, api_key: str) -> AsyncOpenAI:
    if not api_key:
        raise RuntimeError("api_key is empty — refusing to start")
    return AsyncOpenAI(api_key=api_key, base_url=base_url)


def create_model_provider(
    *, model: str, max_tokens: int, temperature: float, client: AsyncOpenAI,
    reasoning_effort: str = "none",
) -> OpenAIModel:
    """`tool_choice="required"` is load-bearing, not a tuning knob.

    `send_reply` is a tool, and it is the ONLY way Nora can speak (A9, R-2).
    `deepseek-v4-flash` — the configured tier — does not emit `tool_calls` on
    its own: given an explicit tool definition AND a system prompt demanding
    one, it answers in plain text with `finish_reason="stop"`, verified
    directly against the gateway. `tool_choice="auto"` changes nothing;
    `"required"` produces the call every time.

    Without it every tag ran the model, got prose, and dropped it as
    `no_send_call` — Nora reacted 👀 then ⚠️ and never spoke. The failure was
    honest and counted, which is how it was found on the first real tag, but
    it made her structurally mute.

    This constrains the model to call SOME tool, not `send_reply` specifically,
    so `load_transcript`/`about` remain reachable and R-7's default-deny
    is untouched.

    **It is the FIRST value of a sequence, not a constant, since step 5b**:
    `ReplyGuard` flips this same model to `auto` once she has spoken, so she is
    compelled to answer and then permitted to stop. `nora.agent.tool_choice`
    owns both ends and the measurements behind them. That flip is why the
    object this function returns is now built PER TAG (`ModelSource` below) —
    it is per-turn mutable state, and one instance shared by two concurrent
    tags would apply one tag's relaxation to the other tag's first call.
    """
    return OpenAIModel(
        model_id=model,
        client=client,
        params={
            "max_tokens": max_tokens,
            "temperature": temperature,
            "tool_choice": FIRST_CALL,
            # Comes from the tier (config.toml), not from here — a runtime
            # value belongs in config, not in code (DEC-8). See
            # `LLMTierConfig.reasoning_effort` for the measurements.
            "reasoning_effort": reasoning_effort,
        },
    )


@dataclass(frozen=True)
class ModelSpec:
    """Scalars for one `OpenAIModel`, resolved once at startup.

    A spec rather than a model INSTANCE, for the same reason `RetryPolicy`
    below is a policy rather than a `ModelRetryStrategy`: the numbers are
    immutable and shareable, the object built from them is neither. An
    `OpenAIModel` carries `config["params"]`, which `nora.agent.tool_choice`
    mutates mid-turn, so one shared instance would let one tag's relaxation
    decide another tag's first call — and design/16 §2.1 measured that call as
    sending nothing 5/6.
    """

    model: str
    max_tokens: int
    temperature: float
    reasoning_effort: str = "none"


class ModelSource:
    """One `OpenAIModel` PER TAG, over the process-wide `AsyncOpenAI` client.

    Audit T2's guarantee is "one CLIENT per process" — that is the one that
    matters, because it is what pools connections — and it is untouched here:
    the client is built exactly once by `nora.app.composition.build_dependencies`
    and handed in. The MODEL is a config dict with a client reference; building
    one per tag costs nothing and is what makes `tool_choice` (and, usefully,
    `reasoning_effort`) per-tag state.

    A constructed collaborator, injected into
    `nora.app.agent_runner.LiveAgentRunner` through `__init__` — never a
    factory callable or a module-level singleton (CLAUDE.md's test-seam
    policy). Tests that need to drive the real Strands loop against a stub
    `Model` pass a stand-in with the same one-method surface
    (`tests/fakes.py::FixedModelSource`).
    """

    def __init__(self, *, client: AsyncOpenAI, spec: ModelSpec) -> None:
        self._client = client
        self._spec = spec

    def create(self) -> OpenAIModel:
        return create_model_provider(
            model=self._spec.model, max_tokens=self._spec.max_tokens,
            temperature=self._spec.temperature, client=self._client,
            reasoning_effort=self._spec.reasoning_effort,
        )


def build_limits(*, output_tokens: int, turns: int, total_tokens: int) -> dict:
    """R-8: the three caps Strands enforces around the WHOLE agent loop.

    All three are LOOP budgets. `output_tokens` is the one that reads like a
    per-call ceiling and is not, and until 2026-08-06 it was passed
    `llm.max_tokens` — the tier's genuine per-CALL ceiling — under a docstring
    that said "`output_tokens` derives from the active tier", which stated the
    bug as though it were the intent. The effect was that the whole loop was
    allowed to emit exactly what a single call was allowed to emit.

    Measured against the live gateway, three consecutive single model calls
    emitted 3,993 / 3,987 / 3,974 completion tokens against `max_tokens =
    4000` — 99.8% of the loop's entire output budget spent by the first call.
    Any turn that needed a second one was cut short by a cap nobody had
    chosen, and (before the `stop_reason` work below) the cut was reported as
    the model declining to speak. So the loop budget is now supplied
    explicitly by the composition root from `[llm.limits] output_tokens`,
    sized as a MULTIPLE of the per-call ceiling (today 12,000 against
    `max_tokens = 2000`, 6x) rather than equal to it. `nora.agent.factory` is
    deliberately not the place that knows the multiple — it takes the number.

    See `nora.engagement.stages.invoke` for what happens when one of these
    three does bind: Strands returns an `AgentResult` whose `stop_reason` is
    `limit_turns` / `limit_total_tokens` / `limit_output_tokens`, raises
    nothing, and logs nothing (R-23).

    Scalarised in Task 8 (Phase 3's composition-root cutover): this used to
    take `(tier, limits)` object pairs because the now-deleted
    `Handler.handle_tag` called it positionally on every tag, and Tasks 5-6
    were scoped not to touch `handler.py`'s call shape while it still carried
    real traffic. `Handler` is gone and this is now called exactly once, at
    process startup, by `nora.app.composition.build_dependencies` — so there
    is no longer a caller forcing an object-shaped signature, and scalars
    keep this module's inputs framework-free the same way
    `create_client`/`create_model_provider` already are.
    """
    return {
        "turns": turns,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


@dataclass(frozen=True)
class RetryPolicy:
    """Scalars for one `ModelRetryStrategy`, resolved once at startup.

    A policy rather than a strategy INSTANCE because `ModelRetryStrategy` holds
    mutable per-invocation state (`_current_attempt`, reset by its own
    `AfterInvocationEvent` hook). Nora builds one `Agent` per tag and two tags
    can overlap, so a shared instance would let one tag's retry counter decide
    another tag's backoff. `create_agent` constructs a fresh strategy from these
    numbers instead — the same reason `ReplyGuard` is per-tag.
    """

    max_attempts: int
    initial_delay: int
    max_delay: int

    @property
    def total_backoff_seconds(self) -> int:
        """Worst-case seconds spent asleep before the last attempt gives up.

        Mirrors `ModelRetryStrategy._calculate_delay`: attempt *n* (0-indexed)
        sleeps `min(initial_delay * 2**n, max_delay)`, and there are
        `max_attempts - 1` sleeps before the final attempt is refused.
        """
        return sum(
            min(self.initial_delay * 2**attempt, self.max_delay)
            for attempt in range(max(0, self.max_attempts - 1))
        )


def build_retry_policy(
    *, timeout_seconds: float, initial_delay: int = 1,
    backoff_fraction: float = RETRY_BACKOFF_FRACTION,
) -> RetryPolicy:
    """A retry budget that fits inside the invocation timeout — an A8 fix.

    Strands' default is `max_attempts=6, initial_delay=4, max_delay=240`
    (`strands/event_loop/_retry.py`), i.e. 4+8+16+32+64 = 124s of backoff before
    it gives up. `nora.engagement.stages.invoke` wraps the whole invocation in
    `asyncio.wait_for(..., timeout=llm.timeout_seconds)`, which is 30s, and the
    retry sleeps happen INSIDE that wait — so the very first throttle would
    burn 4s, retry, and the timeout would fire long before the strategy ever
    gave up. The turn is then counted
    `nora_silent_failures_total{reason="invocation_timeout"}` when what actually
    happened was provider throttling. A8 exists to make the silent-failure rate
    trustworthy, and a reason label that names the wrong cause is worse than a
    missing one: it points the next investigation at latency instead of quota.

    So the budget is derived from the timeout rather than configured beside it.
    `timeout_seconds` is already per-tier config (`LLMConfig.timeout_seconds`),
    and a second knob would be a second thing to keep consistent with it — the
    invariant that matters (`total_backoff_seconds < timeout_seconds`) is one
    nobody can violate if it is never written down twice.

    At today's 30s: `initial_delay=1`, budget 7.5s, so 1+2+4 = 7s over four
    attempts, leaving 23s for the calls themselves.
    """
    # A zero or negative base would never grow `spent` and the loop below would
    # not terminate; one second is the smallest delay that is still a delay.
    initial_delay = max(1, initial_delay)
    budget = timeout_seconds * backoff_fraction
    max_attempts, spent = 1, 0.0
    while spent + initial_delay * 2 ** (max_attempts - 1) <= budget:
        spent += initial_delay * 2 ** (max_attempts - 1)
        max_attempts += 1
    return RetryPolicy(
        max_attempts=max_attempts,
        initial_delay=initial_delay,
        # Nothing may sleep longer than the whole backoff budget, whatever the
        # doubling would otherwise produce.
        max_delay=max(initial_delay, int(budget)),
    )


def create_agent(
    *,
    model: OpenAIModel,
    system_prompt: str,
    tools: list,
    pre_fill: list[dict],
    guard: ReplyGuard,
    tool_call_log: ToolCallLog | None = None,
    trace_attributes: dict,
    retry: RetryPolicy,
    skills_dir: str | Path | None = None,
    agent_name: str = DEFAULT_AGENT_NAME,
) -> Agent:
    """`skills_dir` turns the on-demand skill layer ON (DEC-50, activating DEC-48).

    When set, one `AgentSkills` plugin is built over that directory. The plugin
    does two things and NEITHER is doctrine: it injects an `<available_skills>`
    block (each skill's NAME and when-to-load line — NOT its body — progressive
    disclosure, design/12 §1) into the system prompt before each invocation, and
    it registers the model-facing `skills(skill_name)` activation tool that loads
    a skill's full instructions on demand. R-7 admits that tool because
    `nora.agent.tools.NATIVE_TOOLS` now carries a `skills` budget; without it
    `ReplyGuard` would veto every activation call.

    **One plugin PER TAG, like the model and the guard.** This is the ONLY module
    that constructs Strands objects, and `AgentSkills` is one; built here it gets
    the same per-tag isolation the model (`ModelSource`) and `ReplyGuard` already
    have — its per-agent skill state (a `WeakKeyDictionary` keyed by agent) never
    spans two tags. Construction does no I/O: a directory source is read through
    the agent's sandbox at first invocation, not now.

    **R-29 survives activation.** The `skills` tool resolves a name by pure dict
    lookup against the operator-loaded set (`AgentSkills._skills_for`), exactly as
    restrictive as `nora.skills.loader.SkillStore.resolve`: filesystem/URL loading
    happens only from THIS `skills_dir` (operator config), never from the
    `skill_name` a message supplies — a message naming a path, a URL or an inline
    blob is a dict miss, so item 6 can name a shipped skill but never introduce a
    new source. `strict=False` so a malformed operator skill warns and is skipped
    rather than crashing a tag (R-5); the shipped `navigation` skill is valid and
    `tests/test_skill_content.py` holds it so.

    `skills_dir=None` leaves the layer INERT (offline/eval compositions, and any
    deployment that has not wired it): no plugin, no `<available_skills>` block,
    no activation tool — the pre-DEC-50 prompt, byte-identical.
    """
    plugins: list[Plugin] = (
        [AgentSkills(str(skills_dir), strict=False)] if skills_dir is not None else []
    )
    # `list[HookProvider]`, explicitly — `[guard] + ([tool_call_log] if ...
    # else [])` infers `list[ToolCallLog | ReplyGuard]`, which mypy's
    # invariant-list check then refuses to hand to `Agent(hooks=...)`
    # (`list[HookProvider | HookCallback[Any]] | None`) even though both
    # elements ARE `HookProvider`s.
    hooks: list[HookProvider | HookCallback[Any]] = [guard]
    if tool_call_log is not None:
        hooks.append(tool_call_log)
    return Agent(
        model=model,
        plugins=plugins,
        # Identity, so a trace can be found. `name` becomes the span name;
        # `agent_id` is stable rather than per-tag because the tag is already
        # carried as a trace attribute (`nora.obs.tracing.trace_attributes`) and
        # a per-tag agent_id would make every trace its own agent.
        name=agent_name,
        agent_id=agent_name,
        # Fresh per agent — see RetryPolicy's docstring for why this is not a
        # shared instance.
        retry_strategy=ModelRetryStrategy(
            max_attempts=retry.max_attempts,
            initial_delay=retry.initial_delay,
            max_delay=retry.max_delay,
        ),
        system_prompt=system_prompt,
        tools=tools,
        # pre_fill/messages are plain dicts by design end-to-end (build_prefill in
        # nora.agent.prefill through nora.engagement's stages/pipeline, none of which
        # import strands — see this module's own docstring and
        # tests/test_boundaries.py::test_strands_import_boundary). Strands' `Agent`
        # wants `list[Message]` (a TypedDict); retyping this one call site to match
        # would force list[Message] up through that whole framework-free chain, which
        # is out of scope for this fix. The dicts already have the right shape at
        # runtime; only the static type is being asserted here.
        messages=list(pre_fill),  # type: ignore[arg-type]
        hooks=hooks,
        conversation_manager=NullConversationManager(),
        session_manager=None,
        callback_handler=None,
        load_tools_from_directory=False,
        trace_attributes=trace_attributes,
    )


async def invoke(agent: Agent, tag_message: str, *, invocation_state: dict, limits: dict) -> Any:
    """Run one turn and return Strands' `AgentResult` — `stop_reason` included.

    The return value is NOT decoration and must not be dropped again (R-23).
    When one of `limits`' three caps binds, Strands' `_check_limits` yields an
    `EventLoopStopEvent` carrying `stop_reason="limit_total_tokens"` (or
    `limit_turns` / `limit_output_tokens`) and returns normally: no exception,
    no log line, no warning anywhere in the SDK — verified end to end against
    1.50.2. `AgentResult.stop_reason` is the ONLY channel that says a turn was
    cut off rather than finished, so `nora.app.agent_runner.LiveAgentRunner`
    reads it off this result and hands it up the stage chain.

    Typed `Any` rather than `strands.agent.AgentResult` so that callers in
    `nora.app` — which mypy checks strictly and which must not import
    `strands` (`tests/test_boundaries.py::test_strands_import_boundary`) —
    can narrow the one attribute they want without naming the SDK type.
    """
    return await agent.invoke_async(
        tag_message,
        invocation_state=invocation_state,
        # limits is a plain dict end-to-end for the same reason pre_fill is above:
        # nora.app.composition/nora.engagement build and thread it as `dict` without
        # importing strands. Strands' `Limits` is a TypedDict with the same three
        # keys build_limits() always fills (turns/output_tokens/total_tokens); the
        # value is shape-correct at runtime, only the static type differs.
        limits=limits,  # type: ignore[arg-type]
    )
