"""`ReasoningStripWatch`'s accounting, at the event level.

`tests/unit/test_reasoning_strip_probe.py` drives the REAL Strands loop and is what
proves the instrument detects. This file is the half that loop cannot reach: a
RETRY, and a model call that raised. Both are shapes the SDK produces only
under provider failure, and both would corrupt the denominator silently —
`BeforeModelCallEvent` fires inside the retry loop
(`strands/event_loop/event_loop.py::_handle_model_execution`, the `while True:`
that re-enters at the top of every pass), so an attempt that throttled and
retried is the SAME position asked twice, not two opportunities.

The doubles carry exactly the fields the watch reads, shaped as the SDK shapes
them — `tests/test_guards_terminal.py`'s rule, and for its reason: a double
that answers everything the same way measures nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nora.agent.reasoning_strip import ReasoningStripWatch
from nora.domain.audience import SPEAKING_AUDIENCES
from nora.obs.metrics import Metrics

REASONING = {"reasoningContent": {"reasoningText": {"text": "thinking"}}}
TOOL_USE = {"toolUse": {"toolUseId": "t1", "name": "now", "input": {}}}
TOOL_RESULT = {"toolResult": {"toolUseId": "t1", "status": "success", "content": []}}


class _Agent:
    """`event.agent` — a message list and a model whose config the watch reads
    its provenance off."""

    def __init__(self, messages: list[dict]) -> None:
        self.messages = messages
        self.model = _Model()


class _Model:
    def get_config(self) -> dict[str, Any]:
        return {"model_id": "nora", "params": {"reasoning_effort": "low"}}


class _Before:
    """`cancel` starts False and is a `bool | str`, exactly as the SDK
    declares it."""

    def __init__(self, agent: _Agent, *, cancel: bool | str = False) -> None:
        self.agent = agent
        self.cancel: bool | str = cancel


class _After:
    """`exception` is None on a pass that answered, set on one that raised —
    the discriminator `ReplyGuard._after_model` uses and for the same reason."""

    def __init__(self, agent: _Agent, *, exception: Exception | None = None) -> None:
        self.agent = agent
        self.exception = exception


class _Store:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def record_observation(self, observation: Any) -> None:
        self.rows.append(observation)


def _watch(store: Any = None, metrics: Metrics | None = None) -> ReasoningStripWatch:
    return ReasoningStripWatch(
        path="dreamer:culture", store=store, metrics=metrics or Metrics(),
    )


def _first_call(agent: _Agent) -> list[dict]:
    """The message list as it stands before the loop's first model call."""
    agent.messages[:] = [{"role": "user", "content": [{"text": "go"}]}]
    return agent.messages


def _continuation(agent: _Agent) -> list[dict]:
    """…and as it stands before the second, after one reasoning answer and one
    tool result. This is the list the transport strips."""
    agent.messages.extend([
        {"role": "assistant", "content": [REASONING, TOOL_USE]},
        {"role": "user", "content": [TOOL_RESULT]},
    ])
    return agent.messages


def test_a_retried_attempt_is_one_opportunity_not_two():
    """THE PROPERTY THIS FILE EXISTS FOR.

    A throttled call re-enters `BeforeModelCallEvent` with the message list
    UNCHANGED — same position, same request. Counting the attempt would inflate
    both halves of the ratio exactly on the turns a provider was struggling, so
    the rate would read worst when the evidence is weakest.
    """
    agent = _Agent([])
    watch = _watch()
    _first_call(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))

    _continuation(agent)
    # Attempt 1: raises. Nothing is settled — the next Before is a re-attempt.
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent, exception=RuntimeError("429")))
    # Attempt 2: the same position, answered.
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))

    row = watch.observation
    assert row is not None
    assert row.model_calls == 2
    assert row.continuation_calls == 1, "a retry is not a second opportunity"
    assert row.strip_calls == 1


def test_a_call_that_raised_and_was_not_retried_is_not_counted():
    """A pass that produced NO ANSWER is not an opportunity that was measured.

    The sibling above cannot separate this from the depth guard — a retry lands
    on the same position either way, so the totals agree. This one can: the loop
    gives up (the retry budget is spent, or the error is not retryable) and
    nothing follows. Counting it would put a `stripped` on a request whose
    outcome nobody observed, on exactly the turns a provider was failing — the
    reading least able to afford a false one.

    `exception is None` is the discriminator, the same one
    `ReplyGuard._after_model` uses and for the same reason.
    """
    agent = _Agent([])
    watch = _watch()
    _first_call(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))

    _continuation(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent, exception=RuntimeError("502")))

    assert watch.observation is None, (
        "a continuation pass that raised was counted as an opportunity — the "
        "denominator now includes calls nobody got an answer from"
    )


