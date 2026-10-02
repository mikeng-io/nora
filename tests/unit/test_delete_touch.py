"""The touch stage hands a found delete to the `DeleteWithdrawer`, counts how it
ended, and never withdraws on anything else; a delete whose target is not on record is
kept as an orphan for its message. In-memory, so the sequencing is pinned without a database;
`tests/infra/test_delete_withdraws.py` is the same behaviour against Postgres.
"""

from __future__ import annotations

from typing import Any

from nora.domain.models import EditTarget, NormalizedMessage, Touch
from nora.domain.ports import DeleteWithdrawal
from nora.ingest.pipeline import IngestPipeline
from nora.ingest.policy import TriggerTextPolicy
from nora.ingest.stages import touch as touch_stage
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


# --- a delete whose target is not on record ---------------------------------


async def test_a_delete_of_nothing_on_record_is_kept_as_an_orphan_for_its_message():
    audit, m = FakeRecordStore(), Metrics()
    w = FakeDeleteWithdrawer(audit)
    done = await _run(_delete("LATER"), audit, w, m)
    assert done.ending == "delete_orphaned"
    assert [(o.platform, o.conversation_id, o.handle, o.deleter_id) for o in audit.orphans] == [
        ("whatsapp", ROOM, "LATER", "w1"),
    ]
    assert w.calls == [] and _count(m, "not_stored") == 1


async def test_a_delete_whose_lookup_failed_is_kept_not_dropped():
    audit, m = FakeRecordStore(), Metrics()
    await audit.append(message=_said(1, "H1"))
    real = audit.find_edited
    calls = {"n": 0}

    async def flaky(**kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("db hiccup")
        return await real(**kw)

    audit.find_edited = flaky  # type: ignore[method-assign]
    done = await _run(_delete("H1"), audit, FakeDeleteWithdrawer(audit), m)
    assert done.ending == "delete_orphaned"
    assert len(audit.orphans) == 1, "«could not look» is not «not there»: it is kept"


async def test_a_delete_that_finds_its_message_on_the_second_look_discards_its_orphan():
    audit, m = FakeRecordStore(), Metrics()
    w = FakeDeleteWithdrawer(audit)
    real = audit.find_edited
    calls = {"n": 0}

    async def late(**kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return None                       # the message lands between the two looks
        return await real(**kw)

    await audit.append(message=_said(1, "H1"))
    audit.find_edited = late  # type: ignore[method-assign]
    done = await _run(_delete("H1"), audit, w, m)
    assert done.ending == "delete_recorded"
    assert audit.orphans == [] and len(w.calls) == 1


async def test_nothing_is_kept_for_a_reaction_or_without_a_withdrawer():
    audit, m = FakeRecordStore(), Metrics()
    await _run(_delete("NOPE", kind="reaction"), audit, FakeDeleteWithdrawer(audit), m)
    await _run(_delete("NOPE"), audit, None, m)
    assert audit.orphans == []


async def test_a_delete_that_names_nothing_is_not_kept():
    audit, m = FakeRecordStore(), Metrics()
    msg = NormalizedMessage(
        "whatsapp", ROOM, "group", "w1", None, 5, "", {},
        touch=Touch(kind="delete", target=EditTarget()),
    )
    await touch_stage.run(
        message=msg, touch=msg.touch, audit=audit,  # type: ignore[arg-type]
        persons=Persons(by_person={}, index={}), metrics=m,
        withdrawer=FakeDeleteWithdrawer(audit),
    )
    assert audit.orphans == []


async def test_a_pipeline_applies_a_kept_delete_when_its_message_is_audited():
    audit, w, m = FakeRecordStore(), None, Metrics()
    w = FakeDeleteWithdrawer(audit)
    pipeline = IngestPipeline(
        audit=audit, memory=None,  # type: ignore[arg-type]
        persons=Persons(by_person={}, index={}), policy=TriggerTextPolicy("nora.000"),
        allowlist=permissive_policy(), metrics=m, spawn=lambda c: c.close(),
        delete_withdrawer=w,
    )
    await pipeline.run(message=_delete("LATE"))
    assert len(audit.orphans) == 1
    result = await pipeline.run(message=_said(9, "LATE"))
    assert result.message is None and result.decision is None
    assert audit.orphans == [], "applied, then discarded"
    assert w.calls == [1]
    assert _count(m, "applied_from_orphan") == 1
