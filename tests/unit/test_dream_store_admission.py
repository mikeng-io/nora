"""Admission queues work, never a model wait or foreground operation."""
import asyncio

import pytest

from nora.agent.dream_store import model_phase, store_phase, store_unit


async def test_store_admission_cancellation_failure_and_model_release():
    slots = asyncio.Semaphore(2)
    entered = asyncio.Queue()
    release = asyncio.Event()
    active = 0
    peak = 0

    async def background(index):
        nonlocal active, peak
        async with store_unit(slots):
            active += 1
            peak = max(peak, active)
            await entered.put(index)
            try:
                await release.wait()
            finally:
                active -= 1

    tasks = [asyncio.create_task(background(i)) for i in range(72)]
    try:
        assert {await entered.get(), await entered.get()} == {0, 1}
        # A runnable foreground task makes progress while all admissions are held.
        foreground = asyncio.create_task(asyncio.to_thread(lambda: 'ingested'))
        assert await asyncio.wait_for(foreground, 5) == 'ingested'
        assert entered.empty() and peak == 2
        tasks[2].cancel()  # queued cancellation
        tasks[0].cancel()  # active cancellation must let the next unit enter
        await asyncio.gather(tasks[0], tasks[2], return_exceptions=True)
        assert await entered.get() == 3
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        assert peak == 2 and active == 0
        async with store_unit(slots):
            async with model_phase():
                # Both slots are free during the model, and input/commit work
                # re-enters the same gate, before starting its own deadline.
                assert slots._value == 2
                async with store_phase():
                    assert slots._value == 1
                assert slots._value == 2
            assert slots._value == 1
        with pytest.raises(ValueError):
            async with store_unit(slots):
                raise ValueError('generated failure')
        assert slots._value == 2
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_admission_wait_does_not_consume_the_unit_deadline():
    attempted = asyncio.Queue()

    class ObservedSemaphore(asyncio.Semaphore):
        async def acquire(self):
            attempted.put_nowait(self._value)
            return await super().acquire()

    slots = ObservedSemaphore(1)
    async with store_unit(slots) as lease, asyncio.timeout(5) as deadline:
        lease.deadline = deadline
        assert await attempted.get() == 1
        async with model_phase():
            # Another unit occupies the released slot before this unit's store
            # phase arrives. The observer signals the actual acquire boundary.
            await slots.acquire()
            assert await attempted.get() == 1

            async def input_phase():
                async with store_phase():
                    return 'read'

            task = asyncio.create_task(input_phase())
            assert await attempted.get() == 0
            assert deadline.when() is None
            slots.release()
            assert await task == 'read'
            assert deadline.when() is not None
    assert slots._value == 1
