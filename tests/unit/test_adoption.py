"""Lazy adoption (`nora.app.adoption`): when a sender who was unmapped at
ingest is later added to identity.toml, their already-audited messages are
replayed into their Honcho peer at the next startup instead of staying unfed
forever.

Every test here asserts on real store and memory state — what was fed, what
was marked — never on a coroutine existing.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from nora.app import adoption
from nora.domain.models import NormalizedMessage, PersonRef
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing.fakes import FakeMemoryStore, FakeRecordStore

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")


def _persons(*, mapped: bool = True) -> Persons:
    """A `Persons` index that either does or does not know `+852111` — the
    before/after of the operator editing identity.toml."""
    if not mapped:
        return Persons(by_person={}, index={})
    return Persons(by_person={"miles": MILES}, index={("signal", "+852111"): "miles"})


def _msg(*, body="hello", ts=1, sender="+852111", platform="signal") -> NormalizedMessage:
    return NormalizedMessage(
        platform=platform, conversation_id="g1", conversation_type="group",
        sender_id=sender, sender_name="Miles", timestamp=ts, body=body, raw={},
    )


async def _run(audit, memory, persons, metrics=None, **kw):
    return await adoption.backfill(
        audit=audit, memory=memory, persons=persons,
        metrics=metrics if metrics is not None else Metrics(),
        start_delay_seconds=0.0, **kw,
    )


# ---------------------------------------------------------------------------
# (a) a row whose sender is now mapped gets fed and marked
# ---------------------------------------------------------------------------


async def test_a_now_mapped_sender_is_fed_and_marked():
    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    # Ingested while unmapped: audited with person_id NULL, nothing fed.
    row_id = await audit.append(message=_msg(body="i like congee"), person_id=None)
    assert memory.fed == []

    counts = await _run(audit, memory, _persons())

    assert memory.fed == [("miles", "i like congee")]
    assert "miles" in memory.peers
    assert audit.rows[row_id - 1]["person_id"] == "miles"
    assert counts.scanned == 1
    assert counts.resolved == 1
    assert counts.fed == 1
    assert counts.marked == 1
    assert counts.failed == 0


async def test_adoption_is_counted():
    metrics = Metrics()
    audit = FakeRecordStore()
    await audit.append(message=_msg())
    await _run(audit, FakeMemoryStore(), _persons(), metrics)
    assert metrics.get("nora_lazy_adoptions_total", platform="signal") == 1
    assert metrics.get("nora_adoption_failures_total") == 0


async def test_the_counts_are_logged_even_when_nothing_was_found(caplog):
    """R-5's shape applied to a chore: "found nothing" and "never ran" must not
    look the same in the log."""
    caplog.set_level(logging.INFO, logger="nora.app.adoption")
    await _run(FakeRecordStore(), FakeMemoryStore(), _persons())
    assert any("lazy adoption: scanned 0" in r.getMessage() for r in caplog.records)


async def test_the_feed_is_ordered_by_ts_not_by_insertion():
    """A peer's memory has to read chronologically. signal-cli's catch-up
    delivery means received-order and sent-order genuinely disagree."""
    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    for i in (3, 1, 2):
        await audit.append(message=_msg(body=f"m{i}", ts=i))

    await _run(audit, memory, _persons())

    assert [text for _, text in memory.fed] == ["m1", "m2", "m3"]


# ---------------------------------------------------------------------------
# (b) a row whose sender is still unmapped is left alone
# ---------------------------------------------------------------------------


async def test_a_still_unmapped_sender_is_left_alone():
    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    row_id = await audit.append(message=_msg(sender="+852999"))

    counts = await _run(audit, memory, _persons())

    assert memory.fed == []
    assert audit.rows[row_id - 1]["person_id"] is None, "an unresolved row must stay unmarked"
    assert counts.scanned == 1
    assert counts.resolved == 0
    assert counts.marked == 0


async def test_an_already_attributed_row_is_never_re_fed():
    """It never enters the scan at all — `person_id` is the marker."""
    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    await audit.append(message=_msg(), person_id="miles")

    counts = await _run(audit, memory, _persons())

    assert memory.fed == []
    assert counts.scanned == 0


async def test_noras_own_rows_are_never_adopted():
    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    await audit.append(message=_msg(body="her reply"), is_nora=True)

    counts = await _run(audit, memory, _persons())

    assert memory.fed == []
    assert counts.scanned == 0


# ---------------------------------------------------------------------------
# (c) a Honcho failure leaves person_id NULL so it retries next start
# ---------------------------------------------------------------------------


async def test_a_honcho_failure_leaves_the_row_unmarked_for_the_next_start():
    """THE ordering test. Marking before feeding would retire this row from
    every future scan, making the hole permanent and silent."""
    audit = FakeRecordStore()
    down = FakeMemoryStore(fail=True)
    row_id = await audit.append(message=_msg())

    counts = await _run(audit, down, _persons())

    assert audit.rows[row_id - 1]["person_id"] is None
    assert counts.resolved == 1
    assert counts.fed == 0
    assert counts.marked == 0
    assert counts.failed == 1

    # Honcho comes back; the next start adopts it.
    recovered = FakeMemoryStore()
    again = await _run(audit, recovered, _persons())
    assert recovered.fed == [("miles", "hello")]
    assert audit.rows[row_id - 1]["person_id"] == "miles"
    assert again.marked == 1


async def test_a_honcho_failure_is_counted():
    metrics = Metrics()
    audit = FakeRecordStore()
    await audit.append(message=_msg())
    await _run(audit, FakeMemoryStore(fail=True), _persons(), metrics)
    assert metrics.get("nora_adoption_failures_total", platform="signal") == 1
    assert metrics.get("nora_lazy_adoptions_total") == 0


async def test_a_raising_memory_store_neither_crashes_nor_marks():
    """`HonchoClient` returns False rather than raising, but the port makes no
    such promise — a MemoryStore that raises must not take startup down, and
    must not be mistaken for a successful feed."""

    class ExplodingMemory(FakeMemoryStore):
        async def feed(self, *, peer_id: str, text: str) -> bool:
            raise RuntimeError("connection reset")

    audit = FakeRecordStore()
    row_id = await audit.append(message=_msg())

    counts = await _run(audit, ExplodingMemory(), _persons())

    assert audit.rows[row_id - 1]["person_id"] is None
    assert counts.failed == 1


async def test_one_failing_row_does_not_abort_the_rows_behind_it():
    class FlakyMemory(FakeMemoryStore):
        async def feed(self, *, peer_id: str, text: str) -> bool:
            if text == "m1":
                raise RuntimeError("connection reset")
            return await super().feed(peer_id=peer_id, text=text)

    audit = FakeRecordStore()
    memory = FlakyMemory()
    for i in (1, 2, 3):
        await audit.append(message=_msg(body=f"m{i}", ts=i))

    counts = await _run(audit, memory, _persons())

    assert [text for _, text in memory.fed] == ["m2", "m3"]
    assert counts.fed == 2
    assert counts.failed == 1
    assert audit.rows[0]["person_id"] is None
    assert audit.rows[1]["person_id"] == "miles"


async def test_a_failing_audit_store_is_logged_and_counted_never_fatal():
    """Same discipline as `prune_loop`: the sweep dies, the process does not."""

    class BrokenAudit(FakeRecordStore):
        async def unattributed(self, *, limit: int):
            raise RuntimeError("db down")

    metrics = Metrics()
    counts = await _run(BrokenAudit(), FakeMemoryStore(), _persons(), metrics)

    assert counts == adoption.AdoptionCounts()
    assert metrics.get("nora_errors_total", stage="adoption") == 1


async def test_partial_progress_survives_a_mid_sweep_store_failure():
    """A row already fed AND marked stays adopted when a later write blows up."""

    class FailsOnSecondMark(FakeRecordStore):
        def __init__(self) -> None:
            super().__init__()
            self.marks = 0

        async def set_person_id(self, *, row_id: int, person_id: str) -> None:
            self.marks += 1
            if self.marks == 2:
                raise RuntimeError("db down")
            await super().set_person_id(row_id=row_id, person_id=person_id)

    audit = FailsOnSecondMark()
    metrics = Metrics()
    for i in (1, 2, 3):
        await audit.append(message=_msg(body=f"m{i}", ts=i))

    counts = await _run(audit, FakeMemoryStore(), _persons(), metrics)

    assert counts.marked == 1
    assert audit.rows[0]["person_id"] == "miles"
    assert audit.rows[1]["person_id"] is None
    assert metrics.get("nora_errors_total", stage="adoption") == 1


# ---------------------------------------------------------------------------
# (d) running twice does not double-feed
# ---------------------------------------------------------------------------


async def test_running_twice_does_not_double_feed_or_double_mark():
    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    metrics = Metrics()
    await audit.append(message=_msg(body="once"))

    first = await _run(audit, memory, _persons(), metrics)
    second = await _run(audit, memory, _persons(), metrics)

    assert memory.fed == [("miles", "once")]
    assert first.marked == 1
    assert second.scanned == 0
    assert second.marked == 0
    assert metrics.get("nora_lazy_adoptions_total") == 1


# ---------------------------------------------------------------------------
# scheduling, bounds and edge shapes
# ---------------------------------------------------------------------------


async def test_the_sweep_waits_before_touching_the_single_writer():
    """It must not compete with the first burst of ingest writes — SQLite's WAL
    permits exactly one writer and the ingest path DROPS on contention."""
    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    await audit.append(message=_msg())

    task = asyncio.create_task(adoption.backfill(
        audit=audit, memory=memory, persons=_persons(), metrics=Metrics(),
        start_delay_seconds=5.0,
    ))
    await asyncio.sleep(0.05)
    assert memory.fed == [], "the backfill started before its delay elapsed"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_the_scan_is_bounded_and_the_remainder_survives_to_the_next_run():
    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    for i in range(5):
        await audit.append(message=_msg(body=f"m{i}", ts=i))

    first = await _run(audit, memory, _persons(), limit=2)
    assert [t for _, t in memory.fed] == ["m0", "m1"]
    assert first.marked == 2

    second = await _run(audit, memory, _persons(), limit=2)
    assert [t for _, t in memory.fed] == ["m0", "m1", "m2", "m3"]
    assert second.marked == 2


async def test_an_empty_body_is_marked_but_never_fed():
    """An attachment-only row has no text to replay, only a person_id to
    record. Feeding "" would write an empty turn into the peer's memory."""
    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    row_id = await audit.append(message=_msg(body="   "))

    counts = await _run(audit, memory, _persons())

    assert memory.fed == []
    assert audit.rows[row_id - 1]["person_id"] == "miles"
    assert counts.fed == 0
    assert counts.marked == 1


