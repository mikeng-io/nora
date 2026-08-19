# Event Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Nora's Event module — durable gatherings/RSVP state, chat-first tools that reuse the existing reminders fire path for notifications and the existing Places infra for venue data, and a read-only web surface — exactly as specified.

**Architecture:** A `nora/domain/events.py` (types + `EventStore` port) and `nora/app/events.py` (`EventService`, the one use-case layer both the agent tools and the web read routes call) sit between a Postgres-backed store and two thin adapters. `EventService` reuses three things that already ship rather than rebuilding them: the reminders domain (for notifications), the `route_source`/`PlaceResolver` (for venue resolution), and Honcho's dialectic `chat()` (for preference-based suggestions).

**Tech Stack:** Python (SQLAlchemy Core, asyncpg, Alembic, pytest), TypeScript/Next.js (App Router, zod, NextAuth) for the web surface.

**Spec:** `docs/superpowers/specs/2026-08-19-event-module-design.md` (commit `9fe2ee7`) — the plan argues from this spec; read both.

## Global Constraints

- **Migration number is `0015_events`, not `0016`** — the spec assumed `0015_presence_analytics` had landed first; it hasn't (`0014_lang_obs_scope_idx.py` is still the latest on disk as of this plan). Task 15 fixes the spec's stale assumption.
- **`Event.conversation_id`, not `group_id`.** The spec called this field `group_id` ("same group→workspace concept reminders use") — but reminders' own field for exactly that concept is named `conversation_id` (`nora/domain/reminders.py`'s `Reminder.conversation_id`). This plan uses `conversation_id` throughout so Event's field is *the same field*, not a same-meaning synonym under a different name.
- **`Event.reminder_id: UUID | None`, not `reminder_series_id`.** Event never has a recurrence concept (no repeat field anywhere in the spec's data model) — its linked reminder is always one-shot (`series=None`). Storing a `Series.id` for something that's never a series is wrong; store the `Reminder.id` directly.
- **No `pip install`** — dependencies via `uv add` if any are needed (none should be for this plan).
- **Every datetime is UTC-aware at the port boundary** — reuse `nora.domain.reminders.ensure_utc`, do not write a second copy.
- **Every port method is keyword-only after `self`** except a single whole-object argument (`create(self, event: Event)`), matching `ReminderStore`'s and `nora/domain/ports.py`'s convention.
- **SQLAlchemy Core, never the ORM** — table defs in `nora/infra/sql/schema.py`, `AsyncEngine` injected via `__init__`, writes in `async with self._engine.begin()`, reads in `async with self._engine.connect()` — matching `nora/infra/sql/reminders.py` exactly.
- **Run the full gate before every commit that isn't purely a doc/test-scaffold change**: `uv run pytest -q`, `uv run ruff check nora/ evals/ tests/`, `uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence`, `uv run lint-imports`.

---

## File Structure

```
nora/domain/events.py                 NEW — Event, RSVP, EventStatus, RSVPResponse,
                                       VenueCandidate, VenueSuggestions, EventStore
                                       (Protocol), DuplicateEventError, normalise_event
nora/infra/sql/schema.py              MODIFY — add EVENTS, EVENT_RSVPS tables + indexes
nora/infra/sql/migrations/versions/
  0015_events.py                      NEW — hand-written migration
nora/infra/sql/events.py              NEW — SqlEventStore
nora/testing/events.py                NEW — FakeEventStore
tests/contract/
  test_event_store_contract.py        NEW — sql+fake parametrized suite
nora/agent/security.py                NEW — scan_identifiers/_looks_like_coordinate,
                                       relocated from nora/agent/tools.py so
                                       EventService can reuse them without
                                       duplicating the R-28 screen
nora/app/events.py                    NEW — EventService
tests/test_events_service.py          NEW — EventService unit tests (fakes only)
nora/app/composition.py               MODIFY — construct SqlEventStore + EventService
nora/agent/tools.py                   MODIFY — 6 new tool closures + NATIVE_TOOLS
                                       entries + build_tool_specs param
nora/agent/registry.py                MODIFY — ToolRegistry gains `events` param
tests/test_event_tools.py             NEW — tool-level tests incl. open-event doctrine
tests/test_acceptance_rules.py        MODIFY — add R-52 entry
design/09-decision-index.md           MODIFY — add DEC-117 entry
nora/web/events.py                    NEW — build_events_payload/build_event_payload
nora/web/api.py                       MODIFY — register GET /app/v1/events(/{id})
app/src/lib/events-proxy.ts           NEW — proxyEventsGet, mirrors games-proxy.ts
app/src/app/api/events/route.ts       NEW
app/src/app/api/events/[id]/route.ts  NEW
app/src/lib/events-schema.ts          NEW — zod schema + fetchEvents/fetchEvent
app/src/app/[locale]/(portal)/events/
  page.tsx                            NEW — list page
  [id]/page.tsx                       NEW — detail page
app/src/lib/modules.ts                MODIFY — add "events" module entry
app/src/components/nav-links.tsx      MODIFY — add events icon
app/src/messages/en.json              MODIFY — add nav.events + events namespace
release-fragments/unreleased/
  event-module.json                   NEW
docs/superpowers/specs/
  2026-08-19-event-module-design.md   MODIFY — fix 0016→0015 migration number
```

---

### Task 1: Domain types — `nora/domain/events.py`

**Files:**
- Create: `nora/domain/events.py`
- Test: `tests/test_events_domain.py`

**Interfaces:**
- Produces: `EventStatus`, `RSVPResponse` (StrEnum); `Event`, `RSVP`, `VenueCandidate`, `VenueSuggestions` (frozen dataclasses); `EventStore` (Protocol); `DuplicateEventError`; `normalise_event(event: Event) -> Event`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_events_domain.py
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from nora.domain.events import (
    Event,
    EventStatus,
    RSVP,
    RSVPResponse,
    normalise_event,
)
from nora.domain.reminders import NaiveDatetimeError


def _event(**overrides) -> Event:
    defaults = dict(
        id=uuid4(),
        conversation_id="signal:group-1",
        title="Dinner",
        created_by="miles",
        when=None,
        venue_text=None,
        venue_place_id=None,
        venue_name=None,
        venue_address=None,
        venue_rating=None,
        venue_price_level="",
        venue_maps_url=None,
        venue_resolved_at=None,
        status=EventStatus.PROPOSED,
        reminder_id=None,
        created_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Event(**defaults)


def test_normalise_event_accepts_utc_aware_datetimes():
    ev = _event(when=datetime(2026, 8, 22, 19, 0, tzinfo=timezone.utc))
    out = normalise_event(ev)
    assert out.when == ev.when


def test_normalise_event_rejects_naive_when():
    ev = _event(when=datetime(2026, 8, 22, 19, 0))  # naive
    with pytest.raises(NaiveDatetimeError):
        normalise_event(ev)


def test_rsvp_response_values():
    assert RSVPResponse.YES.value == "yes"
    assert RSVPResponse.NO.value == "no"
    assert RSVPResponse.MAYBE.value == "maybe"


def test_event_status_values():
    assert EventStatus.PROPOSED.value == "proposed"
    assert EventStatus.CONFIRMED.value == "confirmed"
    assert EventStatus.CANCELLED.value == "cancelled"


def test_rsvp_construction():
    r = RSVP(
        event_id=uuid4(), person_id="miles",
        response=RSVPResponse.YES,
        responded_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    assert r.response is RSVPResponse.YES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_events_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nora.domain.events'`

- [ ] **Step 3: Write the implementation**

```python
# nora/domain/events.py
"""Event/RSVP domain types and the `EventStore` port.

Mirrors `nora.domain.reminders`'s split (types + port in one module, no
separate `ports.py` entry — a domain module's own port lives with its
types, matching `ReminderStore`'s own precedent). An `Event` OWNS at most
one `Reminder` (`reminder_id`) when it has a `when` — Event never has a
recurrence concept of its own, so that reminder is always one-shot
(`Series` is never involved). Notifications are entirely the existing
`nora.domain.reminders` + `nora.app.fire` path; nothing here re-implements
scheduling.

Venue fields are resolved ONCE, at write time, through the same
`route_source`/`PlaceResolver` `nora.agent.tools`'s `place_info` tool
already uses (see `nora.app.events.EventService`) — `venue_text` is what
was actually SAID; `venue_place_id`/`venue_name`/`venue_address`/
`venue_rating`/`venue_price_level`/`venue_maps_url` are the cached,
stable result of that one resolution. Raw `lat`/`lng` are deliberately
absent (DEC-24 — never in a model-facing schema, and `route()` never
accepts a coordinate as an argument anyway) and `open_now` is deliberately
absent (it goes stale by the hour; "is it open now" stays a fresh
on-demand check, never a cached column).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, Sequence
from uuid import UUID

from nora.domain.reminders import ensure_utc


class EventStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class RSVPResponse(StrEnum):
    YES = "yes"
    NO = "no"
    MAYBE = "maybe"


class DuplicateEventError(Exception):
    """Raised by `EventStore.create` on a primary-key collision — mirrors
    `DuplicateReminderError`'s shape so callers handle both the same way."""

    def __init__(self, event_id: UUID) -> None:
        super().__init__(f"event {event_id} already exists")
        self.event_id = event_id


@dataclass(frozen=True, slots=True)
class Event:
    id: UUID
    conversation_id: str
    title: str
    created_by: str  # person_id
    when: datetime | None
    venue_text: str | None
    venue_place_id: str | None
    venue_name: str | None
    venue_address: str | None
    venue_rating: float | None
    venue_price_level: str
    venue_maps_url: str | None
    venue_resolved_at: datetime | None
    status: EventStatus
    reminder_id: UUID | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RSVP:
    event_id: UUID
    person_id: str
    response: RSVPResponse
    responded_at: datetime


@dataclass(frozen=True, slots=True)
class VenueCandidate:
    """One `suggest_venue` candidate. `source` names WHY it was suggested —
    "mention_frequency" or "explicit_callback" — so a reply can say why,
    not just what."""

    venue_text: str
    place_id: str | None
    name: str | None
    address: str | None
    rating: float | None
    price_level: str
    maps_url: str | None
    source: str


@dataclass(frozen=True, slots=True)
class VenueSuggestions:
    """`status` is the R-27 three-state shape: `ok` / `nothing_found` /
    `unavailable` — an ERROR is never a PASS. `route_hints` is the reserved
    seam from spec §5.1: always `None` in this version; a later revision
    fills it via `saved_places()` + `route()` per attendee under the R-28
    guard those tools already enforce — the type does not change when it
    does."""

    status: str
    candidates: tuple[VenueCandidate, ...] = ()
    route_hints: None = None


class EventStore(Protocol):
    async def create(self, event: Event) -> None: ...

    async def get(self, event_id: UUID) -> Event | None: ...

    async def list_for_conversation(
        self, *, conversation_id: str, status: EventStatus | None = None,
    ) -> Sequence[Event]: ...

    async def update(self, event: Event) -> None: ...

    async def add_rsvp(self, rsvp: RSVP) -> None: ...

    async def rsvps_for(self, event_id: UUID) -> Sequence[RSVP]: ...

    async def aclose(self) -> None: ...


def normalise_event(event: Event) -> Event:
    """`ensure_utc` over every one of an event's datetimes — the port's
    rule, in one place, exactly matching `normalise_reminder`'s shape."""
    from dataclasses import replace

    return replace(
        event,
        when=(
            None if event.when is None
            else ensure_utc(event.when, field="when")
        ),
        venue_resolved_at=(
            None if event.venue_resolved_at is None
            else ensure_utc(event.venue_resolved_at, field="venue_resolved_at")
        ),
        created_at=ensure_utc(event.created_at, field="created_at"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_events_domain.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Type-check and commit**

Run: `uv run mypy nora/domain`
Expected: no new errors.

```bash
git add nora/domain/events.py tests/test_events_domain.py
git commit -m "feat(nora): add Event/RSVP domain types and EventStore port"
```

---

### Task 2: Schema + hand-written migration `0015_events`

**Files:**
- Modify: `nora/infra/sql/schema.py`
- Create: `nora/infra/sql/migrations/versions/0015_events.py`
- Test: `tests/infra/test_schema_parity.py` (existing file — this task must keep it green, not add to it)

**Interfaces:**
- Produces: `EVENTS`, `EVENT_RSVPS` (`sa.Table` objects), `IDX_EVENTS_CONVERSATION` (index) in `nora/infra/sql/schema.py`, importable as `from nora.infra.sql.schema import EVENTS, EVENT_RSVPS`.
- Consumes: `METADATA` (already defined at `nora/infra/sql/schema.py:51`).

- [ ] **Step 1: Confirm `0014`'s exact revision id**

Run: `grep -n "^revision\|^down_revision" nora/infra/sql/migrations/versions/0014_lang_obs_scope_idx.py`

Use whatever string `revision = "..."` prints as this task's `down_revision`. (Expected, per the numeric-prefix convention every other file follows: `"0014"`.)

- [ ] **Step 2: Add the tables to `nora/infra/sql/schema.py`**

Append after `IDX_REMINDERS_DUE` (or after the last table in the file — check current end-of-file with `tail -30 nora/infra/sql/schema.py` first):

```python
# `events` / `event_rsvps` — gatherings among a group, chat-first (spec
# docs/superpowers/specs/2026-08-19-event-module-design.md). `venue_*` are
# resolved ONCE via the existing Places infra and cached here — see the
# module docstring on `nora.domain.events` for what is deliberately absent
# (raw lat/lng, open_now) and why.
EVENTS = sa.Table(
    "events",
    METADATA,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("conversation_id", sa.Text, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("when", sa.DateTime(timezone=True), nullable=True),
    sa.Column("venue_text", sa.Text, nullable=True),
    sa.Column("venue_place_id", sa.Text, nullable=True),
    sa.Column("venue_name", sa.Text, nullable=True),
    sa.Column("venue_address", sa.Text, nullable=True),
    sa.Column("venue_rating", sa.Float, nullable=True),
    sa.Column(
        "venue_price_level", sa.Text, nullable=False, server_default=sa.text("''"),
    ),
    sa.Column("venue_maps_url", sa.Text, nullable=True),
    sa.Column("venue_resolved_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("reminder_id", sa.Uuid, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "status IN ('proposed','confirmed','cancelled')", name="ck_events_status",
    ),
)

# The open-event doctrine's own query (spec §3.1): `conversation_id = ? AND
# status = 'proposed'`, run before every `create_event`. Composite so that
# scan is the prefix, not a filter over every event ever created in every
# conversation.
IDX_EVENTS_CONVERSATION = sa.Index(
    "idx_events_conversation", EVENTS.c.conversation_id, EVENTS.c.status,
)

EVENT_RSVPS = sa.Table(
    "event_rsvps",
    METADATA,
    sa.Column(
        "event_id", sa.Uuid,
        sa.ForeignKey("events.id"), primary_key=True,
    ),
    sa.Column("person_id", sa.Text, primary_key=True),
    sa.Column("response", sa.Text, nullable=False),
    sa.Column("responded_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "response IN ('yes','no','maybe')", name="ck_event_rsvps_response",
    ),
)
```

- [ ] **Step 3: Write the hand-written migration**

```python
# nora/infra/sql/migrations/versions/0015_events.py
"""events + event_rsvps

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-19

Hand-written (CLAUDE.md: `idx_messages_conv_ts` is the one index
autogenerate must never touch; neither table here is that index, but this
migration is hand-written anyway to stay consistent with every other
table-adding revision in this repo and to keep the CHECK constraints exact).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("when", sa.DateTime(timezone=True), nullable=True),
        sa.Column("venue_text", sa.Text, nullable=True),
        sa.Column("venue_place_id", sa.Text, nullable=True),
        sa.Column("venue_name", sa.Text, nullable=True),
        sa.Column("venue_address", sa.Text, nullable=True),
        sa.Column("venue_rating", sa.Float, nullable=True),
        sa.Column(
            "venue_price_level", sa.Text, nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("venue_maps_url", sa.Text, nullable=True),
        sa.Column("venue_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("reminder_id", sa.Uuid, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('proposed','confirmed','cancelled')",
            name="ck_events_status",
        ),
    )
    op.create_index(
        "idx_events_conversation", "events", ["conversation_id", "status"],
    )
    op.create_table(
        "event_rsvps",
        sa.Column(
            "event_id", sa.Uuid, sa.ForeignKey("events.id"), primary_key=True,
        ),
        sa.Column("person_id", sa.Text, primary_key=True),
        sa.Column("response", sa.Text, nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "response IN ('yes','no','maybe')", name="ck_event_rsvps_response",
        ),
    )


def downgrade() -> None:
    op.drop_table("event_rsvps")
    op.drop_index("idx_events_conversation", table_name="events")
    op.drop_table("events")
```

- [ ] **Step 4: Verify migration + schema parity**

Run: `uv run pytest tests/infra/test_schema_parity.py -v`
Expected: PASS — this is the gate CLAUDE.md names as authoritative over `alembic check` (which is blind to CHECK constraints and index column ordering); it must confirm `EVENTS`/`EVENT_RSVPS` as declared in `schema.py` match what the migration actually creates.

If a local Postgres is available: `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` to confirm the migration round-trips cleanly.

- [ ] **Step 5: Commit**

```bash
git add nora/infra/sql/schema.py nora/infra/sql/migrations/versions/0015_events.py
git commit -m "feat(nora): add events/event_rsvps tables (migration 0015)"
```

---

### Task 3: `SqlEventStore` — `nora/infra/sql/events.py`

**Files:**
- Create: `nora/infra/sql/events.py`

**Interfaces:**
- Consumes: `Event`, `RSVP`, `EventStatus`, `RSVPResponse`, `DuplicateEventError`, `normalise_event` from `nora.domain.events`; `EVENTS`, `EVENT_RSVPS` from `nora.infra.sql.schema`.
- Produces: `SqlEventStore` (satisfies `EventStore`).

- [ ] **Step 1: Write the implementation**

(No standalone unit test here — `SqlEventStore` is exercised entirely by Task 5's contract suite, exactly as `SqlReminderStore` has no test file of its own; the contract suite is the test, run against both implementations.)

```python
# nora/infra/sql/events.py
"""`EventStore` over SQLAlchemy Core. Shaped like `nora.infra.sql.reminders`:
the engine comes through `__init__`, every write is one statement inside
`async with self._engine.begin()`, every read inside
`async with self._engine.connect()`.

`FakeEventStore` (`nora/testing/events.py`) is the second implementation;
`tests/contract/test_event_store_contract.py` is the one suite both pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.events import (
    DuplicateEventError,
    Event,
    EventStatus,
    RSVP,
    RSVPResponse,
    normalise_event,
)
from nora.infra.sql.schema import EVENT_RSVPS, EVENTS

# PostgreSQL SQLSTATE 23505, unique_violation — matched exactly, the same
# reasoning as `nora.infra.sql.reminders._PG_UNIQUE_VIOLATION`: this table
# also carries a CHECK constraint, and a bad status value must not be
# reported as "duplicate id".
_PG_UNIQUE_VIOLATION = "23505"


def _row_to_event(r: Row[Any]) -> Event:
    return normalise_event(
        Event(
            id=r.id,
            conversation_id=r.conversation_id,
            title=r.title,
            created_by=r.created_by,
            when=r.when,
            venue_text=r.venue_text,
            venue_place_id=r.venue_place_id,
            venue_name=r.venue_name,
            venue_address=r.venue_address,
            venue_rating=r.venue_rating,
            venue_price_level=r.venue_price_level,
            venue_maps_url=r.venue_maps_url,
            venue_resolved_at=r.venue_resolved_at,
            status=EventStatus(r.status),
            reminder_id=r.reminder_id,
            created_at=r.created_at,
        )
    )


def _row_to_rsvp(r: Row[Any]) -> RSVP:
    return RSVP(
        event_id=r.event_id,
        person_id=r.person_id,
        response=RSVPResponse(r.response),
        responded_at=r.responded_at,
    )


def _insert_values(event: Event) -> dict[str, Any]:
    return dict(
        id=event.id,
        conversation_id=event.conversation_id,
        title=event.title,
        created_by=event.created_by,
        when=event.when,
        venue_text=event.venue_text,
        venue_place_id=event.venue_place_id,
        venue_name=event.venue_name,
        venue_address=event.venue_address,
        venue_rating=event.venue_rating,
        venue_price_level=event.venue_price_level,
        venue_maps_url=event.venue_maps_url,
        venue_resolved_at=event.venue_resolved_at,
        status=event.status.value,
        reminder_id=event.reminder_id,
        created_at=event.created_at,
    )


class SqlEventStore:
    """Satisfies `nora.domain.events.EventStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, event: Event) -> None:
        row = normalise_event(event)
        stmt = sa.insert(EVENTS).values(**_insert_values(row))
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != _PG_UNIQUE_VIOLATION:
                raise
            raise DuplicateEventError(row.id) from exc

    async def get(self, event_id: UUID) -> Event | None:
        stmt = sa.select(EVENTS).where(EVENTS.c.id == event_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_event(row)

    async def list_for_conversation(
        self, *, conversation_id: str, status: EventStatus | None = None,
    ) -> Sequence[Event]:
        stmt = (
            sa.select(EVENTS)
            .where(EVENTS.c.conversation_id == conversation_id)
            .order_by(EVENTS.c.created_at)
        )
        if status is not None:
            stmt = stmt.where(EVENTS.c.status == status.value)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_event(r) for r in rows]

    async def update(self, event: Event) -> None:
        row = normalise_event(event)
        stmt = (
            sa.update(EVENTS)
            .where(EVENTS.c.id == row.id)
            .values(**{k: v for k, v in _insert_values(row).items() if k != "id"})
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def add_rsvp(self, rsvp: RSVP) -> None:
        stmt = (
            sa.insert(EVENT_RSVPS)
            .values(
                event_id=rsvp.event_id,
                person_id=rsvp.person_id,
                response=rsvp.response.value,
                responded_at=rsvp.responded_at,
            )
            .on_conflict_do_update(
                index_elements=[EVENT_RSVPS.c.event_id, EVENT_RSVPS.c.person_id],
                set_=dict(
                    response=rsvp.response.value, responded_at=rsvp.responded_at,
                ),
            )
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def rsvps_for(self, event_id: UUID) -> Sequence[RSVP]:
        stmt = (
            sa.select(EVENT_RSVPS)
            .where(EVENT_RSVPS.c.event_id == event_id)
            .order_by(EVENT_RSVPS.c.responded_at)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_rsvp(r) for r in rows]

    async def aclose(self) -> None:
        await self._engine.dispose()
```

**Note on `add_rsvp`'s upsert**: `on_conflict_do_update` requires the postgres dialect's insert (`from sqlalchemy.dialects.postgresql import insert as pg_insert`), not `sa.insert`. Fix the import before running Task 5: replace `sa.insert(EVENT_RSVPS)` with `pg_insert(EVENT_RSVPS)` and add `from sqlalchemy.dialects.postgresql import insert as pg_insert` — a person can RSVP more than once (changing their mind), and `(event_id, person_id)` is the primary key, so a repeat RSVP must update, not raise `DuplicateEventError`-shaped noise.

- [ ] **Step 2: Fix the upsert import per the note above, then type-check**

Run: `uv run mypy nora/infra/sql/events.py`
Expected: no new errors (Task 5 provides the actual behavioral test).

- [ ] **Step 3: Commit**

```bash
git add nora/infra/sql/events.py
git commit -m "feat(nora): add SqlEventStore"
```

---

### Task 4: `FakeEventStore` — `nora/testing/events.py`

**Files:**
- Create: `nora/testing/events.py`

**Interfaces:**
- Consumes: same domain types as Task 3.
- Produces: `FakeEventStore` (satisfies `EventStore`).

- [ ] **Step 1: Write the implementation**

```python
# nora/testing/events.py
"""In-memory `EventStore` for tests. Must reproduce the REAL store's
failure/edge shapes — a duplicate `create` raises `DuplicateEventError`
exactly like `SqlEventStore` does, `update`/`add_rsvp` on the same
(event_id, person_id) upserts, `list_for_conversation` sorts by
`created_at` — never a fake that answers every call the same way
(CLAUDE.md's rule)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from nora.domain.events import (
    DuplicateEventError,
    Event,
    EventStatus,
    RSVP,
    normalise_event,
)


class FakeEventStore:
    def __init__(self) -> None:
        self._events: dict[UUID, Event] = {}
        self._rsvps: dict[tuple[UUID, str], RSVP] = {}

    async def create(self, event: Event) -> None:
        row = normalise_event(event)
        if row.id in self._events:
            raise DuplicateEventError(row.id)
        self._events[row.id] = row

    async def get(self, event_id: UUID) -> Event | None:
        return self._events.get(event_id)

    async def list_for_conversation(
        self, *, conversation_id: str, status: EventStatus | None = None,
    ) -> Sequence[Event]:
        out = [
            e for e in self._events.values()
            if e.conversation_id == conversation_id
            and (status is None or e.status == status)
        ]
        return sorted(out, key=lambda e: e.created_at)

    async def update(self, event: Event) -> None:
        row = normalise_event(event)
        self._events[row.id] = row

    async def add_rsvp(self, rsvp: RSVP) -> None:
        self._rsvps[(rsvp.event_id, rsvp.person_id)] = rsvp

    async def rsvps_for(self, event_id: UUID) -> Sequence[RSVP]:
        out = [r for (eid, _), r in self._rsvps.items() if eid == event_id]
        return sorted(out, key=lambda r: r.responded_at)

    async def aclose(self) -> None:
        pass
```

- [ ] **Step 2: Type-check and commit**

Run: `uv run mypy nora/testing/events.py`

```bash
git add nora/testing/events.py
git commit -m "feat(nora): add FakeEventStore"
```

---

### Task 5: Contract tests — `tests/contract/test_event_store_contract.py`

**Files:**
- Create: `tests/contract/test_event_store_contract.py`

**Interfaces:**
- Consumes: `SqlEventStore` (Task 3), `FakeEventStore` (Task 4), `Event`/`RSVP`/`EventStatus`/`RSVPResponse`/`DuplicateEventError` (Task 1).

- [ ] **Step 1: Write the contract suite**

Mirror `tests/contract/test_reminder_store_contract.py`'s fixture exactly (read it first to confirm the `pg_url` fixture name and `run_migrations` signature haven't changed):

```python
# tests/contract/test_event_store_contract.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from nora.domain.events import (
    DuplicateEventError,
    Event,
    EventStatus,
    RSVP,
    RSVPResponse,
)
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.events import SqlEventStore
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing.events import FakeEventStore

pytestmark = pytest.mark.asyncio


def _event(**overrides) -> Event:
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    defaults = dict(
        id=uuid4(),
        conversation_id="signal:group-1",
        title="Dinner",
        created_by="miles",
        when=now + timedelta(days=3),
        venue_text=None,
        venue_place_id=None,
        venue_name=None,
        venue_address=None,
        venue_rating=None,
        venue_price_level="",
        venue_maps_url=None,
        venue_resolved_at=None,
        status=EventStatus.PROPOSED,
        reminder_id=None,
        created_at=now,
    )
    defaults.update(overrides)
    return Event(**defaults)


@pytest.fixture(params=["sql", "fake"])
async def store(request):
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s = SqlEventStore(create_sql_engine(pg_url))
    else:
        s = FakeEventStore()
    yield s
    await s.aclose()


async def test_create_then_get_round_trips(store):
    ev = _event()
    await store.create(ev)
    back = await store.get(ev.id)
    assert back is not None
    assert back.id == ev.id
    assert back.title == ev.title
    assert back.when == ev.when


async def test_get_missing_returns_none(store):
    assert await store.get(uuid4()) is None


async def test_duplicate_create_raises(store):
    ev = _event()
    await store.create(ev)
    with pytest.raises(DuplicateEventError):
        await store.create(ev)


async def test_stored_datetimes_come_back_utc_aware(store):
    ev = _event()
    await store.create(ev)
    back = await store.get(ev.id)
    assert back.when.tzinfo is not None
    assert back.created_at.tzinfo is not None


async def test_list_for_conversation_filters_by_status(store):
    proposed = _event(conversation_id="signal:group-2", status=EventStatus.PROPOSED)
    cancelled = _event(conversation_id="signal:group-2", status=EventStatus.CANCELLED)
    await store.create(proposed)
    await store.create(cancelled)

    only_proposed = await store.list_for_conversation(
        conversation_id="signal:group-2", status=EventStatus.PROPOSED,
    )
    assert [e.id for e in only_proposed] == [proposed.id]

    both = await store.list_for_conversation(conversation_id="signal:group-2")
    assert {e.id for e in both} == {proposed.id, cancelled.id}


async def test_list_for_conversation_scopes_to_conversation(store):
    here = _event(conversation_id="signal:group-3")
    elsewhere = _event(conversation_id="signal:group-4")
    await store.create(here)
    await store.create(elsewhere)

    out = await store.list_for_conversation(conversation_id="signal:group-3")
    assert [e.id for e in out] == [here.id]


async def test_update_persists_changes(store):
    ev = _event()
    await store.create(ev)
    from dataclasses import replace

    updated = replace(ev, status=EventStatus.CONFIRMED, venue_text="Soi 29")
    await store.update(updated)

    back = await store.get(ev.id)
    assert back.status == EventStatus.CONFIRMED
    assert back.venue_text == "Soi 29"


async def test_rsvp_then_rsvps_for_round_trips(store):
    ev = _event()
    await store.create(ev)
    r = RSVP(
        event_id=ev.id, person_id="miles",
        response=RSVPResponse.YES,
        responded_at=datetime(2026, 8, 19, 13, 0, tzinfo=timezone.utc),
    )
    await store.add_rsvp(r)

    back = await store.rsvps_for(ev.id)
    assert len(back) == 1
    assert back[0].person_id == "miles"
    assert back[0].response is RSVPResponse.YES


async def test_rsvp_upserts_on_same_person(store):
    ev = _event()
    await store.create(ev)
    first = datetime(2026, 8, 19, 13, 0, tzinfo=timezone.utc)
    second = datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc)

    await store.add_rsvp(
        RSVP(event_id=ev.id, person_id="miles",
             response=RSVPResponse.MAYBE, responded_at=first),
    )
    await store.add_rsvp(
        RSVP(event_id=ev.id, person_id="miles",
             response=RSVPResponse.YES, responded_at=second),
    )

    back = await store.rsvps_for(ev.id)
    assert len(back) == 1
    assert back[0].response is RSVPResponse.YES
    assert back[0].responded_at == second
```

- [ ] **Step 2: Run against the fake first (no Postgres needed)**

Run: `uv run pytest tests/contract/test_event_store_contract.py -k fake -v`
Expected: PASS (10 tests) once `on_conflict_do_update`'s test (`test_rsvp_upserts_on_same_person`) also passes for the fake — the `FakeEventStore.add_rsvp` implementation from Task 4 already upserts by dict key, so this should pass immediately; if it doesn't, fix `FakeEventStore` before touching `SqlEventStore`.

- [ ] **Step 3: Fix `SqlEventStore`'s upsert import (Task 3's flagged note) and run against Postgres**

Run: `uv run pytest tests/contract/test_event_store_contract.py -k sql -v`
Expected: PASS (10 tests). If this exposes the `sa.insert` vs `pg_insert` issue from Task 3, fix it now — this is exactly what the contract suite exists to catch.

- [ ] **Step 4: Commit**

```bash
git add tests/contract/test_event_store_contract.py nora/infra/sql/events.py
git commit -m "test(nora): add EventStore contract suite, fix RSVP upsert dialect"
```

---

### Task 6: Relocate the R-28 identifier/coordinate screen to a shared module

**Why this task exists:** `nora/agent/tools.py`'s `place_info` and `route` tools screen every place-name argument through `scan_identifiers(...)` and `_looks_like_coordinate(...)` before it reaches Google (R-28) — but `EventService` (Task 7) lives in `nora/app/events.py` and needs to apply the *exact same* screen before it resolves a venue, or a coordinate/identifier could reach Google through a second, unaudited path. These functions must be moved, not reimplemented — copying their logic by hand risks silently drifting from the audited original.

**Files:**
- Create: `nora/agent/security.py`
- Modify: `nora/agent/tools.py` (import from the new location instead of defining locally)

**Interfaces:**
- Produces: `scan_identifiers(text: str) -> <original return type>`, `_looks_like_coordinate(text: str) -> bool` (rename to `looks_like_coordinate`, dropping the leading underscore since it's now a shared public function — update every call site).

- [ ] **Step 1: Locate the current definitions**

Run: `grep -n "^def scan_identifiers\|^def _looks_like_coordinate" nora/agent/tools.py`

Read both function bodies in full (`Read nora/agent/tools.py` around the reported line numbers) before moving anything — copy them verbatim, no logic changes.

- [ ] **Step 2: Move them**

Create `nora/agent/security.py` containing the two function bodies exactly as found (adjust only the module-level imports each needs — e.g. `re`, whatever `scan_identifiers`'s return type requires). Add a short module docstring:

```python
"""R-28's identifier/coordinate egress screen. Applied at every place where
a string that might name a person or carry a raw coordinate is about to
leave the host in a tool argument (Google Places/Routes today; anything
else with the same shape later) — moved out of `nora.agent.tools` so
`nora.app.events.EventService` can apply the identical screen to a venue
string without a second, unaudited copy of this logic."""
```

- [ ] **Step 3: Update `nora/agent/tools.py`**

Delete the two function bodies from `tools.py`; add:

```python
from nora.agent.security import looks_like_coordinate as _looks_like_coordinate
from nora.agent.security import scan_identifiers
```

Every existing call site in `tools.py` (`place_info`, `route`, and any others `grep -n "_looks_like_coordinate\|scan_identifiers" nora/agent/tools.py` finds) keeps working unchanged because the local names are preserved via the `as` import.

- [ ] **Step 4: Run the existing tests that cover these guards to confirm zero behavior change**

Run: `uv run pytest tests/ -k "place_info or route or identifier or coordinate" -v`
Expected: PASS, same tests, same results as before the move — this step is verifying the relocation changed nothing observable.

- [ ] **Step 5: Type-check and commit**

```bash
uv run mypy nora/agent/security.py nora/agent/tools.py
uv run ruff check nora/agent/security.py nora/agent/tools.py
git add nora/agent/security.py nora/agent/tools.py
git commit -m "refactor(nora): relocate R-28 identifier/coordinate screen to nora.agent.security"
```

---

### Task 7: `EventService` — `nora/app/events.py`

**Files:**
- Create: `nora/app/events.py`
- Test: `tests/test_events_service.py`

**Interfaces:**
- Consumes: `EventStore`, `Event`, `RSVP`, `EventStatus`, `RSVPResponse`, `VenueCandidate`, `VenueSuggestions`, `DuplicateEventError` (Task 1); `FakeEventStore` (Task 4); `scan_identifiers`, `looks_like_coordinate` (Task 6); `ReminderStore`, `Reminder`, `ReminderState`, `ReminderCaps`, `ensure_utc` from `nora.domain.reminders`; `FakeReminderStore` from `nora.testing.reminders` (existing); `MemoryStore` from `nora.domain.ports` (its `chat` method).
- Produces: `EventService` with `create_event`, `update_event`, `rsvp`, `cancel_event`, `list_events`, `get_event`, `suggest_venue` — the exact signatures below, which Task 9's tool closures call directly.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_events_service.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from nora.app.events import EventService
from nora.domain.events import EventStatus, RSVPResponse
from nora.domain.reminders import ReminderCaps
from nora.testing.events import FakeEventStore
from nora.testing.reminders import FakeReminderStore

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


class _FakeRouteSource:
    def __init__(self, response: dict | None = None) -> None:
        self._response = response or {
            "status": "ok", "name": "Soi 29 Thai Kitchen",
            "address": "29 Soi Rd, Central", "rating": 4.5,
            "price_level": "PRICE_LEVEL_MODERATE",
            "maps_url": "https://maps.google.com/?q=soi29",
            "place_id": "ChIJfake123",
        }

    async def place_info(self, name: str) -> dict:
        return self._response


class _FakeHoncho:
    def __init__(self, answers: dict[str, str | None] | None = None) -> None:
        self._answers = answers or {}

    async def chat(self, *, peer_id, query, reasoning_level="low", target=None):
        return self._answers.get(target)


def _service(
    *, store=None, reminders=None, route_source=None, honcho=None,
) -> EventService:
    return EventService(
        store=store or FakeEventStore(),
        reminders=reminders or FakeReminderStore(),
        reminder_caps=ReminderCaps(),
        route_source=route_source or _FakeRouteSource(),
        honcho=honcho or _FakeHoncho(),
        ai_peer="nora",
        now=lambda: NOW,
    )


async def test_create_event_persists_and_returns_it():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    assert ev.title == "Dinner"
    assert ev.status is EventStatus.PROPOSED
    back = await svc.get_event(ev.id)
    assert back.id == ev.id


async def test_create_event_with_when_creates_a_linked_one_shot_reminder():
    reminders = FakeReminderStore()
    svc = _service(reminders=reminders)
    when = NOW + timedelta(days=3)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner", when=when,
    )
    assert ev.reminder_id is not None
    row = await reminders.get(ev.reminder_id)
    assert row is not None
    assert row.due_at == when
    assert row.series is None  # never a recurrence — spec §4


async def test_update_event_resolves_venue_once_via_route_source():
    route_source = _FakeRouteSource()
    calls = []
    orig = route_source.place_info

    async def counting(name):
        calls.append(name)
        return await orig(name)

    route_source.place_info = counting  # type: ignore[method-assign]

    svc = _service(route_source=route_source)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    updated = await svc.update_event(event_id=ev.id, venue_text="Soi 29")

    assert updated.venue_name == "Soi 29 Thai Kitchen"
    assert updated.venue_place_id == "ChIJfake123"
    assert updated.venue_rating == 4.5
    assert updated.venue_resolved_at == NOW
    assert calls == ["Soi 29"]  # resolved exactly once

    # Reading it back must NOT re-query the resolver.
    back = await svc.get_event(ev.id)
    assert back.venue_name == "Soi 29 Thai Kitchen"
    assert calls == ["Soi 29"]


async def test_venue_resolution_refuses_a_raw_coordinate():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    updated = await svc.update_event(
        event_id=ev.id, venue_text="22.2793,114.1628",
    )
    # Refused at the R-28 screen — venue_text is kept as what was said,
    # nothing gets resolved.
    assert updated.venue_text == "22.2793,114.1628"
    assert updated.venue_place_id is None


async def test_rsvp_records_a_response():
    svc = _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    r = await svc.rsvp(event_id=ev.id, person_id="daisy", response=RSVPResponse.YES)
    assert r.response is RSVPResponse.YES


async def test_cancel_event_cancels_its_linked_reminder():
    reminders = FakeReminderStore()
    svc = _service(reminders=reminders)
    when = NOW + timedelta(days=3)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner", when=when,
    )
    cancelled = await svc.cancel_event(event_id=ev.id)
    assert cancelled.status is EventStatus.CANCELLED

    row = await reminders.get(ev.reminder_id)
    from nora.domain.reminders import ReminderState

    assert row.state is ReminderState.CANCELLED


async def test_list_events_filters_by_status():
    svc = _service()
    a = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles", title="A",
    )
    await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles", title="B",
    )
    await svc.cancel_event(event_id=a.id)

    proposed = await svc.list_events(
        conversation_id="signal:group-1", status=EventStatus.PROPOSED,
    )
    assert [e.title for e in proposed] == ["B"]


async def test_suggest_venue_ranks_by_mention_and_fails_closed_when_empty():
    honcho = _FakeHoncho({"miles": "Soi 29", "daisy": "Soi 29", "sam": None})
    svc = _service(honcho=honcho)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    out = await svc.suggest_venue(
        event_id=ev.id, roster_person_ids=["miles", "daisy", "sam"],
    )
    assert out.status == "ok"
    assert out.candidates[0].venue_text == "Soi 29"
    assert out.candidates[0].source == "mention_frequency"
    assert out.route_hints is None  # reserved, always None — spec §5.1


async def test_suggest_venue_nothing_found_when_every_person_is_empty():
    honcho = _FakeHoncho({"miles": None, "daisy": None})
    svc = _service(honcho=honcho)
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    out = await svc.suggest_venue(
        event_id=ev.id, roster_person_ids=["miles", "daisy"],
    )
    assert out.status == "nothing_found"
    assert out.candidates == ()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_events_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nora.app.events'`

- [ ] **Step 3: Write the implementation**

```python
# nora/app/events.py
"""`EventService` — the ONE use-case layer for Event business rules (spec
§1: reminders only ever had one adapter, so its rules live inline in a
tool closure; Event has two adapters from day one, so its rules live
here, and both the agent tool and the web read routes call these same
functions rather than each re-implementing what makes an event valid).

Reuses three things that already ship rather than rebuilding them:
- the reminders domain, for an event's linked one-shot notification;
- the SAME `route_source` (`RouteClient`/`PlaceResolver`) the `place_info`
  tool already uses, for venue resolution — behind the same R-28 screen;
- Honcho's dialectic `chat()`, for `suggest_venue`'s per-person query.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Callable, Protocol
from uuid import UUID

from nora.agent.security import looks_like_coordinate, scan_identifiers
from nora.domain.events import (
    Event,
    EventStatus,
    EventStore,
    RSVP,
    RSVPResponse,
    VenueCandidate,
    VenueSuggestions,
)
from nora.domain.reminders import (
    Reminder,
    ReminderCaps,
    ReminderState,
    ReminderStore,
    ensure_utc,
)

# The fixed-shape dialectic query, spec §5 step 2 — engineered to return a
# short, directly-usable venue name (or a clear "none") rather than free
# prose that would need a separate NL-extraction step. Comparable across
# people and stable across repeat calls because the wording never varies.
_VENUE_PREFERENCE_QUERY = (
    "In one short phrase, name ONE restaurant or venue this person has "
    "mentioned positively — food preferences and dietary constraints may "
    "help you pick. Reply with just the name, or the single word 'none' "
    "if you don't know one."
)


class _RouteSource(Protocol):
    async def place_info(self, name: str) -> dict: ...


class _Honcho(Protocol):
    async def chat(
        self, *, peer_id: str, query: str, reasoning_level: str = "low",
        target: str | None = None,
    ) -> str | None: ...


class EventNotFoundError(Exception):
    def __init__(self, event_id: UUID) -> None:
        super().__init__(f"event {event_id} not found")
        self.event_id = event_id


class AmbiguousOpenEventsError(Exception):
    """More than one proposed event exists for a conversation — spec
    §3.1's fail-closed-on-ambiguity case. The caller (the `create_event`
    tool) is expected to catch this and ask which event is meant, never
    guess."""

    def __init__(self, conversation_id: str, open_events: Sequence[Event]) -> None:
        super().__init__(
            f"{len(open_events)} open events for {conversation_id}"
        )
        self.conversation_id = conversation_id
        self.open_events = tuple(open_events)


class EventService:
    def __init__(
        self,
        *,
        store: EventStore,
        reminders: ReminderStore,
        reminder_caps: ReminderCaps,
        route_source: _RouteSource | None,
        honcho: _Honcho | None,
        ai_peer: str,
        now: Callable[[], datetime] = lambda: datetime.now(tz=None),
    ) -> None:
        self._store = store
        self._reminders = reminders
        self._caps = reminder_caps
        self._route_source = route_source
        self._honcho = honcho
        self._ai_peer = ai_peer
        self._now = now

    async def create_event(
        self, *, conversation_id: str, requester_person_id: str, title: str,
        when: datetime | None = None, venue_text: str | None = None,
    ) -> Event:
        """Spec §3.1: the CALLER (the `create_event` tool) is expected to
        have already checked `list_events(status=PROPOSED)` and decided a
        new event is genuinely wanted — this method does not silently
        merge or refuse on its behalf. It DOES still raise
        `AmbiguousOpenEventsError` if two-or-more open events already
        exist, as a last-resort backstop, not the primary mechanism."""
        open_events = await self.list_events(
            conversation_id=conversation_id, status=EventStatus.PROPOSED,
        )
        if len(open_events) >= 2:
            raise AmbiguousOpenEventsError(conversation_id, open_events)

        now = self._now()
        event = Event(
            id=uuid.uuid4(),
            conversation_id=conversation_id,
            title=title,
            created_by=requester_person_id,
            when=ensure_utc(when, field="when") if when is not None else None,
            venue_text=None,
            venue_place_id=None,
            venue_name=None,
            venue_address=None,
            venue_rating=None,
            venue_price_level="",
            venue_maps_url=None,
            venue_resolved_at=None,
            status=EventStatus.PROPOSED,
            reminder_id=None,
            created_at=now,
        )
        if when is not None:
            event = await self._link_reminder(
                event, requester_person_id=requester_person_id,
                conversation_id=conversation_id,
            )
        if venue_text is not None:
            event = await self._resolve_venue(event, venue_text, now=now)
        await self._store.create(event)
        return event

    async def update_event(
        self, *, event_id: UUID, when: datetime | None = None,
        venue_text: str | None = None,
    ) -> Event:
        current = await self._require(event_id)
        updated = current
        if when is not None:
            updated = replace(updated, when=ensure_utc(when, field="when"))
            updated = await self._link_reminder(
                updated, requester_person_id=updated.created_by,
                conversation_id=updated.conversation_id,
            )
        if venue_text is not None:
            updated = await self._resolve_venue(updated, venue_text, now=self._now())
        await self._store.update(updated)
        return updated

    async def rsvp(
        self, *, event_id: UUID, person_id: str, response: RSVPResponse,
    ) -> RSVP:
        await self._require(event_id)  # 404s before writing an orphan RSVP
        row = RSVP(
            event_id=event_id, person_id=person_id, response=response,
            responded_at=self._now(),
        )
        await self._store.add_rsvp(row)
        return row

    async def cancel_event(self, *, event_id: UUID) -> Event:
        current = await self._require(event_id)
        if current.reminder_id is not None:
            await self._reminders.cancel(
                current.reminder_id, conversation_id=current.conversation_id,
            )
        updated = replace(current, status=EventStatus.CANCELLED)
        await self._store.update(updated)
        return updated

    async def list_events(
        self, *, conversation_id: str, status: EventStatus | None = None,
    ) -> Sequence[Event]:
        return await self._store.list_for_conversation(
            conversation_id=conversation_id, status=status,
        )

    async def get_event(self, *, event_id: UUID) -> Event | None:
        return await self._store.get(event_id)

    async def suggest_venue(
        self, *, event_id: UUID, roster_person_ids: Sequence[str],
    ) -> VenueSuggestions:
        await self._require(event_id)
        if self._honcho is None:
            return VenueSuggestions(status="unavailable")

        # spec §5 step 2: one fixed-shape query per mapped person.
        mentions: dict[str, int] = {}
        for person_id in roster_person_ids:
            answer = await self._honcho.chat(
                peer_id=self._ai_peer, query=_VENUE_PREFERENCE_QUERY,
                target=person_id,
            )
            if not answer:
                continue
            name = answer.strip()
            if not name or name.lower() == "none":
                continue
            mentions[name] = mentions.get(name, 0) + 1

        if not mentions:
            return VenueSuggestions(status="nothing_found")

        ranked = sorted(mentions.items(), key=lambda kv: kv[1], reverse=True)
        candidates: list[VenueCandidate] = []
        for venue_text, count in ranked[:3]:
            resolved = await self._resolve_place(venue_text)
            candidates.append(
                VenueCandidate(
                    venue_text=venue_text,
                    place_id=resolved.get("place_id") if resolved else None,
                    name=resolved.get("name") if resolved else None,
                    address=resolved.get("address") if resolved else None,
                    rating=resolved.get("rating") if resolved else None,
                    price_level=(resolved or {}).get("price_level", ""),
                    maps_url=resolved.get("maps_url") if resolved else None,
                    source=(
                        "explicit_callback" if count > 1 else "mention_frequency"
                    ),
                )
            )
        return VenueSuggestions(status="ok", candidates=tuple(candidates))

    # ── internals ────────────────────────────────────────────────────────

    async def _require(self, event_id: UUID) -> Event:
        event = await self._store.get(event_id)
        if event is None:
            raise EventNotFoundError(event_id)
        return event

    async def _link_reminder(
        self, event: Event, *, requester_person_id: str, conversation_id: str,
    ) -> Event:
        """One-shot reminder only — Event never has a recurrence concept
        (Global Constraints). Respects the SAME `ReminderCaps` a manually
        requested reminder would, so an event cannot bypass the anti-runaway
        limits `remind` enforces (`min_lead_seconds`, `max_horizon_days`)."""
        assert event.when is not None
        now = self._now()
        lead = (event.when - now).total_seconds()
        horizon = (event.when - now).days
        if lead < self._caps.min_lead_seconds or horizon > self._caps.max_horizon_days:
            # Out of bounds — the event itself is still created/updated;
            # it simply has no reminder. R-5: never silently pretend one
            # was set.
            return replace(event, reminder_id=None)

        reminder_id = uuid.uuid4()
        row = Reminder(
            id=reminder_id,
            requester_person_id=requester_person_id,
            platform="event",
            conversation_id=conversation_id,
            conversation_type="group",
            text=f"Event: {event.title}",
            created_at=now,
            due_at=event.when,
            state=ReminderState.PENDING,
            fired_at=None,
            outcome=None,
            series=None,
        )
        await self._reminders.create(row)
        return replace(event, reminder_id=reminder_id)

    async def _resolve_venue(
        self, event: Event, venue_text: str, *, now: datetime,
    ) -> Event:
        resolved = await self._resolve_place(venue_text)
        if resolved is None:
            return replace(event, venue_text=venue_text)
        return replace(
            event,
            venue_text=venue_text,
            venue_place_id=resolved.get("place_id"),
            venue_name=resolved.get("name"),
            venue_address=resolved.get("address"),
            venue_rating=resolved.get("rating"),
            venue_price_level=resolved.get("price_level", ""),
            venue_maps_url=resolved.get("maps_url"),
            venue_resolved_at=now,
        )

    async def _resolve_place(self, text: str) -> dict | None:
        """The R-28 screen, THEN the same resolver `place_info` calls —
        applied here because `EventService` reaches Google through a
        second path and must carry the identical guard (Task 6)."""
        if scan_identifiers(text) is not None or looks_like_coordinate(text):
            return None
        if self._route_source is None:
            return None
        out = await self._route_source.place_info(text)
        if out.get("status") != "ok":
            return None
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_events_service.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Type-check and commit**

```bash
uv run mypy nora/app/events.py
uv run ruff check nora/app/events.py tests/test_events_service.py
git add nora/app/events.py tests/test_events_service.py
git commit -m "feat(nora): add EventService use-case layer"
```

---

### Task 8: Wire `EventService` into `nora/app/composition.py`

**Files:**
- Modify: `nora/app/composition.py`

**Interfaces:**
- Consumes: `SqlEventStore` (Task 3), `EventService` (Task 7), the already-built `route_client`/`reminders`/`reminder_caps`/`cfg.honcho`-backed Honcho client, `ai_peer` string — all already constructed elsewhere in this file per the earlier research (route_client at composition.py:698-708; reminders/reminder_caps built from `cfg.reminders` nearby; a Honcho client and `ai_peer` id already threaded to `ToolRegistry`).

- [ ] **Step 1: Locate the existing collaborators**

Run: `grep -n "route_client\|reminder_caps\|ReminderCaps(\|ai_peer\|honcho" nora/app/composition.py | head -40`

Confirm the exact local variable names for the already-built `route_client`, `reminders` (a `ReminderStore`), `reminder_caps`, the Honcho `MemoryStore` instance, and `ai_peer` — use those exact names in Step 2 rather than inventing new ones.

- [ ] **Step 2: Construct `SqlEventStore` + `EventService`**

Immediately after wherever `reminder_caps`/`route_client` are built (same section — Event's collaborators are all things this file already has in scope), add:

```python
from nora.app.events import EventService
from nora.infra.sql.events import SqlEventStore

event_store = SqlEventStore(sql_engine)  # reuse the SAME engine reminders/messages use
events = EventService(
    store=event_store,
    reminders=reminders,
    reminder_caps=reminder_caps,
    route_source=route_client,
    honcho=honcho,          # the same MemoryStore instance already built
    ai_peer=ai_peer,        # the same id already threaded elsewhere
)
```

(Confirm `sql_engine`'s exact local name the same way as Step 1 — it is whatever `SqlReminderStore(...)` and similar stores are already constructed with in this file.)

- [ ] **Step 3: Pass `events` into `ToolRegistry`**

Find the `ToolRegistry(...)` construction call (`grep -n "ToolRegistry(" nora/app/composition.py`) and add `events=events,` as a new keyword argument alongside the existing `reminders=reminders, route=route_client, ...`.

- [ ] **Step 4: Verify it imports and type-checks**

Run: `uv run mypy nora/app/composition.py`
Expected: fails at this step until Task 9 adds `events` to `ToolRegistry.__init__` — that's expected; note it and proceed directly to Task 9 before attempting to run this in isolation. (If executing tasks via subagent-driven-development, land Task 8 and Task 9 as one reviewed unit for this reason.)

- [ ] **Step 5: Commit** (after Task 9 makes this type-check clean)

```bash
git add nora/app/composition.py
git commit -m "feat(nora): wire EventService into composition"
```

---

### Task 9: Six agent tools — `nora/agent/tools.py` + `nora/agent/registry.py`

**Files:**
- Modify: `nora/agent/tools.py`
- Modify: `nora/agent/registry.py`

**Interfaces:**
- Consumes: `EventService` (Task 7), `EventStatus`, `RSVPResponse`, `AmbiguousOpenEventsError`, `EventNotFoundError` (Task 7/1); the existing `NativeTool`, `NATIVE_TOOLS`, `_spec`, `build_tool_specs` machinery; the existing `_parse_when` helper `remind` already uses; `Room`/`RoomView`/`PersonRef` from `nora.agent.room`/`nora.domain.models`.

- [ ] **Step 1: Add `NATIVE_TOOLS` entries**

In `nora/agent/tools.py`, near the existing `remind`/`list_reminders`/`cancel_reminder`/`place_info` entries, add:

```python
"create_event": NativeTool(3, "somebody is proposing a gathering — dinner, an "
                            "outing — with a title and optionally a time and a "
                            "venue. Check list_events for an ALREADY-OPEN "
                            "proposed event in this conversation first — this "
                            "is for a genuinely NEW gathering, not an update to "
                            "one already being planned"),
"update_event": NativeTool(2, "new information about an event already being "
                            "planned in this conversation — a time now settled, "
                            "a venue now settled. Use the id list_events gave "
                            "you"),
"rsvp_event": NativeTool(1, "somebody says whether they are coming — yes, no, "
                          "or maybe — to an event already proposed here"),
"cancel_event": NativeTool(2, "an event already proposed here is called off"),
"list_events": NativeTool(1, "what gatherings are proposed or confirmed in "
                          "THIS conversation — call this BEFORE create_event "
                          "to check whether one is already open"),
"suggest_venue": NativeTool(4, "somebody wants a venue recommendation for an "
                             "event already proposed here, drawn from what "
                             "people have said they like"),
```

- [ ] **Step 2: Add the `events` parameter to `build_tool_specs`**

At the signature found in research (`nora/agent/tools.py:1236`), add `events: EventService | None = None,` alongside the existing `reminders: ReminderStore | None = None,` parameter, and add the import at the top of the file:

```python
from nora.app.events import (
    AmbiguousOpenEventsError,
    EventNotFoundError,
    EventService,
)
from nora.domain.events import EventStatus, RSVPResponse
```

- [ ] **Step 3: Write the six tool closures**

Add these inside `build_tool_specs`, near the `remind`/`list_reminders`/`cancel_reminder` closures (same function, same nesting level — they need the same `room`, `tagger`, `metrics` closure variables those already use):

```python
    async def list_events(status: str = "") -> dict:
        """What gatherings are proposed or confirmed in THIS conversation.
        Call this BEFORE create_event — an existing open event should be
        updated (update_event), not duplicated.

        Args:
            status: Optional filter — "proposed", "confirmed", or
                "cancelled". Leave empty for everything.
        """
        if events is None:
            return {"status": "unavailable", "note": "Events aren't set up here."}
        parsed_status = EventStatus(status) if status else None
        out = await events.list_events(
            conversation_id=room.conversation_id, status=parsed_status,
        )
        return {
            "status": "ok",
            "events": [
                {
                    "id": str(e.id), "title": e.title,
                    "when": e.when.isoformat() if e.when else None,
                    "venue": e.venue_name or e.venue_text,
                    "status": e.status.value,
                }
                for e in out
            ],
        }

    async def create_event(
        title: str, when: str = "", venue_text: str = "",
    ) -> dict:
        """Propose a NEW gathering in this conversation. Call list_events
        FIRST — if one is already open (status "proposed"), call
        update_event against it instead of creating a second one.

        Outcomes: `ok` with the id; `refused` with a note (two or more
        events are already open here — ask which one is meant, do not
        guess which); `unavailable` when events aren't configured.

        Args:
            title: What the gathering is, in their words.
            when: Optional. ISO-8601 with an offset, the first moment it's
                for. Resolve relative time the same way `remind` does.
            venue_text: Optional. The place, the way it was said.
        """
        if events is None:
            return {"status": "unavailable", "note": "Events aren't set up here."}
        if tagger is None:
            return {
                "status": "refused",
                "note": "I don't know who you are well enough to start this.",
            }
        due_at = _parse_when(when) if when else None
        if when and due_at is None:
            return {"status": "refused", "note": "I couldn't work out that time."}
        try:
            ev = await events.create_event(
                conversation_id=room.conversation_id,
                requester_person_id=tagger.person_id,
                title=title, when=due_at,
                venue_text=venue_text or None,
            )
        except AmbiguousOpenEventsError:
            return {
                "status": "refused",
                "note": "There's more than one open event here already — "
                        "which one do you mean? Use list_events to check.",
            }
        return {"status": "ok", "id": str(ev.id)}

    async def update_event(
        event_id: str, when: str = "", venue_text: str = "",
    ) -> dict:
        """Update an event ALREADY proposed here — a time now settled, a
        venue now settled. Use the id list_events gave you.

        Args:
            event_id: The event's id, from list_events.
            when: Optional. ISO-8601 with an offset.
            venue_text: Optional. The place, the way it was said.
        """
        if events is None:
            return {"status": "unavailable", "note": "Events aren't set up here."}
        due_at = _parse_when(when) if when else None
        if when and due_at is None:
            return {"status": "refused", "note": "I couldn't work out that time."}
        try:
            ev = await events.update_event(
                event_id=UUID(event_id), when=due_at,
                venue_text=venue_text or None,
            )
        except EventNotFoundError:
            return {"status": "refused", "note": "I don't know that event."}
        except ValueError:
            return {"status": "refused", "note": "That doesn't look like an event id."}
        return {
            "status": "ok", "id": str(ev.id),
            "venue": ev.venue_name or ev.venue_text,
        }

    async def rsvp_event(event_id: str, response: str) -> dict:
        """Record whether the person talking now is coming to an event
        already proposed here.

        Args:
            event_id: The event's id, from list_events.
            response: "yes", "no", or "maybe".
        """
        if events is None:
            return {"status": "unavailable", "note": "Events aren't set up here."}
        if tagger is None:
            return {"status": "refused", "note": "I don't know who you are."}
        try:
            await events.rsvp(
                event_id=UUID(event_id), person_id=tagger.person_id,
                response=RSVPResponse(response),
            )
        except EventNotFoundError:
            return {"status": "refused", "note": "I don't know that event."}
        except ValueError:
            return {"status": "refused", "note": "That's not yes, no, or maybe."}
        return {"status": "ok"}

    async def cancel_event(event_id: str) -> dict:
        """Call off an event already proposed here.

        Args:
            event_id: The event's id, from list_events.
        """
        if events is None:
            return {"status": "unavailable", "note": "Events aren't set up here."}
        try:
            ev = await events.cancel_event(event_id=UUID(event_id))
        except EventNotFoundError:
            return {"status": "refused", "note": "I don't know that event."}
        except ValueError:
            return {"status": "refused", "note": "That doesn't look like an event id."}
        return {"status": "ok", "id": str(ev.id)}

    async def suggest_venue(event_id: str) -> dict:
        """Suggest a venue for an event already proposed here, drawn from
        what people in this conversation have said they like.

        Outcomes: `ok` with `candidates` (name, address, rating,
        price_level, maps_url, and why it was suggested); `nothing_found`
        when nobody's said anything usable — say so, don't invent a pick;
        `unavailable` when this isn't configured.

        Args:
            event_id: The event's id, from list_events.
        """
        if events is None:
            return {"status": "unavailable", "note": "Events aren't set up here."}
        try:
            roster = await room.members()
        except Exception:
            roster = None
        if roster is None or not roster.available:
            return {
                "status": "unavailable",
                "note": "I couldn't read who's in this conversation right now.",
            }
        try:
            out = await events.suggest_venue(
                event_id=UUID(event_id),
                roster_person_ids=[p.person_id for p in roster.people],
            )
        except EventNotFoundError:
            return {"status": "refused", "note": "I don't know that event."}
        except ValueError:
            return {"status": "refused", "note": "That doesn't look like an event id."}
        return {
            "status": out.status,
            "candidates": [
                {
                    "venue_text": c.venue_text, "name": c.name,
                    "address": c.address, "rating": c.rating,
                    "price_level": c.price_level, "maps_url": c.maps_url,
                    "why": c.source,
                }
                for c in out.candidates
            ],
        }
```

Add `from uuid import UUID` at the top of `nora/agent/tools.py` if not already imported.

- [ ] **Step 4: Register the six specs**

Find the tuple of `_spec(...)` calls (`nora/agent/tools.py:3685` per research) and add:

```python
        _spec("list_events", list_events),
        _spec("create_event", create_event),
        _spec("update_event", update_event),
        _spec("rsvp_event", rsvp_event),
        _spec("cancel_event", cancel_event),
        _spec("suggest_venue", suggest_venue),
```

- [ ] **Step 5: Add `events` to `ToolRegistry`**

In `nora/agent/registry.py`'s `__init__` (line 78-108 per research), add `events: EventService | None = None,` to the constructor's keyword-only parameters, store it as `self._events = events`, and pass `events=self._events,` into the `build_tool_specs(...)` call inside this class.

- [ ] **Step 6: Verify composition + registry now type-check together (closes out Task 8)**

Run: `uv run mypy nora/agent/registry.py nora/agent/tools.py nora/app/composition.py`
Expected: no new errors.

- [ ] **Step 7: Write a smoke test for one tool end-to-end through `build_tool_specs`**

```python
# tests/test_event_tools.py (this file also gets the doctrine test in Task 10 — start it now)
from __future__ import annotations

import pytest

from nora.agent.tools import NATIVE_TOOLS


def test_all_six_event_tools_are_registered():
    for name in (
        "create_event", "update_event", "rsvp_event",
        "cancel_event", "list_events", "suggest_venue",
    ):
        assert name in NATIVE_TOOLS, f"{name} missing from NATIVE_TOOLS"
```

Run: `uv run pytest tests/test_event_tools.py -v`
Expected: PASS

- [ ] **Step 8: Full gate + commit**

```bash
uv run pytest -q
uv run ruff check nora/ evals/ tests/
uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
uv run lint-imports
git add nora/agent/tools.py nora/agent/registry.py nora/app/composition.py tests/test_event_tools.py
git commit -m "feat(nora): add create_event/update_event/rsvp_event/cancel_event/list_events/suggest_venue tools"
```

---

### Task 10: The open-event doctrine — acceptance rule, R-52, DEC-117

**Files:**
- Modify: `tests/test_event_tools.py`
- Modify: `tests/test_acceptance_rules.py`
- Modify: `design/09-decision-index.md`

**Interfaces:**
- Consumes: `AmbiguousOpenEventsError` (Task 7), `EventService` (Task 7), `FakeEventStore` (Task 4).

- [ ] **Step 1: Write the named acceptance test**

Append to `tests/test_event_tools.py`:

```python
async def test_r52_two_open_events_refuses_a_third_rather_than_guessing():
    """R-52: create_event fail-closes on ambiguity — spec §3.1's
    'more than one open event' case — rather than silently picking one or
    creating a duplicate."""
    from datetime import datetime, timezone

    from nora.app.events import AmbiguousOpenEventsError, EventService
    from nora.domain.reminders import ReminderCaps
    from nora.testing.events import FakeEventStore
    from nora.testing.reminders import FakeReminderStore

    svc = EventService(
        store=FakeEventStore(), reminders=FakeReminderStore(),
        reminder_caps=ReminderCaps(), route_source=None, honcho=None,
        ai_peer="nora",
        now=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
    )
    await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles", title="A",
    )
    await svc._store.update(  # force a SECOND open event without going
        # through create_event's own guard, to set up the ambiguous state
        await svc.create_event(
            conversation_id="signal:group-1", requester_person_id="miles",
            title="B",
        )
    )
    with pytest.raises(AmbiguousOpenEventsError):
        await svc.create_event(
            conversation_id="signal:group-1", requester_person_id="miles",
            title="C",
        )
```

(The `svc._store.update(...)` no-op line is a harmless way to keep the test self-contained without adding a bypass method to `EventService`; if this reads awkwardly in review, simplify to two direct `await svc.create_event(...)` calls followed by the third that's expected to raise — the two-events-then-a-third shape is what matters, not this exact construction.)

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_event_tools.py::test_r52_two_open_events_refuses_a_third_rather_than_guessing -v`
Expected: PASS (it should already pass, given Task 7's `create_event` — this step is confirming the rule is actually enforced, not just asserted in a docstring)

- [ ] **Step 3: Add the `RULE_TESTS` entry**

In `tests/test_acceptance_rules.py`, add to the `RULE_TESTS` dict (alphabetical/numeric position near the other `R-5x` entries):

```python
"R-52": "test_r52_two_open_events_refuses_a_third_rather_than_guessing",
```

Run: `uv run pytest tests/test_acceptance_rules.py::test_every_enforced_rule_has_a_named_test -v`
Expected: PASS

- [ ] **Step 4: Add the decision-index entry**

In `design/09-decision-index.md`, add a new row (next free id `DEC-117`, per the existing table format):

```
| DEC-117 | An event's `create_event` checks for an already-open (`proposed`) event in the same conversation and refuses to create a duplicate when two or more are already open, asking which is meant rather than guessing (spec docs/superpowers/specs/2026-08-19-event-module-design.md §3.1). One open event is expected to be *updated* via `update_event`, a model decision this rule does not automate. | `test_r52_two_open_events_refuses_a_third_rather_than_guessing` (R-52) |
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_event_tools.py tests/test_acceptance_rules.py design/09-decision-index.md
git commit -m "test(nora): add R-52 open-event doctrine rule + DEC-117 entry"
```

---

### Task 11: Web backend read routes — `nora/web/events.py` + `nora/web/api.py`

**Files:**
- Create: `nora/web/events.py`
- Modify: `nora/web/api.py`

**Interfaces:**
- Consumes: `EventService` (Task 7), `Event`/`RSVP` (Task 1).
- Produces: `build_events_payload(*, events: EventService, conversation_id: str) -> list[dict]`, `build_event_payload(*, events: EventService, event_id: UUID) -> dict | None`.

- [ ] **Step 1: Write the payload builders**

```python
# nora/web/events.py
"""JSON payload builders for the read-only Event web surface. Plain async
functions over typed collaborators, matching `nora.web.memory`'s shape
(`build_memory_payload`) rather than `nora.web.games`'s remote-client-wrapper
shape — Event's data is local Postgres via `EventService`, not a remote
REST API."""

from __future__ import annotations

from uuid import UUID

from nora.app.events import EventService


def _event_dict(event) -> dict:
    return {
        "id": str(event.id),
        "title": event.title,
        "when": event.when.isoformat() if event.when else None,
        "venue": {
            "text": event.venue_text,
            "name": event.venue_name,
            "address": event.venue_address,
            "rating": event.venue_rating,
            "price_level": event.venue_price_level,
            "maps_url": event.venue_maps_url,
        },
        "status": event.status.value,
        "created_by": event.created_by,
        "created_at": event.created_at.isoformat(),
    }


async def build_events_payload(
    *, events: EventService, conversation_id: str,
) -> list[dict]:
    out = await events.list_events(conversation_id=conversation_id)
    return [_event_dict(e) for e in out]


async def build_event_payload(
    *, events: EventService, event_id: UUID,
) -> dict | None:
    event = await events.get_event(event_id=event_id)
    if event is None:
        return None
    rsvps = await events._store.rsvps_for(event_id)  # read-only projection
    payload = _event_dict(event)
    payload["rsvps"] = [
        {"person_id": r.person_id, "response": r.response.value}
        for r in rsvps
    ]
    return payload
```

**Note**: `build_event_payload` reaches into `events._store` directly for RSVPs because `EventService` has no public `rsvps_for` passthrough yet. Add one instead of reaching past the underscore — amend `nora/app/events.py` (Task 7) with:

```python
    async def rsvps_for(self, *, event_id: UUID) -> Sequence[RSVP]:
        return await self._store.rsvps_for(event_id)
```

and update `build_event_payload` to call `await events.rsvps_for(event_id=event_id)` instead.

- [ ] **Step 2: Register the aiohttp routes**

In `nora/web/api.py`, find `register_web_routes(app, *, store, honcho, reminders, persons, ai_peer, started_at, games_client, presence, presence_circles, skills_dir)` (line 113 per research) and:

1. Add `events: EventService,` to its parameter list.
2. Store it: `app["web_events"] = events` (matching however the existing collaborators get stored — confirm the exact pattern with `grep -n 'app\["web_' nora/web/api.py` before writing this line).
3. Add two handlers and register them:

```python
async def events_list(request: web.Request) -> web.Response:
    conversation_id = request.query.get("conversation_id")
    if not conversation_id:
        return web.json_response(
            {"status": "refused", "note": "conversation_id is required"},
            status=400,
        )
    body = await build_events_payload(
        events=request.app["web_events"], conversation_id=conversation_id,
    )
    return web.json_response(body)


async def events_detail(request: web.Request) -> web.Response:
    from uuid import UUID

    try:
        event_id = UUID(request.match_info["id"])
    except ValueError:
        return web.json_response({"status": "refused", "note": "bad id"}, status=400)
    body = await build_event_payload(
        events=request.app["web_events"], event_id=event_id,
    )
    if body is None:
        return web.json_response({"status": "not_found"}, status=404)
    return web.json_response(body)
```

```python
    app.router.add_get("/app/v1/events", events_list)
    app.router.add_get("/app/v1/events/{id}", events_detail)
```

(Place this registration alongside the existing games/memory route registrations in the same function, and add `from nora.web.events import build_event_payload, build_events_payload` to the file's imports.)

- [ ] **Step 3: Thread `events` through the caller of `register_web_routes`**

Find where `register_web_routes(...)` is actually called (likely `nora/app/composition.py` or `nora/app/__main__.py` — `grep -rn "register_web_routes(" nora/`) and add `events=events,` to that call, using the `events` object Task 8 already built.

- [ ] **Step 4: Write a route-level test**

```python
# tests/test_web_events.py
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from nora.app.events import EventService
from nora.domain.reminders import ReminderCaps
from nora.testing.events import FakeEventStore
from nora.testing.reminders import FakeReminderStore
from nora.web.events import build_event_payload, build_events_payload

pytestmark = pytest.mark.asyncio


async def _service() -> EventService:
    return EventService(
        store=FakeEventStore(), reminders=FakeReminderStore(),
        reminder_caps=ReminderCaps(), route_source=None, honcho=None,
        ai_peer="nora",
        now=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
    )


async def test_build_events_payload_lists_events_for_conversation():
    svc = await _service()
    await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    out = await build_events_payload(events=svc, conversation_id="signal:group-1")
    assert len(out) == 1
    assert out[0]["title"] == "Dinner"
    assert out[0]["status"] == "proposed"


async def test_build_event_payload_returns_none_for_missing_event():
    from uuid import uuid4

    svc = await _service()
    assert await build_event_payload(events=svc, event_id=uuid4()) is None


async def test_build_event_payload_includes_rsvps():
    from nora.domain.events import RSVPResponse

    svc = await _service()
    ev = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner",
    )
    await svc.rsvp(event_id=ev.id, person_id="daisy", response=RSVPResponse.YES)

    out = await build_event_payload(events=svc, event_id=ev.id)
    assert out["rsvps"] == [{"person_id": "daisy", "response": "yes"}]
```

Run: `uv run pytest tests/test_web_events.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Full gate + commit**

```bash
uv run pytest -q
uv run mypy nora/web/events.py nora/web/api.py nora/app/composition.py nora/app/events.py
uv run lint-imports
git add nora/web/events.py nora/web/api.py nora/app/events.py tests/test_web_events.py
git commit -m "feat(nora): add read-only GET /app/v1/events(/{id})"
```

---

### Task 12: Next.js proxy routes

**Files:**
- Create: `app/src/lib/events-proxy.ts`
- Create: `app/src/app/api/events/route.ts`
- Create: `app/src/app/api/events/[id]/route.ts`

**Interfaces:**
- Consumes: `requireApiSession()` from `app/src/lib/require-api-session.ts` (existing); mirrors `app/src/lib/games-proxy.ts`'s `proxyGamesGet` exactly, per the games-hub research (group-scoped reads, no `personId` filtering needed — same shape as `games/servers/[id]/status/route.ts`, not `memory/route.ts`).

- [ ] **Step 1: Write the proxy helper**

Read `app/src/lib/games-proxy.ts` in full first, then mirror it:

```typescript
// app/src/lib/events-proxy.ts
const NORA_WEB_BASE = process.env.NORA_WEB_BASE ?? "http://127.0.0.1:9100";

export async function proxyEventsGet(
  path: string,
  search?: URLSearchParams,
): Promise<Response> {
  const url = new URL(`/app/v1/events${path}`, NORA_WEB_BASE);
  if (search) {
    url.search = search.toString();
  }
  const res = await fetch(url, { cache: "no-store" });
  const body = await res.text();
  return new Response(body, {
    status: res.status,
    headers: { "content-type": "application/json" },
  });
}
```

(If `games-proxy.ts` uses a different base-URL env var name or a shared `fetch` wrapper, use THAT exact name instead of `NORA_WEB_BASE` — confirm before writing this file.)

- [ ] **Step 2: Write the list route**

```typescript
// app/src/app/api/events/route.ts
import { NextRequest } from "next/server";
import { requireApiSession } from "@/lib/require-api-session";
import { proxyEventsGet } from "@/lib/events-proxy";

export async function GET(request: NextRequest) {
  const denied = await requireApiSession();
  if (denied) return denied;

  const conversationId = request.nextUrl.searchParams.get("conversation_id");
  const search = new URLSearchParams();
  if (conversationId) search.set("conversation_id", conversationId);
  return proxyEventsGet("", search);
}
```

(Confirm `requireApiSession()`'s exact return-type contract — research found it returns "a 401-or-null gate"; adjust the `if (denied) return denied;` line to match its actual signature, e.g. it may return a `NextResponse | null` directly usable as a Response, or require wrapping.)

- [ ] **Step 3: Write the detail route**

```typescript
// app/src/app/api/events/[id]/route.ts
import { requireApiSession } from "@/lib/require-api-session";
import { proxyEventsGet } from "@/lib/events-proxy";

export async function GET(
  _request: Request,
  { params }: { params: { id: string } },
) {
  const denied = await requireApiSession();
  if (denied) return denied;

  return proxyEventsGet(`/${params.id}`);
}
```

- [ ] **Step 4: Manual smoke test**

Run the dev server (`cd app && npm run dev` or whatever this project's existing `run` skill/script is) and hit `GET /api/events?conversation_id=signal:group-1` and `GET /api/events/<some-uuid>` — confirm a 401 unauthenticated, and a real JSON body once signed in against a Nora backend with at least one seeded event.

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/events-proxy.ts app/src/app/api/events/route.ts app/src/app/api/events/[id]/route.ts
git commit -m "feat(app): proxy GET /api/events(/[id]) to Nora"
```

---

### Task 13: `events-schema.ts` + web pages

**Files:**
- Create: `app/src/lib/events-schema.ts`
- Create: `app/src/app/[locale]/(portal)/events/page.tsx`
- Create: `app/src/app/[locale]/(portal)/events/[id]/page.tsx`

**Interfaces:**
- Mirrors `app/src/lib/games-schema.ts`'s `fetchServers()` pattern and `app/src/app/[locale]/(portal)/games/page.tsx`'s client-component structure exactly (per games-hub research: `"use client"`, `useTranslations`/`useLocale`, `useEffect` + `useState` fetch, `Card`/`Badge` from `components/ui/*`).

- [ ] **Step 1: Read the two precedent files in full**

`Read app/src/lib/games-schema.ts` and `Read app/src/app/[locale]/(portal)/games/page.tsx` before writing anything — the code below assumes their exact shape from research; confirm field/import names against the live files first.

- [ ] **Step 2: Write the schema + fetch helpers**

```typescript
// app/src/lib/events-schema.ts
import { z } from "zod";

const VenueSchema = z.object({
  text: z.string().nullable(),
  name: z.string().nullable(),
  address: z.string().nullable(),
  rating: z.number().nullable(),
  price_level: z.string(),
  maps_url: z.string().nullable(),
});

const EventSchema = z.object({
  id: z.string(),
  title: z.string(),
  when: z.string().nullable(),
  venue: VenueSchema,
  status: z.enum(["proposed", "confirmed", "cancelled"]),
  created_by: z.string(),
  created_at: z.string(),
});

const EventListSchema = z.array(EventSchema);

const RsvpSchema = z.object({
  person_id: z.string(),
  response: z.enum(["yes", "no", "maybe"]),
});

const EventDetailSchema = EventSchema.extend({
  rsvps: z.array(RsvpSchema),
});

export type Event = z.infer<typeof EventSchema>;
export type EventDetail = z.infer<typeof EventDetailSchema>;

export async function fetchEvents(conversationId: string): Promise<Event[]> {
  const res = await fetch(
    `/api/events?conversation_id=${encodeURIComponent(conversationId)}`,
    { cache: "no-store" },
  );
  if (!res.ok) throw new Error(`fetchEvents failed: ${res.status}`);
  return EventListSchema.parse(await res.json());
}

export async function fetchEvent(id: string): Promise<EventDetail> {
  const res = await fetch(`/api/events/${encodeURIComponent(id)}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`fetchEvent failed: ${res.status}`);
  return EventDetailSchema.parse(await res.json());
}
```

- [ ] **Step 3: Write the list page**

**Scope decision, made here rather than left open**: the games hub has no
per-group concept to copy (Pulse's game servers are global, not
conversation-scoped), and a person can belong to more than one group, so
there is no existing "current conversation" selector anywhere in the app
to reuse. Rather than fabricate a global group-picker component (real
scope, not this plan's — Event's spec never asked for one), this page
reads `conversation_id` from the URL query string, the standard
Next.js mechanism for exactly this — `/events?conversation_id=...` — and
shows an explicit empty state asking for one when it's absent, instead of
silently defaulting to a made-up group. A proper group switcher (reading
the signed-in person's known conversations) is follow-up work outside
this plan.

```tsx
// app/src/app/[locale]/(portal)/events/page.tsx
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchEvents, type Event } from "@/lib/events-schema";

