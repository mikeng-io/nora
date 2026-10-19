"""`PersonaPersonAdaptor`: one person's Persona session in shadow
(design/unified-memory/45 §4, 00 «Sequence» step 6).

Through the real `SqlPersonaStore`, because what the session can get wrong
without anything saying so is a write: one that lands before the session
settles, one judged on handles that moved under it, a refusal that still
spends the batch, a turn's `told` line that never wakes anyone or wakes the
session forever. The judge's own rules are `tests/unit/test_persona_rules.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa

from nora.domain.culture import Episode, RoomMessage, character_key
from nora.domain.models import DeliveryStatus, PersonRef, TranscriptRow
from nora.domain.persona import Account, AddOp, PersonaCitable, Said
from nora.domain.ports import Proposal
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.persona import SqlPersonaStore
from nora.infra.sql.schema import PERSONA
from nora.language.character import CharacterRoom, PersonKey
from nora.language.character_session import AccountNotRecordableError
from nora.language.persona import PersonaEvidence, PersonaPersonAdaptor
from nora.obs.metrics import Metrics
from nora.testing import InMemoryCharacterEvidence, InMemoryCultureStore

DAY = 86_400_000
T0 = 1_790_000_000_000
NOW = datetime.fromtimestamp((T0 + 5 * DAY) / 1000, tz=UTC)
ADA = PersonRef(person_id="ada", name="Ada", honcho_peer_id="ada", aliases=("阿Ada",))
ROOMS = (
    CharacterRoom(key="room-a", platform="signal", label="Friends"),
    CharacterRoom(key="room-b", platform="discord", label="Zone 42"),
)
KEY = character_key("friends", "ada")
PK = PersonKey(unit="friends", person=ADA, rooms=ROOMS, home="room-a", listed=("room-a",))
NAMES = {"ada": "Ada", "bo": "Bo"}


def _msg(rid: int, room: str, ts: int, body: str = "a line", person: str = "ada") -> RoomMessage:
    return RoomMessage(room=room, row=TranscriptRow(
        id=rid, sender_id="+85200000000", sender_name="spoofed", ts=ts, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None, person_id=person,
    ))


#: Three of her messages across two days in a room that lists her.
BATCH = (
    _msg(41, "room-a", T0),
    _msg(42, "room-a", T0 + 1000),
    _msg(43, "room-a", T0 + DAY, "booked 8pm again"),
)

TRAIT = {
    "op": "add",
    "part": "character",
    "kind": "trait",
    "text": "the one who books",
    "mode": "induced",
    "citations": ["41", "42", "43"],
}


@pytest.fixture
async def env(pg_url: str) -> Any:
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def _adaptor(engine: Any, *, reads: InMemoryCharacterEvidence | None = None,
             culture: InMemoryCultureStore | None = None,
             metrics: Metrics | None = None) -> PersonaPersonAdaptor:
    persona = SqlPersonaStore(engine, screen=lambda _t: None)
    reads = reads or InMemoryCharacterEvidence()
    return PersonaPersonAdaptor(
        persona=persona, culture=culture or InMemoryCultureStore(),
        evidence=PersonaEvidence(store=reads, persona=persona, keys={KEY: PK}, wake_batch=5),
        reads=reads, keys={KEY: PK}, name_of=lambda pid: NAMES.get(pid or "", "someone"),
        clock_ms=lambda: T0 + 5 * DAY, metrics=metrics, clock=lambda: NOW,
    )


async def _settle(adaptor: PersonaPersonAdaptor, *, now_ms: int = T0 + 5 * DAY) -> int:
    proposals = await adaptor.accept(key=KEY, items=())
    return await adaptor.commit(key=KEY, now_ms=now_ms, proposals=[
        Proposal(payload=p, instruction_sha256="a" * 64, model="flash",
                 established_by="dream:persona-person:k:1") for p in proposals
    ])


async def _told(engine: Any, text: str = "vegetarian", *, at: int = T0) -> None:
    """A turn's `remember`: a told line written by the tag path."""
    result = await SqlPersonaStore(engine, screen=lambda _t: None).persona_write(
        account=Account(key=KEY, entity_kind="human", unit="friends", subject="ada"),
        ops=[
            AddOp(
                part="identity",
                kind="attribute",
                text=text,
                mode="told",
                citations=("41",),
                told_by="ada",
                fact_key="occupation",
                fact_value=text,
            )
        ],
        citable=PersonaCitable(
            messages={41: Said(at, "ada", "room-a")}, listed_rooms=frozenset({"room-a"})
        ),
        now_ms=at,
        written_by_session="tag",
        apply_decay=False,
    )
    assert result.status == "ok"


