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
    PASS_CLOCK_NAME,
    PROACTIVE_TOOLS,
    WAKE_CLOCK_NAME,
    ProactiveAdaptor,
    WorkspaceRoom,
)
from nora.obs.metrics import Metrics
from nora.testing import (
    InMemoryDreamScheduleClock,
    InMemoryNoteStore,
    InMemoryStandingStore,
)
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
    metrics=None, clock_store=None, records=None, now=None, quiet_seconds=1800.0,
):
    return ProactiveAdaptor(
        notes=notes if notes is not None else InMemoryNoteStore(),
        standing=standing,
        reminders=reminders if reminders is not None else FakeReminderStore(),
        memory=memory,
        rooms_of=lambda ws: rooms if ws == WS else (),
        ai_peer="nora", clock=(lambda: now) if now is not None else (lambda: NOW),
        timezone=HKT, metrics=metrics, clock_store=clock_store, records=records,
        quiet_seconds=quiet_seconds,
    )


async def _note(
    store, *, title="AI Hackathon dinner plan", anchor=20260912,
    now_ms=NOW_MS - 86_400_000, **kw,
):
    return await store.create(
        workspace=WS, title=title, description="where to eat is open",
        uncertainty="where", closing_condition="a place is picked",
        established_in="sig-1", body="Cyberport Saturday; venue open.",
        established_by="tag:miles", now_ms=now_ms, anchor_date=anchor,
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
        # `lookup_person` since 2026-09-10 (issue #145). Without it nothing
        # here answers «who is this handle?»: `group_members` returns real
        # names with no aliases beside them, so a note written in the group's
        # own nicknames had only free-text memory search to resolve against.
        # Measured on `friends` (Langfuse d63f40f2): six of ten tool calls
        # were `search_memory_workspace`, five asking who `@熱咖啡☕少奶多糖`
        # and `@隨風而去` are, until R-7 stopped the turn.
        "lookup_person",
        "about", "ask_memory", "search_memory_workspace",
        "note_read", "note_search",
        "web_search", "web_fetch", "weather", "now",
    })
    assert adaptor.tools == PROACTIVE_TOOLS
    assert "remind" in adaptor.tools
    for forbidden in ("send_reply", "send_to", "note_create", "note_update", "load_transcript"):
        assert forbidden not in adaptor.tools, forbidden


def test_the_adaptor_can_resolve_a_nickname_without_searching_memory_for_it():
    """Issue #145, as the property rather than the list above.

    `group_members` answers WHO IS HERE with real names; nothing in it maps the
    `@handle` a note is actually written in. `lookup_person` is the verb that
    does, and its absence is what turned one decide turn into six searches for
    the same answer — depth where an index belongs.

    Asserted as a pair so removing `lookup_person` and leaving the roster verb
    cannot pass: the point is that BOTH exist, at their two altitudes.
    """
    assert "group_members" in PROACTIVE_TOOLS, "the roster is the index"
    assert "lookup_person" in PROACTIVE_TOOLS, "and this is the depth under it"


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


# ---- issue #136: the deterministic check before any model call ----------------


class _Records:
    """`recent` for the quiet check: the newest row per room, by ts."""

    def __init__(self, last_ts: dict[str, int]) -> None:
        self._last = last_ts

    async def recent(self, *, conversation_id: str, limit: int):
        from nora.domain.models import DeliveryStatus, TranscriptRow

        ts = self._last.get(conversation_id)
        if ts is None:
            return []
        return [TranscriptRow(
            id=1, sender_id="p", sender_name="A", ts=ts, body="hi", is_nora=False,
            delivery_status=DeliveryStatus.SENT, raw_json=None,
        )]


async def _rendered_once(adaptor, clock, *, at_ms):
    """Render one pass and commit it, so the pass clock holds `at_ms`."""
    text = await adaptor.inputs(key=WS, batch=())
    assert text, "the first pass renders (the floor)"
    await adaptor.commit(key=WS, proposals=(), now_ms=at_ms)
    assert await clock.last_fired(adaptor_name=PASS_CLOCK_NAME, key=WS) == at_ms


async def test_the_first_pass_renders_and_stamps_the_pass_clock():
    notes = InMemoryNoteStore()
    await _note(notes)
    clock = InMemoryDreamScheduleClock()
    metrics = Metrics()
    adaptor = _adaptor(notes=notes, clock_store=clock, metrics=metrics)

    await _rendered_once(adaptor, clock, at_ms=NOW_MS)

    assert metrics.get("nora_proactive_pass_total", reason="daily") == 1


