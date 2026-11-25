"""Registered aggregate rows for tests of existing Rollup consumers."""

from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from nora.analytics.rollup import RollupConfig
from nora.analytics.runner import RollupRunner, Subject
from nora.analytics.units import PersonScope, UnitConfig, UnitRollups
from nora.domain.models import PersonRef
from nora.infra.sql.cognitive_graph_schema import REGISTRY
from nora.infra.sql.item_registration import (
    RegistrationScope,
    RuntimeRevisions,
    SqlItemRegistration,
    epoch_ms,
)
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.rollups import SqlRollupStore, UnitRooms
from nora.infra.sql.schema import MESSAGES, PRESENCE_FIXES
from nora.infra.sql.source_health import SqlSourceHealthLog
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Conversation, Policy


async def insert_rollup_row(conn: AsyncConnection, table: sa.Table, **row) -> int:
    graph_id = f"generated:{table.name}:{uuid4()}"
    await conn.execute(sa.insert(REGISTRY).values(id=graph_id, kind="aggregate", unit=row["unit"]))
    result = await conn.execute(
        sa.insert(table).values(graph_id=graph_id, **row).returning(table.c.id)
    )
    return int(result.scalar_one())


class RollupFixture:
    """One generated record shared by the Postgres rollup probes."""

    unit = "generated-unit"
    rooms = (("signal", "generated-signal"), ("discord", "generated-discord"))
    circle = "generated-circle"

    def __init__(self, engine, *, people=("generated-person",), config=None):
        self.engine = engine
        self.people = people
        self.metrics = Metrics()
        conversations = tuple(
            Conversation(
                platform, frozenset({room}), frozenset(people), primary_id=room, unit=self.unit
            )
            for platform, room in self.rooms
        )
        policy = Policy(
            conversations, _by_id={(room.platform, room.primary_id): room for room in conversations}
        )
        accounts = {(platform, p): p for platform, _ in self.rooms for p in people}
        persons = Persons({p: PersonRef(p, p) for p in people}, accounts)
        self.registration = SqlItemRegistration(
            RegistrationScope(policy, persons), RuntimeRevisions({}, policy, persons)
        )
        self.store = SqlRollupStore(engine, registration=self.registration)
        self.mobility = SqlMobilityStore(engine, registration=self.registration)
        self.unit_rooms = UnitRooms(self.unit, self.rooms, people, accounts)
        self.subjects = tuple(Subject(p, (p,), (self.circle,)) for p in people)
        unit_config = config or UnitConfig()
        rollup_config = RollupConfig(
            tick_seconds=120,
            safety_lag=600,
            settled_seconds=900,
            max_age_seconds=86_400,
            batch_limit=50,
            backfill_batch_limit=20_000,
            backfill_threshold=4_000,
            grace_seconds=60,
            stay_radius_m=120,
            stay_min_dwell_seconds=600,
            cluster_eps_m=100,
            cluster_min_samples=3,
            min_nights=3,
            min_weekdays=3,
            label_freshness_days=45,
            corridor_min_samples=2,
        )
        self.runner = RollupRunner(
            mobility=self.mobility,
            rollups=self.store,
            config=rollup_config,
            unit_rollups=UnitRollups(
                store=self.store,
                config=unit_config,
                units=(self.unit_rooms,),
                people={p: PersonScope(p, (self.circle,)) for p in people},
                stimuli=SqlStimulusStore(engine),
            ),
            subjects=self.subjects,
            unit_config=unit_config,
        )

    async def message(self, platform, at_ms, *, person=None, **values):
        room = dict(self.rooms)[platform]
        async with self.engine.begin() as conn:
            at = epoch_ms(at_ms)
            fields = await self.registration.planes(
                conn,
                MESSAGES,
                unit=self.unit,
                occurred_at=at,
                identities=(person,),
                room=room,
                own=True,
            )
            message = (
                sa.insert(MESSAGES)
                .values(
                    **fields,
                    platform=platform,
                    conversation_id=room,
                    conversation_type="group",
                    sender_id=person or "generated-sender",
                    person_id=person,
                    ts=at_ms,
                    created_at=at.isoformat(),
                    **values,
                )
                .returning(MESSAGES.c.id)
            )
            return await conn.scalar(message)

    async def health(self, source, scope, event, outcome, at_ms, *, unit=None):
        health = SqlSourceHealthLog(
            self.engine,
            registration=self.registration,
            clock=lambda: at_ms / 1000,
        )
        async with self.engine.begin() as conn:
            await health.record_in(
                conn,
                source=source,
                scope=scope,
                event=event,
                outcome=outcome,
                at_ms=at_ms,
                unit=unit or self.unit,
                silent_scope={},
            )

    async def fix(self, person, at_ms, *, lat=22.4, lng=114.1, driving=False, address=None):
        async with self.engine.begin() as conn:
            fields = await self.registration.planes(
                conn,
                PRESENCE_FIXES,
                unit=self.unit,
                occurred_at=epoch_ms(at_ms),
                identities=(person,),
                own=True,
            )
            await conn.execute(
                sa.insert(PRESENCE_FIXES).values(
                    **fields,
                    circle_id=self.circle,
                    person_id=person,
                    ts=at_ms,
                    lat=lat,
                    lng=lng,
                    recorded_at=at_ms,
                    accuracy_m=10.0,
                    is_driving=int(driving),
                    wire_state="live",
                    address=address,
                )
            )
