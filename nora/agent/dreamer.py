"""Bounded model invocation and scoped read tools for Cognition, Wake and Review."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..domain.models import TranscriptRow
from ..domain.ports import (
    RosterSource,
)
from ..domain.reasoning_strip import dreamer_path
from ..domain.turn_deadline import turn_deadline
from ..obs.metrics import Metrics
from . import factory
from .dream_guard import DreamGuard
from .ports import ToolSpec, adapt
from .reasoning_strip import ReasoningStripWatch
from .registry import ToolRegistry
from .tag_trace_extract import extract_tag_trace
from .token_reservation import TokenReservation
from .turn_recording import TurnRecording

log = logging.getLogger("nora.dreamer")

MAX_TOOL_RESULT_CHARS = 8000
MAX_MEMORY_RESULT_CHARS = 4000
_TOOL_RESULT_CAPS: Mapping[str, int] = {
    "web_fetch": MAX_TOOL_RESULT_CHARS,
    "web_search": MAX_TOOL_RESULT_CHARS,
    # The memory graph's search and read verbs (DEC-316): the tighter number.
    "memory_search": MAX_MEMORY_RESULT_CHARS,
    "memory_read": MAX_MEMORY_RESULT_CHARS,
}

#: The same table under a public name, for the Cognition session's guard
#: (`nora.app.cognition_wiring`): one table, two readers, never a second copy.
TOOL_RESULT_CAPS: Mapping[str, int] = _TOOL_RESULT_CAPS

@dataclass(frozen=True)
class AdaptorBudget:
    """The model loop's token, turn and wall-clock limits."""

    turns: int
    total_tokens: int
    output_tokens: int
    timeout_seconds: float


@dataclass(frozen=True)
class AgentRunResult:
    """Model stop reason, reported usage and trace evidence from one invocation."""

    stop_reason: str | None = None
    total_tokens: int | None = None
    messages: tuple[dict[str, Any], ...] = ()
    requirements: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HomeRoom:
    """The policy room that provides the context for scoped tools."""

    unit: str
    platform: str
    conversation_id: str
    conversation_type: str = "group"
    location_trusted: bool = False


@dataclass(frozen=True)
class AdaptorGrant:
    """Permission to bind read tools to the agenda room."""

    home_room: bool = False


def cognition_grant() -> AdaptorGrant:
    """The Cognition adaptor's grant: a real room, and NO act verb. Its write, pace
    and act verbs are not registry tools at all: the session holds them as its own
    closures (`nora.app.cognition_session`), so nothing here widens the read-only
    ceiling. It is SCOPE-resolved (`RegistryToolSource.build` with the agenda
    item's room as the key), so lineage and the roster's disclosure guard are the
    room's own."""
    return AdaptorGrant(home_room=True)


