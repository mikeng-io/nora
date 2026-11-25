from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.fetched_page import FetchedPage
from nora.domain.graph_registration import identities_column
from nora.domain.models import NormalizedMessage, PersonRef
from nora.domain.observations import Observation, PollPayload, SourceScope
from nora.domain.presence import PresenceFix, WireState
from nora.domain.social_stream import ItemCounts, SocialItem
from nora.domain.tool_call_log import ToolCallLogEntry
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import EDGE, ENTITY, GRAPH_STIMULUS, INTENTION, REGISTRY
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.computer_events import SqlComputerEventStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.fetched_pages import SqlFetchedPageStore
from nora.infra.sql.item_registration import RegistrationScope, SqlItemRegistration
from nora.infra.sql.migrations_runner import sync_url_for
from nora.infra.sql.observations import SqlObservationStore
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.schema import METADATA
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.infra.sql.source_health import SqlSourceHealthLog
from nora.infra.sql.tool_call_log import SqlToolCallLogStore
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.adapters.watch_results import SqlWatchResults
from nora.memory.app.act_scope import act_pass
from nora.memory.app.acts import ActEnvelope
from nora.memory.domain.acts import Act, ActAuthority, ActIdentity, Outcome
from nora.memory.domain.admission_proposals import PassContext
from nora.memory.domain.registration import AGGREGATE_FAMILIES, BACKFILL_ITEM_SOURCES, ITEM_SOURCES
from nora.memory.wire import memory_intake
from nora.persons import Life360Membership, Persons
from nora.policy import Conversation, Policy
from tests.contract.test_computer_event_store_contract import _event
from tests.infra.test_cognitive_graph_schema import PLANES


@pytest.fixture
def graph_schema(clean_pg_url: str) -> Iterator[sa.Inspector]:
    engine = sa.create_engine(sync_url_for(clean_pg_url))
    try:
        with engine.connect() as connection:
            yield sa.inspect(connection)
    finally:
        engine.dispose()


@pytest.mark.parametrize("name", (*ITEM_SOURCES, *AGGREGATE_FAMILIES))
def test_every_item_and_aggregate_carries_the_planes(
    graph_schema: sa.Inspector, name: str,
) -> None:
    entity_fields = {field["name"]: field["type"] for field in graph_schema.get_columns("entity")}
    expected = {name: str(entity_fields[name]) for name in PLANES - {"id"}}
    expected[identities_column(name)] = expected.pop("identities")
    expected["graph_id"] = str(entity_fields["id"])
    actual = {field["name"]: field["type"] for field in graph_schema.get_columns(name)}
    assert {field: str(actual[field]) for field in actual.keys() & expected.keys()} == expected
    assert {"source_ref_kind", "source_ref_value"}.isdisjoint(actual)

    assert any(
        fk["constrained_columns"] == ["graph_id", "unit", "kind"]
        and fk["referred_table"] == "registry"
        and fk["referred_columns"] == ["id", "unit", "kind"]
        for fk in graph_schema.get_foreign_keys(name)
    )


_ROOM = "test-room"
_UNIT = "test-unit"
_SECOND = "test-unit-2"
_PERSON = "test-person"
_AUTHOR = "test-source-user"
_CIRCLE = "test-circle"
_MS = 1_795_000_000_127
_AT = datetime.fromtimestamp(_MS / 1000, tz=UTC)
_REVISIONS = {f"rev_{key}": f"test-{key}" for key in ("code", "config", "policy", "identity")}
_ROOM_SOURCES = frozenset({
    "messages", "message_edit", "message_reaction", "message_delete",
    "fetched_pages", "platform_observation",
})
_FANOUT_SOURCES = tuple(source for source in BACKFILL_ITEM_SOURCES if source not in _ROOM_SOURCES)
_NON_PASS_SOURCES = (*BACKFILL_ITEM_SOURCES, "watch_result", "forget_request", "dispute")
_FORWARD_SOURCES = tuple(source for source in ITEM_SOURCES if source not in _NON_PASS_SOURCES)
_PASS = PassContext(
    _UNIT, _ROOM, False, "called", "engagement", "test-pass", "test-trace", "test-model",
    rev_instruction="test-instruction", rev_skill="test-skill", **_REVISIONS,
)


