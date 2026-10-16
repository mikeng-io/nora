"""What a session's verbs do when a store fails part-way, and when the model's tool
calls run at the same time .

**Part-way failures.** The pace verbs are not ledger-only: `wake_at`, `wake_move` and
`wake_cancel` change a real `self` stimulus as well as the ledger. A failure after the
stimulus changed must not be answered «nothing was written» (it was), and must not
invite a retry that stands a second wake beside the first. Each test injects a failure
at one step of one verb and asserts what the model reads is true, what stands is what
the ledger and the stimulus store say, and a retry does not duplicate.

A session's tier writes go to the tiers' own stores, whose compare-and-swap is
what keeps two concurrent calls from both landing; those stores' contract suites
hold it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from nora.app.cognition_session import SESSION_TOKENS, TokenSpend
from nora.obs.metrics import Metrics
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.stimuli import InMemoryStimulusStore
from tests.unit.cognition_rig import NOW, Stores, iso
from tests.unit.cognition_rig import session as _session

AT = iso(NOW + 3_600_000)
LATER = iso(NOW + 7_200_000)
HEAD = "rev-2"


class _Ledger(InMemoryCognitionStore):
    """A ledger that can fail a method a set number of times, and that yields on
    every write the way a database does."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_times: dict[str, int] = {}

    def _check(self, method: str) -> None:
        if self.fail_times.get(method, 0) > 0:
            self.fail_times[method] -= 1
            raise RuntimeError(f"ledger unavailable ({method}): postgresql://nora:hunter2@db")
        super()._check(method)

    async def record_pace(self, **kw: Any) -> int:
        self._check("record_pace")
        return await super().record_pace(**kw)


class _Counting(Metrics):
    """A `Metrics` whose counter raises, as one does on an undeclared name."""

    def __init__(self, *, broken: bool = False) -> None:
        super().__init__()
        self.broken = broken

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None:
        if self.broken:
            raise KeyError(name)
        super().inc(name, value, **labels)


async def _run(behave, *, ledger=None, metrics=None, **kw):
    ledger = ledger or _Ledger()
    stimuli = InMemoryStimulusStore(ledger=ledger)
    stimuli.seed_wake("u", 1)

    async def pending(unit: str, since_ms: int) -> Any:
        return await ledger.pending_self_wakes(unit=unit, since_ms=since_ms)

    await _session(
        behave, stores=Stores(cognition=ledger), metrics=metrics, runner_kwargs={
            "stimulus_store": stimuli,
            **kw.pop("runner", {}),
        }, **kw,
    )
    return ledger, stimuli


async def _live(stimuli: InMemoryStimulusStore) -> list[tuple[str, int | None]]:
    view = await stimuli.view(unit="u", mode="live", now_ms=NOW + 99_000_000, limit=50)
    return [(s.item, s.due_ms) for s in view.pending if s.kind == "self"]


def _true_text(answer: dict[str, Any]) -> None:
    """The text is one a model can act on truthfully after a PART-way failure."""
    assert answer["status"] == "unavailable"
    text = json.dumps(answer)
    assert "nothing was written" not in text and "hunter2" not in text
    assert "may or may not have taken effect" in answer["detail"]


# ---------------------------------------------------------------------------
# wake_at
# ---------------------------------------------------------------------------

async def test_a_wake_at_whose_ledger_write_fails_leaves_none_standing_and_a_retry_makes_one():
    answers: list[Any] = []
    ledger = _Ledger()
    ledger.fail_times["record_pace"] = 1

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["wake_at"](subject_rows=["event:1"], at=AT, why="look"))
        answers.append(await by["wake_at"](subject_rows=["event:1"], at=AT, why="look again"))

    _, stimuli = await _run(behave, ledger=ledger)
    _true_text(answers[0])
    assert answers[1]["status"] == "ok"
    assert len(await _live(stimuli)) == 1                        # the failed one was taken back


async def test_a_superseding_wake_at_whose_retract_fails_is_still_ok_and_one_row_not_two_attempts():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        answers.append(await by["wake_at"](subject_rows=["event:1"], at=AT, why="look"))
        stimuli_box[0].fail_on.add("retract_self")
        answers.append(await by["wake_at"](subject_rows=["event:1"], at=LATER, why="move it"))

    stimuli_box: list[InMemoryStimulusStore] = []
    ledger = _Ledger()
    stimuli = InMemoryStimulusStore(ledger=ledger)
    stimuli.seed_wake("u", 1)
    stimuli_box.append(stimuli)

    async def pending(unit: str, since_ms: int) -> Any:
        return await ledger.pending_self_wakes(unit=unit, since_ms=since_ms)

    await _session(behave, stores=Stores(cognition=ledger), runner_kwargs={
        "stimulus_store": stimuli,
    })
    # The NEW wake exists (ledger row and stimulus), so the answer is ok: an error here
    # would have the model set it again. The displaced wake could not be retracted, which
    # is logged, and is the only thing left standing beside it.
    assert [a["status"] for a in answers] == ["ok", "ok"]
    rows = [p.transition for _, p, _ in ledger.paces if p.outcome == "recorded"]
    assert rows == ["created", "created"]


# ---------------------------------------------------------------------------
# wake_move and wake_cancel
# ---------------------------------------------------------------------------