async def test_an_unchanged_workspace_renders_nothing_the_same_day():
    """The deterministic check: nothing moved since the rendered pass, same
    HK day → `""`, so the runtime makes no model call. A blank tick does
    not touch the pass clock."""
    notes = InMemoryNoteStore()
    await _note(notes)
    clock = InMemoryDreamScheduleClock()
    metrics = Metrics()
    adaptor = _adaptor(notes=notes, clock_store=clock, metrics=metrics)
    await _rendered_once(adaptor, clock, at_ms=NOW_MS)
    later = _adaptor(notes=notes, clock_store=clock, metrics=metrics,
                     now=NOW + timedelta(hours=3))

    assert await later.inputs(key=WS, batch=()) == ""
    await later.commit(key=WS, proposals=(), now_ms=NOW_MS + 3 * 3_600_000)

    assert metrics.get("nora_proactive_pass_total", reason="unchanged") == 1
    assert await clock.last_fired(adaptor_name=PASS_CLOCK_NAME, key=WS) == NOW_MS


async def test_a_tag_path_revision_in_a_quiet_room_renders_and_a_background_one_does_not():
    notes = InMemoryNoteStore()
    note = await _note(notes)
    clock = InMemoryDreamScheduleClock()
    metrics = Metrics()
    await _rendered_once(_adaptor(notes=notes, clock_store=clock), clock, at_ms=NOW_MS)
    t1 = NOW + timedelta(hours=2)
    t1_ms = int(t1.timestamp() * 1000)

    background = await notes.append_revision(
        workspace=WS, note_id=note.id, body="still open", parent_revision_id=note.head_revision_id,
        established_by="dream:note-curation:friends:1", now_ms=t1_ms - 60_000,
    )
    quiet_rooms = _Records({"sig-1": t1_ms - 3_600_000, "1/2": t1_ms - 3_600_000})
    assert await _adaptor(
        notes=notes, clock_store=clock, metrics=metrics, now=t1, records=quiet_rooms,
    ).inputs(key=WS, batch=()) == ""
    assert metrics.get("nora_proactive_pass_total", reason="unchanged") == 1

    await notes.append_revision(
        workspace=WS, note_id=note.id, body="Ada booked", parent_revision_id=background.id,
        established_by="tag:ada", now_ms=t1_ms - 30_000,
    )
    text = await _adaptor(
        notes=notes, clock_store=clock, metrics=metrics, now=t1, records=quiet_rooms,
    ).inputs(key=WS, batch=())

    assert text
    assert metrics.get("nora_proactive_pass_total", reason="changed") == 1


async def _changed_note(notes, clock, *, at_ms):
    """A workspace whose ORIENT state has MOVED since the last rendered pass:
    one note revised on the tag path. Everything after it turns on the quiet
    condition alone."""
    note = await _note(notes)
    await _rendered_once(_adaptor(notes=notes, clock_store=clock), clock, at_ms=NOW_MS)
    await notes.append_revision(
        workspace=WS, note_id=note.id, body="moved", parent_revision_id=note.head_revision_id,
        established_by="tag:miles", now_ms=at_ms - 30_000,
    )


async def test_a_change_is_held_while_the_home_room_is_still_talking():
    notes = InMemoryNoteStore()
    clock = InMemoryDreamScheduleClock()
    metrics = Metrics()
    t1 = NOW + timedelta(hours=2)
    t1_ms = int(t1.timestamp() * 1000)
    await _changed_note(notes, clock, at_ms=t1_ms)
    # `sig-1` is the HOME room and it spoke a minute ago.
    talking = _Records({"sig-1": t1_ms - 60_000, "1/2": t1_ms - 3_600_000})

    assert await _adaptor(
        notes=notes, clock_store=clock, metrics=metrics, now=t1, records=talking,
    ).inputs(key=WS, batch=()) == ""

    assert metrics.get("nora_proactive_pass_total", reason="held_quiet") == 1


async def test_a_home_room_the_quiet_check_cannot_see_is_not_read_as_quiet():
    """`_quiet` iterating no rooms must FAIL CLOSED.

    `inputs` returns `no_home_room` before `_pass_reason` is reached, so on
    the live path the filtered sequence is never empty — a single-caller
    invariant, and that is exactly why the empty case is asserted here rather
    than left to the loop. An empty `for` falls through to `return True`,
    which reads as QUIET and opens the gate on precisely the wiring gap the
    `no_home_room` guard exists to catch. Every other failure in this method
    holds the pass (a missing record store, a failed read); this one did not.

    Driven at `_quiet` directly because `inputs` cannot reach it with an
    empty home set — which is the point.
    """
    adaptor = _adaptor(records=_Records({}))

    assert await adaptor._quiet((), now_ms=int(NOW.timestamp() * 1000)) is False


