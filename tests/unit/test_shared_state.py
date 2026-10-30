"""The shared cognitive state: dedupe, projection, coverage, render.

`design/notebook-memory-types/20-shared-cognitive-state.md`'s generated
scenarios, at unit grain: the same act in two rooms is ONE act (never a
citation-set match), an unread source is `unavailable` never
`nothing_found`, and a send the row could not record is `unknown`, never
`sent` and never retried silently.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from nora.agent.shared_state import read_shared
from nora.domain.cognition_remind import PendingRemind, RemindContext, check_remind
from nora.domain.reminders import Reminder, ReminderState
from nora.language.shared_render import render_shared_state
from nora.testing.reminders import FakeReminderStore

T0_MS = 1_780_000_000_000
T0 = datetime.fromtimestamp(T0_MS / 1000, UTC)
ROOM_A = ("signal", "room-a")
ROOM_B = ("signal", "room-b")
ROOMS = (ROOM_A, ROOM_B)


def _pending(
    *, platform: str = "signal", conversation_id: str = "room-a",
    subject: tuple[str, str] | None = ("note", "n-1"), action: str = "tell",
    rows: tuple[str, ...] = ("m1",),
) -> PendingRemind:
    return PendingRemind(
        reminder_ref=str(uuid.uuid4()), platform=platform,
        conversation_id=conversation_id, subject_rows=rows,
        subject=subject, action=action,
    )


def _check(**kw):
    args = {
        "subject_rows": ["m1"], "intent": "tell", "platform": "signal",
        "conversation_id": "room-a", "pending": (),
        "context": RemindContext(),
    }
    args.update(kw)
    return check_remind(**args)


def test_same_subject_and_action_in_another_room_is_a_conflict():
    """The incident case: the unit already waits to `tell` note:n-1 in
    room-b; a second room-a act for it is refused and names room-b."""
    refusal = _check(
        subject=("note", "n-1"),
        pending=[_pending(conversation_id="room-b")],
    )
    assert refusal is not None and refusal.reason == "conflict_other_room"
    assert "room-b" in refusal.detail


def test_an_act_pending_everywhere_asked_is_a_duplicate():
    refusal = _check(
        subject=("note", "n-1"),
        pending=[_pending(conversation_id="room-a")],
    )
    assert refusal is not None and refusal.reason == "duplicate_pending"


def test_explicit_multi_destination_is_the_producers_intent():
    """room-b already waits on this act; a bare room-a call is refused, and
    naming room-b in `also_rooms` IS the explicit intent that lets room-a
    join — while asking only for the already-pending destination is a plain
    duplicate."""
    refusal = _check(
        subject=("note", "n-1"),
        pending=[_pending(conversation_id="room-b")],
        extra_destinations=[("signal", "room-b")],
    )
    assert refusal is None
    refusal = _check(
        subject=("note", "n-1"), conversation_id="room-b",
        pending=[_pending(conversation_id="room-b")],
    )
    assert refusal is not None and refusal.reason == "duplicate_pending"


def test_a_different_action_on_the_same_subject_is_another_act():
    refusal = _check(
        subject=("note", "n-1"), intent="ask",
        pending=[_pending(conversation_id="room-b", action="tell")],
    )
    assert refusal is None


def test_citation_inequality_is_never_uniqueness_and_never_identity():
    """Different citation rows, same subject+action: still a conflict. Same
    citation rows, different subject: still another act."""
    refusal = _check(
        subject=("note", "n-1"), subject_rows=["m9", "m8"],
        pending=[_pending(conversation_id="room-b", rows=("m1", "m2"))],
    )
    assert refusal is not None and refusal.reason == "conflict_other_room"
    refusal = _check(
        subject=("decision", "d-7"), subject_rows=["m1"],
        pending=[_pending(subject=("note", "n-1"), rows=("m1",))],
    )
    assert refusal is None


def test_an_unresolved_new_row_falls_back_to_the_legacy_room_rule():
    refusal = _check(
        pending=[_pending(subject=None, rows=("m1",))],
    )
    assert refusal is not None and refusal.reason == "duplicate_pending"


def _reminder(**kw) -> Reminder:
    base = {
        "id": uuid.uuid4(), "requester_person_id": "nora", "platform": "signal",
        "conversation_id": "room-a", "conversation_type": "group",
        "text": "the thing to raise",
        "created_at": T0 - timedelta(minutes=10),
        "due_at": T0 - timedelta(minutes=5), "state": ReminderState.FAILED,
        "fired_at": T0 - timedelta(minutes=5), "outcome": "failed_error",
        "kind": "proactive",
        "payload": {"subject_kind": "note", "subject_id": "n-1", "action": "tell"},
    }
    base.update(kw)
    return Reminder(**base)


def _send(message_id: int, at_ms: int, text: str = "already said it"):
    """The real `RecordHit` shape `own_sends` returns — the delivered fact
    lives under `row`, exactly as `SqlRecordQuery` rows it."""
    from nora.domain.models import TranscriptRow
    from nora.domain.record_query import RecordHit
    return RecordHit(
        platform="signal", conversation_id="room-a",
        row=TranscriptRow(
            id=message_id, sender_id="nora", sender_name="Nora", ts=at_ms,
            body=text, is_nora=True, delivery_status="sent", raw_json=None,
        ),
    )


class _Sends:
    def __init__(self, hits=()):
        self._hits = list(hits)

    async def own_sends(self, rooms, *, start_ms, end_ms, limit):
        room_set = set(rooms)
        return tuple(
            h for h in self._hits
            if (h.platform, h.conversation_id) in room_set
            and start_ms <= h.row.ts < end_ms
        )[:limit]


class _Broken:
    def __getattr__(self, name):
        async def _boom(*a, **k):
            raise RuntimeError("store is down")
        return _boom


async def test_a_failed_act_with_a_send_in_the_window_is_unknown():
    """The sweep marked it `failed` after the process lost the row — but a
    delivered send exists minutes from `fired_at`. `unknown` is the honest
    word: the row that would prove it is the row that was lost."""
    store = FakeReminderStore()
    await store.create(_reminder())
    fired_ms = int((T0 - timedelta(minutes=5)).timestamp() * 1000)
    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS,
        reminders=store,
        record_query=_Sends([_send(9, fired_ms + 30_000)]),
    )
    assert view.status == "ok"
    act = next(e for e in view.entries if e.kind == "act")
    assert act.delivery == "unknown"


async def test_a_failed_act_with_no_send_near_it_is_none():
    store = FakeReminderStore()
    await store.create(_reminder())
    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS,
        reminders=store, record_query=_Sends(),
    )
    act = next(e for e in view.entries if e.kind == "act")
    assert act.delivery == "none"


async def test_a_source_that_raises_is_unavailable_never_nothing_found():
    """An unread premise must not degrade to «the unit is empty»: the
    source's own status names which read failed."""
    store = FakeReminderStore()
    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS,
        reminders=store, ledger=_Broken(),
    )
    assert view.status == "unavailable"
    assert ("ledger", "unavailable") in view.sources


