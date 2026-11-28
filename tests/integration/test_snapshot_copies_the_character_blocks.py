"""A restore copies the account claims the profiles render.

The unit's own account, Nora's own, and the account of each person the room
LISTS are profile presentations of graph claims (`graph_profile`,
design/cognition/context.md's migration table — `unit_culture`, `self` and
`character` live there now). A restore (`sandbox_as_of`, the graph's as-of
mechanism) brings them over, each as of the instant and at the age it had.

A restore that copied only the room's rows would give an eval turn none of
them: the case would grade a prompt missing the very thing an account change
is about. The source also holds what must not be rendered: a claim asserted
after the instant, a person the room does not list, and the same person's
account in another unit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa

from nora.domain.culture import character_key, nora_character_key, unit_culture_key
from nora.domain.models import NormalizedMessage
from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, ENTITY
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.culture import SqlCultureStore
from nora.subject import (
    Knowledge,
    Message,
    SubjectSpec,
    sandbox_as_of,
)
from nora.subject.as_of import render_tag_blocks
from tests.sandbox_fixtures import ROOM, Source, offline_subject, seed_source

BEFORE_MS = 1_752_000_000_000
NOW = datetime.fromtimestamp(BEFORE_MS / 1000, tz=UTC)
MINUTE = 60_000
HOUR = 3_600_000
UNIT = "nora"
POLICY = (
    'default = "deny"\n[[conversations]]\nplatform = "signal"\n'
    f'id = "g1"\nlabel = "g1"\npersons = ["miles_novak"]\nunit = "{UNIT}"\n'
)
IDENTITY = (
    '[persons.miles_novak]\nname = "Miles"\n'
    '[[persons.miles_novak.signal]]\nphone = "+852111"\n'
    '[persons.bo]\nname = "Bo"\n'
    '[[persons.bo.signal]]\nphone = "+852222"\n'
)

NORA_ENTITY = "nora:nora:nora"
UNIT_ENTITY = "nora:unit:nora"
MILES_ENTITY = "nora:identity:miles_novak"


async def _graph_accounts(engine: Any, item_id: str) -> None:
    """The unit's, Nora's and the listed person's accounts as graph claims —
    what `graph_profile` renders of each subject — plus what must not render:
    claims asserted after the instant, the unlisted person's account, and the
    same person's account in another unit.
    """
    graph = SqlCognitiveGraph(engine)
    old = datetime.fromtimestamp((BEFORE_MS - 30 * 24 * HOUR) / 1000, tz=UTC)

    async def entity(eid: str, unit: str, name: str, kind: str, ref: str) -> None:
        await graph.create(ENTITY, {
            "id": eid, "kind": "entity", "basis": "computed", "state": "admitted", "unit": unit,
            "name": name, "pointer_kind": kind, "pointer_ref": ref, "identities": [],
            "exposure": "internal", "writer": "test", "asserted_at": old,
        })

    async def edge(eid: str, kind: str, src: str, dst: str, *, unit: str,
                   at_ms: int) -> None:
        await graph.create(EDGE, {
            "id": eid, "kind": "edge", "basis": "inferred", "state": "live", "unit": unit,
            "type": kind, "src_id": src, "dst_id": dst, "reasoning": "deduced",
            "confidence": "high", "exposure": "internal", "writer": "test",
            "asserted_at": datetime.fromtimestamp(at_ms / 1000, tz=UTC),
        })

    async def claim(cid: str, text: str, *, unit: str, at_ms: int,
                    about: str, cite: str | None = None) -> None:
        await graph.create(CLAIM, {
            "id": cid, "kind": "claim", "basis": "inferred", "state": "live", "unit": unit,
            "part": "fact", "content": {"text": text}, "reasoning": "deduced",
            "confidence": "high", "exposure": "internal", "writer": "test",
            "origin_room": "g1",
            "asserted_at": datetime.fromtimestamp(at_ms / 1000, tz=UTC),
        })
        await edge(f"edge:{cid}:about", "about", cid, about, unit=unit,
                   at_ms=at_ms)
        if cite is not None:
            await edge(f"edge:{cid}:cites", "cites", cid, cite, unit=unit,
                       at_ms=at_ms)

    for eid, unit, name, kind, ref in (
        (NORA_ENTITY, "nora", "Nora", "nora", "nora"),
        (UNIT_ENTITY, "nora", "nora", "unit", "nora"),
        ("nora:room:signal:g1", "nora", "g1", "room", "signal:g1"),
        (MILES_ENTITY, "nora", "Miles", "identity", "miles_novak"),
        ("nora:identity:bo", "nora", "Bo", "identity", "bo"),
        ("elsewhere:nora:nora", "elsewhere", "Nora", "nora", "nora"),
        ("elsewhere:identity:miles_novak", "elsewhere", "Miles", "identity",
         "miles_novak"),
    ):
        await entity(eid, unit, name, kind, ref)

    # Each subject's account as of T: the unit's claim cites the tag item so
    # the called pass reaches it (a pointer's neighbours travel `about` only);
    # Nora's and Miles's claims sit `about` anchored pointers.
    await claim("claim:unit:account", "a close group of cyclists", unit="nora",
                at_ms=BEFORE_MS - 5 * HOUR, about=UNIT_ENTITY, cite=item_id)
    await claim("claim:nora:account", "she answers short and dry", unit="nora",
                at_ms=BEFORE_MS - 3 * HOUR, about=NORA_ENTITY)
    await claim("claim:miles:account", "Miles plans, then waits", unit="nora",
                at_ms=BEFORE_MS - 2 * HOUR, about=MILES_ENTITY)
    # Asserted after the instant: reached but outside the read's window.
    await claim("claim:unit:after", "AFTER: the unit changed", unit="nora",
                at_ms=BEFORE_MS + HOUR, about=UNIT_ENTITY, cite=item_id)
    await claim("claim:nora:after", "AFTER: she changed", unit="nora",
                at_ms=BEFORE_MS + HOUR, about=NORA_ENTITY)
    await claim("claim:miles:after", "AFTER: Miles stopped", unit="nora",
                at_ms=BEFORE_MS + HOUR, about=MILES_ENTITY)
    # The room does not list Bo: his pointer is never an anchor.
    await claim("claim:bo:account", "UNLISTED: Bo is loud", unit="nora",
                at_ms=BEFORE_MS - HOUR, about="nora:identity:bo")
    # Other-unit accounts are outside the read's unit scope.
    await claim("claim:other:nora", "OTHER UNIT: her", unit="elsewhere",
                at_ms=BEFORE_MS - HOUR, about="elsewhere:nora:nora")
    await claim("claim:other:miles", "OTHER UNIT: Miles", unit="elsewhere",
                at_ms=BEFORE_MS - HOUR, about="elsewhere:identity:miles_novak")


async def _clock(engine: Any) -> datetime:
    async with engine.connect() as conn:
        return (await conn.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()


@pytest.fixture
async def source(clean_pg_url: str, pg_template: str, tmp_path: Any, monkeypatch: Any) -> Source:
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    src = await seed_source(
        clean_pg_url, tmp_path, Knowledge(transcript=(
            Message(body="anyone up for friday", at_ms=BEFORE_MS - 10 * MINUTE),
            Message(body="yes, if it stays dry", at_ms=BEFORE_MS - 5 * MINUTE),
        )),
        template=pg_template, before_ms=BEFORE_MS, policy=POLICY, identity=IDENTITY,
    )
    engine = create_sql_engine(clean_pg_url)
    try:
        store = SqlCultureStore(engine)

        async def write(key: str, body: str, at: int) -> None:
            await store.append(key=key, body=body, written_at=at, established_by="test")

        await write(unit_culture_key(UNIT), "a close group of cyclists", BEFORE_MS - 5 * HOUR)
        await write(unit_culture_key(UNIT), "AFTER: the unit changed", BEFORE_MS + HOUR)
        await write(nora_character_key(UNIT), "she answers short and dry", BEFORE_MS - 3 * HOUR)
        await write(nora_character_key(UNIT), "AFTER: she changed", BEFORE_MS + HOUR)
        await write(nora_character_key("elsewhere"), "OTHER UNIT: her", BEFORE_MS - HOUR)
        await write(
            character_key(UNIT, "miles_novak"), "Miles plans, then waits", BEFORE_MS - 2 * HOUR,
        )
        await write(character_key(UNIT, "miles_novak"), "AFTER: Miles stopped", BEFORE_MS + HOUR)
        # The room does not list Bo, and Miles's account in another unit is not
        # this unit's: neither may reach the sandbox.
        await write(character_key(UNIT, "bo"), "UNLISTED: Bo is loud", BEFORE_MS - HOUR)
        await write(character_key(
            "elsewhere", "miles_novak",
        ), "OTHER UNIT: Miles", BEFORE_MS - HOUR)
    finally:
        await engine.dispose()
    return src


async def test_an_eval_turn_carries_the_unit_and_person_accounts_as_of_then(
    clean_pg_url: str, pg_template: str, source: Source, tmp_path: Any,
) -> None:
    # The tag lands on the source first — from Miles, so his pointer is one of
    # the pass's person anchors — and the account claims cite or sit about it
    # before the instant the restore is taken at.
    tag = NormalizedMessage(
        platform=ROOM[0], conversation_id=ROOM[1], conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=BEFORE_MS,
        body="@nora what is the plan", raw={},
    )
    engine = create_sql_engine(clean_pg_url)
    try:
        async with offline_subject(SubjectSpec(
            database_url=clean_pg_url, workdir=tmp_path / "live",
            room=source.room, policy=source.policy, identity=source.identity,
            now=NOW,
        )) as live:
            await live.deps.audit.append(
                message=tag, person_id="miles_novak")
        async with engine.connect() as conn:
            item_id = (await conn.execute(sa.text(
                "SELECT graph_id FROM messages WHERE conversation_id = :c "
                "AND body = :b"
            ), {"c": ROOM[1], "b": "@nora what is the plan"})).scalar_one()
        await _graph_accounts(engine, item_id)
        at = await _clock(engine)
    finally:
        await engine.dispose()

    async with (
        sandbox_as_of(source.url, at, template=pg_template) as (url, _report),
        offline_subject(SubjectSpec(
            database_url=url, workdir=tmp_path / "restored", room=source.room,
            policy=source.policy, identity=source.identity, now=NOW,
        )) as restored,
    ):
        blocks = await render_tag_blocks(restored, message=tag, now=NOW)

    assert "a close group of cyclists" in blocks[
        "graph_profile of=" + UNIT_ENTITY]
    assert "asserted=5h ago" in blocks[
        "graph_profile of=" + UNIT_ENTITY], blocks
    assert "she answers short and dry" in blocks[
        "graph_profile of=" + NORA_ENTITY]
    assert "asserted=3h ago" in blocks[
        "graph_profile of=" + NORA_ENTITY], blocks
    assert "Miles plans, then waits" in blocks[
        "graph_profile of=" + MILES_ENTITY]
    assert "asserted=2h ago" in blocks[
        "graph_profile of=" + MILES_ENTITY], blocks
    rendered = "\n".join(blocks.values())
    for absent in ("AFTER:", "UNLISTED:", "OTHER UNIT:"):
        assert absent not in rendered, absent
