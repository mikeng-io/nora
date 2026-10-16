"""A continuation call carries the previous call's reasoning.

Two layers, both against the REAL formatter:

- the request the provider `nora.agent.factory` builds carries the reasoning
  as `reasoning` on the assistant message (the field the upstream reads;
  `reasoning_content` is discarded, see `nora.agent.reasoning_carry`),
  and is otherwise the SDK's own request, byte for byte;
- a real Strands loop driven through `invoke.run` sends that request, and the
  strip watch records the turn as CLEAN, where the same loop through the
  dash-free model alone records it as stripped (the control).
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterable
from typing import Any

from openai import AsyncOpenAI
from strands.models.openai import OpenAIModel

from nora.agent.factory import create_model_provider
from nora.agent.punctuation import DashFreeOpenAIModel
from nora.agent.reasoning_carry import ReasoningCarryingOpenAIModel
from nora.agent.reasoning_strip import (
    REASONING_BLOCK_KEY,
    dropped_reasoning_blocks,
    reasoning_blocks,
)
from tests.unit.test_reasoning_strip_probe import _drive

REASONING_TEXT = "the order ships from Busan, so ask for the ETA next"

#: A literal, not the module's constant: the expected value is the
#: field the upstream reads, independent of the code
#: under test, so a change to the constant cannot move the test with it.
CARRIED_REASONING_FIELD = "reasoning"

MULTI_TURN: list[dict] = [
    {"role": "user", "content": [{"text": "when will A-1177 reach Osaka"}]},
    {
        "role": "assistant",
        "content": [
            {REASONING_BLOCK_KEY: {"reasoningText": {"text": REASONING_TEXT}}},
            {"toolUse": {"toolUseId": "t1", "name": "lookup_order",
                         "input": {"order_id": "A-1177"}}},
        ],
    },
    {
        "role": "user",
        "content": [
            {"toolResult": {"toolUseId": "t1", "status": "success",
                            "content": [{"text": "packed, Busan-2"}]}},
        ],
    },
]


def _provider() -> OpenAIModel:
    """Built exactly the way production builds it."""
    return create_model_provider(
        model="nora-dreamer", max_tokens=64, temperature=0.7,
        client=AsyncOpenAI(api_key="x", base_url="http://127.0.0.1:1/v1"),
        reasoning_effort="low",
    )


def _assistant(request: dict) -> dict:
    (message,) = [m for m in request["messages"] if m["role"] == "assistant"]
    return message


def test_the_production_provider_carries_the_reasoning_as_reasoning() -> None:
    request = _provider().format_request(MULTI_TURN)  # type: ignore[arg-type]
    assistant = _assistant(request)
    assert assistant.get(CARRIED_REASONING_FIELD) == REASONING_TEXT, (
        "the continuation request does not carry the previous call's reasoning "
        "in the field the upstream reads; #325's drop is back"
    )
    assert "reasoning_content" not in json.dumps(request), (
        "`reasoning_content` is accepted and DISCARDED by the upstream "
        "(measured, see nora.agent.reasoning_carry): a request that looks "
        "carried and is not"
    )
    assert dropped_reasoning_blocks(_provider(), MULTI_TURN) == 0
    assert reasoning_blocks(MULTI_TURN) == 1, "positive control: there was one to carry"


def test_apart_from_the_field_the_request_is_the_sdks_own() -> None:
    """The override formats one message at a time; this pins that doing so
    changes nothing but the one field, tool messages and order included."""
    carried = _provider().format_request(copy.deepcopy(MULTI_TURN))  # type: ignore[arg-type]
    plain = DashFreeOpenAIModel(
        model_id="nora-dreamer", client=None,  # type: ignore[arg-type]
        params=_provider().get_config()["params"],
    ).format_request(copy.deepcopy(MULTI_TURN))  # type: ignore[arg-type]
    _assistant(carried).pop(CARRIED_REASONING_FIELD)
    assert carried == plain


def test_the_messages_are_not_mutated() -> None:
    messages = copy.deepcopy(MULTI_TURN)
    _provider().format_request(messages)  # type: ignore[arg-type]
    assert messages == MULTI_TURN


def test_reasoning_with_nothing_to_emit_beside_it_is_still_counted_as_dropped() -> None:
    """An assistant message that is ONLY reasoning is filtered out of the
    request whole, so its reasoning reaches nothing, and the watch must say so."""
    only_reasoning = [
        MULTI_TURN[0],
        {"role": "assistant", "content": [
            {REASONING_BLOCK_KEY: {"reasoningText": {"text": "orphan"}}}]},
    ]
    assert "orphan" not in json.dumps(
        _provider().format_request(only_reasoning))  # type: ignore[arg-type]
    assert dropped_reasoning_blocks(_provider(), only_reasoning) == 1


def test_a_redacted_block_cannot_be_carried_and_is_counted() -> None:
    redacted = copy.deepcopy(MULTI_TURN)
    redacted[1]["content"].insert(0, {REASONING_BLOCK_KEY: {"redactedContent": b"x"}})
    assert dropped_reasoning_blocks(_provider(), redacted) == 1
    assert _assistant(_provider().format_request(redacted)).get(  # type: ignore[arg-type]
        CARRIED_REASONING_FIELD) == REASONING_TEXT


def test_one_carried_message_does_not_vouch_for_another() -> None:
    """A reasoning-only message beside a carried one: the request has a
    `reasoning` field, and still only one of the two blocks reached it."""
    mixed = [*copy.deepcopy(MULTI_TURN), {"role": "assistant", "content": [
        {REASONING_BLOCK_KEY: {"reasoningText": {"text": "orphan"}}}]}]
    assert dropped_reasoning_blocks(_provider(), mixed) == 1


def test_a_dropped_block_is_not_vouched_for_by_a_longer_carried_one() -> None:
    """Each block is judged against ITS OWN message's carried field: a reasoning-only
    message whose short text is a substring of another message's carried reasoning must not count as
    carried (an undercount is the worse error)."""
    messages = copy.deepcopy(MULTI_TURN)
    messages[1]["content"][0][REASONING_BLOCK_KEY]["reasoningText"]["text"] = "I will call f."
    messages.append({"role": "assistant", "content": [
        {REASONING_BLOCK_KEY: {"reasoningText": {"text": "call f"}}}]})
    assert dropped_reasoning_blocks(_provider(), messages) == 1


def test_a_carried_field_counts_only_the_text_that_reached_it() -> None:
    """A field being present is not the claim; its CONTENT is. A formatter
    that carries only the first of two reasoning blocks dropped the second,
    and the counter must see that."""

    class _FirstOnly(ReasoningCarryingOpenAIModel):
        @classmethod
        def _format_regular_messages(cls, messages: Any, **kwargs: Any) -> list[dict[str, Any]]:
            formatted = super()._format_regular_messages(messages, **kwargs)
            for entry in formatted:
                if CARRIED_REASONING_FIELD in entry:
                    entry[CARRIED_REASONING_FIELD] = "first thought"
            return formatted

    two = copy.deepcopy(MULTI_TURN)
    two[1]["content"][0][REASONING_BLOCK_KEY]["reasoningText"]["text"] = "first thought"
    second = {REASONING_BLOCK_KEY: {"reasoningText": {"text": "second thought"}}}
    two[1]["content"].insert(1, second)
    model = _FirstOnly(model_id="nora-dreamer", client=None,  # type: ignore[arg-type]
                       params={"max_tokens": 64, "reasoning_effort": "low"})
    assert dropped_reasoning_blocks(model, two) == 1
    assert dropped_reasoning_blocks(_provider(), two) == 0, "control: both carried"


def test_a_model_whose_formatting_raises_is_recorded_as_dropping_everything() -> None:
    class _Broken:
        def format_request(self, messages: Any) -> dict:
            raise RuntimeError("boom")

    assert dropped_reasoning_blocks(_Broken(), MULTI_TURN) == 1


def test_the_counter_judges_the_formatted_request_not_a_prediction() -> None:
    """ADR 0013 decision 1: the detector is judged against the formatter's own
    output. Simulate a Strands release whose formatter no longer routes
    through the override (the SDK's `_format_regular_messages` reached
    directly): nothing is carried, and the counter must SAY so, even though
    the model is still a `ReasoningCarryingOpenAIModel` by class."""

    class _Bypassed(ReasoningCarryingOpenAIModel):
        _format_regular_messages = classmethod(  # type: ignore[assignment]
            OpenAIModel._format_regular_messages.__func__  # type: ignore[attr-defined]
        )

    bypassed = _Bypassed(
        model_id="nora-dreamer", client=None,  # type: ignore[arg-type]
        params={"max_tokens": 64, "reasoning_effort": "low"},
    )
    assert REASONING_TEXT not in json.dumps(
        bypassed.format_request(MULTI_TURN)), "the bypass did not bypass"  # type: ignore[arg-type]
    assert dropped_reasoning_blocks(bypassed, MULTI_TURN) == 1, (
        "the override was skipped and nothing was carried, yet the counter "
        "reported no strip: it is predicting, not reading the request"
    )


def test_the_detector_reads_the_carried_text_after_the_dash_free_rewrite() -> None:
    dashed = copy.deepcopy(MULTI_TURN)
    dashed[1]["content"][0][REASONING_BLOCK_KEY]["reasoningText"]["text"] = (
        "Busan \u2014 then the ETA")
    assert dropped_reasoning_blocks(_provider(), dashed) == 0


# ---- the loop ---------------------------------------------------------------


class _Scripted:
    """The real provider's `format_request`, a scripted answer: reasons, calls
    `now` twice, then answers. Records every request it formats, so the test
    reads what would have gone on the wire."""

    def _init_script(self) -> None:
        self.calls = 0
        self.requests: list[dict] = []

    async def stream(  # type: ignore[override]
        self, messages: Any, tool_specs: Any = None, system_prompt: Any = None,
        **kwargs: Any,
    ) -> AsyncIterable[Any]:
        self.requests.append(self.format_request(messages, tool_specs, system_prompt))  # type: ignore[attr-defined]
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {
            "reasoningContent": {"text": f"thinking about pass {self.calls}"}}}}
        yield {"contentBlockStop": {}}
        if self.calls <= 2:
            yield {"contentBlockStart": {"start": {"toolUse": {
                "name": "now", "toolUseId": f"t{self.calls}"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "it is Thursday"}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1}}}


class _CarryingScripted(_Scripted, ReasoningCarryingOpenAIModel):
    pass


class _PlainScripted(_Scripted, DashFreeOpenAIModel):
    pass


def _scripted(cls: type) -> Any:
    model = cls(model_id="nora", client=None,
                params={"max_tokens": 64, "reasoning_effort": "low"})
    model._init_script()
    return model


async def test_a_reasoning_tool_loop_sends_its_reasoning_and_records_no_strip() -> None:
    model, _, store, _ = await _drive(reasons=True, model=_scripted(_CarryingScripted))
    assert model.calls == 3, "the loop did not go multi-turn; there is nothing to probe"

    last = model.requests[-1]
    carried = [m.get(CARRIED_REASONING_FIELD) for m in last["messages"]
               if m["role"] == "assistant" and "tool_calls" in m]
    assert carried == ["thinking about pass 1", "thinking about pass 2"], (
        "the third request did not carry both earlier passes' reasoning"
    )

    (row,) = store.rows
    assert row.continuation_calls == 2, "the denominator must still be recorded"
    assert row.strip_calls == 0, "the watch counted a strip the request did not make"
    assert row.stripped_blocks == 0


async def test_the_same_loop_without_the_carry_still_records_the_strip() -> None:
    """The control: identical loop and script through the dash-free model
    alone, which is the SDK's formatter. A watch or a test that answered
    `clean` for everything would pass the test above and fail here."""
    model, _, store, _ = await _drive(reasons=True, model=_scripted(_PlainScripted))
    assert model.calls == 3
    assert "thinking about pass" not in json.dumps(model.requests[-1])

    (row,) = store.rows
    assert row.continuation_calls == 2
    assert row.strip_calls == 2
    assert row.stripped_blocks == 2
