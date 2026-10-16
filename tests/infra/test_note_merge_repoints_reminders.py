"""`SqlNoteStore.merge_into` resolves BOTH notes' open proactive reminders
onto the survivor's single slot, inside the fold's own transaction.

TRUE OF POSTGRES ONLY, so this cannot live in
`tests/contract/test_note_store_contract.py`: that suite's harness never
wires a `FakeReminderStore` into the `InMemoryNoteStore` param it also runs
(see `nora/testing/note_store.py`'s `merge_into` docstring — that double CAN
model this, just not through the shared harness), and
`uq_reminders_proactive_open_note` (migration 0064) is a real Postgres index
with no in-memory equivalent regardless.

THE DEFECT THIS GUARDS AGAINST: the index keys on the RAW note id a
proactive row's `payload` carries. Folding two notes that each held an open
row without resolving them leaves both rows alone — neither row's OWN note id
ever collides, so the index never objects — and both then forward to the
survivor through `nora.app.proactive_screen`'s `successor()` chase and both
fire. Reachable through the Cognition session's `note_merge` (DEC-277).

`revision_id` must move too: re-pointing `note_id` alone leaves `revision_id`
naming a revision of the RETIRED duplicate — not in the survivor's chain at
all — which makes `tag_path_progressed_since_wake` (walked from
`ProactiveScreen.refusal`'s normal, non-`_follow_survivor` branch, since the
row now names the survivor directly) fall through to the note's ROOT revision
and return `True` the moment it meets any tag-path write, which is nearly
every note's creation. A forwarded reminder would be CANCELLED, not delivered.
`test_a_repointed_row_fires_when_nothing_moved_since_it_was_linked` guards
that: moving only `note_id` makes `ProactiveScreen.refusal` return a
cancellation instead of `None`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from nora.app.proactive_screen import ProactiveScreen
from nora.domain.notes import Note, NoteRevision, StaleNoteHeadError
from nora.domain.reminders import Reminder, ReminderState
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.reminders import SqlReminderStore
from nora.infra.sql.schema import REMINDERS
from nora.policy import Conversation

WS = "friends"
T0 = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
NOW_MS = int(T0.timestamp() * 1000)


@pytest.fixture
async def stores(pg_url):
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    notes = SqlNoteStore(engine)
    reminders = SqlReminderStore(engine)
    yield notes, reminders, engine
    await reminders.aclose()


async def _make_note(store: SqlNoteStore, *, title: str, now_ms: int = NOW_MS) -> Note:
    return await store.create(
        unit=WS, title=title, description="venue open",
        uncertainty="where", closing_condition="a place is picked",
        established_in="conv-1", body="Saturday; venue open.",
        established_by="tag:miles", now_ms=now_ms, anchor_date=20260927,
    )


def _proactive_row(
    note: Note, *, due_at: datetime, state: ReminderState = ReminderState.PENDING,
    created_at: datetime = T0,
) -> Reminder:
    return Reminder(
        id=uuid.uuid4(), requester_person_id="nora", platform="signal",
        conversation_id="conv-1", conversation_type="group",
        text="ask where the dinner is", created_at=created_at, due_at=due_at,
        state=state, fired_at=None, outcome=None, kind="proactive",
        payload={
            "note_id": note.id, "revision_id": note.head_revision_id,
            "note_title": note.title, "intent": "ask where they are",
        },
    )


async def _fold(
    notes: SqlNoteStore, *, survivor: Note, duplicate: Note,
    survivor_head: str | None = None, now_ms: int = NOW_MS + 1_000,
    on_reminder_outcome: Callable[[str], None] | None = None,
) -> NoteRevision:
    return await notes.merge_into(
        unit=WS, survivor_id=survivor.id,
        survivor_head=survivor_head or survivor.head_revision_id,
        duplicate_id=duplicate.id, duplicate_head=duplicate.head_revision_id,
        body="folded body", anchor_date=20260927, revisit_at=NOW_MS + 5_000,
        reset_strikes=True, established_by="dream:note-merge:friends:1",
        now_ms=now_ms, on_reminder_outcome=on_reminder_outcome,
    )


@dataclass
class _Policy:
    """The two reads `ProactiveScreen` makes — always allowed, always this
    unit."""

    unit: str = WS

    def proactive_allowed(self, *, platform: str, conversation_id: str) -> bool:
        return True

    def conversation(self, platform: str, conversation_id: str) -> Conversation:
        return Conversation(
            platform=platform, ids=frozenset({conversation_id}), persons=None,
            unit=self.unit, primary_id=conversation_id,
        )


async def test_only_the_duplicate_has_an_open_row_is_repointed(stores) -> None:
    notes, reminders, _engine = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(row)
    outcomes: list[str] = []

    fold = await _fold(
        notes, survivor=survivor, duplicate=duplicate,
        on_reminder_outcome=outcomes.append,
    )

    got = await reminders.get(row.id)
    assert got is not None
    assert got.state is ReminderState.PENDING, "nothing was cancelled"
    assert got.outcome is None
    assert got.payload is not None
    assert got.payload["note_id"] == survivor.id, "re-pointed to the survivor"
    # `revision_id` must move onto the FOLD's own new revision, not stay on the
    # duplicate's (which is not in the survivor's chain — see the module
    # docstring). Falsification: `_repoint_stmt` moving only `note_id` leaves
    # this asserting `duplicate.head_revision_id`, and hides a row that would be
    # wrongly cancelled at fire time.
    assert got.payload["revision_id"] == fold.id
    assert got.payload["note_title"] == "Neway booking", (
        "the rest of the payload survives the JSONB merge untouched"
    )
    assert outcomes == ["repointed"]


async def test_only_the_survivor_has_an_open_row_is_untouched(stores) -> None:
    notes, reminders, _engine = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    row = _proactive_row(survivor, due_at=T0 + timedelta(hours=1))
    await reminders.create(row)
    outcomes: list[str] = []

    await _fold(
        notes, survivor=survivor, duplicate=duplicate,
        on_reminder_outcome=outcomes.append,
    )

    got = await reminders.get(row.id)
    assert got is not None
    assert got.state is ReminderState.PENDING
    assert got.outcome is None
    assert got.payload is not None
    assert got.payload["note_id"] == survivor.id
    assert outcomes == [], "nothing happened to report"


async def test_both_open_rows_resolve_to_one_earliest_due_on_the_survivor(
    stores,
) -> None:
    """Both notes held an open row: after the fold there is exactly ONE open
    row, on the survivor, keeping the earliest `due_at`; the other is
    cancelled naming why.

    Falsification: skip the resolution entirely (comment out the call in
    `merge_into`) and this fails on `open_rows` — two rows survive, one still
    pointing at the retired duplicate."""
    notes, reminders, _engine = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    survivor_row = _proactive_row(survivor, due_at=T0 + timedelta(hours=2))
    duplicate_row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(survivor_row)
    await reminders.create(duplicate_row)
    outcomes: list[str] = []

    fold = await _fold(
        notes, survivor=survivor, duplicate=duplicate,
        on_reminder_outcome=outcomes.append,
    )

    kept, cancelled = await reminders.get(duplicate_row.id), await reminders.get(
        survivor_row.id
    )
    assert kept is not None and cancelled is not None
    assert kept.state is ReminderState.PENDING, "the earliest due_at survives"
    assert kept.outcome is None
    assert kept.payload is not None
    assert kept.payload["note_id"] == survivor.id
    assert kept.payload["revision_id"] == fold.id
    assert cancelled.state is ReminderState.CANCELLED
    assert cancelled.outcome == "cancelled_duplicate_note"
    assert cancelled.fired_at is not None

    open_rows = [
        r for r in (kept, cancelled) if r.state is ReminderState.PENDING
    ]
    assert len(open_rows) == 1, "exactly one open row after the merge"
    assert outcomes == ["collision_repointed"]


async def test_a_firing_row_is_never_cancelled_for_a_pending_one(stores) -> None:
    """The survivor's row is `pending` and due FIRST; the duplicate's is
    `firing` and due LATER. Earliest-`due_at` alone would keep the pending
    row and cancel the one being spoken right now — the double-send
    `sweep_firing`'s own docstring exists to prevent. The firing row must
    survive regardless of due order."""
    notes, reminders, _engine = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    survivor_row = _proactive_row(survivor, due_at=T0 + timedelta(minutes=5))
    duplicate_row = _proactive_row(
        duplicate, due_at=T0 + timedelta(hours=3), state=ReminderState.FIRING,
    )
    await reminders.create(survivor_row)
    await reminders.create(duplicate_row)

    await _fold(notes, survivor=survivor, duplicate=duplicate)

    firing = await reminders.get(duplicate_row.id)
    pending = await reminders.get(survivor_row.id)
    assert firing is not None and pending is not None
    assert firing.state is ReminderState.FIRING, "never given up for a pending row"
    assert firing.payload is not None and firing.payload["note_id"] == survivor.id
    assert pending.state is ReminderState.CANCELLED
    assert pending.outcome == "cancelled_duplicate_note"


async def test_both_rows_firing_neither_is_cancelled(stores) -> None:
    """Both rows are `firing` — a real shape, not a corner case invented for
    this test: the heartbeat can have both in flight at once. BOTH stay
    firing, untouched, keeper included: the heartbeat's own `mark()` call is
    about to resolve the loser to `done`/`failed` the moment its turn
    finishes and would silently overwrite a `cancelled` written here — and
    re-pointing the keeper's `note_id` onto the survivor while the loser
    still holds that same id open (never cancelled) would collide with
    `uq_reminders_proactive_open_note` at the re-point statement itself.

    Falsification: cancel the loser unconditionally (drop the `state ==
    FIRING` exemption) and `still_firing_loser.state` reads `CANCELLED`; or
    keep the exemption but still try to re-point the keeper, and this
    raises `IntegrityError` (23505) instead of leaving both alone."""
    notes, reminders, _engine = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    survivor_row = _proactive_row(
        survivor, due_at=T0 + timedelta(hours=2), state=ReminderState.FIRING,
    )
    duplicate_row = _proactive_row(
        duplicate, due_at=T0 + timedelta(hours=1), state=ReminderState.FIRING,
    )
    await reminders.create(survivor_row)
    await reminders.create(duplicate_row)
    outcomes: list[str] = []

    await _fold(
        notes, survivor=survivor, duplicate=duplicate,
        on_reminder_outcome=outcomes.append,
    )

    still_firing_loser = await reminders.get(survivor_row.id)
    still_firing_keeper = await reminders.get(duplicate_row.id)
    assert still_firing_loser is not None and still_firing_keeper is not None
    assert still_firing_loser.state is ReminderState.FIRING
    assert still_firing_loser.outcome is None
    assert still_firing_loser.fired_at is None
    assert still_firing_loser.payload is not None
    assert still_firing_loser.payload["note_id"] == survivor.id
    assert still_firing_keeper.state is ReminderState.FIRING
    assert still_firing_keeper.outcome is None
    assert still_firing_keeper.payload is not None
    assert still_firing_keeper.payload["note_id"] == duplicate.id, (
        "untouched, not re-pointed — the keeper is left exactly as it was too"
    )
    assert outcomes == ["skipped_firing_collision"]


async def test_a_repointed_row_fires_when_nothing_moved_since_it_was_linked(
    stores,
) -> None:
    """A row on the duplicate, re-pointed onto the survivor by a fold with no
    intervening tag-path write, must FIRE at `ProactiveScreen.refusal` —
    `None` — not be cancelled.

    A stale `revision_id` would make `tag_path_progressed_since_wake` walk from
    the survivor's head down through `parent_revision_id`, never find the
    duplicate's `revision_id` among the survivor's own ancestors, and fall to
    the note's root revision — `established_by="tag:miles"` (`_make_note`'s own
    default) — which reads as tag-path progress on ANY note, so the row would
    come back `(CANCELLED, "cancelled_note_moved")`.
    """
    notes, reminders, _engine = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(row)

    await _fold(notes, survivor=survivor, duplicate=duplicate)

    repointed = await reminders.get(row.id)
    assert repointed is not None and repointed.payload is not None
    assert repointed.payload["note_id"] == survivor.id

    screen = ProactiveScreen(policy=_Policy(), notes=notes)
    verdict = await screen.refusal(repointed)
    assert verdict is None, f"should have fired; screen said {verdict!r}"


async def test_a_repointed_row_is_cancelled_moved_when_the_survivor_moved_first(
    stores,
) -> None:
    """The survivor's tag path writes something AFTER the row was linked but
    BEFORE the fold. Re-pointing the row onto the fold's own new revision
    (the survivor's head at fold time) would HIDE that earlier write — the
    next check would start counting from the fold, not from when the row was
    booked, so it would read `not moved` when the premise had already
    changed. This is caught eagerly, at merge time, not left for the fire
    tick to discover.

    Falsification: skip the eager moved-check (repoint unconditionally) and
    this fails on `resolved.state`, which stays `PENDING` instead of
    `CANCELLED`."""
    notes, reminders, _engine = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(row)

    # A person revises the SURVIVOR after the row was linked (T0) and before
    # the fold.
    moved = await notes.append_revision(
        unit=WS, note_id=survivor.id, body="Ada booked it",
        parent_revision_id=survivor.head_revision_id,
        established_by="tag:ada", now_ms=NOW_MS + 500,
    )
    outcomes: list[str] = []

    await _fold(
        notes, survivor=survivor, duplicate=duplicate,
        survivor_head=moved.id, on_reminder_outcome=outcomes.append,
    )

    resolved = await reminders.get(row.id)
    assert resolved is not None
    assert resolved.state is ReminderState.CANCELLED
    assert resolved.outcome == "cancelled_note_moved"
    assert resolved.payload is not None
    assert resolved.payload["note_id"] == duplicate.id, (
        "cancelled, not re-pointed — the payload is left exactly as it was"
    )
    assert outcomes == ["moved"]


async def test_a_stale_survivor_row_does_not_win_the_collision(stores) -> None:
    """Mirrors the parity suite's stale-survivor case, on Postgres. The SURVIVOR's own row
    was linked, then a person revised the survivor, then the duplicate's row
    was linked (after that write, so it is live). The survivor's row is due
    first, so an unclassified tie-break would keep it and cancel the live
    duplicate row — and at fire the kept row is cancelled «moved»: nothing
    fires. The fire-time baseline fires the duplicate's row.

    Falsification: skip classifying the survivor's row before the keeper is
    chosen and the duplicate's row ends `cancelled_duplicate_note`."""
    notes, reminders, _engine = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    s_row = _proactive_row(survivor, due_at=T0 + timedelta(hours=1))
    await reminders.create(s_row)
    moved = await notes.append_revision(
        unit=WS, note_id=survivor.id, body="Ada booked it",
        parent_revision_id=survivor.head_revision_id,
        established_by="tag:ada", now_ms=NOW_MS + 500,
    )
    d_row = _proactive_row(
        duplicate, due_at=T0 + timedelta(hours=2),
        created_at=T0 + timedelta(seconds=1),
    )
    await reminders.create(d_row)
    outcomes: list[str] = []

    await _fold(
        notes, survivor=survivor, duplicate=duplicate,
        survivor_head=moved.id, now_ms=NOW_MS + 2_000,
        on_reminder_outcome=outcomes.append,
    )

    s_after = await reminders.get(s_row.id)
    d_after = await reminders.get(d_row.id)
    assert s_after is not None and d_after is not None
    assert s_after.state is ReminderState.CANCELLED
    assert s_after.outcome == "cancelled_note_moved"
    assert d_after.state is ReminderState.PENDING
    assert d_after.payload is not None
    assert d_after.payload["note_id"] == survivor.id
    assert outcomes == ["moved", "repointed"]


async def test_merge_rollback_leaves_reminders_untouched(stores) -> None:
    """A head moved under the fold: `merge_into` raises and the whole
    transaction rolls back. Both proactive rows must be exactly as they were
    — no re-point, no cancel — because the reminder half runs INSIDE the same
    transaction as the two note swaps."""
    notes, reminders, _engine = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    survivor_row = _proactive_row(survivor, due_at=T0 + timedelta(hours=2))
    duplicate_row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(survivor_row)
    await reminders.create(duplicate_row)

    # The survivor's head moved under the pass.
    await notes.append_revision(
        unit=WS, note_id=survivor.id, body="Ada booked it",
        parent_revision_id=survivor.head_revision_id,
        established_by="tag:ada", now_ms=NOW_MS + 10,
    )

    with pytest.raises(StaleNoteHeadError):
        await _fold(notes, survivor=survivor, duplicate=duplicate)

    still_survivor = await reminders.get(survivor_row.id)
    still_duplicate = await reminders.get(duplicate_row.id)
    assert still_survivor is not None and still_duplicate is not None
    assert still_survivor.state is ReminderState.PENDING
    assert still_survivor.outcome is None
    assert still_survivor.payload is not None
    assert still_survivor.payload["note_id"] == survivor.id
    assert still_duplicate.state is ReminderState.PENDING
    assert still_duplicate.outcome is None
    assert still_duplicate.payload is not None
    assert still_duplicate.payload["note_id"] == duplicate.id


async def test_a_concurrent_insert_during_repoint_becomes_stale_not_a_crash(
    stores,
) -> None:
    """The one window `SELECT ... FOR UPDATE` cannot close: it locks rows
    that already exist, not the ABSENCE of one. A `remind` call landing on
    the survivor between that read and the re-point `UPDATE` makes the
    UPDATE collide with `uq_reminders_proactive_open_note` — `IntegrityError`
    23505 — which must become `StaleNoteHeadError`, the refusal `nora.
    language.note_merge._fold` already catches to record `stale`, never an
    unhandled crash that takes the whole pass down.

    White-box on purpose: reproducing the actual race needs two real
    concurrent transactions interleaved at a specific statement, which is not
    reproducible on demand. This calls `SqlNoteStore._repoint_row`
    directly with the collision already committed, which is the exact state
    the real race leaves the re-point `UPDATE` to find — the same failure,
    reached by construction instead of by luck.

    Falsification: catch every `IntegrityError` there (not just 23505), or
    drop the translation entirely, and this fails — either a plain
    `IntegrityError` propagates (drop the translation) or is impossible to
    tell apart from a mis-typed `state` violation (catch too broadly, proven
    separately by `tests/infra/test_reminder_store.py`'s narrow-translation
    test, whose sibling this module borrows the argument from)."""
    notes, reminders, engine = stores
    survivor = await _make_note(notes, title="Saturday dinner")
    duplicate = await _make_note(notes, title="Neway booking")
    duplicate_row = _proactive_row(duplicate, due_at=T0 + timedelta(hours=1))
    await reminders.create(duplicate_row)
    # The "concurrent" remind call, already committed by the time our
    # transaction's re-point statement runs.
    survivor_row = _proactive_row(survivor, due_at=T0 + timedelta(hours=2))
    await reminders.create(survivor_row)

    sel = sa.select(
        REMINDERS.c.id, REMINDERS.c.state, REMINDERS.c.due_at,
        REMINDERS.c.created_at,
    ).where(REMINDERS.c.id == duplicate_row.id)
    async with engine.begin() as conn:
        row = (await conn.execute(sel)).one()
        with pytest.raises(StaleNoteHeadError):
            await SqlNoteStore._repoint_row(
                conn, row=row, survivor_id=survivor.id,
                revision_id=str(uuid.uuid4()), on_reminder_outcome=None,
                outcome="repointed",
            )

    # And nothing was left half-written: the duplicate's row is exactly as
    # it was (the raise happened before any UPDATE could land, and the
    # `with engine.begin()` block above rolled back on the raise regardless).
    untouched = await reminders.get(duplicate_row.id)
    assert untouched is not None
    assert untouched.state is ReminderState.PENDING
    assert untouched.payload is not None
    assert untouched.payload["note_id"] == duplicate.id
