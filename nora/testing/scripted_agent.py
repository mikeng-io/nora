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
    """
    tool_calls: list[tuple[str, dict]] = dataclasses.field(default_factory=list)
    final_text: str = ""
    bypass_guard: bool = False


async def _invoke_tool(tool, **kwargs):
    """Call a Strands @tool-decorated function the way a scripted turn exercises it."""
    result = tool(**kwargs)
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

    async def invoke(self, **kwargs) -> None:
        self.invocations += 1
        if self._on_call is not None:
            self._on_call(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        tools_by_name = {t.tool_name: t for t in kwargs["tools"]}
        guard = kwargs["guard"]
        for turn in self.turns:
            for name, args in turn.tool_calls:
                if not turn.bypass_guard:
                    veto = guard.evaluate(name, args)
                    if veto:
                        continue                   # the model sees the correction and moves on
                await _invoke_tool(tools_by_name[name], **args)
                guard.record(name)
