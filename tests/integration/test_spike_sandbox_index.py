"""A replay sandbox's derived substrates are rebuilt from the restored instant."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import sqlalchemy as sa

from nora.infra.embedder import fingerprint
from nora.infra.sql.schema import (
    LEGACY_RELATION,
)

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

