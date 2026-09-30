"""`nora cognition compare`'s shadow-week read against real Postgres (review B1,
D5, 2026-09-30): the Cognition session's would-writes against what each LIVE
clocked writer wrote, per tier and per writer. Each test seeds live-writer rows
and would-write rows and reads the verdict back; the tolerance, the closing
rule and the fail-closed ERROR are each pinned by name."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa

from nora.app.cognition_compare import CompareWindow, run_shadow_compare
from nora.domain.cognition_shadow_compare import RoomDirectory, report_json, verdict_lines
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import (
    COGNITION_REMIND_MIRROR,
    COGNITION_SESSION,
    COGNITION_SESSION_CLOSE,
    COGNITION_WOULD_WRITE,
    CULTURE_REVISION,
    DECISION_LOG,
    EVENT_REVISION,
    EVENTS,
    NOTE,
    NOTE_REVISION,
    REMINDERS,
    STANDING_REVISION,
)

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
HOUR = 3_600_000
WINDOW = CompareWindow(
    since_ms=int((T0 - timedelta(days=1)).timestamp() * 1000),
    until_ms=int((T0 + timedelta(days=1)).timestamp() * 1000),
)
DIRECTORY = RoomDirectory(labels={"room-a": "Dinner", "room-b": "Work"}, unit_rooms={
    "u": frozenset({"room-a"}), "v": frozenset({"room-b"}),
})


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _run(engine: Any, *, tolerance_min: int = 60, unit: str | None = None):
    return await run_shadow_compare(
        engine, WINDOW, tolerance_ms=tolerance_min * 60_000, unit=unit, directory=DIRECTORY,
    )


def _writer(report: Any, tier: str, writer: str) -> Any:
    tr = next(t for t in report.tiers if t.tier == tier)
    return tr, next(w for w in tr.writers if w.writer == writer)


async def _would(engine: Any, at: datetime, tier: str, verb: str, *, room: str = "room-a",
                 candidate: dict | None = None, outcome: str = "would_commit",
                 unit: str = "u", session_id: int | None = None) -> int:
    async with engine.begin() as conn:
        return (await conn.execute(sa.insert(COGNITION_WOULD_WRITE).values(
            session_id=session_id, unit=unit, tier=tier, verb=verb, platform="signal",
            conversation_id=room, args={}, outcome=outcome,
            refusal_reason=None if outcome == "would_commit" else "bad_mode",
            candidate=(candidate or {}) if outcome == "would_commit" else None,
            recorded_at=at,
        ).returning(COGNITION_WOULD_WRITE.c.id))).scalar_one()


async def _note(engine: Any, *, by: str, at: datetime, room: str = "room-a", unit: str = "u",
                anchor_date: int | None = 20260930, parent: bool = False,
                note_id: str | None = None) -> tuple[str, str]:
    """A note and one revision of it by `by`. `parent=True` writes a LATER
    revision on an existing note (`note_id`)."""
    async with engine.begin() as conn:
        if note_id is None:
            note_id = str(uuid4())
            head = str(uuid4())
            await conn.execute(sa.insert(NOTE).values(
                id=note_id, unit=unit, title="t", description="d", uncertainty="u",
                closing_condition="c", anchor_date=anchor_date, established_in=room,
                created_at=_ms(at), head_revision_id=head,
            ))
        else:
            head = str(uuid4())
        first_parent = None
        if parent:
            first_parent = str(uuid4())
            await conn.execute(sa.insert(NOTE_REVISION).values(
                id=first_parent, note_id=note_id, body="b", written_at=_ms(at) - 1,
                established_by="tag:someone", recorded_at=at - timedelta(days=30),
            ))
        await conn.execute(sa.insert(NOTE_REVISION).values(
            id=head, note_id=note_id, body="b", parent_revision_id=first_parent,
            written_at=_ms(at), established_by=by, recorded_at=at,
        ))
    return note_id, head


async def _standing(engine: Any, at: datetime, fold_count: int, *, room: str = "room-a",
                    source: str = "fold") -> str:
    async with engine.begin() as conn:
        return str((await conn.execute(sa.insert(STANDING_REVISION).values(
            platform="signal", conversation_id=room, body="b", fold_count=fold_count,
            updated_at=_ms(at), source=source, recorded_at=at,
        ).returning(STANDING_REVISION.c.id))).scalar_one())


async def _event(engine: Any, at: datetime, *, title: str = "Dinner", room: str = "room-a",
                 written_by: str = "tag", event_id: Any = None, parent: bool = False,
                 unit: str = "u") -> Any:
    async with engine.begin() as conn:
        if event_id is None:
            event_id = uuid4()
            await conn.execute(sa.insert(EVENTS).values(
                id=event_id, conversation_id=room, title=title, created_by="p",
                status="proposed", created_at=at, unit_id=unit,
            ))
        first = None
        if parent:
            first = uuid4()
            await conn.execute(sa.insert(EVENT_REVISION).values(
                id=first, event_id=event_id, status="proposed", title=title, written_by="tag",
                written_at=at - timedelta(days=30), recorded_at=at - timedelta(days=30),
            ))
        await conn.execute(sa.insert(EVENT_REVISION).values(
            id=uuid4(), event_id=event_id, parent_revision_id=first, status="proposed",
            title=title, written_by=written_by, written_at=at, recorded_at=at,
        ))
    return event_id


async def _session_close(engine: Any, at: datetime, reason: str, *, room: str = "room-a",
                         unit: str = "u", agenda: Any = None) -> None:
    async with engine.begin() as conn:
        sid = (await conn.execute(sa.insert(COGNITION_SESSION).values(
            unit=unit, mode="shadow", started_at_ms=_ms(at), from_stimulus_id=1,
            through_stimulus_id=1, situation={},
            agenda=agenda if agenda is not None else {"items": [
                {"kind": "message", "platform": "signal", "conversation_id": room}]},
            recorded_at=at,
        ).returning(COGNITION_SESSION.c.id))).scalar_one()
        await conn.execute(sa.insert(COGNITION_SESSION_CLOSE).values(
            session_id=sid, item_index=0, kind="message", reason=reason, recorded_at=at,
        ))


# ---- note tier ------------------------------------------------------------

async def test_a_note_update_matches_the_revision_of_the_same_note_by_its_writer(engine):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _would(engine, T0 + timedelta(minutes=20), "note", "note_update",
                 candidate={"note_id": note_id})
    report = await _run(engine)
    tier, curation = _writer(report, "note", "note-curation")
    assert curation.matched == 1 and not curation.clocked_only and not tier.cognition_only


async def test_a_note_create_matches_a_first_revision_in_the_same_room_with_the_same_anchor(engine):
    await _note(engine, by="dream:note-candidates:k:1", at=T0, anchor_date=20260930)
    await _would(engine, T0, "note", "note_create", candidate={
        "anchor_date": 20260930, "anchor_entity_ids": []})
    tier, w = _writer(await _run(engine), "note", "note-candidates")
    assert w.matched == 1 and not w.clocked_only and not tier.cognition_only


async def test_a_create_in_another_room_or_with_another_anchor_does_not_match(engine):
    await _note(engine, by="dream:note-candidates:k:1", at=T0, anchor_date=20260930)
    await _would(engine, T0, "note", "note_create", room="room-b", candidate={
        "anchor_date": 20260930, "anchor_entity_ids": []})
    await _would(engine, T0, "note", "note_create", candidate={
        "anchor_date": 20261001, "anchor_entity_ids": []})
    tier, w = _writer(await _run(engine), "note", "note-candidates")
    assert w.matched == 0 and len(w.clocked_only) == 1 and len(tier.cognition_only) == 2


async def test_a_match_outside_the_tolerance_is_two_unmatched_rows(engine):
    note_id, _ = await _note(engine, by="dream:note-merge:k:1", at=T0, parent=True)
    await _would(engine, T0 + timedelta(hours=3), "note", "note_update",
                 candidate={"note_id": note_id})
    tier, w = _writer(await _run(engine, tolerance_min=60), "note", "note-merge")
    assert w.matched == 0 and len(w.clocked_only) == 1 and len(tier.cognition_only) == 1
    tier, w = _writer(await _run(engine, tolerance_min=240), "note", "note-merge")
    assert w.matched == 1


async def test_one_would_write_explains_only_one_clocked_write(engine):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _note(engine, by="dream:note-merge:k:1", at=T0 + timedelta(minutes=1),
                parent=True, note_id=note_id)
    await _would(engine, T0, "note", "note_update", candidate={"note_id": note_id})
    report = await _run(engine)
    _, curation = _writer(report, "note", "note-curation")
    _, merge = _writer(report, "note", "note-merge")
    assert curation.matched + merge.matched == 1
    assert len(curation.clocked_only) + len(merge.clocked_only) == 1


async def test_the_tag_path_is_not_a_clocked_writer(engine):
    await _note(engine, by="tag:person-1", at=T0)
    report = await _run(engine)
    note = next(t for t in report.tiers if t.tier == "note")
    assert all(not w.clocked_only for w in note.writers)


async def test_a_refused_would_write_is_not_a_write_and_is_named_beside_the_clocked_row(engine):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _would(engine, T0, "note", "note_update", outcome="refused")
    tier, w = _writer(await _run(engine), "note", "note-curation")
    assert w.matched == 0 and not tier.cognition_only and tier.refused == 1
    assert w.clocked_only[0].refused_nearby == 1 and note_id


async def test_a_note_update_does_not_match_a_first_revision_of_the_same_note(engine):
    note_id, _ = await _note(engine, by="dream:note-candidates:k:1", at=T0)
    await _would(engine, T0, "note", "note_update", candidate={"note_id": note_id})
    tier, w = _writer(await _run(engine), "note", "note-candidates")
    assert w.matched == 0 and len(w.clocked_only) == 1 and len(tier.cognition_only) == 1


# ---- deliberately closed ---------------------------------------------------

@pytest.mark.parametrize("reason", ["no_change", "waiting", "covered"])
async def test_a_close_in_the_same_room_explains_a_clocked_only_row(engine, reason):
    await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0 + timedelta(minutes=10), reason)
    _, w = _writer(await _run(engine), "note", "note-curation")
    assert [g.closed_by for g in w.clocked_only] == [reason] and w.closed == 1 and w.open == 0


@pytest.mark.parametrize("reason", ["written", "struck"])
async def test_written_and_struck_do_not_explain_anything(engine, reason):
    await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0, reason)
    _, w = _writer(await _run(engine), "note", "note-curation")
    assert w.closed == 0 and w.open == 1


async def test_a_close_in_another_room_or_outside_the_tolerance_does_not_close_it(engine):
    await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0, "no_change", room="room-b")
    await _session_close(engine, T0 + timedelta(hours=5), "no_change")
    _, w = _writer(await _run(engine, tolerance_min=60), "note", "note-curation")
    assert w.open == 1


async def test_a_close_with_an_unreadable_agenda_explains_nothing_and_says_so(engine):
    await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0, "no_change", agenda={"items": []})
    tier, w = _writer(await _run(engine), "note", "note-curation")
    assert w.open == 1 and any("unreadable agenda" in n for n in tier.notes)


# ---- standing ---------------------------------------------------------------

async def test_standing_writers_are_told_apart_by_fold_count_and_match_by_room(engine):
    await _standing(engine, T0 - timedelta(days=3), 1)                   # debrief, first
    await _standing(engine, T0, 2)                                       # debrief
    await _standing(engine, T0 + timedelta(minutes=5), 2, room="room-a")  # ingest fold: same count
    await _standing(engine, T0 + timedelta(minutes=6), 2, source="operator")  # not clocked
    await _would(engine, T0 + timedelta(minutes=2), "standing", "standing_revise")
    report = await _run(engine)
    tier, debrief = _writer(report, "standing", "standing-debrief")
    _, ingest = _writer(report, "standing", "standing-ingest-fold")
    assert debrief.matched == 1 and not debrief.clocked_only
    assert ingest.matched == 0 and len(ingest.clocked_only) == 1 and not tier.cognition_only


async def test_a_would_write_with_no_live_write_is_cognition_only(engine):
    wid = await _would(engine, T0, "standing", "standing_revise", room="room-b")
    tier, _ = _writer(await _run(engine), "standing", "standing-debrief")
    assert [g.id for g in tier.cognition_only] == [str(wid)]


# ---- event ------------------------------------------------------------------

async def test_event_propose_matches_by_room_and_title_and_revise_by_event_id(engine):
    await _event(engine, T0, title="Dinner")
    eid = await _event(engine, T0, title="Lunch", parent=True)
    await _would(engine, T0, "event", "event_propose", candidate={"title": "dinner"})
    await _would(engine, T0, "event", "event_revise", candidate={"id": str(eid)})
    tier, w = _writer(await _run(engine), "event", "event:tag")
    assert w.matched == 2 and not w.clocked_only and not tier.cognition_only


async def test_a_backfill_revision_is_not_a_live_write(engine):
    await _event(engine, T0, written_by="backfill")
    tier, w = _writer(await _run(engine), "event", "event:tag")
    assert w.matched == 0 and not w.clocked_only
    assert [x.writer for x in tier.writers] == ["event:tag"]


# ---- culture ----------------------------------------------------------------

async def test_culture_writes_are_clocked_only_by_construction_and_never_closed(engine):
    async with engine.begin() as conn:
        await conn.execute(sa.insert(CULTURE_REVISION).values(
            culture_key="room-a", body="b", written_at=_ms(T0),
            established_by="dream:culture:k:1", recorded_at=T0))
    await _session_close(engine, T0, "no_change")
    tier, w = _writer(await _run(engine), "culture", "culture")
    assert len(w.clocked_only) == 1 and w.closed == 0
    assert any("no culture verb" in n for n in tier.notes)


# ---- the act tiers -----------------------------------------------------------

async def _mirror(engine: Any, note: str, at: datetime, room: str = "room-a") -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.insert(COGNITION_REMIND_MIRROR).values(
            unit="u", verb="remind", reminder_ref=f"r-{note}", platform="signal",
            conversation_id=room, subject_rows=[f"note:{note}"], at_ms=_ms(at),
            outcome="recorded", recorded_at=at))


async def _reminder(engine: Any, payload: dict, at: datetime, room: str = "room-a") -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.insert(REMINDERS).values(
            id=uuid4(), requester_person_id="nora", platform="signal", conversation_id=room,
            conversation_type="group", text="t", created_at=at, due_at=at, state="pending",
            kind="proactive", payload=payload))


async def test_proactive_reminders_match_a_mirrored_remind_by_room_and_note(engine):
    await _mirror(engine, "n1", T0)
    await _reminder(engine, {"note_id": "n1"}, T0)
    await _reminder(engine, {"note_id": "n2"}, T0)
    await _reminder(engine, {}, T0)
    await _mirror(engine, "n9", T0)
    tier, w = _writer(await _run(engine), "act", "proactive")
    assert w.matched == 1 and len(w.clocked_only) == 2 and len(tier.cognition_only) == 1
    assert sorted(g.keyless for g in w.clocked_only) == [False, True]


async def test_a_proactive_decide_remind_decision_is_its_own_writer(engine):
    await _mirror(engine, "n1", T0)
    async with engine.begin() as conn:
        rows = [("remind", "n1"), ("remind", "n3"), ("decline", "n1")]
        for i, (verdict, note) in enumerate(rows):
            await conn.execute(sa.insert(DECISION_LOG).values(
                id=f"d{i}", writer="proactive_decide", platform="signal",
                conversation_id="room-a", unit="u", note_id=note, verdict=verdict,
                reason="", ts=_ms(T0), recorded_at=T0))
    report = await _run(engine)
    _, decide = _writer(report, "act-decision", "proactive-decide")
    assert decide.matched == 1 and [g.key for g in decide.clocked_only] == ["n3"]


# ---- unit filter --------------------------------------------------------------

async def test_a_unit_filter_keeps_only_that_units_rows_in_every_tier(engine):
    await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True, unit="u")
    await _note(engine, by="dream:note-curation:k:2", at=T0, parent=True, unit="v", room="room-b")
    await _standing(engine, T0, 1, room="room-a")
    await _standing(engine, T0, 1, room="room-b")
    await _reminder(engine, {"note_id": "x"}, T0, room="room-b")
    report = await _run(engine, unit="u")
    assert len(_writer(report, "note", "note-curation")[1].clocked_only) == 1
    assert len(_writer(report, "standing", "standing-debrief")[1].clocked_only) == 1
    assert _writer(report, "act", "proactive")[1].clocked_only == ()


# ---- fail closed ---------------------------------------------------------------

async def test_an_unreadable_source_is_ERROR_and_never_zero(engine):
    async with engine.begin() as conn:
        await conn.execute(sa.text("ALTER TABLE standing_revision RENAME TO standing_revision_x"))
    report = await _run(engine)
    tier = next(t for t in report.tiers if t.tier == "standing")
    assert tier.status == "error" and report.any_error
    assert all(w.status == "error" and w.matched is None and w.closed is None for w in tier.writers)
    assert any(line.startswith("standing-debrief: ERROR") for line in verdict_lines(report))
    note = next(t for t in report.tiers if t.tier == "note")
    assert note.status == "compared"
    # the ERROR names the exception CLASS only: a message can quote a row
    assert tier.error and tier.error.isidentifier() and "standing_revision" not in tier.error
    data = report_json(report, DIRECTORY)
    standing = next(t for t in data["tiers"] if t["tier"] == "standing")
    first = standing["writers"][0]
    assert first["matched"] is None and first["clocked_only"] is None


async def test_an_unreadable_cognition_side_errors_every_cognition_tier(engine):
    async with engine.begin() as conn:
        await conn.execute(sa.text("ALTER TABLE cognition_would_write RENAME TO cww_x"))
    report = await _run(engine)
    assert {t.tier: t.status for t in report.tiers} == {
        "note": "error", "event": "error", "standing": "error", "culture": "compared",
        "act": "compared", "act-decision": "compared",
    }


async def test_an_unreadable_close_table_errors_rather_than_reporting_nothing_closed(engine):
    async with engine.begin() as conn:
        await conn.execute(sa.text("ALTER TABLE cognition_session_close RENAME TO csc_x"))
    report = await _run(engine)
    assert {t.tier: t.status for t in report.tiers if t.tier in ("note", "act", "culture")} == {
        "note": "error", "act": "error", "culture": "compared",
    }


# ---- output -------------------------------------------------------------------

async def test_the_output_names_ids_labels_and_times_and_no_room_id(engine):
    await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _would(engine, T0, "standing", "standing_revise", room="room-a")
    report = await _run(engine)
    import json

    from nora.domain.cognition_shadow_compare import render_text
    text = "\n".join(render_text(report, DIRECTORY)) + json.dumps(report_json(report, DIRECTORY))
    assert "Dinner" in text and "room-a" not in text
    assert "note-curation: 0 matched" in "\n".join(verdict_lines(report))
