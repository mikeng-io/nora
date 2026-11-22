"""Lazy adoption (`nora.app.adoption`): when a sender who was unmapped at
ingest is later added to identity.toml, their already-audited messages are
attributed to them at the next startup instead of staying anonymous forever.

Every test here asserts on real store state — which row carries a person, what
was counted — never on a coroutine existing.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from nora.app import adoption
from nora.domain.models import NormalizedMessage, PersonRef
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing.fakes import FakeRecordStore

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


async def _run(audit, persons, metrics=None, **kw):
    return await adoption.backfill(
        audit=audit, persons=persons,
        metrics=metrics if metrics is not None else Metrics(),
        start_delay_seconds=0.0, **kw,
    )


# ---------------------------------------------------------------------------
# (a) a row whose sender is now mapped gets marked
# ---------------------------------------------------------------------------


async def test_a_now_mapped_sender_is_marked():
    audit = FakeRecordStore()
    # Ingested while unmapped: audited with person_id NULL.
    row_id = await audit.append(message=_msg(body="i like congee"), person_id=None)

    counts = await _run(audit, _persons())

    assert audit.rows[row_id - 1]["person_id"] == "miles"
    assert counts == adoption.AdoptionCounts(scanned=1, resolved=1, marked=1)


async def test_adoption_is_counted():
    metrics = Metrics()
    audit = FakeRecordStore()
    await audit.append(message=_msg())
    await _run(audit, _persons(), metrics)
    assert metrics.get("nora_lazy_adoptions_total", platform="signal") == 1


async def test_the_counts_are_logged_even_when_nothing_was_found(caplog):
    """R-5's shape applied to a chore: "found nothing" and "never ran" must not
    look the same in the log."""
    caplog.set_level(logging.INFO, logger="nora.app.adoption")
    await _run(FakeRecordStore(), _persons())
    assert any("lazy adoption: scanned 0" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# (b) a row whose sender is still unmapped is left alone
# ---------------------------------------------------------------------------


async def test_a_still_unmapped_sender_is_left_alone():
    audit = FakeRecordStore()
    row_id = await audit.append(message=_msg(sender="+852999"))

    counts = await _run(audit, _persons())

    assert audit.rows[row_id - 1]["person_id"] is None, "an unresolved row must stay unmarked"
    assert counts == adoption.AdoptionCounts(scanned=1, resolved=0, marked=0)


async def test_an_already_attributed_row_never_enters_the_scan():
    """`person_id` is the marker."""
    audit = FakeRecordStore()
    await audit.append(message=_msg(), person_id="miles")

    counts = await _run(audit, _persons())

    assert counts.scanned == 0


async def test_noras_own_rows_are_never_adopted():
    audit = FakeRecordStore()
    await audit.append(message=_msg(body="her reply"), is_nora=True)

    counts = await _run(audit, _persons())

    assert counts.scanned == 0


async def test_an_attachment_only_row_is_marked_like_any_other():
    """Attribution does not depend on there being any text."""
    audit = FakeRecordStore()
    row_id = await audit.append(message=_msg(body="   "))

    counts = await _run(audit, _persons())

    assert audit.rows[row_id - 1]["person_id"] == "miles"
    assert counts.marked == 1


# ---------------------------------------------------------------------------
# (c) a failure is counted and never fatal
# ---------------------------------------------------------------------------


async def test_a_failing_audit_store_is_logged_and_counted_never_fatal():
    """Same discipline as `prune_loop`: the sweep dies, the process does not."""

    class BrokenAudit(FakeRecordStore):
        async def unattributed(self, *, limit: int):
            raise RuntimeError("db down")

    metrics = Metrics()
    counts = await _run(BrokenAudit(), _persons(), metrics)

    assert counts == adoption.AdoptionCounts()
    assert metrics.get("nora_errors_total", stage="adoption") == 1


async def test_partial_progress_survives_a_mid_sweep_store_failure():
    """A row already marked stays adopted when a later write blows up."""

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

    counts = await _run(audit, _persons(), metrics)

    assert counts.marked == 1
    assert audit.rows[0]["person_id"] == "miles"
    assert audit.rows[1]["person_id"] is None
    assert metrics.get("nora_errors_total", stage="adoption") == 1


# ---------------------------------------------------------------------------
# (d) running twice does not double-mark
# ---------------------------------------------------------------------------


async def test_running_twice_does_not_double_mark():
    audit = FakeRecordStore()
    metrics = Metrics()
    await audit.append(message=_msg(body="once"))

    first = await _run(audit, _persons(), metrics)
    second = await _run(audit, _persons(), metrics)

    assert first.marked == 1
    assert second.scanned == 0
    assert second.marked == 0
    assert metrics.get("nora_lazy_adoptions_total") == 1


# ---------------------------------------------------------------------------
# scheduling, bounds and edge shapes
# ---------------------------------------------------------------------------


async def test_the_sweep_waits_before_touching_the_single_writer():
    """It must not compete with the first burst of ingest writes — a habit from
    SQLite's single-writer WAL (the ingest path DROPS on contention), still
    free under Postgres."""
    audit = FakeRecordStore()
    row_id = await audit.append(message=_msg())

    task = asyncio.create_task(adoption.backfill(
        audit=audit, persons=_persons(), metrics=Metrics(),
        start_delay_seconds=5.0,
    ))
    await asyncio.sleep(0.05)
    assert audit.rows[row_id - 1]["person_id"] is None, (
        "the backfill started before its delay elapsed"
    )
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_the_scan_is_bounded_and_the_remainder_survives_to_the_next_run():
    audit = FakeRecordStore()
    for i in range(5):
        await audit.append(message=_msg(body=f"m{i}", ts=i))

    def marked() -> list[str]:
        return [r["body"] for r in audit.rows if r["person_id"] == "miles"]

    first = await _run(audit, _persons(), limit=2)
    assert marked() == ["m0", "m1"], "oldest first"
    assert first.marked == 2

    second = await _run(audit, _persons(), limit=2)
    assert marked() == ["m0", "m1", "m2", "m3"]
    assert second.marked == 2


async def test_adoption_against_the_real_postgres_store(clean_pg_url):
    """The fake proves the logic; this proves the UPDATE actually lands in
    production's storage — `person_id` is the idempotence marker, and a marker
    that only works in memory is no marker at all."""
    from nora.infra.sql import SqlRecordStore, create_sql_engine, run_migrations

    run_migrations(clean_pg_url)
    store = SqlRecordStore(create_sql_engine(clean_pg_url))
    try:
        await store.append(message=_msg(body="older", ts=1))
        await store.append(message=_msg(body="newer", ts=2))
        await store.append(message=_msg(body="stranger", ts=3, sender="+852999"))

        first = await _run(store, _persons())
        assert first.marked == 2

        # Reopened process, same database: nothing is re-marked.
        second = await _run(store, _persons())
        assert second.scanned == 1, "only the still-unmapped stranger remains"
        assert second.marked == 0
    finally:
        await store.aclose()


async def test_a_second_platform_resolves_through_the_same_person():
    """A2/DEC-14: every account a person owns lands on one person, so adoption
    must key on the row's own platform, not assume Signal."""
    persons = Persons(
        by_person={"miles": MILES},
        index={("signal", "+852111"): "miles", ("telegram", "@miles"): "miles"},
    )
    audit = FakeRecordStore()
    row_id = await audit.append(message=_msg(platform="telegram", sender="@miles", body="tg"))

    await _run(audit, persons)

    assert audit.rows[row_id - 1]["person_id"] == "miles"
