from dataclasses import dataclass
from typing import Any

from nora.app.composition_helpers import _room_claims
from nora.app.gatherings import GatheringService
from nora.domain.live import LiveMapping
from nora.domain.reminders import ReminderStore
from nora.infra.sql.gatherings import SqlGatheringStore
from nora.infra.sql.reminders import SqlReminderStore

"""Compose this area's stores from shared dependencies."""


@dataclass(frozen=True)
class RemindersWiring:
    reminders: Any


def reminders(engine) -> RemindersWiring:
    # The reminders store, REUSING `engine` for the reason above: a second
    # `create_sql_engine` forks the pool and its `aclose()` would dispose the engine the
    # record store and the presence reader are still using. Built HERE, beside the other…
    reminders: ReminderStore = SqlReminderStore(engine)
    return RemindersWiring(reminders=reminders)


@dataclass(frozen=True)
class EventStoreWiring:
    event_room_claims: Any
    event_store: Any


def event_store(allowlist, engine) -> EventStoreWiring:
    # `GatheringService`: the ONE use-case layer for Gathering business rules. `event_store`
    # REUSES `engine` for the same reason `reminders`/`compute_jobs`/`tool_call_log_store`
    # do above — a second `create_sql_engine` would fork the pool.…
    event_store = SqlGatheringStore(engine)
    # Events stored before units existed carry `unit_id=""` — units live in `policy.toml`,
    # not the DB, so the column cannot be filled — and policy also knows the platform each
    # id belongs to, which the row does not. The service admits a legacy row only when this…
    event_room_claims = LiveMapping(lambda: _room_claims(allowlist))
    return EventStoreWiring(event_room_claims=event_room_claims, event_store=event_store)


@dataclass(frozen=True)
class EventsWiring:
    events: Any


def events(event_store) -> EventsWiring:
    events = GatheringService(store=event_store)
    return EventsWiring(events=events)