async def test_adoption_against_the_real_postgres_store(pg_url):
    """The fake proves the logic; this proves the UPDATE actually lands in
    production's storage — `person_id` is the idempotence marker, and a marker
    that only works in memory is no marker at all."""
    from nora.infra.sql import SqlRecordStore, create_sql_engine, run_migrations

    run_migrations(pg_url)
    store = SqlRecordStore(create_sql_engine(pg_url))
    memory = FakeMemoryStore()
    try:
        await store.append(message=_msg(body="older", ts=1))
        await store.append(message=_msg(body="newer", ts=2))
        await store.append(message=_msg(body="stranger", ts=3, sender="+852999"))

        first = await _run(store, memory, _persons())
        assert [t for _, t in memory.fed] == ["older", "newer"]
        assert first.marked == 2

        # Reopened process, same database: nothing is re-fed.
        second = await _run(store, memory, _persons())
        assert [t for _, t in memory.fed] == ["older", "newer"]
        assert second.scanned == 1, "only the still-unmapped stranger remains"
        assert second.marked == 0
    finally:
        await store.aclose()


async def test_a_second_platform_resolves_through_the_same_person():
    """A2/DEC-14: every account a person owns lands on one peer, so adoption
    must key on the row's own platform, not assume Signal."""
    persons = Persons(
        by_person={"miles": MILES},
        index={("signal", "+852111"): "miles", ("telegram", "@miles"): "miles"},
    )
    audit = FakeRecordStore()
    memory = FakeMemoryStore()
    await audit.append(message=_msg(platform="telegram", sender="@miles", body="tg"))

    await _run(audit, memory, persons)

    assert memory.fed == [("miles", "tg")]
