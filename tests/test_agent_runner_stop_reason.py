"""R-23's two production halves: the SDK's behaviour, and the one line that
carries it out of `LiveAgentRunner.invoke`.

`tests/integration/test_turn_budget_is_legible.py` proves the pipeline reports
a budget stop distinctly, but it runs against `FakeAgentRunner`. Everything
below the stage boundary — Strands actually populating `stop_reason`, and
`LiveAgentRunner` actually reading it — is only exercised here. A runner that
awaited `factory.invoke` and discarded its return value would pass every test
that uses the fake.
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
                          budget=99))


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
    stop_reason, _final_text, _trace = await runner.invoke(
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
    """THE regression: `LiveAgentRunner.invoke` must not drop the `AgentResult`
    from `factory.invoke(...)` and return `None`, or no caller could tell a
    cut-off turn from a finished one. A `None` here is that bug exactly,
    whatever the loop did."""
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
    # The LOOP makes exactly one call — the cap binds at the top of cycle two —
    # and the second is the budget-exhaustion draft, which fires on
    # `limit_turns` for the same reason it fires on `limit_total_tokens`: both
    # mean "cut off mid-work", and the group is owed an answer either way. The
    # assertion counts calls because the cost of that draft is a real thing to
    # keep pinned; the drafting tests below own the behaviour itself.
    assert model.calls == 2


# ── DEC-93: what `final_text` reads, against the REAL SDK shape ──────────────
#
# `nora.agent.factory.final_text` is shared by the reply runner and the Dreamer
# (which cannot import `nora.app`), so the one extraction is tested here.


def test_final_text_reads_only_authored_text_blocks():
    """`final_text` is production code on the reply path; this covers the
    shape it reads.

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
    renames or restructures `message` must produce no recovery text — never an
    exception into a turn that otherwise succeeded."""
    assert factory.final_text(SimpleNamespace()) == ""                # no `message`
    assert factory.final_text(SimpleNamespace(message="not a dict")) == ""
    assert factory.final_text(SimpleNamespace(message={"role": "assistant"})) == ""


async def test_the_live_runner_still_returns_the_promoted_final_text():
    """`LiveAgentRunner.invoke` returns `(stop_reason, final_text, trace)`, and
    the second element is produced by a function in another package. An import
    that resolved but was never CALLED — or a runner that returned "" — would
    leave every other test in this file green, because they all discard the
    second element.

    The stub model ends its last cycle with a `text` block ("ok"), which is
    exactly DEC-93's shape: prose the model wrote instead of calling a tool."""
    stop_reason, text, _trace = await _run_returning_text(
        tokens_per_call=10,
        limits={"turns": 8, "output_tokens": 12_000, "total_tokens": 64_000},
    )
    assert stop_reason == "end_turn"
    assert text == "ok"


async def _run_returning_text(
    *, tokens_per_call: int, limits: dict,
) -> tuple[str | None, str, Any]:
    model = _StubModel(tokens_per_call=tokens_per_call, tool_use_calls=1)
    runner = LiveAgentRunner(models=FixedModelSource(model), retry=RETRY)
    return await runner.invoke(
        doctrine=_doctrine(), tools=[_tool()], pre_fill=[],
        guard=ReplyGuard(conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
                         metrics=Metrics()),
        tag_message="<tag_message/>", limits=limits, ctx=_ctx(), trace_attributes={},
    )


