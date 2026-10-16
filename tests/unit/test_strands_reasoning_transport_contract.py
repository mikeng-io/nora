"""THE LOUD FAILURE for the reasoning-strip instrument.

`nora.agent.reasoning_strip.reasoning_blocks` is a FIRST-PARTY predicate over
the message list: it counts the `reasoningContent` blocks a pending request
carries. Everything downstream — the counter, the row, the ratio — treats that
count as "the transport is about to throw this away". Nothing in Nora enforces
that; it is a property of `strands.models.openai.OpenAIModel`, which is
somebody else's code and one `uv lock` away from changing.

So this file is the joint. It formats a synthetic multi-turn exchange through
the REAL model object and asserts BOTH halves at once:

  1. the SDK still drops the reasoning (the defect the instrument counts), and
  2. `reasoning_blocks` agrees with it, block for block.

**Deliberately NOT a check on the warning string.** The SDK logs
«reasoningContent is not supported in multi-turn conversations with the Chat
Completions API» one loop above the drop, and matching that sentence is what
the instrument refuses to do — a third-party sentence can be reworded in a
patch release, and a detector that silently stops detecting reads as a fix. The
property is what the request CONTAINS, and that is what is asserted here.

If a future SDK starts carrying reasoning through the Chat Completions
transport, this file goes RED — and that is the intended outcome, not a nuisance:
`reasoning_strip_log` would from that moment be recording a drop that no longer
happens, and a table quietly over-counting is exactly as useless as one quietly
under-counting. The remedy when it reddens is to re-read ADR 0011's
"the reading that would reopen this decision", not to loosen the assertion.
"""

from __future__ import annotations

import json

import pytest
from strands.models.openai import OpenAIModel

from nora.agent.reasoning_strip import REASONING_BLOCK_KEY, reasoning_blocks

# The exact shape the agent loop builds when a turn reaches for a tool: the
# model answers with its reasoning AND a tool call, the loop appends the tool
# result, and the NEXT completion is formed from all three. That third request
# is where the reasoning is lost.
REASONING_TEXT = "the-reasoning-that-gets-discarded"

MULTI_TURN: list[dict] = [
    {"role": "user", "content": [{"text": "what is the weather"}]},
    {
        "role": "assistant",
        "content": [
            {REASONING_BLOCK_KEY: {"reasoningText": {"text": REASONING_TEXT}}},
            {"toolUse": {"toolUseId": "t1", "name": "weather", "input": {}}},
        ],
    },
    {
        "role": "user",
        "content": [
            {"toolResult": {
                "toolUseId": "t1", "status": "success",
                "content": [{"text": "22C"}],
            }},
        ],
    },
]

SINGLE_TURN: list[dict] = [
    {"role": "user", "content": [{"text": "what is the weather"}]},
]


def _model() -> OpenAIModel:
    """A provider object built the way `nora.agent.factory` builds one, minus
    the client — `format_request` never touches it."""
    return OpenAIModel(
        model_id="nora", client=None,  # type: ignore[arg-type]
        params={"max_tokens": 64, "temperature": 0.7, "reasoning_effort": "low"},
    )


def test_the_transport_still_drops_the_reasoning_and_the_detector_agrees():
    """The whole contract, in one assertion pair.

    The count and the drop are asserted TOGETHER on purpose. Either half alone
    is self-attestation: a detector that agrees with itself measures nothing,
    and a drop nobody counts is an unmeasured defect.
    """
    assert reasoning_blocks(MULTI_TURN) == 1, (
        "the detector no longer sees the reasoning block the loop puts in "
        "agent.messages — `reasoning_blocks` has stopped matching the shape "
        "the SDK actually builds, and reasoning_strip_log is now blind"
    )
    request = _model().format_request(MULTI_TURN)  # type: ignore[arg-type]
    rendered = json.dumps(request)
    assert REASONING_TEXT not in rendered, (
        "the Chat Completions transport now CARRIES reasoningContent in a "
        "multi-turn position. Issue #325's defect is gone, and with it the "
        "meaning of every row in reasoning_strip_log written from here on: "
        "the instrument is now counting a drop that does not happen. Re-read "
        "#325 and ADR 0011 before relaxing this."
    )


def test_a_first_turn_carries_no_reasoning_to_lose():
    """The negative control, and it is not decoration.

    `== 0` is also true of a predicate that has stopped reading anything at
    all, so the positive case above is what makes this one mean something —
    and this one is what stops the positive case from being satisfied by a
    function that returns 1 for everything. The pair is the measurement.
    """
    assert reasoning_blocks(SINGLE_TURN) == 0
    request = _model().format_request(SINGLE_TURN)  # type: ignore[arg-type]
    assert "what is the weather" in json.dumps(request), (
        "the first turn's own content did not survive formatting either — "
        "this control is passing because NOTHING is being rendered, not "
        "because there was no reasoning to lose"
    )


@pytest.mark.parametrize(
    "messages",
    [
        None,
        [],
        ["not a dict"],
        [{"role": "assistant"}],
        [{"role": "assistant", "content": "a string, not a list"}],
        [{"role": "assistant", "content": ["not a dict either"]}],
    ],
)
def test_a_message_shape_it_cannot_read_counts_as_nothing_rather_than_raising(
    messages,
):
    """`reasoning_blocks` runs on the reply path, inside a hook, on a
    third-party structure. A Strands version that reshapes a message must
    degrade to «I saw none» rather than raise into a turn that is otherwise
    fine — the same posture `factory.final_text` holds for `AgentResult`.

    That this is SAFE rather than merely quiet is what the two tests above are
    for: a zero read for the wrong reason is caught there, against the real
    formatter, not here.
    """
    assert reasoning_blocks(messages) == 0
