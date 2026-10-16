import asyncio
import time
from collections.abc import Callable

import pytest

from nora.domain.models import ConversationTarget
from nora.tag import Debounce


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


A = ConversationTarget("signal", "gA", "group")
B = ConversationTarget("signal", "gB", "group")


def test_first_tag_is_allowed():
    assert Debounce(5, clock=FakeClock()).allow(A) is True


def test_second_tag_within_the_window_is_blocked():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    assert d.allow(A) is True
    clock.advance(1)
    assert d.allow(A) is False


def test_tag_after_the_window_is_allowed():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    assert d.allow(A) is True
    clock.advance(5.001)
    assert d.allow(A) is True


def test_the_window_is_independent_per_conversation():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    assert d.allow(A) is True
    assert d.allow(B) is True
    clock.advance(1)
    assert d.allow(A) is False
    assert d.allow(B) is False


def test_a_blocked_tag_does_not_extend_the_window():
    """Otherwise a burst of tags would starve the conversation indefinitely."""
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    d.allow(A)
    clock.advance(4)
    assert d.allow(A) is False
    clock.advance(1.001)
    assert d.allow(A) is True


def test_zero_interval_allows_everything():
    clock = FakeClock()
    d = Debounce(0, clock=clock)
    assert d.allow(A) is True
    assert d.allow(A) is True


def test_a_platform_conversation_tuple_works_as_a_key():
    d = Debounce(5, clock=FakeClock())
    assert d.allow(("signal", "gA")) is True
    assert d.allow(("signal", "gA")) is False


def test_sweep_drops_entries_older_than_the_ttl():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    d.allow(A)
    clock.advance(100)
    dropped = d.sweep(10)
    assert dropped == 1
    assert len(d._last) == 0


def test_sweep_keeps_entries_within_the_ttl():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    d.allow(A)
    clock.advance(1)
    dropped = d.sweep(10)
    assert dropped == 0
    assert len(d._last) == 1


def test_sweep_only_drops_the_stale_key_not_the_fresh_one():
    clock = FakeClock()
    d = Debounce(5, clock=clock)
    d.allow(A)
    clock.advance(100)
    d.allow(B)
    dropped = d.sweep(10)
    assert dropped == 1
    assert list(d._last) == [B]


def test_sweep_returns_zero_on_an_empty_debounce():
    assert Debounce(5, clock=FakeClock()).sweep(10) == 0


# --- surviving a restart (migration 0056) ----------------------------------


