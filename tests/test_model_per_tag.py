"""ONE `OpenAIModel` PER TAG, over the process-wide `AsyncOpenAI` client.

`tool_choice` is per-turn state — `required` for the first model call, `auto`
once she has spoken — so a single `OpenAIModel` for the whole process would be
wrong. The engagement in-flight lock (`nora.engagement.slots`) keys on
`(platform, conversation_id)`, so two conversations run at the same time, and a
flip made for tag A would apply to tag B's FIRST call.

A first call under `auto` can answer in plain prose and call
nothing — zero messages end to end. That is R-5's failure shape exactly, and it
would appear only under concurrent load, i.e. almost never in testing.

The guarantee is "one CLIENT per process" — that is what pools connections —
and the client is built once by `build_dependencies`. The MODEL is cheap and is
built per tag, the same argument `factory.RetryPolicy`'s docstring already makes for
`ModelRetryStrategy` ("one shared instance would let one tag's retry counter
decide another tag's backoff").

**The probes below record the tool_choice used for each TAG's Nth model call,
not per model object**, because that is the invariant regardless of how many
model objects exist — a probe that asserted on `source.created[1]` would have
nothing to assert against when the revert makes `created` one element long.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from typing import Any

import pytest
from strands.models.model import Model

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from nora.agent.ports import ToolSpec, adapt
from nora.agent.tool_choice import AFTER_SPEAKING, FIRST_CALL
from nora.app.agent_runner import LiveAgentRunner
from nora.doctrine.loader import Doctrine
from nora.domain.models import NormalizedMessage, TagContext
from nora.obs.metrics import Metrics

RETRY = factory.build_retry_policy(timeout_seconds=30.0)
# `now` is the terminal verb here, with room for two calls: the guard relaxes
# after the first (she has spoken) and cancels after the second (the terminal
# budget is spent), so one tag produces exactly the two-element sequence
# `required, auto` — which is the whole mechanism, expressed as a list.
BUDGETS = {"now": 2}
TERMINAL = frozenset({"now"})
LIMITS = {"turns": 6, "output_tokens": 12_000, "total_tokens": 64_000}


async def _now() -> str:
    """The current time.

    Returns: a fixed string.
    """
    return "Thursday"


class _RecordsToolChoice(Model):
    """A model that logs the `tool_choice` in force at every call, tagged by prompt.

    `update_config`/`get_config` are the REAL `OpenAIModel` semantics, not a
    convenient approximation: `update_config` does `self.config.update(...)`,
    so passing `params={...}` REPLACES the whole params dict rather than
    merging into it. A double that merged would let a relaxation that dropped
    `max_tokens`/`temperature` pass here and lose them in production.

    The tag is read off `system_prompt` at call time rather than held on the
    instance, because under the shared-model arrangement this file exists to
    forbid there is only ONE instance for both tags — an instance-held label
    could not express the question.
    """

    def __init__(self, params: dict, log: list[tuple[str, str | None]],
                 relaxed: asyncio.Event) -> None:
        self.config: dict[str, Any] = {"model_id": "stub", "params": dict(params)}
        self._log = log
        self._relaxed = relaxed

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> Any:
        return self.config

    async def structured_output(self, output_model: Any, prompt: Any = None, **kw: Any) -> Any:
        raise NotImplementedError

    async def stream(self, messages: Any, tool_specs: Any = None,
                     system_prompt: str | None = None, **kwargs: Any) -> AsyncIterable[Any]:
        tag = (system_prompt or "").strip()
        if tag == "B" and not any(t == "B" for t, _ in self._log):
            # B's FIRST model call is held until A has been relaxed. Without
            # this the interleaving is up to the event loop and the probe would
            # only sometimes see the leak; with it, "B's first call happens
            # after A flipped" is a fact of the test rather than a hope.
            await asyncio.wait_for(self._relaxed.wait(), timeout=5)
        choice = self.config["params"].get("tool_choice")
        self._log.append((tag, choice))
        if tag == "A" and choice == AFTER_SPEAKING:
            self._relaxed.set()
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {"toolUse": {"name": "now", "toolUseId": "t1"}}}}
        yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                            "metrics": {"latencyMs": 1}}}


class _RecordingSource:
    """A `factory.ModelSource` stand-in: a fresh model per `create()`, all logging
    into one shared list. `created` is what the per-tag property is asserted on."""

    def __init__(self, log: list[tuple[str, str | None]], relaxed: asyncio.Event) -> None:
        self._log = log
        self._relaxed = relaxed
        self.created: list[_RecordsToolChoice] = []

    def create(self) -> _RecordsToolChoice:
        model = _RecordsToolChoice(
            {"max_tokens": 2000, "temperature": 0.7, "tool_choice": FIRST_CALL},
            self._log, self._relaxed,
        )
        self.created.append(model)
        return model


def _doctrine(text: str) -> Doctrine:
    return Doctrine(sections=(), text=text, doctrine_text=text, sha256="deadbeef",
                    runtime_context="", loaded_at=0.0)


def _ctx() -> TagContext:
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="nora.000 hi", raw={},
    )
    return TagContext(tag_id="tag-1", message=msg, tagger=None, transcript=(),
                      peer_card=(), started_at=0.0)


async def _run_tag(runner: LiveAgentRunner, tag: str) -> None:
    tool = adapt(ToolSpec(name="now", description="", schema={}, call=_now,
                          budget=2, terminal=True))
    await runner.invoke(
        doctrine=_doctrine(tag), tools=[tool], pre_fill=[],
        guard=ReplyGuard(conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
                         metrics=Metrics()),
        tag_message="<tag_message/>", ctx=_ctx(), trace_attributes={}, limits=LIMITS,
    )


def _for(log: list[tuple[str, str | None]], tag: str) -> list[str | None]:
    return [choice for name, choice in log if name == tag]


# ---- the probe -------------------------------------------------------------


async def test_one_tags_relaxed_tool_choice_is_invisible_to_the_next_tag():
    """Sequential, and the simplest form of the leak: A finishes, then B starts.

    With one model per process, A's flip to `auto` is still in force when B
    makes its FIRST call — the call that would send nothing.
    """
    log: list[tuple[str, str | None]] = []
    source = _RecordingSource(log, asyncio.Event())
    runner = LiveAgentRunner(models=source, retry=RETRY)
    await _run_tag(runner, "A")
    await _run_tag(runner, "B")
    assert _for(log, "A") == [FIRST_CALL, AFTER_SPEAKING]
    assert _for(log, "B") == [FIRST_CALL, AFTER_SPEAKING]


async def test_two_concurrent_tags_cannot_observe_each_others_tool_choice():
    """THE probe, with the interleaving pinned rather than hoped.

    B's first model call is held inside the model double until A has been
    relaxed, so "B asks after A flipped" is deterministic. Share one model
    between them and B's first call reads `auto`.
    """
    log: list[tuple[str, str | None]] = []
    source = _RecordingSource(log, asyncio.Event())
    runner = LiveAgentRunner(models=source, retry=RETRY)
    await asyncio.gather(_run_tag(runner, "A"), _run_tag(runner, "B"))
    assert _for(log, "B")[0] == FIRST_CALL
    assert _for(log, "A") == [FIRST_CALL, AFTER_SPEAKING]


async def test_the_runner_builds_one_model_per_invocation():
    """The mechanism behind the two probes above, asserted directly so a
    regression names itself rather than surfacing as a tool_choice value."""
    log: list[tuple[str, str | None]] = []
    source = _RecordingSource(log, asyncio.Event())
    runner = LiveAgentRunner(models=source, retry=RETRY)
    await _run_tag(runner, "A")
    await _run_tag(runner, "B")
    assert len(source.created) == 2
    assert source.created[0] is not source.created[1]


# ---- the real source, which is what production hands the runner ------------


def _real_source() -> factory.ModelSource:
    return factory.ModelSource(
        client=factory.create_client(base_url="http://localhost:20128/v1", api_key="k"),
        spec=factory.ModelSpec(model="deepseek-v4-flash", max_tokens=2000,
                               temperature=0.7, reasoning_effort="low"),
    )


def test_the_real_source_hands_out_a_fresh_model_over_the_same_client():
    """One pooled client per process: one client, many models. Both halves in one assertion,
    because keeping the client and splitting the model is the whole change."""
    source = _real_source()
    a, b = source.create(), source.create()
    assert a is not b
    # `_custom_client` is where `OpenAIModel.__init__` parks an injected client
    # (1.50.2). Asserting identity on it is what says one pooled
    # `AsyncOpenAI` per process survived the model being split per tag; a
    # source that built its own client per model would pass every other
    # assertion in this file and quietly open a connection pool per tag.
    assert a._custom_client is b._custom_client is not None


def test_mutating_one_tags_model_leaves_the_next_tags_at_required():
    source = _real_source()
    first = source.create()
    first.update_config(params={**first.get_config()["params"], "tool_choice": AFTER_SPEAKING})
    assert first.get_config()["params"]["tool_choice"] == AFTER_SPEAKING
    assert source.create().get_config()["params"]["tool_choice"] == FIRST_CALL


def test_the_spec_carries_the_tier_scalars_into_every_model_it_builds():
    """`reasoning_effort` per tag is the independently useful half of 5a —
    `config.toml` already flags it as the escape hatch needed if the pinned
    route changes behaviour, and a process-wide model could not offer one."""
    params = _real_source().create().get_config()["params"]
    assert params["max_tokens"] == 2000
    assert params["temperature"] == pytest.approx(0.7)
    assert params["reasoning_effort"] == "low"
    assert params["tool_choice"] == FIRST_CALL