async def test_the_pending_view_is_unit_wide_not_room_wide():
    """A row waiting for room-b is visible to a read asked about the whole
    unit — the visibility gap the incident reported."""
    store = FakeReminderStore()
    await store.create(_reminder(conversation_id="room-b",
                                 state=ReminderState.PENDING, fired_at=None,
                                 outcome=None))
    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS,
        reminders=store,
    )
    act = next(e for e in view.entries if e.kind == "act")
    assert act.status == "pending" and act.destination == "signal:room-b"


def test_the_renderer_names_withheld_entries_and_coverage():
    entry = SimpleNamespace(
        kind="decision", id="d-1", scope="signal:room-b", status="open",
        subject="the question", revision="2", destination="", action="",
        subject_ref="", refs=("d-1",), at_ms=T0_MS, provenance="cognition",
        delivery="", permitted=False,
    )
    from nora.domain.shared_state import SharedView
    view = SharedView(
        unit="u1", status="partial", as_of_ms=T0_MS,
        entries=(entry,), sources=(("ledger", "partial"),),
    )
    block = render_shared_state(view, now_ms=T0_MS)
    assert block is not None and block.name == "shared_cognitive_state"
    # Withheld entries name the room only as «another» — the raw
    # platform/conversation_id of a room this read cannot see never renders.
    assert "open in another room · withheld" in block.body
    assert "room-b" not in block.body
    assert "ledger partial" in block.body


def test_nothing_found_renders_no_block():
    from nora.domain.shared_state import SharedView
    view = SharedView(unit="u1", status="nothing_found", as_of_ms=T0_MS)
    assert render_shared_state(view, now_ms=T0_MS) is None


async def test_a_destination_read_withholds_other_rooms_content():
    """The disclosure boundary a destination read must hold: this room's
    sends stay readable, another room's are entries that exist but carry no
    content — existence visible, words withheld."""
    room_b_send = _send(11, T0_MS - 60_000)
    room_b_send = SimpleNamespace(
        platform="signal", conversation_id="room-b", row=room_b_send.row,
    )
    own_send = _send(10, T0_MS - 30_000)
    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS,
        room=ROOM_A,
        record_query=_Sends([own_send, room_b_send]),
    )
    by_room = {e.scope: e for e in view.entries if e.kind == "send"}
    assert by_room["signal:room-a"].permitted is True
    assert by_room["signal:room-b"].permitted is False


async def test_a_unit_read_sees_every_room():
    """The session's read is the unit itself (DEC-231): nothing is
    withheld — the same stores, one boundary wider."""
    room_b_send = _send(11, T0_MS - 60_000)
    room_b_send = SimpleNamespace(
        platform="signal", conversation_id="room-b", row=room_b_send.row,
    )
    view = await read_shared(
        unit="u1", rooms=ROOMS, ai_peer="nora", now_ms=T0_MS,
        record_query=_Sends([_send(10, T0_MS - 30_000), room_b_send]),
    )
    assert all(e.permitted for e in view.entries)
