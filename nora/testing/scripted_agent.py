"""Scripted model double, shared by `tests/` and `evals/` — one definition.

`ScriptedTurn`/`FakeAgentRunner` replay a scripted tool-call sequence against the
REAL tools and the REAL guards a pipeline builds, structurally satisfying
`nora.engagement.stages.invoke.AgentRunner`'s `Protocol` (never imported here —
`nora.engagement` sits above `nora.testing` in the layers contract, so the match
is structural, the same way `nora.app.agent_runner.LiveAgentRunner` satisfies it).
`AgentRunner` is a stage-level `Protocol` (`nora/engagement/stages/invoke.py`),
not one of `nora/domain/ports.py`'s ports, so it is not bound by this project's
"a port never gains a second implementation without a shared contract suite"
rule.

`group_event` builds the signal-cli-shaped raw event dict every scripted
scenario feeds into a pipeline's `Dispatcher.on_event`.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
from collections.abc import Callable
from typing import Any

from nora.domain.tag_trace import TagTrace


def group_event(*, body: str, sender: str = "+852111", conv: str = "g1",
                 name: str = "Miles", ts: int = 1,
                 mentions: list[dict] | None = None) -> dict:
    """The signal-cli-shaped raw event every scripted scenario feeds a pipeline.

    `mentions` is the parallel array a Signal @-mention really travels in —
    `{name, number, uuid, start, length}` per mention, with `body` carrying
    U+FFFC at each `start`. Omitted by default because most scenarios have no
    mention; there is no way to express one WITHOUT it, which is how a scenario
    reproduces DEC-34's bug.
    """
    data: dict = {"message": body, "groupInfo": {"groupId": conv}, "timestamp": ts}
    if mentions:
        data["mentions"] = mentions
    return {"method": "receive", "params": {"envelope": {
        "source": sender, "sourceName": name, "timestamp": ts, "dataMessage": data}}}


@dataclasses.dataclass
class ScriptedTurn:
    """One model turn: tool calls to perform, then the final text.

    `bypass_guard` is an escape hatch for tests that specifically exercise a lower
    layer's own defenses (e.g. Deliverer's own empty-text/leak checks) independent of
    ReplyGuard's pre-tool veto — everywhere else it stays False and behavior is
    unchanged.

    `total_tokens`/`output_tokens` are what this cycle COSTS, so that a
    scripted turn can reach R-8's caps the way a real one does. They default
    to 0, which is the honest cost of a scripted turn that is not exercising
    the budget at all — a turn that wants to trip `total_tokens = 64000`
    states the number that trips it, and stops tripping it if the operator
    raises the cap. See `limit_stop_reason` below.
    """
    tool_calls: list[tuple[str, dict]] = dataclasses.field(default_factory=list)
    final_text: str = ""
    bypass_guard: bool = False
    total_tokens: int = 0
    output_tokens: int = 0


def limit_stop_reason(
    *, cycles: int, total_tokens: int, output_tokens: int, limits: dict | None,
) -> str | None:
    """The `stop_reason` Strands would return for this much spend — or None.

    A faithful transcription of `strands.event_loop.event_loop._check_limits`
    (strands 1.50.2), INCLUDING its priority order — turns, then
    total_tokens, then output_tokens — and its `>=` comparisons. The order is
    load-bearing rather than cosmetic: it is what decides which cap gets the
    blame when a turn crosses two at once, and the whole point of R-23 is that
    the reported reason is the true one.

    A cap that is absent from `limits` is not a cap: the SDK skips it, so an
    incomplete `limits` dict silently means "unbounded" for whatever it omits,
    which is why `nora.config.LimitsConfig` makes all three required.

    **A cap can only bind on a cycle that used tools.** `_check_limits` runs
    at the TOP of an event-loop cycle, and the loop only reaches another cycle
    when the previous one ended in `tool_use` — so a turn that answers in one
    shot can never trip a limit however much it spent. Callers must therefore
    only consult this after a cycle that would recurse; `FakeAgentRunner`
    below does. Nora's turns always use tools (`send_reply` IS one and
    `tool_choice="required"` forces a call — DEC-21), so this is the shape
    every real turn has. Pinned by
    `tests/test_agent_runner_stop_reason.py`, which drives the REAL SDK.

    Transcribed rather than imported because `nora.testing` must not import
    `strands` (`tests/test_boundaries.py::test_strands_import_boundary`), and
    because the SDK function takes an `Agent` and reads its live
    `event_loop_metrics` — there is no scalar entry point to call.
    """
    if not limits:
        return None
    turns_cap = limits.get("turns")
    if turns_cap is not None and cycles >= turns_cap:
        return "limit_turns"
    total_cap = limits.get("total_tokens")
    if total_cap is not None and total_tokens >= total_cap:
        return "limit_total_tokens"
    output_cap = limits.get("output_tokens")
    if output_cap is not None and output_tokens >= output_cap:
        return "limit_output_tokens"
    return None


def _accepted_arguments(tool, kwargs: dict) -> dict:
    """The subset of a model's arguments that would actually reach the tool body.

    Production never passes a model's raw arguments to the function. Strands
    validates `tool_use["input"]` through a pydantic model built from the
    callable's signature (`DecoratedFunctionTool.stream` →
    `FunctionToolMetadata.validate_input`), and pydantic ignores properties the
    model does not declare — so an argument that is not in `inputSchema` is
    silently dropped, and the call succeeds without it.

    A scripted turn that called `tool(**kwargs)` directly would instead raise
    `TypeError`, which is not a failure mode production has. That difference
    matters here specifically: DEC-24 removed the conversation parameters, and
    the behaviour that has to stay reproducible is a model supplying one ANYWAY
    — counted by `ReplyGuard`, dropped before the body. A fake that exploded on
    it could not express the case at all.
    """
    schema = (tool.tool_spec.get("inputSchema") or {}).get("json") or {}
    if "properties" not in schema:
        return kwargs
    declared = set(schema["properties"])
    return {k: v for k, v in kwargs.items() if k in declared}


async def _invoke_tool(tool, **kwargs):
    """Call a Strands @tool-decorated function the way a scripted turn exercises it."""
    result = tool(**_accepted_arguments(tool, kwargs))
    if asyncio.iscoroutine(result):
        result = await result
    return result


# ---- the SDK's hook dispatch, as this double performs it ------------------
#
# The guard is a `strands.hooks.HookProvider`. Production NEVER calls
# `evaluate`/`record` — it hands the provider to a `HookRegistry` and the SDK
# dispatches events at three points. Calling the two methods by hand would
# diverge: skipping `record` on a veto would cost no budget here and cost one
# in production, which CLAUDE.md's "make the fake behave like the real server"
# rule forbids — a regression test written against such a replay could not
# fail, because the guard's after-tool decision was never consulted.
#
# So the replay goes through `register_hooks` and dispatches the same three
# events Strands does, with the same field values (see `tests/
# test_strands_api_contract.py`, which asserts those fields against the real
# SDK). What a hook DECIDES is then the guard's business, not this module's.
#
# Keyed by class NAME rather than by the event class itself: `nora.testing` must
# not import `strands` (`tests/test_boundaries.py::test_strands_import_boundary`),
# and `add_callback` is handed the real class object, whose `__name__` needs no
# import to read.


class _HookRegistry:
    """The `strands.hooks.HookRegistry` surface a `HookProvider` actually uses."""

    def __init__(self) -> None:
        self.callbacks: dict[str, list] = {}

    def add_callback(self, event_type, callback) -> None:
        self.callbacks.setdefault(event_type.__name__, []).append(callback)

    async def dispatch(self, event):
        for callback in self.callbacks.get(event.sdk_event, ()):
            if inspect.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        return event


class _BeforeToolCall:
    """`cancel_tool` starts False and is a `bool | str`, exactly as the SDK declares."""

    sdk_event = "BeforeToolCallEvent"

    def __init__(self, name: str, tool_input: dict) -> None:
        self.tool_use = {"name": name, "input": tool_input, "toolUseId": "t1"}
        self.cancel_tool: bool | str = False


class _AfterToolCall:
    """Fired for a call that RAN **and for one `_before_tool` cancelled**.

    `exception` is None in both cases — `cancel_tool` produces an error
    `ToolResult`, not a raised exception (`strands.tools.executors._executor`,
    strands 1.50.2) — so `cancel_message` is the only field that tells
    the two apart. Carrying it is the whole point of this class.
    """

    sdk_event = "AfterToolCallEvent"

    def __init__(self, name: str, tool_input: dict, *,
                 cancel_message: str | None = None, result: Any = None) -> None:
        self.tool_use = {"name": name, "input": tool_input, "toolUseId": "t1"}
        self.exception = None
        self.cancel_message = cancel_message
        if cancel_message is not None:
            self.result = {
                "toolUseId": "t1", "status": "error", "content": [{"text": cancel_message}],
            }
        elif result is not None:
            self.result = {
                "toolUseId": "t1", "status": "success",
                "content": [{"text": json.dumps(result)}],
            }
        else:
            self.result = {"toolUseId": "t1", "status": "success", "content": [{"text": "True"}]}


#: `nora.agent.tool_choice.FIRST_CALL`, transcribed rather than imported —
#: `nora.testing` sits BELOW `nora.agent` in the layers contract
#: (`pyproject.toml`'s import-linter config), the same reason
#: `limit_stop_reason` above transcribes Strands' `_check_limits`.
#: `tests/test_scripted_agent_fidelity.py` asserts the two agree, so the copy
#: cannot drift silently.
TOOL_CHOICE_FIRST_CALL = "required"


class ScriptedModel:
    """The `OpenAIModel` surface a hook reaches through `event.agent.model`.

    `update_config` is `self.config.update(...)` — the REAL strands 1.50.2 semantics,
    a shallow dict update, so `update_config(params={...})` REPLACES the whole
    params dict. That is not a convenience: `ReplyGuard` relaxes `tool_choice`
    by writing `params` back whole, and a double that merged instead would let
    a relaxation which silently dropped `max_tokens`, `temperature` and
    `reasoning_effort` pass every test here and lose them in production.
    """

    def __init__(self, params: dict | None = None) -> None:
        self.config: dict = {
            "model_id": "scripted",
            "params": dict(params or {"tool_choice": TOOL_CHOICE_FIRST_CALL}),
        }

    def update_config(self, **model_config) -> None:
        self.config.update(model_config)

    def get_config(self) -> dict:
        return self.config

    @property
    def tool_choice(self):
        return self.config["params"].get("tool_choice")


class _Agent:
    """`HookEvent.agent` — a required dataclass field on every single-agent hook
    event, and the ONLY route a `BeforeModelCallEvent` hook has to the model
    (`_can_write` is `return name == "cancel"`, so the event itself is
    read-only except for cancellation). Carrying it is what lets `ReplyGuard`'s
    relaxation run here exactly as it does under the SDK."""

    def __init__(self, model: ScriptedModel) -> None:
        self.model = model


class _BeforeModelCall:
    """`cancel` starts False and is a `bool | str`, exactly as the SDK declares."""

    sdk_event = "BeforeModelCallEvent"

    def __init__(self, agent: _Agent) -> None:
        self.agent = agent
        self.cancel: bool | str = False


class FakeAgentRunner:
    """Replays a scripted turn sequence against the real tools and the real guards.

    Satisfies `nora.engagement.stages.invoke.AgentRunner` — the injected-collaborator
    seam the test-seam policy requires (never an `_invoke_fn` hook on a
    production class). Stands in for
    `nora.app.agent_runner.LiveAgentRunner`: what a scripted scenario must prove is
    *our* orchestration, not Strands' agent loop (a separate contract test covers
    that).

    **The guard is driven through its HOOKS, never by calling `evaluate`/`record`
    directly** — see the `_HookRegistry` block above for why. `register_hooks`
    is the SDK's own entry point, so what a vetoed call
    costs, and what ends a turn, are decided by `ReplyGuard` here exactly as they
    are in production; this module only supplies the events.

    **A FRESH `ScriptedModel` PER `invoke`**, mirroring
    `nora.app.agent_runner.LiveAgentRunner`'s per-tag
    `nora.agent.factory.ModelSource`. Without it a scenario could not
    tell "`tool_choice` was relaxed for this tag" from "it was relaxed for the
    previous one and never reset", which is the exact confusion the per-tag
    source exists to remove.

    `on_call`, if given, is invoked with this call's kwargs before anything else —
    the hook callers use to keep a cumulative call log that survives swapping the
    active runner mid-scenario. `models` and `tool_choices` are the same kind of
    bookkeeping: `tool_choices` records the value in force at EVERY model call
    across every invocation, in order, so a scenario can assert the
    `required → auto` sequence rather than a final state. All TEST-side,
    unbounded for exactly as long as one scenario's harness lives — nothing
    production retains any of it.
    """

    def __init__(self, turns: list[ScriptedTurn] | None = None, *, delay: float = 0.0,
                 raises: Exception | None = None,
                 on_call: Callable[[dict], None] | None = None,
                 follow_up_turns: list[ScriptedTurn] | None = None,
                 follow_up_delay: float = 0.0) -> None:
        self.turns = turns or []
        # What the model does AFTER a `follow_up` message, when the caller
        # passes one and it answers (`LiveAgentRunner.invoke`'s contract).
        # Replayed against the SAME hooks, guard and spend as `turns`.
        self.follow_up_turns = follow_up_turns or []
        # Wall clock the follow-up spends before its first turn, so a test can
        # run it into the caller's timeout (the real one runs inside it).
        self.follow_up_delay = follow_up_delay
        self.delay = delay
        self.raises = raises
        self._on_call = on_call
        self.invocations = 0
        # Every follow-up message sent, in order: at most one per invoke.
        self.follow_ups: list[str] = []
        self.models: list[ScriptedModel] = []
        self.tool_choices: list = []

    async def invoke(self, **kwargs) -> tuple[str | None, str, TagTrace]:
        """Replay the script and return `(stop_reason, final_text, trace)` —
        what the real loop would.

        `final_text` is `ScriptedTurn.final_text` for the LAST turn replayed,
        and it is what makes DEC-93 reproducible here: a scenario whose script
        ends with prose and no `send_reply` is exactly the shape that reached
        the group as silence, and this double has to be able to express it.

        The budget check is not a flag this double is TOLD to return — it is
        computed from what the script actually spent against the `limits` dict
        the pipeline actually passed, in Strands' own order, and it STOPS the
        replay exactly like the SDK does. So a scenario written to trip
        `total_tokens` stops tripping it if `[llm.limits] total_tokens` is
        raised, and a scenario that spends nothing gets `end_turn` — the fake
        does not answer every turn the same way, which is the only way this
        seam can catch R-23 regressing.

        `trace` is a blank, valid `TagTrace` (`messages=()`, `transcript=()`)
        — scripted mode has no `agent.messages` to extract from, the same
        shape `evals/harness.py` already has for the analogous `tool_calls`
        field ("scripted mode has no script to read tool intent from"). The
        real extraction is `nora.app.agent_runner.LiveAgentRunner`'s, over a
        real Strands `agent`.
        """
        self.invocations += 1
        if self._on_call is not None:
            self._on_call(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        # `ctx` is optional here, not required: a fidelity test exercising
        # this double below the `invoke.run` seam (e.g.
        # `tests/test_scripted_agent_fidelity.py`) supplies none.
        ctx = kwargs.get("ctx")
        trace = TagTrace(
            tag_id=ctx.tag_id if ctx is not None else "",
            conversation_id=ctx.message.conversation_id if ctx is not None else "",
            messages=(), transcript=(), outcome="send_reply", created_at=0,
        )
        tools_by_name = {t.tool_name: t for t in kwargs["tools"]}
        guard = kwargs["guard"]
        tool_call_log = kwargs.get("tool_call_log")
        limits = kwargs.get("limits")
        hooks = _HookRegistry()
        guard.register_hooks(hooks)
        if tool_call_log is not None:
            tool_call_log.register_hooks(hooks)
        # After the guard, as `factory.create_agent` orders them.
        for tool_hook in kwargs.get("tool_hooks") or ():
            tool_hook.register_hooks(hooks)
        model = ScriptedModel()
        self.models.append(model)
        agent = _Agent(model)
        spent = [0, 0, 0]  # cycles, total_tokens, output_tokens: the WHOLE turn's
        stop, final = await self._replay(
            self.turns, hooks=hooks, agent=agent, model=model,
            tools_by_name=tools_by_name, limits=limits, spent=spent,
        )
        # The real runner's one continuation: consulted only after a loop that
        # ended on its own, and only when a cap is left. The spend is NOT
        # reset, so the follow-up is bounded by what the first pass left —
        # `factory.remaining_limits`'s arithmetic, expressed as one counter.
        follow_up = kwargs.get("follow_up")
        if follow_up is not None and stop == "end_turn" and limit_stop_reason(
            cycles=spent[0], total_tokens=spent[1], output_tokens=spent[2], limits=limits,
        ) is None:
            message = follow_up(final)
            if message:
                self.follow_ups.append(message)
                if self.follow_up_delay:
                    await asyncio.sleep(self.follow_up_delay)
                stop, final = await self._replay(
                    self.follow_up_turns, hooks=hooks, agent=agent, model=model,
                    tools_by_name=tools_by_name, limits=limits, spent=spent,
                )
        return stop, final, trace

    async def _replay(
        self, turns: list[ScriptedTurn], *, hooks: _HookRegistry, agent: _Agent,
        model: ScriptedModel, tools_by_name: dict, limits: dict | None, spent: list[int],
    ) -> tuple[str, str]:
        """One loop over `turns` → `(stop_reason, final_text)`, charging
        `spent` in place. The body `invoke` always had, lifted so a follow-up
        can replay a second script over the same hooks."""
        for turn in turns:
            cancel = (await hooks.dispatch(_BeforeModelCall(agent))).cancel
            if cancel:
                # A terminal verb has run and the guard ended the turn, so the
                # SDK never calls the MODEL. It does still synthesise an
                # assistant message carrying the cancel text and yield
                # `end_turn` (`strands/event_loop/event_loop.py`) — so this double
                # does too, rather than returning "": a fake that returned ""
                # there would hide a defect that delivers the guard's own veto
                # string to the group. CLAUDE.md's rule: make the fake behave
                # like the real server, INCLUDING its wrong-call shapes.
                return "end_turn", cancel if isinstance(cancel, str) else ""
            # Recorded AFTER the hooks, because that is when the SDK reads the
            # model's config to build the request — a value sampled before them
            # would miss the relaxation this line exists to make visible.
            self.tool_choices.append(model.tool_choice)
            for name, args in turn.tool_calls:
                if not turn.bypass_guard:
                    veto = (await hooks.dispatch(_BeforeToolCall(name, args))).cancel_tool
                    if veto:
                        # The model sees the correction and moves on — but the
                        # SDK still fires the after-tool hook for the cancelled
                        # call, so the guard still gets to decide what it costs.
                        await hooks.dispatch(_AfterToolCall(name, args, cancel_message=veto))
                        continue
                tool_result = await _invoke_tool(tools_by_name[name], **args)
                await hooks.dispatch(_AfterToolCall(name, args, result=tool_result))
            spent[0] += 1
            spent[1] += turn.total_tokens
            spent[2] += turn.output_tokens
            if not turn.tool_calls:
                # The cycle ended `end_turn`, so the loop stops here and never
                # reaches the top-of-cycle check — a one-shot answer cannot
                # trip a cap however much it spent. See `limit_stop_reason`.
                return "end_turn", turn.final_text or ""
            stop = limit_stop_reason(
                cycles=spent[0], total_tokens=spent[1],
                output_tokens=spent[2], limits=limits,
            )
            if stop is not None:
                # `final_text` on a budget stop, not "". The REAL runner
                # returns words here: either prose the cut-off completion
                # left behind, or the draft `LiveAgentRunner` makes over the
                # context already gathered. A fake that always answered ""
                # could not express the case the pipeline branches on, which
                # is exactly the "fake answers every request the same way"
                # shape the conventions forbid — reverting the pipeline branch
                # would not turn anything red.
                #
                # So a ScriptedTurn that trips a cap WITH `final_text` is a
                # runner that drafted; one without is a draft that came back
                # empty. Both are real production states.
                return stop, turn.final_text or ""
        final = (turns[-1].final_text or "") if turns else ""
        return "end_turn", final
