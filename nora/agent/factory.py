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

import asyncio
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
from strands.types.exceptions import MaxTokensReachedException
from strands.vended_plugins.skills import AgentSkills

from ..domain.errors import PartialReplyError
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

# The second agent's span name (`create_dream_agent`). Separate from the value
# above ON PURPOSE: the Dreamer's traces must be findable APART from her turns —
# it runs with nobody waiting on it, and a background loop sharing her span name
# would make every saved Langfuse view a mixture of the two.
DEFAULT_DREAM_AGENT_NAME = "dreamer"

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
    reasoning_effort: str = "none", tool_choice: str = FIRST_CALL,
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

    **`tool_choice` is a PARAMETER since the Dreamer, and the default above is
    unchanged.** Everything written above is an argument about *a turn that
    must end in `send_reply`* — Nora's turn — and it is not relativised by the
    parameter: every tag-path call site takes the default and the request is
    byte-identical to what it was before this parameter existed. The parameter
    exists because there is now a SECOND kind of loop in this process, and the
    argument above does not reach it.

    The Dreamer (`create_dream_agent` below) has **no terminal verb at all**:
    every act verb is excluded from it by construction, and the terminal ones
    ARE act verbs. Its loop therefore ends the only way a loop with nothing to
    end it can — the model **declines to call anything** and returns its
    structured result. Under `"required"` that is unreachable: the model must
    call something on every pass, so a loop whose terminal condition is a
    non-call would run until a limit caught it. On Nora's path
    `ReplyGuard`'s `BeforeModelCallEvent` hook is what ends a turn, and it ends
    it by observing a terminal call (DEC-21, DEC-32); the Dreamer has no such
    observation to make. So it passes `"auto"` — for the same reason
    `ReplyGuard` flips to `AFTER_SPEAKING` after she has spoken, one step
    earlier: a loop that is permitted to stop is a loop that can stop.
    """
    return OpenAIModel(
        model_id=model,
        client=client,
        params={
            "max_tokens": max_tokens,
            # `temperature` is the ONLY sampling parameter we send. `top_p` is
            # absent from this dict ON PURPOSE and has no config key, so the
            # value in force is the PROVIDER's default and no nucleus
            # truncation is applied by us anywhere in the loop.
            #
            # Stated here because the absence was invisible from both the code
            # and config.example.toml, and an audit reasoning about her
            # sampling got the wrong picture (F8, the 2026-08-20
            # prompt-architecture audit). Do NOT "make it explicit" by adding
            # `top_p`: 1.0 is a no-op, and anything below it truncates the
            # distribution on a guess. See the sampling note in
            # config.example.toml's `[llm]` for what measuring it would take.
            "temperature": temperature,
            "tool_choice": tool_choice,
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
    # DEFAULTED to the tag path's value, so every existing call site is
    # unchanged and the turn that must end in `send_reply` still gets
    # `"required"`. The Dreamer builds a SECOND source with
    # `tool_choice=AFTER_SPEAKING`, because its loop has no terminal verb at
    # all: under `"required"` the model must call something on every pass, and
    # the only thing that ends a turn is a guard observing a terminal call
    # (DEC-21/DEC-32) — so a read-only research loop could never stop by
    # itself. The field lives here rather than on `create_dream_agent` because
    # `tool_choice` is a property of the MODEL PROVIDER, and `ModelSource` is
    # the only thing that builds one.
    tool_choice: str = FIRST_CALL


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
            tool_choice=self._spec.tool_choice,
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


def create_dream_agent(
    *,
    model: OpenAIModel,
    system_prompt: str,
    tools: list,
    guard: HookProvider,
    trace_attributes: dict,
    retry: RetryPolicy,
    agent_name: str = DEFAULT_DREAM_AGENT_NAME,
) -> Agent:
    """The Dreamer's agent: a loop that may LOOK and may not ACT.

    A sibling of `create_agent` rather than a flag on it, and it lives HERE
    rather than beside the Dreamer runtime because this module is the only one
    in the codebase allowed to construct Strands objects (spec C.7). A second
    agent gets a second constructor in this file; it never gets an `Agent(...)`
    call somewhere else.

    **What is absent is the specification.** `create_agent`'s parameters are
    each welded to Nora's turn and none of them has a meaning here:

    - no `pre_fill` — a tag's prefill is the transcript, the peer card and the
      room; the Dreamer is handed its batch by the runtime as the invocation's
      own prompt, and there is no conversation to reconstruct;
    - no `skills_dir` — DEC-50's on-demand layer injects `<available_skills>`
      and registers the `skills` activation tool for a model composing a reply.
      A research loop has an objective, not a repertoire, and a tool the guard
      would have to admit is a tool the Dreamer would have to budget;
    - no `tool_call_log` — that log exists so `recall_lookups` can replay one
      conversation's earlier lookups to a later turn in the same conversation.
      The Dreamer has no conversation.

    **`guard` is typed `HookProvider`, not `ReplyGuard` and not `DreamGuard`** —
    structurally, because what this constructor needs from it is exactly what
    `Agent(hooks=...)` needs from it and nothing more. `ReplyGuard` satisfies it
    too; that is a property of the protocol, not an invitation. The guard that
    belongs here is `DreamGuard` — R-7 default-deny with no terminal verb and no
    deliverer — and typing this parameter to that class would make this module
    import it for a constraint the SDK does not impose.

    **`tool_choice` is not set here, and that is deliberate**: it is a property
    of the MODEL, and the model arrives already built. The Dreamer's is built
    with `create_model_provider(..., tool_choice=AFTER_SPEAKING)`; see that
    function's docstring for why a loop with no terminal verb cannot run under
    `"required"`. Setting it from this constructor would put the same fact in
    two places and let a caller pass a `"required"` model that silently could
    not stop.

    Everything else is the posture `create_agent` already holds, for the same
    reasons: `conversation_manager=NullConversationManager()` and
    `session_manager=None` (D4 — no agent memory, ever; continuity is the store),
    `callback_handler=None` (nothing prints to stdout),
    `load_tools_from_directory=False` (the tool set is the one passed in, and for
    this agent that set is a *ceiling*, not a default), and a FRESH
    `ModelRetryStrategy` per agent (see `RetryPolicy` — it holds per-invocation
    state, and dream runs overlap the same way tags do).
    """
    return Agent(
        model=model,
        # Identity, so a background run is findable in Langfuse APART from her
        # turns — same argument as `create_agent`, different default.
        name=agent_name,
        agent_id=agent_name,
        retry_strategy=ModelRetryStrategy(
            max_attempts=retry.max_attempts,
            initial_delay=retry.initial_delay,
            max_delay=retry.max_delay,
        ),
        system_prompt=system_prompt,
        tools=tools,
        hooks=[guard],
        conversation_manager=NullConversationManager(),
        session_manager=None,
        callback_handler=None,
        load_tools_from_directory=False,
        trace_attributes=trace_attributes,
    )


#: Strands' canned recovery notice (`_recover_message_on_max_tokens_reached`,
#: `event_loop.py:601`) as two fragments, so a partial-text extraction can
#: exclude a block carrying it without asserting a full-sentence match.
#: A SINGLE shared definition (review, 2026-09-19) — `nora.agent.structured.
#: ask_text`'s own extraction split the same notice at different points,
#: which made `grep` for the literal string Strands emits find neither site.
MAX_TOKENS_NOTICE_PREFIX = "The selected tool "
MAX_TOKENS_NOTICE_INFIX = "tool use was incomplete due to maximum token limits being reached"


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

    **`MaxTokensReachedException` is caught here, not left to bubble.** Strands
    raises it AFTER appending the partial message to `agent.messages`
    (event_loop.py:640) — the words are recoverable, they just never left the
    loop. Without this catch the exception lands in `invoke.run`'s generic
    `except Exception`, which counts a silent failure and drops the partial
    reply entirely (measured live 2026-09-02: Blake's Raft-password tag hit
    max_tokens and Nora stayed silent). The partial text is extracted from the
    last message's text blocks and carried up as `PartialReplyError` so the
    pipeline can deliver it. `nora.agent` may import `strands` (only
    `nora.app` may not), so naming the SDK exception here is legal.
    """
    try:
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
    except MaxTokensReachedException:
        # The partial message is the LAST entry in `agent.messages` — Strands
        # appended it before raising (event_loop.py:640). Extract its text
        # blocks the same way `final_text` does, but from the message dict
        # directly (there is no `AgentResult` to read here).
        #
        # EXCLUDE Strands' canned recovery notice. Before appending, the SDK
        # replaces EVERY tool-use block with a fixed English sentence:
        # "The selected tool X's tool use was incomplete due to maximum token
        # limits being reached." (`_recover_message_on_max_tokens_reached`,
        # called at event_loop.py:601). A cut-off mid-send_reply therefore
        # leaves a message whose ONLY text blocks are that notice — nothing
        # the model wrote. Delivering it would leak SDK machinery into the
        # group (the exact thing R-5/R-6 exist to prevent). So blocks that
        # carry that exact shape are dropped: a partial that is ONLY the
        # notice yields partial_text="" and the turn stays silent.
        partial = ""
        if agent.messages:
            last = agent.messages[-1]
            content = last.get("content") if isinstance(last, dict) else None
            if isinstance(content, list):
                parts = [
                    block["text"] for block in content
                    if isinstance(block, dict) and isinstance(block.get("text"), str)
                    and not block["text"].startswith(MAX_TOKENS_NOTICE_PREFIX)
                    and MAX_TOKENS_NOTICE_INFIX not in block["text"]
                ]
                partial = "\n".join(p for p in parts if p.strip()).strip()
        raise PartialReplyError(partial) from None


