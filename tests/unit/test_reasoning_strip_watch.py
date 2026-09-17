"""`ReasoningStripWatch`'s accounting, at the event level (issue #325).

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

from typing import Any

from nora.agent.reasoning_strip import ReasoningStripWatch
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
    each, and after 2026-09-17 the population most at risk."""
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