class _FixedRevisions:
    def current(self) -> dict[str, str]:
        return _REVISIONS.copy()


def _registration(*, multiple: bool = False) -> SqlItemRegistration:
    owner = PersonRef(_PERSON, "Generated")
    people = Persons(
        {_PERSON: owner}, {("signal", _AUTHOR): _PERSON},
        life360=(Life360Membership(_PERSON, "test-member", _CIRCLE),),
    )
    primary = Conversation("signal", frozenset({_ROOM}), frozenset({_PERSON}), unit=_UNIT)
    rooms = (primary,)
    if multiple:
        rooms += (Conversation(
            "signal", frozenset({"test-room-2"}), frozenset({_PERSON}), unit=_SECOND,
        ),)
    policy = Policy(
        rooms, _by_id={(c.platform, key): c for c in rooms for key in c.ids},
    )
    return SqlItemRegistration(RegistrationScope(policy, people), _FixedRevisions())


@pytest.fixture
async def engine(clean_pg_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_sql_engine(clean_pg_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@dataclass
class _Writer:
    engine: AsyncEngine
    registration: SqlItemRegistration
    room: str = _ROOM

    def _message(self) -> NormalizedMessage:
        return NormalizedMessage(
            "signal", self.room, "group", _AUTHOR, None, _MS, "Generated message", {},
        )

    async def _message_event(self, table: str) -> None:
        store = SqlRecordStore(self.engine, registration=self.registration)
        message = self._message()
        row_id = await store.append(message=message, person_id=_PERSON)
        if table == "message_edit":
            await store.append_edit(message_id=row_id, message=message)
        elif table == "message_reaction":
            await store.append_reaction(message_id=row_id, message=message, person_id=_PERSON)
        elif table == "message_delete":
            await store.append_delete(message_id=row_id, message=message, person_id=_PERSON)

    async def _social(self, table: str) -> None:
        store = SqlSocialStreamStore(self.engine, registration=self.registration)
        item = SocialItem(
            network="instagram", item_id="test-post", account="test-account", kind="post",
            taken_at=_AT, expires_at=None, caption=None, alt_text=None, media=(),
            counts=ItemCounts(likes=2),
        )
        if table == "social_poll":
            await store.record_poll(
                network=item.network, person=_PERSON, stage="probe", outcome="unchanged",
            )
            return
        await store.insert_item(item, person=_PERSON, origin="poll", seen_at=_AT)
        if table == "social_item_state":
            await store.record_state(network=item.network, item_id=item.item_id, state="hidden")
        elif table == "social_item_count":
            await store.record_counts(network=item.network, items=[item], read_id=None)

    async def _executor_evidence(self, table: str) -> None:
        authority = ActAuthority(_UNIT, _PERSON, room=_ROOM, pass_context=_PASS)
        if table in {"nora_act", "nora_act_outcome", "nora_pass_output"}:
            store = SqlActStore(self.engine, self.registration)
            if table == "nora_pass_output":
                await ActEnvelope(store).record_expression(authority, [
                    {"role": "assistant", "content": [{"text": "Generated draft"}]},
                ])
                return
            act = Act(ActIdentity("test-executor", _PASS.pass_id, 0),
                      "delivery", "test-destination", authority, "unknown")
            assert await store.reserve(act)
            if table == "nora_act_outcome":
                await store.observe(act, Outcome("done", {"status": "generated"}))
            return

        self.engine.update_execution_options(item_registration=self.registration)
        with act_pass(authority):
            if table == "tool_call_log":
                await SqlToolCallLogStore(self.engine).append(ToolCallLogEntry(
                    id=uuid4(), conversation_id=_ROOM, conversation_type="group",
                    tag_id=_PASS.pass_id, tool_name="test-tool", tool_input={},
                    tool_result={"status": "generated"}, created_at=_AT,
                ))
            elif table == "computer_events":
                await SqlComputerEventStore(self.engine).append(_event(
                    conversation_id=_ROOM, tag_id=_PASS.pass_id, requester_person_id=_PERSON,
                    created_at=_AT,
                ))
            else:
                raise AssertionError(f"Missing writer fixture for {table}")

    async def _watch_result(self) -> None:
        graph = SqlCognitiveGraph(self.engine)
        common = {"unit": _UNIT, "writer": "fixture", "exposure": "internal"}
        await graph.create(ENTITY, {
            **common, "id": "target", "kind": "entity", "basis": "computed",
            "state": "admitted", "name": "fixture page",
            "source_ref_kind": "page", "source_ref_value": "https://example.invalid/page",
        })
        await graph.create(INTENTION, {
            **common, "id": "maintain", "kind": "intention", "basis": "committed",
            "state": "open", "owner": "nora", "owed_to": "nora", "form": "maintain",
            "holds_while_form": "predicate",
            "holds_while": {"graph_id": "fixture", "path": ["state"], "equals": "open"},
            "delivery_mode": "when_it_comes_up",
        })
        await graph.create(EDGE, {
            **common, "id": "watch", "kind": "edge", "basis": "committed", "state": "live",
            "type": "watches", "src_id": "maintain", "dst_id": "target",
        })
        store = SqlWatchResults(self.engine, self.registration, {"page"})
        async with store.next(None) as attempt:
            assert attempt is not None
            await attempt.record("fixture-hash", _AT)

    async def write(self, table: str) -> None:
        if table == "messages" or table.startswith("message_"):
            await self._message_event(table)
        elif table.startswith("social_"):
            await self._social(table)
        elif table == "presence_fixes":
            await PresenceStore(self.engine, registration=self.registration).record_fix(
                fix=PresenceFix(_CIRCLE, _PERSON, _MS, 1.0, 2.0, WireState.LIVE, _MS),
            )
        elif table == "source_health":
            await SqlSourceHealthLog(self.engine, registration=self.registration).record(
                source="presence", scope=_CIRCLE, event="poll", outcome="ok", at_ms=_MS,
            )
        elif table == "fetched_pages":
            await SqlFetchedPageStore(self.engine, registration=self.registration).put(
                page=FetchedPage(
                    self.room, "https://example.invalid/page", "test-blob",
                    _AT, _AT + timedelta(days=1), {},
                ),
            )
        elif table == "platform_observation":
            await SqlObservationStore(self.engine, registration=self.registration).append(
                observation=Observation(
                    object_kind="poll", scope=SourceScope("signal", "test-account", self.room),
                    object_ref="test-poll", operation="create",
                    payload=PollPayload(question="Test?", options=(("a", "Yes"),), state="open"),
                    actor_id=_AUTHOR, actor_status="known",
                    received_ms=_MS + 1000, occurred_ms=_MS, time_quality="upstream",
                ),
                event_id="test-observation", source_digest="test-digest",
            )
        elif table == "watch_result":
            await self._watch_result()
        elif table in {"forget_request", "dispute"}:
            intake = memory_intake(self.engine, self.registration)
            if table == "forget_request":
                await intake.forget(unit=_UNIT, author=_PERSON, targets=["generated-item"], at=_AT)
            else:
                await SqlCognitiveGraph(self.engine).create(METADATA.tables["claim"], {
                    "id": "generated-claim", "unit": _UNIT, "kind": "claim", "basis": "inferred",
                    "reasoning": "induced", "confidence": "low", "part": "fact",
                    "content": {"text": "fixture"}, "state": "live",
                    "writer": "generated", "exposure": "internal",
                })
                await intake.dispute(unit=_UNIT, author=_PERSON, claim_id="generated-claim", at=_AT)
        elif table in _FORWARD_SOURCES:
            await self._executor_evidence(table)
        else:
            raise AssertionError(f"Missing writer fixture for {table}")


@pytest.mark.parametrize("name", _NON_PASS_SOURCES)
async def test_item_writer_registers_with_its_planes(engine: AsyncEngine, name: str) -> None:
    before = datetime.now(UTC)
    await _Writer(engine, _registration()).write(name)
    table = METADATA.tables[name]
    async with engine.connect() as conn:
        row = (await conn.execute(sa.select(table))).mappings().one()
        registered = (await conn.execute(sa.select(REGISTRY).where(
            REGISTRY.c.id == row["graph_id"], REGISTRY.c.unit == _UNIT,
        ))).mappings().one()

    source = ITEM_SOURCES[name]
    assert row["unit"] == _UNIT
    assert registered["kind"] == row["kind"] == "item"
    assert registered["state"] == row["state"] == "live"
    assert row["basis"] == "observed"
    assert (row["exposure"], row["writer"]) == (source.exposure, source.writer)
    expected_at = row["recorded_at"] if name in {
        "social_item_count", "social_item_state", "social_poll",
    } else _AT
    assert row["occurred_at"] == row["valid_from"] == expected_at
    assert row["origin_room"] == (_ROOM if name in _ROOM_SOURCES else None)
    assert row["identities"] == ([_PERSON] if source.identity_columns else [])
    assert before <= row["asserted_at"] <= datetime.now(UTC)
    assert row["excluded_identities"] == []
    assert row["lineage"] == {}
    assert {key: row[key] for key in _REVISIONS} == _REVISIONS
    for null in (
        "valid_to", "replaces", "occasion", "pass", "trace", "model",
        "rev_instruction", "rev_skill",
    ):
        assert row[null] is None


@pytest.mark.parametrize("name", _FORWARD_SOURCES)
async def test_executor_writer_registers_with_its_pass_planes(
    engine: AsyncEngine, name: str,
) -> None:
    before = datetime.now(UTC)
    await _Writer(engine, _registration(multiple=True)).write(name)
    table = METADATA.tables[name]
    async with engine.connect() as conn:
        row = (await conn.execute(sa.select(table))).mappings().one()
        registered = (await conn.execute(sa.select(REGISTRY).where(
            REGISTRY.c.id == row["graph_id"], REGISTRY.c.unit == _UNIT,
        ))).mappings().one()

    assert (row["unit"], row["origin_room"], row["identities"]) == (_UNIT, _ROOM, [_PERSON])
    assert registered["kind"] == row["kind"] == "item"
    assert registered["state"] == row["state"] == "live"
    assert row["basis"] == "observed"
    assert row["valid_from"] == row["occurred_at"]
    assert before <= row["asserted_at"] <= datetime.now(UTC)
    assert {key: row[key] for key in _PASS.provenance()} == _PASS.provenance()


@pytest.mark.fresh_database
@pytest.mark.parametrize("name", ITEM_SOURCES)
async def test_item_writer_rolls_back_after_registry_insert(
    engine: AsyncEngine, name: str, caplog: pytest.LogCaptureFixture,
) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text("""
            CREATE FUNCTION fail_after_item_registry() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM registry WHERE id = NEW.graph_id AND unit = NEW.unit)
                THEN RAISE EXCEPTION 'probe missed the registry insert';
                END IF;
                RAISE EXCEPTION 'generated failure after registry insert';
            END
            $$
        """))
        await conn.execute(sa.text(
            f'CREATE TRIGGER generated_failure BEFORE INSERT ON "{name}" '
            "FOR EACH ROW EXECUTE FUNCTION fail_after_item_registry()"
        ))
    writer = _Writer(engine, _registration())
    if name in {"source_health", "fetched_pages"}:
        await writer.write(name)
        assert "generated failure after registry insert" in caplog.text
    else:
        with pytest.raises(sa.exc.DBAPIError, match="generated failure after registry insert"):
            await writer.write(name)
    table = METADATA.tables[name]
    async with engine.connect() as conn:
        count = (await conn.execute(sa.select(sa.func.count()).select_from(table))).scalar_one()
        registered = await conn.scalar(sa.select(sa.func.count()).select_from(REGISTRY))
    prerequisites = 3 if name == "watch_result" else int(name in {
        "message_edit", "message_reaction", "message_delete",
        "social_item_count", "social_item_state", "nora_act_outcome", "dispute",
    })
    assert count == 0
    assert registered == prerequisites


@pytest.mark.parametrize("name", ITEM_SOURCES)
async def test_item_insert_with_unit_and_no_registry_is_refused(
    engine: AsyncEngine, name: str,
) -> None:
    await _Writer(engine, _registration()).write(name)
    table = METADATA.tables[name]
    async with engine.begin() as conn:
        row = dict((await conn.execute(sa.select(table))).mappings().one())
        if name in _FORWARD_SOURCES or name in {"watch_result", "forget_request", "dispute"}:
            if name in {"nora_act_outcome", "watch_result", "forget_request", "dispute"}:
                row.pop("id")
            elif name in {"computer_events", "tool_call_log"}:
                row["id"] = uuid4()
            else:
                row["id"] += "-unregistered"
        else:
            await conn.execute(sa.delete(table))
            row["unit"] = "test-unregistered"
        if name == "computer_events":
            row["operation_id"] = uuid4()
        row["graph_id"] = "test-no-registry"
        with pytest.raises(sa.exc.IntegrityError, match=f"fk_{name}_graph_registry"):
            async with conn.begin_nested():
                await conn.execute(sa.insert(table).values(row))


@pytest.mark.parametrize("name", sorted(_ROOM_SOURCES))
async def test_item_from_room_outside_a_unit_keeps_its_record(
    engine: AsyncEngine, name: str,
) -> None:
    await _Writer(engine, _registration(), room="test-unbound").write(name)
    table = METADATA.tables[name]
    async with engine.connect() as conn:
        row = (await conn.execute(sa.select(table.c.unit, table.c.graph_id))).one()
        registered = (await conn.execute(sa.select(sa.func.count()).select_from(REGISTRY))).scalar()
    assert row == (None, None)
    assert registered == 0


async def test_a_raw_presence_fix_is_collector_input_not_a_graph_item(
    engine: AsyncEngine,
) -> None:
    registration = _registration(multiple=True)
    written = await PresenceStore(engine, registration=registration).record_fix(
        fix=PresenceFix(_CIRCLE, _PERSON, _MS, 1.0, 2.0, WireState.LIVE, _MS),
    )
    assert written is True
    async with engine.connect() as conn:
        fixes = (await conn.execute(
            sa.select(METADATA.tables["presence_fixes"].c.unit).order_by(
                METADATA.tables["presence_fixes"].c.unit,
            ),
        )).scalars().all()
        registered = (await conn.execute(
            sa.select(sa.func.count()).select_from(REGISTRY),
        )).scalar_one()
        stimuli = (await conn.execute(
            sa.select(sa.func.count()).select_from(GRAPH_STIMULUS),
        )).scalar_one()
    assert fixes == [_UNIT, _SECOND]
    assert registered == 0
    assert stimuli == 0


async def test_a_raw_presence_fix_dedupes_inside_each_observing_unit(engine: AsyncEngine) -> None:
    registration = _registration(multiple=True)
    store = PresenceStore(engine, registration=registration)
    fix = PresenceFix(_CIRCLE, _PERSON, _MS, 1.0, 2.0, WireState.LIVE, _MS)
    assert await store.record_fix(fix=fix) is True
    table = METADATA.tables["presence_fixes"]
    async with engine.begin() as conn:
        await conn.execute(sa.delete(table).where(table.c.unit == _SECOND))

    assert await store.record_fix(fix=fix) is True
    assert await store.record_fix(fix=fix) is False

    async with engine.connect() as conn:
        rows = (await conn.execute(
            sa.select(table.c.unit, sa.func.count().label("copies"))
            .group_by(table.c.unit).order_by(table.c.unit),
        )).all()
    assert rows == [(_UNIT, 1), (_SECOND, 1)]


@pytest.mark.parametrize("name", _FANOUT_SOURCES)
async def test_one_item_per_observing_unit(engine: AsyncEngine, name: str) -> None:
    await _Writer(engine, _registration(multiple=True)).write(name)
    table = METADATA.tables[name]
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.select(table.c.unit, table.c.graph_id))).all()
        registry_rows = (await conn.execute(sa.select(REGISTRY.c.unit, REGISTRY.c.id))).all()
    assert len(rows) == 2
    assert {row.unit for row in rows} == {_UNIT, _SECOND}
    assert all(row in registry_rows and row.graph_id for row in rows)
    assert len({row.graph_id for row in rows}) == 2
    if name == "presence_fixes":
        fixes = await PresenceStore(engine).window(
            circle_id=_CIRCLE, person_id=_PERSON, since_ts=_MS, until_ts=_MS,
        )
        assert len(fixes) == 1
    if name == "social_item":
        through = int(datetime.now(UTC).timestamp() * 1000) + 1000
        posted = await SqlSocialStreamStore(engine).posted(persons=[_PERSON], through_ms=through)
        assert len(posted.items) == 1
    if name == "social_poll":
        calls = await SqlSocialStreamStore(engine).calls_since(
            network="instagram", since=datetime(1970, 1, 1, tzinfo=UTC),
        )
        assert len(calls) == 1
