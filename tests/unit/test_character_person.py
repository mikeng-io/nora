"""`CharacterPersonAdaptor` — one person's account per (unit, person), woken by
their own messages.

Written around what a person session can get wrong without anything saying
so: write someone else's lines into an account, depend on a memory answer
fetched before the pass, cite a message it was never shown, key the account by
a room so two rooms of one unit hold two accounts, or hand presence to an
account that renders where presence may not go.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nora.agent.egress import scan_identifiers
from nora.domain.culture import CultureRevision, RoomMessage, character_key
from nora.domain.models import DeliveryStatus, PersonRef, TranscriptRow
from nora.domain.ports import Proposal
from nora.language.character import (
    _BLOCKS,
    CHARACTER_PERSON_TOOLS,
    CharacterPersonAdaptor,
    CharacterRoom,
    PersonEvidence,
    PersonKey,
)
from nora.language.character_session import AccountNotRecordableError
from nora.obs.metrics import Metrics
from nora.testing import InMemoryCharacterEvidence, InMemoryCultureStore

T0 = 1_790_000_000_000
NOW = datetime.fromtimestamp((T0 + 86_400_000) / 1000, tz=UTC)
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada", aliases=("阿Ada",))
ROOMS = (
    CharacterRoom(key="room-a", platform="signal", label="Friends"),
    CharacterRoom(key="room-b", platform="discord", label="Zone 42"),
)
KEY = character_key("friends", "ada")
PK = PersonKey(unit="friends", person=ADA, rooms=ROOMS, home="room-a")


def _msg(rid: int, room: str, body: str) -> RoomMessage:
    return RoomMessage(room=room, row=TranscriptRow(
        id=rid, sender_id="+85200000000", sender_name="spoofed", ts=T0, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None,
    ))


def _adaptor(
    store: InMemoryCultureStore | None = None, metrics: Metrics | None = None,
) -> CharacterPersonAdaptor:
    return CharacterPersonAdaptor(
        culture=store or InMemoryCultureStore(),
        evidence=PersonEvidence(store=InMemoryCharacterEvidence(), keys={KEY: PK}),
        keys={KEY: PK}, metrics=metrics, clock=lambda: NOW,
        screen=scan_identifiers, render_chars=240,
    )


BATCH = (_msg(41, "room-a", "booked 8pm, see you"), _msg(57, "room-b", "gg, again?"))


def test_the_key_is_the_unit_and_the_person_not_a_room():
    assert KEY == "friends#character:ada"
    assert _adaptor().keys() == (KEY,)
    assert _adaptor().tool_room(KEY) == "room-a"


async def test_an_empty_batch_renders_nothing_so_no_model_is_called():
    assert await _adaptor().inputs(key=KEY, batch=()) == ""


async def test_the_session_is_handed_its_account_its_rooms_and_its_messages_by_id():
    store = InMemoryCultureStore()
    await store.append(key=KEY, body="the one who books", written_at=T0,
                       established_by="dream:character-person:x")
    await store.append(key="room-b", body="a games room that plays late",
                       written_at=T0, established_by="dream:culture:x",
                       citations=("57",))
    out = await _adaptor(store).inputs(key=KEY, batch=BATCH)
    names = [name for name, _o, _k in _BLOCKS
             if name not in ("about_unit", "episodes", "social_items")]
    positions = [out.index(f"<{name} ") for name in names]
    assert positions == sorted(positions), out
    assert 'name="Ada"' in out and "阿Ada" in out
    assert "the one who books" in out
    assert 'room="Zone 42"' in out and "a games room that plays late" in out
    assert '<m n="41" room="Friends">booked 8pm, see you</m>' in out
    assert '<m n="57" room="Zone 42">gg, again?</m>' in out
    assert "spoofed" not in out and "+852" not in out


async def test_a_cited_account_is_written_under_the_persons_unit_key():
    store = InMemoryCultureStore()
    adaptor = _adaptor(store)
    await adaptor.inputs(key=KEY, batch=BATCH)
    revise, _keep = adaptor.verbs(key=KEY)
    answer = await revise(entity="Ada", body="the one who books", citations=["41", "57"])
    assert answer == {"status": "ok"}
    proposals = await adaptor.accept(key=KEY, items=())
    await adaptor.commit(key=KEY, now_ms=T0, proposals=[
        Proposal(payload=p, instruction_sha256="a" * 64, model="flash",
                 established_by="dream:character-person:k:1") for p in proposals
    ])
    head = await store.head(key=KEY)
    assert head is not None and head.body == "the one who books"
    assert head.citations == ("41", "57")
    assert await store.head(key=character_key("room-a", "ada")) is None


async def test_a_citation_the_session_was_not_handed_is_refused():
    m = Metrics()
    adaptor = _adaptor(metrics=m)
    await adaptor.inputs(key=KEY, batch=BATCH)
    revise, _keep = adaptor.verbs(key=KEY)
    answer = await revise(entity="Ada", body="x", citations=["41", "40"])
    assert (answer["status"], answer["reason"]) == ("refused", "unresolved_citation")
    assert m.get("nora_character_session_total", kind="person", outcome="refused") == 1


async def test_another_persons_name_is_not_an_entity_of_this_session():
    adaptor = _adaptor()
    await adaptor.inputs(key=KEY, batch=BATCH)
    revise, _keep = adaptor.verbs(key=KEY)
    answer = await revise(entity="Bo", body="x", citations=["41"])
    assert answer["reason"] == "unknown_entity"


async def test_an_unsettled_session_holds_and_a_kept_one_spends_the_batch():
    m = Metrics()
    adaptor = _adaptor(metrics=m)
    await adaptor.inputs(key=KEY, batch=BATCH)
    try:
        await adaptor.accept(key=KEY, items=())
        raise AssertionError("an unsettled session must raise")
    except AccountNotRecordableError:
        pass
    await adaptor.inputs(key=KEY, batch=BATCH)
    _revise, keep = adaptor.verbs(key=KEY)
    assert await keep(entity="ada", why="nothing new") == {"status": "ok"}
    assert await adaptor.accept(key=KEY, items=()) == ()
    assert m.get("nora_character_session_total", kind="person", outcome="kept") == 1


def test_memory_is_a_tool_the_session_may_call_never_a_prefetch():
    """Memory is a read the session MAY make, so a later merge or sunset
    of Honcho changes no Character code. The adaptor holds no memory
    store at all, and its tools carry the two memory reads."""
    import inspect

    params = inspect.signature(CharacterPersonAdaptor.__init__).parameters
    assert "memory" not in params
    assert {"ask_memory", "search_memory"} <= CHARACTER_PERSON_TOOLS


def test_a_person_session_holds_no_presence_read_and_no_act_verb():
    """The account renders in every room of the unit that lists the person,
    and those rooms' `location` flags differ: no presence read reaches it
    (R-31). Every verb is inside the Dreamer's read-only ceiling."""
    from nora.agent.registry import ToolRegistry

    assert CHARACTER_PERSON_TOOLS.isdisjoint({"where_been", "online_now", "where_is"})
    assert ToolRegistry().read_only_tools() >= CHARACTER_PERSON_TOOLS