def test_a_position_asked_twice_is_counted_once():
    """A hook that discards a SUCCESSFUL response and re-asks (the SDK's
    `AfterModelCallEvent.retry`) leaves the message list where it was. The
    request went twice; the CONVERSATION had one opportunity, and the rate this
    instrument reports is per opportunity."""
    agent = _Agent([])
    watch = _watch()
    _first_call(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))

    _continuation(agent)
    for _ in range(3):
        watch._before_model(_Before(agent))
        watch._after_model(_After(agent))

    row = watch.observation
    assert row is not None
    assert (row.continuation_calls, row.strip_calls) == (1, 1)


def test_a_cancelled_pass_settles_nothing():
    """`event.cancel` set by an earlier hook means the request is never
    formatted, so there is nothing to strip. The watch registers AFTER
    `ReplyGuard` in `factory.create_agent` precisely so this field is already
    populated when it reads it."""
    agent = _Agent([])
    watch = _watch()
    _first_call(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))

    _continuation(agent)
    watch._before_model(_Before(agent, cancel="Reply already sent."))
    watch._after_model(_After(agent))

    assert watch.observation is None, (
        "a turn whose only continuation pass was cancelled has no opportunity"
    )


def test_the_first_call_is_never_an_opportunity():
    """Nothing precedes it, so nothing it carries can have come from the loop.
    A single-call turn produces no row at all — the denominator would be zero
    and there is nothing to divide."""
    agent = _Agent([])
    watch = _watch()
    _first_call(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))
    assert watch.observation is None


def test_an_unreadable_model_records_the_absence_rather_than_a_guess():
    """Provenance that cannot be read is stored as "", never as a plausible
    default. A row claiming `reasoning_effort = "low"` because nobody could
    check is worse than one admitting it does not know — it is the column an
    operator would use to decide whether a zero means «healthy» or
    «impossible»."""

    class _Opaque:
        model = object()

        def __init__(self) -> None:
            self.messages: list[dict] = []

    agent = _Opaque()
    watch = _watch()
    agent.messages[:] = [{"role": "user", "content": [{"text": "go"}]}]
    watch._before_model(_Before(agent))  # type: ignore[arg-type]
    watch._after_model(_After(agent))  # type: ignore[arg-type]
    agent.messages.extend([
        {"role": "assistant", "content": [REASONING, TOOL_USE]},
        {"role": "user", "content": [TOOL_RESULT]},
    ])
    watch._before_model(_Before(agent))  # type: ignore[arg-type]
    watch._after_model(_After(agent))  # type: ignore[arg-type]

    row = watch.observation
    assert row is not None
    assert (row.model, row.reasoning_effort) == ("", "")
    assert row.strip_calls == 1, "the count must survive the provenance being unreadable"


async def test_the_dreamer_path_records_the_adaptor_by_name():
    """«Which path» is one of the three questions the instrument exists to
    answer, and on the Dreamer the useful answer is the ADAPTOR — one run a day
    each, and the population most at risk."""
    agent = _Agent([])
    store = _Store()
    watch = _watch(store=store)
    _first_call(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))
    _continuation(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))
    await watch.flush()

    (row,) = store.rows
    assert row.path == "dreamer:culture"
    assert row.tag_id is None


# ---- the bound: a wedged store must not hold the slot ----------------------


