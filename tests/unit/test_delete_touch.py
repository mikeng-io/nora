"""The touch stage hands a found delete to the `DeleteWithdrawer`, counts how it
ended, and never withdraws on anything else; `PendingDeletes` holds a delete that
overtook its message. In-memory, so the sequencing is pinned without a database;
`tests/infra/test_delete_withdraws.py` is the same behaviour against Postgres.
"""

from __future__ import annotations

from typing import Any

from nora.domain.models import EditTarget, NormalizedMessage, Touch
from nora.domain.ports import DeleteWithdrawal
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import TriggerTextPolicy
from nora.ingest.stages import touch as touch_stage
from nora.ingest.stages.touch import PendingDeletes
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import permissive_policy
from nora.testing import FakeDeleteWithdrawer, FakeRecordStore

ROOM = "g1"


def _said(ts: int, handle: str, *, sender: str = "w1", room: str = ROOM) -> NormalizedMessage:
    return NormalizedMessage(
        "whatsapp", room, "group", sender, "Ada", ts, "words", {}, message_id=handle,
    )


def _delete(handle: str = "", *, sent_ts: int | None = None, sender: str = "w1",
            room: str = ROOM, kind: str = "delete") -> NormalizedMessage:
    target = EditTarget(sent_ts=sent_ts) if sent_ts is not None else EditTarget(message_id=handle)
    touch = Touch(kind="delete", target=target) if kind == "delete" else Touch(
        kind="reaction", target=target, emoji="👍",
    )
    return NormalizedMessage("whatsapp", room, "group", sender, None, 5, "", {}, touch=touch)


class _Broken:
    """A withdrawer that broke its promise never to raise."""

    async def withdraw(self, *, message_id: int, now_ms: int) -> DeleteWithdrawal:
        raise RuntimeError("down")


async def _run(msg: NormalizedMessage, audit: FakeRecordStore, withdrawer: Any,
               metrics: Metrics) -> touch_stage.TouchResult:
    assert msg.touch is not None
    return await touch_stage.run(
        message=msg, touch=msg.touch, audit=audit,
        persons=Persons(by_person={}, index={}), metrics=metrics, withdrawer=withdrawer,
    )


def _count(metrics: Metrics, outcome: str) -> float:
    return metrics.get("nora_delete_withdrawals_total", platform="whatsapp", outcome=outcome)


async def test_a_found_delete_is_withdrawn_and_counted_by_its_outcome():
    audit = FakeRecordStore()
    w, m = FakeDeleteWithdrawer(audit), Metrics()
    row = await audit.append(message=_said(1, "H1"))
    first = await _run(_delete("H1"), audit, w, m)
    assert first.ending == "delete_recorded"
    assert w.calls == [row]
    assert first.withdrawal is not None and first.withdrawal.outcome == "hidden"
    again = await _run(_delete("H1"), audit, w, m)           # redelivered
    assert again.withdrawal is not None and again.withdrawal.outcome == "already_hidden"
    assert w.calls == [row, row]
    assert (_count(m, "hidden"), _count(m, "already_hidden")) == (1, 1)


async def test_a_reaction_withdraws_nothing():
    audit = FakeRecordStore()
    w, m = FakeDeleteWithdrawer(audit), Metrics()
    await audit.append(message=_said(1, "H1"))
    done = await _run(_delete("H1", kind="reaction"), audit, w, m)
    assert done.ending == "reaction_recorded"
    assert w.calls == []


async def test_a_delete_of_nothing_on_record_is_not_stored_and_calls_nothing():
    audit = FakeRecordStore()
    w, m = FakeDeleteWithdrawer(audit), Metrics()
    done = await _run(_delete("NOPE"), audit, w, m)
    assert done.ending == "delete_orphaned"
    assert w.calls == [] and _count(m, "not_stored") == 1


async def test_a_delete_by_someone_else_is_refused_and_counted_where_only_authors_delete():
    for platform, ts, target in (
        ("signal", 1000, EditTarget(sent_ts=1000)),
        ("whatsapp", 5, EditTarget(message_id="H9")),
    ):
        audit = FakeRecordStore()
        w, m = FakeDeleteWithdrawer(audit), Metrics()
        await audit.append(message=NormalizedMessage(
            platform, ROOM, "group", "author", "Ada", ts, "words", {},
            message_id=target.message_id))
        msg = NormalizedMessage(
            platform, ROOM, "group", "stranger", None, 5, "", {},
            touch=Touch(kind="delete", target=target),
        )
        assert msg.touch is not None
        done = await touch_stage.run(
            message=msg, touch=msg.touch, audit=audit,
            persons=Persons(by_person={}, index={}), metrics=m, withdrawer=w,
        )
        assert done.ending == "delete_refused", platform
        assert w.calls == [], platform
        assert m.get("nora_delete_withdrawals_total", platform=platform,
                     outcome="refused_not_author") == 1
        assert audit.deletes == [], "a refused delete leaves no record"


async def test_discord_and_instagram_deletes_name_nobody_and_are_not_author_checked():
    for platform in ("discord", "instagram"):
        audit = FakeRecordStore()
        w, m = FakeDeleteWithdrawer(audit), Metrics()
        await audit.append(message=NormalizedMessage(
            platform, ROOM, "group", "author", "Ada", 1, "words", {}, message_id="M1"))
        msg = NormalizedMessage(
            platform, ROOM, "group", "", None, 5, "", {},
            touch=Touch(kind="delete", target=EditTarget(message_id="M1")),
        )
        assert msg.touch is not None
        done = await touch_stage.run(
            message=msg, touch=msg.touch, audit=audit,
            persons=Persons(by_person={}, index={}), metrics=m, withdrawer=w,
        )
        assert done.ending == "delete_recorded", platform
        assert len(w.calls) == 1


