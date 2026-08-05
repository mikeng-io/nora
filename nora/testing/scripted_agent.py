"""Scripted model double, shared by `tests/` and `evals/` — one definition.

`ScriptedTurn`/`FakeAgentRunner` replay a scripted tool-call sequence against the
REAL tools and the REAL guards a pipeline builds, structurally satisfying
`nora.engagement.stages.invoke.AgentRunner`'s `Protocol` (never imported here —
`nora.engagement` sits above `nora.testing` in the layers contract, so the match
is structural, the same way `nora.app.agent_runner.LiveAgentRunner` satisfies it).
`AgentRunner` is a stage-level `Protocol` (`nora/engagement/stages/invoke.py`),
not one of `nora/domain/ports.py`'s ports, so it is not bound by this project's
"a port never gains a second implementation without a shared contract suite"
rule (see the Task 10 report for the reasoning).

`group_event` builds the signal-cli-shaped raw event dict every scripted
scenario feeds into a pipeline's `Dispatcher.on_event`.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable


def group_event(*, body: str, sender: str = "+852111", conv: str = "g1",
                 name: str = "Miles", ts: int = 1) -> dict:
    return {"method": "receive", "params": {"envelope": {
        "source": sender, "sourceName": name, "timestamp": ts,
        "dataMessage": {"message": body, "groupInfo": {"groupId": conv}, "timestamp": ts}}}}


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
    (verified against 1.50.2), INCLUDING its priority order — turns, then
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
    matters here specifically: DEC-24 deleted the conversation parameters, and
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


class FakeAgentRunner:
    """Replays a scripted turn sequence against the real tools and the real guards.

    Satisfies `nora.engagement.stages.invoke.AgentRunner` — the injected-collaborator
    seam spec §10's test-seam policy requires (never an `_invoke_fn` hook on a
    production class, which is what the deleted `Handler` did). Stands in for
    `nora.app.agent_runner.LiveAgentRunner`: what a scripted scenario must prove is
    *our* orchestration, not Strands' agent loop (a separate contract test covers
    that).

    `on_call`, if given, is invoked with this call's kwargs before anything else —
    the hook callers use to keep a cumulative call log that survives swapping the
    active runner mid-scenario. This is TEST-side bookkeeping only, unbounded for
    exactly as long as one scenario's harness lives — nothing production retains it.
    """

    def __init__(self, turns: list[ScriptedTurn] | None = None, *, delay: float = 0.0,
                 raises: Exception | None = None,
                 on_call: Callable[[dict], None] | None = None) -> None:
        self.turns = turns or []
        self.delay = delay
        self.raises = raises
        self._on_call = on_call
        self.invocations = 0

    async def invoke(self, **kwargs) -> str | None:
        """Replay the script and return the `stop_reason` the real loop would.

        The budget check is not a flag this double is TOLD to return — it is
        computed from what the script actually spent against the `limits` dict
        the pipeline actually passed, in Strands' own order, and it STOPS the
        replay exactly like the SDK does. So a scenario written to trip
        `total_tokens` stops tripping it if `[llm.limits] total_tokens` is
        raised, and a scenario that spends nothing gets `end_turn` — the fake
        does not answer every turn the same way, which is the only way this
        seam can catch R-23 regressing.
        """
        self.invocations += 1
        if self._on_call is not None:
            self._on_call(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        tools_by_name = {t.tool_name: t for t in kwargs["tools"]}
        guard = kwargs["guard"]
        limits = kwargs.get("limits")
        cycles = total_tokens = output_tokens = 0
        for turn in self.turns:
            for name, args in turn.tool_calls:
                if not turn.bypass_guard:
                    veto = guard.evaluate(name, args)
                    if veto:
                        continue                   # the model sees the correction and moves on
                await _invoke_tool(tools_by_name[name], **args)
                guard.record(name)
            cycles += 1
            total_tokens += turn.total_tokens
            output_tokens += turn.output_tokens
            if not turn.tool_calls:
                # The cycle ended `end_turn`, so the loop stops here and never
                # reaches the top-of-cycle check — a one-shot answer cannot
                # trip a cap however much it spent. See `limit_stop_reason`.
                return "end_turn"
            stop = limit_stop_reason(
                cycles=cycles, total_tokens=total_tokens,
                output_tokens=output_tokens, limits=limits,
            )
            if stop is not None:
                return stop
        return "end_turn"
