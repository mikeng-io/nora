"""The only module that constructs Strands objects (spec C.7).

One `AsyncOpenAI` client for the whole process, never one per tag. One
`OpenAIModel` PER TAG, though (DEC-42, `ModelSource` below): the client pools
connections, while the model is a config dict whose `tool_choice` is per-turn
state. Sharing the model would apply one tag's relaxation to a concurrent tag's
first call.

The client guarantee lives in the composition root's call discipline, not in a
module-level singleton (DEC-13): `create_client` is a plain constructor, called
once by `nora.app.composition.build_dependencies`, whose result is threaded
through `Dependencies.client` to every consumer. `nora.__main__.App.shutdown`
closes that same object (`await self.deps.client.close()`). The module holds no
import-time state.

Takes explicit scalars, never `nora.config` objects: the composition root
resolves `LLMConfig`/`LLMTierConfig`/`LimitsConfig` down to the scalars below,
including the API key, and this module does not read the environment.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI
from strands import Agent, ModelRetryStrategy
from strands.agent.conversation_manager import (
    NullConversationManager,
    SlidingWindowConversationManager,
)
from strands.hooks import HookCallback, HookProvider
from strands.models.openai import OpenAIModel
from strands.plugins import Plugin
from strands.tools.executors import ConcurrentToolExecutor, SequentialToolExecutor
from strands.tools.executors._executor import ToolExecutor
from strands.types.exceptions import MaxTokensReachedException
from strands.vended_plugins.skills import AgentSkills

from ..domain.errors import PartialReplyError
from .guards import ReplyGuard, ToolCallLog
from .reasoning_carry import ReasoningCarryingOpenAIModel
from .tool_choice import FIRST_CALL

log = logging.getLogger("nora.factory")

# The agent's name in a trace. Strands names the agent span
# `f"invoke_agent {agent.name}"` and defaults `name` to "Strands Agents" and
# `agent_id` to "default"; this default gives a Langfuse span a findable name.
# The composition root passes `cfg.agent.name`, so a cloned agent gets its own
# span. A saved Langfuse view filters on the span name, so the default holds
# "nora" exactly: a rename is a deliberate new-agent act.
DEFAULT_AGENT_NAME = "nora"

# The second agent's span name (`create_dream_agent`). Separate from the value
# above on purpose: the Dreamer runs with nobody waiting on it, and sharing her
# span name would make every saved Langfuse view a mixture of the two.
DEFAULT_DREAM_AGENT_NAME = "dreamer"

# The fraction of one invocation's wall clock that may be spent sleeping between
# retries. The rest covers the model calls themselves, of which there may be
# `max_attempts`: a quarter leaves three quarters for up to four attempts.
RETRY_BACKOFF_FRACTION = 0.25


def create_client(
    *, base_url: str, api_key: str, http_client: httpx.AsyncClient | None = None,
) -> AsyncOpenAI:
    """`http_client` is the transport seam for an endpoint that wants something
    on EVERY request that the SDK does not send, e.g. OpenCode Go's mandatory
    `x-opencode-session` (DEC-197). `None` keeps the SDK's own client."""
    if not api_key:
        raise RuntimeError("api_key is empty — refusing to start")
    return AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)


