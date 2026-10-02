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
        # A report over a window with NO session is an ERROR (and never a table of
        # zeros), so every test below reads against one session per unit it names:
        # the rows under test are what the sessions did, as in a real week.
        for unit in ("u", "v"):
            await _bare_session(eng, T0, unit=unit)
        yield eng
    finally:
        await eng.dispose()


async def _bare_session(engine: Any, at: datetime, *, unit: str = "u",
                        skills: dict[str, str] | None = None, wake_id: int | None = None) -> int:
    """A session row with no agenda items and no close: the denominator."""
    async with engine.begin() as conn:
        return (await conn.execute(sa.insert(COGNITION_SESSION).values(
            unit=unit, mode="shadow", started_at_ms=_ms(at), from_stimulus_id=1,
            through_stimulus_id=1, situation={}, agenda={"items": []},
            skill_versions=skills or {}, wake_id=wake_id, recorded_at=at,
        ).returning(COGNITION_SESSION.c.id))).scalar_one()


async def _run(engine: Any, *, tolerance_min: int = 60, unit: str | None = None):
    return await run_shadow_compare(
        engine, WINDOW, tolerance_ms=tolerance_min * 60_000, unit=unit, directory=DIRECTORY,
    )


async def _run_with(engine: Any, directory: RoomDirectory):
    return await run_shadow_compare(
        engine, WINDOW, tolerance_ms=60 * 60_000, unit=None, directory=directory,
    )


def _writer(report: Any, tier: str, writer: str) -> Any:
    tr = next(t for t in report.tiers if t.tier == tier)
    return tr, next(w for w in tr.writers if w.writer == writer)


async def _would(engine: Any, at: datetime, tier: str, verb: str, *, room: str = "room-a",
                 candidate: dict | None = None, outcome: str = "would_commit",
                 unit: str = "u", session_id: int | None = None,
                 args: dict | None = None) -> int:
    async with engine.begin() as conn:
        return (await conn.execute(sa.insert(COGNITION_WOULD_WRITE).values(
            session_id=session_id, unit=unit, tier=tier, verb=verb, platform="signal",
            conversation_id=room, args=args or {}, outcome=outcome,
            refusal_reason=None if outcome == "would_commit" else "bad_mode",
            candidate=(candidate or {}) if outcome == "would_commit" else None,
            recorded_at=at,
        ).returning(COGNITION_WOULD_WRITE.c.id))).scalar_one()


async def _note(engine: Any, *, by: str, at: datetime, room: str = "room-a", unit: str = "u",
                anchor_date: int | None = 20260930, parent: bool = False,
                note_id: str | None = None, title: str = "t") -> tuple[str, str]:
    """A note and one revision of it by `by`. `parent=True` writes a LATER
    revision on an existing note (`note_id`)."""
    async with engine.begin() as conn:
        if note_id is None:
            note_id = str(uuid4())
            head = str(uuid4())
            await conn.execute(sa.insert(NOTE).values(
                id=note_id, unit=unit, title=title, description="d", uncertainty="u",
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
                         unit: str = "u", agenda: Any = None, kind: str = "message",
                         cites: str = "") -> int:
    """A session with one agenda item and its close. `cites` is text the item's
    summary carries (a note or event id)."""
    async with engine.begin() as conn:
        sid = (await conn.execute(sa.insert(COGNITION_SESSION).values(
            unit=unit, mode="shadow", started_at_ms=_ms(at), from_stimulus_id=1,
            through_stimulus_id=1, situation={},
            agenda=agenda if agenda is not None else {"items": [
                {"kind": kind, "platform": "signal", "conversation_id": room,
                 "summary": f"3 new messages {cites}".strip(), "cues": []}]},
            recorded_at=at,
        ).returning(COGNITION_SESSION.c.id))).scalar_one()
        await conn.execute(sa.insert(COGNITION_SESSION_CLOSE).values(
            session_id=sid, item_index=0, kind=kind, reason=reason, recorded_at=at,
        ))
    return int(sid)


def _tag(report: Any) -> Any:
    tr = next(t for t in report.tiers if t.tier == "event")
    return tr, next((w for w in tr.tag_path if w.writer == "event:tag"), None)


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
        "anchor_date": 20260930, "anchor_entity_ids": [], "title": "T"})
    tier, w = _writer(await _run(engine), "note", "note-candidates")
    assert w.matched == 1 and not w.clocked_only and not tier.cognition_only


