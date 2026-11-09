"""The shared logical clock: one-minute ticks on the Unix epoch.

`design/cognition-loop/18-execution-and-time.md`: a **tick** is an integer —
the index of the one-minute interval an instant falls in, `floor(ms /
60_000)`. A deadline's **due tick** is the FIRST tick that is entirely at or
after the target instant, `ceil(ms / 60_000)`, so pending work is eligible
exactly when `due_tick <= current_tick` and never before — a reminder due at
03:30:50 is eligible at 03:31:00, one due at 04:30:00 exactly is eligible at
04:30:00.

The stores write a target's real instant (`reminders.due_at`,
`stimulus.due_ms`) and carry `due_tick` as a GENERATED column derived from it
in SQL, so no writer can let the two disagree; `due_at`/`due_ms` stay as the
requested instant and audit fields. The same rule is spelled here in Python
so the in-memory stores and every reader compute it identically.

**Slots, not only ticks.** `_slot` (`nora.app.runtime`) already computed
`floor((t - offset) / interval)` for a scheduled Dreamer adaptor — the same
arithmetic a tick is, with the base minute as interval and no offset.
`slot_index` is that function generalised, and `_slot` calls it, so there is
one floor-division on one time base rather than two.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

#: Tick quantum: one minute, in milliseconds and as a `timedelta`.
TICK_MS = 60_000
TICK_SECONDS = 60
_TICK = timedelta(milliseconds=TICK_MS)
_EPOCH = datetime.fromtimestamp(0, tz=UTC)


def tick_of_ms(at_ms: int) -> int:
    """The tick `at_ms` falls IN: `floor(at_ms / 60_000)` — the current tick
    for a `now`. Python floors toward negative infinity, which is right: an
    instant before the epoch still lands in a well-defined tick rather than
    truncating toward zero and colliding with its neighbour."""
    return at_ms // TICK_MS


def due_tick_of_ms(target_ms: int) -> int:
    """The tick a deadline is DUE in: `ceil(target_ms / 60_000)` — the first
    tick boundary at or after the target, so an exact boundary is due in its
    own tick and nothing ever becomes eligible early."""
    return -((-target_ms) // TICK_MS)


def tick_of_at(at: datetime) -> int:
    """`tick_of_ms` on a `datetime`, computed on its microseconds — no float
    `timestamp()` rounding between a stored `timestamptz` and the tick the
    SQL generated column computed from it."""
    return (at - _EPOCH) // _TICK


def due_tick_of_at(at: datetime) -> int:
    """`due_tick_of_ms` on a `datetime`, microseconds-exact (see `tick_of_at`)."""
    return -((_EPOCH - at) // _TICK)


def tick_start_ms(tick: int) -> int:
    """The first millisecond of `tick` — the earliest instant at which a
    deadline with `due_tick == tick` is eligible."""
    return tick * TICK_MS


def slot_index(at_ms: int, *, quantum_ms: int, phase_ms: int = 0) -> int:
    """`floor((at_ms - phase_ms) / quantum_ms)`: which slot of a periodic
    schedule `at_ms` falls in. Two instants in the same slot are the same
    scheduled occasion, so a schedule fires at most once per slot — what
    makes "daily at 06:00 UTC" expressible and lets two adaptors sharing an
    interval be given different PHASES.

    `nora.app.runtime._slot` delegates to this; a due tick is
    `slot_index(..., quantum_ms=TICK_MS)` on a `ceil` boundary — the same
    arithmetic, not a parallel one.
    """
    return (at_ms - phase_ms) // quantum_ms
