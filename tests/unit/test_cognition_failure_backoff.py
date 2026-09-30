"""A failing Cognition session is not woken again every cooldown, for ever
(#637 review, major 1: 283 failed sessions a day measured, one per
`cooldown_seconds`; 20 §3: «per-unit backoff after a failed session»).

The REAL evaluator over the REAL session runner and the in-memory store the SQL
store's contract suite holds to, one fake clock ticked every 30 s for whole
days, and only the model scripted. The numbers asserted here are the measured
ones: the same run with the backoff and the strike switched off is 288
sessions a day, which is the reviewer's number.

What each test would catch if the fix were reverted is named in
`scripts/mutations/cognition-step3a3-review.json`.
"""

from __future__ import annotations

import dataclasses
import itertools

from nora.domain.stimuli import (
    LastSession,
    WakePolicy,
    Watermark,
    backoff_ms,
    failure_run,
    failure_streak,
)
from tests.unit.test_cognition_session_lifecycle import (
    POLICY,
    Rig,
    S,
    ScriptedAgent,
    _rig,
    boom,
    ok,
)

TICK = 30
DAY_TICKS = 86_400 // TICK


async def _run(rig: Rig, *, ticks: int, chatty: bool) -> None:
    """`ticks` evaluator ticks, 30 s apart. `chatty`: a new message every 60 s,
    so there is always something pending (the worst case for a retry loop)."""
    for i in range(ticks):
        rig.clock.advance(TICK)
        if chatty and i % 2 == 0:
            await rig.say()
        await rig.tick()
        await rig.settle()


def _unguarded() -> WakePolicy:
    """The behaviour before the fix: the cooldown and nothing else."""
    return dataclasses.replace(
        POLICY, backoff_max_seconds=POLICY.cooldown_seconds, max_consecutive_failures=10**9,
    )


# ---- the arithmetic ----------------------------------------------------------

def test_the_backoff_starts_at_the_cooldown_doubles_and_is_capped():
    p = POLICY                                     # cooldown 300 s, cap 21600 s
    assert [backoff_ms(n, p) // S for n in range(0, 10)] == [
        300, 300, 600, 1200, 2400, 4800, 9600, 19200, 21600, 21600,
    ]
    assert backoff_ms(10**6, p) // S == 21600      # no overflow at absurd counts


def _st(state: str, outcome: str | None = None) -> LastSession:
    return LastSession(state=state, started_at_ms=0, ended_at_ms=1, outcome=outcome)  # type: ignore[arg-type]


def test_the_streak_counts_failed_and_timed_out_and_only_a_closed_wake_resets_it():
    failed, timeout = _st("failed", "failed"), _st("failed", "timeout")
    closed = _st("closed", "closed")
    cancelled, running = _st("failed", "cancelled"), _st("running")
    assert failure_streak([failed, timeout, failed, closed, failed]) == 3
    assert failure_streak([closed, failed, failed]) == 0
    # a shutdown is not a failure, and neither ends nor extends the run
    assert failure_streak([failed, cancelled, failed]) == 2
    # a wake still open has not ended
    assert failure_streak([running, failed, failed]) == 2
    # an untracked wake (no state) ends it
    assert failure_streak([failed, None, failed]) == 1
    assert failure_streak([]) == 0


def test_the_failed_mark_is_the_newest_failed_wakes_watermark():
    from nora.domain.stimuli import WakeRow

    def wake(i: int, through: int, at: int) -> WakeRow:
        return WakeRow(
            id=i, unit="u", mode="shadow", reason="settled", from_stimulus_id=0,
            through_stimulus_id=through, stimulus_count=1, eligible_count=1,
            oldest_pending_ms=None, cut_cooldown=False, at_ms=at, recorded_at_ms=at,
        )

    streak, mark = failure_run([
        (wake(3, 30, 3000), _st("failed", "failed")),
        (wake(2, 20, 2000), _st("failed", "timeout")),
        (wake(1, 10, 1000), _st("closed", "closed")),
    ])
    assert streak == 2 and mark == Watermark(30, 3000)
    assert failure_run([(wake(1, 10, 1000), _st("closed", "closed"))]) == (0, None)


# ---- the measured day ------------------------------------------------------------

async def test_an_always_failing_agenda_without_the_fix_is_288_sessions_a_day():
    """The positive control: the measurement means something only if the same
    run WITHOUT the backoff reproduces the reviewer's ~283."""
    rig = _rig(agent=ScriptedAgent(script=[boom]), policy=_unguarded())
    for _ in range(3):
        await rig.say()
    await _run(rig, ticks=DAY_TICKS, chatty=False)
    assert rig.agent.calls == 288


