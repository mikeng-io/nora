"""One open proactive row per note (issue #158).

Production booked the same note's reminder twice, each second row written while
the first was still pending:

- ACROSS MIDNIGHT: decided 12 Sep 23:01 and again 13 Sep 01:01, both for
  13 Sep 10:00. The only per-note check was the wake clock, keyed on the Hong
  Kong day, and the two decisions fell on different days.
- THE SAME DAY: decided 11 Sep 07:02 and again 18:04, both for Sat 17:00, and
  both were marked sent. The wake clock never looked at the row at all.

These pin the rule at the write: the second `remind` is refused with
`duplicate_note`, while the legitimate cases stay writable — a re-raise after
the first went off, a note the tag path has moved since the waiting row was
linked, and (#495) a waiting row on a note MERGED into this one counts as this
note's row.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from nora.agent import tools as tools_module
from nora.agent.tools import OriginRoom
from nora.app import proactive_screen
from nora.domain import reminders as reminders_domain
from nora.domain.reminders import ReminderState
from nora.obs.metrics import Metrics
from nora.testing import InMemoryNoteStore
from nora.testing.reminders import FakeReminderStore
from tests.test_reminder_tools import CONVERSATION, UNIT, _tools

TITLE = "Blake 拉人跑馬拉松，邊個落水未定"


def _hkt(day: int, hour: int, minute: int) -> datetime:
    """A September 2026 Hong Kong wall-clock moment."""
    return datetime(2026, 9, day, hour, minute, tzinfo=ZoneInfo("Asia/Hong_Kong"))


def _origins() -> dict[str, OriginRoom]:
    return {
        CONVERSATION: OriginRoom(
            platform="signal", conversation_id=CONVERSATION,
            conversation_type="group", proactive=True, unit=UNIT,
        ),
    }


async def _note(notes: InMemoryNoteStore, title: str = TITLE, *, at: datetime):
    return await notes.create(
        unit=UNIT, title=title, description="who is running",
        uncertainty="who signs up", closing_condition="they decide",
        established_in=CONVERSATION, body="Blake 問邊個跑。",
        established_by="tag:blake", now_ms=int(at.timestamp() * 1000),
        anchor_date=20260913,
    )


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _decide(store, notes, clock, metrics):
    return _tools(
        reminders=store, tagger=None, notes=notes, clock=clock,
        metrics=metrics, dream_adaptor="proactive", origin_rooms=_origins(),
    )["remind"]


@pytest.mark.asyncio
async def test_a_second_booking_across_midnight_is_refused() -> None:
    """d03's shape: 23:01 and 01:01 for the same 10:00, two Hong Kong days."""
    store, notes, metrics = FakeReminderStore(), InMemoryNoteStore(), Metrics()
    clock = _Clock(_hkt(12, 23, 1))
    await _note(notes, at=_hkt(12, 20, 0))
    due = "2026-09-13T10:00:00+08:00"

    first = await _decide(store, notes, clock, metrics)(
        when=due, text="週日早上提返馬拉松", note=TITLE,
    )
    clock.now = _hkt(13, 1, 1)
    second = await _decide(store, notes, clock, metrics)(
        when=due, text="週日早上提返馬拉松", note=TITLE,
    )

    assert first["status"] == "ok", first
    assert second["status"] == "refused", second
    assert "already have a reminder waiting" in second["note"]
    assert "13 Sep 10:00" in second["note"]
    assert len(await store.all_pending(limit=10)) == 1
    assert metrics.get(
        "nora_reminders_created_total", status="duplicate_note",
    ) == 1


@pytest.mark.asyncio
async def test_a_second_booking_the_same_day_is_refused() -> None:
    """d01's shape: 07:02 and 18:04 on one day, both for the next 17:00."""
    store, notes, metrics = FakeReminderStore(), InMemoryNoteStore(), Metrics()
    clock = _Clock(_hkt(11, 7, 2))
    await _note(notes, at=_hkt(10, 20, 0))
    due = "2026-09-12T17:00:00+08:00"

    first = await _decide(store, notes, clock, metrics)(
        when=due, text="問晚餐搞掂未", note=TITLE,
    )
    clock.now = _hkt(11, 18, 4)
    second = await _decide(store, notes, clock, metrics)(
        when=due, text="黃昏問晚餐", note=TITLE,
    )

    assert first["status"] == "ok", first
    assert second["status"] == "refused", second
    assert len(await store.all_pending(limit=10)) == 1


