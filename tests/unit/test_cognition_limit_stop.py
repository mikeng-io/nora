"""A session whose loop stops at a bound is a FAILED session, never a closed one
(DEC-247, DEC-244).

Strands RETURNS normally when a loop hits `limit_turns`, `limit_total_tokens`
or `limit_output_tokens`, so «it returned» is not «it finished». Closing such a
session struck every item the model had not reached and reset the unit's
backoff: a human's message dropped for the model's runaway. Driven through the
real evaluator and the real session runner, the model scripted.

What each test would catch if the change were reverted is named in
`scripts/mutations/cognition-rounds.json`.
"""

from __future__ import annotations

from typing import Any

import pytest

from nora.app.cognition_session import CognitionRunResult
from nora.domain.stimuli import LIMIT_STOP_REASONS
from tests.unit.test_cognition_session_lifecycle import (
    Rig,
    ScriptedAgent,
    _rig,
    close_written,
)


def _stops_at(reason: str):
    async def behaviour(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        return CognitionRunResult(stop_reason=reason)
    return behaviour


def _causes(rig: Rig) -> list[str]:
    return [o.cause for _, o in sorted(rig.stimuli.outcomes.items())]


async def _wake(rig: Rig, *, advance: float = 1) -> None:
    rig.clock.advance(advance)
    await rig.tick()
    await rig.settle()


def test_the_three_limit_reasons_are_the_three_strands_names():
    assert set(LIMIT_STOP_REASONS) == {
        "limit_turns", "limit_total_tokens", "limit_output_tokens",
    }


@pytest.mark.parametrize("reason", LIMIT_STOP_REASONS)
async def test_a_session_that_stops_at_a_limit_is_failed_infra_and_strikes_nothing(reason):
    rig = _rig(agent=ScriptedAgent(script=[_stops_at(reason)]))
    for _ in range(3):
        await rig.say()
    await _wake(rig)

    assert rig.agent.calls == 1
    assert rig.ends() == ["failed"]
    assert _causes(rig) == ["infra"]
    assert reason in rig.detail()
    assert rig.ledger.closes == []          # nothing closed, so nothing struck
    assert rig.carried() == []              # no carry row of any state
    view = await rig.stimuli.view(unit="u1", mode="shadow", now_ms=rig.clock.ms, limit=100)
    assert [s.id for s in view.pending] == [1, 2, 3]
    assert dict(view.carry) == {}
    assert view.mark is not None and view.mark.through_stimulus_id == 0
    assert rig.metrics.get("nora_cognition_session_limit_total", limit=reason) == 1
    assert rig.metrics.get("nora_cognition_session_ended_total", outcome="failed") == 1
    assert rig.metrics.get("nora_cognition_session_ended_total", outcome="closed") == 0


@pytest.mark.parametrize("reason", LIMIT_STOP_REASONS)
async def test_a_limit_stop_backs_the_unit_off_and_repeated_ones_grow_it(reason):
    rig = _rig(agent=ScriptedAgent(script=[_stops_at(reason)]))
    await rig.say()
    await _wake(rig)
    view = await rig.stimuli.view(unit="u1", mode="shadow", now_ms=rig.clock.ms, limit=100)
    assert view.failures == 1

    # Inside the first backoff (the cooldown, 300 s) nothing wakes.
    rig.clock.advance(100)
    await rig.say(cues=("datetime",))
    assert await rig.tick() == []
    assert rig.agent.calls == 1

    # Past it, the same stimuli are the next agenda, and it stops again: the
    # streak is 2 and the wait doubles (600 s), so 400 s after is still inside.
    await _wake(rig, advance=250)
    assert rig.agent.calls == 2
    view = await rig.stimuli.view(unit="u1", mode="shadow", now_ms=rig.clock.ms, limit=100)
    assert view.failures == 2
    rig.clock.advance(400)
    await rig.say(cues=("datetime",))
    assert await rig.tick() == []
    assert rig.agent.calls == 2
    assert _causes(rig) == ["infra", "infra"]
    assert rig.ledger.closes == []


async def test_a_session_that_ends_normally_still_closes_and_resets_the_streak():
    rig = _rig(agent=ScriptedAgent(script=[_stops_at("limit_turns"), close_written]))
    await rig.say()
    await _wake(rig)
    assert rig.ends() == ["failed"]
    await _wake(rig, advance=400)
    assert rig.ends() == ["failed", "closed"]
    assert rig.metrics.get("nora_cognition_session_limit_total", limit="limit_turns") == 1
    view = await rig.stimuli.view(unit="u1", mode="shadow", now_ms=rig.clock.ms, limit=100)
    assert list(view.pending) == []
    assert view.failures == 0


async def test_an_unclosed_item_of_a_normal_end_is_still_struck_as_before():
    """The control: the SAME unclosed agenda, ended with `end_turn`, closes
    `waiting` with a strike. The limit stop is what changed it."""
    rig = _rig(agent=ScriptedAgent(script=[_stops_at("end_turn")]))
    await rig.say()
    await _wake(rig)
    assert rig.ends() == ["closed"]
    assert [(c.reason, c.strike) for _, c, _ in rig.ledger.closes] == [("waiting", 1)]


async def test_a_rerun_after_a_limit_stop_duplicates_neither_a_self_wake_nor_a_reminder():
    """What the stopped session ALREADY wrote stays as recorded (the wake and the
    reminder rows, the self stimulus). The next session reads them back as
    pending: its `wake_at` on the same subject row SUPERSEDES the earlier
    self-wake (one live, the old one retracted), and its identical `remind` is
    refused `duplicate_pending`, so neither is duplicated."""
    holder: list[Rig] = []
    answers: list[dict[str, Any]] = []

    async def write_then_stop(agent: ScriptedAgent, tools: list[Any]) -> CognitionRunResult:
        by = {t.__name__: t for t in tools if hasattr(t, "__name__")}
        rig = holder[0]
        answers.append(await by["wake_at"](
            subject_rows=["event:1"], at_ms=rig.clock.ms + 3_600_000, why="venue",
        ))
        answers.append(await by["remind"](
            room_platform="signal", room_conversation_id="room-a", intent="tell",
            subject_rows=["event:1"], at_ms=rig.clock.ms + 7_200_000,
        ))
        return CognitionRunResult(stop_reason="limit_turns")

    rig = _rig(
        agent=ScriptedAgent(script=[write_then_stop]), real_pending_reminds=True,
    )
    holder.append(rig)
    await rig.say()
    await _wake(rig)
    assert [a["status"] for a in answers] == ["ok", "ok"]
    assert rig.ends() == ["failed"]
    # the writes stay: one self-wake pending, one mirrored reminder pending
    assert len(await rig.ledger.pending_self_wakes(unit="u1", since_ms=0)) == 1
    assert len(await rig.ledger.pending_mirrored_reminds(unit="u1", now_ms=rig.clock.ms)) == 1

    await _wake(rig, advance=400)
    assert rig.agent.calls == 2
    assert [a["status"] for a in answers[2:]] == ["ok", "refused"]
    assert answers[3]["reason"] == "duplicate_pending"
    pending = await rig.ledger.pending_self_wakes(unit="u1", since_ms=0)
    assert len(pending) == 1                     # the replacement, not a second
    assert pending[0].wake_ref == answers[2]["wake_id"]
    assert len(await rig.ledger.pending_mirrored_reminds(unit="u1", now_ms=rig.clock.ms)) == 1
    # the earlier self stimulus is retracted: only one can ever fire
    live = [
        s for s in rig.stimuli.stimuli
        if s.kind == "self" and s.id not in rig.stimuli.retracted
    ]
    assert len(live) == 1