export default function EventsPage() {
  const t = useTranslations("events");
  const conversationId = useSearchParams().get("conversation_id");
  const [events, setEvents] = useState<Event[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!conversationId) return;
    fetchEvents(conversationId)
      .then(setEvents)
      .catch((e) => setError(String(e)));
  }, [conversationId]);

  if (!conversationId) {
    return <p className="p-6 text-muted-foreground">{t("pickConversation")}</p>;
  }

  return (
    <div className="flex flex-col gap-4 p-6">
      <div>
        <p className="text-sm uppercase tracking-wide text-muted-foreground">
          {t("eyebrow")}
        </p>
        <h1 className="text-2xl font-semibold">{t("heading")}</h1>
        <p className="text-muted-foreground">{t("dek")}</p>
      </div>

      {error && <p className="text-destructive">{t("error")}</p>}
      {!error && events === null && <p>{t("loading")}</p>}
      {events?.length === 0 && <p>{t("empty")}</p>}

      <div className="grid gap-3">
        {events?.map((ev) => (
          <Link key={ev.id} href={`/events/${ev.id}`}>
            <Card className="p-4 flex items-center justify-between">
              <div>
                <p className="font-medium">{ev.title}</p>
                <p className="text-sm text-muted-foreground">
                  {ev.venue.name ?? ev.venue.text ?? t("noVenue")}
                  {ev.when ? ` · ${new Date(ev.when).toLocaleString()}` : ""}
                </p>
              </div>
              <Badge variant={ev.status === "cancelled" ? "outline" : "default"}>
                {ev.status}
              </Badge>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Write the detail page**

```tsx
// app/src/app/[locale]/(portal)/events/[id]/page.tsx
"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchEvent, type EventDetail } from "@/lib/events-schema";

export default function EventDetailPage() {
  const t = useTranslations("events");
  const params = useParams<{ id: string }>();
  const [event, setEvent] = useState<EventDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchEvent(params.id)
      .then(setEvent)
      .catch((e) => setError(String(e)));
  }, [params.id]);

  if (error) return <p className="p-6 text-destructive">{t("error")}</p>;
  if (!event) return <p className="p-6">{t("loading")}</p>;

  return (
    <div className="flex flex-col gap-4 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{event.title}</h1>
        <Badge variant={event.status === "cancelled" ? "outline" : "default"}>
          {event.status}
        </Badge>
      </div>

      <Card className="p-4">
        <p className="font-medium">{t("venue")}</p>
        <p>{event.venue.name ?? event.venue.text ?? t("noVenue")}</p>
        {event.venue.address && (
          <p className="text-sm text-muted-foreground">{event.venue.address}</p>
        )}
        {event.venue.rating != null && (
          <p className="text-sm text-muted-foreground">
            {t("rating", { rating: event.venue.rating })}
          </p>
        )}
        {event.venue.maps_url && (
          <a
            href={event.venue.maps_url}
            target="_blank"
            rel="noreferrer"
            className="text-sm underline"
          >
            {t("openInMaps")}
          </a>
        )}
      </Card>

      <Card className="p-4">
        <p className="font-medium">{t("rsvps")}</p>
        {event.rsvps.length === 0 && (
          <p className="text-sm text-muted-foreground">{t("noRsvps")}</p>
        )}
        <ul className="flex flex-col gap-1">
          {event.rsvps.map((r) => (
            <li key={r.person_id} className="flex justify-between text-sm">
              <span>{r.person_id}</span>
              <Badge variant="outline">{r.response}</Badge>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
```

- [ ] **Step 5: Manual verification in the browser**

Per this project's `run` skill: launch the app, sign in, navigate to `/events`, confirm the list renders (or the empty state, if no events are seeded yet), click into a detail page, confirm RSVPs and venue data render. Confirm the loading and error states by temporarily pointing `NORA_WEB_BASE` at an unreachable port and reloading.

- [ ] **Step 6: Commit**

```bash
git add app/src/lib/events-schema.ts app/src/app/[locale]/(portal)/events/
git commit -m "feat(app): add read-only Events list + detail pages"
```

---

### Task 14: Nav wiring

**Files:**
- Modify: `app/src/lib/modules.ts`
- Modify: `app/src/components/nav-links.tsx`
- Modify: `app/src/messages/en.json`

- [ ] **Step 1: Extend the `Module.labelKey` union and `MODULES` array**

In `app/src/lib/modules.ts`, extend the union `"status" | "memory" | "games" | "map"` to include `"events"`, and add to the `MODULES` array (matching the existing entries' shape):

```typescript
{ id: "events", labelKey: "events", href: "/events", status: "live" },
```

- [ ] **Step 2: Add the nav icon**

In `app/src/components/nav-links.tsx`'s `ICONS` record, add an entry using `CalendarDays` from `lucide-react` (already a dependency, per the games/map icons in the same record):

```typescript
import { CalendarDays } from "lucide-react";
// ...
events: CalendarDays,
```

- [ ] **Step 3: Add translation strings**

In `app/src/messages/en.json`, add `"events": "Events"` to the existing `"nav"` namespace, and a new top-level `"events"` namespace matching `"games"`'s shape:

```json
"events": {
  "eyebrow": "Gatherings",
  "heading": "Events",
  "dek": "Dinners, outings, and everything Nora is holding for this group.",
  "loading": "Loading events…",
  "error": "Couldn't load events.",
  "empty": "No events yet — ask Nora to plan one in chat.",
  "noVenue": "No venue yet",
  "venue": "Venue",
  "rating": "{rating} / 5",
  "openInMaps": "Open in Maps",
  "rsvps": "Who's coming",
  "noRsvps": "No one's RSVP'd yet",
  "pickConversation": "Open this from a specific conversation to see its events."
}
```

- [ ] **Step 4: Manual verification**

Reload the app, confirm "Events" appears in the portal nav with the calendar icon, and clicking it lands on `/events`.

- [ ] **Step 5: Commit**

```bash
git add app/src/lib/modules.ts app/src/components/nav-links.tsx app/src/messages/en.json
git commit -m "feat(app): add Events to portal nav"
```

---

### Task 15: Release fragment, final gate, spec correction

**Files:**
- Create: `release-fragments/unreleased/event-module.json`
- Modify: `docs/superpowers/specs/2026-08-19-event-module-design.md`

- [ ] **Step 1: Write the release fragment**

```json
{
  "type": "feature",
  "summary": "Nora can now plan gatherings — dinners, outings — with RSVP tracking and reminders, entirely through chat.",
  "details": [
    "create_event/update_event/rsvp_event/cancel_event/list_events/suggest_venue tools, chat-first per docs/superpowers/specs/2026-08-19-event-module-design.md.",
    "Venue data (rating, price level, address, a Maps link) is resolved once via the existing Places infra and cached, not re-queried on every read.",
    "A read-only /events web view shows event and RSVP status.",
    "An event with a time owns a one-shot reminder via the existing reminders fire path — no new scheduling."
  ],
  "limitations": [
    "Route/ETA recommendations are not included — reserved for a later revision (spec §5.1).",
    "The web app is view-only; all creation and changes happen through chat."
  ]
}
```

- [ ] **Step 2: Fix the spec's stale migration-number assumption**

In `docs/superpowers/specs/2026-08-19-event-module-design.md`, the "## 2. Data model" section currently reads `migration \`0016_events\`` with a note assuming `0015_presence_analytics` landed first. Replace with:

```markdown
## 2. Data model — migration `0015_events`

(`0015_presence_analytics`, which an earlier draft of this spec assumed
would land first, has not — `0015` was free and this module took it. See
the implementation plan, `docs/superpowers/plans/2026-08-19-event-module.md`,
for the actual migration.)
```

- [ ] **Step 3: Run the release-fragment gate**

Run: `uv run python scripts/check_release_fragments.py --base origin/master`
Expected: PASS

Run: `uv run python scripts/render_release_notes.py`
Expected: the new fragment renders without error (spot-check the output).

- [ ] **Step 4: Full repo gate, one last time, on the complete branch**

```bash
uv run pytest -q
uv run ruff check nora/ evals/ tests/
uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence
uv run lint-imports
uv run python scripts/check_release_fragments.py --base origin/master
```

Expected: all green. If anything in `nora/agent` or `nora/web` needs mypy coverage beyond the scoped dirs CLAUDE.md names, run `uv run mypy nora/agent nora/web` too and fix anything new this branch introduced.

- [ ] **Step 5: Commit**

```bash
git add release-fragments/unreleased/event-module.json docs/superpowers/specs/2026-08-19-event-module-design.md
git commit -m "docs(nora): release fragment for Event module, fix spec migration number"
```

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage**: §1/§1.1 architecture → Tasks 1,3,7,8. §2/§2.1 data model + venue caching → Tasks 1,2,3. §3/§3.1 EventService + open-event doctrine → Tasks 7,10. §4 notifications → Task 7 (`_link_reminder`/cancel path). §5/§5.1/§5.2 recommendations + reserved routes seam → Task 7 (`suggest_venue`, `route_hints` always `None`). §6 web surface → Tasks 11-14. §7 skills → deliberately no task (spec says not needed). §8 testing/rollout → Tasks 5,10,15. Out-of-scope items (route/ETA, web write path, background extractor, new skills system, cross-workspace visibility) → deliberately have no task; nothing in this plan builds any of them.
- **Migration number**: corrected from the spec's `0016` assumption to `0015` (Global Constraints, Task 2, Task 15).
- **Field naming**: `group_id`→`conversation_id`, `reminder_series_id`→`reminder_id` — both corrected up front in Global Constraints so every later task uses the resolved names consistently, not the spec's original ones.
- **R-28 reuse**: Task 6 exists specifically so Task 7's venue resolution doesn't duplicate the identifier/coordinate screen — flagged as a real risk in the spec's §2.1, resolved here as a relocate-don't-reinvent task.
