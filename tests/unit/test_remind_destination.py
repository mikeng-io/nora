"""Proactive destination routing (design/dreamer-agent-loop/40, issue #248).

The live failure: Blake asked for the night off in **Discord Zone 42**; the
note-candidates pass folded it into a friends-unit note with
`established_in = Zone 42`; ~2h later the proactive fire path spoke into
**Signal Friends** — the unit's home room — so the person the answer
was about never saw it.

These tests pin the decision: a note-linked proactive row is stamped into
the note's own room, never the home room, and every way the origin cannot
be reached refuses the row rather than falling back home.
"""

from __future__ import annotations

import pytest

from nora.agent.tools import OriginRoom
from nora.obs.metrics import Metrics
from nora.testing.reminders import FakeReminderStore
from tests.test_reminder_tools import (
    CONVERSATION,
    SOON,
    T0,
    UNIT,
    _tools,
)

#: The conversation id of the room a note is SEEDED in when the test wants
#: the origin to differ from the home room the tool set is built for.
FOREIGN_CONVERSATION = "conv-2"

#: A room with `proactive = False` — the operator has not granted
#: unprompted speech there, so a note born there must not fire at all.
UNTRUSTED_CONVERSATION = "conv-3"


def _home_origin_map() -> dict[str, OriginRoom]:
    """The map the tool set is handed at build: `conv-1` is the home room the
    `_tools` fixture builds the whole set against, so the map must carry it
    for destination resolution to work — and carry nothing else unless the
    test says so."""
    return {
        CONVERSATION: OriginRoom(
            platform="signal", conversation_id=CONVERSATION,
            conversation_type="group", proactive=True, unit=UNIT,
        ),
    }


def _foreign_map() -> dict[str, OriginRoom]:
    """Home (`conv-1`, signal) + a foreign Discord room the note may be born
    in (`conv-2`)."""
    return {
        CONVERSATION: OriginRoom(
            platform="signal", conversation_id=CONVERSATION,
            conversation_type="group", proactive=True, unit=UNIT,
        ),
        FOREIGN_CONVERSATION: OriginRoom(
            platform="discord", conversation_id=FOREIGN_CONVERSATION,
            conversation_type="group", proactive=True, unit=UNIT,
        ),
    }