class _RacingStore(FakeReminderStore):
    """Holds each `pending_of` reader until two have read, so both calls see
    no waiting row before either writes: the interleaving a real store's
    network round trips allow and the in-memory store's never suspend for."""

    def __init__(self) -> None:
        super().__init__()
        self._both_read = asyncio.Barrier(2)

    async def pending_of(self, requester_person_id: str):
        rows = await super().pending_of(requester_person_id)
        await self._both_read.wait()
        return rows


@pytest.mark.asyncio
async def test_two_bookings_in_one_step_land_one_row() -> None:
    """Strands runs one step's tool calls concurrently, so both calls pass the
    read before either writes; the store's half of the rule is what refuses
    the second (`DuplicateProactiveNoteError`)."""
    store, notes, metrics = _RacingStore(), InMemoryNoteStore(), Metrics()
    clock = _Clock(_hkt(23, 7, 1))
    await _note(notes, at=_hkt(22, 20, 0))
    remind = _decide(store, notes, clock, metrics)

    results = await asyncio.gather(
        remind(when="2026-09-23T16:00:00+08:00", text="下晝問", note=TITLE),
        remind(when="2026-09-23T16:00:00+08:00", text="下晝再問", note=TITLE),
    )

    assert sorted(r["status"] for r in results) == ["ok", "refused"], results
    assert len(await store.all_pending(limit=10)) == 1
    assert metrics.get(
        "nora_reminders_created_total", status="duplicate_note",
    ) == 1


@pytest.mark.asyncio
async def test_a_re_raise_after_the_first_went_off_is_allowed() -> None:
    """A row that has fired holds nothing: a second raise after silence is the
    strikes brake's to limit (39 D6), not this rule's."""
    store, notes, metrics = FakeReminderStore(), InMemoryNoteStore(), Metrics()
    clock = _Clock(_hkt(12, 23, 1))
    await _note(notes, at=_hkt(12, 20, 0))
    first = await _decide(store, notes, clock, metrics)(
        when="2026-09-13T10:00:00+08:00", text="提返", note=TITLE,
    )
    await store.mark(
        uuid.UUID(first["id"]), ReminderState.DONE,
        fired_at=_hkt(13, 10, 0), outcome="sent",
    )

    clock.now = _hkt(14, 9, 0)
    again = await _decide(store, notes, clock, metrics)(
        when="2026-09-14T20:00:00+08:00", text="冇人覆，再提一次", note=TITLE,
    )

    assert again["status"] == "ok", again


@pytest.mark.asyncio
async def test_a_note_the_room_moved_since_the_row_was_linked_can_be_rebooked() -> None:
    """The waiting row's premise is gone (39 D11: the screen would cancel it
    at fire), so it holds nothing. It is resolved now, with the outcome the
    screen would have written, and the new decision lands."""
    store, notes, metrics = FakeReminderStore(), InMemoryNoteStore(), Metrics()
    clock = _Clock(_hkt(12, 9, 0))
    note = await _note(notes, at=_hkt(11, 20, 0))
    first = await _decide(store, notes, clock, metrics)(
        when="2026-09-13T10:00:00+08:00", text="提返", note=TITLE,
    )
    await notes.append_revision(
        unit=UNIT, note_id=note.id, body="改咗做星期六早上。",
        parent_revision_id=note.head_revision_id, established_by="tag:blake",
        now_ms=int(_hkt(12, 12, 0).timestamp() * 1000),
    )

    clock.now = _hkt(12, 13, 0)
    again = await _decide(store, notes, clock, metrics)(
        when="2026-09-12T19:00:00+08:00", text="改咗時間，今晚提", note=TITLE,
    )

    assert again["status"] == "ok", again
    pending = await store.all_pending(limit=10)
    assert [str(r.id) for r in pending] == [again["id"]]
    old = await store.get(uuid.UUID(first["id"]))
    assert old is not None
    assert (old.state, old.outcome) == (
        ReminderState.CANCELLED, proactive_screen.CANCELLED_NOTE_MOVED,
    )


