"""`ProactiveAdaptor` — the decide turn's inputs, parse and sink
(design/dreamer-agent-loop/39 D6-D9, DEC-162).

Every store here is the contract-suite double for its production sibling
(`InMemoryNoteStore`, `InMemoryStandingStore`, `FakeReminderStore`,
`FakeMemoryStore`), so what the adaptor renders is read off stores a shared
suite has already judged rather than off a fake that answers everything the
same way. No model anywhere: the runtime's loop is `tests/unit/
test_dreamer_runtime.py`'s subject, and the two eval suites are where the
model is measured.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from nora.domain.ports import DreamAdaptor, Proposal
from nora.domain.reminders import Reminder, ReminderState
from nora.domain.standing import Standing
from nora.language.proactive import (
    PROACTIVE_TOOLS,
    ProactiveAdaptor,
    WorkspaceRoom,
)
from nora.obs.metrics import Metrics
from nora.testing import InMemoryNoteStore, InMemoryStandingStore
from nora.testing.fakes import FakeMemoryStore
from nora.testing.reminders import FakeReminderStore

HKT = ZoneInfo("Asia/Hong_Kong")
# 2026-09-09 07:00 HKT, the Wednesday the design was closed on.
NOW = datetime(2026, 9, 8, 23, 0, tzinfo=UTC)
NOW_MS = int(NOW.timestamp() * 1000)
WS = "friends"
ROOMS = (
    WorkspaceRoom(platform="signal", conversation_id="sig-1", label="Friends", home=True),
    WorkspaceRoom(platform="discord", conversation_id="1/2", label="Zone 42"),
)


def _adaptor(
    *, notes=None, standing=None, reminders=None, memory=None, rooms=ROOMS,
    metrics=None,
):
    return ProactiveAdaptor(
        notes=notes if notes is not None else InMemoryNoteStore(),
        standing=standing,
        reminders=reminders if reminders is not None else FakeReminderStore(),
        memory=memory,
        rooms_of=lambda ws: rooms if ws == WS else (),
        ai_peer="nora", clock=lambda: NOW, timezone=HKT, metrics=metrics,
    )


async def _note(store, *, title="AI Hackathon dinner plan", anchor=20260912, **kw):
    return await store.create(
        workspace=WS, title=title, description="where to eat is open",
        uncertainty="where", closing_condition="a place is picked",
        established_in="sig-1", body="Cyberport Saturday; venue open.",
        established_by="tag:miles", now_ms=NOW_MS - 86_400_000, anchor_date=anchor,
        **kw,
    )


def test_the_declared_shape_is_the_designs():
    adaptor = _adaptor()
    assert isinstance(adaptor, DreamAdaptor)
    assert adaptor.key_kind == "workspace"
    assert adaptor.uses_watermark is False
    assert adaptor.envelope_key == "decisions"
    # 39 D3's list, exactly, spelled out here rather than compared to the
    # module's own constant (which would compare it to itself): every read
    # through the home room, one act.
    assert adaptor.tools == frozenset({
        "remind",
        "where_is", "saved_places", "route", "group_members",
        "about", "ask_memory", "search_memory_workspace",
        "note_read", "note_search",
        "web_search", "web_fetch", "weather", "now",
    })
    assert adaptor.tools == PROACTIVE_TOOLS
    assert "remind" in adaptor.tools
    for forbidden in ("send_reply", "send_to", "note_create", "note_update", "load_transcript"):
        assert forbidden not in adaptor.tools, forbidden


async def test_a_workspace_with_no_home_room_renders_nothing():
    """39 D2: the home room IS the operator's opt-in. No room, no pass — and
    no model call, which is what a blank `inputs` buys."""
    notes = InMemoryNoteStore()
    await _note(notes)
    adaptor = _adaptor(notes=notes, rooms=(ROOMS[1],))

    assert await adaptor.inputs(key=WS, batch=()) == ""


async def test_nothing_open_and_no_position_renders_nothing():
    adaptor = _adaptor(standing=InMemoryStandingStore())
    assert await adaptor.inputs(key=WS, batch=()) == ""


async def test_inputs_renders_the_index_the_rooms_the_log_and_the_self_card():
    """39 D9's table: the notes as an INDEX (handle, title, anchor, revisit,
    strikes — no body, no id), Standing per room with its age (D8), the
    decision log restricted to this workspace's rooms with outcomes (D7),
    and the self card framed as DEC-96 frames it."""
    notes = InMemoryNoteStore()
    note = await _note(notes)
    standing = InMemoryStandingStore()
    await standing.write(standing=Standing(
        platform="signal", conversation_id="sig-1",
        body="Dinner Saturday is agreed; where is still open.",
        fold_count=3, updated_at=NOW_MS - 2 * 3_600_000,
    ))
    log = FakeReminderStore()
    await log.create(Reminder(
        id=uuid.uuid4(), requester_person_id="nora", platform="signal",
        conversation_id="sig-1", conversation_type="group",
        text="nudge about the venue", created_at=NOW - timedelta(days=1),
        due_at=NOW - timedelta(hours=17), state=ReminderState.DONE,
        fired_at=NOW - timedelta(hours=17), outcome="silent", kind="proactive",
        payload={"note_id": note.id, "note_title": note.title, "intent": "nudge"},
    ))
    # Another workspace's row: joined OUT through the room map.
    await log.create(Reminder(
        id=uuid.uuid4(), requester_person_id="nora", platform="signal",
        conversation_id="wedding-room", conversation_type="group",
        text="wedding seating", created_at=NOW - timedelta(days=1),
        due_at=NOW, state=ReminderState.PENDING, fired_at=None, outcome=None,
        kind="proactive",
    ))
    memory = FakeMemoryStore(conclusions={"nora": ["nora said she would find a place"]})
    adaptor = _adaptor(notes=notes, standing=standing, reminders=log, memory=memory)

    text = await adaptor.inputs(key=WS, batch=())

    assert "<now>2026-09-09 Wed 07:00 (Asia/Hong_Kong)</now>" in text
    assert '<home_room platform="signal" label="Friends"' in text
    assert (
        "[1] «AI Hackathon dinner plan» — where to eat is open; about Sat 12 Sep, in 3 days"
        in text
    )
    assert note.id not in text                      # the index, never the id
    assert "Cyberport Saturday" not in text         # never the body
    assert "- Friends (signal), as of 2 h ago: Dinner Saturday is agreed" in text
    assert "Zone 42" not in text.split("<rooms")[1].split("</rooms>")[0]
    assert "«nudge about the venue» — silent" in text
    assert "wedding seating" not in text
    assert "<self_card" in text and "nora said she would find a place" in text
    assert "not a description of who you are" in text


async def test_strikes_and_a_due_revisit_are_said_on_the_line():
    notes = InMemoryNoteStore()
    note = await _note(notes)
    for at in (NOW_MS - 2, NOW_MS - 1):
        await notes.record_wake(
            workspace=WS, note_id=note.id, next_revisit_at=NOW_MS - 1, now_ms=at,
        )
    adaptor = _adaptor(notes=notes)

    text = await adaptor.inputs(key=WS, batch=())

    assert "revisit due" in text
    assert "you decided to raise this 1x before, no reply since" in text


async def test_a_failed_standing_read_is_rendered_as_unread_not_as_no_position():
    """R-27 at the prompt: «could not read» and «no position» are different
    sentences, and the counter says which happened."""
    class _Down:
        async def read(self, **_kw: str):
            raise RuntimeError("standing store down")

    notes = InMemoryNoteStore()
    await _note(notes)
    metrics = Metrics()
    adaptor = _adaptor(notes=notes, standing=_Down(), metrics=metrics)

    text = await adaptor.inputs(key=WS, batch=())

    assert "(could not read the position)" in text
    assert metrics.get("nora_errors_total", stage="proactive_standing") == 2


async def test_accept_maps_handles_and_drops_what_it_cannot_read():
    """Handles, never ids (the curator's screen argument); an unknown handle,
    an unknown action and a non-string reason are dropped, never raised."""
    notes = InMemoryNoteStore()
    note = await _note(notes)
    adaptor = _adaptor(notes=notes)
    await adaptor.inputs(key=WS, batch=())

    parsed = await adaptor.accept(key=WS, items=(
        {"note": "1", "action": "remind", "why": "Saturday is near"},
        {"note": 1, "action": "decline", "why": "int handles are fine"},
        {"action": "decline", "why": "nothing else today"},
        {"note": "9", "action": "remind", "why": "unknown handle"},
        {"note": "1", "action": "maybe", "why": "unknown action"},
        {"note": "1", "action": "remind", "why": 7},
    ))

    assert [(d.note_id, d.action) for d in parsed] == [  # type: ignore[attr-defined]
        (note.id, "remind"), (note.id, "decline"), (None, "decline"),
    ]


async def test_commit_records_one_wake_per_note_it_decided_to_raise():
    """39 D6: bookkeeping is not read-only — a wake per note RAISED, once
    even when two items name it; a decline is not a raise and wakes nothing
    (a strike is «raised and nobody replied», not «looked at»); a
    workspace-level item wakes nothing. Returns wakes recorded, and every
    decision is counted."""
    notes = InMemoryNoteStore()
    first = await _note(notes)
    second = await _note(notes, title="Trip to Taipei", anchor=20261003)
    metrics = Metrics()
    adaptor = _adaptor(notes=notes, metrics=metrics)
    await adaptor.inputs(key=WS, batch=())
    payloads = await adaptor.accept(key=WS, items=(
        {"note": "1", "action": "remind", "why": "a"},
        {"note": "1", "action": "remind", "why": "b"},
        {"note": "2", "action": "decline", "why": "c"},
        {"action": "decline", "why": "d"},
    ))
    proposals = tuple(
        Proposal(payload=p, instruction_sha256="a" * 64, model="m", established_by="dream:x")
        for p in payloads
    )

    written = await adaptor.commit(key=WS, proposals=proposals, now_ms=NOW_MS)

    assert written == 1
    woken = {n.id: n for n in await notes.standing(workspace=WS)}
    assert woken[first.id].last_wake_revision_id == first.head_revision_id
    assert woken[second.id].last_wake_revision_id is None
    assert woken[first.id].revisit_at is not None
    assert metrics.get("nora_proactive_decisions_total", action="remind") == 2
    assert metrics.get("nora_proactive_decisions_total", action="decline") == 2


async def test_an_empty_commit_records_nothing():
    notes = InMemoryNoteStore()
    note = await _note(notes)
    adaptor = _adaptor(notes=notes)
    await adaptor.inputs(key=WS, batch=())

    assert await adaptor.commit(key=WS, proposals=(), now_ms=NOW_MS) == 0
    (unwoken,) = await notes.standing(workspace=WS)
    assert unwoken.id == note.id and unwoken.last_wake_revision_id is None


@pytest.mark.parametrize("body", [None, ""])
async def test_inputs_ignores_the_batch(body):
    """`uses_watermark = False`: the batch is the port's shape, not an input."""
    from nora.domain.models import DeliveryStatus, TranscriptRow

    row = TranscriptRow(
        id=1, sender_id="p", sender_name="A", ts=1, body=body, is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None,
    )
    assert await _adaptor().inputs(key=WS, batch=(row,)) == ""


async def test_a_failed_decision_log_read_refuses_the_pass():
    """R-27 at the input (audit 2026-09-09): the decision log is the record of
    what she already scheduled, so a pass that cannot read it must not go on
    to write rows on the strength of nothing. `inputs` raises, and the
    runtime maps that to `unavailable` — never a prompt with a placeholder."""
    class _Down:
        async def history(self, **_kw: object):
            raise RuntimeError("reminder store down")

    notes = InMemoryNoteStore()
    await _note(notes)
    adaptor = _adaptor(notes=notes, reminders=_Down())

    with pytest.raises(RuntimeError, match="reminder store down"):
        await adaptor.inputs(key=WS, batch=())
