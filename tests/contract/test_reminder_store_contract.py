"""Every ReminderStore implementation passes this. CLAUDE.md: a port never
gains a second implementation — including a test fake — without a shared
contract suite both pass."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from nora.domain.reminders import (
    DuplicateProactiveNoteError,
    DuplicateReminderError,
    NaiveDatetimeError,
    Recurrence,
    RecurrenceRule,
    Reminder,
    ReminderState,
    ReminderStore,
    Series,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.reminders import SqlReminderStore
from nora.testing.reminders import FakeReminderStore

T0 = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(days=1)

# The repo's own downstream clock is +08:00 (`nora.agent.tools._HKT`), so an
# offset that is NOT UTC is the realistic input, not an exotic one.
HKT = timezone(timedelta(hours=8), "HKT")


def _row(**kw) -> Reminder:
    base = {
        "id": uuid.uuid4(), "requester_person_id": "miles", "platform": "signal",
        "conversation_id": "conv-1", "conversation_type": "group", "text": "洗衫",
        "created_at": T0, "due_at": T0 + timedelta(minutes=40),
        "state": ReminderState.PENDING, "fired_at": None, "outcome": None,
    }
    base.update(kw)
    return Reminder(**base)


@pytest.fixture(params=["sql", "fake"])
async def store(request):
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s: ReminderStore = SqlReminderStore(create_sql_engine(pg_url))
    else:
        s = FakeReminderStore()
    yield s
    await s.aclose()


async def test_a_created_row_is_returned_once_due(store):
    r = _row()
    await store.create(r)
    assert await store.due(now=T0, limit=10) == []
    due = await store.due(now=r.due_at, limit=10)
    assert [x.id for x in due] == [r.id]


async def test_due_is_ordered_by_due_at(store):
    late = _row(due_at=T0 + timedelta(hours=2))
    early = _row(due_at=T0 + timedelta(hours=1))
    await store.create(late)
    await store.create(early)
    due = await store.due(now=T0 + timedelta(hours=3), limit=10)
    assert [x.id for x in due] == [early.id, late.id]


async def test_only_pending_rows_are_due(store):
    for state in (ReminderState.DONE, ReminderState.CANCELLED,
                  ReminderState.FAILED, ReminderState.FIRING):
        await store.create(_row(state=state))
    assert await store.due(now=LATER, limit=10) == []


async def test_mark_moves_state_and_reports_the_change(store):
    r = _row()
    await store.create(r)
    assert await store.mark(r.id, ReminderState.DONE,
                            fired_at=T0, outcome="sent") is True
    assert (await store.get(r.id)).state is ReminderState.DONE
    assert await store.mark(uuid.uuid4(), ReminderState.DONE) is False


async def test_claim_is_a_compare_and_set_from_pending(store):
    """`claim` is the ONE transition the heartbeat must not lose a race on.

    `mark` moves a row by id alone, whatever state it is in — which is right for
    closing out a row the tick already owns, and wrong for TAKING one. `due()`
    is a query, not a claim, and a turn awaits: between the batch being read and
    a row being fired, a human may cancel it. With `mark` doing the take, that
    cancel was overwritten (CANCELLED -> FIRING) and the reminder was spoken
    anyway; the caller's `if not moved: already resolved` branch could never run
    because `UPDATE ... WHERE id = :id` always matches a row that still exists,
    and nothing deletes reminders.

    So: True only from PENDING, and the second caller of the same row gets
    False with the row untouched.
    """
    r = _row()
    await store.create(r)

    assert await store.claim(r.id, now=LATER) is True
    assert (await store.get(r.id)).state is ReminderState.FIRING
    # Idempotence is not the point — EXCLUSIVITY is. The second claim loses.
    assert await store.claim(r.id, now=LATER) is False

    # Every non-PENDING state refuses, and the row is left exactly as it was.
    for state in (ReminderState.CANCELLED, ReminderState.DONE,
                  ReminderState.FAILED, ReminderState.FIRING):
        other = _row(state=state)
        await store.create(other)
        assert await store.claim(other.id, now=LATER) is False, state
        assert (await store.get(other.id)).state is state

    assert await store.claim(uuid.uuid4(), now=LATER) is False


async def test_a_cancel_beats_a_later_claim(store):
    """The race from the heartbeat's side, end to end: a row selected by `due()`
    and cancelled before the tick gets to it is never fired."""
    r = _row()
    await store.create(r)
    selected = await store.due(now=LATER, limit=10)
    assert [x.id for x in selected] == [r.id]

    assert await store.cancel(r.id, conversation_id="conv-1") is True
    assert await store.claim(r.id, now=LATER) is False
    assert (await store.get(r.id)).state is ReminderState.CANCELLED


async def test_cancel_is_scoped_to_the_conversation(store):
    r = _row(conversation_id="conv-1")
    await store.create(r)
    assert await store.cancel(r.id, conversation_id="conv-OTHER") is False
    assert await store.cancel(r.id, conversation_id="conv-1") is True
    assert (await store.get(r.id)).state is ReminderState.CANCELLED


async def test_pending_for_is_scoped_and_excludes_finished(store):
    mine = _row(conversation_id="conv-1")
    theirs = _row(conversation_id="conv-2")
    done = _row(conversation_id="conv-1", state=ReminderState.DONE)
    for r in (mine, theirs, done):
        await store.create(r)
    assert [x.id for x in await store.pending_for("conv-1")] == [mine.id]


async def test_all_pending_crosses_conversations_and_excludes_finished(store):
    """The operator's read (`nora reminders list`), and the one `pending_for`
    cannot stand in for: which conversations have rows waiting is the question,
    so there is no conversation to scope by.

    Ordered like `due()` — `due_at` then `id` — and PENDING only, or the CLI's
    "what is still coming" would include everything that already went out. Both
    implementations get their own test because the SQL adapter and the fake
    drift the moment one of them is not under the suite."""
    early = _row(conversation_id="conv-1", due_at=T0 + timedelta(hours=1))
    late = _row(conversation_id="conv-2", due_at=T0 + timedelta(hours=2))
    done = _row(conversation_id="conv-3", state=ReminderState.DONE)
    cancelled = _row(conversation_id="conv-4", state=ReminderState.CANCELLED)
    for r in (late, early, done, cancelled):
        await store.create(r)

    assert [x.id for x in await store.all_pending(limit=10)] == [early.id, late.id]
    assert [x.id for x in await store.all_pending(limit=1)] == [early.id]


async def test_all_pending_refuses_a_non_positive_limit(store):
    """`check_limit`'s rule reaches this read too. Unguarded, Postgres raised
    `InvalidRowCountInLimitClauseError` on `LIMIT -1` while the fake's `[:-1]`
    returned everything-except-the-last — plausible-looking wrong data, which is
    the worse of the two."""
    with pytest.raises(ValueError):
        await store.all_pending(limit=0)


async def test_count_pending_is_per_requester(store):
    await store.create(_row(requester_person_id="miles"))
    await store.create(_row(requester_person_id="miles"))
    await store.create(_row(requester_person_id="tyler"))
    assert await store.count_pending("miles") == 2
    assert await store.count_pending("tyler") == 1


async def test_history_is_one_requesters_rows_in_every_state_oldest_first(store):
    """The decision log: what one owner
    decided and how it went. Unlike `pending_for`/`all_pending` it KEEPS the
    finished rows — a log that forgot its outcomes would read every restraint
    and every failure as «never happened»."""
    done = _row(requester_person_id="nora", created_at=T0,
                state=ReminderState.DONE, outcome="silent")
    later = _row(requester_person_id="nora", created_at=T0 + timedelta(hours=1))
    theirs = _row(requester_person_id="miles", created_at=T0 + timedelta(minutes=30))
    for r in (later, theirs, done):
        await store.create(r)

    rows = await store.history(
        requester_person_id="nora", since=T0 - timedelta(days=1), limit=10,
    )

    assert [r.id for r in rows] == [done.id, later.id]
    assert rows[0].outcome == "silent"
    assert rows[0].state is ReminderState.DONE


async def test_history_honours_since_and_limit(store):
    old = _row(requester_person_id="nora", created_at=T0 - timedelta(days=30))
    recent = [
        _row(requester_person_id="nora", created_at=T0 + timedelta(minutes=i))
        for i in range(3)
    ]
    for r in (old, *recent):
        await store.create(r)

    rows = await store.history(requester_person_id="nora", since=T0, limit=2)

    assert [r.id for r in rows] == [recent[0].id, recent[1].id]


async def test_history_refuses_a_non_positive_limit(store):
    with pytest.raises(ValueError):
        await store.history(requester_person_id="nora", since=T0, limit=0)


async def test_history_refuses_a_naive_since(store):
    with pytest.raises(NaiveDatetimeError):
        await store.history(
            requester_person_id="nora", since=T0.replace(tzinfo=None), limit=5,
        )


async def test_sweep_resolves_firing_rows_to_failed(store):
    """At-most-once. A crash mid-fire loses that reminder, visibly. It is
    NEVER re-queued — re-queueing double-sends anything that crashed after
    egress, and nothing links a row to its delivered message.

    The assertion reads the TERMINAL STATE via get(). Asserting only that the row
    vanished from due() stays green if the sweep moves FIRING to anything at
    all — decorative, per CLAUDE.md."""
    stuck = _row(state=ReminderState.FIRING)
    ok = _row(state=ReminderState.PENDING)
    await store.create(stuck)
    await store.create(ok)

    assert await store.sweep_firing(now=LATER) == 1

    swept = await store.get(stuck.id)
    assert swept is not None
    assert swept.state is ReminderState.FAILED
    assert swept.outcome == "failed_error"
    # `fired_at` is STAMPED, exactly as `mark` stamps it on every other terminal
    # write. A swept row is the one an operator most needs a time on: the
    # process that set it FIRING is gone and nothing else in the table records
    # when the crash happened.
    assert swept.fired_at == LATER
    assert swept.fired_at.utcoffset() == timedelta(0)
    assert (await store.get(ok.id)).state is ReminderState.PENDING
    # Untouched rows keep their NULL: a blanket stamp would date every pending
    # reminder to the last boot.
    assert (await store.get(ok.id)).fired_at is None


# --------------------------------------------------------------------------
# Behaviours where the two implementations can diverge: the timezone contract,
# the `limit` contract, the ordering tiebreak, and what a duplicate id does.
# --------------------------------------------------------------------------


async def test_stored_datetimes_come_back_utc_aware(store):
    """UTC-AWARE IS THE CONTRACT, on the way in and on the way out.

    A `timestamptz` column normalises to UTC and asyncpg hands the value back
    with `tzinfo=UTC`; an in-memory dict hands back exactly the object it was
    given. Both are "correct" instants, so `==` compares equal and cannot see
    the difference — `utcoffset()` is what differs, and it is what downstream
    formatting reads. Feed a +08:00 aware row (this repo's own clock, `_HKT`):
    without the contract the two stores print digits eight hours apart for the
    same instant.
    """
    due_hkt = datetime(2026, 8, 9, 17, 0, tzinfo=HKT)
    created_hkt = datetime(2026, 8, 8, 20, 0, tzinfo=HKT)
    r = _row(created_at=created_hkt, due_at=due_hkt)
    await store.create(r)

    got = await store.get(r.id)
    assert got is not None
    for label, value in (("due_at", got.due_at), ("created_at", got.created_at)):
        assert value.utcoffset() == timedelta(0), f"{label} is not UTC-normalised"
    # Same instant, and the digits a downstream formatter would print are UTC's.
    assert got.due_at == due_hkt
    assert got.due_at.hour == 9

    # Every read path, not just get(): due(), pending_for(), and the fired_at
    # stamp a mark() writes.
    (via_due,) = await store.due(now=LATER, limit=10)
    assert via_due.due_at.utcoffset() == timedelta(0)
    (via_pending,) = await store.pending_for("conv-1")
    assert via_pending.due_at.utcoffset() == timedelta(0)

    await store.mark(r.id, ReminderState.DONE,
                     fired_at=datetime(2026, 8, 9, 17, 5, tzinfo=HKT), outcome="sent")
    marked = await store.get(r.id)
    assert marked is not None
    assert marked.fired_at is not None
    assert marked.fired_at.utcoffset() == timedelta(0)
    assert marked.fired_at.hour == 9


async def test_naive_datetimes_are_refused_rather_than_guessed(store):
    """A naive datetime has no answer, so neither store may invent one.

    asyncpg would read it in the CLIENT HOST's zone (a deployment fact, not a
    domain one) and the fake would store it as-is and then raise `TypeError`
    from `naive <= aware` inside `due()`. Both are guesses dressed as answers;
    `NaiveDatetimeError` is the contract instead, on every datetime that
    crosses the port.
    """
    naive = datetime(2026, 8, 9, 9, 0)
    assert naive.tzinfo is None

    with pytest.raises(NaiveDatetimeError):
        await store.create(_row(due_at=naive))
    with pytest.raises(NaiveDatetimeError):
        await store.create(_row(created_at=naive))

    ok = _row()
    await store.create(ok)
    with pytest.raises(NaiveDatetimeError):
        await store.due(now=naive, limit=10)
    with pytest.raises(NaiveDatetimeError):
        await store.mark(ok.id, ReminderState.DONE, fired_at=naive)


async def test_due_refuses_a_non_positive_limit(store):
    """`limit` is a batch size; a non-positive one is a caller bug.

    Postgres raises `InvalidRowCountInLimitClauseError` on `LIMIT -1` while
    Python's `[:-1]` cheerfully returns everything-except-the-last, which is
    plausible-looking data for a bad call. The rule for both stores: `limit >= 1`
    or `ValueError`.
    """
    await store.create(_row(due_at=T0))
    await store.create(_row(due_at=T0))

    for bad in (-1, 0):
        with pytest.raises(ValueError):
            await store.due(now=LATER, limit=bad)

    assert len(await store.due(now=LATER, limit=1)) == 1


async def test_equal_due_at_is_broken_deterministically_by_id(store):
    """Two reminders for "9am" is ordinary, not exotic.

    `ORDER BY due_at` alone leaves ties to the heap's physical order, which an
    ordinary `mark()` UPDATE reshuffles by moving a tuple; the fake leant on
    Python's stable sort over dict insertion order. Same rows, two orders. With
    `limit`, a tie straddling the batch boundary makes the BATCH's composition
    unspecified too — which is why the third assertion takes `limit=2`.

    `id` is the tiebreak: Postgres compares `uuid` bytewise and Python's
    `UUID.__lt__` compares `.int` (the same 16 bytes, big-endian), so the two
    orderings are the same ordering.
    """
    same = T0 + timedelta(minutes=40)
    ids = sorted(uuid.uuid4() for _ in range(3))
    # Insert in an order that is neither id order nor its reverse.
    for rid in (ids[1], ids[2], ids[0]):
        await store.create(_row(id=rid, due_at=same))

    # An ordinary state move, then a move back: enough to relocate a heap tuple
    # in Postgres, and the exact operation that reordered a real batch.
    await store.mark(ids[1], ReminderState.FIRING)
    await store.mark(ids[1], ReminderState.PENDING)

    assert [x.id for x in await store.due(now=LATER, limit=10)] == ids
    assert [x.id for x in await store.pending_for("conv-1")] == ids
    assert [x.id for x in await store.due(now=LATER, limit=2)] == ids[:2]


# --------------------------------------------------------------------------
# Recurrence (DEC-85). A recurring reminder is a CHAIN of one-shot rows, and
# `claim` is the ONE place a link is forged: these hold both stores to the same
# chain, the same catch-up rule and the same refusal to forge a second link.
# --------------------------------------------------------------------------

SERIES = uuid.UUID("11111111-1111-5111-9111-111111111111")


def _daily(**kw) -> Reminder:
    """A daily row, occurrence 0, anchored on its own `due_at`.

    HKT, not UTC, and not a fixed offset used as a zone: `RecurrenceRule.tz`
    takes an IANA NAME because the local wall clock is what a recurrence keeps.
    The anchor is 20:40 HKT (12:40 UTC) so the daily step is visible in both.
    """
    rule = RecurrenceRule(
        every=kw.pop("every", Recurrence.DAILY), tz="Asia/Hong_Kong",
        count=kw.pop("count", 3), until=kw.pop("until", None),
        interval=kw.pop("interval", 1),
    )
    occurrence = kw.pop("occurrence", 0)
    anchor = kw.pop("anchor_at", T0 + timedelta(minutes=40))
    kw.setdefault("due_at", anchor)
    return _row(
        id=uuid.uuid5(SERIES, str(occurrence)),
        series=Series(
            id=SERIES, anchor_at=anchor, occurrence=occurrence, rule=rule,
        ),
        **kw,
    )


async def test_a_recurring_row_round_trips_every_field_of_its_series(store):
    """Seven columns on one side, one optional value object on the other.

    Asserted field by field rather than by `==` on the whole row, because the
    two stores fail this differently and both failures are silent: the SQL side
    can drop a column from one of its two INSERT paths, and the fake can hand
    back an object it never normalised. A series missing its `rule.count` reads
    as a series with no bound; one missing its `anchor_at` cannot compute a
    successor at all.
    """
    ends = T0 + timedelta(days=10)
    r = _daily(count=5, until=ends)
    await store.create(r)

    got = await store.get(r.id)
    assert got is not None
    assert got.series is not None
    assert got.series.id == SERIES
    assert got.series.occurrence == 0
    assert got.series.anchor_at == r.series.anchor_at
    assert got.series.rule.every is Recurrence.DAILY
    assert got.series.rule.tz == "Asia/Hong_Kong"
    assert got.series.rule.count == 5
    assert got.series.rule.until == ends
    assert got.series.rule.interval == 1
    # The port's UTC rule reaches the two new datetimes as well.
    assert got.series.anchor_at.utcoffset() == timedelta(0)
    assert got.series.rule.until.utcoffset() == timedelta(0)


async def test_a_sub_day_series_round_trips_its_unit_and_its_interval(store):
    """A series' persisted recurrence interval and kind, through BOTH stores.

    The fake holds domain objects, so it round-trips anything by construction;
    the SQL store serialises through two hand-written mappings
    (`_row_to_series` and `_insert_values`) and a widened CHECK constraint.
    Hard-coding `recurrence_interval` to 1 on write, or dropping it from the
    read, would leave every other test green — a series would come back firing
    every minute instead of every ten.

    `minutely` also proves the `ck_reminders_recurrence_kind` constraint admits
    the kind by INSERT rather than by the dumped text in the schema baseline.
    """
    r = _daily(every=Recurrence.MINUTELY, count=7, interval=10)
    await store.create(r)

    got = await store.get(r.id)
    assert got is not None
    assert got.series is not None
    assert got.series.rule.every is Recurrence.MINUTELY
    assert got.series.rule.interval == 10
    # Read back as the STEP it will actually fire at, not merely as a stored
    # integer: this is what `plan_successor` computes from.
    assert got.series.rule.period_seconds == 600


async def test_a_one_shot_row_carries_no_series(store):
    """The other half, and not a formality: `series=None` is what every row
    written before DEC-85 has, so a store that invented an empty series for one
    would turn every historical reminder into a chain that never ends."""
    r = _row()
    await store.create(r)
    got = await store.get(r.id)
    assert got is not None
    assert got.series is None


async def test_claiming_a_recurring_row_mints_exactly_one_successor(store):
    """THE CHAIN, forged in the claim (DEC-85).

    Four facts, and each is a different way this goes wrong:

    * the successor EXISTS and is PENDING — without it the series ends at its
      first occurrence and nothing says so;
    * it is due one LOCAL day later, from the anchor;
    * it carries the same series, the same rule and `occurrence + 1`, so the
      third link can be forged from it in turn;
    * the claimed row is FIRING, unchanged in every other respect — the
      successor is an addition, never a reset of the row that fired. R-37 ("a
      row fires once, ever") is what that last one protects.
    """
    r = _daily(count=3)
    await store.create(r)

    assert await store.claim(r.id, now=r.due_at) is True

    pending = await store.pending_for("conv-1")
    assert len(pending) == 1, "exactly one successor, and only one"
    successor = pending[0]
    assert successor.id != r.id
    assert successor.due_at == r.due_at + timedelta(days=1)
    assert successor.state is ReminderState.PENDING
    assert successor.text == r.text
    assert successor.requester_person_id == r.requester_person_id
    assert successor.conversation_id == r.conversation_id
    assert successor.fired_at is None and successor.outcome is None
    assert successor.series is not None
    assert successor.series.id == SERIES
    assert successor.series.occurrence == 1
    assert successor.series.anchor_at == r.series.anchor_at
    assert successor.series.rule == r.series.rule

    fired = await store.get(r.id)
    assert fired is not None
    assert fired.state is ReminderState.FIRING


async def test_a_one_shot_claim_mints_nothing(store):
    """The control, and the reason this file can claim the change is additive:
    a claim that minted a successor for every row would pass every assertion
    above and turn every one-shot reminder into an unbounded series."""
    r = _row()
    await store.create(r)

    assert await store.claim(r.id, now=LATER) is True

    assert await store.pending_for("conv-1") == []


async def test_the_last_occurrence_of_a_series_mints_nothing(store):
    """`count` is a bound on the SERIES, so the final link forges nothing.

    Without this a series would run forever and the bound would be decoration —
    which is precisely the failure `RecurrenceRule.count` being REQUIRED exists
    to make unreachable. `count=3` with `occurrence=2` is the third and last.
    """
    r = _daily(count=3, occurrence=2, anchor_at=T0, due_at=T0 + timedelta(days=2))
    await store.create(r)

    assert await store.claim(r.id, now=r.due_at) is True

    assert await store.pending_for("conv-1") == []


async def test_an_until_bound_ends_the_series_before_the_count_does(store):
    """Two bounds, and the series ends at whichever comes FIRST.

    `count=10` would run ten days; `until` is set two days past the anchor, so
    the occurrence after that one is refused. A store that honoured only the
    count would keep a «提到10月11號為止」 reminder going for another week.
    """
    anchor = T0 + timedelta(minutes=40)
    r = _daily(
        count=10, occurrence=1, anchor_at=anchor,
        due_at=anchor + timedelta(days=1),
        until=anchor + timedelta(days=1, hours=1),
    )
    await store.create(r)

    assert await store.claim(r.id, now=r.due_at) is True

    assert await store.pending_for("conv-1") == []


async def test_a_late_claim_resumes_at_the_next_future_occurrence(store):
    """THE CATCH-UP RULE: skip, never burst (DEC-85).

    The process was down for three days. The one overdue row fires once — that
    is `due()`'s ordinary behaviour and unchanged — and its successor is the
    next occurrence STRICTLY AFTER `now`, not the day after the row that just
    fired. A store that stepped from `due_at` instead would leave three more
    overdue rows behind it, and the next three ticks would fire them one after
    another into a group where every one of them is about a moment that has
    passed.

    `count=10`, so the skipped occurrences are visible in the INDEX as well:
    they are consumed (occurrence 4, not occurrence 1), because an outage must
    not lengthen a series' calendar footprint.
    """
    anchor = T0 + timedelta(minutes=40)
    r = _daily(count=10, anchor_at=anchor, due_at=anchor)
    await store.create(r)

    now = anchor + timedelta(days=3, hours=2)
    assert await store.claim(r.id, now=now) is True

    (successor,) = await store.pending_for("conv-1")
    assert successor.due_at > now
    assert successor.due_at == anchor + timedelta(days=4)
    assert successor.series is not None
    assert successor.series.occurrence == 4


async def test_a_losing_claim_mints_no_second_successor(store):
    """EXCLUSIVITY reaches the chain too.

    The second claim of the same row loses the compare-and-set, and a store that
    computed the successor before consulting `rowcount` would write it anyway —
    two pending occurrences for one series, so the same reminder twice. The
    derived id (`uuid5(series_id, occurrence)`) makes that a primary-key
    collision rather than a silent duplicate, and this asserts the state either
    way: still exactly one successor, still `occurrence == 1`.
    """
    r = _daily(count=5)
    await store.create(r)

    assert await store.claim(r.id, now=r.due_at) is True
    assert await store.claim(r.id, now=r.due_at) is False

    (successor,) = await store.pending_for("conv-1")
    assert successor.series is not None
    assert successor.series.occurrence == 1


async def test_cancelling_the_pending_occurrence_ends_the_series(store):
    """Cancelling a recurring reminder cancels the SERIES, with no extra verb.

    The chain's next link is only ever forged by `claim`, so a row that is
    cancelled rather than claimed forges nothing: after this there is no pending
    row for the series and nothing will ever create one. That is the whole of
    DEC-85's cancel story, and it is asserted rather than argued because the
    alternative failure is unrecoverable — a person told a daily reminder is off
    and told again tomorrow that it is not.
    """
    r = _daily(count=10)
    await store.create(r)

    assert await store.cancel(r.id, conversation_id="conv-1") is True
    assert await store.claim(r.id, now=r.due_at) is False

    assert await store.pending_for("conv-1") == []
    cancelled = await store.get(r.id)
    assert cancelled is not None
    assert cancelled.state is ReminderState.CANCELLED


async def test_a_recurring_series_costs_one_against_the_pending_count(store):
    """The cap decision, held by the STORE rather than argued in a docstring.

    `max_pending_per_requester` counts pending rows, and a series only ever has
    one — before the claim it is the occurrence that is waiting, after it the
    successor. If the chain ever wrote its successor without the claim
    resolving the predecessor, a daily reminder would eat the requester's whole
    allowance in three weeks.
    """
    r = _daily(count=10)
    await store.create(r)
    assert await store.count_pending("miles") == 1

    assert await store.claim(r.id, now=r.due_at) is True
    assert await store.count_pending("miles") == 1


async def test_a_daily_series_keeps_its_local_time_across_a_dst_boundary(store):
    """08:00 LOCAL stays 08:00 local, and the UTC instant is what moves.

    New York rather than Hong Kong deliberately: Hong Kong has had no DST since
    1979, so a store that added a flat 24 hours would pass every other test in
    this file and be wrong the first time Nora is deployed anywhere that does.
    8 Mar 2026 is the US spring-forward, so 08:00 EST (13:00 UTC) becomes 08:00
    EDT (12:00 UTC) — a 23-hour step in absolute time, which is the arithmetic
    `due_at + timedelta(days=1)` cannot produce.
    """
    anchor = datetime(2026, 3, 7, 13, 0, tzinfo=UTC)      # 08:00 EST
    rule = RecurrenceRule(
        every=Recurrence.DAILY, tz="America/New_York", count=5,
    )
    r = _row(
        id=uuid.uuid5(SERIES, "0"), due_at=anchor,
        series=Series(id=SERIES, anchor_at=anchor, occurrence=0, rule=rule),
    )
    await store.create(r)

    assert await store.claim(r.id, now=anchor) is True

    (successor,) = await store.pending_for("conv-1")
    assert successor.due_at == datetime(2026, 3, 8, 12, 0, tzinfo=UTC)
    assert successor.due_at - anchor == timedelta(hours=23)


async def test_create_refuses_a_duplicate_id(store):
    """The same id twice is a collision, and a collision must be loud.

    Postgres raises `UniqueViolationError` off the primary key; a fake that
    silently OVERWROTE the row would show a clobbered reminder as success where
    the real store errors. `create` is insert-only in both, and the first row is
    left untouched.
    """
    first = _row(text="洗衫")
    await store.create(first)

    with pytest.raises(DuplicateReminderError):
        await store.create(_row(id=first.id, text="買餸"))

    survivor = await store.get(first.id)
    assert survivor is not None
    assert survivor.text == "洗衫"


async def test_one_undecodable_row_does_not_strand_every_other_reminder(store):
    """`recurrence_tz` is free `Text` — no CHECK
    constraint can validate an IANA zone name — and `_row_to_reminder` raises on
    one this host's tzdb cannot resolve. A raise escaping `due()` into
    `HeartbeatLoop.tick` ABOVE its per-group `try` (whose docstring promises one
    bad reminder must not strand the others) would stop EVERY reminder firing, in
    every conversation, on every tick — visible only as
    `nora_errors_total{stage="heartbeat"}`.

    Skipping is the fail-closed answer: the bad row never fires, the good ones
    still do. Revert `_decode_due` to a comprehension and this reds.

    SQL-store only: the fake holds objects, so it has no undecodable state to
    reproduce — the hazard is deserialisation, which only the real store does.
    """
    if not hasattr(store, "_engine"):
        pytest.skip("row decoding is a property of the SQL store only")

    good = _row(text="good one")
    await store.create(good)
    bad = _row(text="poisoned")
    await store.create(bad)

    # Corrupt one row's zone out of band, exactly as a tzdata rename would.
    import sqlalchemy as sa
    async with store._engine.begin() as conn:
        await conn.execute(sa.text(
            "UPDATE reminders SET recurrence='daily', recurrence_tz='Mars/Olympus_Mons', "
            "recurrence_count=3, series_id=id, series_anchor_at=due_at WHERE id=:i"
        ), {"i": str(bad.id)})

    due = await store.due(now=bad.due_at, limit=10)

    assert [r.text for r in due] == ["good one"]      # the healthy row still fires


async def test_kind_and_payload_round_trip_through_both_stores(store):
    """DEC-139: `kind`/`payload` are first-class Reminder fields — both
    implementations must round-trip them (fake-fidelity rule)."""
    r = _row(
        kind="proactive", payload={"note_title": "Dinner"},
    )
    await store.create(r)

    back = await store.get(r.id)
    assert back is not None
    assert back.kind == "proactive"
    assert back.payload == {"note_title": "Dinner"}


async def test_default_kind_is_reminder(store):
    """A row written with no kind reads back as `"reminder"` — the whole of
    the pre-DEC-139 table behaves byte-identically after the columns land."""
    r = _row()
    await store.create(r)

    back = await store.get(r.id)
    assert back is not None
    assert back.kind == "reminder"
    assert back.payload is None


# -- one open proactive row per note -----------------------------


def _proactive(note_id: str | None = "note-1", **kw) -> Reminder:
    payload = {"intent": "raise it"} if note_id is None else {
        "note_id": note_id, "revision_id": "rev-1", "intent": "raise it",
    }
    return _row(
        requester_person_id="nora", kind="proactive", payload=payload, **kw,
    )


@pytest.mark.parametrize("held", [ReminderState.PENDING, ReminderState.FIRING])
async def test_a_second_open_proactive_row_on_a_note_is_refused(store, held):
    """A pass writes a row while an earlier row on the
    same note still waited (or was being said). The store refuses it and
    writes nothing, whichever implementation holds it."""
    first = _proactive(state=held)
    await store.create(first)

    second = _proactive(due_at=T0 + timedelta(hours=3))
    with pytest.raises(DuplicateProactiveNoteError) as caught:
        await store.create(second)

    assert caught.value.note_id == "note-1"
    assert await store.get(second.id) is None


async def test_the_same_proactive_row_twice_is_a_duplicate_id(store):
    """Its id and its note both collide; the id is named. Postgres checks the
    `ON CONFLICT` arbiter first and would skip on the note, so the SQL store
    has to ask for the id itself to agree with the in-memory one."""
    row = _proactive()
    await store.create(row)

    with pytest.raises(DuplicateReminderError):
        await store.create(row)


@pytest.mark.parametrize(
    "gone", [ReminderState.DONE, ReminderState.CANCELLED, ReminderState.FAILED],
)
async def test_a_row_that_went_off_holds_its_note_no_longer(store, gone):
    """A re-raise after silence is writable: only an open row holds a note."""
    await store.create(_proactive(state=gone))

    again = _proactive()
    await store.create(again)

    assert await store.get(again.id) is not None


async def test_the_rule_is_per_note_and_proactive_only(store):
    """Another note, a proactive row with no note, and a person's reminder
    carrying the same note id are none of them a duplicate."""
    await store.create(_proactive("note-1"))

    await store.create(_proactive("note-2"))
    await store.create(_proactive(None))
    await store.create(_proactive(None))
    await store.create(_row(payload={"note_id": "note-1"}))

    assert len(await store.all_pending(limit=10)) == 5


async def test_a_claimed_row_still_holds_its_note(store):
    """`claim` moves the SAME row pending -> firing; it neither trips the rule
    nor frees the note."""
    first = _proactive()
    await store.create(first)
    assert await store.claim(first.id, now=first.due_at)

    with pytest.raises(DuplicateProactiveNoteError):
        await store.create(_proactive())


async def test_pending_of_is_one_requesters_pending_rows_in_due_order(store):
    late = _proactive("note-1", due_at=T0 + timedelta(hours=2))
    early = _proactive("note-2", due_at=T0 + timedelta(hours=1))
    await store.create(late)
    await store.create(early)
    await store.create(_proactive("note-3", state=ReminderState.DONE))
    await store.create(_row())                          # miles's, not nora's

    rows = await store.pending_of("nora")

    assert [r.id for r in rows] == [early.id, late.id]