async def test_a_create_on_the_same_day_with_another_title_is_another_note(engine):
    await _note(engine, by="dream:note-candidates:k:1", at=T0, title="Dentist")
    await _would(engine, T0, "note", "note_create", candidate={
        "anchor_date": 20260930, "anchor_entity_ids": [], "title": "Passport renewal"})
    tier, w = _writer(await _run(engine), "note", "note-candidates")
    assert w.matched == 0 and len(w.clocked_only) == 1 and len(tier.cognition_only) == 1


async def test_a_create_is_read_at_the_anchor_the_revision_wrote_not_the_notes_current_one(engine):
    note_id, _ = await _note(engine, by="dream:note-candidates:k:1", at=T0, title="Dentist")
    async with engine.begin() as conn:   # a later curation moves the anchor and retitles it
        await conn.execute(sa.update(NOTE).where(NOTE.c.id == note_id).values(
            anchor_date=20261015, title="Dentist (moved)"))
    await _would(engine, T0, "note", "note_create", candidate={
        "anchor_date": 20260930, "anchor_entity_ids": [], "title": "dentist"})
    tier, w = _writer(await _run(engine), "note", "note-candidates")
    assert w.matched == 1 and not w.clocked_only and not tier.cognition_only


async def test_a_create_in_another_room_or_with_another_anchor_does_not_match(engine):
    await _note(engine, by="dream:note-candidates:k:1", at=T0, anchor_date=20260930)
    await _would(engine, T0, "note", "note_create", room="room-b", candidate={
        "anchor_date": 20260930, "anchor_entity_ids": [], "title": "t"})
    await _would(engine, T0, "note", "note_create", candidate={
        "anchor_date": 20261001, "anchor_entity_ids": [], "title": "t"})
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
async def test_a_close_in_the_same_room_naming_the_note_explains_a_clocked_only_row(engine, reason):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0 + timedelta(minutes=10), reason, cites=f"note {note_id}")
    _, w = _writer(await _run(engine), "note", "note-curation")
    # The close EXPLAINS the row, but it is the model's own word: it is counted as
    # self-attested and never as closed (no same-session write backs it).
    assert [g.closed_by for g in w.clocked_only] == [reason]
    assert (w.closed, w.closed_self_attested, w.open) == (0, 1, 0)


@pytest.mark.parametrize("reason", ["written", "struck"])
async def test_written_and_struck_do_not_explain_anything(engine, reason):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0, reason, cites=note_id)
    _, w = _writer(await _run(engine), "note", "note-curation")
    assert w.closed == 0 and w.open == 1


async def test_a_close_in_another_room_or_outside_the_tolerance_does_not_close_it(engine):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0, "no_change", room="room-b", cites=note_id)
    await _session_close(engine, T0 + timedelta(hours=5), "no_change", cites=note_id)
    _, w = _writer(await _run(engine, tolerance_min=60), "note", "note-curation")
    assert w.open == 1


async def test_a_close_before_the_clocked_write_does_not_explain_it(engine):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0 - timedelta(minutes=5), "no_change", cites=note_id)
    _, w = _writer(await _run(engine), "note", "note-curation")
    assert w.open == 1


async def test_a_close_that_names_another_note_or_none_does_not_close_a_note_row(engine):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0 + timedelta(minutes=5), "no_change", cites=str(uuid4()))
    await _session_close(engine, T0 + timedelta(minutes=6), "no_change")
    _, w = _writer(await _run(engine), "note", "note-curation")
    assert w.open == 1 and note_id


async def test_a_rollup_close_does_not_close_a_note_row(engine):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0 + timedelta(minutes=1), "no_change", kind="rollup",
                         cites=note_id)
    assert _writer(await _run(engine), "note", "note-curation")[1].open == 1


async def test_a_time_close_does_not_close_a_standing_row_but_a_rollup_close_does(engine):
    await _standing(engine, T0, 2)
    await _session_close(engine, T0 + timedelta(minutes=2), "no_change", kind="time")
    assert _writer(await _run(engine), "standing", "standing-debrief")[1].open == 1
    await _session_close(engine, T0 + timedelta(minutes=3), "no_change", kind="rollup")
    explained = _writer(await _run(engine), "standing", "standing-debrief")[1]
    assert (explained.closed, explained.closed_self_attested) == (0, 1)


