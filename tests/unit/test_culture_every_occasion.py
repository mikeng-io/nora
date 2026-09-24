"""`<culture>` reaches every runtime's prompt (design/culture-tier, DEC-204).

One test per occasion that is not a turn path (those are in
`test_culture_render.py`): the note-candidates pass, the note curator, the
proactive decide pass, and both standing folds. Each asserts the RENDERED
prompt, because the failure DEC-204 guards against is a row declared in a
`_BLOCKS` table that never reaches the model — and each asserts the key the
store was READ with, because DEC-181's failure is a read that matches nothing
and renders as «no account yet», a real state nothing reports.

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
        culture, {"thread-7": ("room-primary", "Friends")}.get)
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