async def test_nothing_new_and_nothing_told_renders_nothing_so_no_model_is_called(env):
    assert await _adaptor(env).inputs(key=KEY, batch=()) == ""


async def test_the_session_is_handed_its_messages_with_their_age_and_never_a_sender_id(env):
    reads = InMemoryCharacterEvidence()
    reads.add_tie(unit="friends", person_a="ada", person_b="bo", counts=(3, 1, 0, 0, 2, 0))
    out = await _adaptor(env, reads=reads).inputs(key=KEY, batch=BATCH)
    assert 'name="Ada"' in out and "阿Ada" in out
    assert '<m n="43" room="Friends" age="' in out and "booked 8pm again" in out
    assert 'cite="rollup:tie:friends|ada,bo"' in out and 'with="Bo"' in out
    assert "spoofed" not in out and "+852" not in out and "colocation" not in out


async def test_a_staged_write_lands_only_at_commit_under_the_runs_provenance(env):
    adaptor = _adaptor(env)
    await adaptor.inputs(key=KEY, batch=BATCH)
    write, _no_change = adaptor.verbs(key=KEY)
    assert await write(ops=[TRAIT]) == {"status": "ok"}
    store = SqlPersonaStore(env, screen=lambda _t: None)
    assert await store.account_heads(key=KEY) == ()
    assert await _settle(adaptor) == 1
    [line] = await store.account_heads(key=KEY)
    assert (line.mode, line.part, line.citations) == ("induced", "character", ("41", "42", "43"))
    assert line.written_by_session == "dream:persona-person:k:1"


async def test_a_refused_write_answers_the_rule_it_broke_and_stages_nothing(env):
    metrics = Metrics()
    adaptor = _adaptor(env, metrics=metrics)
    await adaptor.inputs(key=KEY, batch=BATCH)
    write, _no_change = adaptor.verbs(key=KEY)
    answer = await write(ops=[{**TRAIT, "citations": ["41", "42"]}])
    assert answer["status"] == "refused"
    assert [r["reason"] for r in answer["refusals"]] == ["induced_instances"]
    assert metrics.get("nora_persona_session_total", outcome="refused") == 1
    with pytest.raises(AccountNotRecordableError):
        await _settle(adaptor)
    assert await SqlPersonaStore(env, screen=lambda _t: None).account_heads(key=KEY) == ()


async def test_an_unsettled_batch_is_held_twice_then_abandoned(env):
    metrics = Metrics()
    adaptor = _adaptor(env, metrics=metrics)
    for _ in range(2):
        await adaptor.inputs(key=KEY, batch=BATCH)
        with pytest.raises(AccountNotRecordableError):
            await _settle(adaptor)
    await adaptor.inputs(key=KEY, batch=BATCH)
    assert await _settle(adaptor) == 0
    assert metrics.get("nora_persona_session_total", outcome="unsettled") == 3
    assert metrics.get("nora_persona_session_total", outcome="abandoned") == 1


async def test_a_write_landing_between_read_and_commit_writes_nothing(env):
    adaptor = _adaptor(env)
    await adaptor.inputs(key=KEY, batch=BATCH)
    write, _no_change = adaptor.verbs(key=KEY)
    assert (await write(ops=[TRAIT]))["status"] == "ok"
    await _told(env, at=T0 + 2 * DAY)
    with pytest.raises(AccountNotRecordableError):
        await _settle(adaptor)
    heads = await SqlPersonaStore(env, screen=lambda _t: None).account_heads(key=KEY)
    assert [h.mode for h in heads] == ["told"]


