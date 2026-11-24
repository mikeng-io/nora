"""`EventOnce` (nora/platform/bridge/answers.py): an event id reserved before
it applies, a repeat waiting on the first, and a bound that never drops one
still being applied."""

from __future__ import annotations

import asyncio

import pytest

from nora.platform.bridge.answers import EventOnce, EventWindowFull


def _gated(gate: asyncio.Event, calls: list[str], *, fail: bool = False):
    async def apply() -> str:
        calls.append("apply")
        await gate.wait()
        if fail:
            raise RuntimeError("apply failed")
        return "applied"

    return apply


async def test_a_repeat_in_flight_waits_for_the_first_and_applies_nothing():
    ids, gate, calls = EventOnce(bound=8), asyncio.Event(), []
    first = asyncio.ensure_future(ids.once("evt-1", _gated(gate, calls)))
    await asyncio.sleep(0)
    second = asyncio.ensure_future(ids.once("evt-1", _gated(gate, calls)))
    await asyncio.sleep(0)

    assert not second.done(), "a repeat was answered before the event applied"
    gate.set()
    assert await first == (True, "applied")
    assert await second == (False, None)
    assert calls == ["apply"]


async def test_a_failure_keeps_nothing_and_fails_its_waiter():
    ids, gate, calls = EventOnce(bound=8), asyncio.Event(), []
    first = asyncio.ensure_future(ids.once("evt-1", _gated(gate, calls, fail=True)))
    await asyncio.sleep(0)
    waiting = asyncio.ensure_future(ids.once("evt-1", _gated(gate, calls)))
    await asyncio.sleep(0)
    gate.set()

    for failed in (first, waiting):
        with pytest.raises(RuntimeError):
            await failed
    assert await ids.once("evt-1", _gated(gate, calls)) == (True, "applied")
    assert calls == ["apply", "apply"]


async def test_settled_ids_leave_oldest_first_at_the_bound():
    ids, gate, calls = EventOnce(bound=2), asyncio.Event(), []
    gate.set()
    for event_id in ("evt-1", "evt-2", "evt-1", "evt-3"):
        await ids.once(event_id, _gated(gate, calls))

    assert calls == ["apply"] * 3, "a repeat inside the bound applied again"
    assert (await ids.once("evt-3", _gated(gate, calls)))[0] is False
    assert (await ids.once("evt-1", _gated(gate, calls)))[0] is True


async def test_a_window_full_of_events_in_flight_refuses_before_applying():
    ids, gate, calls = EventOnce(bound=1), asyncio.Event(), []
    first = asyncio.ensure_future(ids.once("evt-1", _gated(gate, calls)))
    await asyncio.sleep(0)

    with pytest.raises(EventWindowFull):
        await ids.once("evt-2", _gated(gate, calls))
    gate.set()
    assert await first == (True, "applied")
    assert calls == ["apply"]


async def test_a_ttl_releases_a_settled_id():
    now = [0.0]
    ids, gate, calls = EventOnce(bound=8, ttl_s=300.0, clock=lambda: now[0]), asyncio.Event(), []
    gate.set()
    await ids.once("evt-1", _gated(gate, calls))
    now[0] = 299.0
    assert (await ids.once("evt-1", _gated(gate, calls)))[0] is False
    now[0] = 301.0
    assert (await ids.once("evt-1", _gated(gate, calls)))[0] is True