async def test_an_always_failing_static_agenda_stops_after_five_sessions():
    """Three messages that always fail: 5 sessions (300 + 600 + 1200 + 2400 s
    apart), then the three stimuli are struck with reason `failed` and nothing
    more wakes for them, that day or after."""
    rig = _rig(agent=ScriptedAgent(script=[boom]))
    for _ in range(3):
        await rig.say()
    await _run(rig, ticks=DAY_TICKS, chatty=False)
    assert rig.agent.calls == 5
    assert rig.ends() == ["failed"] * 5
    assert rig.stimuli.retracted == {1, 2, 3}
    assert rig.metrics.get("nora_cognition_stimuli_struck_total", reason="failed") == 3
    await _run(rig, ticks=DAY_TICKS, chatty=False)               # a second day
    assert rig.agent.calls == 5


async def test_an_always_failing_chatty_room_is_10_sessions_on_day_one_and_4_a_day_after():
    """The worst case: something new is always pending. The first day is the
    backoff climbing (300, 600, 1200, 2400, 4800, 9600 s, then 6 h), and every
    later day is the 6 h cap: 4 sessions."""
    rig = _rig(agent=ScriptedAgent(script=[boom]))
    await _run(rig, ticks=DAY_TICKS, chatty=True)
    day_one = rig.agent.calls
    assert day_one == 10
    await _run(rig, ticks=DAY_TICKS, chatty=True)
    await _run(rig, ticks=DAY_TICKS, chatty=True)
    assert rig.agent.calls - day_one == 8                        # days two and three
    # Failing stimuli are struck once the streak reaches the limit, every time.
    assert rig.metrics.get("nora_cognition_stimuli_struck_total", reason="failed") > 0


async def test_a_stimulus_that_arrived_after_the_failed_wakes_is_not_struck_unseen():
    rig = _rig(agent=ScriptedAgent(script=[boom]))
    await rig.say()
    await _run(rig, ticks=int(4500 / TICK) + 2, chatty=False)    # five failures in
    assert rig.agent.calls == 5
    late = await rig.say()
    rig.clock.advance(TICK)
    await rig.tick()
    assert late not in rig.stimuli.retracted
    view = await rig.stimuli.view(unit="u1", mode="shadow", now_ms=rig.clock.ms, limit=100)
    assert [s.id for s in view.pending] == [late]


async def test_a_closed_session_resets_the_backoff_and_the_strike_count():
    """Three failures, one success, then failing again: the wait after the
    success is the plain cooldown, not the 2400 s the streak would have owed,
    and the failure after it starts a NEW streak of one."""
    rig = _rig(agent=ScriptedAgent(script=[boom, boom, boom, ok, boom, boom]))
    while rig.agent.calls < 5:
        await _run(rig, ticks=1, chatty=True)
    starts = [w.at_ms for w in rig.stimuli.wakes]
    gaps = [(b - a) // S for a, b in itertools.pairwise(starts)]
    assert rig.ends() == ["failed", "failed", "failed", "closed", "failed"]
    # 1st->2nd waits the cooldown, 2nd->3rd doubles, 3rd->4th doubles again
    assert 300 <= gaps[0] < 600 and 600 <= gaps[1] < 900 and 1200 <= gaps[2] < 1500, gaps
    # the 4th closed: the 5th waits only the cooldown
    assert 300 <= gaps[3] < 600, gaps
    view = await rig.stimuli.view(unit="u1", mode="shadow", now_ms=rig.clock.ms, limit=100)
    assert view.failures == 1                       # a new streak, not 4
    while rig.agent.calls < 7:                      # the streak climbs again from one
        await _run(rig, ticks=1, chatty=True)
    starts = [w.at_ms for w in rig.stimuli.wakes]
    assert 300 <= (starts[5] - starts[4]) // S < 600
    assert 600 <= (starts[6] - starts[5]) // S < 900


async def test_a_strong_cue_does_not_cut_a_backoff():
    """A room that keeps saying a time must not wake a failing unit every
    `cue_quiet_seconds`: a strong cue cuts a cooldown once, never a backoff."""
    rig = _rig(agent=ScriptedAgent(script=[boom]))
    await rig.say(cues=("datetime",))
    for _ in range(3):                                            # three failures
        rig.clock.advance(TICK)
        await rig.tick()
        await rig.settle()
        rig.clock.advance(700)
        await rig.say(cues=("datetime",))
        await rig.tick()
        await rig.settle()
    calls = rig.agent.calls
    # 100 s after the last failure, inside its backoff, with a strong cue pending
    rig.clock.advance(100)
    await rig.say(cues=("datetime",))
    assert await rig.tick() == []
    assert rig.agent.calls == calls
