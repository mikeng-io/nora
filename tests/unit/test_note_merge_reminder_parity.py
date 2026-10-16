"""THE INVARIANT: `merge_into`'s eager re-point/cancel of a proactive row
must agree with what `ProactiveScreen.refusal` would decide about the SAME
row, left un-re-pointed, at its own real fire time. `merge_into` is not
allowed a second opinion — it is answering the SAME question early, using a
DIFFERENT read path (chains it happens to have open in its own transaction
rather than `notes.read`/`successor`/`revision_chain`), so the two paths can
drift: a merge-time check that read only the survivor's chain, where the
fire-time path reads the duplicate's own chain FIRST, then the survivor's.

This module is `tests/unit/test_note_merge_repoints_reminders_fake.py`'s
sibling, not a replacement: that file pins each of `merge_into`'s branches
in isolation (does it cancel, does it re-point, does it skip a firing
collision). This file pins the CROSS-CHECK: whatever `merge_into` decided,
running the fire-time screen on the un-re-pointed row (the world as it
would look if the fold never touched reminders at all) must reach the
identical verdict. Six scenarios (S1-S6): S1, S2 and S6 exposed real defects;
S3-S5 match on both paths and are kept so the parity check itself is proven
to distinguish agreement from disagreement — a check that always passes
proves nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from nora.app.proactive_screen import ProactiveScreen
from nora.domain.notes import Note
from nora.domain.reminders import Reminder, ReminderState
from nora.policy import Conversation
from nora.testing.note_store import InMemoryNoteStore
from nora.testing.reminders import FakeReminderStore

WS = "friends"
T0 = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
MS0 = int(T0.timestamp() * 1000)


@dataclass
class _Policy:
    unit: str = WS

    def proactive_allowed(self, *, platform: str, conversation_id: str) -> bool:
        return True

    def conversation(self, platform: str, conversation_id: str) -> Conversation:
        return Conversation(
            platform=platform, ids=frozenset({conversation_id}), persons=None,
            unit=self.unit, primary_id=conversation_id,
        )


def _row_on(note: Note, *, created_ms: int, due_h: float) -> Reminder:
    return Reminder(
        id=uuid.uuid4(), requester_person_id="nora", platform="signal",
        conversation_id="conv-1", conversation_type="group", text="ask",
        created_at=datetime.fromtimestamp(created_ms / 1000, tz=UTC),
        due_at=T0 + timedelta(hours=due_h), state=ReminderState.PENDING,
        fired_at=None, outcome=None, kind="proactive",
        payload={
            "note_id": note.id, "revision_id": note.head_revision_id,
            "note_title": note.title, "intent": "i",
        },
    )


async def _mk(notes: InMemoryNoteStore, title: str, now_ms: int) -> Note:
    return await notes.create(
        unit=WS, title=title, description="d", uncertainty="u",
        closing_condition="c", established_in="conv-1", body="b",
        established_by="tag:miles", now_ms=now_ms, anchor_date=20260927,
    )


async def _write(notes: InMemoryNoteStore, note_id: str, by: str, now_ms: int) -> None:
    n = notes.notes[note_id]
    await notes.append_revision(
        unit=WS, note_id=note_id, body=f"b{now_ms}",
        parent_revision_id=n.head_revision_id, established_by=by, now_ms=now_ms,
    )


async def _fold(notes: InMemoryNoteStore, survivor_id: str, duplicate_id: str, now_ms: int) -> None:
    s, d = notes.notes[survivor_id], notes.notes[duplicate_id]
    await notes.merge_into(
        unit=WS, survivor_id=s.id, survivor_head=s.head_revision_id,
        duplicate_id=d.id, duplicate_head=d.head_revision_id, body="folded",
        anchor_date=20260927, revisit_at=now_ms + 5_000, reset_strikes=True,
        established_by="dream:note-merge:friends:1", now_ms=now_ms,
    )


# --- the scenarios, returning the rows' ids keyed by name --------------------


async def _s1_tag_on_duplicate_after_link(notes, reminders):
    """A tag edit on the DUPLICATE itself, after its row was linked, then
    the fold. The merge-time check must read the duplicate's own chain: a
    check reading only the survivor's chain misses this, and the row fires
    instead of cancelling `cancelled_note_moved`."""
    s = await _mk(notes, "S", MS0 - 10_000)
    d = await _mk(notes, "D", MS0 - 10_000)
    r = _row_on(d, created_ms=MS0, due_h=1)
    await reminders.create(r)
    await _write(notes, d.id, "tag:miles", MS0 + 1_000)
    await _fold(notes, s.id, d.id, MS0 + 2_000)
    return {"d_row": r.id}


async def _s2_collision_survivor_tag_between_links(notes, reminders):
    """A collision where the SURVIVOR was tag-edited between the duplicate's
    row being linked and the survivor's own row being linked. The keeper's own
    staleness must be checked before the loser is cancelled: otherwise the
    duplicate's (stale) row wins the tie-break and BOTH end up cancelled —
    including the survivor's row, which should fire."""
    s = await _mk(notes, "S", MS0 - 10_000)
    d = await _mk(notes, "D", MS0 - 10_000)
    rd = _row_on(d, created_ms=MS0, due_h=1)
    await reminders.create(rd)
    await _write(notes, s.id, "tag:miles", MS0 + 1_000)
    rs = _row_on(notes.notes[s.id], created_ms=MS0 + 2_000, due_h=2)
    await reminders.create(rs)
    await _fold(notes, s.id, d.id, MS0 + 3_000)
    return {"d_row": rd.id, "s_row": rs.id}