class DreamToolSource(Protocol):
    """Build read-only tools against the scope of the session."""

    def read_only(self) -> frozenset[str]: ...

    def build(
        self, *, key: str, names: frozenset[str],
        adaptor: str = "", grant: AdaptorGrant | None = None,
        on_rows: Callable[[Sequence[TranscriptRow]], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> list[Any]: ...

    def tool_param_names(self) -> Mapping[str, frozenset[str]]: ...


class _ModelSource(Protocol):
    """One model provider per run, for the same reason there is one per tag:
    `tool_choice` is per-invocation state and two runs overlap."""

    def create(self) -> Any: ...


class RegistryToolSource:
    """Bind read-only registry tools to a policy room and its roster."""

    def __init__(
        self, *, registry: ToolRegistry, store: Any, persons: Any,
        metrics: Metrics, ai_peer: str = "nora", access: Any = None,
        scope_rooms: Mapping[str, HomeRoom] | None = None,
        rosters: Mapping[str, RosterSource] | None = None,
        self_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._registry = registry
        self._store = store
        self._access = access
        self._persons = access.persons_view() if access is not None else persons
        self._metrics = metrics
        self._ai_peer = ai_peer
        self._scope_rooms = scope_rooms if scope_rooms is not None else {}
        # Platform -> roster source, the same map `FirePath` holds: a home
        # room on a platform with no roster answers `available=False`, the
        # honest state, never an empty room.
        self._rosters = dict(rosters or {})
        self._self_ids = frozenset(self_ids)
        self._home_room_reported: set[tuple[str, str]] = set()

    def read_only(self) -> frozenset[str]:
        return self._registry.read_only_tools()

    def tool_param_names(self) -> Mapping[str, frozenset[str]]:
        return self._registry.tool_param_names()

    def build(
        self, *, key: str, names: frozenset[str],
        adaptor: str = "", grant: AdaptorGrant | None = None,
        on_rows: Callable[[Sequence[TranscriptRow]], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> list[Any]:
        from .room import Room

        allowance = grant if grant is not None else AdaptorGrant()
        home = self._scope_rooms.get(key) if allowance.home_room else None
        if allowance.home_room and home is None:
            self._metrics.inc("nora_dream_home_room_missing_total", adaptor=adaptor, unit="")
            seen = (adaptor, key)
            if seen not in self._home_room_reported:
                self._home_room_reported.add(seen)
                log.error(
                    "dream adaptor %r is granted a home room but scope %r "
                    "is not a conversation in policy.toml; building without a room",
                    adaptor, key,
                )
        wanted = frozenset(names) & self.read_only()
        if not wanted:
            return []
        if home is not None:
            room = Room(
                platform=home.platform, conversation_id=home.conversation_id,
                roster_source=self._rosters.get(home.platform),
                persons=self._persons, access=self._access,
                # No tagger: the run is owed to nobody. `Room.scope()`'s degraded
                # branch therefore narrows to nothing when the roster cannot be
                # read, and `where_is` answers `unavailable` there.
                self_ids=self._self_ids,
            )
        else:
            room = Room(
                platform="", conversation_id=key, roster_source=None,
                persons=self._persons, access=self._access,
            )
        registry = (
            self._registry if clock is None else self._registry.with_clock(clock)
        )
        return registry.surface(
            grant=wanted,
            store=self._store,
            conversation_id=home.conversation_id if home is not None else key,
            conversation_type=home.conversation_type if home is not None else "group",
            persons=self._persons,
            deliverer=None,
            metrics=self._metrics,
            room=room,
            location_trusted=home.location_trusted if home is not None else False,
            dream_adaptor=adaptor,
            on_rows=on_rows,
        ).tools


class DreamerRuntime:
    """Runs a session's agent inside its model and wall-clock limits."""

    def __init__(
        self,
        *,
        models: _ModelSource,
        tools: DreamToolSource,
        retry: factory.RetryPolicy,
        metrics: Metrics,
        reasoning_strip_store: Any = None,
    ) -> None:
        self._models = models
        self._tools = tools
        self._retry = retry
        self._metrics = metrics
        self._reasoning_strip_store = reasoning_strip_store

    async def run_agent(
        self, *, system_prompt: str, prompt: str,
        tools: Sequence[Any] = (), strands_tools: Sequence[Any] = (),
        budget: AdaptorBudget, guard: DreamGuard | None = None,
        trace_attributes: Mapping[str, str] | None = None,
        agent_name: str = "session", spend: Any = None,
        max_trace_chars: int = 10_000,
        reservation: TokenReservation | None = None,
        tool_hooks: Sequence[Any] = (),
    ) -> AgentRunResult:
        """Run the bounded agent loop.

        `spend` is the caller's mutable holder: its `.total`, `.messages` and
        `.requirements` are filled even on a failed or cancelled loop, because
        a failed session is the costliest kind. An unread usage stays `None`
        — `unavailable`, never a quiet zero. `tool_hooks` adds a surface's own
        providers (a `ToolSurface` shelf loader) beside the recording."""
        # The default guard is the SESSION shape (DEC-247): the tools passed
        # are the R-7 membership set, R-99 reads the runtime's own
        # argument-name table and the result caps hold — but no count vetoes
        # a call; rounds, tokens and the wall clock are a session's bound.
        # Callers holding ceilings pass their own guard.
        g = guard or DreamGuard(
            adaptor=agent_name,
            budgets=dict.fromkeys(
                (n for t in (*tools, *strands_tools)
                 if (n := str(
                     getattr(t, "tool_name", None) or getattr(t, "__name__", "")
                 ))),
                0,
            ),
            metrics=self._metrics, result_caps=_TOOL_RESULT_CAPS,
            param_names=self._tools.tool_param_names(), per_tool_ceilings=False,
        )
        attributes = dict(trace_attributes or {})
        return await self._invoke_agent(
            system_prompt=system_prompt, prompt=prompt,
            tools=as_strands_tools(tools, strands_tools),
            guard=g, budget=budget,
            trace_attributes=attributes, agent_name=agent_name,
            max_trace_chars=max_trace_chars, reservation=reservation,
            spend=spend, tool_hooks=tool_hooks,
        )

    async def _invoke_agent(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        guard: DreamGuard, budget: AdaptorBudget,
        trace_attributes: Mapping[str, str], agent_name: str,
        max_trace_chars: int, reservation: TokenReservation | None,
        spend: Any, tool_hooks: Sequence[Any] = (),
    ) -> AgentRunResult:
        """One bounded agent loop: `_invoke_once` creates the agent and runs
        it inside the deadline and the reservation, then this reads its
        usage and its trace — on EVERY exit, failure and cancellation
        included, because a failed session is the costliest kind and its
        usage lives in the agent.

        It RAISES on failure, the session contract (`CognitionAgentRunner`'s):
        a timed-out or errored loop is not a session that returned, and only
        a loop that returned may close its items."""
        recording = TurnRecording()
        created: dict[str, Any] = {}
        result: Any = None
        extra = (reservation,) if reservation is not None else ()
        try:
            _agent, _baseline, result = await self._invoke_once(
                system_prompt=system_prompt, prompt=prompt, tools=tools,
                guard=guard, budget=budget, trace_attributes=trace_attributes,
                agent_name=agent_name,
                tool_hooks=(*tool_hooks, recording, *extra),
                agent_out=created,
            )
            stop_reason = (
                "limit_total_tokens"
                if reservation is not None and reservation.stopped
                else getattr(result, "stop_reason", None)
            )
            if stop_reason is not None:
                self._metrics.inc(
                    "nora_dream_stop_reason_total",
                    adaptor=agent_name, reason=str(stop_reason),
                )
        finally:
            # The trace and the spend, on EVERY exit: a failed, timed-out or
            # cancelled loop is the costliest kind and its usage lives in the
            # agent even when no result came back. Sync work only — nothing
            # here may await, for the reason `_invoke_once`'s flush comment
            # gives. `created` is filled BY `_invoke_once` the moment the
            # agent exists — the usage and the retained partial messages
            # live there even when the loop raises or is cancelled before
            # returning. `agent` may not exist (a `create_dream_agent`
            # failure) — nothing to extract then.
            agent = created.get("agent")
            baseline = created.get("baseline", 0)
            trace = (
                extract_tag_trace(
                    agent, baseline=baseline,
                    tag_id=str(trace_attributes.get("cognition.session_id", "")),
                    conversation_id="", transcript=(), outcome="silent",
                    max_chars=max_trace_chars, metrics=self._metrics,
                )
                if agent is not None else None
            )
            requirements = recording.to_json()
            total = (
                reservation.spent if reservation is not None
                else _total_tokens(result) if result is not None
                else _usage(_agent_usage(agent)) if agent is not None
                else None
            )
            _fill_spend(
                spend, total,
                () if trace is None else tuple(trace.messages), requirements,
            )
        return AgentRunResult(
            stop_reason=stop_reason, total_tokens=total,
            messages=() if trace is None else tuple(trace.messages),
            requirements=requirements,
        )

    async def _invoke_once(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        guard: DreamGuard, budget: AdaptorBudget,
        trace_attributes: Mapping[str, str], agent_name: str,
        tool_hooks: Sequence[Any] = (),
        agent_out: dict[str, Any] | None = None,
    ) -> tuple[Any, int, Any]:
        """Create and invoke an agent under its guard and deadline.

        Returns `(agent, baseline, result)`: `baseline` is the agent's
        message count before the loop, for a caller's trace extraction.
        When `agent_out` is given it is filled with `agent` and `baseline`
        the MOMENT the agent exists — before the loop runs — so a caller's
        `finally` still reaches the usage and the retained partial messages
        when the loop raises or is cancelled and no tuple returns.
        RAISES on timeout, failure and cancellation — translating those is
        the caller's contract. The reasoning watch is flushed on EVERY exit,
        success included, before the exception propagates or the result
        returns.
        """
        watch = ReasoningStripWatch(
            path=dreamer_path(agent_name), store=self._reasoning_strip_store,
            metrics=self._metrics,
        )
        agent = factory.create_dream_agent(
            model=self._models.create(), system_prompt=system_prompt,
            tools=tools, guard=guard, reasoning_watch=watch,
            trace_attributes=dict(trace_attributes), retry=self._retry,
            agent_name=dreamer_path(agent_name), tool_hooks=tuple(tool_hooks),
        )
        baseline = len(getattr(agent, "messages", ()))
        if agent_out is not None:
            agent_out["agent"] = agent
            agent_out["baseline"] = baseline
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget.timeout_seconds
        try:
            with turn_deadline(budget.timeout_seconds):
                # `asyncio.timeout` raises TimeoutError only after the
                # deadline callback fired; an external task.cancel()
                # propagates as CancelledError.
                async with asyncio.timeout_at(deadline):
                    result = await factory.invoke(
                        agent, prompt, invocation_state={},
                        limits=factory.build_limits(
                            output_tokens=budget.output_tokens,
                            turns=budget.turns,
                            total_tokens=budget.total_tokens,
                        ),
                    )
        except BaseException:
            # Flushed here rather than in `finally`: an await in `finally`
            # on a cancelled task re-raises out of the cleanup path, and a
            # record of something that already happened must never change
            # how a run ends. `flush` is idempotent.
            await watch.flush()
            raise
        await watch.flush()
        return agent, baseline, result


def as_strands_tools(*tool_groups: Sequence[Any]) -> list[Any]:
    """The runtime's ONE way to a Strands tools list, for a session adaptor
    that holds verbs, tools or both: Strands `tool_spec`s pass through, bare
    callables go through `nora.agent.ports.adapt` (`_as_tool`, the one place
    a `ToolSpec` becomes a tool), first occurrence kept by tool name so a
    session cannot register the same verb twice."""
    out: dict[str, Any] = {}
    for tools in tool_groups:
        for tool in tools:
            name = str(
                getattr(tool, "tool_name", None)
                or getattr(tool, "__name__", "") or ""
            )
            if not name:
                continue
            if hasattr(tool, "tool_spec") and hasattr(tool, "tool_name"):
                # Already a Strands tool (a registry read): through untouched.
                out.setdefault(name, tool)
            elif name not in out:
                out[name] = _as_tool(tool) if callable(tool) else tool
    return list(out.values())


def _fill_spend(
    spend: Any, total: int | None, messages: tuple[Mapping[str, Any], ...],
    requirements: Mapping[str, Any],
) -> None:
    """The caller's mutable spend holder, filled on every exit —
    `spend`-as-`SessionTokens`, duck-typed so the runtime never imports an
    app-layer type."""
    if spend is None:
        return
    spend.total = total
    spend.messages = messages
    spend.requirements = requirements


def _agent_usage(agent: Any) -> Any:
    """The usage record one loop left on the agent (a Strands accounting
    mapping), there to be read even when the loop never returned a result —
    the failed-session case `run_agent`'s `spend` holder exists for. A read
    that itself fails answers None: it must never mask the loop's own
    error."""
    try:
        return getattr(
            getattr(agent, "event_loop_metrics", None), "accumulated_usage", None,
        )
    except Exception:
        log.warning("agent usage unread", exc_info=True)
        return None


def _usage(usage: Any) -> int | None:
    """`usage["totalTokens"]` of a Strands accounting mapping, or None when it
    is absent, not a number, or ZERO: a loop that made a model call spent
    something, so a zero is a provider that reported no usage, not a free
    session. An unread count is never a zero."""
    total = usage.get("totalTokens") if isinstance(usage, Mapping) else None
    ok = isinstance(total, int) and not isinstance(total, bool) and total > 0
    return total if ok else None


def _total_tokens(result: Any) -> int | None:
    """The loop's total tokens as Strands accumulated them over every cycle
    (`AgentResult.metrics.accumulated_usage`), or None (see `_usage`)."""
    return _usage(getattr(getattr(result, "metrics", None), "accumulated_usage", None))


VERB_CALLS = 6


def _as_tool(verb: Callable[..., Any]) -> Any:
    """An adaptor's write verb (a typed closure) as a Strands tool, through
    `nora.agent.ports.adapt`, the one place a `ToolSpec` becomes one (ADR 0015:
    the schema comes from the closure's signature and docstring)."""
    return adapt(ToolSpec(
        name=verb.__name__, description="", schema={}, call=verb, budget=VERB_CALLS,
    ))
