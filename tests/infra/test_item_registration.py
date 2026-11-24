from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.fetched_page import FetchedPage
from nora.domain.models import NormalizedMessage, PersonRef
from nora.domain.observations import Observation, PollPayload, SourceScope
from nora.domain.presence import PresenceFix, WireState
from nora.domain.social_stream import ItemCounts, SocialItem
from nora.infra.sql.audit import SqlRecordStore
from nora.infra.sql.cognitive_graph_schema import REGISTRY
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.fetched_pages import SqlFetchedPageStore
from nora.infra.sql.item_registration import RegistrationScope, SqlItemRegistration
from nora.infra.sql.migrations_runner import sync_url_for
from nora.infra.sql.observations import SqlObservationStore
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.schema import METADATA
from nora.infra.sql.social_stream import SqlSocialStreamStore
from nora.infra.sql.source_health import SqlSourceHealthLog
from nora.memory.domain.registration import AGGREGATE_FAMILIES, ITEM_SOURCES
from nora.persons import Life360Membership, Persons
from nora.policy import Conversation, Policy


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
    for payload in ("name", "aliases", "pointer_kind", "pointer_ref"):
        del entity_fields[payload]
    entity_fields["graph_id"] = entity_fields.pop("id")

    actual = {field["name"]: field["type"] for field in graph_schema.get_columns(name)}
    assert {field: str(actual[field]) for field in actual.keys() & entity_fields.keys()} == {
        field: str(field_type) for field, field_type in entity_fields.items()
    }

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
_FANOUT_SOURCES = tuple(source for source in ITEM_SOURCES if source not in _ROOM_SOURCES)


class _FixedRevisions:
    def current(self) -> dict[str, str]:
        return _REVISIONS.copy()


def _registration(*, multiple: bool = False) -> SqlItemRegistration:
    owner = PersonRef(_PERSON, "Generated", _PERSON)
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
        else:
            raise AssertionError(f"Missing writer fixture for {table}")


@pytest.mark.parametrize("name", ITEM_SOURCES)
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
    assert row["persons"] == ([_PERSON] if source.person_columns else [])
    assert before <= row["asserted_at"] <= datetime.now(UTC)
    assert row["excluded_persons"] == []
    assert row["lineage"] == {}
    assert {key: row[key] for key in _REVISIONS} == _REVISIONS
    for null in (
        "valid_to", "replaces", "occasion", "pass", "trace", "model",
        "rev_instruction", "rev_skill",
    ):
        assert row[null] is None


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
    prerequisites = int(name in {
        "message_edit", "message_reaction", "message_delete",
        "social_item_count", "social_item_state",
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
        await conn.execute(sa.delete(table))
        row.update(unit="test-unregistered", graph_id="test-no-registry")
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
