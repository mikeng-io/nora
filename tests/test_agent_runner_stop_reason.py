"""R-23's two production halves: the SDK's behaviour, and the one line that
used to throw it away.

`tests/integration/test_turn_budget_is_legible.py` proves the pipeline reports
a budget stop distinctly, but it runs against `FakeAgentRunner`. Everything
below the stage boundary — Strands actually populating `stop_reason`, and
`LiveAgentRunner` actually reading it — is only exercised here. That gap is
not academic: the bug WAS `LiveAgentRunner.invoke` awaiting `factory.invoke`
and discarding its return value, a line no test touched.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from types import SimpleNamespace
from typing import Any

from strands.models.model import Model

from nora.agent import factory
from nora.agent.guards import ReplyGuard
from nora.agent.ports import ToolSpec, adapt
from nora.agent.registry import ToolRegistry
from nora.app.agent_runner import LiveAgentRunner
from nora.doctrine.loader import Doctrine
from nora.domain.models import NormalizedMessage, TagContext
from nora.obs.metrics import Metrics
from tests.fakes import FixedModelSource

BUDGETS = ToolRegistry().budgets()
TERMINAL = ToolRegistry().terminal_tools()
RETRY = factory.build_retry_policy(timeout_seconds=30.0)


async def _now() -> str:
    """The current time.

    Returns: a fixed string.
    """
    return "Thursday"


def _tool():
    """A real Strands tool object, built the way production builds one."""
    return adapt(ToolSpec(name="now", description="", schema={}, call=_now,
                          when="the stub turn needs a tool", budget=99))


class _StubModel(Model):
    """A model that answers like the real one, including its usage accounting.

    Not a stub that returns a fixed `stop_reason`: it emits a `toolUse` and a
    per-call `totalTokens` in a `metadata` event, exactly as the OpenAI
    provider does under `tool_choice="required"`, and lets Strands' own
    `_check_limits` decide whether a cap was crossed. That is the whole point
    — the value under test is one the SDK computes, and a double that handed
    it over directly would assert nothing about the SDK at all.

    **It has to call a tool for a cap to bind at all**, and that is a real
    property of the SDK worth writing down: `_check_limits` runs at the TOP of
    an event-loop cycle, and the loop only reaches a second cycle when the
    previous one ended in `tool_use`. A turn that answers in one shot can
    never trip a limit. Nora's turns always use tools — `send_reply` IS a tool
    and `tool_choice="required"` forces one (DEC-21) — so every real turn has
    the shape reproduced here.

    `tool_use_calls` bounds how many cycles it asks for before settling on
    `end_turn`, so the control case below can finish naturally rather than
    looping forever.
    """

    def __init__(self, *, tokens_per_call: int, tool_use_calls: int = 1) -> None:
        self._tokens = tokens_per_call
        self._tool_use_calls = tool_use_calls
        self.calls = 0

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(self, output_model: Any, prompt: Any = None, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        self.calls += 1
        use_tool = self.calls <= self._tool_use_calls
        yield {"messageStart": {"role": "assistant"}}
        if use_tool:
            yield {"contentBlockStart": {"start": {"toolUse": {
                "name": "now", "toolUseId": f"t{self.calls}"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": "{}"}}}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": "ok"}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if use_tool else "end_turn"}}
        yield {
            "metadata": {
                "usage": {
                    "inputTokens": self._tokens // 2,
                    "outputTokens": self._tokens // 2,
                    "totalTokens": self._tokens,
                },
                "metrics": {"latencyMs": 1},
            }
        }


def _doctrine() -> Doctrine:
    return Doctrine(
        sections=(), text="<nora_doctrine>be helpful</nora_doctrine>", doctrine_text="be helpful",
        sha256="deadbeef", runtime_context="", loaded_at=0.0,
    )


def _ctx() -> TagContext:
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="nora.000 hi", raw={},
    )
    return TagContext(
        tag_id="tag-1", message=msg, tagger=None, transcript=(), peer_card=(), started_at=0.0,
    )


async def _run(
    *, tokens_per_call: int, limits: dict, tool_use_calls: int = 1,
) -> tuple[str | None, _StubModel]:
    model = _StubModel(tokens_per_call=tokens_per_call, tool_use_calls=tool_use_calls)
    runner = LiveAgentRunner(models=FixedModelSource(model), retry=RETRY)
    stop_reason, _final_text = await runner.invoke(
        doctrine=_doctrine(), tools=[_tool()], pre_fill=[],
        guard=ReplyGuard(conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
                         metrics=Metrics()),
        tag_message="<tag_message/>", limits=limits, ctx=_ctx(), trace_attributes={},
    )
    return stop_reason, model


async def test_strands_reports_a_token_cap_as_a_stop_reason_and_raises_nothing():
    """The SDK behaviour every other part of R-23 depends on.

    `_check_limits` yields an `EventLoopStopEvent` with
    `stop_reason="limit_total_tokens"` and the call RETURNS. If a future
    Strands upgrade started raising instead, `invoke.run` would catch it and
    count `llm_error` — a different reason, and this test is what would say so
    before the deployment did.
    """
    stop_reason, _model = await _run(
        tokens_per_call=5_000, limits={"turns": 8, "output_tokens": 12_000, "total_tokens": 4_000},
    )
    assert stop_reason == "limit_total_tokens"


async def test_the_same_turn_under_a_sufficient_budget_ends_normally():
    """The control. Identical model, identical spend — only the cap differs —
    so a `limit_total_tokens` above cannot be an artefact of the double."""
    stop_reason, _model = await _run(
        tokens_per_call=5_000, limits={"turns": 8, "output_tokens": 12_000, "total_tokens": 64_000},
    )
    assert stop_reason == "end_turn"


async def test_the_live_runner_returns_the_stop_reason_instead_of_discarding_it():
    """THE regression. `LiveAgentRunner.invoke` was `await factory.invoke(...)`
    with the `AgentResult` dropped on the floor and `None` returned, so no
    caller could tell a cut-off turn from a finished one. A `None` here is that
    bug exactly, whatever the loop did."""
    for total_tokens, expected in ((4_000, "limit_total_tokens"), (64_000, "end_turn")):
        stop_reason, _model = await _run(
            tokens_per_call=5_000,
            limits={"turns": 8, "output_tokens": 12_000, "total_tokens": total_tokens},
        )
        assert stop_reason is not None
        assert stop_reason == expected


async def test_a_turn_cap_stops_the_loop_before_the_token_cap_does():
    """`turns` binds first in Strands' check order, which is what R-24's
    64,000 is chosen to make true in production: a cheap turn should run out of
    ROUND TRIPS, not tokens."""
    stop_reason, model = await _run(
        tokens_per_call=10, limits={"turns": 1, "output_tokens": 12_000, "total_tokens": 64_000},
    )
    assert stop_reason == "limit_turns"
    assert model.calls == 1


# ── DEC-93: what `final_text` reads, against the REAL SDK shape ──────────────
#
# It was `nora.app.agent_runner._final_text` until the Dreamer needed the same
# extraction and could not import `nora.app`; it is now
# `nora.agent.factory.final_text`, imported back by this module's runner. The
# tests below moved with it UNCHANGED except for that import — the point of a
# promotion rather than a reimplementation is that the behaviour is the same
# behaviour, so a test that had to be rewritten to keep passing would have been
# evidence against the move.


def test_final_text_reads_only_authored_text_blocks():
    """`final_text` is production code on the reply path and had NO coverage of
    the shape it reads (pre-deploy review 2026-08-10).

    The blocks it must SKIP are the point. `reasoningContent` is the model's
    private thinking — Strands emits it as
    `{"reasoningContent": {"reasoningText": {"text": ...}}}`, so a naive
    recursive text-scrape would ship her internal monologue to the group — and
    `toolUse` is a call, not speech."""
    result = SimpleNamespace(message={"role": "assistant", "content": [
        {"reasoningContent": {"reasoningText": {"text": "let me think…"}}},
        {"toolUse": {"name": "weather", "input": {}}},
        {"text": "37 度，酷熱警告生效"},
    ]})

    assert factory.final_text(result) == "37 度，酷熱警告生效"


def test_final_text_degrades_to_empty_on_an_unexpected_result_shape():
    """It reads a third-party shape on the reply path. A Strands version that
    renames or restructures `message` must produce the pre-DEC-93 behaviour —
    no recovery — never an exception into a turn that otherwise succeeded."""
    assert factory.final_text(SimpleNamespace()) == ""                # no `message`
    assert factory.final_text(SimpleNamespace(message="not a dict")) == ""
    assert factory.final_text(SimpleNamespace(message={"role": "assistant"})) == ""


async def test_the_live_runner_still_returns_the_promoted_final_text():
    """The promotion's own regression: `LiveAgentRunner.invoke` returns
    `(stop_reason, final_text)`, and the second element is now produced by a
    function in another package. An import that resolved but was never CALLED —
    or a runner that went back to returning "" — would leave every other test in
    this file green, because they all discard the second element.

    The stub model ends its last cycle with a `text` block ("ok"), which is
    exactly DEC-93's shape: prose the model wrote instead of calling a tool."""
    stop_reason, text = await _run_returning_text(
        tokens_per_call=10,
        limits={"turns": 8, "output_tokens": 12_000, "total_tokens": 64_000},
    )
    assert stop_reason == "end_turn"
    assert text == "ok"