async def test_a_change_is_not_held_by_a_sibling_room_still_talking():
    """The live defect this rule was changed for (2026-09-10, design 39 D10).
    A proactive row fires into the HOME room, so a sibling room's traffic
    cannot be what stops her speaking there — on the `friends` workspace the
    sibling was Zone 42, a games room, and it held four consecutive hourly
    ticks on a note anchored that same day."""
    notes = InMemoryNoteStore()
    clock = InMemoryDreamScheduleClock()
    metrics = Metrics()
    t1 = NOW + timedelta(hours=2)
    t1_ms = int(t1.timestamp() * 1000)
    await _changed_note(notes, clock, at_ms=t1_ms)
    # The home room has been silent an hour; the sibling spoke a minute ago.
    sibling_loud = _Records({"sig-1": t1_ms - 3_600_000, "1/2": t1_ms - 60_000})

    assert await _adaptor(
        notes=notes, clock_store=clock, metrics=metrics, now=t1, records=sibling_loud,
    ).inputs(key=WS, batch=())

    assert metrics.get("nora_proactive_pass_total", reason="changed") == 1
    assert metrics.get("nora_proactive_pass_total", reason="held_quiet") == 0


async def test_a_new_note_a_due_revisit_and_a_standing_move_each_count_as_change():
    notes = InMemoryNoteStore()
    first = await _note(notes)
    clock = InMemoryDreamScheduleClock()
    await _rendered_once(_adaptor(notes=notes, clock_store=clock), clock, at_ms=NOW_MS)
    t1 = NOW + timedelta(hours=1)
    t1_ms = int(t1.timestamp() * 1000)

    # a new note
    await _note(notes, title="Trip to Taipei", anchor=20261003, now_ms=t1_ms - 1000)
    assert await _adaptor(notes=notes, clock_store=clock, now=t1).inputs(key=WS, batch=())
    await clock.record_fire(adaptor_name=PASS_CLOCK_NAME, key=WS, at_ms=t1_ms)

    # a revisit crossing due
    t2 = t1 + timedelta(hours=1)
    t2_ms = int(t2.timestamp() * 1000)
    await notes.record_wake(
        workspace=WS, note_id=first.id, next_revisit_at=t2_ms - 1, now_ms=t1_ms + 1,
    )
    assert await _adaptor(notes=notes, clock_store=clock, now=t2).inputs(key=WS, batch=())
    await clock.record_fire(adaptor_name=PASS_CLOCK_NAME, key=WS, at_ms=t2_ms)

    # a Standing rewrite
    t3 = t2 + timedelta(hours=1)
    t3_ms = int(t3.timestamp() * 1000)
    standing = InMemoryStandingStore()
    await standing.write(standing=Standing(
        platform="signal", conversation_id="sig-1", body="Venue is Sai Kung.",
        fold_count=1, updated_at=t3_ms - 1000,
    ))
    assert await _adaptor(
        notes=notes, standing=standing, clock_store=clock, now=t3,
    ).inputs(key=WS, batch=())


async def test_the_daily_floor_renders_once_a_day_after_the_hour_with_nothing_changed():
    notes = InMemoryNoteStore()
    await _note(notes)
    clock = InMemoryDreamScheduleClock()
    metrics = Metrics()
    await _rendered_once(_adaptor(notes=notes, clock_store=clock), clock, at_ms=NOW_MS)
    # 06:30 HKT next day: past midnight, before the hour → nothing.
    early = datetime(2026, 9, 9, 22, 30, tzinfo=UTC)
    assert await _adaptor(
        notes=notes, clock_store=clock, metrics=metrics, now=early,
    ).inputs(key=WS, batch=()) == ""
    # 07:05 HKT next day → the floor.
    floor = datetime(2026, 9, 9, 23, 5, tzinfo=UTC)
    assert await _adaptor(
        notes=notes, clock_store=clock, metrics=metrics, now=floor,
    ).inputs(key=WS, batch=())
    assert metrics.get("nora_proactive_pass_total", reason="daily") == 1
    assert metrics.get("nora_proactive_pass_total", reason="unchanged") == 1


async def test_no_pass_clock_renders_every_tick_and_says_so():
    """The wiring gap is a series that climbs, never a silence: with no
    clock the check cannot run, so every tick renders under `unclocked`."""
    notes = InMemoryNoteStore()
    await _note(notes)
    metrics = Metrics()
    adaptor = _adaptor(notes=notes, metrics=metrics)

    assert await adaptor.inputs(key=WS, batch=())
    assert await adaptor.inputs(key=WS, batch=())

    assert metrics.get("nora_proactive_pass_total", reason="unclocked") == 2


