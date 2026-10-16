"""A unit that cannot run a session is still watched.

A unit with no home room stays in the wake evaluator, so its would-wakes (the
shadow evidence for it) are not lost: the evaluator keeps recording them exactly
as it does with sessions off (untracked, the shadow assumption, no hook), and only the units
that CAN run a session start one.

What each test would catch if the fix were reverted is named in
`scripts/mutations/cognition-step3a3-review.json`.
"""

from __future__ import annotations

from typing import Any

from nora.app.wake import WakeEvaluator
from nora.domain.stimuli import NewStimulus, UnitView, WakePolicy
from nora.obs.metrics import Metrics
from nora.testing.stimuli import InMemoryStimulusStore

T = 1_790_000_000_000
POLICY = WakePolicy(quiet_seconds=0, cue_quiet_seconds=0, batch_max=1000)


class _Clock:
    ms = T

    def __call__(self) -> float:
        return self.ms / 1000


async def _say(store: InMemoryStimulusStore, unit: str, ms: int) -> None:
    await store.append(stimulus=NewStimulus(
        unit=unit, kind="message", item="message", ref=f"messages:{unit}:{ms}", at_ms=ms,
        platform="signal", conversation_id=f"room-{unit}",
    ), recorded_at_ms=ms)


async def test_a_unit_outside_session_units_is_still_recorded_and_never_hooked():
    store = InMemoryStimulusStore()
    hooked: list[str] = []

    async def hook(*, unit: str, view: UnitView, wake_id: int, now_ms: int) -> Any:
        hooked.append(unit)

    clock = _Clock()
    ev = WakeEvaluator(
        store=store, units=lambda: ("with-home", "no-home"), policy=POLICY, metrics=Metrics(),
        clock=clock, settle_seconds=0, on_wake=hook,
        session_units=lambda: {"with-home"},
    )
    await _say(store, "with-home", T)
    await _say(store, "no-home", T)
    clock.ms += 1000
    wake_ids = await ev.tick()
    await ev.drain()

    assert len(wake_ids) == 2                        # BOTH are recorded
    by_unit = {w.unit: w for w in store.wakes}
    assert by_unit["with-home"].tracked is True      # a session was started for it
    assert by_unit["no-home"].tracked is False       # a would-wake, exactly as with sessions off
    assert hooked == ["with-home"]                   # and the hook never ran for the other


async def test_an_untracked_would_wake_holds_its_unit_for_the_shadow_assumption_only():
    """No session runs for it, so its brakes are the step-1 ones: in flight for
    `session_seconds`, then the cooldown; and it never awaits an outcome."""
    store = InMemoryStimulusStore()
    clock = _Clock()
    ev = WakeEvaluator(
        store=store, units=lambda: ("no-home",), policy=POLICY, metrics=Metrics(),
        clock=clock, settle_seconds=0, on_wake=lambda **kw: None,
        session_units=lambda: set(),
    )
    await _say(store, "no-home", T)
    clock.ms += 1000
    assert len(await ev.tick()) == 1
    assert store.outcomes == {}
    await _say(store, "no-home", clock.ms)
    clock.ms += 30_000
    assert await ev.tick() == []                  # inside the 60 s a would-wake is assumed to run
    clock.ms += 600_000
    assert len(await ev.tick()) == 1              # and the next one is recorded, not stuck open