async def test_a_withdrawer_that_raises_is_cascade_failed_and_the_delete_is_still_recorded():
    audit, m = FakeRecordStore(), Metrics()
    await audit.append(message=_said(1, "H1"))
    done = await _run(_delete("H1"), audit, _Broken(), m)
    assert done.ending == "delete_recorded"
    assert _count(m, "cascade_failed") == 1
    assert len(audit.deletes) == 1


async def test_no_withdrawer_changes_nothing_and_counts_nothing():
    audit, m = FakeRecordStore(), Metrics()
    await audit.append(message=_said(1, "H1"))
    done = await _run(_delete("H1"), audit, None, m)
    assert done.ending == "delete_recorded" and done.withdrawal is None
    assert all(_count(m, o) == 0 for o in ("hidden", "already_hidden", "not_stored"))


# --- PendingDeletes ---------------------------------------------------------


def _hold(held: PendingDeletes, delete: NormalizedMessage, *, person_id: str | None = None) -> int:
    assert delete.touch is not None
    return held.add(delete, delete.touch, person_id=person_id)


def test_a_held_delete_matches_by_handle_or_by_signal_timestamp_in_its_own_room():
    held = PendingDeletes()
    _hold(held, _delete("H1"))
    _hold(held, _delete(sent_ts=777))
    assert held.take(_said(1, "H2"), person_id=None) == []
    assert held.take(_said(1, "H1", room="other"), person_id=None) == []
    assert len(held.take(_said(777, ""), person_id=None)) == 1          # by timestamp
    assert len(held.take(_said(1, "H1"), person_id=None)) == 1          # by handle
    assert held.take(_said(1, "H1"), person_id=None) == [], "taken once"


def test_a_held_delete_never_matches_on_an_empty_handle():
    """A Signal delete names a timestamp, so its held target carries no handle; a
    message with no handle must not match it on `"" == ""`."""
    held = PendingDeletes()
    _hold(held, _delete(sent_ts=777))
    assert held.take(_said(5, ""), person_id=None) == []
    assert held.matches(_said(5, ""), person_id=None) is False
    assert held.matches(_said(777, ""), person_id=None) is True


def test_a_held_delete_is_per_platform():
    held = PendingDeletes()
    _hold(held, _delete("H1"))                       # whatsapp
    other = NormalizedMessage("discord", ROOM, "group", "w1", "Ada", 1, "x", {}, message_id="H1")
    assert held.take(other, person_id=None) == []
    assert len(held.take(_said(1, "H1"), person_id=None)) == 1


def test_on_an_author_only_platform_the_arriving_message_must_be_the_deleters_own():
    """Two members, one millisecond: only the deleter's message may take the hold, by
    platform id or by the person both ids resolve to."""
    held = PendingDeletes()
    _hold(held, _delete(sent_ts=777, sender="w1"), person_id="ada")
    assert held.take(_said(777, "", sender="w2"), person_id=None) == []
    assert held.take(_said(777, "", sender="w2"), person_id="ben") == []
    assert len(held.take(_said(777, "", sender="w1-lid"), person_id="ada")) == 1


def test_a_held_delete_expires_and_the_hold_is_bounded_and_each_loss_is_reported():
    now = [0.0]
    lost: list[str] = []
    held = PendingDeletes(
        ttl_seconds=10, max_entries=2, clock=lambda: now[0],
        on_expired=lambda m: lost.append(m.touch.target.message_id if m.touch else ""),
    )
    for handle in ("A", "B", "C"):
        _hold(held, _delete(handle))
    assert lost == ["A"], "the oldest was pushed out past the bound"
    assert held.take(_said(1, "A"), person_id=None) == []
    assert len(held.take(_said(1, "B"), person_id=None)) == 1
    now[0] = 11.0
    assert held.take(_said(1, "C"), person_id=None) == [], "expired"
    assert lost == ["A", "C"]


def test_discarding_says_whether_it_was_still_held():
    held = PendingDeletes()
    entry = _hold(held, _delete("H1"))
    assert held.discard(entry) is True
    assert held.discard(entry) is False


async def test_a_delete_older_than_the_hold_does_not_withdraw_a_late_message():
    """The race is seconds wide. Past the hold, a delete is the orphan it always
    was, so a message that lands afterwards is recorded and stays."""
    audit = FakeRecordStore()
    w, m = FakeDeleteWithdrawer(audit), Metrics()
    now = [0.0]
    pipeline = IngestPipeline(
        audit=audit, memory=None,  # type: ignore[arg-type]
        persons=Persons(by_person={}, index={}), policy=TriggerTextPolicy("nora.000"),
        allowlist=permissive_policy(), metrics=m, spawn=lambda c: c.close(),
        delete_withdrawer=w,
    )
    pipeline._pending_deletes = PendingDeletes(
        ttl_seconds=10, clock=lambda: now[0], on_expired=pipeline._hold_expired,
    )
    await pipeline.run(message=_delete("LATE"))
    now[0] = 11.0
    result = await pipeline.run(message=_said(9, "LATE"))
    assert result.message is not None, "an ordinary message: recorded and handled"
    assert w.calls == []
    assert m.get("nora_delete_withdrawals_total", platform="whatsapp", outcome="hold_expired") == 1
