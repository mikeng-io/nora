"""THE PROBE: the counter moves when the condition occurs, and
does not when it does not.

A test that asserts `reasoning_strip_log` exists would be decoration. What is
asserted here is that a REAL Strands loop, driven by a model that answers the
way a reasoning model answers — a `reasoningContent` block and a tool call —
leaves behind a row saying its reasoning was thrown away, and that the same
loop driven by a model that emits no reasoning leaves behind a row saying it
was not. The two together are the measurement; either alone is satisfied by a
watch that answers everything the same way.

The loop is the real one (`LiveAgentRunner` -> `factory.create_agent` ->
`strands.Agent`). Only the MODEL is a double, because the model is the
instrument being read and the gateway is what cannot be reached from a test.
`_AlwaysCallsNow` in `tests/test_guards_terminal.py` is the shape this borrows.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any

from strands.models.model import Model

from nora.agent import factory
from nora.agent.guards import Deliverer, ReplyGuard
from nora.agent.ports import ToolSpec, adapt
from nora.agent.reasoning_strip import ReasoningStripWatch
from nora.app.agent_runner import LiveAgentRunner
from nora.doctrine.loader import Doctrine
from nora.domain.audience import SPEAKING_AUDIENCES
from nora.domain.models import ConversationTarget, NormalizedMessage, TagContext
from nora.engagement.stages import deliver, invoke
from nora.obs.metrics import Metrics
from tests.fakes import FixedModelSource, SinkSender

RETRY = factory.build_retry_policy(timeout_seconds=30.0)
TARGET = ConversationTarget("signal", "g1", "group")
BUDGETS = {"now": 4}
# Five turns of rope, so nothing here is bounded by R-8: the loop stops
# because the MODEL stops, which is what keeps the call counts below a
# property of this probe rather than of the budget. It also keeps the
# budget-exhaustion draft out of the picture — that is a second
# `invoke_async` on the same agent, and it would add a fourth model call
# whose presence depends on a decision this file is not about.
LIMITS = {"turns": 5, "output_tokens": 12_000, "total_tokens": 64_000}


async def _now() -> str:
    """The current time.

    Returns: a fixed string.
    """
    return "Thursday"


class _ScriptedModel(Model):
    """Calls `now` on every pass, optionally emitting its reasoning first.

    `reasons=True` is what a model at `reasoning_effort != "none"` does, and
    what the OmniRoute transport then cannot carry into the SECOND request:
    the loop appends this whole message — reasoning block included — to
    `agent.messages`, and the next completion is formed from it.

    `reasons=False` is the same loop at `reasoning_effort = "none"`. It is the
    control, and it is the population the tag path runs in.
    """

    def __init__(self, *, reasons: bool, tool_passes: int = 2) -> None:
        self._reasons = reasons
        # After this many tool calls it ANSWERS, so the loop ends on
        # `end_turn` rather than on one of R-8's caps. See LIMITS.
        self._tool_passes = tool_passes
        self.calls = 0
        # `config` is real rather than `{}`, because the watch reads its
        # provenance off the provider — `model_id` and `reasoning_effort` land
        # on the row from here, exactly as they do from a real `OpenAIModel`.
        self._config: dict[str, Any] = {
            "model_id": "nora",
            "params": {"reasoning_effort": "low" if reasons else "none"},
        }

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> Any:
        return self._config

    async def structured_output(
        self, output_model: Any, prompt: Any = None, **kwargs: Any
    ) -> Any:
        raise NotImplementedError

    async def stream(self, *args: Any, **kwargs: Any) -> AsyncIterable[Any]:
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        if self._reasons:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {
                "reasoningContent": {"text": f"thinking about pass {self.calls}"}}}}
            yield {"contentBlockStop": {}}
        if self.calls <= self._tool_passes:
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


def _doctrine() -> Doctrine:
    return Doctrine(
        sections=(), text="<nora_doctrine>be helpful</nora_doctrine>",
        doctrine_text="be helpful", sha256="deadbeef", runtime_context="",
        loaded_at=0.0,
    )


def _ctx() -> TagContext:
    msg = NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body="nora.000 hi",
        raw={},
    )
    return TagContext(
        tag_id="tag-1", message=msg, tagger=None, transcript=(), peer_card=(),
        started_at=0.0,
    )


class _Store:
    async def recent(self, *, conversation_id: str, limit: int):
        return []


class _RecordingStore:
    """The `ReasoningStripStore` port, in memory. A real fake: it keeps what it
    is given so the test can read the ROW rather than a flag, which is what
    makes the provenance columns assertable at all."""

    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def record_observation(self, observation: Any) -> None:
        self.rows.append(observation)


async def _drive(
    *, reasons: bool, terminal=frozenset(), metrics=None, store=None,
    budgets=None, model=None,
):
    """One real turn. Returns `(model, watch, store, metrics)`."""
    metrics = metrics or Metrics()
    budgets = budgets or BUDGETS
    store = store if store is not None else _RecordingStore()
    model = model if model is not None else _ScriptedModel(reasons=reasons)
    watch = ReasoningStripWatch(
        path="tag", tag_id="tag-1", store=store, metrics=metrics,
    )
    deliverer = Deliverer(
        store=_Store(), adapter=SinkSender(), target=TARGET, doctrine=_doctrine(),
        canary="C", tag_id="tag-1", nora_sender_id="nora", message_cap=1,
        audience="tag", metrics=metrics,
    )
    tool = adapt(ToolSpec(name="now", description="", schema={}, call=_now,
                          budget=budgets["now"]))
    # THROUGH `invoke.run`, not straight into `LiveAgentRunner`. The stage is
    # what owns the `await watch.flush()` on the tag and participation paths,
    # so driving the runner directly would leave the one line that turns a
    # counted turn into a STORED one untested — and the row is the half a
    # Prometheus counter cannot give.
    await invoke.run(
        runner=LiveAgentRunner(models=FixedModelSource(model), retry=RETRY),
        doctrine=_doctrine(), tools=[tool], pre_fill=[],
        guard=ReplyGuard(conversation_id="g1", budgets=budgets, terminal=terminal,
                         deliverer=deliverer, metrics=metrics),
        reasoning_watch=watch,
        tag_message="<tag_message/>", ctx=_ctx(), limits=LIMITS,
        timeout_seconds=30.0, metrics=metrics, audience="tag",
    )
    return model, watch, store, metrics


# ---- the probe -------------------------------------------------------------


async def test_a_tool_using_turn_that_reasons_is_recorded_as_stripped():
    """The core assertion.

    Three model calls: the first carries nothing to lose, and the two
    continuations each carry the previous pass's reasoning into a request the
    Chat Completions transport will not take it in. Both continuation calls are
    opportunities and both are strips — a 2-of-2 turn, which is precisely the
    reading a bare strip count cannot give.
    """
    model, _watch, store, _ = await _drive(reasons=True)
    assert model.calls == 3, "the loop did not go multi-turn; there is nothing to probe"

    (row,) = store.rows
    assert row.model_calls == 3
    assert row.continuation_calls == 2, "the denominator is wrong"
    assert row.strip_calls == 2, (
        "a continuation call carried reasoning into the Chat Completions "
        "transport and the watch did not notice — the instrument is blind"
    )
    # TWO distinct reasoning blocks were lost, not three re-drops of them:
    # the third request re-formats the first pass's block as well as the
    # second's, and counting that twice would report more reasoning lost
    # than the turn ever produced.
    assert row.stripped_blocks == 2


async def test_the_same_turn_with_no_reasoning_is_recorded_as_clean():
    """THE CONTROL, and it is what makes the probe above mean something.

    Identical loop, identical tool, identical call count — one different
    model. A watch that recorded `stripped` for everything would pass the test
    above and fail here, which is the failure mode CLAUDE.md names as "a fake
    that answers every request the same way".

    It is also a real population rather than a hypothetical: the tag path runs
    at `reasoning_effort = "none"` — and a zero here reads «cannot happen», not
    «fixed», which is why `reasoning_effort` is on the row beside the counts.
    """
    model, _, store, _ = await _drive(reasons=False)
    assert model.calls == 3

    (row,) = store.rows
    assert row.continuation_calls == 2, "the denominator must be recorded either way"
    assert row.strip_calls == 0
    assert row.stripped_blocks == 0
    assert row.reasoning_effort == "none", (
        "without this column a clean row at `none` is indistinguishable from a "
        "clean row at `low`, and only one of those is good news"
    )


async def test_both_halves_of_the_ratio_reach_prometheus():
    """The counter carries its own denominator.

    A series with only the strips cannot separate «no strips» from «no
    observation» — both read as absent, and this process restarts two or three
    times a day with labels created lazily. `clean` being recorded is what
    makes an absent series mean NO DATA.
    """
    _, _, _, metrics = await _drive(reasons=True)
    assert metrics.get("nora_reasoning_strip_total", path="tag", outcome="stripped") == 2
    assert metrics.get("nora_reasoning_strip_total", path="tag", outcome="clean") == 0

    _, _, _, clean_metrics = await _drive(reasons=False)
    assert clean_metrics.get(
        "nora_reasoning_strip_total", path="tag", outcome="clean") == 2
    assert clean_metrics.get(
        "nora_reasoning_strip_total", path="tag", outcome="stripped") == 0


async def test_the_provenance_on_the_row_is_read_off_the_model_not_from_config():
    """`model` and `reasoning_effort` come from the provider the call actually
    used. That is the whole reason they are on the row: config says what was
    configured, not what ran."""
    _, _, store, _ = await _drive(reasons=True)
    (row,) = store.rows
    assert (row.model, row.reasoning_effort) == ("nora", "low")
    assert row.path == "tag"
    assert row.tag_id == "tag-1"


async def test_a_cancelled_model_call_is_not_an_opportunity():
    """The pass `ReplyGuard` refuses is never formatted, so it strips nothing.

    `now` is declared TERMINAL here, so the guard cancels the second pass —
    and at that moment `agent.messages` holds exactly the reasoning-carrying
    assistant message a naive watch would count. A request that is never sent
    has no reasoning to lose. This is what holds the watch's registration
    AFTER the guard in `factory.create_agent`: reorder them and the cancelled
    pass is counted, inflating both halves of the ratio on most tag turns.
    """
    # `now` terminal at a budget of ONE, so the turn is over the moment it
    # has run — the same declaration `tests/test_guards_terminal.py` uses
    # for its own cancellation probe. DEC-42 ends a turn when no terminal
    # verb can be called AGAIN, so a budget of 4 would not cancel anything.
    model, _, store, metrics = await _drive(
        reasons=True, terminal={"now"}, budgets={"now": 1},
    )
    assert model.calls == 1, "the guard did not cancel; this control proves nothing"
    assert store.rows == [], (
        "a turn whose only continuation call was cancelled has no opportunity "
        "and must write no row"
    )
    assert metrics.get(
        "nora_reasoning_strip_total", path="tag", outcome="stripped") == 0


async def test_a_single_call_turn_writes_no_row():
    """Zero opportunity, zero denominator, nothing to divide.

    Writing it would turn `reasoning_strip_log` from a census of the calls at
    risk into a log of every model call in the system — and the ratio it
    exists to report would be diluted by turns that could never have
    contributed to either half.
    """
    store = _RecordingStore()
    watch = ReasoningStripWatch(path="tag", store=store, metrics=Metrics())
    assert watch.observation is None
    await watch.flush()
    assert store.rows == []


async def test_flush_is_idempotent():
    """Both production paths flush on more than one exit (a timeout, an error,
    the happy path), and the Dreamer flushes from three places. One row."""
    _, watch, store, _ = await _drive(reasons=True)
    await watch.flush()
    await watch.flush()
    assert len(store.rows) == 1


async def test_a_store_that_raises_costs_the_turn_nothing():
    """The record is of something that has already happened. A failed write is
    counted and swallowed — never the reason a reply does not go out."""

    class _Broken:
        async def record_observation(self, observation: Any) -> None:
            raise RuntimeError("database is gone")

    metrics = Metrics()
    _, _watch, _, _ = await _drive(reasons=True, metrics=metrics, store=_Broken())
    assert metrics.get("nora_reasoning_strip_writes_total", outcome="failed") == 1
    assert metrics.get("nora_reasoning_strip_writes_total", outcome="ok") == 0
    # The counters the loop itself moved are untouched by the write failing:
    # the observation happened, only its durable copy did not.
    assert metrics.get("nora_reasoning_strip_total", path="tag", outcome="stripped") == 2


# ---- the unit of a row is the TURN, not one `invoke_async` ----------------


async def test_the_budget_exhaustion_draft_is_counted_into_the_same_row():
    """A DECISION, recorded here because the row it produces looks like a bug.

    A turn cut off by one of R-8's caps with no prose gets one more completion
    over its own accumulated context (`factory.draft_from_exhausted_loop`) — a
    SECOND `invoke_async` on the SAME agent, with this watch still registered.
    Its request genuinely carries the accumulated reasoning and the transport
    genuinely drops it.

    So it is counted, and the stated unit of a row is the TURN rather than the
    invocation. The alternative — excluding it — would under-report a real
    strip on precisely the turns that had already run out of room, and would
    need a second flush point to implement. The rate is unaffected either way:
    numerator and denominator move together.

    This is the population most likely to be inspected (a cut-off turn is why
    somebody is looking), so the row's scope is pinned rather than left to be
    rediscovered from a `model_calls` that does not match the loop.
    """
    metrics = Metrics()
    store = _RecordingStore()
    model = _ScriptedModel(reasons=True, tool_passes=99)
    watch = ReasoningStripWatch(
        path="tag", tag_id="tag-1", store=store, metrics=metrics,
    )
    deliverer = Deliverer(
        store=_Store(), adapter=SinkSender(), target=TARGET, doctrine=_doctrine(),
        canary="C", tag_id="tag-1", nora_sender_id="nora", message_cap=1,
        audience="tag", metrics=metrics,
    )
    tool = adapt(ToolSpec(name="now", description="", schema={}, call=_now,
                          budget=BUDGETS["now"]))
    await invoke.run(
        runner=LiveAgentRunner(models=FixedModelSource(model), retry=RETRY),
        doctrine=_doctrine(), tools=[tool], pre_fill=[],
        guard=ReplyGuard(conversation_id="g1", budgets=BUDGETS,
                         terminal=frozenset(), deliverer=deliverer,
                         metrics=metrics),
        reasoning_watch=watch,
        tag_message="<tag_message/>", ctx=_ctx(),
        limits={"turns": 2, "output_tokens": 12_000, "total_tokens": 64_000},
        timeout_seconds=30.0, metrics=metrics, audience="tag",
    )

    assert model.calls == 3, (
        "the loop was not cut off, or the budget draft did not fire — this "
        "probe is about the third call and there isn't one"
    )
    (row,) = store.rows
    assert row.model_calls == 3, (
        "the draft's model call is missing from the row: the unit has "
        "silently become one `invoke_async` again"
    )
    assert row.continuation_calls == 2
    assert row.strip_calls == 2


# ---- the wiring: both production senders carry one, labelled ---------------


def _handle(*, audience: str, store: Any):
    """`deliver.build` is the one seam both production senders pass through
    (`tests/architecture/test_only_two_paths_can_send.py` proves there are
    exactly two), which is why the watch is minted there rather than at each
    call site."""
    return deliver.build(
        store=_Store(), adapter=SinkSender(), target=TARGET, doctrine=_doctrine(),
        canary="C", tag_id="tag-1", nora_sender_id="nora",
        conversation_id="g1", budgets={"send_reply": 1},
        terminal={"send_reply"}, metrics=Metrics(), audience=audience,
        composer=None, reasoning_strip_store=store,
    )


def test_every_audience_deliver_build_can_be_given_reaches_the_row():
    """`path` IS `audience`, and the vocabulary is the declared one.

    Parametrized over `SPEAKING_AUDIENCES` rather than over a list written
    here, so a path such as `heartbeat` (every reminder and gathering turn —
    tool-using and multi-turn) or `proactive` cannot be missing from the
    documented vocabulary.
    """
    for audience in SPEAKING_AUDIENCES:
        handle = _handle(audience=audience, store=None)
        assert handle.reasoning_watch._path == audience


def test_the_participation_occasion_labels_its_rows_participation():
    """`path` is `audience`, not a literal.

    The two senders already declare which loop they are, and the label is
    required with no default there for the reason a second spelling of the same
    fact would undo — a row labelled `tag` from the participation occasion
    cannot be read beside `nora_silent_failures_total{audience="participation"}`,
    and "which path" is one of the questions the row answers.
    """
    tag = _handle(audience="tag", store=None)
    participation = _handle(audience="participation", store=None)
    assert tag.reasoning_watch._path == "tag"
    assert participation.reasoning_watch._path == "participation", (
        "the participation occasion's rows are labelled `tag` — the path column "
        "no longer distinguishes the two loops"
    )


def test_a_composition_with_no_store_still_counts_and_writes_nothing():
    """`store=None` is the honest inert default: the offline and eval
    compositions record the Prometheus counters and no row. Never a raised
    error, and never a fallback that pretends to have remembered something."""
    handle = _handle(audience="tag", store=None)
    assert handle.reasoning_watch._store is None