async def _run_returning_text(*, tokens_per_call: int, limits: dict) -> tuple[str | None, str]:
    model = _StubModel(tokens_per_call=tokens_per_call, tool_use_calls=1)
    runner = LiveAgentRunner(models=FixedModelSource(model), retry=RETRY)
    return await runner.invoke(
        doctrine=_doctrine(), tools=[_tool()], pre_fill=[],
        guard=ReplyGuard(conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
                         metrics=Metrics()),
        tag_message="<tag_message/>", limits=limits, ctx=_ctx(), trace_attributes={},
    )


def test_a_guard_cancelled_model_call_is_not_treated_as_her_words():
    """THE PROBE for the defect this fix exists for, at the seam that produces
    it (`tests/unit/test_engagement_pipeline.py` covers the consumer).

    When `ReplyGuard` cancels a model call, Strands synthesises
    `message={"content":[{"text": cancel_text}]}` with `stop_reason="end_turn"`
    — byte-identical in shape to the model having written prose. The recovery
    would have sent «Reply already sent — the turn is finished.» to a Cantonese
    group, fed it to Honcho as her own speech, and settled the turn ✅.

    It clears both content guards trivially, so nothing downstream stops it:
    the ONLY thing that can tell the two apart is that the guard knows it
    cancelled. Delete `cancelled_model_call` and the recovery has no way to
    refuse this."""
    from nora.agent.egress import scan_reply
    from nora.agent.guards import ReplyGuard

    veto = "Reply already sent — the turn is finished."
    assert scan_reply(veto) is None            # R-18 cannot see it
    guard = ReplyGuard(
        conversation_id="g1", budgets={"send_reply": 1}, terminal={"send_reply"},
        metrics=Metrics(),
    )
    assert guard.cancelled_model_call is False
