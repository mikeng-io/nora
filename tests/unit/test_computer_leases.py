"""`ComputerLeases`: a live caller waits its
bound; a background caller never waits; a released key is dropped."""

from __future__ import annotations

import asyncio

from nora.domain.computer_leases import ComputerLeases


async def _try(leases: ComputerLeases, key: str):
    """`try_acquire` under a bound, so a broken «never waits» fails the test
    instead of hanging it."""
    return await asyncio.wait_for(leases.try_acquire(key), 1)


async def test_a_background_caller_defers_while_the_key_is_held():
    leases = ComputerLeases(live_wait_seconds=5)
    held = await leases.acquire("session:a", live=True)
    assert held is not None
    assert await _try(leases, "session:a") is None
    assert await leases.try_acquire("session:b") is not None
    leases.release(held)
    assert await leases.try_acquire("session:a") is not None


async def test_a_background_caller_defers_while_a_live_one_waits():
    leases = ComputerLeases(live_wait_seconds=5)
    held = await leases.acquire("k", live=True)
    waiter = asyncio.create_task(leases.acquire("k", live=True))
    await asyncio.sleep(0)
    leases.release(held)
    # The key is free for an instant, but a live caller is queued for it.
    assert await _try(leases, "k") is None
    got = await waiter
    assert got is not None
    leases.release(got)


async def test_a_live_caller_waits_then_is_refused():
    leases = ComputerLeases(live_wait_seconds=0.05)
    held = await leases.acquire("k", live=True)
    assert await leases.acquire("k", live=True) is None
    leases.release(held)
    again = await leases.acquire("k", live=True)
    assert again is not None and again.waited_ms < 50


async def test_a_live_caller_gets_the_key_when_it_frees_within_the_bound():
    leases = ComputerLeases(live_wait_seconds=2)
    held = await leases.acquire("k", live=True)
    waiter = asyncio.create_task(leases.acquire("k", live=True))
    await asyncio.sleep(0.02)
    leases.release(held)
    assert (await waiter) is not None


async def test_a_released_key_leaves_nothing_behind():
    leases = ComputerLeases()
    lease = await leases.acquire("k", live=False)
    assert lease is not None
    leases.release(lease)
    assert not leases.busy("k") and leases._locks == {}