# What the model is asked for when its loop ran out mid-work. Deliberately NOT
# "summarise": she is answering the person who asked, in her own voice, with
# what she has — a summary of her own research reads like a report, not a
# reply.
#
# It says nothing about budgets, tokens or limits, and that is load-bearing:
# whatever comes back is DELIVERED to the group, and
# `tests/integration/test_turn_budget_is_legible.py::
# test_the_budget_fallback_says_nothing_diagnostic_to_the_group` pins that the
# room never reads the machine's excuses. Telling the model it ran out of
# budget is the surest way to make it say so.
_DRAFT_INSTRUCTION = (
    "Answer now, from what you already have in this conversation. Do not call "
    "any tool and do not look anything else up — everything you are going to "
    "get, you already have. Say what you found, in your own voice, to the "
    "person who asked. If what you have is partial, say what you know and what "
    "you could not pin down; do not apologise for the process and do not "
    "mention searching, fetching, tools, or how much you read."
)


async def draft_from_exhausted_loop(
    agent: Agent, *, timeout_seconds: float, limits: dict,
) -> str:
    """One more completion over the agent's OWN accumulated context, tools off.

    WHY THIS EXISTS. A turn cut off by one of R-8's caps has usually done all
    the work — trace 844eb6d1 (2026-09-06) read seven pages across four
    `web_search` and three `web_fetch` calls, then stopped on
    `limit_total_tokens` at call seven and the group received
    the canned apology. She had the answer in front of her and sent an
    apology (issue #110, operator: "This is exactly how it shouldn't be, not
    like a friend, just like a bot").

    WHY ON THE AGENT AND NOT FROM THE TRACE. The pipeline could draft from
    `TagTrace.messages` without touching this layer, and that was the first
    design. It is wrong: `DEFAULT_MAX_TRACE_CHARS` is 10,000, and the turn
    above had accumulated ~45,600 tokens — the trace carries about 5% of what
    she read. Drafting from it ships the feature and defeats its purpose. The
    full context exists only on this `agent`, which is why the call belongs
    here.

    WHY A SECOND `invoke_async` IS NOT INSTANTLY RE-CUT. Strands' caps are
    PER-INVOCATION: `_check_limits` reads
    `event_loop_metrics.latest_agent_invocation`, documented in the SDK as
    "scoped to the current invocation so caps don't fire prematurely on the
    second invoke against a reused agent" (verified against 1.50.2). So this
    call gets a fresh budget rather than inheriting the spent one. `limits` is
    still passed — an unbounded call on a turn that just proved it can run
    away is not a thing to write.

    WHY `tool_choice` IS FLIPPED. The agent runs `tool_choice="required"`
    (DEC-21) because that is what makes her reach for `send_reply` instead of
    answering in prose. Here prose is exactly what is wanted, and leaving the
    pin on would force yet another tool call — the one thing there is no
    budget for. `update_config` is the supported route and is pinned by
    `tests/test_strands_api_contract.py::test_obligation_7...`; the guard
    already uses it. Restored in the `finally` because the agent is not
    ours to leave altered, even though this one is discarded right after.

    FAIL-CLOSED. Returns "" on a timeout, an exception, or a model that
    answers with nothing. The caller keeps the canned line for that case: this
    is an IMPROVEMENT on the fallback, never a replacement for having one, and
    an exhausted turn must not also hang — hence its own timeout rather than
    the tier's.
    """
    model = getattr(agent, "model", None)
    previous: object = None
    restore = False
    if model is not None and hasattr(model, "get_config") and hasattr(model, "update_config"):
        try:
            previous = dict(model.get_config().get("params") or {})
            model.update_config(params={**previous, "tool_choice": "none"})
            restore = True
        except Exception:  # noqa: BLE001 — a config knob is not worth losing the draft over
            # A model shape that will not take the flip still gets the call —
            # it may answer in prose anyway, and refusing to draft because a
            # config knob moved is a worse failure than drafting with tools on.
            log.warning("draft: could not disable tool_choice; drafting anyway")
    try:
        result = await asyncio.wait_for(
            agent.invoke_async(
                _DRAFT_INSTRUCTION, invocation_state={},
                limits=limits,  # type: ignore[arg-type]
            ),
            timeout=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — incl. TimeoutError; see FAIL-CLOSED above
        # Never re-raise into a turn that has already failed once. The caller
        # falls back to the canned line, which is where it was anyway.
        log.warning("draft: exhausted-loop draft failed (%s)", type(exc).__name__)
        return ""
    finally:
        if restore and model is not None:
            try:
                model.update_config(params=previous)
            except Exception:  # noqa: BLE001 — the agent is discarded next line anyway
                log.warning("draft: could not restore tool_choice")
    return final_text(result)


def final_text(result: Any) -> str:
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

    **It lives HERE, beside `invoke`, and not in `nora/app/agent_runner.py`
    where it was written.** Two reasons, and the second is the one that made it
    move. First, it reads the SDK shape `invoke` above returns — the same
    `AgentResult` that function is typed `Any` for — and this module is where
    knowing a Strands shape belongs; `agent_runner` may not even import
    `strands`, so a reader there could not check this parser against the type it
    parses. Second, the Dreamer needs exactly this extraction (its loop ends
    with the model's structured result as authored text, DEC-93's shape read for
    a different purpose) and `nora.agent` may not import `nora.app` — so the
    alternative to promoting it was a second copy, which is how two defensive
    parsers of one third-party shape drift apart.
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
