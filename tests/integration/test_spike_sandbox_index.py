"""A replay sandbox's empty derived index is projected before inspect.

`sandbox_as_of` restores no `memory_*` table (`history.NOT_RESTORED`), so a
wake whose pending stimuli carry a `social` read finds no `memory_node` and
`WakeMemory.prepare`'s `records.load` raises NoResultFound — the smoke's
social-wake ERROR. The spike's `index_replay_sandbox` projects the index
the way a graph-surface eval draw gets it (`nora.subject.sandbox_index`):
production's `rebuild` over the rows restored as of the wake's instant,
the source's vectors adopted where the input is identical. Everything here
is generated; the as-of controls pin that the index comes from the
restored rows, never from the source's later state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import NoResultFound

from nora.agent.fetch_watch import GraphFetched
from nora.app.cognition_memory import WakeMemory
from nora.domain.cognition_ground import Grounding, RecordItem
from nora.domain.cognition_session import Room
from nora.domain.memory_graph import WorkItem
from nora.infra.embedder import fingerprint
from nora.infra.sql import memory_index
from nora.infra.sql.cognition_replay import recorded_wake
from nora.infra.sql.comprehend import SqlComprehendRecords
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.memory_graph import SqlMemoryGraph
from nora.infra.sql.schema import (
    COGNITION_WAKE,
    MEMORY_NODE,
    MEMORY_PROJECTION_QUEUE,
    MEMORY_WORK_ITEM,
    MESSAGES,
    SOCIAL_ITEM,
    SOCIAL_ITEM_STATE,
    SOCIAL_POLL,
    STIMULUS,
)
from nora.obs.metrics import Metrics
from nora.subject.sandbox import sandbox_as_of
from scripts import memory_graph_spike as spike

MODEL_ID = "synthetic-embedder.gguf@0000feedbeef"
MODEL = fingerprint(MODEL_ID)
UNIT = "generated-unit"
PERSON = "generated-person"
ROOM = Room("signal", "generated-room")
NET = "generated-net"
FILLER = 50


def _vector(i: int) -> list[float]:
    from nora.infra.sql.schema import EMBEDDING_DIMENSIONS

    vec = [0.0] * EMBEDDING_DIMENSIONS
    vec[i] = 1.0
    return vec


def _message(n: int, body: str) -> dict:
    return {
        "id": 1000 + n,
        "platform": "signal",
        "conversation_id": "generated-room",
        "conversation_type": "group",
        "sender_id": "generated-sender",
        "sender_name": "Member A",
        "person_id": "member-a",
        "ts": 1_700_000_000_000 + n,
        "body": body,
        "is_nora": 0,
    }


def _item(item_id: str, caption: str, read_id: str) -> dict:
    return {
        "network": NET,
        "item_id": item_id,
        "person": PERSON,
        "account": "generated-account",
        "kind": "post",
        "taken_at": datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
        "caption": caption,
        "seen_at": datetime(2026, 10, 29, 12, 5, tzinfo=UTC),
        "origin": "poll",
        "read_id": read_id,
    }


async def test_the_replay_preparation_indexes_the_restored_sandbox(pg_url, pg_template):
    engine = create_sql_engine(pg_url)
    try:
        read_id = uuid4().hex
        # Everything the instant held: the read and its items, the stimuli and
        # the wake that claims them, and the room's messages.
        async with engine.begin() as conn:
            await conn.execute(
                sa.insert(MESSAGES),
                [_message(n, f"Generated message {n}") for n in range(FILLER)]
                + [_message(FILLER, "Generated body at the instant")],
            )
            await conn.execute(sa.insert(SOCIAL_POLL).values(
                id=read_id, network=NET, person=PERSON, stage="items", outcome="read_ok"))
            await conn.execute(sa.insert(SOCIAL_ITEM), [
                _item("item-1", "Generated pre-instant caption", read_id),
                _item("item-2", "Generated hidden-after-the-instant caption", read_id),
            ])
            tick = await conn.scalar(sa.text("SELECT clock_timestamp()"))
            at_ms = int(tick.timestamp() * 1000)
            sid_msg = (await conn.execute(sa.insert(STIMULUS).values(
                unit=UNIT, kind="message", item="message", ref="messages:1000",
                platform="signal", conversation_id="generated-room",
                person_id="member-a", at_ms=at_ms - 1000,
            ).returning(STIMULUS.c.id))).scalar_one()
            sid_soc = (await conn.execute(sa.insert(STIMULUS).values(
                unit=UNIT, kind="social", item="post", ref=f"social_read:{read_id}",
                platform="signal", conversation_id="generated-room",
                person_id=PERSON, at_ms=at_ms - 1000,
            ).returning(STIMULUS.c.id))).scalar_one()
            wid = (await conn.execute(sa.insert(COGNITION_WAKE).values(
                unit=UNIT, mode="live", reason="settled", from_stimulus_id=0,
                through_stimulus_id=sid_soc, stimulus_count=2, eligible_count=2,
                at_ms=at_ms + 60_000,
            ).returning(COGNITION_WAKE.c.id))).scalar_one()
        # The restore instant, strictly after every row above and before every
        # later one: `recorded_at <= at` decides what the sandbox holds.
        async with engine.connect() as conn:
            at = await conn.scalar(sa.text("SELECT clock_timestamp()"))
        # The replay clock the script restores and runs at.
        replay_ms, view = await recorded_wake(engine, wid)
        # After the instant: an item is hidden, another arrives, a message is
        # edited. None of that may leak into the sandbox or its index.
        async with engine.begin() as conn:
            await conn.execute(sa.insert(SOCIAL_ITEM).values(
                _item("item-3", "Generated post-instant caption", uuid4().hex)))
            await conn.execute(sa.insert(SOCIAL_ITEM_STATE).values(
                network=NET, item_id="item-2", state="hidden"))
            await conn.execute(sa.update(MESSAGES).where(
                MESSAGES.c.id == 1000 + FILLER).values(body="Generated post-instant edit"))
        # The source's index is today's: built and embedded over the rows as
        # they stand NOW, as the deployment's clone holds them.
        await memory_index.rebuild(engine)
        async with engine.begin() as conn:
            for n, item in enumerate(
                await memory_index.embed_pick(conn, limit=1000, now_ms=0)
            ):
                assert await memory_index.embed_store(
                    conn, item, vector=_vector(n), model=MODEL, now_ms=0,
                    by_bytes=False,
                )
        source_acts = await spike.acts(engine)

        async with sandbox_as_of(pg_url, at, template=pg_template) as (url, _):
            sandbox = create_sql_engine(url)
            try:
                records = SqlComprehendRecords(
                    sandbox, media_join=None,
                    rooms_by_unit={UNIT: [("signal", "generated-room")]},
                    persons_by_unit={UNIT: {PERSON, "member-a"}},
                )
                def work(source):
                    return WorkItem(f"stimulus:{source}", source, UNIT, "", at)
                async with sandbox.connect() as conn:
                    assert await conn.scalar(
                        sa.select(sa.func.count()).select_from(MEMORY_NODE)) == 0
                # The smoke's ERROR, reproduced: the as-of rows are there, the
                # derived index is not, and nothing tolerates its absence here.
                with pytest.raises(NoResultFound):
                    await records.load(work(("social", f"{NET}:item-1")))
                before = await spike.acts(sandbox)
                subject = SimpleNamespace(
                    database_url=url,
                    cfg=SimpleNamespace(
                        memory=SimpleNamespace(
                            index=SimpleNamespace(embedder_model=MODEL_ID))),
                )
                built = await spike.index_replay_sandbox(subject, source_url=pg_url)
                assert built.source.ratio == 1.0
                # The two nodes a vector cannot be adopted for: one source no
                # longer holds (item-2), one whose input moved (the edit).
                assert built.coverage.owed == 2
                assert built.coverage.embedded == FILLER + 1

                async with sandbox.connect() as conn:
                    def node(kind, sid):
                        return conn.execute(sa.select(MEMORY_NODE).where(
                            MEMORY_NODE.c.kind == kind,
                            MEMORY_NODE.c.source_id == sid))
                    adopted = (await node("social", f"{NET}:item-1")).mappings().one()
                    assert adopted["embedding_model"] == MODEL
                    # As-of, not today: the edit and the hidden state came after.
                    edited = (await node("message", str(1000 + FILLER))).mappings().one()
                    assert edited["text"] == "Generated body at the instant"
                    assert edited["embedding_model"] is None
                    assert (await node("social", f"{NET}:item-2")).mappings().one()
                    assert await conn.scalar(
                        sa.select(sa.func.count()).select_from(MEMORY_WORK_ITEM)) == 0
                    assert await conn.scalar(
                        sa.select(sa.func.count()).select_from(
                            MEMORY_PROJECTION_QUEUE)) == 0
                # Today's state cannot leak: the item recorded after the instant
                # has no node here, exactly like production at the instant.
                with pytest.raises(NoResultFound):
                    await records.load(work(("social", f"{NET}:item-3")))

                # The real route inspect takes: WakeMemory.prepare through the
                # composed SqlComprehendRecords and SqlMemoryGraph.
                memory = WakeMemory(
                    graph=SqlMemoryGraph(sandbox, metrics=Metrics()),
                    records=records, metrics=Metrics())
                async def enrich(rows, **_):
                    return tuple(rows)
                records.media_join = SimpleNamespace(enrich=enrich)
                state = SimpleNamespace(
                    now_ms=replay_ms, unit=UNIT, session_id=1, historical=True,
                    items_by_index={
                        0: SimpleNamespace(stimulus_ids=(sid_msg,), room=ROOM),
                        1: SimpleNamespace(stimulus_ids=(sid_soc,), room=ROOM),
                    },
                    memory_items={}, graph_fetched=GraphFetched(),
                    admitted_indices=set(), admitted_relations={},
                    admitted_evidence=[])
                grounding = Grounding(records={
                    "messages:1000": RecordItem(
                        ref="messages:1000", kind="message", message_id=1000,
                        room=ROOM, author="Member A", at_ms=1_700_000_000_000,
                        text="Generated message 0")})
                await memory.prepare(
                    state, {s.id: s for s in view.pending}, grounding)
                keys = {s.key for s in state.memory_items[1].sources}
                assert ("social", f"{NET}:item-1") in keys
                assert ("social", f"{NET}:item-2") in keys
                assert ("message", "1000") in {
                    s.key for s in state.memory_items[0].sources}
                assert "Generated pre-instant caption" in state.graph_text

                # No durable acts and no work produced; the source is untouched.
                assert spike.check_acts(before, await spike.acts(sandbox)) == 0
            finally:
                await sandbox.dispose()
        assert spike.check_acts(source_acts, await spike.acts(engine)) == 0
    finally:
        await engine.dispose()