async def test_one_waiting_close_does_not_close_three_writers_rows(engine):
    await _standing(engine, T0 - timedelta(days=3), 1)
    await _standing(engine, T0, 2)                                        # debrief
    await _standing(engine, T0 + timedelta(minutes=1), 2)                 # ingest fold
    await _standing(engine, T0 + timedelta(minutes=2), 3)                 # debrief
    await _session_close(engine, T0 + timedelta(minutes=10), "waiting")
    report = await _run(engine)
    tier = next(t for t in report.tiers if t.tier == "standing")
    assert sum(w.closed for w in tier.writers) == 0    # a `waiting` is the model's say-so
    assert sum(w.closed_self_attested for w in tier.writers) == 1
    assert sum(w.open for w in tier.writers) == 2      # 3 rows in the window, one explained


async def test_a_refused_would_write_nearby_does_not_close_a_row(engine):
    await _standing(engine, T0, 2)
    await _would(engine, T0 + timedelta(minutes=1), "standing", "standing_revise",
                 outcome="refused")
    _, w = _writer(await _run(engine), "standing", "standing-debrief")
    assert w.open == 1 and w.clocked_only[0].refused_nearby == 1


async def test_a_close_with_an_unreadable_agenda_explains_nothing_and_says_so(engine):
    await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0 + timedelta(minutes=1), "no_change", agenda={"items": []})
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
    assert debrief.coincident == 1 and debrief.matched == 0 and not debrief.clocked_only
    assert ingest.coincident == 0 and len(ingest.clocked_only) == 1 and not tier.cognition_only


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
    tier, w = _tag(await _run(engine))
    assert w.matched == 2 and not w.clocked_only and not tier.cognition_only
    assert tier.writers == ()


async def test_a_backfill_revision_is_not_a_live_write(engine):
    await _event(engine, T0, written_by="backfill")
    tier, w = _tag(await _run(engine))
    assert w is None and tier.writers == ()


# ---- culture ----------------------------------------------------------------

async def test_culture_writes_are_clocked_only_by_construction_and_never_closed(engine):
    async with engine.begin() as conn:
        await conn.execute(sa.insert(CULTURE_REVISION).values(
            culture_key="room-a", body="b", written_at=_ms(T0),
            established_by="dream:culture:k:1", recorded_at=T0))
    await _session_close(engine, T0 + timedelta(minutes=1), "no_change")
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


async def test_the_live_tag_path_is_reported_apart_and_in_no_gate_verdict(engine):
    await _event(engine, T0, title="Dinner")                 # written_by = 'tag'
    report = await _run(engine)
    _, tag = _tag(report)
    assert tag is not None and len(tag.clocked_only) == 1 and tag.clocked_only[0].closed_by is None
    assert not any("event:tag" in line for line in verdict_lines(report))
    assert any("event:tag" in line for line in report_json(report, DIRECTORY)["tag_path"])


# ---- privacy: no conversation id, phone number or member name, in any tier -------

PHONE, OTHER_PHONE, MEMBER = "+85291234567", "+85299998888", "Alice Walsh"


async def test_no_tier_prints_a_room_id_a_phone_number_or_a_member_name(engine):
    import json

    from nora.domain.cognition_shadow_compare import render_text
    directory = RoomDirectory(labels={PHONE: "Dinner"}, unit_rooms={})
    rooms = (PHONE, f"signal:{OTHER_PHONE}")             # one labelled, one not
    for n, room in enumerate(rooms):
        at = T0 + timedelta(minutes=n)
        await _note(engine, by="dream:note-curation:k:1", at=at, parent=True, room=room,
                    title=MEMBER)
        await _note(engine, by="dream:note-candidates:k:1", at=at, room=room, title=MEMBER)
        await _event(engine, at, room=room, title=MEMBER, written_by="tag")
        await _event(engine, at, room=room, title=MEMBER, written_by="someone")
        await _standing(engine, at, 1, room=room)
        await _standing(engine, at + timedelta(seconds=1), 2, room=room)
        async with engine.begin() as conn:
            await conn.execute(sa.insert(CULTURE_REVISION).values(
                culture_key=room, body=MEMBER, written_at=_ms(at),
                established_by="dream:culture:k:1", recorded_at=at))
            await conn.execute(sa.insert(DECISION_LOG).values(
                id=f"d{n}", writer="proactive_decide", platform="signal", conversation_id=room,
                unit="u", note_id=f"n{n}", verdict="remind", reason=MEMBER, ts=_ms(at),
                recorded_at=at))
        await _reminder(engine, {"note_id": f"p{n}"}, at, room=room)
        await _reminder(engine, {}, at, room=room)
        await _would(engine, at, "standing", "standing_revise", room=room)
        await _would(engine, at, "note", "note_create", room=room, candidate={
            "anchor_date": 1, "anchor_entity_ids": [], "title": MEMBER})
        await _session_close(engine, at + timedelta(minutes=5), "waiting", room=room)
    report = await _run_with(engine, directory)
    # every tier really reported a clocked-only row, so the absence below is not vacuous
    for tier in report.tiers:
        assert any(w.clocked_only for w in (*tier.writers, *tier.tag_path)), tier.tier
    text = "\n".join(render_text(report, directory)) + json.dumps(report_json(report, directory))
    for secret in (PHONE, OTHER_PHONE, "85291234567", "85299998888", "Alice", "Walsh"):
        assert secret not in text, secret
    assert "Dinner" in text and "room#" in text