@pytest.mark.asyncio
async def test_a_note_born_elsewhere_fires_into_its_own_room_not_home() -> None:
    """#248's core: the note's `established_in` (Discord Zone 42) differs from
    the home room the adaptor stands in (Signal Friends). The row must land in
    the DISCORD room, with the DISCORD platform — the home room never sees it.
    """
    store = FakeReminderStore()
    from nora.testing import InMemoryNoteStore

    other_store = InMemoryNoteStore()
    await other_store.create(
        unit=UNIT, title="Blake 請假 request 未回覆",
        description="leave request, night off",
        uncertainty="whether he is back",
        closing_condition="he replies",
        established_in=FOREIGN_CONVERSATION,
        body="Blake 喺 Discord 話請假，喉嚨痛。",
        established_by="tag:eval", now_ms=int(T0.timestamp() * 1000),
        anchor_date=20260914,
    )

    tools = _tools(
        reminders=store, tagger=None, notes=other_store,
        dream_adaptor="proactive",
        origin_rooms=_foreign_map(),
    )

    result = await tools["remind"](
        when=SOON, text="Blake 病就好返先——請假批准",
        note="Blake 請假 request 未回覆",
    )

    assert result["status"] == "ok", result
    # The row must be destined for the Discord room the note was born in.
    rows = await store.all_pending(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row.platform == "discord"
    assert row.conversation_id == FOREIGN_CONVERSATION
    assert row.kind == "proactive"
    assert row.payload["established_in"] == FOREIGN_CONVERSATION
    # And nothing was written into the home room.
    assert await store.pending_for(CONVERSATION) == []


@pytest.mark.asyncio
async def test_a_note_born_in_an_untrusted_room_is_refused_not_relocated() -> None:
    """#248's fail-closed: if the origin room is not `proactive = true`, the
    row is refused — it must NOT silently fall back to the home room, since
    that is the bug this decision removes."""
    store = FakeReminderStore()
    metrics = Metrics()
    from nora.testing import InMemoryNoteStore

    other_store = InMemoryNoteStore()
    await other_store.create(
        unit=UNIT, title="wedding 討論",
        description="wedding talk",
        uncertainty="when",
        closing_condition="a date is picked",
        established_in=UNTRUSTED_CONVERSATION,
        body="傾緊個婚禮。",
        established_by="tag:eval", now_ms=int(T0.timestamp() * 1000),
        anchor_date=20260920,
    )

    tools = _tools(
        reminders=store, tagger=None, notes=other_store,
        metrics=metrics, dream_adaptor="proactive",
        origin_rooms={
            **_foreign_map(),
            UNTRUSTED_CONVERSATION: OriginRoom(
                platform="discord", conversation_id=UNTRUSTED_CONVERSATION,
                conversation_type="group", proactive=False, unit=UNIT,
            ),
        },
    )

    result = await tools["remind"](
        when=SOON, text="提醒佢哋諗吓個日期",
        note="wedding 討論",
    )

    assert result["status"] == "refused"
    assert "not trusted" in result["note"]
    assert await store.all_pending(limit=10) == []
    assert metrics.get(
        "nora_reminders_created_total", status="refused_origin_not_proactive",
    ) == 1


@pytest.mark.asyncio
async def test_an_unresolvable_origin_refuses_the_row() -> None:
    """If `established_in` is not in the origin map — a note born in a room
    that left policy, or that this deployment never learned — refuse; do not
    guess a destination."""
    store = FakeReminderStore()
    metrics = Metrics()
    from nora.testing import InMemoryNoteStore

    other_store = InMemoryNoteStore()
    await other_store.create(
        unit=UNIT, title="ghost room note",
        description="note from a room that is no longer listed",
        uncertainty="?", closing_condition="nothing",
        established_in="conv-gone",  # NOT in the map
        body="無從追溯。",
        established_by="tag:eval", now_ms=int(T0.timestamp() * 1000),
        anchor_date=20260915,
    )

    tools = _tools(
        reminders=store, tagger=None, notes=other_store,
        metrics=metrics, dream_adaptor="proactive",
        origin_rooms=_home_origin_map(),
    )

    result = await tools["remind"](
        when=SOON, text="提返呢條 ghost note",
        note="ghost room note",
    )

    assert result["status"] == "refused"
    assert "can't resolve" in result["note"]
    assert await store.all_pending(limit=10) == []
    assert metrics.get(
        "nora_reminders_created_total", status="refused_origin_unresolvable",
    ) == 1


@pytest.mark.asyncio
async def test_a_cross_unit_origin_refuses_the_row() -> None:
    """The origin room must belong to the unit the adaptor is keyed on.
    A note that drifted across social units is not something this pass may
    raise — that boundary is what a unit IS."""
    store = FakeReminderStore()
    metrics = Metrics()
    from nora.testing import InMemoryNoteStore

    other_store = InMemoryNoteStore()
    await other_store.create(
        unit=UNIT,  # the NOTE is in the friends unit
        title="婚禮 賓客表",
        description="wedding guest list",
        uncertainty="?", closing_condition="done",
        established_in=FOREIGN_CONVERSATION,
        body="婚禮嘢。",
        established_by="tag:eval", now_ms=int(T0.timestamp() * 1000),
        anchor_date=20260930,
    )

    # The origin room carries unit="wedding" — the room drifted into a
    # DIFFERENT social unit — while the note and the adaptor stand in friends.
    drifted = _foreign_map()
    drifted[FOREIGN_CONVERSATION] = OriginRoom(
        platform="discord", conversation_id=FOREIGN_CONVERSATION,
        conversation_type="group", proactive=True, unit="wedding",
    )

    tools = _tools(
        reminders=store, tagger=None, notes=other_store,
        metrics=metrics, dream_adaptor="proactive",
        origin_rooms=drifted,
    )

    result = await tools["remind"](
        when=SOON, text="提醒婚禮嘢",
        note="婚禮 賓客表",
    )

    assert result["status"] == "refused"
    assert "outside this unit" in result["note"]
    assert metrics.get(
        "nora_reminders_created_total", status="refused_origin_wrong_unit",
    ) == 1