def test_a_guard_cancelled_model_call_is_not_treated_as_her_words():
    """THE PROBE at the seam that produces the defect
    (`tests/unit/test_engagement_pipeline.py` covers the consumer).

    When `ReplyGuard` cancels a model call, Strands synthesises
    `message={"content":[{"text": cancel_text}]}` with `stop_reason="end_turn"`
    — byte-identical in shape to the model having written prose. The recovery
    would send «Reply already sent — the turn is finished.» to a Cantonese
    group, feed it to Honcho as her own speech, and settle the turn ✅.

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


# ── the budget-exhaustion draft ───────────────────────────────────────────────
#
# Same discipline as the R-23 tests above: the SDK decides, the double only
# behaves like a provider. The property under test is that a turn cut off
# mid-work makes ONE more tool-less call over the context it already had, and
# that everything about that call is bounded and fail-closed.


class _ConfigRecordingModel(_StubModel):
    """`_StubModel` that owns a real `params` dict, so the `tool_choice` flip
    is observable rather than assumed.

    The base class answers `get_config() -> {}` and swallows `update_config`,
    which would let a draft that never actually disabled tools pass silently —
    the exact thing this subclass exists to prevent. `params_at_call` records
    what was in force for EACH stream, because "off during the draft" and
    "off afterwards" are different claims and only the first one matters for
    the answer the group reads.
    """

    def __init__(self, *, tokens_per_call: int, tool_use_calls: int = 1,
                 fail_on_call: int | None = None) -> None:
        super().__init__(tokens_per_call=tokens_per_call, tool_use_calls=tool_use_calls)
        self._config: dict[str, Any] = {"params": {"tool_choice": "required"}}
        self.params_at_call: list[dict] = []
        self._fail_on_call = fail_on_call

    def get_config(self) -> Any:
        return self._config

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        self.params_at_call.append(dict(self._config.get("params") or {}))
        if self._fail_on_call is not None and self.calls + 1 == self._fail_on_call:
            self.calls += 1
            raise RuntimeError("provider fell over during the draft")
        async for event in super().stream(*args, **kwargs):
            yield event


async def _run_recording(
    *, tokens_per_call: int, limits: dict, tool_use_calls: int = 1,
    fail_on_call: int | None = None, draft_timeout_seconds: float = 45.0,
) -> tuple[str | None, str, _ConfigRecordingModel]:
    model = _ConfigRecordingModel(
        tokens_per_call=tokens_per_call, tool_use_calls=tool_use_calls,
        fail_on_call=fail_on_call,
    )
    runner = LiveAgentRunner(
        models=FixedModelSource(model), retry=RETRY,
        draft_timeout_seconds=draft_timeout_seconds,
    )
    stop_reason, final_text, _trace = await runner.invoke(
        doctrine=_doctrine(), tools=[_tool()], pre_fill=[],
        guard=ReplyGuard(conversation_id="g1", budgets=BUDGETS, terminal=TERMINAL,
                         metrics=Metrics()),
        tag_message="<tag_message/>", limits=limits, ctx=_ctx(), trace_attributes={},
    )
    return stop_reason, final_text, model


_CUT_OFF = {"turns": 8, "output_tokens": 12_000, "total_tokens": 4_000}
_ROOMY = {"turns": 8, "output_tokens": 12_000, "total_tokens": 64_000}


async def test_an_exhausted_loop_drafts_an_answer_from_what_it_already_had():
    """A turn cut off by a cap must not hand the pipeline an empty
    `final_text`, leaving the canned apology as the only thing to deliver — on
    a turn that has usually already read everything it needed.

    One extra call, and the words come back. `model.calls == 2` is the whole
    claim: the loop spent one, the draft spent exactly one more.
    """
    stop_reason, final_text, model = await _run_recording(
        tokens_per_call=5_000, limits=_CUT_OFF,
    )
    assert stop_reason == "limit_total_tokens"
    assert final_text == "ok"
    assert model.calls == 2


async def test_the_draft_runs_with_tools_off_and_puts_the_pin_back():
    """`tool_choice="required"` (DEC-21) is what makes her reach for
    `send_reply` instead of answering in prose. During the draft prose is
    exactly what is wanted, and leaving the pin on would force another tool
    call — the one thing there is no budget for.

    Both directions asserted: OFF for the drafting stream, RESTORED after. The
    second matters less (the agent is discarded) but a flip that never
    reverses is the kind of thing that becomes load-bearing elsewhere later.
    """
    _stop, _text, model = await _run_recording(tokens_per_call=5_000, limits=_CUT_OFF)
    assert model.params_at_call[0]["tool_choice"] == "required"   # the loop
    assert model.params_at_call[1]["tool_choice"] == "none"       # the draft
    assert model.get_config()["params"]["tool_choice"] == "required"


async def test_a_turn_that_finished_normally_never_drafts():
    """The control, and the cost guard. A healthy turn must not pay for a
    second call — if this ever goes to 2, every tag in the room got more
    expensive for a feature that only applies to cut-off turns."""
    # `tool_use_calls=0` so the LOOP itself is one call — it answers in prose
    # on the first stream. With the default of 1 the loop legitimately spends
    # two (tool, then answer) and a `calls == 2` here would pass whether or not
    # a draft happened, which is a test that asserts nothing.
    stop_reason, _text, model = await _run_recording(
        tokens_per_call=5_000, limits=_ROOMY, tool_use_calls=0,
    )
    assert stop_reason == "end_turn"
    assert model.calls == 1


async def test_a_cut_off_turn_that_already_wrote_prose_costs_no_extra_call():
    """DEC-93's case, which needs no model at all: the cut-off completion
    already carried text, so there is nothing to draft. `tool_use_calls=0`
    makes the very first stream answer in prose, and the cap then binds on the
    NEXT cycle with those words already in hand."""
    _stop, final_text, model = await _run_recording(
        tokens_per_call=5_000, limits=_CUT_OFF, tool_use_calls=0,
    )
    assert final_text == "ok"
    assert model.calls == 1


async def test_a_draft_that_fails_leaves_the_turn_with_nothing_to_say():
    """FAIL-CLOSED. The drafting call is an improvement on the fallback, never
    a replacement for having one: when it raises, `final_text` comes back empty
    and the pipeline delivers the canned line, as it would with no draft.
    A turn that has already failed once must not fail louder."""
    stop_reason, final_text, model = await _run_recording(
        tokens_per_call=5_000, limits=_CUT_OFF, fail_on_call=2,
    )
    assert stop_reason == "limit_total_tokens"
    assert final_text == ""
    assert model.calls == 2          # it was attempted, and it was survived