# ---- session coverage, and what a close proves ---------------------------------------
#
# Added with the measurability fixes (an audit of the live shadow data found a
# report that printed zeros over no sessions and counted the model's own closes as
# closed). Each test names the property; `scripts/mutations/cognition-measures.json`
# names what reverting each fix turns red.

async def _no_sessions(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.delete(COGNITION_SESSION))


async def test_a_window_with_no_session_is_an_error_and_never_a_table_of_zeros(engine):
    await _no_sessions(engine)
    await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)    # live rows exist
    report = await _run(engine)
    assert report.any_error and report.tiers == ()
    assert report.coverage is not None and report.coverage.sessions == 0
    assert verdict_lines(report) == [report_json(report, DIRECTORY)["verdicts"][0]]
    assert verdict_lines(report)[0].startswith("ERROR: no Cognition session ran")
    data = report_json(report, DIRECTORY)
    assert data["status"] == "error" and data["tiers"] == []
    assert "0 matched" not in "\n".join(verdict_lines(report))


async def test_a_unit_with_no_session_is_an_error_even_when_another_unit_has_one(engine):
    async with engine.begin() as conn:
        await conn.execute(sa.delete(COGNITION_SESSION).where(COGNITION_SESSION.c.unit == "v"))
    assert not (await _run(engine, unit="u")).any_error
    report = await _run(engine, unit="v")
    assert report.any_error and "for unit 'v'" in (report.error or "")


async def test_coverage_says_sessions_units_days_wakes_and_skills(engine):
    await _no_sessions(engine)
    from nora.infra.sql.schema import COGNITION_WAKE, COGNITION_WAKE_OUTCOME
    sid = await _bare_session(engine, T0, wake_id=1, skills={"x": "sha"})
    await _bare_session(engine, T0 + timedelta(hours=13), unit="v", wake_id=2)
    async with engine.begin() as conn:
        for wid in (1, 2, 3, 4):
            await conn.execute(sa.insert(COGNITION_WAKE).values(
                id=wid, unit="u", mode="shadow", reason="settled", from_stimulus_id=0,
                through_stimulus_id=1, stimulus_count=1, eligible_count=1, at_ms=_ms(T0),
                tracked=1,
            ))
        await conn.execute(sa.insert(COGNITION_WAKE_OUTCOME).values(
            wake_id=4, unit="u", outcome="failed", at_ms=_ms(T0)))
        await conn.execute(sa.insert(COGNITION_WAKE_OUTCOME).values(
            wake_id=3, unit="u", outcome="closed", at_ms=_ms(T0)))
    c = (await _run(engine)).coverage
    assert c is not None and sid
    assert (c.sessions, c.units_with_sessions, c.policy_units) == (2, 2, 2)
    assert c.active_days == 2 and abs(c.days - 2.0) < 1e-9 and abs(c.per_day - 1.0) < 1e-9
    assert (c.wakes, c.wakes_without_session, c.wakes_failed_without_session) == (4, 2, 1)
    assert c.sessions_without_skills == 1


async def test_a_session_with_no_skill_is_named_in_the_report(engine):
    from nora.domain.cognition_shadow_compare import render_text
    text = "\n".join(render_text(await _run(engine), DIRECTORY))
    assert "no skills injected in 2 of 2 session(s)" in text
    assert "not the full design" in text


async def test_every_output_states_what_matched_does_and_does_not_establish(engine):
    from nora.domain.cognition_shadow_compare import render_text
    report = await _run(engine)
    text = "\n".join(render_text(report, DIRECTORY))
    assert "matched = the same KIND" in text and "does NOT mean the two writes say the same" in text
    assert "matched_means" in report_json(report, DIRECTORY)