class _WedgedStore:
    """A database that does not answer. NOT one that raises — an exception is
    the harmless case (`except Exception` catches it, the turn completes in
    milliseconds, `writes_total{failed}` moves). The hazard is silence, and no
    `except` can see it."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def record_observation(self, observation: Any) -> None:
        self.entered.set()
        await asyncio.Event().wait()          # forever


async def _watch_with_one_opportunity(store: Any, **kwargs: Any):
    watch = ReasoningStripWatch(
        path="tag", store=store, metrics=Metrics(), **kwargs,
    )
    agent = _Agent([])
    _first_call(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))
    _continuation(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))
    return watch


async def test_a_wedged_store_does_not_hold_the_conversation_slot():
    """THE BOUND ON `flush`.

    Every caller awaits `flush` with the conversation's slot held, and
    `release_and_dispatch` cannot run until it returns. `create_sql_engine`
    sets no `command_timeout` and nora sets no `statement_timeout`, so without
    the `wait_for` inside `flush` this await has NO bound at all — and the
    failure is a room that stops answering, not an exception anybody catches.

    The outer `wait_for` here is the assertion: remove the inner one and this
    test does not hang, it goes RED with `TimeoutError`. A test that could hang
    is a test that stops a mutation sweep rather than reporting on it.
    """
    store = _WedgedStore()
    watch = await _watch_with_one_opportunity(store, write_timeout_seconds=0.05)

    await asyncio.wait_for(watch.flush(), timeout=2.0)

    assert store.entered.is_set(), (
        "the store was never reached — this probe passed without exercising "
        "the bound it exists to check"
    )


async def test_a_wedged_store_is_counted_as_a_failed_write():
    """And the abandoned row is REPORTED, not merely dropped: a record that
    goes blind with no series moving is the defect to avoid."""
    metrics = Metrics()
    watch = ReasoningStripWatch(
        path="tag", store=_WedgedStore(), metrics=metrics,
        write_timeout_seconds=0.05,
    )
    agent = _Agent([])
    _first_call(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))
    _continuation(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))

    await asyncio.wait_for(watch.flush(), timeout=2.0)

    assert metrics.get("nora_reasoning_strip_writes_total", outcome="failed") == 1
    assert metrics.get("nora_reasoning_strip_writes_total", outcome="ok") == 0


# ---- R-27: unavailable must not overwrite available ------------------------


class _FlakyModel:
    """Readable once, then not — a transient config read failure on a later
    call of the same turn."""

    def __init__(self) -> None:
        self.reads = 0

    def get_config(self) -> Any:
        self.reads += 1
        if self.reads == 1:
            return {"model_id": "nora", "params": {"reasoning_effort": "low"}}
        raise RuntimeError("config read failed")


async def test_a_later_unreadable_provenance_does_not_erase_an_earlier_read():
    """R-27's rule applied to provenance: `unavailable` never overwrites
    `available`.

    `_model_identity` answers `("", "")` for four distinct conditions and all
    four land in a NOT NULL TEXT column as the same empty string. Overwriting
    unconditionally would let one failed read on the LAST call of a turn silently
    turn a known model into an unknown one — on the two columns an operator
    uses to decide whether a clean row means «healthy» or «impossible»."""
    agent = _Agent([])
    agent.model = _FlakyModel()  # type: ignore[assignment]
    store = _Store()
    watch = ReasoningStripWatch(path="tag", store=store, metrics=Metrics())

    _first_call(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))
    _continuation(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))
    await watch.flush()

    (row,) = store.rows
    assert (row.model, row.reasoning_effort) == ("nora", "low"), (
        "a transient provenance read failure erased a good earlier read"
    )


# ---- the impossible row is refused under its own name ----------------------


async def test_an_impossible_row_is_refused_and_named_as_such():
    """`strip_calls > continuation_calls` cannot come from the loop — it would
    mean the reasoning arrived from the PREFILL. The table's CHECK refuses it,
    and letting it get that far would surface as an `IntegrityError` counted
    `failed`: the same signal a wedged or restarting database gives.

    So it is refused here, before the write, under `impossible`."""
    metrics = Metrics()
    store = _Store()
    watch = ReasoningStripWatch(path="tag", store=store, metrics=metrics)
    # Reach past the hooks: this state is unreachable through them, which is
    # the point — the guard exists for a writer that has drifted, and a test
    # that could only build it through the writer could not test the guard.
    watch._model_calls, watch._continuation_calls = 2, 1
    watch._strip_calls, watch._stripped_blocks = 2, 2

    await watch.flush()

    assert store.rows == [], "an arithmetically impossible row reached the store"
    assert metrics.get("nora_reasoning_strip_writes_total", outcome="impossible") == 1
    assert metrics.get("nora_reasoning_strip_writes_total", outcome="failed") == 0, (
        "an impossible row reported as `failed` is indistinguishable from a "
        "database blip, which is the one confusion this outcome exists to end"
    )


async def test_a_turn_with_no_opportunity_moves_no_write_counter():
    """A single-call turn writes no row AND reports no write. Counting it
    `failed` would put every such turn in the system on the one series that
    says the record has gone blind."""
    metrics = Metrics()
    watch = ReasoningStripWatch(path="tag", store=_Store(), metrics=metrics)
    agent = _Agent([])
    _first_call(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))

    await watch.flush()

    for outcome in ("ok", "failed", "impossible"):
        assert metrics.get(
            "nora_reasoning_strip_writes_total", outcome=outcome) == 0


@pytest.mark.parametrize("path", list(SPEAKING_AUDIENCES))
async def test_every_speaking_audience_is_a_path_this_watch_accepts(path: str):
    """`heartbeat` and `proactive` reach `path` through `deliver.build`'s
    `audience`, so `path` takes every declared audience value, not only
    «tag / participation / dreamer:<adaptor>». A dashboard filtered off a shorter
    list would drop the reminder path in silence.

    Parametrized over the DECLARED set rather than a list written here, so a
    fifth audience cannot arrive untested."""
    store = _Store()
    watch = ReasoningStripWatch(path=path, store=store, metrics=Metrics())
    agent = _Agent([])
    _first_call(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))
    _continuation(agent)
    watch._before_model(_Before(agent))
    watch._after_model(_After(agent))
    await watch.flush()

    (row,) = store.rows
    assert row.path == path