async def _s3_tag_on_survivor_after_fold(notes, reminders):
    """Solo duplicate row, fold, THEN a tag write on the survivor. Correct
    under the eager moved-check's original purpose — kept so the parity check
    is proven to pass on a real case, not just to always fail loud."""
    s = await _mk(notes, "S", MS0 - 10_000)
    d = await _mk(notes, "D", MS0 - 10_000)
    r = _row_on(d, created_ms=MS0, due_h=1)
    await reminders.create(r)
    await _fold(notes, s.id, d.id, MS0 + 2_000)
    await _write(notes, s.id, "tag:miles", MS0 + 3_000)
    return {"d_row": r.id}


async def _s4_non_tag_write_on_survivor_after_fold(notes, reminders):
    """Solo, fold, then a NON-tag-path write (a background pass) on the
    survivor. Must still fire — a background write is never «moved»."""
    s = await _mk(notes, "S", MS0 - 10_000)
    d = await _mk(notes, "D", MS0 - 10_000)
    r = _row_on(d, created_ms=MS0, due_h=1)
    await reminders.create(r)
    await _fold(notes, s.id, d.id, MS0 + 2_000)
    await _write(notes, s.id, "dream:note-candidates:friends:9", MS0 + 3_000)
    return {"d_row": r.id}


async def _s5_survivor_tag_same_ms_as_link(notes, reminders):
    """The survivor's tag write lands in the SAME millisecond as the row's
    `created_at` — the boundary the `>` (not `>=`) in both checks draws."""
    s = await _mk(notes, "S", MS0 - 10_000)
    d = await _mk(notes, "D", MS0 - 10_000)
    r = _row_on(d, created_ms=MS0, due_h=1)
    await reminders.create(r)
    await _write(notes, s.id, "tag:miles", MS0)
    await _fold(notes, s.id, d.id, MS0 + 2_000)
    return {"d_row": r.id}


async def _s6_collision_survivor_row_stale_duplicate_live(notes, reminders):
    """A collision where the SURVIVOR's own row went stale (a tag edit on
    the survivor after it was linked) while the duplicate's row, linked
    after that edit, is live — and the survivor's row is due first, so it
    wins the tie-break. The keeper must be chosen after the SURVIVOR's row is
    classified: otherwise the stale survivor row is kept, the live duplicate
    row cancelled, and at fire the kept row is cancelled «moved» — nothing
    fires. The baseline fires the duplicate's row."""
    s = await _mk(notes, "S", MS0 - 10_000)
    d = await _mk(notes, "D", MS0 - 10_000)
    rs = _row_on(s, created_ms=MS0, due_h=1)
    await reminders.create(rs)
    await _write(notes, s.id, "tag:miles", MS0 + 1_000)
    rd = _row_on(notes.notes[d.id], created_ms=MS0 + 2_000, due_h=2)
    await reminders.create(rd)
    await _fold(notes, s.id, d.id, MS0 + 3_000)
    return {"d_row": rd.id, "s_row": rs.id}