async def test_a_covered_close_backed_by_a_same_session_write_is_closed_by_code(engine):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    sid = await _session_close(engine, T0 + timedelta(minutes=10), "covered",
                               cites=f"note {note_id}")
    # The session's own write on that note, three hours on: outside the tolerance, so
    # it does not MATCH the clocked row, but it is the write the close points at.
    await _would(engine, T0 + timedelta(hours=3), "note", "note_update",
                 candidate={"note_id": note_id}, session_id=sid)
    _, w = _writer(await _run(engine), "note", "note-curation")
    assert [g.closed_by for g in w.clocked_only] == ["covered"]
    assert (w.closed, w.closed_self_attested, w.open) == (1, 0, 0)


async def test_a_covered_close_whose_session_wrote_another_note_or_another_room_stays_self_attested(
    engine,
):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    sid = await _session_close(engine, T0 + timedelta(minutes=10), "covered",
                               cites=f"note {note_id}")
    await _would(engine, T0 + timedelta(hours=3), "note", "note_update",
                 candidate={"note_id": str(uuid4())}, session_id=sid)          # another note
    await _would(engine, T0 + timedelta(hours=3), "note", "note_update", room="room-b",
                 candidate={"note_id": note_id}, session_id=sid)               # another room
    _, w = _writer(await _run(engine), "note", "note-curation")
    assert (w.closed, w.closed_self_attested, w.open) == (0, 1, 0)


async def test_a_would_write_of_another_session_does_not_back_a_covered_close(engine):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    # Two sessions each close an item `covered`; only the SECOND wrote the note. The
    # nearer close (the first session's) is the one that explains the clocked row, and
    # it is not backed by what a different session wrote.
    await _session_close(engine, T0 + timedelta(minutes=10), "covered", cites=f"note {note_id}")
    other = await _session_close(
        engine, T0 + timedelta(minutes=20), "covered", cites=f"note {note_id}",
    )
    await _would(engine, T0 + timedelta(hours=3), "note", "note_update",
                 candidate={"note_id": note_id}, session_id=other)
    _, w = _writer(await _run(engine), "note", "note-curation")
    assert (w.closed, w.closed_self_attested) == (0, 1)


async def test_a_refused_write_of_the_same_session_does_not_back_a_covered_close(engine):
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    sid = await _session_close(engine, T0 + timedelta(minutes=10), "covered",
                               cites=f"note {note_id}")
    # A refused write still names the note in its args: only its outcome says it was not
    # a write, so a proof that forgot to read the outcome would take it for one.
    await _would(engine, T0 + timedelta(hours=3), "note", "note_update", outcome="refused",
                 session_id=sid, args={"note_id": note_id})
    _, w = _writer(await _run(engine), "note", "note-curation")
    assert (w.closed, w.closed_self_attested) == (0, 1)


async def test_the_output_says_plainly_that_a_self_attested_close_is_not_evidence(engine):
    from nora.domain.cognition_shadow_compare import render_text
    note_id, _ = await _note(engine, by="dream:note-curation:k:1", at=T0, parent=True)
    await _session_close(engine, T0 + timedelta(minutes=10), "no_change", cites=f"note {note_id}")
    report = await _run(engine)
    line = next(ln for ln in verdict_lines(report) if ln.startswith("note-curation"))
    assert "0 closed by code, 1 closed self-attested: not evidence, 0 open" in line
    text = "\n".join(render_text(report, DIRECTORY))
    assert "closed_by=no_change(self-attested)" in text
    assert "never closed or matched" in text
    unmatched = report_json(report, DIRECTORY)["tiers"][0]["writers"]
    row = next(w for w in unmatched if w["writer"] == "note-curation")
    assert (row["closed"], row["closed_by_code"], row["closed_self_attested"]) == (0, 0, 1)


async def test_an_unreadable_coverage_is_an_error_naming_the_class_and_no_tiers(engine):
    async with engine.begin() as conn:
        await conn.execute(sa.text("ALTER TABLE cognition_wake RENAME TO cognition_wake_x"))
    report = await _run(engine)
    assert report.any_error and report.tiers == () and report.coverage is None
    assert "could not be read" in (report.error or "") and "cognition_wake" not in report.error
    assert report_json(report, DIRECTORY)["coverage"] is None