@pytest.mark.asyncio
async def test_a_row_on_a_note_merged_into_this_one_holds_it() -> None:
    """#495: a waiting row on a `retired-merged` note FOLLOWS its survivor at
    fire, so it is already the survivor's reminder. Booking the survivor is a
    second row about the same thing."""
    store, notes, metrics = FakeReminderStore(), InMemoryNoteStore(), Metrics()
    clock = _Clock(_hkt(12, 9, 0))
    duplicate = await _note(notes, "馬拉松報名", at=_hkt(11, 20, 0))
    survivor = await _note(notes, at=_hkt(11, 21, 0))
    first = await _decide(store, notes, clock, metrics)(
        when="2026-09-13T10:00:00+08:00", text="提返報名", note="馬拉松報名",
    )
    assert first["status"] == "ok", first
    await notes.merge_into(
        unit=UNIT, survivor_id=survivor.id,
        survivor_head=survivor.head_revision_id, duplicate_id=duplicate.id,
        duplicate_head=duplicate.head_revision_id, body="合併。",
        anchor_date=20260913, revisit_at=0, reset_strikes=False,
        established_by="dream:note_merge:run-1",
        now_ms=int(_hkt(12, 10, 0).timestamp() * 1000),
    )

    clock.now = _hkt(12, 11, 0)
    second = await _decide(store, notes, clock, metrics)(
        when="2026-09-13T10:30:00+08:00", text="提返", note=TITLE,
    )

    assert second["status"] == "refused", second
    assert len(await store.all_pending(limit=10)) == 1


@pytest.mark.asyncio
async def test_a_merged_row_whose_own_note_moved_holds_nothing() -> None:
    """#520 round-3 review: `remind` read only the survivor half of the
    forwarded check. A row on the duplicate, then a tag edit on the
    DUPLICATE, then the fold: the screen would cancel that row `moved` at
    fire, so it holds nothing and booking the survivor lands. The check is
    `proactive_row_forwarded_is_moved`, the one the screen and `merge_into`
    call."""
    store, notes, metrics = FakeReminderStore(), InMemoryNoteStore(), Metrics()
    clock = _Clock(_hkt(12, 9, 0))
    duplicate = await _note(notes, "馬拉松報名", at=_hkt(11, 20, 0))
    survivor = await _note(notes, at=_hkt(11, 21, 0))
    first = await _decide(store, notes, clock, metrics)(
        when="2026-09-13T10:00:00+08:00", text="提返報名", note="馬拉松報名",
    )
    assert first["status"] == "ok", first
    moved = await notes.append_revision(
        unit=UNIT, note_id=duplicate.id, body="改咗做下個月。",
        parent_revision_id=duplicate.head_revision_id, established_by="tag:blake",
        now_ms=int(_hkt(12, 9, 30).timestamp() * 1000),
    )
    await notes.merge_into(
        unit=UNIT, survivor_id=survivor.id,
        survivor_head=survivor.head_revision_id, duplicate_id=duplicate.id,
        duplicate_head=moved.id, body="合併。",
        anchor_date=20260913, revisit_at=0, reset_strikes=False,
        established_by="dream:note_merge:run-1",
        now_ms=int(_hkt(12, 10, 0).timestamp() * 1000),
    )

    clock.now = _hkt(12, 11, 0)
    second = await _decide(store, notes, clock, metrics)(
        when="2026-09-13T10:30:00+08:00", text="提返", note=TITLE,
    )

    assert second["status"] == "ok", second
    old = await store.get(uuid.UUID(first["id"]))
    assert old is not None
    assert (old.state, old.outcome) == (
        ReminderState.CANCELLED, proactive_screen.CANCELLED_NOTE_MOVED,
    )


def test_the_outcome_and_kind_spellings_match_the_screen() -> None:
    """The tool layer and the store may not import `nora.app`, so each holds
    its own copy of two words the fire-time screen owns."""
    assert tools_module._CANCELLED_NOTE_MOVED == proactive_screen.CANCELLED_NOTE_MOVED
    assert reminders_domain.PROACTIVE_KIND == proactive_screen.PROACTIVE_KIND
