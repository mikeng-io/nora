"""`SqlReminderStore` facts that are TRUE OF POSTGRES ONLY, so they cannot live
in `tests/contract/test_reminder_store_contract.py` — the in-memory store has no
constraints to violate.

Everything about the port's behaviour is in the contract suite. This module
holds exactly the one thing that suite structurally cannot ask: that `create`'s
`IntegrityError` -> `DuplicateReminderError` translation is NARROW.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from nora.domain.reminders import DuplicateReminderError, Reminder, ReminderState
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.infra.sql.reminders import SqlReminderStore

T0 = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _row(**kw) -> Reminder:
    base = {
        "id": uuid.uuid4(), "requester_person_id": "miles", "platform": "signal",
        "conversation_id": "conv-1", "conversation_type": "group", "text": "洗衫",
        "created_at": T0, "due_at": T0 + timedelta(minutes=40),
        "state": ReminderState.PENDING, "fired_at": None, "outcome": None,
    }
    base.update(kw)
    return Reminder(**base)


@pytest.fixture
async def store(pg_url):
    run_migrations(pg_url)
    s = SqlReminderStore(create_sql_engine(pg_url))
    yield s
    await s.aclose()


async def test_only_a_unique_violation_becomes_duplicate_reminder_error(store):
    """The translation matches SQLSTATE 23505 and nothing else.

    `create` catches `IntegrityError`, which is the class Postgres ALSO reports
    a NOT NULL violation and `ck_reminders_state` under. Catching the class and
    calling all of it "duplicate id" would name the wrong defect on every one of
    them, so the guard checks the code — and this asserts the guard is doing
    work, by sending a violation that is genuinely not a duplicate (a NULL
    `text`, sqlstate 23502) and requiring the raw `IntegrityError` back.
    """
    with pytest.raises(IntegrityError) as caught:
        await store.create(_row(text=None))
    assert not isinstance(caught.value, DuplicateReminderError)
    assert getattr(caught.value.orig, "sqlstate", None) == "23502"

    # ...and the duplicate really does translate, on the same code path.
    first = _row()
    await store.create(first)
    with pytest.raises(DuplicateReminderError):
        await store.create(_row(id=first.id))
