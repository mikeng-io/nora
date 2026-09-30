"""`nora cognition compare`'s two SQL reads against real Postgres (review of
#627): the mirrored side reads each remind CHAIN at its latest state, and the
proactive side keeps a row that names neither a note nor an event."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa

from nora.app.cognition_compare import CompareWindow, read_mirrored, read_proactive, run_compare
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import COGNITION_REMIND_MIRROR, REMINDERS

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


WINDOW = CompareWindow(since_ms=_ms(T0 - timedelta(hours=1)), until_ms=_ms(T0 + timedelta(hours=1)))


@pytest.fixture
async def engine(pg_url: str) -> AsyncIterator[Any]:
    eng = create_sql_engine(pg_url)
    try:
        yield eng
    finally:
        await eng.dispose()


async def _mirror(engine: Any, verb: str, ref: str, at: datetime, *, at_ms: int | None = None,
                  subjects: tuple[str, ...] = (), outcome: str = "recorded") -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.insert(COGNITION_REMIND_MIRROR).values(
            unit="u", verb=verb, reminder_ref=ref, platform="signal", conversation_id="room-a",
            intent="tell" if verb == "remind" else None, subject_rows=list(subjects),
            at_ms=at_ms, outcome=outcome,
            refusal_reason=None if outcome == "recorded" else "not_found", recorded_at=at,
        ))


async def _proactive(engine: Any, payload: dict[str, str]) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.insert(REMINDERS).values(
            id=uuid4(), requester_person_id="nora", platform="signal",
            conversation_id="room-a", conversation_type="group", text="t", created_at=T0,
            due_at=T0, state="pending", kind="proactive", payload=payload,
        ))


async def test_a_chain_is_read_at_its_latest_state_and_a_cancelled_one_is_not_an_act(engine):
    await _mirror(engine, "remind", "r-moved", T0, at_ms=_ms(T0), subjects=("note:n1",))
    await _mirror(engine, "remind_move", "r-moved", T0 + timedelta(minutes=5),
                  at_ms=_ms(T0) + 999)
    await _mirror(engine, "remind", "r-cancelled", T0, at_ms=_ms(T0), subjects=("event:e1",))
    await _mirror(engine, "remind_cancel", "r-cancelled", T0 + timedelta(minutes=5))
    await _mirror(engine, "remind", "r-plain", T0, at_ms=_ms(T0), subjects=("note:n2",))
    # refused rows change nothing
    await _mirror(engine, "remind_cancel", "r-plain", T0 + timedelta(minutes=5),
                  outcome="refused")
    # a chain that began BEFORE the window is not read, even if its cancel is inside it
    await _mirror(engine, "remind", "r-old", T0 - timedelta(days=2), at_ms=1,
                  subjects=("note:old",))
    await _mirror(engine, "remind_cancel", "r-old", T0)

    acts = {a.reminder_ref: a for a in await read_mirrored(engine, WINDOW)}
    assert set(acts) == {"r-moved", "r-cancelled", "r-plain"}
    assert (acts["r-moved"].moved, acts["r-moved"].at_ms) == (True, _ms(T0) + 999)
    assert acts["r-cancelled"].cancelled and not acts["r-plain"].cancelled


async def test_a_proactive_row_naming_neither_note_nor_event_is_kept_unkeyed(engine):
    await _proactive(engine, {"note_id": "n1", "revision_id": "rev"})
    await _proactive(engine, {"event_id": "e1"})
    await _proactive(engine, {})
    acts = await read_proactive(engine, WINDOW)
    assert sorted((a.note_id, a.event_id) for a in acts) == [("", ""), ("", "e1"), ("n1", "")]


async def test_run_compare_matches_event_anchored_and_lists_the_footnotes(engine):
    await _mirror(engine, "remind", "r1", T0, at_ms=_ms(T0), subjects=("event:e1",))
    await _mirror(engine, "remind", "r2", T0, at_ms=_ms(T0), subjects=("note:n1",))
    await _mirror(engine, "remind_cancel", "r2", T0 + timedelta(minutes=1))
    await _proactive(engine, {"event_id": "e1"})
    await _proactive(engine, {})
    result = await run_compare(engine, WINDOW)
    assert result.counts == {"matched": 1, "cognition_only": 0, "proactive_only": 0}
    assert [m.reminder_ref for m in result.cancelled] == ["r2"]
    assert len(result.unkeyed) == 1
