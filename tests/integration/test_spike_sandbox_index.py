"""A replay sandbox's derived substrates are rebuilt from the restored instant."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import sqlalchemy as sa

from nora.infra.embedder import fingerprint
from nora.infra.sql import memory_index
from nora.infra.sql.cognitive_graph_schema import CLAIM, GRAPH_SEARCH
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.notes import SqlNoteStore
from nora.infra.sql.schema import (
    LEGACY_MEMORY_WORK_ITEM,
    LEGACY_RELATION,
    MEMORY_NODE,
    MEMORY_PROJECTION_QUEUE,
    MESSAGES,
    SOCIAL_ITEM,
    SOCIAL_ITEM_STATE,
    SOCIAL_POLL,
)
from nora.subject.sandbox import sandbox_as_of
from scripts import memory_graph_spike as spike

MODEL_ID = "synthetic-embedder.gguf@0000feedbeef"
MODEL = fingerprint(MODEL_ID)
UNIT = "generated-unit"
PERSON = "generated-person"
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
        "source_kind": "post",
        "taken_at": datetime(2026, 10, 29, 12, 0, tzinfo=UTC),
        "caption": caption,
        "seen_at": datetime(2026, 10, 29, 12, 5, tzinfo=UTC),
        "origin": "poll",
        "read_id": read_id,
    }


def _claim() -> dict:
    return {
        "id": "claim-before-instant", "unit": UNIT, "kind": "claim",
        "basis": "inferred", "reasoning": "deduced", "confidence": "medium",
        "state": "live", "exposure": "internal", "writer": "generated",
        "part": "fact", "content": {"text": "Generated graph text at the instant"},
    }


def _graph_context_subject(database_url: str, tmp_path) -> SimpleNamespace:
    from nora.config_reload import LivePersons, LivePolicy
    from nora.persons import load_persons
    from nora.policy import load_policy

    identity = tmp_path / "identity.toml"
    identity.write_text(
        '[persons."member-a"]\nname="Generated Member"\n'
        '[persons."generated-person"]\nname="Generated Person"\n'
    )
    policy = tmp_path / "policy.toml"
    policy.write_text(
        'default="deny"\n[[conversations]]\nplatform="signal"\n'
        'id="generated-room"\nlabel="Generated Room"\n'
        f'unit="{UNIT}"\npersons=["member-a","generated-person"]\n'
    )
    reload = SimpleNamespace(
        policy=LivePolicy(load_policy(policy)),
        persons=LivePersons(load_persons(identity)),
    )
    return SimpleNamespace(
        database_url=database_url,
        cfg=SimpleNamespace(
            doctrine=SimpleNamespace(display_name="Generated Nora"),
            memory=SimpleNamespace(
                graph=SimpleNamespace(enabled=True),
                index=SimpleNamespace(read=False, embed=False, embedder_model=MODEL_ID))),
        deps=SimpleNamespace(config_reload=reload),
    )


async def _projected_authorship_count(conn) -> int:
    return int((await conn.execute(
        sa.select(sa.func.count()).select_from(LEGACY_RELATION).where(
            LEGACY_RELATION.c.rel == "authored_by",
            LEGACY_RELATION.c.written_by.like("project:%"),
            LEGACY_RELATION.c.status == "current",
        ))).scalar_one())


async def test_the_replay_preparation_indexes_both_restored_substrates(
    pg_url, pg_template, tmp_path,
):
    engine = create_sql_engine(pg_url)
    try:
        read_id = uuid4().hex
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
        note = await SqlNoteStore(engine).create(
            unit=UNIT, title="generated", description="d", uncertainty="q",
            closing_condition="c", established_in="signal:generated-room",
            body="generated act body", established_by="nora", now_ms=1_760_000_000_000,
            anchor_date=20251101,
        )
        assert note.id  # positive control: the act fingerprint is non-empty
        await SqlCognitiveGraph(engine).create(CLAIM, _claim())

        async with engine.connect() as conn:
            at = await conn.scalar(sa.text("SELECT clock_timestamp()"))
        async with engine.begin() as conn:
            await conn.execute(sa.insert(SOCIAL_ITEM).values(
                _item("item-3", "Generated post-instant caption", uuid4().hex)))
            await conn.execute(sa.insert(SOCIAL_ITEM_STATE).values(
                network=NET, item_id="item-2", source_state="hidden"))
            await conn.execute(sa.update(MESSAGES).where(
                MESSAGES.c.id == 1000 + FILLER).values(body="Generated post-instant edit"))

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
                async with sandbox.connect() as conn:
                    assert await conn.scalar(
                        sa.select(sa.func.count()).select_from(MEMORY_NODE)) == 0
                    assert await conn.scalar(
                        sa.select(sa.func.count()).select_from(GRAPH_SEARCH)) == 0
                before = await spike.acts(sandbox)
                subject = _graph_context_subject(url, tmp_path)
                built = await spike.index_replay_sandbox(subject, source_url=pg_url)

                async with sandbox.connect() as conn:
                    assert await _projected_authorship_count(conn) > 0
                    assert await conn.scalar(sa.select(sa.func.count()).select_from(
                        MEMORY_NODE).where(MEMORY_NODE.c.kind == "social")) == 2

                    def node(kind, sid):
                        return conn.execute(sa.select(MEMORY_NODE).where(
                            MEMORY_NODE.c.kind == kind,
                            MEMORY_NODE.c.source_id == sid))

                    adopted = (await node("social", f"{NET}:item-1")).mappings().one()
                    assert adopted["embedding_model"] == MODEL
                    assert "Generated pre-instant caption" in adopted["text"]
                    edited = (await node("message", str(1000 + FILLER))).mappings().one()
                    assert edited["text"] == "Generated body at the instant"
                    assert edited["embedding_model"] is None
                    assert (await node("social", f"{NET}:item-2")).mappings().one()
                    assert await conn.scalar(
                        sa.select(sa.func.count()).select_from(LEGACY_MEMORY_WORK_ITEM)) == 0
                    assert await conn.scalar(
                        sa.select(sa.func.count()).select_from(
                            MEMORY_PROJECTION_QUEUE)) == 0

                assert (built.source.ratio, built.coverage.owed) == (0.0, 1)
                assert built.coverage.embedded == 0
                async with sandbox.connect() as conn:
                    graph_text = await conn.scalar(sa.select(GRAPH_SEARCH.c.text).where(
                        GRAPH_SEARCH.c.graph_id == "claim-before-instant",
                        GRAPH_SEARCH.c.unit == UNIT,
                    ))
                assert graph_text == "Generated graph text at the instant"

                # Today's source state cannot leak into the sandbox.
                async with sandbox.connect() as conn:
                    assert await conn.scalar(sa.select(sa.func.count()).select_from(
                        MEMORY_NODE).where(
                        MEMORY_NODE.c.kind == "social",
                        MEMORY_NODE.c.source_id == f"{NET}:item-3",
                    )) == 0

                assert spike.check_acts(before, await spike.acts(sandbox)) == 0
            finally:
                await sandbox.dispose()
        assert spike.check_acts(source_acts, await spike.acts(engine)) == 0
    finally:
        await engine.dispose()
