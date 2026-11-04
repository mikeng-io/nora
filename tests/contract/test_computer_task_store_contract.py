"""One suite, both implementations of `ComputerTaskStore` and
`BrowserTabStore` (migration `0147_computer_tasks`): the Postgres stores on
a database migrated once per module and truncated between tests, and the
in-memory fakes.

What both must hold:

- `try_claim_head` is the one-slot FIFO's atomic half: only the scope's
  oldest `queued` row may claim, and ANY `running` row of the scope blocks
  the claim — including a LATE-COMMITTED row whose `created_at` is older
  than the running one's (the overlap the first review found: checking
  "oldest open" alone lets an older queued row slip past a running one).
  Another scope's running row never blocks.
- `open_scopes` is the breadth read the queue and the tab sweep use — the
  table, never a Redis SCAN: only scopes with non-terminal rows appear.
- `expire_instance` retires exactly the open rows of a dead VM instance;
  live-instance rows keep their status.

Generated rows only: every scope is a synthetic hex key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa

from nora.domain.browser_tasks import BrowserTab, BrowserTabStore
from nora.domain.computer_tasks import ComputerTask, ComputerTaskStore
from nora.infra.sql.browser_tabs import SqlBrowserTabStore
from nora.infra.sql.computer_tasks import SqlComputerTaskStore
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations
from nora.testing import FakeBrowserTabStore, FakeComputerTaskStore

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
SCOPE = "ab" * 16
OTHER = "cd" * 16


@pytest.fixture(scope="module")
def _tasks_db(pg_admin: Any) -> Iterator[str]:
    dbname = "t_computer_tasks_" + uuid4().hex
    with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{dbname}"')
    url = (
        f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
        f"@{pg_admin.host}:{pg_admin.port}/{dbname}"
    )
    run_migrations(url)
    try:
        yield url
    finally:
        with psycopg.connect(pg_admin.dsn(pg_admin.maintenance_db), autocommit=True) as conn:
            conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,),
            )
            conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


@pytest.fixture(params=["fake", "sql"])
async def tasks(request: Any) -> AsyncIterator[Any]:
    if request.param == "fake":
        yield FakeComputerTaskStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_tasks_db"))
    try:
        yield SqlComputerTaskStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("TRUNCATE computer_tasks"))
        await engine.dispose()


@pytest.fixture(params=["fake", "sql"])
async def tabs(request: Any) -> AsyncIterator[Any]:
    if request.param == "fake":
        yield FakeBrowserTabStore()
        return
    engine = create_sql_engine(request.getfixturevalue("_tasks_db"))
    try:
        yield SqlBrowserTabStore(engine)
    finally:
        async with engine.begin() as conn:
            await conn.execute(sa.text("SET LOCAL nora.row_history = 'off'"))
            await conn.execute(sa.text("TRUNCATE browser_tasks"))
        await engine.dispose()


def _task(*, scope: str = SCOPE, created: datetime = T0,
          kind: str = "native", status: str = "queued",
          started_at: datetime | None = None) -> ComputerTask:
    return ComputerTask(
        id=uuid4(), scope=scope, kind=kind, status=status,
        payload={"tool": "computer_run", "args": {"command": "true"}},
        origin={"platform": "signal", "conversation_id": "room-a",
                "conversation_type": "group"},
        created_at=created, updated_at=created,
        started_at=created if status == "running" and started_at is None else started_at)


def _tab(*, scope: str = SCOPE, instance: str = "boot-1",
         task_id: Any = None, status: str = "open") -> BrowserTab:
    return BrowserTab(
        id=uuid4(), scope=scope, task_id=task_id or uuid4(),
        instance=instance, target_id=f"target-{uuid4().hex[:8]}",
        status=status, created_at=T0, last_activity_at=T0)


def test_both_satisfy_the_ports() -> None:
    assert isinstance(FakeComputerTaskStore(), ComputerTaskStore)
    assert isinstance(FakeBrowserTabStore(), BrowserTabStore)


# --- the one-slot FIFO --------------------------------------------------------


async def test_any_running_row_blocks_the_scope(tasks: Any) -> None:
    """A `running` row blocks EVERY later claim in its scope — the
    head-of-queue claim is refused, not just reordered."""
    first = _task(created=T0)
    second = _task(created=T0 + timedelta(seconds=1))
    await tasks.enqueue(first)
    await tasks.enqueue(second)
    claimed = await tasks.try_claim_head(first.id, now=T0)
    assert claimed is not None and claimed.status == "running"
    assert await tasks.try_claim_head(second.id, now=T0) is None


async def test_late_committed_older_row_cannot_overlap(tasks: Any) -> None:
    """The review's overlap: a row COMMITTED after the running one with an
    OLDER created_at is still refused — `running` excludes, not position."""
    running = _task(created=T0 + timedelta(seconds=5), status="running")
    older_queued = _task(created=T0)
    await tasks.enqueue(running)
    await tasks.enqueue(older_queued)
    assert await tasks.try_claim_head(older_queued.id, now=T0) is None


async def test_another_scopes_running_row_never_blocks(tasks: Any) -> None:
    running = _task(scope=OTHER, status="running")
    mine = _task()
    await tasks.enqueue(running)
    await tasks.enqueue(mine)
    claimed = await tasks.try_claim_head(mine.id, now=T0)
    assert claimed is not None and claimed.status == "running"


async def test_fifo_among_queued_rows(tasks: Any) -> None:
    older = _task(created=T0)
    newer = _task(created=T0 + timedelta(seconds=1))
    await tasks.enqueue(newer)
    await tasks.enqueue(older)
    assert await tasks.try_claim_head(newer.id, now=T0) is None  # not head
    assert await tasks.try_claim_head(older.id, now=T0) is not None


async def test_terminal_row_frees_the_head(tasks: Any) -> None:
    first = _task(created=T0)
    second = _task(created=T0 + timedelta(seconds=1))
    await tasks.enqueue(first)
    await tasks.enqueue(second)
    await tasks.try_claim_head(first.id, now=T0)
    await tasks.finish(first.id, status="done", result={"note": "ran"}, now=T0)
    assert await tasks.try_claim_head(second.id, now=T0) is not None


# --- breadth and lifecycle ------------------------------------------------------


async def test_open_scopes_names_only_unfinished_scopes(tasks: Any) -> None:
    await tasks.enqueue(_task(scope=SCOPE))
    done = _task(scope=OTHER, status="running")
    await tasks.enqueue(done)
    await tasks.finish(done.id, status="done", result={}, now=T0)
    scopes = await tasks.open_scopes(limit=10)
    assert scopes == [SCOPE]


async def test_cancel_and_interrupt_close_the_row(tasks: Any) -> None:
    queued = _task()
    running = _task(status="running", started_at=T0)
    await tasks.enqueue(queued)
    await tasks.enqueue(running)
    cancelled = await tasks.request_cancel(queued.id, now=T0)
    assert cancelled is not None and cancelled.status == "cancelled"
    flagged = await tasks.request_cancel(running.id, now=T0)
    assert flagged is not None and flagged.status == "running"
    assert flagged.cancel_requested_at is not None
    await tasks.mark_interrupted(running.id, now=T0, reason="the owner was lost")
    row = await tasks.get(running.id)
    assert row is not None and row.status == "interrupted"
    assert not await tasks.open_scopes(limit=10)


# --- browser tab bindings ---------------------------------------------------------


async def test_expire_instance_retires_only_the_dead_vm(tabs: Any) -> None:
    dead = _tab(instance="boot-1")
    alive = _tab(instance="boot-2")
    other_scope = _tab(scope=OTHER, instance="boot-1")
    for tab in (dead, alive, other_scope):
        await tabs.bind(tab)
    assert await tabs.expire_instance(scope=SCOPE, instance="boot-2", now=T0) == 1
    assert (await tabs.get(dead.id)).status == "expired"
    assert (await tabs.get(alive.id)).status == "open"
    assert (await tabs.get(other_scope.id)).status == "open"  # another scope untouched


async def test_open_for_scope_filters_by_instance(tabs: Any) -> None:
    live = _tab(instance="boot-2")
    stale = _tab(instance="boot-1")
    closed = _tab(instance="boot-2", status="closed")
    for tab in (live, stale, closed):
        await tabs.bind(tab)
    rows = await tabs.open_for_scope(SCOPE, instance="boot-2")
    assert [r.id for r in rows] == [live.id]


async def test_tab_open_scopes_is_the_sweep_breadth(tabs: Any) -> None:
    await tabs.bind(_tab(scope=SCOPE))
    await tabs.bind(_tab(scope=OTHER, status="expired"))
    assert await tabs.open_scopes(limit=10) == [SCOPE]