class FakeStore:
    """In-memory `DebounceStore` with `SqlTagDebounceStore`'s semantics."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], int] = {}
        self.pruned: list[int] = []

    async def read_all(self) -> list[tuple[str, str, int]]:
        return [(p, c, at) for (p, c), at in self.rows.items()]

    async def prune_before(self, *, before_ms: int) -> int:
        self.pruned.append(before_ms)
        stale = [k for k, at in self.rows.items() if at < before_ms]
        for k in stale:
            del self.rows[k]
        return len(stale)

    async def record_allowed(
        self, *, platform: str, conversation_id: str, at_ms: int
    ) -> None:
        self.rows[(platform, conversation_id)] = at_ms


def run(coro):
    """One `asyncio.run` per await, so these stay plain sync tests like every
    other test in this file — `Debounce.allow` is synchronous by design."""
    return asyncio.run(coro)


def spawner(loop_tasks: list) -> Callable:
    """Stands in for `BackgroundTasks.spawn`, which `allow` cannot await: it
    collects the coroutine so the test can drive it to completion."""
    return loop_tasks.append


def drain(tasks: list) -> None:
    async def _all() -> None:
        for coro in tasks:
            await coro
        tasks.clear()

    run(_all())


KEY = ("signal", "gA")


def test_the_rate_limiter_survives_a_restart():
    """The defect 0056 closes: `nora` is force-recreated routinely, and before
    this a duplicate tag arriving seconds after a recreate ran a second turn."""
    store, tasks, clock = FakeStore(), [], FakeClock()
    first = Debounce(5, clock=clock, store=store, spawn=spawner(tasks))
    assert first.allow(KEY) is True
    drain(tasks)

    # THE RESTART: a brand-new Debounce with an empty dict, same store.
    clock.advance(1)
    second = Debounce(5, clock=clock, store=store, spawn=spawner(tasks))
    assert run(second.hydrate()) == 1
    assert second.allow(KEY) is False


def test_the_window_still_expires_across_a_restart():
    """Restored, not frozen."""
    store, tasks, clock = FakeStore(), [], FakeClock()
    first = Debounce(5, clock=clock, store=store, spawn=spawner(tasks))
    first.allow(KEY)
    drain(tasks)

    clock.advance(4.9)
    second = Debounce(5, clock=clock, store=store, spawn=spawner(tasks))
    run(second.hydrate())
    assert second.allow(KEY) is False
    clock.advance(0.2)
    assert second.allow(KEY) is True
    drain(tasks)


def test_hydration_prunes_what_can_no_longer_decide():
    """The bound that needs no retention policy: a row older than the interval
    cannot change `allow()` again, so dropping it is lossless by arithmetic."""
    store, tasks, clock = FakeStore(), [], FakeClock()
    store.rows[("signal", "ancient")] = int((clock.t - 3600) * 1000)
    store.rows[KEY] = int(clock.t * 1000)

    d = Debounce(5, clock=clock, store=store, spawn=spawner(tasks))
    assert run(d.hydrate()) == 1
    assert store.pruned == [int((clock.t - 5) * 1000)]
    assert list(store.rows) == [KEY]


def test_a_blocked_tag_is_never_written_down():
    """R-9's own rule, at the store: a blocked tag does not extend the window,
    so a verb that wrote on every call would starve a conversation under a
    burst."""
    store, tasks, clock = FakeStore(), [], FakeClock()
    d = Debounce(5, clock=clock, store=store, spawn=spawner(tasks))
    d.allow(KEY)
    drain(tasks)
    clock.advance(1)
    assert d.allow(KEY) is False
    assert tasks == []
    assert store.rows[KEY] == int((clock.t - 1) * 1000)


def test_a_key_with_no_durable_form_stays_in_memory():
    """`ConversationTarget` is a dataclass, not production's two-tuple. It has no row to be,
    and coercing one would hydrate a window against a room nobody can name — so it is kept
    in memory and nowhere else."""
    store, tasks, clock = FakeStore(), [], FakeClock()
    d = Debounce(5, clock=clock, store=store, spawn=spawner(tasks))
    assert d.allow(A) is True
    assert tasks == []
    assert store.rows == {}
    assert d.allow(A) is False, "and the in-memory window still works"


def test_a_store_with_nowhere_to_spawn_is_refused_at_construction():
    """The fourth wiring state — configured, persisting nothing — made
    unrepresentable rather than documented."""
    with pytest.raises(ValueError):
        Debounce(5, clock=FakeClock(), store=FakeStore())


def test_a_spawn_that_refuses_never_refuses_the_tag():
    """A rate limiter that raised because a write failed would refuse a tag for
    a reason that has nothing to do with R-9."""

    def refuse(coro):
        coro.close()
        raise RuntimeError("loop is shutting down")

    d = Debounce(5, clock=FakeClock(), store=FakeStore(), spawn=refuse)
    assert d.allow(KEY) is True


def test_without_a_store_nothing_survives_a_restart():
    """The dormant default, pinned rather than assumed: `store=None` is the in-memory-only
    shape, and every test above this line constructs it."""
    clock = FakeClock()
    assert Debounce(5, clock=clock).allow(KEY) is True
    clock.advance(1)
    assert Debounce(5, clock=clock).allow(KEY) is True


def test_hydrate_without_a_store_is_a_no_op_not_an_error():
    """One call site in the composition root, not a branch."""
    assert run(Debounce(5, clock=FakeClock()).hydrate()) == 0


def test_the_default_clock_reads_an_absolute_instant():
    """Not `time.monotonic`. Both are `Callable[[], float]` and every
    comparison here is a difference, so nothing else would notice — but
    `tag_debounce_state.last_allowed_at` is epoch milliseconds (0056), and a
    monotonic epoch is arbitrary per process, so a stored monotonic value is
    not a time to whatever reads it back.

    Asserted against `time.time()` rather than against the function object: the
    property that matters is «this reading names an instant the next process
    will agree with», and a monotonic clock is a process uptime.
    """
    assert abs(Debounce(5)._clock() - time.time()) < 5.0