async def test_a_second_write_after_settling_is_refused(env):
    adaptor = _adaptor(env)
    await adaptor.inputs(key=KEY, batch=BATCH)
    write, no_change = adaptor.verbs(key=KEY)
    assert await no_change(why="nothing new") == {"status": "ok"}
    again = await write(ops=[TRAIT])
    assert (again["status"], again["reason"]) == ("refused", "settled")


async def test_a_told_by_name_is_resolved_to_the_person(env):
    adaptor = _adaptor(env)
    await adaptor.inputs(key=KEY, batch=BATCH)
    write, _no_change = adaptor.verbs(key=KEY)
    told = {
        "op": "add",
        "part": "identity",
        "kind": "attribute",
        "text": "books dinners",
        "mode": "told",
        "citations": ["43"],
        "told_by": "ada",
        "fact_key": "occupation",
        "fact_value": "books dinners",
    }
    assert await write(ops=[{**told, "told_by": "Ada"}]) == {"status": "ok"}
    await _settle(adaptor)
    [line] = await SqlPersonaStore(env, screen=lambda _t: None).account_heads(key=KEY)
    assert line.told_by == "ada"


async def test_a_turns_told_line_wakes_the_session_until_it_settles(env):
    """The stimulus wake: a `told` line from a turn counts as a full batch,
    renders with no new messages, and stops waking once a session settles,
    even one that adds nothing (0118 `settled_at`)."""
    adaptor = _adaptor(env)
    assert await adaptor.evidence.count(key=KEY, after=0) == 0
    await _told(env)
    assert await adaptor.evidence.count(key=KEY, after=0) == 5
    out = await adaptor.inputs(key=KEY, batch=())
    assert 'mode="told"' in out and "vegetarian" in out
    _write, no_change = adaptor.verbs(key=KEY)
    assert (await no_change(why="already held"))["status"] == "ok"
    assert await _settle(adaptor) == 0
    assert await adaptor.evidence.count(key=KEY, after=0) == 0
    assert await adaptor.inputs(key=KEY, batch=()) == ""
    async with env.connect() as conn:
        assert (await conn.execute(sa.select(PERSONA.c.settled_at))).scalar_one() is not None

async def test_the_first_session_is_handed_the_character_account_and_its_cited_rows(env):
    """The backfill: until a session first settles the account it reads the
    prose Character head, and the rows it cited are citable; a turn's `told`
    line landing first does not skip it, and once settled it is not shown."""
    reads = InMemoryCharacterEvidence()
    for rid, ts in ((11, T0 - 3 * DAY), (12, T0 - 2 * DAY), (13, T0 - 2 * DAY + 5)):
        reads.add_message(id=rid, platform="signal", room="room-a", person_id="ada", ts=ts)
    culture = InMemoryCultureStore()
    await culture.append(key=KEY, body="the one who books", written_at=T0,
                         established_by="dream:character-person:x", citations=("11", "12", "13"))
    await _told(env)  # a turn's line before the first session does not skip it
    adaptor = _adaptor(env, reads=reads, culture=culture)
    out = await adaptor.inputs(key=KEY, batch=BATCH[:1])
    assert "<previous_character" in out and 'cites="11, 12, 13"' in out
    write, _no_change = adaptor.verbs(key=KEY)
    assert await write(ops=[{**TRAIT, "citations": ["11", "12", "13"]}]) == {"status": "ok"}
    assert await _settle(adaptor) == 1
    later = await _adaptor(env, reads=reads, culture=culture).inputs(key=KEY, batch=BATCH)
    assert "<previous_character" not in later and "<persona_lines" in later