async def test_a_note_is_woken_at_most_once_per_day_across_passes():
    """39 D6 as amended by #136: two remind decisions on one note in one HK
    day charge one wake; the next day charges another. Each pass here
    RENDERS (a tag-path change, then the daily floor), so the zero in the
    middle is the wake rule and not a blank tick."""
    notes = InMemoryNoteStore()
    note = await _note(notes)
    clock = InMemoryDreamScheduleClock()

    async def _decide(at: datetime) -> int:
        adaptor = _adaptor(notes=notes, clock_store=clock, now=at)
        assert await adaptor.inputs(key=WS, batch=()), "this pass must render"
        payloads = await adaptor.accept(key=WS, items=(
            {"note": "1", "action": "remind", "why": "x"},
        ))
        proposals = tuple(
            Proposal(payload=p, instruction_sha256="a" * 64, model="m", established_by="d")
            for p in payloads
        )
        return await adaptor.commit(key=WS, proposals=proposals, now_ms=int(at.timestamp() * 1000))

    assert await _decide(NOW) == 1                                  # the floor
    head = (await notes.read(workspace=WS, note_id=note.id))[1]
    await notes.append_revision(
        workspace=WS, note_id=note.id, body="moved", parent_revision_id=head.id,
        established_by="tag:miles", now_ms=NOW_MS + 3 * 3_600_000,
    )
    assert await _decide(NOW + timedelta(hours=4)) == 0             # changed, same day
    assert await _decide(NOW + timedelta(hours=26)) == 1            # next day's floor
    last = await clock.last_fired(adaptor_name=WAKE_CLOCK_NAME, key=f"{WS}/{note.id}")
    assert last == int((NOW + timedelta(hours=26)).timestamp() * 1000)


async def _proactive_row(log, note_id, *, created_at, conversation_id="sig-1"):
    await log.create(Reminder(
        id=uuid.uuid4(), requester_person_id="nora", platform="signal",
        conversation_id=conversation_id, conversation_type="group",
        text="say the venue is still open", created_at=created_at,
        due_at=created_at + timedelta(hours=6), state=ReminderState.PENDING,
        fired_at=None, outcome=None, kind="proactive",
        payload={"note_id": note_id, "note_title": "t", "intent": "nudge"},
    ))


async def test_a_row_the_envelope_never_reported_is_still_charged_a_wake():
    """`remind` writes inside the model loop; the envelope is the model's
    ACCOUNT of that write, and three paths lose it (screened item, unknown
    handle, no readable sample). `commit` reads the rows back."""
    notes = InMemoryNoteStore()
    note = await _note(notes)
    log = FakeReminderStore()
    clock_store = InMemoryDreamScheduleClock()
    metrics = Metrics()
    adaptor = _adaptor(
        notes=notes, reminders=log, clock_store=clock_store, metrics=metrics,
    )

    await adaptor.inputs(key=WS, batch=())
    # The row lands inside the loop; the envelope comes back EMPTY.
    await _proactive_row(log, note.id, created_at=NOW)
    wakes = await adaptor.commit(key=WS, proposals=(), now_ms=NOW_MS)

    assert wakes == 1
    assert metrics.get("nora_proactive_reconciled_total") == 1
    assert await clock_store.last_fired(
        adaptor_name=WAKE_CLOCK_NAME, key=f"{WS}/{note.id}",
    ) == NOW_MS


async def test_a_row_orphaned_by_an_unavailable_run_is_charged_by_the_next_pass():
    """A run with no readable sample returns before `commit`, so its rows are
    charged by the NEXT pass: that run never stamped the pass clock, so the
    window still reaches back over them."""
    notes = InMemoryNoteStore()
    note = await _note(notes)
    log = FakeReminderStore()
    clock_store = InMemoryDreamScheduleClock()
    # A pass rendered yesterday and was accounted for.
    await clock_store.record_fire(
        adaptor_name=PASS_CLOCK_NAME, key=WS, at_ms=NOW_MS - 86_400_000,
    )
    # Then a pass wrote a row and died unreadable — no `commit`, no wake.
    await _proactive_row(log, note.id, created_at=NOW - timedelta(hours=2))
    adaptor = _adaptor(notes=notes, reminders=log, clock_store=clock_store)

    await adaptor.inputs(key=WS, batch=())
    wakes = await adaptor.commit(key=WS, proposals=(), now_ms=NOW_MS)

    assert wakes == 1


