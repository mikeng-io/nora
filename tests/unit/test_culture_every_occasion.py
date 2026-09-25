"""`<culture>` reaches every runtime's prompt (design/culture-tier, DEC-207).

The note-candidates pass and the note curator are asserted here through
`inputs` — the RENDERED prompt — and by the key the store was READ with.
Proactive's `_cultures` and the rollup renderer are unit-tested here; their
end-to-end halves live beside their own harnesses:
`test_proactive_adaptor.py::test_inputs_carries_each_folded_rooms_account_under_rooms`,
`test_debrief.py::test_the_fold_reads_the_rooms_account_the_turn_read`,
`test_standing_event.py::test_the_event_fold_reads_the_rooms_account_by_primary_id`,
`test_engagement_pipeline.py::test_the_debrief_is_handed_the_account_the_turn_read`
and, for the tag path, `tests/test_peer_history.py::
test_a_tag_turn_carries_the_rooms_account_through_assemble_and_prefill`.

Every occasion also renders byte-identically to before when the room has no
account, which is the ordinary state for a while after the tier is enabled.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from nora.domain.culture import CultureRevision
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.engagement.debrief_render import render_rollup_input
from nora.language.note_candidates import NoteCandidateAdaptor
from nora.language.note_curator import NoteCuratorAdaptor
from nora.language.proactive import ProactiveAdaptor, UnitRoom
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.testing import InMemoryCultureStore, InMemoryNoteStore
from nora.testing.fakes import FakeRecordStore
from nora.testing.reminders import FakeReminderStore

ACCOUNT = "they decide by attrition; nobody ever says cancelled"
NOW_MS = 1_790_000_000_000
WS = "friends"


async def _seeded(**accounts: str) -> InMemoryCultureStore:
    store = InMemoryCultureStore()
    for key, body in accounts.items():
        await store.append(
            key=key, body=body, written_at=NOW_MS - 86_400_000,
            established_by="dream:culture:test")
    store.reads.clear()
    return store


def _row(row_id: int, body: str) -> TranscriptRow:
    return TranscriptRow(
        id=row_id, sender_id="p1", sender_name="Ada", ts=NOW_MS, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    )


# --- note candidates ----------------------------------------------------------


def _candidates(culture: InMemoryCultureStore | None) -> NoteCandidateAdaptor:
    notes = InMemoryNoteStore()
    return NoteCandidateAdaptor(
        notes=notes, candidates=notes, unit_for=lambda _: WS,
        label_for=lambda _: "Friends", description_for=lambda _: None,
        persons=Persons(by_person={}, index={}), platform_for=lambda _: "signal",
        metrics=Metrics(), culture=culture,
    )


async def test_note_candidates_carries_the_rooms_account_under_room():
    culture = await _seeded(g1=ACCOUNT)
    out = await _candidates(culture).inputs(key="g1", batch=[_row(1, "2200?")])
    assert ACCOUNT in out
    assert out.index("<room") < out.index("<culture") < out.index("<batch")
    assert culture.reads == ["g1"], "read by the scope key the fold wrote under"


async def test_note_candidates_without_an_account_is_unchanged():
    batch = [_row(1, "2200?")]
    off = await _candidates(None).inputs(key="g1", batch=batch)
    empty = await _candidates(await _seeded()).inputs(key="g1", batch=batch)
    assert "<culture" not in off
    assert empty == off


# --- note curator -------------------------------------------------------------


async def _curator_prompt(culture, room_for) -> str:
    notes = InMemoryNoteStore()
    await notes.create(
        unit=WS, title="Dinner, 9 Sept", description="one line",
        uncertainty="undecided", closing_condition="somebody decides",
        established_in="thread-7", body="Nobody booked.",
        established_by="tag:miles", now_ms=NOW_MS, anchor_date=20260909,
    )
    curator = NoteCuratorAdaptor(
        notes=notes, record=FakeRecordStore(), today=lambda: 20261001,
        max_expired_per_tick=10, metrics=Metrics(), clock=lambda: NOW_MS,
        culture=culture, room_for=room_for,
    )
    return await curator.inputs(key=WS, batch=())


async def test_the_curator_reads_the_account_of_the_room_each_note_was_raised_in():
    """`established_in` is a THREAD id; the account is keyed on the room's
    `primary_id`. The note and the account both carry the room's label, which
    is how the model joins the two — never the id (R-18)."""
    culture = await _seeded(**{"room-primary": ACCOUNT})
    out = await _curator_prompt(
        culture,
        lambda cid, unit: {("thread-7", WS): ("room-primary", "Friends")}.get(
            (cid, unit)))
    assert ACCOUNT in out
    assert '<culture room="Friends"' in out
    assert 'room="Friends"' in out[out.index("<note"):]
    assert out.index("<culture") < out.index("<note")
    assert culture.reads == ["room-primary"]
    assert "room-primary" not in out.replace(ACCOUNT, "")


async def test_the_curator_without_culture_is_unchanged():
    off = await _curator_prompt(None, None)
    assert "<culture" not in off
    assert "room=" not in off


async def test_the_curator_with_a_reader_but_no_account_is_byte_identical():
    """The tier ON and the room never folded: no `<culture>`, and no `room=`
    on the note either — a label that joins to no account is noise, and the
    ordinary state for a day after enabling must not change the prompt."""
    off = await _curator_prompt(None, None)
    on_empty = await _curator_prompt(
        await _seeded(),
        lambda cid, unit: ("room-primary", "Friends"))
    assert on_empty == off


async def test_the_curator_does_not_match_a_thread_id_from_another_unit():
    """Thread ids are unique per platform, not across platforms, and a note
    has no platform: the unit is what disambiguates."""
    culture = await _seeded(**{"room-primary": ACCOUNT})
    out = await _curator_prompt(
        culture,
        lambda cid, unit: {("thread-7", "another-unit"): ("room-primary", "X")}
        .get((cid, unit)))
    assert "<culture" not in out and culture.reads == []


# --- proactive ----------------------------------------------------------------


ROOMS = (
    UnitRoom(platform="signal", conversation_id="sig-1", label="Friends",
             home=True, proactive=True),
    UnitRoom(platform="discord", conversation_id="1/2", label=""),
)


def _proactive(culture) -> ProactiveAdaptor:
    return ProactiveAdaptor(
        notes=InMemoryNoteStore(), standing=None, reminders=FakeReminderStore(),
        memory=None, rooms_of=lambda ws: ROOMS if ws == WS else (),
        ai_peer="nora", clock=lambda: datetime(2026, 9, 25, tzinfo=UTC),
        timezone=ZoneInfo("Asia/Hong_Kong"), metrics=Metrics(), culture=culture,
    )


async def test_proactive_carries_one_named_account_per_folded_room():
    """The pass chooses WHERE to raise something, so it reads every room's
    account, each named — and a room never folded renders nothing."""
    culture = await _seeded(**{"sig-1": ACCOUNT})
    blocks = await _proactive(culture)._cultures(ROOMS)
    assert len(blocks) == 1
    assert '<culture room="Friends"' in blocks[0] and ACCOUNT in blocks[0]
    assert culture.reads == ["sig-1", "1/2"]


async def test_an_unlabelled_room_is_named_by_the_stand_in_never_its_id():
    culture = await _seeded(**{"1/2": ACCOUNT})
    (block,) = await _proactive(culture)._cultures(ROOMS)
    assert "1/2" not in block
    assert 'room="' in block


async def test_proactive_without_culture_renders_no_account():
    assert await _proactive(None)._cultures(ROOMS) == []


# --- the standing folds -------------------------------------------------------


def _account() -> CultureRevision:
    return CultureRevision(
        id="r1", culture_key="g1", body=ACCOUNT, written_at=NOW_MS - 3_600_000,
        established_by="dream:culture:test")


def test_the_rollup_fold_reads_the_account_first():
    rows = [_row(1, "anyone?")]
    with_it = render_rollup_input(
        prior=None, rows=rows, display_name="Nora", now_ms=NOW_MS,
        culture=_account())
    without = render_rollup_input(
        prior=None, rows=rows, display_name="Nora", now_ms=NOW_MS)
    assert with_it.startswith("<culture")
    assert with_it.endswith(without), "the rest of the prompt is untouched"
    assert "<culture" not in without