async def test_evidence_reads_only_rooms_that_list_them_and_episodes_they_answered(env):
    reads = InMemoryCharacterEvidence()
    reads.add_message(id=1, platform="signal", room="room-a", person_id="ada", ts=T0)
    reads.add_message(id=2, platform="discord", room="room-b", person_id="ada", ts=T0)
    reply = TranscriptRow(id=4, sender_id="x", sender_name=None, ts=T0, body="yes",
                          is_nora=False, delivery_status=DeliveryStatus.SENT,
                          raw_json=None, person_id="ada")
    for send_id, room, replies in ((3, "room-a", (reply,)), (5, "room-a", ()),
                                   (6, "room-b", (reply,))):
        reads.add_episode(unit="friends", episode=Episode(
            send_id=send_id, room=room, text="dinner?", outcome="responded",
            reactions=1, edits=0, replies=replies))
    adaptor = _adaptor(env, reads=reads)
    assert await adaptor.evidence.count(key=KEY, after=0) == 5
    items, cursor = await adaptor.evidence.newest(key=KEY, after=0, limit=10)
    assert [i.row.id for i in items] == [1]
    assert cursor == 0  # history owns a separate cursor
    await adaptor.evidence.settle(key=KEY)
    # One empty history page proves exhaustion through the frozen boundary.
    await adaptor.evidence.newest(key=KEY, after=0, limit=10)
    await adaptor.evidence.settle(key=KEY)
    items, _ = await adaptor.evidence.newest(key=KEY, after=0, limit=10)
    assert [i.send_id for i in items] == [3]


async def test_mentions_are_read_with_the_messages_rendered_by_who_said_them_and_citable(env):
    """The mentions input: a message that MENTIONS the person is handed beside
    their own (never waking the session by itself), rendered with its author,
    and citable like any shown message."""
    reads = InMemoryCharacterEvidence()
    reads.add_message(id=1, platform="signal", room="room-a", person_id="ada", ts=T0)
    reads.add_message(id=2, platform="signal", room="room-a", person_id="bo", ts=T0 + 1,
                      body="ada booked again", mention_persons=frozenset({"ada"}))
    adaptor = _adaptor(env, reads=reads)
    assert await adaptor.evidence.count(key=KEY, after=0) == 5  # history wake
    items, cursor = await adaptor.evidence.newest(key=KEY, after=0, limit=10)
    assert cursor == 0  # normal recent watermark stays independent
    out = await adaptor.inputs(key=KEY, batch=items)
    assert '<persona_mentions' in out and 'by="Bo"' in out and "ada booked again" in out
    write, _no_change = adaptor.verbs(key=KEY)
    told = {
        "op": "add",
        "part": "identity",
        "kind": "attribute",
        "text": "books dinners",
        "mode": "told",
        "citations": ["2"],
        "told_by": "bo",
        "fact_key": "occupation",
        "fact_value": "books dinners",
    }
    assert await write(ops=[told]) == {"status": "ok"}


async def test_a_dispute_wakes_the_session_and_is_weighed_with_its_message(env):
    """The dispute input (35 §2): a dispute raised in a turn wakes the account's
    session like a told line, renders with its claim, its raiser and the held
    line it matched, the message it was said in is citable, and settling the
    account (even with no change) closes it."""
    await _told(env)
    reads = InMemoryCharacterEvidence()
    reads.add_message(id=77, platform="signal", room="room-a", person_id="bo",
                      ts=T0 + DAY, body="that's wrong")
    store = SqlPersonaStore(env, screen=lambda _t: None)
    done = await store.record_dispute(
        account=Account(key=KEY, entity_kind="human", unit="friends", subject="ada"),
        disputed_by="bo", kind="dispute", claim="vegetarian", message_id=77,
        now_ms=T0 + DAY)
    assert (done.status, done.effect) == ("ok", "disputed")
    adaptor = _adaptor(env, reads=reads)
    assert await adaptor.evidence.count(key=KEY, after=0) == 5
    out = await adaptor.inputs(key=KEY, batch=())
    assert '<persona_disputes' in out and 'by="Bo"' in out and 'line="P1"' in out
    assert 'said="77"' in out and "vegetarian" in out
    write, _no_change = adaptor.verbs(key=KEY)
    told = {
        "op": "add",
        "part": "identity",
        "kind": "attribute",
        "text": "bo disputes the vegetarian line",
        "mode": "told",
        "citations": ["77"],
        "told_by": "bo",
        "fact_key": "occupation",
        "fact_value": "bo disputes the vegetarian line",
    }
    assert await write(ops=[told]) == {"status": "ok"}
    assert await _settle(adaptor) == 1
    assert await store.pending_disputes(key=KEY) == 0
    assert await adaptor.evidence.count(key=KEY, after=0) == 5