def create_model_provider(
    *, model: str, max_tokens: int, temperature: float, client: AsyncOpenAI,
    reasoning_effort: str = "none", tool_choice: str = FIRST_CALL,
) -> OpenAIModel:
    """Build the per-tag model provider.

    `tool_choice="required"` (the default, `FIRST_CALL`) is load-bearing, not a
    tuning knob. `send_reply` is a tool and the only way Nora can speak (A9,
    R-2). `deepseek-v4-flash`, the configured tier, does not emit `tool_calls`
    on its own: given an explicit tool definition and a system prompt demanding
    one, it answers in plain text with `finish_reason="stop"`, and
    `tool_choice="auto"` changes nothing. Without `"required"` every tag is
    dropped as `no_send_call` and she is structurally mute.

    It constrains the model to call SOME tool, not `send_reply` specifically, so
    `load_transcript`/`about` remain reachable and R-7's default-deny is
    untouched.

    The value is the FIRST of a sequence, not a constant: `ReplyGuard` flips
    this same model to `auto` once she has spoken, so she is compelled to answer
    and then permitted to stop. `nora.agent.tool_choice` owns both ends. The
    model is therefore built PER TAG (`ModelSource` below): it is per-turn
    mutable state, and one instance shared by two concurrent tags would apply
    one tag's relaxation to the other's first call.

    `tool_choice` is a parameter because a second kind of loop exists. The
    Dreamer (`create_dream_agent`) has no terminal verb: every act verb is
    excluded from it, and the terminal ones are act verbs. Its loop ends only
    when the model declines to call anything and returns its structured result,
    which is unreachable under `"required"`. On Nora's path `ReplyGuard`'s
    `BeforeModelCallEvent` hook ends a turn by observing a terminal call (DEC-21,
    DEC-32); the Dreamer has none, so it passes `"auto"`.

    Dash-free (`nora.agent.punctuation`): the request carries no em dash,
    because she copies the ones she is shown.

    Reasoning-carrying (`nora.agent.reasoning_carry`): a continuation call
    carries the reasoning the previous call produced, as the `reasoning` field
    the upstream reads, instead of the SDK dropping it. Inert at
    `reasoning_effort = "none"`, where there is no reasoning to carry.
    """
    return ReasoningCarryingOpenAIModel(
        model_id=model,
        client=client,
        params={
            "max_tokens": max_tokens,
            # `temperature` is the only sampling parameter we send. `top_p` is
            # absent on purpose and has no config key, so the PROVIDER's
            # default is in force and we apply no nucleus truncation anywhere
            # in the loop. Do not add `top_p`: 1.0 is a no-op, and anything
            # below it truncates the distribution on a guess. See the sampling
            # note in config.example.toml's `[llm]`.
            "temperature": temperature,
            "tool_choice": tool_choice,
            # From the tier (config.toml), not from here: a runtime value
            # belongs in config (DEC-8). See `LLMTierConfig.reasoning_effort`.
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
    decide another tag's first call.
    """

    model: str
    max_tokens: int
    temperature: float
    reasoning_effort: str = "none"
    # Defaults to the tag path's value (`"required"`). The Dreamer builds a second
    # source with `tool_choice=AFTER_SPEAKING`: its loop has no terminal verb,
    # and under `"required"` a read-only research loop could never stop by
    # itself. The field lives here rather than on `create_dream_agent` because
    # `tool_choice` is a property of the model provider, and `ModelSource` is
    # the only thing that builds one.
    tool_choice: str = FIRST_CALL


class ModelSource:
    """One `OpenAIModel` PER TAG, over the process-wide `AsyncOpenAI` client.

    The client is what pools connections and is built exactly once by
    `nora.app.composition.build_dependencies` and handed in. The MODEL is a
    config dict with a client reference; building one per tag costs nothing and
    makes `tool_choice` and `reasoning_effort` per-tag state.

    A constructed collaborator, injected into
    `nora.app.agent_runner.LiveAgentRunner` through `__init__`, never a factory
    callable or a module-level singleton (CLAUDE.md's test-seam
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

    All three are LOOP budgets. `output_tokens` reads like a per-call ceiling
    and is not: it is supplied by the composition root from
    `[llm.limits] output_tokens`, sized as a multiple of the per-call
    `max_tokens` (`llm.max_tokens`), so a loop may emit more than one call.
    `nora.agent.factory` takes the number and does not know the multiple.

    See `nora.engagement.stages.invoke` for what happens when one of these binds:
    Strands returns an `AgentResult` whose `stop_reason` is `limit_turns` /
    `limit_total_tokens` / `limit_output_tokens`, raises nothing, and logs
    nothing (R-23).

    Takes scalars (from `nora.app.composition.build_dependencies` and from each
    run that builds its own agent), which keeps this module's inputs
    framework-free like `create_client`/`create_model_provider`.
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
    numbers instead, as `ReplyGuard` is per-tag.
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
    """A retry budget that fits inside the invocation timeout.

    Strands' default is `max_attempts=6, initial_delay=4, max_delay=240`
    (`strands/event_loop/_retry.py`), i.e. 4+8+16+32+64 = 124s of backoff before
    it gives up. `nora.engagement.stages.invoke` wraps the whole invocation in
    `asyncio.wait_for(..., timeout=llm.timeout_seconds)`, and the retry sleeps
    happen INSIDE that wait, so the timeout would fire long before the strategy
    gave up, and the turn would be counted
    `nora_silent_failures_total{reason="invocation_timeout"}` when the cause was
    provider throttling.

    So the budget is derived from the timeout rather than configured beside it:
    `timeout_seconds` is already per-tier config (`LLMConfig.timeout_seconds`),
    and deriving keeps `total_backoff_seconds < timeout_seconds` true by
    construction.

    At a 30s timeout: `initial_delay=1`, budget 7.5s, so 1+2+4 = 7s over four
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


#: How many model-controlled tool calls may run at once across EVERY agent in the process. A
#: parallel batch is not limited by Strands, and each turn, dream and sub-agent loop used to hold
#: its own unbounded batch; overlapping turns multiplied that.
TOOL_CONCURRENCY = 8

#: Slots background work (the Dreamer, the sub-agent worker) may never take. A user-facing turn
#: finds one free even while eight long-running background tools run; without the reserve they
#: could hold every slot and the turn's tool would wait out its timeout.
INTERACTIVE_RESERVE = 2


class ToolSlots:
    """The in-flight tool calls that every executor sharing one instance draws on: `limit` in all.

    Background work may hold at most `limit - reserve` of them at once, so the `reserve` left are
    for interactive turns whatever the background is doing. Both gates are `asyncio.Semaphore`s,
    which bind to the event loop they first wait on: one instance serves one loop, the process's.
    """

    def __init__(self, limit: int, *, reserve: int = 0) -> None:
        if limit < 1:
            raise ValueError("a tool executor needs at least one slot")
        if not 0 <= reserve < limit:
            raise ValueError("background work must leave at least one slot for interactive turns")
        self.limit = limit
        self._total = asyncio.Semaphore(limit)
        self._background = asyncio.Semaphore(limit - reserve)
        self.in_flight = 0
        self.waiting = 0
        self.peak = 0

    async def acquire(self, *, background: bool) -> None:
        self.waiting += 1
        try:
            if background:
                await self._background.acquire()
                try:
                    await self._total.acquire()
                except BaseException:
                    self._background.release()
                    raise
            else:
                await self._total.acquire()
        finally:
            self.waiting -= 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)

    def release(self, *, background: bool) -> None:
        self.in_flight -= 1
        self._total.release()
        if background:
            self._background.release()


class BoundedToolExecutor(ConcurrentToolExecutor):
    """Concurrent tool execution drawing on one `ToolSlots`, shared by every agent given it.

    Each tool call of a batch is a task (Strands' concurrent executor), and each task takes a slot
    before it starts and gives it back when its stream ends, however it ends. Results keep the
    model's order because Strands collects them by task, not by completion. A cancelled turn gives
    its slots back the same way, so it cannot starve the next turn. A `background` executor takes
    its slots under the reserve (see `ToolSlots`); an interactive one takes them from the whole.

    What this does not do: it sets no timeout. A tool that never returns holds its slot until it
    is cancelled; the tools' own timeouts (MCP read timeouts, the browse task's clock) are what
    end one. A tool must not await another agent that shares this executor, or the two can wait on
    each other's slots.
    """

    def __init__(self, slots: ToolSlots, *, background: bool = False) -> None:
        super().__init__()
        self.slots = slots
        self.background = background

    @property
    def in_flight(self) -> int:
        return self.slots.in_flight

    @property
    def waiting(self) -> int:
        return self.slots.waiting

    @property
    def peak(self) -> int:
        return self.slots.peak

    async def _task(self, agent: Any, tool_use: Any, *args: Any, **kwargs: Any) -> None:
        await self.slots.acquire(background=self.background)
        try:
            await super()._task(agent, tool_use, *args, **kwargs)
        finally:
            self.slots.release(background=self.background)


@functools.cache
def shared_tool_slots() -> ToolSlots:
    """The process's tool slots, built on first use: one object, so every executor draws on one
    limit. `functools.cache` makes it the same object for every caller."""
    return ToolSlots(TOOL_CONCURRENCY, reserve=INTERACTIVE_RESERVE)


@functools.cache
def shared_tool_executor() -> BoundedToolExecutor:
    """The interactive executor, the tag loop's default: it may take any slot."""
    return BoundedToolExecutor(shared_tool_slots())


@functools.cache
def shared_background_executor() -> BoundedToolExecutor:
    """The background executor, the Dreamer's and the sub-agent's default: the same slots, but
    at most `TOOL_CONCURRENCY - INTERACTIVE_RESERVE` of them at once."""
    return BoundedToolExecutor(shared_tool_slots(), background=True)


def _executor_or_shared(tool_executor: ToolExecutor | None) -> ToolExecutor:
    """The executor a caller passed, else the process-wide interactive one."""
    return tool_executor if tool_executor is not None else shared_tool_executor()


def _background_executor_or_shared(tool_executor: ToolExecutor | None) -> ToolExecutor:
    """The executor a caller passed, else the process-wide background one."""
    return tool_executor if tool_executor is not None else shared_background_executor()


def create_agent(
    *,
    model: OpenAIModel,
    system_prompt: str,
    tools: list,
    pre_fill: list[dict],
    guard: ReplyGuard,
    tool_call_log: ToolCallLog | None = None,
    reasoning_watch: HookProvider | None = None,
    tool_hooks: Sequence[HookProvider] = (),
    trace_attributes: dict,
    retry: RetryPolicy,
    skills_dir: str | Path | None = None,
    agent_name: str = DEFAULT_AGENT_NAME,
    tool_executor: ToolExecutor | None = None,
) -> Agent:
    """Build the per-tag agent for a Nora turn.

    `skills_dir` turns the on-demand skill layer on (DEC-50, DEC-48). When set,
    one `AgentSkills` plugin is built over that directory. It injects an
    `<available_skills>` block (each skill's name and when-to-load line, not its
    body) into the system prompt before each invocation, and registers the
    model-facing `skills(skill_name)` activation tool that loads a skill's full
    instructions on demand. R-7 admits that tool because
    `nora.agent.tools.NATIVE_TOOLS` carries a `skills` budget; without it
    `ReplyGuard` would veto every activation call.

    One plugin PER TAG, like the model and the guard: its per-agent skill state
    (a `WeakKeyDictionary` keyed by agent) never spans two tags. Construction
    does no I/O; a directory source is read through the agent's sandbox at first
    invocation.

    R-29 holds through activation. The `skills` tool resolves a name by pure
    dict lookup against the operator-loaded set (`AgentSkills._skills_for`),
    exactly as restrictive as `nora.skills.loader.SkillStore.resolve`: loading
    happens only from this `skills_dir` (operator config), never from a
    `skill_name` a message supplies, so a message naming a path, a URL or an
    inline blob is a dict miss. `strict=False` so a malformed operator skill
    warns and is skipped rather than crashing a tag (R-5).

    `skills_dir=None` leaves the layer inert (offline/eval compositions): no
    plugin, no `<available_skills>` block, no activation tool.
    """
    plugins: list[Plugin] = (
        [AgentSkills(str(skills_dir), strict=False)] if skills_dir is not None else []
    )
    # Annotated explicitly: `[guard] + ([tool_call_log] if ... else [])` infers
    # `list[ToolCallLog | ReplyGuard]`, which mypy's invariant-list check refuses
    # to hand to `Agent(hooks=...)` even though both are `HookProvider`s.
    hooks: list[HookProvider | HookCallback[Any]] = [guard]
    if tool_call_log is not None:
        hooks.append(tool_call_log)
    # After the guard: `BeforeToolCallEvent` dispatches in registration order,
    # and a tool-surface hook (`ShelfLoader`) must see the guard's verdict so it
    # never loads a tool the guard cancelled. Before `reasoning_watch`, which
    # must stay last (below).
    hooks.extend(tool_hooks)
    # Last, and the position is load-bearing. `BeforeModelCallEvent` dispatches
    # in registration order (only the After events reverse), so a watch
    # registered after `ReplyGuard` reads an `event.cancel` the guard has already
    # set. The guard cancels the pass that follows a terminal verb; at that
    # moment `agent.messages` holds the reasoning-carrying assistant message a
    # naive watch would count as a strip, on a request that is never formatted.
    # `nora.agent.reasoning_strip.ReasoningStripWatch`'s docstring carries this
    # and `tests/unit/test_reasoning_strip_probe.py` holds it.
    if reasoning_watch is not None:
        hooks.append(reasoning_watch)
    return Agent(
        model=model,
        plugins=plugins,
        # Identity, so a trace can be found. `name` becomes the span name;
        # `agent_id` is stable rather than per-tag because the tag is already a
        # trace attribute (`nora.obs.tracing.trace_attributes`), and a per-tag
        # agent_id would make every trace its own agent.
        name=agent_name,
        agent_id=agent_name,
        # Fresh per agent; see RetryPolicy's docstring.
        retry_strategy=ModelRetryStrategy(
            max_attempts=retry.max_attempts,
            initial_delay=retry.initial_delay,
            max_delay=retry.max_delay,
        ),
        system_prompt=system_prompt,
        tools=tools,
        # pre_fill/messages are plain dicts end-to-end (build_prefill in
        # nora.agent.prefill through nora.engagement's stages/pipeline, none of
        # which import strands; see
        # tests/test_boundaries.py::test_strands_import_boundary). Strands'
        # `Agent` wants `list[Message]` (a TypedDict); the dicts have the right
        # shape at runtime and only the static type is asserted here.
        messages=list(pre_fill),  # type: ignore[arg-type]
        hooks=hooks,
        tool_executor=_executor_or_shared(tool_executor),
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
    reasoning_watch: HookProvider | None = None,
    trace_attributes: dict,
    retry: RetryPolicy,
    agent_name: str = DEFAULT_DREAM_AGENT_NAME,
    tool_executor: ToolExecutor | None = None,
    tool_hooks: tuple[HookProvider, ...] = (),
) -> Agent:
    """The Dreamer's agent: a loop that may LOOK and may not ACT.

    A sibling of `create_agent` rather than a flag on it. It lives here because
    this module is the only one allowed to construct Strands objects (spec
    C.7). Each `create_agent` parameter that is absent is welded to Nora's turn:

    - no `pre_fill`: a tag's prefill is the transcript, the peer card and the
      room; the Dreamer is handed its batch by the runtime as the invocation's
      own prompt, and there is no conversation to reconstruct;
    - no `skills_dir`: DEC-50's layer injects `<available_skills>` and registers
      the `skills` activation tool for a model composing a reply. A research loop
      has an objective, not a repertoire, and a tool the guard admits is a tool
      the Dreamer would have to budget;
    - no `tool_call_log`: that log replays one conversation's earlier lookups to
      a later turn in the same conversation, and the Dreamer has no conversation.

    `guard` is typed `HookProvider`, not `ReplyGuard` or `DreamGuard`, because
    what this constructor needs from it is exactly what `Agent(hooks=...)` needs.
    The guard that belongs here is `DreamGuard` (R-7 default-deny, no terminal
    verb, no deliverer); typing the parameter to that class would make this
    module import it for a constraint the SDK does not impose.

    `tool_choice` is not set here: it is a property of the MODEL, which arrives
    already built with `create_model_provider(..., tool_choice=AFTER_SPEAKING)`
    (see that function for why a loop with no terminal verb cannot run under
    `"required"`). Setting it here would put one fact in two places and let a
    caller pass a `"required"` model that could not stop.

    The rest is `create_agent`'s posture:
    `conversation_manager=NullConversationManager()` and `session_manager=None`
    (D4: no agent memory; continuity is the store), `callback_handler=None`,
    `load_tools_from_directory=False` (the tool set passed in is a ceiling, not a
    default), and a fresh `ModelRetryStrategy` per agent (see `RetryPolicy`; dream
    runs overlap the way tags do).
    """
    hooks: list[HookProvider | HookCallback] = [guard]
    if reasoning_watch is not None:
        hooks.extend((reasoning_watch,))
    hooks.extend(tool_hooks)
    return Agent(
        model=model,
        # Identity, so a background run is findable in Langfuse apart from her
        # turns.
        name=agent_name,
        agent_id=agent_name,
        retry_strategy=ModelRetryStrategy(
            max_attempts=retry.max_attempts,
            initial_delay=retry.initial_delay,
            max_delay=retry.max_delay,
        ),
        system_prompt=system_prompt,
        tools=tools,
        # After the guard, for `create_agent`'s reason (see the comment on its
        # `hooks` list). `None` is the inert default.
        hooks=hooks,
        tool_executor=_background_executor_or_shared(tool_executor),
        conversation_manager=NullConversationManager(),
        session_manager=None,
        callback_handler=None,
        load_tools_from_directory=False,
        trace_attributes=trace_attributes,
    )


#: The sub-agent worker's history window, in messages. The oldest steps scroll
#: away so a long job's history stays bounded, and the checkpoint, re-anchored
#: into the pinned brief (`SubagentProgress`), carries the place.
SUBAGENT_WINDOW_MESSAGES = 40


def create_subagent(
    *,
    model: OpenAIModel,
    system_prompt: str,
    tools: list,
    guard: HookProvider,
    progress: HookProvider,
    reasoning_watch: HookProvider | None = None,
    skills_dir: str | Path,
    trace_attributes: dict,
    retry: RetryPolicy,
    agent_name: str = "subagent",
    tool_executor: ToolExecutor | None = None,
) -> Agent:
    """The sub-agent worker's agent: a background loop that does ONE objective for
    Nora and answers with a report.

    A sibling of `create_dream_agent` rather than a flag on it, living here for
    the same reason (only this module constructs Strands objects, spec C.7). What
    differs from the Dreamer's:

    - it has a `skills_dir`. The worker's know-how arrives as skills, as
      `nora/skills/document-creation` does for a tag turn, so `AgentSkills` is
      built over `skills_dir` as `create_agent` builds it (one plugin per agent,
      `strict=False`). The `skills` activation tool the plugin registers is
      admitted by the guard because the runner adds its name to the guard's
      membership set.
    - `progress` is a second hook, after the guard, which counts rounds, records
      the last tool and collects the `document_id`s of `document_publish` results
      that came back `ok`.
    - `reasoning_watch` is optional, as the Dreamer's: the worker runs at a
      reasoning effort (`[subagent] reasoning_effort`), and the watch counts
      continuation calls whose reasoning the transport threw away.
    - no `pre_fill` (the worker starts blank; its brief is the invocation's
      prompt) and no `tool_call_log` (no conversation to replay).

    `guard` is typed `HookProvider` for `create_dream_agent`'s reason; the guard
    that belongs here is a `DreamGuard` built with `per_tool_ceilings=False`
    (DEC-247). The posture is otherwise the Dreamer's: no session manager, no
    stdout handler, no tools loaded from a directory, and a fresh
    `ModelRetryStrategy` per agent. The history differs: a sliding window of
    `SUBAGENT_WINDOW_MESSAGES`, not `NullConversationManager`, because a job runs
    hundreds of rounds in one invocation. `progress` re-anchors the checkpoint
    after each trim.

    `tool_choice` is not set here: it belongs to the MODEL, built with
    `AFTER_SPEAKING` so the loop ends when the model stops calling tools.
    """
    return Agent(
        model=model,
        plugins=[AgentSkills(str(skills_dir), strict=False)],
        name=agent_name,
        agent_id=agent_name,
        retry_strategy=ModelRetryStrategy(
            max_attempts=retry.max_attempts,
            initial_delay=retry.initial_delay,
            max_delay=retry.max_delay,
        ),
        system_prompt=system_prompt,
        tools=tools,
        # `reasoning_watch` last, for `create_agent`'s reason: it must read the
        # guard's verdicts.
        hooks=[guard, progress] if reasoning_watch is None else [guard, progress, reasoning_watch],
        tool_executor=_background_executor_or_shared(tool_executor),
        # A sliding window, trimmed before every model call (`per_turn=True`; the
        # default trims once per invocation, and a job is one invocation of
        # hundreds of rounds) with the brief, the first message, pinned. The
        # agent registers the manager's own callback before `hooks`, so
        # `progress` re-anchors the checkpoint on the trimmed list.
        conversation_manager=SlidingWindowConversationManager(
            window_size=SUBAGENT_WINDOW_MESSAGES, per_turn=True, pin_first=1,
        ),
        session_manager=None,
        callback_handler=None,
        load_tools_from_directory=False,
        trace_attributes=trace_attributes,
    )


#: How many messages the browse sub-agent keeps: each page snapshot is a few
#: thousand tokens, so a long task keeps its brief and its recent pages only.
BROWSE_WINDOW_MESSAGES = 24


def create_browse_agent(
    *, model: OpenAIModel, system_prompt: str, tools: list, trace_attributes: dict,
    retry: RetryPolicy, agent_name: str = "browse",
) -> Agent:
    """The browse sub-agent: one browser task for Nora, in her own computer's
    browser, with page tools only.

    Built here because only this module constructs Strands objects (spec C.7).
    Unlike `create_subagent` it has no skills, no guard and no progress hook:
    its tools are the whole of what it can do, its step budget is counted inside
    those tools (`nora.agent.browse`), and the loop's three caps come from
    `build_limits` at `invoke`. The window keeps the brief pinned and the most
    recent pages, as `create_subagent`'s does for long jobs. `tool_choice`
    belongs to the model (`AFTER_SPEAKING`): the loop ends when the model, having
    called `browse_done`, stops calling tools.
    """
    return Agent(
        model=model,
        name=agent_name,
        agent_id=agent_name,
        retry_strategy=ModelRetryStrategy(
            max_attempts=retry.max_attempts,
            initial_delay=retry.initial_delay,
            max_delay=retry.max_delay,
        ),
        system_prompt=system_prompt,
        tools=tools,
        conversation_manager=SlidingWindowConversationManager(
            window_size=BROWSE_WINDOW_MESSAGES, per_turn=True, pin_first=1,
        ),
        # One action at a time: every page tool acts on the ONE page, so two
        # `page_type`s from one response would interleave their focus and
        # typing, and a click batched after `browse_done` would still run.
        tool_executor=SequentialToolExecutor(),
        session_manager=None,
        callback_handler=None,
        load_tools_from_directory=False,
        trace_attributes=trace_attributes,
    )


#: Strands' canned recovery notice (`_recover_message_on_max_tokens_reached`,
#: `event_loop.py:601`) as two fragments, so a partial-text extraction can
#: exclude a block carrying it without asserting a full-sentence match. One
#: shared definition, also used by `nora.agent.structured.ask_text`.
MAX_TOKENS_NOTICE_PREFIX = "The selected tool "
MAX_TOKENS_NOTICE_INFIX = "tool use was incomplete due to maximum token limits being reached"


async def invoke(agent: Agent, tag_message: str, *, invocation_state: dict, limits: dict) -> Any:
    """Run one turn and return Strands' `AgentResult`, `stop_reason` included.

    The return value must not be dropped (R-23). When one of `limits`' three caps
    binds, Strands' `_check_limits` yields an `EventLoopStopEvent` carrying
    `stop_reason="limit_total_tokens"` (or `limit_turns` / `limit_output_tokens`)
    and returns normally: no exception, no log line, no warning anywhere in the
    SDK. `AgentResult.stop_reason` is the only channel that says a turn was cut
    off rather than finished, so `nora.app.agent_runner.LiveAgentRunner` reads it
    off this result and hands it up the stage chain.

    Typed `Any` rather than `strands.agent.AgentResult` so callers in `nora.app`,
    which mypy checks strictly and which must not import `strands`
    (`tests/test_boundaries.py::test_strands_import_boundary`), can narrow the one
    attribute they want without naming the SDK type.

    Raises:
        PartialReplyError: `MaxTokensReachedException` is caught here, not left
            to bubble. Strands raises it AFTER appending the partial message to
            `agent.messages` (event_loop.py:640), so the words are recoverable.
            Left uncaught, `invoke.run`'s generic `except Exception` would count
            a silent failure and drop the partial reply. The partial text is
            extracted from the last message's text blocks and carried up so the
            pipeline can deliver it. `nora.agent` may import `strands` (only
            `nora.app` may not).
    """
    try:
        return await agent.invoke_async(
            tag_message,
            invocation_state=invocation_state,
            # `limits` is a plain dict end-to-end for the same reason as
            # `pre_fill` above. Strands' `Limits` is a TypedDict with the same
            # three keys build_limits() fills (turns/output_tokens/total_tokens);
            # only the static type differs.
            limits=limits,  # type: ignore[arg-type]
        )
    except MaxTokensReachedException:
        # The partial message is the LAST entry in `agent.messages` (Strands
        # appended it before raising, event_loop.py:640). Extract its text
        # blocks the way `final_text` does, from the message dict directly
        # (there is no `AgentResult` here).
        #
        # Exclude Strands' canned recovery notice. Before appending, the SDK
        # replaces every tool-use block with a fixed sentence: "The selected
        # tool X's tool use was incomplete due to maximum token limits being
        # reached." (`_recover_message_on_max_tokens_reached`, event_loop.py:601).
        # A cut-off mid-send_reply therefore leaves a message whose only text
        # blocks are that notice, and delivering it would leak SDK machinery into
        # the group (R-5/R-6). Blocks of that shape are dropped, so a partial
        # that is only the notice yields partial_text="" and the turn stays
        # silent.
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


# What the model is asked for when its loop ran out mid-work. Deliberately not
# "summarise": she is answering the person who asked, in her own voice, with what
# she has; a summary of her own research reads like a report, not a reply.
#
# It says nothing about budgets, tokens or limits, which is load-bearing:
# whatever comes back is DELIVERED to the group, and
# `tests/integration/test_turn_budget_is_legible.py::
# test_the_budget_fallback_says_nothing_diagnostic_to_the_group` pins that the
# room never reads the machine's excuses. Telling the model it ran out of budget
# is the surest way to make it say so.
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

    A turn cut off by one of R-8's caps has usually done all the work, and the
    group should get her answer rather than a canned apology.

    It runs on the agent, not from the trace: `TagTrace.messages` is truncated
    (`DEFAULT_MAX_TRACE_CHARS` is 10,000) and carries a small fraction of what she
    read, so drafting from it would defeat the purpose. The full context exists
    only on this `agent`.

    A second `invoke_async` is not instantly re-cut: Strands' caps are
    per-invocation. `_check_limits` reads
    `event_loop_metrics.latest_agent_invocation`, documented in the SDK as
    "scoped to the current invocation so caps don't fire prematurely on the
    second invoke against a reused agent". So this call gets a fresh budget.
    `limits` is still passed, since an unbounded call on a turn that just proved
    it can run away is not acceptable.

    `tool_choice` is flipped because the agent runs `tool_choice="required"`
    (DEC-21) to make her reach for `send_reply`, whereas here prose is wanted and
    the pin would force yet another tool call. `update_config` is the supported
    route, pinned by
    `tests/test_strands_api_contract.py::test_obligation_7...`; the guard uses it
    too. It is restored in the `finally` because the agent is not ours to leave
    altered.

    Fail-closed: returns "" on a timeout, an exception, or a model that answers
    with nothing, and the caller keeps the canned line. This improves on the
    fallback and never replaces it, and an exhausted turn must not also hang,
    hence its own timeout rather than the tier's.
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
            # A model that will not take the flip still gets the call: it may
            # answer in prose anyway, and refusing to draft is worse than
            # drafting with tools on.
            log.warning("draft: could not disable tool_choice; drafting anyway")
    try:
        result = await asyncio.wait_for(
            agent.invoke_async(
                _DRAFT_INSTRUCTION, invocation_state={},
                limits=limits,  # type: ignore[arg-type]
            ),
            timeout=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 — incl. TimeoutError; fail-closed, see docstring
        # Never re-raise into a turn that has already failed once; the caller
        # falls back to the canned line.
        log.warning("draft: exhausted-loop draft failed (%s)", type(exc).__name__)
        return ""
    finally:
        if restore and model is not None:
            try:
                model.update_config(params=previous)
            except Exception:  # noqa: BLE001 — the agent is discarded next line anyway
                log.warning("draft: could not restore tool_choice")
    return final_text(result)


def remaining_limits(agent: Agent, limits: dict) -> dict | None:
    """What is left of `limits` after the agent's LATEST invocation, or None
    when any cap is already spent.

    Strands' caps are per-invocation (`draft_from_exhausted_loop` above): a
    second `invoke_async` on the same agent starts from zero. A continuation that
    must count against the SAME turn's ceilings (the summons' one follow-up,
    `nora.app.agent_runner.LiveAgentRunner.invoke`'s `follow_up`) is therefore
    handed the remainder, read off the metrics `_check_limits` reads
    (`latest_agent_invocation`: its cycle count and its
    `outputTokens`/`totalTokens`). A cap absent from `limits` stays absent.
    Fail-closed: metrics the SDK did not keep read as None, never as a full
    fresh budget."""
    invocation = getattr(
        getattr(agent, "event_loop_metrics", None), "latest_agent_invocation", None,
    )
    if invocation is None:
        return None
    usage = getattr(invocation, "usage", None)
    cycles = getattr(invocation, "cycles", None)
    if not isinstance(usage, Mapping) or not isinstance(cycles, list):
        return None
    spent: dict[str, Any] = {
        "turns": len(cycles),
        "output_tokens": usage.get("outputTokens"),
        "total_tokens": usage.get("totalTokens"),
    }
    left: dict[str, int] = {}
    for key, cap in limits.items():
        if cap is None:
            continue
        used = spent.get(key)
        # A cap nothing measures, or a count that is not a number, is not a cap
        # with room left: no continuation.
        if not isinstance(used, int) or isinstance(used, bool):
            return None
        left[key] = cap - used
    if any(value <= 0 for value in left.values()):
        return None
    return left


def final_text(result: Any) -> str:
    """The model's last PROSE, or "": the words it wrote instead of calling
    `send_reply` (DEC-93).

    Empty on every healthy turn: a turn that used the tool has already said what
    it had to say, and its final message carries a `toolUse` block rather than
    text. This covers the turn that ends `end_turn` with a complete,
    group-addressed reply in a `text` block that strict-silence would discard.

    Defensive throughout (`getattr`, `isinstance`) because this reads a
    third-party result shape on the reply path: a Strands version that renames
    `message` must degrade to "" rather than raise into a turn that otherwise
    succeeded.

    It lives here, beside `invoke`, because it reads the SDK shape `invoke`
    returns, and knowing a Strands shape belongs in this module (`nora.app` may
    not import `strands`). The Dreamer needs the same extraction (its loop ends
    with the model's structured result as authored text) and `nora.agent` may not
    import `nora.app`.
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
