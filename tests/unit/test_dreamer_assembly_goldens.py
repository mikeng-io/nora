"""Step 2a of `design/context-assembly/04-every-occasion.md` §5: the four Dreamer
adaptors that declared `_BLOCKS` and joined their blocks by hand (proactive,
culture, note-candidates, the note curator) now WALK `assemble`, so each
table's `order` is the order the model reads, and NOT ONE BYTE any model reads
changes.

**The goldens in `tests/fixtures/context_assembly_step2a_goldens.json` were
captured from `master` at `7fd3cbb6`, BEFORE any adaptor was touched**, over
synthetic fixtures, and are the criterion. What is compared is each adaptor's
whole `inputs()` text, not a substring of it, over the cases a hand join and
`assemble` could disagree on:

* a block present and the same block absent (every optional block of every
  adaptor), and the whole prompt blank where the adaptor returns `""`;
* no operator room description (culture's and note-candidates' self-closing
  `<room label="..."/>`, which `assemble` would DROP as an empty body), and a
  description made only of characters XML cannot carry, which escapes to an
  empty body without being falsy;
* no decision log wired (proactive's `<loop_decisions>` is `""` there and the
  hand join kept the separator it left behind: the eval harness builds exactly
  this prompt), with and without a self card after it;
* blocks built by a shared builder (`render_notes`, `render_standing`,
  `render_culture`, `render_presence`, `render_decisions`, `render_self_card`,
  `today_block`) and restamped with the occasion's own row;
* long content: a notebook past its character budget, a batch past its row cap,
  a position past its clip, an evidence window cut short.

Nothing here regenerates a golden. Changing one is a model-visible change and
belongs in a step that says so.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.reminders import Reminder, ReminderState
from nora.domain.self_presence import Presence, PresenceRoom
from nora.domain.standing import Standing
from nora.language import note_candidates
from nora.language.note_candidates import NoteCandidateAdaptor
from nora.language.note_curator import NoteCuratorAdaptor
from nora.language.note_render import FORM_NOT_SHOWN
from nora.obs.metrics import Metrics
from nora.testing import (
    InMemoryCultureStore,
    InMemoryDecisionLogStore,
    InMemoryNoteStore,
    InMemoryStandingStore,
)
from nora.testing.fakes import FakeMemoryStore, FakeRecordStore
from nora.testing.reminders import FakeReminderStore
from tests.unit import test_culture_adaptor as cul
from tests.unit import test_note_candidates as cand
from tests.unit import test_note_curator as cur
from tests.unit import test_proactive_adaptor as pro

GOLDEN: dict[str, str] = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "context_assembly_step2a_goldens.json")
    .read_text(encoding="utf-8"),
)

CASES: dict[str, Callable[[], Awaitable[str]]] = {}


def _case(name: str):
    def register(fn: Callable[[], Awaitable[str]]):
        assert name not in CASES, name
        CASES[name] = fn
        return fn
    return register


# =============================================================================
# proactive
# =============================================================================

_PRO_ROOMS_DESCRIBED = (
    pro.UnitRoom(
        platform="signal", conversation_id="sig-1", label="Friends", home=True,
        proactive=True,
        description="the friends group; 2200 is when people log on <not> an appointment",
    ),
    pro.UnitRoom(platform="discord", conversation_id="1/2", label="Zone 42"),
)
_PRO_ROOMS_UNLABELLED = (
    pro.UnitRoom(platform="signal", conversation_id="sig-1", label="", home=True),
    pro.UnitRoom(platform="discord", conversation_id="1/2", label=""),
)


async def _pro_notes(*, count: int = 3, long: bool = False) -> InMemoryNoteStore:
    store = InMemoryNoteStore()
    first = await pro._note(store)
    if count >= 2:
        await pro._note(
            store, title="Trip to <Taipei> & back", anchor=None, established_in="1/2",
            anchor_entity_ids=("grant",),
        )
    if count >= 3:
        third = await pro._note(
            store, title="Hackathon demo", anchor=20260915,
            now_ms=pro.NOW_MS - 3 * 86_400_000,
        )
        store.notes[third.id] = replace(
            third, revisit_at=pro.NOW_MS - 1_000, strikes=2,
        )
    for i in range(count, 22 if long else count):
        await pro._note(
            store, title=f"Open arrangement number {i} of the long list",
            anchor=20260920 + i % 9, established_in="sig-1" if i % 2 else "1/2",
            now_ms=pro.NOW_MS - i * 3_600_000,
        )
    assert first is not None
    return store


async def _pro_standing(*, long: bool = False) -> InMemoryStandingStore:
    standing = InMemoryStandingStore()
    await standing.write(standing=Standing(
        platform="signal", conversation_id="sig-1",
        body=("Dinner Saturday is agreed; where is still open.\nSecond line <b>& co</b>. "
              + ("long position text " * 80 if long else "")),
        fold_count=3, updated_at=pro.NOW_MS - 2 * 3_600_000,
    ))
    await standing.write(standing=Standing(
        platform="discord", conversation_id="1/2", body="games night is weekly",
        fold_count=1, updated_at=pro.NOW_MS - 30 * 3_600_000,
    ))
    return standing


async def _pro_reminders() -> FakeReminderStore:
    log = FakeReminderStore()
    await log.create(Reminder(
        id=uuid.uuid4(), requester_person_id="nora", platform="signal",
        conversation_id="sig-1", conversation_type="group",
        text="nudge about the venue", created_at=pro.NOW - timedelta(days=1),
        due_at=pro.NOW - timedelta(hours=17), state=ReminderState.DONE,
        fired_at=pro.NOW - timedelta(hours=17), outcome="silent", kind="proactive",
        payload={"note_title": "AI Hackathon dinner plan", "intent": "nudge"},
    ))
    await log.create(Reminder(
        id=uuid.uuid4(), requester_person_id="nora", platform="signal",
        conversation_id="wedding-room", conversation_type="group",
        text="wedding seating", created_at=pro.NOW - timedelta(days=1),
        due_at=pro.NOW, state=ReminderState.PENDING, fired_at=None, outcome=None,
        kind="proactive",
    ))
    return log


async def _pro_decisions(*, rows: bool = True):
    store = InMemoryDecisionLogStore()
    if rows:
        await store.append(row=pro._other_decision(
            writer="participation", verdict="spoke", reason="spoke",
            conversation_id="sig-1", ago_ms=3_600_000, row_id="a",
        ))
        await store.append(row=pro._other_decision(
            writer="tag", verdict="replied", reason="send_reply",
            conversation_id="1/2", platform="discord", ago_ms=7_200_000, row_id="b",
        ))
    return pro._recorder(store)


async def _pro_culture() -> InMemoryCultureStore:
    culture = InMemoryCultureStore()
    await culture.append(
        key="sig-1", body="they decide by attrition; <nobody> says cancelled",
        written_at=pro.NOW_MS - 86_400_000, established_by="dream:culture:test",
    )
    await culture.append(
        key="1/2", body="a games room; short bursts at night",
        written_at=pro.NOW_MS - 5 * 86_400_000, established_by="dream:culture:test",
    )
    return culture


async def _pro_presence(_home):
    return Presence(
        platform="signal", label="Friends", here_state="up",
        unit_rooms=(PresenceRoom("discord", "Zone 42", "reconnecting"),),
        other_groups=True,
    )


_PRO_MEMORY = {
    "miles_novak": ["miles_novak jokingly calls nora 管理員", "second line"],
    "grant": ["grant works Saturdays"],
    "nora": ["nora said she would find a place", "nora owes a route <tip>"],
}


@_case("proactive-full")
async def _proactive_full() -> str:
    return await pro._adaptor(
        notes=await _pro_notes(), standing=await _pro_standing(),
        reminders=await _pro_reminders(),
        memory=FakeMemoryStore(conclusions=_PRO_MEMORY), rooms=_PRO_ROOMS_DESCRIBED,
        people=pro.ROSTER, roster_index_conclusions=1, presence_of=_pro_presence,
        culture=await _pro_culture(), decisions=await _pro_decisions(),
    ).inputs(key=pro.WS, batch=())


@_case("proactive-minimal-no-decision-log")
async def _proactive_minimal() -> str:
    """The eval harness's shape: notes only, no log, no card. The hand join
    leaves a trailing separator after `<decisions>`."""
    return await pro._adaptor(notes=await _pro_notes(count=1)).inputs(
        key=pro.WS, batch=(),
    )


@_case("proactive-no-decision-log-with-self-card")
async def _proactive_unwired_with_card() -> str:
    """The log absent in the MIDDLE: the hand join leaves an empty slot, and
    its two separators, between `<decisions>` and `<self_card>`."""
    return await pro._adaptor(
        notes=await _pro_notes(count=2), memory=FakeMemoryStore(conclusions=_PRO_MEMORY),
    ).inputs(key=pro.WS, batch=())


@_case("proactive-decision-log-wired-empty")
async def _proactive_wired_empty() -> str:
    return await pro._adaptor(
        notes=await _pro_notes(count=2), decisions=await _pro_decisions(rows=False),
    ).inputs(key=pro.WS, batch=())


@_case("proactive-decision-log-wired-with-self-card")
async def _proactive_wired_card() -> str:
    return await pro._adaptor(
        notes=await _pro_notes(count=2), decisions=await _pro_decisions(),
        memory=FakeMemoryStore(conclusions=_PRO_MEMORY),
    ).inputs(key=pro.WS, batch=())


@_case("proactive-standing-only-no-open-notes")
async def _proactive_standing_only() -> str:
    return await pro._adaptor(
        notes=InMemoryNoteStore(), standing=await _pro_standing(),
        decisions=await _pro_decisions(),
    ).inputs(key=pro.WS, batch=())


@_case("proactive-unlabelled-rooms-no-description")
async def _proactive_unlabelled() -> str:
    return await pro._adaptor(
        notes=await _pro_notes(count=2), standing=await _pro_standing(),
        rooms=_PRO_ROOMS_UNLABELLED, decisions=await _pro_decisions(),
    ).inputs(key=pro.WS, batch=())


@_case("proactive-people-without-memory-depth")
async def _proactive_people_names_only() -> str:
    return await pro._adaptor(
        notes=await _pro_notes(count=2), people=pro.ROSTER,
        memory=FakeMemoryStore(conclusions=_PRO_MEMORY), roster_index_conclusions=0,
        decisions=await _pro_decisions(rows=False),
    ).inputs(key=pro.WS, batch=())


@_case("proactive-culture-for-one-room-only")
async def _proactive_one_culture() -> str:
    culture = InMemoryCultureStore()
    await culture.append(
        key="1/2", body="a games room; short bursts at night",
        written_at=pro.NOW_MS - 86_400_000, established_by="dream:culture:test",
    )
    return await pro._adaptor(
        notes=await _pro_notes(count=2), culture=culture,
        standing=await _pro_standing(), decisions=await _pro_decisions(),
    ).inputs(key=pro.WS, batch=())


@_case("proactive-long")
async def _proactive_long() -> str:
    return await pro._adaptor(
        notes=await _pro_notes(count=3, long=True),
        standing=await _pro_standing(long=True), reminders=await _pro_reminders(),
        memory=FakeMemoryStore(conclusions=_PRO_MEMORY), rooms=_PRO_ROOMS_DESCRIBED,
        people=pro.ROSTER, roster_index_conclusions=1, presence_of=_pro_presence,
        culture=await _pro_culture(), decisions=await _pro_decisions(),
    ).inputs(key=pro.WS, batch=())


@_case("proactive-no-home-room-is-blank")
async def _proactive_no_home() -> str:
    return await pro._adaptor(
        notes=await _pro_notes(count=1), rooms=(pro.ROOMS[1],),
    ).inputs(key=pro.WS, batch=())


@_case("proactive-nothing-to-show-is-blank")
async def _proactive_nothing() -> str:
    return await pro._adaptor(
        notes=InMemoryNoteStore(), standing=InMemoryStandingStore(),
    ).inputs(key=pro.WS, batch=())


# =============================================================================
# culture
# =============================================================================

_CUL_BATCH = (
    cul._row("dinner Saturday? <b>&</b> 開車喇", ts=cul.T0, name="Ada", rid=11),
    cul._row("sure\nsecond line", ts=cul.T0 + 60_000, name="Bo]: x", rid=12),
    cul._row("2200", ts=cul.T0 + 120_000, name="Ada", rid=13),
)


async def _cul_standing_store(*, present: bool = True) -> cul._StandingStore:
    return cul._StandingStore(
        {("discord", "g1"): cul._standing()} if present else {},
    )


@_case("culture-full")
async def _culture_full() -> str:
    return await cul._adaptor(
        cul._Store(cul._revision("they decide by attrition & <never> cancel")),
        standing=await _cul_standing_store(),
        notes=cul._Notes((cul._note(), cul._note("trip & <away>"))),
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-no-operator-description")
async def _culture_no_description() -> str:
    return await cul._adaptor(
        cul._Store(cul._revision("account")), description=None,
        standing=await _cul_standing_store(), notes=cul._Notes((cul._note(),)),
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-empty-string-description")
async def _culture_empty_description() -> str:
    return await cul._adaptor(cul._Store(), description="").inputs(
        key=cul.KEY, batch=_CUL_BATCH,
    )


@_case("culture-description-of-forbidden-characters-only")
async def _culture_control_description() -> str:
    """Truthy, and escapes to an EMPTY body: not the same input as `None`."""
    return await cul._adaptor(cul._Store(), description="\x0b\x00").inputs(
        key=cul.KEY, batch=_CUL_BATCH,
    )


@_case("culture-no-label")
async def _culture_no_label() -> str:
    return await cul._adaptor(
        cul._Store(cul._revision("account")), label=None, description=None,
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-first-fold-nothing-else-wired")
async def _culture_bare() -> str:
    return await cul._adaptor(cul._Store(), notes=None, standing=None).inputs(
        key=cul.KEY, batch=_CUL_BATCH[:1],
    )


@_case("culture-previous-account-only")
async def _culture_previous_only() -> str:
    return await cul._adaptor(
        cul._Store(cul._revision("account")), notes=None, standing=None,
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-standing-without-notes")
async def _culture_standing_only() -> str:
    return await cul._adaptor(
        cul._Store(), standing=await _cul_standing_store(), notes=None,
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-notes-without-standing")
async def _culture_notes_only() -> str:
    return await cul._adaptor(
        cul._Store(), standing=None, notes=cul._Notes((cul._note(),)),
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-wired-but-empty-standing-and-notes")
async def _culture_wired_empty() -> str:
    return await cul._adaptor(
        cul._Store(), standing=await _cul_standing_store(present=False),
        notes=cul._Notes(()),
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-key-names-no-policy-conversation")
async def _culture_unresolved() -> str:
    return await cul._adaptor(
        cul._Store(cul._revision("account")), unit=None, standing_key=None,
        standing=await _cul_standing_store(), notes=cul._Notes((cul._note(),)),
    ).inputs(key=cul.KEY, batch=_CUL_BATCH)


@_case("culture-empty-batch-is-blank")
async def _culture_empty_batch() -> str:
    return await cul._adaptor(cul._Store(cul._revision("account"))).inputs(
        key=cul.KEY, batch=(),
    )


@_case("culture-long")
async def _culture_long() -> str:
    batch = tuple(
        cul._row(f"message {i} " + "word " * 40, ts=cul.T0 + i * 60_000,
                 name="Ada" if i % 2 else "Bo", rid=100 + i)
        for i in range(30)
    )
    return await cul._adaptor(
        cul._Store(cul._revision("a long account " * 60)),
        standing=await _cul_standing_store(),
        notes=cul._Notes(tuple(cul._note(f"arrangement {i}") for i in range(12))),
    ).inputs(key=cul.KEY, batch=batch)


# =============================================================================
# note-candidates
# =============================================================================

_CAND_UTC = ZoneInfo("Asia/Hong_Kong")


def _cand_rows(count: int = 3) -> tuple[TranscriptRow, ...]:
    return tuple(
        TranscriptRow(
            id=200 + i, sender_id=f"p{i}", sender_name="Ada" if i % 2 else "Bo",
            ts=cand.NOW + i * 60_000,
            body=f"Sai Kung <b>& co</b> {i}\nsecond line" if i % 3 else None,
            is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
            media_json='[{"k":1}]' if i == 1 else None,
        )
        for i in range(count)
    )


def _cand_adaptor(
    store: InMemoryNoteStore, *, label="Friends", description=None, unit=cand.WS,
    culture=None,
) -> NoteCandidateAdaptor:
    return NoteCandidateAdaptor(
        notes=store, candidates=store, unit_for=lambda _: unit,
        label_for=lambda _: label, description_for=lambda _: description,
        metrics=Metrics(), culture=culture, timezone=_CAND_UTC,
    )


async def _cand_notebook(*, count: int = 2) -> InMemoryNoteStore:
    store = InMemoryNoteStore()
    await cand._open_note(
        store, "Wedding <venue>", "## Decided\n## Open\n- which venue [200]",
        established_in="conv-0",
    )
    if count >= 2:
        await cand._open_note(
            store, "Trip", "legacy body with no sections & <markup>",
            established_in="conv-1", now_ms=cand.NOW - 86_400_000,
        )
    for i in range(2, count):
        await cand._open_note(
            store, f"Arrangement {i}", "## Decided\n## Open\n" + "- line [200]\n" * 30,
            established_in="conv-0", now_ms=cand.NOW - i * 3_600_000,
        )
    return store


async def _cand_culture() -> InMemoryCultureStore:
    culture = InMemoryCultureStore()
    await culture.append(
        key=cand.SCOPE, body="a games room; <short> bursts at night",
        written_at=cand.NOW - 3 * 86_400_000, established_by="dream:culture:test",
    )
    return culture


@_case("candidates-full")
async def _candidates_full() -> str:
    return await _cand_adaptor(
        await _cand_notebook(), description="a games room; 2200 is <log on>",
        culture=await _cand_culture(),
    ).inputs(key=cand.SCOPE, batch=_cand_rows())


@_case("candidates-no-operator-description")
async def _candidates_no_description() -> str:
    return await _cand_adaptor(
        await _cand_notebook(), culture=await _cand_culture(),
    ).inputs(key=cand.SCOPE, batch=_cand_rows())


@_case("candidates-description-of-forbidden-characters-only")
async def _candidates_control_description() -> str:
    return await _cand_adaptor(
        await _cand_notebook(), description="\x0b\x00",
    ).inputs(key=cand.SCOPE, batch=_cand_rows())


@_case("candidates-no-label-falls-back-to-unit")
async def _candidates_unit_label() -> str:
    return await _cand_adaptor(
        await _cand_notebook(), label=None, description="a room",
    ).inputs(key=cand.SCOPE, batch=_cand_rows(2))


@_case("candidates-no-label-and-no-unit")
async def _candidates_unnamed() -> str:
    return await _cand_adaptor(
        InMemoryNoteStore(), label=None, unit=None,
    ).inputs(key=cand.SCOPE, batch=_cand_rows(1))


@_case("candidates-batch-only-no-notes-no-culture")
async def _candidates_batch_only() -> str:
    return await _cand_adaptor(InMemoryNoteStore()).inputs(
        key=cand.SCOPE, batch=_cand_rows(),
    )


@_case("candidates-culture-without-notes")
async def _candidates_culture_only() -> str:
    return await _cand_adaptor(
        InMemoryNoteStore(), culture=await _cand_culture(),
        description="a room",
    ).inputs(key=cand.SCOPE, batch=_cand_rows())


@_case("candidates-culture-wired-room-never-folded")
async def _candidates_culture_empty() -> str:
    return await _cand_adaptor(
        await _cand_notebook(count=1), culture=InMemoryCultureStore(),
    ).inputs(key=cand.SCOPE, batch=_cand_rows())


@_case("candidates-empty-batch-is-blank")
async def _candidates_empty_batch() -> str:
    return await _cand_adaptor(await _cand_notebook()).inputs(
        key=cand.SCOPE, batch=(),
    )


@_case("candidates-batch-past-its-row-cap-and-notebook-past-its-budget")
async def _candidates_long() -> str:
    with (
        patch.object(note_candidates, "MAX_RENDERED_MESSAGES", 4),
        patch.object(note_candidates, "NOTEBOOK_BODY_BUDGET_CHARS", 1_500),
    ):
        return await _cand_adaptor(
            await _cand_notebook(count=6), description="a games room",
            culture=await _cand_culture(),
        ).inputs(key=cand.SCOPE, batch=_cand_rows(9))


# =============================================================================
# note curator
# =============================================================================

_CUR_NOW = cur.NOW + 5_000_000


def _cur_row_dict(row_id: int, body: str, *, ts: int, is_nora: bool = False,
                  conv: str = "conv-1") -> dict:
    return {**cur._row(row_id, body, ts=ts, is_nora=is_nora), "conversation_id": conv}


async def _cur_notes() -> InMemoryNoteStore:
    store = InMemoryNoteStore()
    await cur._seed(store, title="Dinner, 9 Sept", anchor=20260909, body="Nobody <booked>.")
    await cur._seed(store, title="Route to Sai Kung", anchor=None,
                    body="969P from Tin Shui Chi", revisit_at=cur.NOW - 1)
    await cur._seed(store, title="Drinks & Friday", anchor=20260925, body="maybe")
    return store


def _cur_record(*, own: bool = True) -> FakeRecordStore:
    record = FakeRecordStore()
    record.rows.append(_cur_row_dict(1, "Ada booked Sai Kung <b>&</b>", ts=cur.NOW + 1_000))
    record.rows.append(_cur_row_dict(2, "thanks\nsecond line", ts=cur.NOW + 2_000))
    if own:
        record.rows.append(_cur_row_dict(
            3, "969P from <TST>", ts=cur.NOW + 3_000, is_nora=True))
    return record


async def _cur_culture() -> InMemoryCultureStore:
    culture = InMemoryCultureStore()
    await culture.append(
        key="room-primary", body="they decide by attrition; <never> cancelled",
        written_at=cur.NOW - 86_400_000, established_by="dream:culture:test",
    )
    await culture.append(
        key="room-two", body="a games room",
        written_at=cur.NOW - 2 * 86_400_000, established_by="dream:culture:test",
    )
    return culture


def _cur_room_for(established_in: str, unit: str):
    return {
        ("conv-1", cur.WS): ("room-primary", "Friends"),
        ("conv-2", cur.WS): ("room-two", ""),
        ("conv-3", cur.WS): ("room-never-folded", "Quiet room"),
    }.get((established_in, unit))


def _cur_adaptor(store, *, record=None, today=cur.TODAY, culture=None, room_for=None,
                 limit: int = 40) -> NoteCuratorAdaptor:
    return NoteCuratorAdaptor(
        notes=store, record=record if record is not None else FakeRecordStore(),
        today=lambda: today, max_expired_per_tick=10, max_revisits_per_tick=10,
        evidence_messages_per_note=limit, metrics=Metrics(), clock=lambda: _CUR_NOW,
        culture=culture, room_for=room_for,
    )


@_case("curator-evidence-present-own-sends-and-self-only")
async def _curator_full() -> str:
    return await _cur_adaptor(await _cur_notes(), record=_cur_record()).inputs(
        key=cur.WS, batch=(),
    )


@_case("curator-no-evidence-at-all")
async def _curator_no_evidence() -> str:
    return await _cur_adaptor(await _cur_notes()).inputs(key=cur.WS, batch=())


@_case("curator-evidence-without-own-sends")
async def _curator_no_own() -> str:
    return await _cur_adaptor(
        await _cur_notes(), record=_cur_record(own=False),
    ).inputs(key=cur.WS, batch=())


@_case("curator-with-the-rooms-accounts")
async def _curator_cultures() -> str:
    store = InMemoryNoteStore()
    await cur._seed(store, title="Dinner, 9 Sept", anchor=20260909, body="Nobody booked.")
    for i, conv in enumerate(("conv-2", "conv-3", "conv-1", "elsewhere"), start=1):
        note = await store.create(
            unit=cur.WS, title=f"Arrangement {i}", description="one line",
            uncertainty="undecided", closing_condition="somebody decides",
            established_in=conv, body=f"body {i}", established_by="tag:miles",
            now_ms=cur.NOW, anchor_date=20260909 + i,
        )
        assert note is not None
    return await _cur_adaptor(
        store, record=_cur_record(), culture=await _cur_culture(),
        room_for=_cur_room_for,
    ).inputs(key=cur.WS, batch=())


@_case("curator-culture-wired-but-no-room-folded")
async def _curator_culture_empty() -> str:
    return await _cur_adaptor(
        await _cur_notes(), culture=InMemoryCultureStore(), room_for=_cur_room_for,
    ).inputs(key=cur.WS, batch=())


@_case("curator-evidence-window-cut-short")
async def _curator_cut_short() -> str:
    record = FakeRecordStore()
    for i in range(6):
        record.rows.append(_cur_row_dict(10 + i, f"chatter {i}", ts=cur.NOW + 1_000 * (i + 1)))
    return await _cur_adaptor(await _cur_notes(), record=record, limit=3).inputs(
        key=cur.WS, batch=(),
    )


@_case("curator-today-is-not-a-date")
async def _curator_odd_today() -> str:
    return await _cur_adaptor(
        await _cur_notes(), record=_cur_record(), today=20261345,
    ).inputs(key=cur.WS, batch=())


@_case("curator-tidy-notebook-is-blank")
async def _curator_tidy() -> str:
    store = InMemoryNoteStore()
    await cur._seed(store, title="Wedding", anchor=20271231)
    return await _cur_adaptor(store).inputs(key=cur.WS, batch=())


@_case("curator-long")
async def _curator_long() -> str:
    store = InMemoryNoteStore()
    for i in range(5):
        await cur._seed(
            store, title=f"Arrangement {i} of the long list", anchor=20260901 + i,
            body="line of the body\n" * 15,
        )
    record = FakeRecordStore()
    for i in range(6):
        record.rows.append(_cur_row_dict(
            50 + i, f"evidence {i} " + "word " * 30, ts=cur.NOW + 1_000 * (i + 1)))
    return await _cur_adaptor(store, record=record).inputs(key=cur.WS, batch=())


# =============================================================================
# the criterion
# =============================================================================


@pytest.mark.parametrize("name", sorted(CASES))
async def test_the_prompt_is_byte_identical_to_master(name: str):
    assert await CASES[name]() == GOLDEN[name]


def test_every_case_has_a_golden_and_no_golden_is_orphaned():
    assert set(CASES) == set(GOLDEN)


#: What each golden holds and does not, so «every optional block present and
#: absent» is a checked property of the fixtures and not a claim about them.
_PRESENT = {
    "proactive-full": (
        "now", "presence", "people", "notes", "rooms", "culture", "decisions",
        "loop_decisions", "self_card",
    ),
    "proactive-minimal-no-decision-log": ("now", "presence", "notes", "rooms", "decisions"),
    "proactive-no-decision-log-with-self-card": ("decisions", "self_card"),
    "proactive-decision-log-wired-empty": ("decisions", "loop_decisions"),
    "culture-full": ("room", "previous_account", "standing", "notes", "room_messages"),
    "culture-first-fold-nothing-else-wired": ("room", "room_messages"),
    "candidates-full": ("room", "culture", "batch", "today", "candidate_open_note"),
    "candidates-batch-only-no-notes-no-culture": ("room", "batch"),
    "curator-with-the-rooms-accounts": ("today", "culture", "note", "evidence", "own_sends"),
    "curator-no-evidence-at-all": ("today", "note"),
}
_ABSENT = {
    "proactive-minimal-no-decision-log": ("people", "culture", "loop_decisions", "self_card"),
    "proactive-no-decision-log-with-self-card": ("loop_decisions",),
    "culture-first-fold-nothing-else-wired": ("previous_account", "standing", "notes"),
    "culture-previous-account-only": ("standing", "notes"),
    "culture-standing-without-notes": ("previous_account", "notes"),
    "culture-notes-without-standing": ("previous_account", "standing"),
    "candidates-batch-only-no-notes-no-culture": ("culture", "today", "candidate_open_note"),
    "candidates-culture-without-notes": ("today", "candidate_open_note"),
    "curator-no-evidence-at-all": ("culture", "own_sends"),
}


def test_the_goldens_vary_the_blocks_this_step_is_about():
    for name, tags in _PRESENT.items():
        for tag in tags:
            assert f"<{tag} " in GOLDEN[name], (name, tag)
    for name, tags in _ABSENT.items():
        for tag in tags:
            assert f"<{tag} " not in GOLDEN[name], (name, tag)
    # The three empty-body shapes `assemble` would change, each in its golden.
    assert '<room label="Zone 42" description=' in GOLDEN["culture-no-operator-description"]
    assert "/>" in GOLDEN["culture-no-operator-description"].split("<room ", 1)[1].split("\n", 1)[0]
    assert GOLDEN["proactive-minimal-no-decision-log"].endswith("</decisions>\n\n")
    assert "</decisions>\n\n\n\n<self_card" in GOLDEN["proactive-no-decision-log-with-self-card"]
    for blank in (
        "proactive-no-home-room-is-blank", "proactive-nothing-to-show-is-blank",
        "culture-empty-batch-is-blank", "candidates-empty-batch-is-blank",
        "curator-tidy-notebook-is-blank",
    ):
        assert GOLDEN[blank] == "", blank


def test_the_cases_cover_the_cut_short_and_budget_edges():
    long = GOLDEN["candidates-batch-past-its-row-cap-and-notebook-past-its-budget"]
    assert "window: cut short" in GOLDEN["curator-evidence-window-cut-short"]
    assert "evidence: NONE" in GOLDEN["curator-no-evidence-at-all"]
    assert "more messages" in long
    assert FORM_NOT_SHOWN in long


# =============================================================================
# the one place the text is NOT byte-identical, pinned so nobody calls it so
# =============================================================================


async def test_a_body_that_escapes_to_nothing_is_dropped_like_the_tag_path():
    """A block whose body is non-empty but escapes to NOTHING (control
    characters only; `\\x01\\x08` is storable, `\\x00` is not in Postgres) used
    to render as an empty `<previous_account>` pair, because a hand join keeps
    every string it is handed. `assemble` drops an empty body, as the tag turn
    already does, so the empty pair is gone. That is a change to what the model
    reads, for input no person produces; step 2a accepts it rather than
    carrying a `when_empty` flag for every block that can come out empty
    (found by the independent review of #683). The goldens above cover none of
    these inputs, so this pins the decision."""
    store = InMemoryCultureStore()
    await store.append(
        key=cul.KEY, body="\x01\x08", written_at=cul.T0, established_by="t",
    )
    text = await cul._adaptor(store).inputs(key=cul.KEY, batch=(cul._row("hi"),))
    assert "<previous_account" not in text
    assert "<room " in text  # the rest of the prompt is still there
