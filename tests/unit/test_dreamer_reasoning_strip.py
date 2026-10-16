"""The Dreamer half of the reasoning-strip instrument, through the REAL loop.

`tests/unit/test_reasoning_strip_probe.py` proves the detector on the tag path. This
file proves it on the Dreamer path, and the difference is not symmetry for its own sake:

* with `[llm]` reasoning off the tag path emits nothing for the transport to
  drop, while a Dreamer adaptor whose `[dreamer] reasoning_effort` is above
  `none` can;
* a tool-using Dreamer run with reasoning on is exactly the multi-turn shape
  that gets stripped;
* a Dreamer run produces one row a day rather than a reply somebody reads, so
  a discarded reasoning there is both closer to being the verdict and much
  harder to notice.

The doubles are `test_dream_memory_budget.py`'s, imported rather than copied —
`tests/architecture/test_port_fakes_live_in_nora_testing.py` pins one inline
`DreamAdaptor` stand-in per suite and its list only shrinks. Only the MODEL is
new here, because emitting `reasoningContent` is the whole point.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Sequence
from typing import Any

from strands.models.model import Model

from nora.agent import factory
from nora.agent.dreamer import AdaptorBudget, AdaptorGrant, DreamerRuntime
from nora.obs.metrics import Metrics
from tests.unit.test_dream_memory_budget import (
    ENVELOPE,
    _Adaptor,
    _Instructions,
    _Models,
    _Tools,
)


def _REMIND_GRANT() -> AdaptorGrant:
    """An adaptor that may hold `remind` past the read-only ceiling, in a real room."""
    return AdaptorGrant(home_room=True)

BUDGET = AdaptorBudget(
    turns=8, total_tokens=100_000, output_tokens=4_000, timeout_seconds=10.0,
    max_memory_calls=4,
)


class _ReasoningModel(Model):
    """Calls one tool, then answers — with or without emitting its reasoning.

    `get_config` is real rather than `{}` because the watch reads the row's
    provenance off the provider: `model_id` and `reasoning_effort` land on the
    row from here, exactly as they do from a live `OpenAIModel`.
    """

    def __init__(self, *, reasons: bool, script: Sequence[str | None]) -> None:
        self._reasons = reasons
        self._script = list(script)
        self.turn = 0

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {
            "model_id": "nora-dream",
            "params": {"reasoning_effort": "low" if self._reasons else "none"},
        }

    async def structured_output(
        self, output_model: Any, prompt: Any = None, **kwargs: Any
    ) -> Any:
        raise NotImplementedError

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        call = self._script[self.turn] if self.turn < len(self._script) else None
        self.turn += 1
        yield {"messageStart": {"role": "assistant"}}
        if self._reasons:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {
                "reasoningContent": {"text": f"weighing pass {self.turn}"}}}}
            yield {"contentBlockStop": {}}
        if call is None:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": ENVELOPE}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        else:
            yield {"contentBlockStart": {"start": {"toolUse": {
                "name": call, "toolUseId": f"t{self.turn}"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {
                "input": '{"person": "someone"}'}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        }}


class _RecordingStore:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def record_observation(self, observation: Any) -> None:
        self.rows.append(observation)


async def _run(*, reasons: bool) -> tuple[_RecordingStore, Metrics]:
    tools = _Tools(["about"])
    model = _ReasoningModel(reasons=reasons, script=["about", None])
    store = _RecordingStore()
    metrics = Metrics()
    runtime = DreamerRuntime(
        models=_Models(model),
        instructions=_Instructions(),
        tools=tools,
        budgets={"proactive": BUDGET},
        default_budget=BUDGET,
        retry=factory.RetryPolicy(max_attempts=1, initial_delay=0, max_delay=0),
        metrics=metrics,
        model_name="scripted",
        grants={"proactive": _REMIND_GRANT()},
        store_timeout_seconds=10.0,
        reasoning_strip_store=store,
    )
    await runtime.run(
        adaptor=_Adaptor(frozenset(tools.read_only())), key="friends", batch=(),
    )
    return store, metrics


async def test_a_tool_using_dreamer_run_that_reasons_is_recorded_by_adaptor():
    """THE DREAMER PROBE.

    Two model calls: the first asks `about`, the second answers. The second is
    the continuation, and it carries the first's reasoning into a request the
    Chat Completions transport will not take it in.

    `path` is the ADAPTOR by name — «which path» is one of the three questions
    the instrument exists to answer, and on a loop that runs once a day per
    adaptor, "dreamer" alone would be the wrong grain.
    """
    store, metrics = await _run(reasons=True)

    (row,) = store.rows
    assert row.path == "dreamer:proactive"
    assert row.tag_id is None, "a Dreamer run has no tag; the absence is the answer"
    assert row.continuation_calls == 1, "the denominator is wrong"
    assert row.strip_calls == 1, (
        "a Dreamer continuation carried reasoning into the Chat Completions "
        "transport and nothing noticed — this is the path the 2026-09-17 "
        "config change moved the exposure onto"
    )
    assert (row.model, row.reasoning_effort) == ("nora-dream", "low")
    assert metrics.get(
        "nora_reasoning_strip_total", path="dreamer:proactive", outcome="stripped"
    ) == 1


async def test_the_same_dreamer_run_with_no_reasoning_is_recorded_as_clean():
    """The control. Identical run, identical call count, one different model —
    and the row still lands, because the denominator is what makes a later zero
    mean «measured» rather than «nothing ran»."""
    store, metrics = await _run(reasons=False)

    (row,) = store.rows
    assert row.continuation_calls == 1
    assert row.strip_calls == 0
    assert row.reasoning_effort == "none"
    assert metrics.get(
        "nora_reasoning_strip_total", path="dreamer:proactive", outcome="clean"
    ) == 1
