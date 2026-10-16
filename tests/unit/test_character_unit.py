"""`CharacterUnitAdaptor` and `<unit_culture>` — what a unit is across its rooms."""

from __future__ import annotations

from datetime import UTC, datetime

from nora.domain.culture import CultureProposal, unit_culture_key
from nora.domain.ports import Proposal
from nora.language.character import (
    CharacterRoom,
    CharacterUnitAdaptor,
    UnitEvidence,
    UnitKey,
)
from nora.language.character_session import AccountNotRecordableError
from nora.language.culture_render import render_unit_culture
from nora.testing import InMemoryCharacterEvidence, InMemoryCultureStore

T0 = 1_790_000_000_000
NOW = datetime.fromtimestamp((T0 + 86_400_000) / 1000, tz=UTC)
ROOMS = (
    CharacterRoom(key="room-a", platform="signal", label="Friends"),
    CharacterRoom(key="room-b", platform="discord", label="Zone 42"),
)
KEY = unit_culture_key("friends")
UK = UnitKey(unit="friends", rooms=ROOMS)


def _adaptor(store: InMemoryCultureStore) -> CharacterUnitAdaptor:
    return CharacterUnitAdaptor(
        culture=store, evidence=UnitEvidence(store=InMemoryCharacterEvidence(), keys={KEY: UK}),
        keys={KEY: UK}, clock=lambda: NOW,
    )


async def _rooms(store: InMemoryCultureStore) -> None:
    await store.append(key="room-a", body="they plan by attrition", written_at=T0,
                       established_by="x", citations=("41", "42"))
    await store.append(key="room-b", body="a late games room", written_at=T0,
                       established_by="x", citations=("57",))


async def test_the_unit_session_is_handed_each_rooms_culture_with_what_it_cites():
    store = InMemoryCultureStore()
    await _rooms(store)
    out = await _adaptor(store).inputs(key=KEY, batch=("room-a",))
    assert 'rooms="Friends, Zone 42"' in out
    assert 'room="Friends"' in out and 'cites="41, 42"' in out
    assert "a late games room" in out and 'cites="57"' in out


async def test_a_room_culture_with_no_citations_is_not_handed_to_the_unit():
    """A room account written by the envelope fold held the presence reads; the
    unit's account renders in every room, so it is skipped (the cited room
    still is)."""
    store = InMemoryCultureStore()
    await store.append(key="room-a", body="they plan by attrition", written_at=T0,
                       established_by="x", citations=("41",))
    await store.append(key="room-b", body="Bo is usually at the gym", written_at=T0,
                       established_by="old")
    out = await _adaptor(store).inputs(key=KEY, batch=("room-a",))
    assert "they plan by attrition" in out
    assert "Bo is usually at the gym" not in out


async def test_it_cites_only_what_its_rooms_cultures_cite_and_writes_under_the_unit_key():
    store = InMemoryCultureStore()
    await _rooms(store)
    adaptor = _adaptor(store)
    await adaptor.inputs(key=KEY, batch=("room-a",))
    revise, _keep = adaptor.verbs(key=KEY)
    refused = await revise(entity="unit", body="x", citations=["99"])
    assert refused["reason"] == "unresolved_citation"
    assert await revise(entity="unit", body="one circle", citations=["41", "57"]) == {
        "status": "ok"}
    proposals = await adaptor.accept(key=KEY, items=())
    await adaptor.commit(key=KEY, now_ms=T0, proposals=[Proposal(
        payload=p, instruction_sha256="a" * 64, model="m", established_by="r",
    ) for p in proposals])
    head = await store.head(key=KEY)
    assert head is not None and head.body == "one circle" and head.citations == ("41", "57")


async def test_no_citable_room_culture_or_no_new_revision_calls_no_model():
    """A room culture written without the verb cites nothing, so a unit account
    could rest on nothing: no model call and no new revision."""
    store = InMemoryCultureStore()
    await store.append(key="room-a", body="old, uncited", written_at=T0, established_by="x")
    assert await _adaptor(store).inputs(key=KEY, batch=("room-a",)) == ""
    await _rooms(store)
    assert await _adaptor(store).inputs(key=KEY, batch=()) == ""


async def test_an_unsettled_unit_session_holds_its_batch():
    store = InMemoryCultureStore()
    await _rooms(store)
    adaptor = _adaptor(store)
    await adaptor.inputs(key=KEY, batch=("room-a",))
    try:
        await adaptor.accept(key=KEY, items=())
        raise AssertionError("must raise")
    except AccountNotRecordableError:
        pass
    assert isinstance(CultureProposal(body="x"), CultureProposal)


async def test_the_evidence_is_new_room_revisions_counted():
    store = InMemoryCharacterEvidence()
    for i, key in enumerate(("room-a", "room-b", "room-c", "room-a")):
        store.add_revision(key=key, written_at=T0 + i)
    evidence = UnitEvidence(store=store, keys={KEY: UK})
    assert await evidence.count(key=KEY, after=0) == 3
    assert await evidence.count(key=KEY, after=2) == 1
    items, cursor = await evidence.newest(key=KEY, after=1, limit=5)
    assert items == ["room-b", "room-a"] and cursor == 3


def test_no_unit_account_renders_no_block():
    assert render_unit_culture(None) is None
