"""Read the SQL scope through the area's ordinary retrieval service."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import NAMESPACE_DNS, uuid5

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.access import IdentityAccess
from nora.agent.lineage import LineagePolicy
from nora.domain.lineage import ROW, Lineage, LineageEntry, LiveRoom, message
from nora.domain.models import PersonRef
from nora.infra.sql.cognitive_graph_schema import CLAIM, EDGE, ENTITY, REGISTRY
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import MESSAGE_EDIT, MESSAGES, SOCIAL_ITEM
from nora.memory.app.retrieval import RetrievalService
from nora.memory.domain.retrieval import ReadingRoom, RetrievalAnchors, RetrievalBounds
from nora.memory.wire import retrieval
from nora.persons import Persons
from nora.policy import Conversation, Policy
from nora.testing.memory_index import FakeQueryEmbedder

UNIT = "retrieval-unit"
ROOM = "retrieval-room"
OTHER_ROOM = "retrieval-other"
PERSON = "fixture-retrieval-member"
OTHER = "fixture-other-member"
DM = str(uuid5(NAMESPACE_DNS, "retrieval.direct.fixture"))
AT = datetime(2026, 11, 1, 12, tzinfo=UTC)
BOUNDS = RetrievalBounds(2, 8, 30, datetime(2026, 1, 1, tzinfo=UTC),
                         datetime(2099, 1, 1, tzinfo=UTC))
READ = ReadingRoom("signal", ROOM, LiveRoom(frozenset({PERSON})))


@dataclass
class Scenario:
    engine: AsyncEngine
    persons: Persons
    policy: Policy

    @property
    def graph(self) -> SqlCognitiveGraph:
        return SqlCognitiveGraph(self.engine)

    def service(
        self, *, access: IdentityAccess | None = None, fail: Exception | None = None,
        model: str | None = None,
    ) -> RetrievalService:
        chosen = access or IdentityAccess(file_persons=self.persons, file_policy=self.policy)
        lineage = LineagePolicy.from_policy(self.policy, access=chosen)
        return retrieval(self.engine, chosen, lineage, FakeQueryEmbedder(fail=fail), model)

    async def item(
        self, graph_id: str, body: str = "Generated observation", *,
        unit: str = UNIT, room: str = ROOM, dm: bool = False,
        person: str = PERSON, at: datetime = AT, **fields,
    ) -> None:
        token = str(uuid5(NAMESPACE_DNS, graph_id))
        async with self.engine.begin() as conn:
            await conn.execute(sa.insert(REGISTRY).values(id=graph_id, unit=unit, kind="item"))
            await conn.execute(sa.insert(MESSAGES).values(
                graph_id=graph_id, unit=unit, platform="signal", conversation_id=room,
                conversation_type="dm" if dm else "group", origin_room=room,
                sender_id=token, ts=int(at.timestamp() * 1000), occurred_at=at,
                asserted_at=at, valid_from=at, person_id=person, persons=[person], body=body,
                **fields,
            ))

    async def social(self, graph_id: str, person: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(sa.insert(REGISTRY).values(id=graph_id, unit=UNIT, kind="item"))
            await conn.execute(sa.insert(SOCIAL_ITEM).values(
                graph_id=graph_id, unit=UNIT, network="instagram", item_id=graph_id,
                account="fixture-social", source_kind="post", person=person, persons=[person],
                origin="poll", taken_at=AT, seen_at=AT, occurred_at=AT,
                asserted_at=AT, caption="Generated social item",
            ))

    async def claim(self, graph_id: str, **fields) -> None:
        await self.graph.create(CLAIM, claim_values(graph_id, **fields))

    async def entity(self, graph_id: str, **fields) -> None:
        await self.graph.create(ENTITY, {
            "id": graph_id, "unit": UNIT, "kind": "entity", "basis": "computed",
            "state": "admitted", "exposure": "internal", "writer": "fixture",
            "asserted_at": AT, "name": graph_id, **fields,
        })

    async def now(self) -> datetime:
        async with self.engine.connect() as conn:
            return (await conn.execute(sa.select(sa.func.clock_timestamp()))).scalar_one()


def claim_values(graph_id: str, **fields) -> dict:
    return {
        "id": graph_id, "unit": UNIT, "kind": "claim", "basis": "inferred", "part": "fact",
        "reasoning": "deduced", "confidence": "high", "state": "live",
        "exposure": "internal", "writer": "fixture", "origin_room": ROOM, "asserted_at": AT,
        "content": {"text": graph_id}, **fields,
    }


@pytest.fixture
async def scenario(clean_pg_url: str) -> AsyncIterator[Scenario]:
    engine = create_sql_engine(clean_pg_url)
    people = {
        PERSON: PersonRef(PERSON, "Fixture reader"),
        OTHER: PersonRef(OTHER, "Fixture other reader"),
    }
    rooms = (
        Conversation("signal", frozenset({ROOM}), frozenset({PERSON}), primary_id=ROOM, unit=UNIT),
        Conversation("signal", frozenset({OTHER_ROOM}), frozenset({OTHER}), primary_id=OTHER_ROOM,
                     unit=UNIT),
        Conversation("signal", frozenset({DM}), frozenset({OTHER}), primary_id=DM, unit=UNIT),
    )
    policy = Policy(rooms, _by_id={("signal", room.primary_id): room for room in rooms})
    try:
        yield Scenario(engine, Persons(people, {("signal", DM): OTHER}), policy)
    finally:
        await engine.dispose()


async def write_edge(scenario: Scenario, source: str, target: str, kind: str) -> None:
    await scenario.graph.create(EDGE, {
        "id": f"edge:{kind}:{source}:{target}", "unit": UNIT, "kind": "edge",
        "basis": "computed", "state": "live", "exposure": "internal", "writer": "fixture",
        "type": kind, "src_id": source, "dst_id": target, "asserted_at": AT,
    })


def references(*keys: str) -> RetrievalAnchors:
    return RetrievalAnchors(graph_ids=keys)


async def test_other_unit_is_excluded(scenario) -> None:
    await scenario.item("in-unit")
    await scenario.item("other-unit", unit="affiliate")
    result = await scenario.service().retrieve(READ, references("other-unit", "in-unit"), BOUNDS)
    assert [row.graph_id for row in result.rows] == ["in-unit"]
    assert result.status == "ok"


async def test_operator_exposure_is_excluded(scenario) -> None:
    await scenario.item("visible-item", exposure="internal")
    await scenario.item("operator-item", exposure="operator")
    result = await scenario.service().retrieve(
        READ, references("operator-item", "visible-item"), BOUNDS,
    )
    assert [row.graph_id for row in result.rows] == ["visible-item"]
    assert result.status == "ok"


async def test_excluded_persons_apply_to_room_members_and_pass_subjects(scenario) -> None:
    await scenario.item("member-excluded", excluded_persons=[PERSON])
    await scenario.item("subject-excluded", excluded_persons=[OTHER])
    await scenario.item("audience-visible")
    anchors = references("member-excluded", "subject-excluded", "audience-visible")
    svc = scenario.service()
    room = await svc.retrieve(READ, anchors, BOUNDS)
    assert [row.graph_id for row in room.rows] == ["subject-excluded", "audience-visible"]
    pass_scope = await svc.retrieve(READ, anchors, BOUNDS, concerns=frozenset({OTHER}))
    assert [row.graph_id for row in pass_scope.rows] == ["audience-visible"]


async def test_dm_raw_item_is_visible_only_in_its_dm(scenario) -> None:
    await scenario.item("dm-evidence", room=DM, dm=True, person=OTHER)
    svc = scenario.service()
    denied = await svc.retrieve(READ, references("dm-evidence"), BOUNDS)
    assert denied.status == "nothing_found"
    allowed = await svc.retrieve(ReadingRoom("signal", DM), references("dm-evidence"), BOUNDS)
    assert [row.graph_id for row in allowed.rows] == ["dm-evidence"]


async def test_dm_claim_crosses_rooms_with_its_origin_and_without_the_raw_item(scenario) -> None:
    await scenario.item("dm-evidence", room=DM, dm=True, person=OTHER)
    await scenario.claim(
        "dm-claim", origin_room=f"signal:{DM}",
        lineage=Lineage.of({ROW: (message(f"signal:{DM}"),)}).to_json(),
    )
    await write_edge(scenario, "dm-claim", "dm-evidence", "cites")
    result = await scenario.service().retrieve(
        READ, references("dm-claim", "dm-evidence"), BOUNDS,
    )
    assert [row.graph_id for row in result.rows] == ["dm-claim"]
    assert result.rows[0].origin_room == f"signal:{DM}"


async def test_social_opt_out_applies_inside_sql_scope(scenario) -> None:
    await scenario.social("opted-out", OTHER)
    await scenario.social("opted-in", PERSON)
    scenario.persons = replace(scenario.persons, _social_off=frozenset({OTHER}))
    result = await scenario.service().retrieve(READ, references("opted-out", "opted-in"), BOUNDS)
    assert [row.graph_id for row in result.rows] == ["opted-in"]


async def test_lineage_follows_existing_room_and_nested_entry_permits(scenario) -> None:
    good = Lineage.of({ROW: (message(f"signal:{ROOM}"),)}).to_json()
    denied = Lineage.of({ROW: (
        LineageEntry("perception", reads=(message(f"signal:{OTHER_ROOM}"),)),
    )}).to_json()
    await scenario.claim("same-room-lineage", lineage=good)
    await scenario.claim("other-room-lineage", lineage=denied)
    result = await scenario.service().retrieve(
        READ, references("other-room-lineage", "same-room-lineage"), BOUNDS,
    )
    assert [row.graph_id for row in result.rows] == ["same-room-lineage"]


class _UnavailableRoomMembership(IdentityAccess):
    @property
    def is_file_backend(self) -> bool:
        return False

    async def lineage_snapshot(self, scopes):
        raise OSError("fixture access view unavailable")


async def test_unavailable_access_view_cannot_substitute_an_empty_audience(scenario) -> None:
    await scenario.item("known-item")
    anchors = references("known-item")
    healthy = await scenario.service().retrieve(READ, anchors, BOUNDS)
    assert [row.graph_id for row in healthy.rows] == ["known-item"]
    denied = scenario.service(access=_UnavailableRoomMembership(
        file_persons=scenario.persons, file_policy=scenario.policy,
    ))
    result = await denied.retrieve(READ, anchors, BOUNDS)
    assert (result.status, result.cause) == ("unavailable", "access_view_unavailable")
    assert result.rows == ()


async def test_scope_precedes_the_lexical_candidate_bound(scenario) -> None:
    term = "Lantern celebration"
    for i in range(50):
        await scenario.item(f"out-of-scope-{i}", term, excluded_persons=[PERSON])
    keep = [f"inside-{i}" for i in range(5)]
    for i, key in enumerate(keep):
        await scenario.item(key, f"{term} fixture extra context {i}")
    bounds = RetrievalBounds(1, 3, 5, BOUNDS.since, BOUNDS.until)
    result = await scenario.service().retrieve(READ, RetrievalAnchors(query=term), bounds)
    assert result.status == "ok"
    assert {row.graph_id for row in result.rows} == set(keep)


async def test_portal_keeps_the_own_claim_but_never_shows_dm_evidence_to_a_nonmember(scenario):
    from nora.agent.egress import scan_reply
    from nora.memory import wire
    from nora.web.memory import build_memory_payload

    origin = f"signal:{DM}"
    await scenario.item("generated-private-item", "generated earlier private DM body",
                        room=DM, dm=True, person=OTHER, exposure="external")
    await scenario.claim("own-derived-claim", persons=[PERSON], origin_room=origin,
                         lineage=Lineage.of({ROW: (message(origin),)}).to_json())
    await scenario.claim("foreign-subject", persons=[OTHER], origin_room=origin)
    await scenario.claim("withdrawn-own", persons=[PERSON], state="hidden", origin_room=origin)
    await write_edge(scenario, "own-derived-claim", "generated-private-item", "cites")
    async with scenario.engine.begin() as conn:
        message_id = (await conn.execute(sa.select(MESSAGES.c.id).where(
            MESSAGES.c.graph_id == "generated-private-item",
        ))).scalar_one()
        await conn.execute(sa.insert(MESSAGE_EDIT).values(
            message_id=message_id, ts=int(AT.timestamp() * 1000) + 1,
            body="generated private DM body",
        ))
    common = {
        "persons": scenario.persons, "person_id": PERSON, "screen": scan_reply,
        "profile": wire.subject_claims(scenario.engine).read,
        "profile_scopes_of": lambda _: ((UNIT, "generated-workspace"),),
    }
    [other_room] = await build_memory_payload(
        **common, rooms_of=lambda _: frozenset({("discord", DM), ("signal", ROOM)}),
    )
    [line] = other_room["lines"]
    assert (line["id"], line["text"], line["origin"]) == (
        "own-derived-claim", "own-derived-claim", "direct message",
    )
    assert line["evidence"] == [{"ref": "generated-private-item", "kind": "unknown"}]
    [same_room] = await build_memory_payload(
        **common, rooms_of=lambda _: frozenset({("signal", DM)}),
    )
    assert same_room["lines"][0]["origin"] == origin
    [evidence] = same_room["lines"][0]["evidence"]
    assert evidence["text"] == "generated private DM body"
    assert evidence["by"] == scenario.persons.by_person[OTHER].name


async def test_portal_guess_verdict_uses_only_a_visible_grade_for_the_same_subject(scenario):
    from nora.memory import wire

    for guess in ("own-graded-guess", "other-graded-guess"):
        await scenario.claim(guess, part="guess", persons=[PERSON], prediction="Observed later",
                             window_end=AT)
    await scenario.claim("own-grade", part="grade", persons=[PERSON], grade="confirmed")
    await scenario.claim("other-grade", part="grade", persons=[OTHER], grade="refuted")
    await write_edge(scenario, "own-grade", "own-graded-guess", "grades")
    await write_edge(scenario, "other-grade", "other-graded-guess", "grades")

    claims = await wire.subject_claims(scenario.engine).read(
        unit=UNIT, viewer=PERSON, viewer_rooms=frozenset({("signal", ROOM)}),
    )
    assert {claim["id"]: claim["grade"] for claim in claims} == {
        "own-graded-guess": "confirmed", "other-graded-guess": None, "own-grade": "confirmed",
    }


async def test_profile_snapshot_keeps_visible_graph_heads_in_the_requested_room(scenario):
    from nora.memory import wire

    origin = f"signal:{ROOM}"
    await scenario.claim("profile-kept", exposure="external", origin_room=origin)
    await scenario.claim("profile-replaced", exposure="external", origin_room=origin,
                         state="superseded")
    await scenario.claim("profile-internal", exposure="internal", origin_room=origin)
    await scenario.claim("profile-other-room", exposure="external",
                         origin_room=f"signal:{OTHER_ROOM}")
    await scenario.claim("profile-excluded", exposure="external", origin_room=origin,
                         excluded_persons=[PERSON])
    async with scenario.engine.connect() as conn:
        [head] = await wire.profile_snapshot(conn, unit=UNIT, rooms=(origin,))
        assert head["id"] == "profile-kept"
        assert set(head) == set(wire.PROFILE_METADATA)