def test_a_person_session_holds_no_web_tool():
    """A person session carries memory and a roster check, not the open web:
    `web_search` and `web_fetch` each cost a call and reach outside the room
    (DEC-290)."""
    assert CHARACTER_PERSON_TOOLS.isdisjoint({"web_search", "web_fetch"})
    assert {
        "ask_memory", "search_memory", "about", "lookup_person", "group_members",
        # DEC-316: built instead of the first two while the index is on
        "memory_search",
    } == CHARACTER_PERSON_TOOLS


async def test_the_evidence_counts_and_reads_their_lines_across_the_units_rooms():
    store = InMemoryCharacterEvidence()
    store.add_message(id=1, platform="signal", room="room-a", person_id="ada")
    store.add_message(id=2, platform="discord", room="room-b", person_id="ada")
    store.add_message(id=3, platform="signal", room="room-c", person_id="ada")
    store.add_message(id=4, platform="signal", room="room-a", person_id="bo")
    evidence = PersonEvidence(store=store, keys={KEY: PK})
    assert await evidence.count(key=KEY, after=0) == 2
    items, cursor = await evidence.newest(key=KEY, after=0, limit=5)
    assert [m.row.id for m in items] == [1, 2] and cursor == 2


async def test_a_byte_identical_account_is_not_appended():
    store = InMemoryCultureStore()
    await store.append(key=KEY, body="same", written_at=T0, established_by="x")
    adaptor = _adaptor(store)
    from nora.domain.culture import CultureProposal

    written = await adaptor.commit(key=KEY, now_ms=T0, proposals=[Proposal(
        payload=CultureProposal(body="same", citations=("41",)),
        instruction_sha256="a" * 64, model="flash", established_by="run",
    )])
    assert written == 0
    assert isinstance(await store.head(key=KEY), CultureRevision)


async def test_a_room_culture_with_no_citations_is_not_handed_to_the_person():
    """An account written by the envelope fold, which held the presence
    reads, renders only in its own room; the person's account renders in every
    room of the unit that lists them, so it is not handed one. A cited room culture still is."""
    store = InMemoryCultureStore()
    await store.append(key="room-a", body="plans by attrition", written_at=T0,
                       established_by="dream:culture:x", citations=("41",))
    await store.append(key="room-b", body="Bo is usually at the gym", written_at=T0,
                       established_by="dream:culture:old")
    out = await _adaptor(store).inputs(key=KEY, batch=BATCH)
    assert "plans by attrition" in out
    assert "Bo is usually at the gym" not in out


class _SpyEvidence(InMemoryCharacterEvidence):
    """Records the `after` of every `person_count`, to see how far back it
    looked."""

    def __init__(self) -> None:
        super().__init__()
        self.afters: list[int] = []

    async def person_count(self, *, rooms, person_id, after):
        self.afters.append(after)
        return await super().person_count(rooms=rooms, person_id=person_id, after=after)


async def test_a_listed_person_who_never_posted_is_not_rescanned_from_the_start_every_tick():
    """Their watermark never moves, so the count over everything after it is
    the same scan over the unit's whole history every tick. After a count that
    found nothing, the next starts where that one looked to, and a message
    that arrives later is still counted."""
    store = _SpyEvidence()
    for i in range(1, 51):
        store.add_message(id=i, platform="signal", room="room-a", person_id="bo")
    evidence = PersonEvidence(store=store, keys={KEY: PK})
    assert await evidence.count(key=KEY, after=0) == 0
    assert await evidence.count(key=KEY, after=0) == 0
    assert store.afters == [0, 50]
    store.add_message(id=51, platform="signal", room="room-a", person_id="ada")
    store.add_message(id=52, platform="discord", room="room-b", person_id="ada")
    assert await evidence.count(key=KEY, after=0) == 2
    assert store.afters == [0, 50, 50]


async def test_a_person_with_messages_is_counted_from_their_watermark_as_before():
    store = _SpyEvidence()
    store.add_message(id=1, platform="signal", room="room-a", person_id="ada")
    store.add_message(id=2, platform="signal", room="room-a", person_id="bo")
    evidence = PersonEvidence(store=store, keys={KEY: PK})
    assert await evidence.count(key=KEY, after=0) == 1
    assert await evidence.count(key=KEY, after=0) == 1
    assert store.afters == [0, 0]