async def _with_a_wake(then, *, ledger=None, **kw):
    made: list[str] = []
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        first = await by["wake_at"](subject_rows=["event:1"], at=AT, why="look")
        made.append(first["wake_id"])
        await then(by, made[0], answers, ledger)

    ledger, stimuli = await _run(behave, ledger=ledger, **kw)
    return answers, ledger, stimuli


async def test_a_wake_move_whose_ledger_write_fails_twice_says_so_and_a_retry_does_not_duplicate():
    ledger = _Ledger()

    async def then(by, wake_id, answers, ledger):
        ledger.fail_times["record_pace"] = 2        # the module tries a second time
        answers.append(await by["wake_move"](wake_id=wake_id, at=LATER, why="later"))
        ledger.fail_times.clear()
        answers.append(await by["wake_move"](wake_id=wake_id, at=LATER, why="later"))

    answers, _, stimuli = await _with_a_wake(then, ledger=ledger)
    _true_text(answers[0])
    # the move TOOK EFFECT (one wake, at the new time), so the retry finds nothing pending
    # and is refused, and nothing is duplicated
    assert answers[1]["status"] == "refused" and answers[1]["reason"] == "not_found"
    assert await _live(stimuli) == [(await _wake_ref(stimuli), NOW + 7_200_000)]


async def _wake_ref(stimuli: InMemoryStimulusStore) -> str:
    return (await _live(stimuli))[0][0]


async def test_a_wake_move_whose_ledger_write_fails_once_is_recorded_by_the_second_try():
    ledger = _Ledger()

    async def then(by, wake_id, answers, ledger):
        ledger.fail_times["record_pace"] = 1
        answers.append(await by["wake_move"](wake_id=wake_id, at=LATER, why="later"))

    answers, ledger, stimuli = await _with_a_wake(then, ledger=ledger)
    assert answers[0]["status"] == "ok"
    assert [p.transition for _, p, _ in ledger.paces if p.outcome == "recorded"] == [
        "created", "moved",
    ]
    assert len(await _live(stimuli)) == 1


async def test_a_wake_cancel_whose_ledger_write_fails_once_is_recorded_by_the_second_try():
    ledger = _Ledger()

    async def then(by, wake_id, answers, ledger):
        ledger.fail_times["record_pace"] = 1
        answers.append(await by["wake_cancel"](wake_id=wake_id, why="done"))

    answers, ledger, stimuli = await _with_a_wake(then, ledger=ledger)
    assert answers[0]["status"] == "ok"
    assert [p.transition for _, p, _ in ledger.paces if p.outcome == "recorded"] == [
        "created", "cancelled",
    ]
    assert await _live(stimuli) == []


async def test_a_wake_cancel_whose_ledger_write_fails_twice_says_so_and_a_retry_does_not_repeat():
    ledger = _Ledger()

    async def then(by, wake_id, answers, ledger):
        ledger.fail_times["record_pace"] = 2
        answers.append(await by["wake_cancel"](wake_id=wake_id, why="done"))
        ledger.fail_times.clear()
        answers.append(await by["wake_cancel"](wake_id=wake_id, why="done"))

    answers, _, stimuli = await _with_a_wake(then, ledger=ledger)
    _true_text(answers[0])
    assert answers[1]["reason"] == "not_found"              # it had taken effect
    assert await _live(stimuli) == []                       # and nothing fires


async def test_a_pace_verb_that_fails_before_any_write_still_says_it_may_not_have_taken_effect():
    answers: list[Any] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        stimuli_box[0].fail_on.add("append")
        answers.append(await by["wake_at"](subject_rows=["event:1"], at=AT, why="look"))

    stimuli_box: list[InMemoryStimulusStore] = []
    ledger = _Ledger()
    stimuli = InMemoryStimulusStore(ledger=ledger)
    stimuli.seed_wake("u", 1)
    stimuli_box.append(stimuli)
    await _session(
        behave, stores=Stores(cognition=ledger), runner_kwargs={"stimulus_store": stimuli},
    )
    _true_text(answers[0])
    assert await _live(stimuli) == []






# ---------------------------------------------------------------------------
# the overlay under tool calls that run together
# ---------------------------------------------------------------------------

def _statuses(answers: list[Any]) -> list[str]:
    return sorted(a.get("reason") or a["status"] for a in answers)














# ---------------------------------------------------------------------------
# the overlay covers Standing and event_propose, one after the other
# ---------------------------------------------------------------------------









# ---------------------------------------------------------------------------
# the token holder is never left set
# ---------------------------------------------------------------------------

async def test_the_token_holder_is_unset_after_a_session_that_returned():
    seen: list[TokenSpend | None] = []

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        seen.append(SESSION_TOKENS.get())

    await _session(behave)
    assert seen[0] is not None and SESSION_TOKENS.get() is None


async def test_the_token_holder_is_unset_after_a_session_that_failed():
    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        await _session(behave)
    assert SESSION_TOKENS.get() is None


async def test_the_token_holder_is_unset_after_a_session_that_timed_out():
    from nora.app.cognition_session import SessionTimeoutError

    async def behave(by: dict[str, Any], tools: list[Any]) -> None:
        await asyncio.sleep(30)

    with pytest.raises(SessionTimeoutError):
        await _session(behave, runner_kwargs={"budget": _short_budget()})
    assert SESSION_TOKENS.get() is None


def _short_budget() -> Any:
    from nora.app.cognition_session import AdaptorBudget
    return AdaptorBudget(turns=4, total_tokens=20_000, output_tokens=2_000, timeout_seconds=0.05)