async def test_reconcile_never_double_charges_what_the_envelope_reported():
    notes = InMemoryNoteStore()
    note = await _note(notes)
    log = FakeReminderStore()
    clock_store = InMemoryDreamScheduleClock()
    metrics = Metrics()
    adaptor = _adaptor(
        notes=notes, reminders=log, clock_store=clock_store, metrics=metrics,
    )

    await adaptor.inputs(key=WS, batch=())
    await _proactive_row(log, note.id, created_at=NOW)
    parsed = await adaptor.accept(
        key=WS, items=({"action": "remind", "note": "1", "why": "it is Saturday"},),
    )
    wakes = await adaptor.commit(
        key=WS,
        proposals=tuple(
            Proposal(payload=p, instruction_sha256="x", model="m", established_by="e")
            for p in parsed
        ),
        now_ms=NOW_MS,
    )

    assert wakes == 1
    assert metrics.get("nora_proactive_reconciled_total") == 0


async def test_reconcile_joins_rows_to_the_workspace_and_ignores_plain_reminders():
    notes = InMemoryNoteStore()
    note = await _note(notes)
    log = FakeReminderStore()
    clock_store = InMemoryDreamScheduleClock()
    adaptor = _adaptor(notes=notes, reminders=log, clock_store=clock_store)

    await adaptor.inputs(key=WS, batch=())
    # Another workspace's room, and a tag-path row in this one: neither is a
    # decision this pass made.
    await _proactive_row(log, note.id, created_at=NOW, conversation_id="wedding-room")
    await log.create(Reminder(
        id=uuid.uuid4(), requester_person_id="nora", platform="signal",
        conversation_id="sig-1", conversation_type="group", text="milk",
        created_at=NOW, due_at=NOW + timedelta(hours=1),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
        payload={"note_id": note.id},
    ))
    wakes = await adaptor.commit(key=WS, proposals=(), now_ms=NOW_MS)

    assert wakes == 0


async def test_a_blank_tick_reconciles_nothing():
    """`inputs` rendered nothing, so no pass ran and no row is this pass's."""
    notes = InMemoryNoteStore()
    note = await _note(notes)
    log = FakeReminderStore()
    adaptor = _adaptor(notes=notes, reminders=log, rooms=())

    await adaptor.inputs(key=WS, batch=())
    await _proactive_row(log, note.id, created_at=NOW)

    assert await adaptor.commit(key=WS, proposals=(), now_ms=NOW_MS) == 0


async def test_a_due_tick_that_renders_nothing_is_not_counted_as_a_pass():
    """`daily`/`changed` are the series «a pass ran» is read off. A workspace
    with no open notes and no Standing is due and renders nothing, and
    counting that as a pass would inflate the denominator with blank ticks."""
    metrics = Metrics()
    adaptor = _adaptor(notes=InMemoryNoteStore(), metrics=metrics)

    assert await adaptor.inputs(key=WS, batch=()) == ""
    assert metrics.get("nora_proactive_pass_total", reason="nothing_shown") == 1
    assert metrics.get("nora_proactive_pass_total", reason="daily") == 0
    assert metrics.get("nora_proactive_pass_total", reason="unclocked") == 0


async def test_a_workspace_with_no_home_room_is_counted_rather_than_silent():
    """An operator who never named a home room otherwise sees no series at
    all — a wiring gap reading as quiet."""
    metrics = Metrics()
    notes = InMemoryNoteStore()
    await _note(notes)
    adaptor = _adaptor(
        notes=notes, metrics=metrics,
        rooms=(WorkspaceRoom(
            platform="signal", conversation_id="sig-1", label="Friends",
        ),),
    )

    assert await adaptor.inputs(key=WS, batch=()) == ""
    assert metrics.get("nora_proactive_pass_total", reason="no_home_room") == 1


def test_the_objectives_stated_ration_is_the_ration_the_guard_enforces():
    """DEC-150's rule, applied to prose: the objective NAMES its per-tool
    ceilings so the model can triage before it spends them, and an authored
    copy of a number the code owns goes stale silently. This is the read-back
    that stops it."""
    import re
    from pathlib import Path

    from nora.agent.tools import NATIVE_TOOLS

    text = (
        Path(__file__).resolve().parents[2]
        / "nora" / "dreamer" / "objectives" / "proactive.md"
    ).read_text(encoding="utf-8")
    stated = {
        name: int(n)
        for name, n in re.findall(r"`(\w+)` (\d+)", text)
        if name in NATIVE_TOOLS
    }
    assert len(stated) >= 6, "the ration line no longer names the tools it rations"
    assert stated == {
        name: NATIVE_TOOLS[name].budget for name in stated
    }, "the objective's stated ration has drifted from NATIVE_TOOLS"