async def _s7_duplicate_moved_then_survivor_retired(notes, reminders):
    """A tag edit on the duplicate after its row was linked, the fold, and
    then the SURVIVOR retired. The row's own note moved, so it reads
    `cancelled_note_moved` whatever later became of the survivor (the
    duplicate step must not live only inside `_follow_survivor`, or a retired
    survivor turns the label into `cancelled_note_retired`)."""
    s = await _mk(notes, "S", MS0 - 10_000)
    d = await _mk(notes, "D", MS0 - 10_000)
    r = _row_on(d, created_ms=MS0, due_h=1)
    await reminders.create(r)
    await _write(notes, d.id, "tag:miles", MS0 + 1_000)
    await _fold(notes, s.id, d.id, MS0 + 2_000)
    await notes.supersede(
        key=WS, target=s.id, replaced_by=None, because="retired-expired",
        source=None, now_ms=MS0 + 3_000,
    )
    return {"d_row": r.id}

SCENARIOS = {
    "S1 tag on duplicate after link": _s1_tag_on_duplicate_after_link,
    "S2 collision, survivor tag between links": _s2_collision_survivor_tag_between_links,
    "S3 tag on survivor after fold": _s3_tag_on_survivor_after_fold,
    "S4 non-tag write on survivor after fold": _s4_non_tag_write_on_survivor_after_fold,
    "S5 survivor tag same ms as link": _s5_survivor_tag_same_ms_as_link,
    "S6 collision, survivor row stale, duplicate live":
        _s6_collision_survivor_row_stale_duplicate_live,
    "S7 duplicate moved, then survivor retired": _s7_duplicate_moved_then_survivor_retired,
}


#: What each scenario MUST resolve to, stated by hand. Parity alone cannot
#: catch a defect in `proactive_row_forwarded_is_moved` itself — the fire-
#: time screen and `merge_into` both call it, so a wrong function agrees with
#: itself. The baseline is pinned here.
EXPECTED = {
    "S1 tag on duplicate after link": {"d_row": "cancelled/cancelled_note_moved"},
    "S2 collision, survivor tag between links": {
        "d_row": "cancelled/cancelled_note_moved", "s_row": "FIRES",
    },
    "S3 tag on survivor after fold": {"d_row": "cancelled/cancelled_note_moved"},
    "S4 non-tag write on survivor after fold": {"d_row": "FIRES"},
    "S5 survivor tag same ms as link": {"d_row": "FIRES"},
    "S6 collision, survivor row stale, duplicate live": {
        "d_row": "FIRES", "s_row": "cancelled/cancelled_note_moved",
    },
    "S7 duplicate moved, then survivor retired": {"d_row": "cancelled/cancelled_note_moved"},
}


async def _outcome(row: Reminder, screen: ProactiveScreen) -> str:
    """A row's resolved state if `merge_into` already touched it, or the
    fire-time screen's verdict if not — one string, comparable either way."""
    if row.state is not ReminderState.PENDING:
        return f"{row.state.value}/{row.outcome}"
    verdict = await screen.refusal(row)
    return "FIRES" if verdict is None else f"{verdict[0].value}/{verdict[1]}"


async def _run(scenario, *, wired: bool) -> dict[str, str]:
    reminders = FakeReminderStore()
    notes = InMemoryNoteStore(reminders=reminders if wired else None)
    rows = await scenario(notes, reminders)
    screen = ProactiveScreen(policy=_Policy(), notes=notes)
    out = {}
    for name, row_id in rows.items():
        row = await reminders.get(row_id)
        assert row is not None
        out[name] = await _outcome(row, screen)
    return out


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_merge_time_outcome_matches_the_fire_time_baseline(name: str) -> None:
    """THE INVARIANT (module docstring). `baseline` runs the fold with
    `reminders=None` — `merge_into` never touches a reminder — then asks the
    fire-time screen about the row as it naturally forwards. `pr` runs the SAME
    fold wired, so `merge_into` resolves it eagerly. They must agree, row for
    row.

    Falsification: revert either fix (`nora/domain/notes.py`'s
    `proactive_row_forwarded_is_moved` missing its first step, or
    `_repoint_proactive_rows` choosing a keeper before checking staleness)
    and S1 or S2 goes red here."""
    scenario = SCENARIOS[name]
    baseline = await _run(scenario, wired=False)
    pr = await _run(scenario, wired=True)
    assert baseline == EXPECTED[name]
    assert pr == baseline
