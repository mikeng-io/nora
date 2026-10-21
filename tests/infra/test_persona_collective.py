"""`PersonaCollectiveAdaptor`: a room's, the unit's and Nora's Persona
sessions in shadow (design/unified-memory/45 §4) — the person session's
checked writer over each collective's own evidence.

Through the real `SqlPersonaStore`, because what a collective session can
get wrong without anything saying so is the same as a person's: a write that
lands before the session settles, a citation of a row it was never shown
(her own send above all — the echo rule), a first session that never saw the
prose head it exists to replace.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from nora.domain.culture import (
    Episode,
    RoomMessage,
    nora_character_key,
    unit_culture_key,
)
from nora.domain.models import DeliveryStatus, TranscriptRow
from nora.domain.persona import Account, AddOp, PersonaCitable, Said
from nora.domain.ports import Proposal
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.persona import SqlPersonaStore
from nora.language.character import CharacterRoom
from nora.language.persona_collective import (
    CollectiveEvidence,
    CollectiveKey,
    PersonaCollectiveAdaptor,
)
from nora.language.persona_verdict import DocketAccount, PersonaVerdictAdaptor
from nora.obs.metrics import Metrics
from nora.testing import InMemoryCharacterEvidence, InMemoryCultureStore

DAY = 86_400_000
T0 = 1_790_000_000_000
NOW = datetime.fromtimestamp((T0 + 5 * DAY) / 1000, tz=UTC)
UNIT = "friends"
ROOM = "room-a"
ROOM_KEY = CollectiveKey(
    unit=UNIT,
    label="Friends",
    rooms=(CharacterRoom(key=ROOM, platform="signal", label="Friends"),),
)
UNIT_KEY = CollectiveKey(
    unit=UNIT,
    label=UNIT,
    rooms=(
        CharacterRoom(key=ROOM, platform="signal", label="Friends"),
        CharacterRoom(key="room-b", platform="discord", label="Zone 42"),
    ),
)
NORA_KEY = CollectiveKey(unit=UNIT, label="Nora", rooms=UNIT_KEY.rooms)
NAMES = {"ada": "Ada", "bo": "Bo"}


def _msg(rid: int, room: str, ts: int, body: str = "a line", person: str = "ada",
         nora: bool = False) -> RoomMessage:
    return RoomMessage(room=room, row=TranscriptRow(
        id=rid, sender_id="+85200000000", sender_name="spoofed", ts=ts, body=body,
        is_nora=nora, delivery_status=DeliveryStatus.SENT, raw_json=None, person_id=person,
    ))


def _episode(send_id: int, room: str, ts: int, text: str,
             replies: tuple[TranscriptRow, ...] = ()) -> Episode:
    return Episode(send_id=send_id, room=room, text=text,
                   outcome="responded" if replies else "silent",
                   reactions=0, edits=0, replies=replies)


def _reply(rid: int, ts: int, person: str = "ada", body: str = "yes!") -> TranscriptRow:
    return TranscriptRow(
        id=rid, sender_id="+85200000000", sender_name="spoofed", ts=ts, body=body,
        is_nora=False, delivery_status=DeliveryStatus.SENT, raw_json=None, person_id=person,
    )


@pytest.fixture
async def env(pg_url: str) -> Any:
    run_migrations(pg_url)
    engine = create_sql_engine(pg_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def _adaptor(engine: Any, kind: str, keys: dict[str, CollectiveKey], *,
             reads: InMemoryCharacterEvidence | None = None,
             culture: InMemoryCultureStore | None = None) -> PersonaCollectiveAdaptor:
    reads = reads or InMemoryCharacterEvidence()
    return PersonaCollectiveAdaptor(
        kind=kind,  # type: ignore[arg-type]
        persona=SqlPersonaStore(engine, screen=lambda _t: None),
        culture=culture or InMemoryCultureStore(),
        evidence=CollectiveEvidence(store=reads, keys=keys, kind=kind),  # type: ignore[arg-type]
        reads=reads, keys=keys,
        name_of=lambda pid: NAMES.get(pid or "", "someone"),
        clock_ms=lambda: T0 + 5 * DAY, metrics=Metrics(), clock=lambda: NOW,
    )


async def _settle(adaptor: PersonaCollectiveAdaptor, key: str) -> int:
    proposals = await adaptor.accept(key=key, items=())
    return await adaptor.commit(key=key, now_ms=T0 + 5 * DAY, proposals=[
        Proposal(payload=p, instruction_sha256="a" * 64, model="flash",
                 established_by=f"dream:{adaptor.name}:k:1") for p in proposals
    ])


# -- a room -----------------------------------------------------------------


async def test_a_room_wakes_on_its_own_messages_and_reads_them_but_never_hers(env):
    reads = InMemoryCharacterEvidence()
    reads.add_message(id=41, platform="signal", room=ROOM, person_id="ada", ts=T0)
    reads.add_message(id=42, platform="signal", room=ROOM, person_id=None,
                      body="anyone up for it", ts=T0 + 1000, is_nora=True)
    reads.add_message(id=43, platform="signal", room=ROOM, person_id="bo",
                      body="wednesday works", ts=T0 + DAY)
    reads.add_message(id=44, platform="signal", room=ROOM, person_id="ada",
                      body="same again", ts=T0 + 2 * DAY)
    reads.add_message(id=45, platform="signal", room="elsewhere", person_id="ada", ts=T0)
    adaptor = _adaptor(env, "room", {ROOM: ROOM_KEY}, reads=reads)
    evidence = adaptor.evidence
    assert await evidence.count(key=ROOM, after=0) == 3  # hers and elsewhere's excluded
    batch, cursor = await evidence.newest(key=ROOM, after=0, limit=10)
    assert cursor == 44
    out = await adaptor.inputs(key=ROOM, batch=batch)
    assert 'kind="room"' in out and 'name="Friends"' in out
    assert "wednesday works" in out
    assert "anyone up for it" not in out  # her own words are never evidence
    [write] = [v for v in adaptor.verbs(key=ROOM) if v.__name__ == "persona_write"]
    result = await write(
        ops=[
            {
                "op": "add",
                "part": "identity",
                "kind": "purpose",
                "text": "planning the weekly game",
                "mode": "told",
                "citations": ["43"],
                "told_by": "Bo",
                "fact_key": "purpose",
                "fact_value": "planning the weekly game",
            },
            {
                "op": "add",
                "part": "character",
                "kind": "trait",
                "text": "meets midweek",
                "mode": "induced",
                "citations": ["41", "43", "44"],
            },
        ]
    )
    assert result["status"] == "ok"
    assert await _settle(adaptor, ROOM) == 2
    heads = await SqlPersonaStore(env, screen=lambda _t: None).account_heads(key=ROOM)
    assert {h.kind for h in heads} == {"purpose", "trait"}
    assert all(h.unit == UNIT for h in heads)


async def test_a_rooms_first_session_rewrites_the_prose_culture_head(env):
    """The backfill (35 §4): until a session settles the account it is handed
    the prose head under the same key, once, and may cite what it cited."""
    culture = InMemoryCultureStore()
    await culture.append(key=ROOM, body="the room that games on Wednesdays",
                         written_at=T0 - 30 * DAY, established_by="dream:culture:k:1",
                         citations=("41",))
    reads = InMemoryCharacterEvidence()
    reads.add_message(id=41, platform="signal", room=ROOM, person_id="ada",
                      body="wednesday?", ts=T0 - 30 * DAY)
    reads.add_message(id=42, platform="signal", room=ROOM, person_id="bo",
                      body="again!", ts=T0)
    adaptor = _adaptor(env, "room", {ROOM: ROOM_KEY}, reads=reads, culture=culture)
    batch, _ = await adaptor.evidence.newest(key=ROOM, after=0, limit=10)
    out = await adaptor.inputs(key=ROOM, batch=batch)
    assert "the room that games on Wednesdays" in out
    assert 'cites="41"' in out  # the person session's rendering: the session
    # may cite what the prose head cited; the row itself is in the messages
    [write] = [v for v in adaptor.verbs(key=ROOM) if v.__name__ == "persona_write"]
    reads.add_message(id=44, platform="signal", room=ROOM, person_id="ada", ts=T0 + 2 * DAY)
    batch, _ = await adaptor.evidence.newest(key=ROOM, after=0, limit=10)
    await adaptor.inputs(key=ROOM, batch=batch)
    result = await write(ops=[
        {"op": "add", "part": "character", "kind": "trait", "text": "games midweek",
         "mode": "deduced", "citations": ["41", "42", "44"]},
    ])
    assert result["status"] == "ok", result
    assert await _settle(adaptor, ROOM) == 1
    # Once settled, the prose head is never handed again.
    reads.add_message(id=43, platform="signal", room=ROOM, person_id="ada", ts=T0 + DAY)
    batch, _ = await adaptor.evidence.newest(key=ROOM, after=42, limit=10)
    out = await _adaptor(env, "room", {ROOM: ROOM_KEY}, reads=reads,
                       culture=culture).inputs(key=ROOM, batch=batch)
    assert "the room that games on Wednesdays" not in out


async def test_a_room_line_cannot_cite_her_own_message(env):
    """The echo rule, enforced: her rows are never in the citable set, so one
    named in a citation is refused as something the session was not shown."""
    reads = InMemoryCharacterEvidence()
    reads.add_message(id=41, platform="signal", room=ROOM, person_id="ada", ts=T0)
    reads.add_message(id=42, platform="signal", room=ROOM, person_id=None,
                      body="game tonight?", ts=T0 + 1000, is_nora=True)
    reads.add_message(id=43, platform="signal", room=ROOM, person_id="bo",
                      body="yes", ts=T0 + 2000)
    adaptor = _adaptor(env, "room", {ROOM: ROOM_KEY}, reads=reads)
    batch, _ = await adaptor.evidence.newest(key=ROOM, after=0, limit=10)
    await adaptor.inputs(key=ROOM, batch=batch)
    [write] = [v for v in adaptor.verbs(key=ROOM) if v.__name__ == "persona_write"]
    result = await write(
        ops=[
            {
                "op": "add",
                "part": "identity",
                "kind": "name",
                "text": "the game room",
                "mode": "told",
                "citations": ["42"],
                "told_by": "someone",
                "fact_key": "name",
                "fact_value": "the game room",
            },
        ]
    )
    assert result["status"] == "refused"
    assert result["refusals"][0]["reason"] == "unresolved_citation"


# -- the unit ---------------------------------------------------------------


async def test_the_unit_wakes_on_its_rooms_culture_revisions_and_cites_their_rows(env):
    reads = InMemoryCharacterEvidence()
    reads.add_revision(key=ROOM, written_at=T0)
    reads.add_revision(key=ROOM, written_at=T0 + DAY)
    reads.add_message(id=41, platform="signal", room=ROOM, person_id="ada",
                      body="wednesday again", ts=T0 - DAY)
    reads.add_message(id=42, platform="signal", room=ROOM, person_id="bo",
                      body="and next week", ts=T0)
    culture = InMemoryCultureStore()
    await culture.append(key=ROOM, body="games midweek", written_at=T0,
                         established_by="dream:culture:k:1", citations=("41", "42"))
    key = unit_culture_key(UNIT)
    adaptor = _adaptor(env, "unit", {key: UNIT_KEY}, reads=reads, culture=culture)
    evidence = adaptor.evidence
    assert await evidence.count(key=key, after=0) == 2
    assert await evidence.count(key=key, after=2) == 0
    batch, cursor = await evidence.newest(key=key, after=0, limit=10)
    assert cursor == 2
    out = await adaptor.inputs(key=key, batch=batch)
    assert 'kind="unit"' in out
    assert "games midweek" in out  # the room's culture, as context
    assert 'cites="41, 42"' in out  # and what it rests on: the unit cites through them
    assert "wednesday again" in out  # and the row it cited, as evidence
    [write] = [v for v in adaptor.verbs(key=key) if v.__name__ == "persona_write"]
    result = await write(
        ops=[
            {
                "op": "add",
                "part": "identity",
                "kind": "purpose",
                "text": "the weekly game",
                "mode": "told",
                "citations": ["41"],
                "told_by": "Ada",
                "fact_key": "purpose",
                "fact_value": "the weekly game",
            },
        ]
    )
    assert result["status"] == "ok", result
    assert await _settle(adaptor, key) == 1
    heads = await SqlPersonaStore(env, screen=lambda _t: None).account_heads(key=key)
    assert [h.kind for h in heads] == ["purpose"]


async def test_a_collective_identity_holds_no_actor_kind(env):
    """`instruction` is an actor's kind: code refuses it on a room's account."""
    reads = InMemoryCharacterEvidence()
    reads.add_message(id=41, platform="signal", room=ROOM, person_id="ada", ts=T0)
    adaptor = _adaptor(env, "room", {ROOM: ROOM_KEY}, reads=reads)
    batch, _ = await adaptor.evidence.newest(key=ROOM, after=0, limit=10)
    await adaptor.inputs(key=ROOM, batch=batch)
    [write] = [v for v in adaptor.verbs(key=ROOM) if v.__name__ == "persona_write"]
    result = await write(
        ops=[
            {
                "op": "add",
                "part": "identity",
                "kind": "instruction",
                "text": "never plan on a friday",
                "mode": "told",
                "citations": ["41"],
                "told_by": "Ada",
                "fact_key": "instruction",
                "fact_value": "never plan on a friday",
            },
        ]
    )
    assert result["status"] == "refused"
    assert result["refusals"][0]["reason"] == "part_kind"


# -- Nora -------------------------------------------------------------------


async def test_nora_wakes_on_closed_episodes_and_cites_them_never_her_send(env):
    reads = InMemoryCharacterEvidence()
    reads.add_message(id=41, platform="signal", room=ROOM, person_id=None,
                      body="game tonight?", ts=T0, is_nora=True)
    episode = _episode(41, ROOM, T0, "game tonight?",
                       replies=(_reply(42, T0 + 1000, "ada", "in!"),
                                _reply(43, T0 + DAY, "bo", "again wednesday")))
    reads.add_episode(unit=UNIT, episode=episode)
    key = nora_character_key(UNIT)
    adaptor = _adaptor(env, "nora", {key: NORA_KEY}, reads=reads)
    evidence = adaptor.evidence
    assert await evidence.count(key=key, after=0) == 1
    batch, cursor = await evidence.newest(key=key, after=0, limit=10)
    assert cursor == 41
    out = await adaptor.inputs(key=key, batch=batch)
    assert 'kind="nora"' in out and 'name="Nora"' in out
    assert 'n="response:41"' in out and "in!" in out
    [write] = [v for v in adaptor.verbs(key=key) if v.__name__ == "persona_write"]
    # Her send is not a citation; the episode is.
    refused = await write(
        ops=[
            {
                "op": "add",
                "part": "identity",
                "kind": "attribute",
                "text": "asks about games",
                "mode": "told",
                "citations": ["41"],
                "told_by": "Ada",
                "fact_key": "occupation",
                "fact_value": "asks about games",
            },
        ]
    )
    assert refused["status"] == "refused"
    ok = await write(ops=[
        {"op": "add", "part": "character", "kind": "trait", "text": "game nights land",
         "mode": "induced", "citations": ["response:41"]},
    ])
    # One episode is one instance on one day: the induction rule refuses it.
    assert ok["status"] == "refused"
    assert ok["refusals"][0]["reason"] == "induced_instances"
    deduced = await write(ops=[
        {"op": "add", "part": "character", "kind": "trait", "text": "game nights land",
         "mode": "deduced", "citations": ["response:41", "42"]},
    ])
    assert deduced["status"] == "refused", deduced
    assert deduced["refusals"][0]["reason"] == "deduced_premises"


async def test_a_collective_hypothesis_joins_the_units_docket(env):
    """The verdict judge grades a room's hypothesis like a person's (45 §3):
    the docket is the unit's, and the room's own rooms bound its window."""
    store = SqlPersonaStore(env, screen=lambda _t: None)
    said = PersonaCitable(
        messages={n: Said(T0 + n * DAY, "ada", ROOM) for n in range(1, 5)},
        listed_rooms=frozenset({ROOM}))
    account = Account(key=ROOM, entity_kind="room", unit=UNIT)
    wrote = await store.persona_write(
        account=account, citable=said, now_ms=T0, written_by_session="t",
        apply_decay=False,
        ops=[AddOp(part="character", kind="trait", text="games midweek",
                   mode="induced", citations=("1", "2", "3"))])
    assert wrote.status == "ok", wrote.refusals
    wrote = await store.persona_write(
        account=account, citable=said, now_ms=T0 + DAY, written_by_session="t",
        apply_decay=False,
        ops=[AddOp(part="hypothesis", kind="explanation", text="weekday nights are short",
                   content={"proposed_cause": "limited time", "alternative": "prefers brevity"},
                   mode="abduced", citations=("1",), draws_on=("P1",),
                   prediction="a short game midweek",
                   prediction_observable="someone proposes a weeknight game",
                   prediction_window_end=T0 + 3 * DAY)])
    assert wrote.status == "ok", wrote.refusals
    docket = await store.due_hypotheses(unit=UNIT, now_ms=T0 + 5 * DAY, limit=5)
    assert len(docket) == 1
    judge = PersonaVerdictAdaptor(
        store=store, reads=InMemoryCharacterEvidence(), rollup=None,  # type: ignore[arg-type]
        keys={ROOM: DocketAccount(about="Friends", rooms=(("signal", ROOM),))},
        name_of=lambda pid: NAMES.get(pid or "", "someone"),
        clock_ms=lambda: T0 + 5 * DAY, metrics=Metrics(), clock=lambda: NOW)
    out = await judge.inputs(key=UNIT, batch=docket)
    assert 'about="Friends"' in out
    [verdict] = judge.verbs(key=UNIT)
    assert await verdict(verdicts=[{"line": "H1", "verdict": "expired"}]) == {"status": "ok"}
    staged = await judge.accept(key=UNIT, items=())
    written = await judge.commit(key=UNIT, now_ms=T0 + 5 * DAY, proposals=[
        Proposal(payload=p, instruction_sha256="a" * 64, model="flash",
                 established_by="dream:persona-verdict:k:1") for p in staged])
    assert written == 1
    [head] = [h for h in await store.account_heads(key=ROOM) if h.part == "hypothesis"]
    assert head.verdict == "expired"


async def test_unit_switch_and_rollback_keep_independent_durable_cursors(env):
    """Real wake/inputs/commit and SQL cursors, with unequal evidence domains."""
    from types import SimpleNamespace

    from nora.app.runtime import _dream_evidence_once
    from nora.infra.sql.language import SqlLanguageStore

    class Reads(InMemoryCharacterEvidence):
        legacy = 100
        persona = 3

        async def revision_count(self, *, keys):
            return self.legacy

        async def revisions_after(self, *, keys, after, limit):
            return ([ROOM] if self.legacy > after else []), self.legacy

        async def persona_revision_count(self, *, keys):
            return self.persona

        async def persona_revisions_after(self, *, keys, after, limit):
            return ([ROOM] if self.persona > after else []), self.persona

    reads = Reads()
    reads.add_message(id=41, platform="signal", room=ROOM, person_id="ada", ts=T0)
    culture = InMemoryCultureStore()
    await culture.append(key=ROOM, body="old room context", written_at=T0,
                         established_by="test", citations=("41",))
    key = unit_culture_key(UNIT)
    cursor = SqlLanguageStore(env)
    await cursor.advance_watermark(consolidator_name="persona-unit", scope_id=key,
                                   last_message_id=100)
    seen = []

    class Runner:
        failed = False

        async def run(self, *, adaptor, key, batch):
            prompt = await adaptor.inputs(key=key, batch=batch)
            assert prompt
            seen.append(prompt)
            if self.failed:
                return SimpleNamespace(status="unavailable")
            [write] = [v for v in adaptor.verbs(key=key) if v.__name__ == "persona_write"]
            context = "new room context" if adaptor._render else "old room context"
            result = await write(ops=[{
                "op": "add", "part": "identity", "kind": "norm", "mode": "told",
                "text": context, "fact_key": "norm", "fact_value": context,
                "citations": ["41"], "told_by": "Ada",
            }])
            assert result["status"] == "ok", result
            await _settle(adaptor, key)
            return SimpleNamespace(status="ok")

    runner = Runner()

    async def tick(render):
        adaptor = _adaptor(env, "unit", {key: UNIT_KEY}, reads=reads, culture=culture)
        adaptor._render = render
        adaptor.evidence = CollectiveEvidence(store=reads, keys={key: UNIT_KEY},
                                              kind="unit", render=render)
        return await _dream_evidence_once(
            adaptor=adaptor, runtime=runner, language=cursor, metrics=Metrics(),
            evidence=adaptor.evidence, key=key, sched=None, batch_limit=10,
            max_age_seconds=0,
        )

    # A newly committed room account must wake even though its counter is 3 < 100.
    store = SqlPersonaStore(env, screen=lambda _t: None)
    await store.persona_write(
        account=Account(key=ROOM, entity_kind="room", unit=UNIT),
        ops=[AddOp(part="identity", kind="purpose", text="new room context",
                   fact_key="purpose", fact_value="new room context", mode="told",
                   citations=("41",), told_by="ada")],
        citable=PersonaCitable(messages={41: Said(T0, "ada", ROOM)}),
        now_ms=T0, written_by_session="test", apply_decay=False,
    )
    assert await tick(True) == "ok"
    assert "new room context" in seen[-1]
    assert any(h.fact_value == "new room context" for h in await store.account_heads(key=key))
    assert await cursor.watermark_for(consolidator_name="persona-unit:persona", scope_id=key) == 3
    assert await cursor.watermark_for(consolidator_name="persona-unit", scope_id=key) == 100
    reads.persona = 1000
    runner.failed = True
    assert await tick(True) == "unavailable"
    assert await cursor.watermark_for(consolidator_name="persona-unit:persona", scope_id=key) == 3
    runner.failed = False
    assert await tick(True) == "ok"
    reads.legacy = 101
    assert await tick(False) == "ok"
    assert "old room context" in seen[-1]
    assert any(h.fact_value == "old room context" for h in await store.account_heads(key=key))
    assert await cursor.watermark_for(consolidator_name="persona-unit", scope_id=key) == 101
    assert await tick(False) is None
    assert await tick(True) is None
    reads.legacy = 102
    runner.failed = True
    assert await tick(False) == "unavailable"
    assert await cursor.watermark_for(consolidator_name="persona-unit", scope_id=key) == 101
    runner.failed = False
    assert await tick(False) == "ok"
    assert await store.account_state(key=key) == (4, T0 + 5 * DAY)
