# Nora Compute — Phase 4: `make_artifact` Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Phases 1-3 into an actual capability Nora can use — the `make_artifact` native tool (hybrid sync/async, per spec §4), a `ComputeJob` durable outbox for the slow path, and a `ComputeJobPoller` that delivers a finished job through `FirePath.speak` as a **third `Occasion`** — reusing the exact mechanism `VersionAnnouncer` (DEC-97) already established as the pattern for a second thing built on `FirePath` beside reminders, rather than inventing a new delivery path (R-1b: `deliver.build` has exactly two callers, structurally enforced — a compute-job completion goes through `FirePath.speak`, the same as everything else that isn't the live tag pipeline).

**Architecture:** `make_artifact` calls `ComputeBackend.launch` (Phase 2), then polls `ComputeBackend.poll` for up to ~15s. Done in time → returns the result inline, no durable row needed. Still running → writes one `ComputeJob` row (`PENDING`) and returns immediately. `ComputeJobPoller` (mirrors `VersionAnnouncer`'s shape, not `HeartbeatLoop`'s `due_at`-based one — a compute job is event-complete, not time-due) ticks, calls `ComputeBackend.poll` for every `PENDING` row's `backend_job_id`, and on a non-`RUNNING` result claims the row (compare-and-set, at-most-once, same discipline as reminders' `claim`) and calls `fire_path.speak(compute_occasion(row, result))` — a new `Occasion` factory in `nora/app/fire.py`, beside `reminder_occasion`.

**Tech Stack:** Python 3.12, SQLAlchemy Core + `asyncpg` (matching `nora/infra/sql/reminders.py`'s own shape), `alembic`, pytest.

## Global Constraints

- `ComputeJob` carries NO recurrence concept — a compute job never repeats. Do not copy `Reminder`'s `Series`/`RecurrenceRule` machinery; `ComputeJobStore` is deliberately simpler than `ReminderStore`.
- Every datetime crossing `ComputeJobStore` is UTC-aware, same rule `nora/domain/reminders.py`'s module docstring states for `ReminderStore` — reuse `ensure_utc`'s pattern (a local helper in the new domain module, not an import from `nora.domain.reminders`, since these are two independent ports even though the rule is identical).
- `make_artifact` is a plain native tool (`nora/agent/tools.py`, `NATIVE_TOOLS` table + `_spec` registration), never MCP — DEC-2's rule, already established for `remind`.
- The delivery path is `FirePath.speak(occasion)`, never a new send path — R-1b's structural constraint (`tests/architecture/test_only_two_paths_can_send.py`) forbids a third caller of `deliver.build`; `FirePath` itself remains the only caller, for a third `Occasion`, exactly as `VersionAnnouncer` already proved out for the second one (DEC-97).
- `ComputeJobPoller`'s claim step must be a compare-and-set (`PENDING -> FIRING` matched in the `WHERE` clause), not a read-then-write — the exact reason `ReminderStore.claim` (not `mark`) exists: two overlapping poll ticks must not both deliver the same finished job.

---

## File Structure

| File | Change |
|---|---|
| `nora/domain/compute.py` (new) | `ComputeJob`, `ComputeJobState`, `ComputeJobStore` Protocol |
| `nora/infra/sql/compute_jobs.py` (new) | `SqlComputeJobStore` |
| `nora/infra/sql/migrations/versions/0008_compute_jobs.py` (new) | migration |
| `nora/infra/sql/schema.py` | Add `COMPUTE_JOBS` table |
| `nora/testing/fakes.py` | Add `FakeComputeJobStore` |
| `nora/testing/__init__.py` | Export it |
| `tests/contract/test_compute_job_store_contract.py` (new) | Shared contract suite |
| `nora/app/fire.py` | Add `compute_occasion` |
| `nora/app/compute_poller.py` (new) | `ComputeJobPoller` |
| `nora/agent/tools.py` | Add `make_artifact`, its `NATIVE_TOOLS` entry, its `_spec` registration |
| `nora/agent/registry.py` | Thread `ComputeBackend`/`ComputeJobStore` through |
| `nora/app/composition.py` | Wire `LocalDockerBackend`, `SqlComputeJobStore`, `ComputeJobPoller` |
| `design/09-decision-index.md` | `DEC-99` |
| `tests/test_acceptance_rules.py` | `R-43` |

---

### Task 1: `ComputeJob` domain type + `ComputeJobStore` port

**Files:**
- Create: `nora/domain/compute.py`
- Create: `tests/test_ports.py` extension (or a dedicated `tests/domain/test_compute.py` if a domain-level test file convention exists — check `tests/domain/` first)

**Interfaces:**
- Produces: `ComputeJobState` (`PENDING`, `FIRING`, `DONE`, `FAILED`), `ComputeJob` (frozen dataclass), `ComputeJobStore` Protocol (`create`, `get`, `pending`, `claim`, `mark`, `sweep_firing`, `aclose`) — Task 2's `SqlComputeJobStore` and Task 3's `FakeComputeJobStore` both implement this exactly; Task 4's `ComputeJobPoller` and Task 5's `make_artifact` both consume it.

- [ ] **Step 1: Write the failing test**

Add to a new `tests/domain/test_compute.py` (create `tests/domain/` if it doesn't exist as a directory — check first):

```python
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.domain.compute import (
    ComputeJob,
    ComputeJobState,
    NaiveComputeJobDatetimeError,
    ensure_utc_job,
)


def test_a_naive_datetime_is_refused():
    with pytest.raises(NaiveComputeJobDatetimeError):
        ensure_utc_job(datetime(2026, 1, 1), field="created_at")  # noqa: DTZ001


def test_an_aware_datetime_normalises_to_utc():
    aware = datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert ensure_utc_job(aware, field="created_at") == aware


def test_a_compute_job_constructs_with_pending_state():
    job = ComputeJob(
        id=uuid4(), backend_job_id="job_abc123", task="write a haiku",
        artifact_id=None, platform="signal", conversation_id="conv-1",
        conversation_type="group", requester_person_id="person-1",
        created_at=datetime.now(UTC), state=ComputeJobState.PENDING,
        completed_at=None, result_url=None, result_summary=None, error=None,
    )
    assert job.state is ComputeJobState.PENDING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/domain/test_compute.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nora.domain.compute'`.

- [ ] **Step 3: Implement `nora/domain/compute.py`**

```python
"""The compute-job outbox port (Nora Compute spec §2/§4). A compute job is
NOT a deferred request repeated on a schedule (unlike `nora.domain.reminders`
— no `Series`, no recurrence): one job, one delivery, ever.

Every datetime crossing this port is UTC-aware, the same rule
`nora.domain.reminders`'s module docstring states for `ReminderStore` — kept
as a SEPARATE local rule/helper here rather than imported from that module,
since the two ports are independent even though the rule is identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID


class NaiveComputeJobDatetimeError(ValueError):
    """A datetime reached `ComputeJobStore` without a timezone."""


def ensure_utc_job(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveComputeJobDatetimeError(
            f"{field} must be timezone-aware; got naive {value!r}."
        )
    return value.astimezone(UTC)


class ComputeJobState(StrEnum):
    PENDING = "pending"
    FIRING = "firing"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ComputeJob:
    """One `make_artifact` call that fell to the slow path (Nora Compute
    spec §4) — the fast path never writes a row at all.

    `backend_job_id` is `ComputeBackend.launch`'s own return value — the
    handle `ComputeJobPoller` polls with. `artifact_id`, when set, means this
    job is revising an existing artifact (spec §9), matching
    `ComputeBackend.launch`'s own parameter of the same name and purpose.
    `requester_person_id`/`platform`/`conversation_id`/`conversation_type`
    are exactly what `compute_occasion` (nora/app/fire.py) needs to build an
    `Occasion` once the job completes — the same four fields
    `Occasion`/`Reminder` both already carry, for the same reason (a fire
    turn needs one Room, one policy decision, one Deliverer binding).
    """

    id: UUID
    backend_job_id: str
    task: str
    artifact_id: str | None
    platform: str
    conversation_id: str
    conversation_type: str
    requester_person_id: str
    created_at: datetime
    state: ComputeJobState
    completed_at: datetime | None
    result_url: str | None
    result_summary: str | None
    error: str | None


def normalise_compute_job(job: ComputeJob) -> ComputeJob:
    from dataclasses import replace

    return replace(
        job,
        created_at=ensure_utc_job(job.created_at, field="created_at"),
        completed_at=(
            None if job.completed_at is None
            else ensure_utc_job(job.completed_at, field="completed_at")
        ),
    )


@runtime_checkable
class ComputeJobStore(Protocol):
    async def create(self, job: ComputeJob) -> None:
        """Insert-only, `state` always `PENDING` at creation."""
        ...

    async def get(self, job_id: UUID) -> ComputeJob | None: ...

    async def pending(self, *, limit: int) -> list[ComputeJob]:
        """Every `PENDING` row — `ComputeJobPoller`'s own batch, polled
        against `ComputeBackend` each tick. Unlike `ReminderStore.due()`,
        there is no `due_at` filter: EVERY pending row is a candidate on
        every tick, because "is it done" is a poll, not a clock comparison.
        """
        ...

    async def claim(self, job_id: UUID) -> bool:
        """`PENDING -> FIRING`, compare-and-set. True iff this caller took
        the row — the same at-most-once discipline `ReminderStore.claim`
        exists for, without the recurrence-successor half (a compute job
        never repeats)."""
        ...

    async def mark(
        self, job_id: UUID, state: ComputeJobState, *,
        completed_at: datetime | None = None, result_url: str | None = None,
        result_summary: str | None = None, error: str | None = None,
    ) -> bool:
        """Terminal write for a row already claimed. True if it changed."""
        ...

    async def sweep_firing(self, *, now: datetime) -> int:
        """Boot recovery: `FIRING` rows (a crash mid-delivery) resolve to
        `FAILED`, never re-delivered — at-most-once, mirroring
        `ReminderStore.sweep_firing`. Returns how many."""
        ...

    async def aclose(self) -> None: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/domain/test_compute.py -v`
Expected: PASS.

- [ ] **Step 5: Type-check and lint**

Run: `uv run mypy nora/domain && uv run ruff check nora/`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add nora/domain/compute.py tests/domain/test_compute.py
git commit -m "feat(compute): add the ComputeJob domain type and ComputeJobStore port"
```

---

### Task 2: `SqlComputeJobStore` + migration

**Files:**
- Create: `nora/infra/sql/compute_jobs.py`
- Create: `nora/infra/sql/migrations/versions/0008_compute_jobs.py`
- Modify: `nora/infra/sql/schema.py`
- Modify: `tests/infra/pg_schema_baseline.sql`

**Interfaces:**
- Produces: `SqlComputeJobStore(engine: AsyncEngine)`, satisfying `ComputeJobStore`.

- [ ] **Step 1: Add `COMPUTE_JOBS` to `nora/infra/sql/schema.py`**

Following the `ARTIFACTS`/`REMINDERS` table style already in that file:

```python
# `compute_jobs` — the Nora Compute outbox (Nora Compute spec §4, §12). One
# row per `make_artifact` call that fell to the slow path; the fast path
# never writes one. No recurrence columns — unlike `REMINDERS`, a compute
# job never repeats.
COMPUTE_JOBS = sa.Table(
    "compute_jobs",
    METADATA,
    sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
    sa.Column("backend_job_id", sa.Text, nullable=False),
    sa.Column("task", sa.Text, nullable=False),
    sa.Column("artifact_id", sa.Text, nullable=True),
    sa.Column("platform", sa.Text, nullable=False),
    sa.Column("conversation_id", sa.Text, nullable=False),
    sa.Column("conversation_type", sa.Text, nullable=False),
    sa.Column("requester_person_id", sa.Text, nullable=False),
    sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("state", sa.Text, nullable=False, server_default="pending"),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("result_url", sa.Text, nullable=True),
    sa.Column("result_summary", sa.Text, nullable=True),
    sa.Column("error", sa.Text, nullable=True),
    sa.CheckConstraint(
        "state IN ('pending', 'firing', 'done', 'failed')",
        name="ck_compute_jobs_state",
    ),
)
```

(Match this file's actual import aliases for `sa`/`pg`/`METADATA` — confirm against the existing `ARTIFACTS`/`REMINDERS` definitions immediately above this addition, since this plan doesn't have that file's exact import lines in front of it.)

Add an index the poller's own `pending()` query will use — matching `idx_reminders_due`'s own precedent (referenced in `nora/infra/sql/reminders.py`'s module docstring):

```python
COMPUTE_JOBS_STATE_IDX = sa.Index(
    "idx_compute_jobs_state", COMPUTE_JOBS.c.state,
)
```

- [ ] **Step 2: Write the migration**

Create `nora/infra/sql/migrations/versions/0008_compute_jobs.py`, following `0007_drop_artifacts.py`'s exact shape (hand-written or autogenerated — since this table, like `artifacts`, has a CHECK constraint alembic's autogenerate is blind to per CLAUDE.md's own gate note, write it by hand):

```python
"""compute_jobs — the Nora Compute outbox (Nora Compute spec §4, §12).

Revision ID: 0008_compute_jobs
Revises: 0007_drop_artifacts
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0008_compute_jobs"
down_revision = "0007_drop_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compute_jobs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("backend_job_id", sa.Text, nullable=False),
        sa.Column("task", sa.Text, nullable=False),
        sa.Column("artifact_id", sa.Text, nullable=True),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("conversation_id", sa.Text, nullable=False),
        sa.Column("conversation_type", sa.Text, nullable=False),
        sa.Column("requester_person_id", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("state", sa.Text, nullable=False, server_default="pending"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_url", sa.Text, nullable=True),
        sa.Column("result_summary", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'firing', 'done', 'failed')",
            name="ck_compute_jobs_state",
        ),
    )
    op.create_index("idx_compute_jobs_state", "compute_jobs", ["state"])


def downgrade() -> None:
    op.drop_index("idx_compute_jobs_state", table_name="compute_jobs")
    op.drop_table("compute_jobs")
```

- [ ] **Step 3: Implement `SqlComputeJobStore`**

Create `nora/infra/sql/compute_jobs.py`, mirroring `nora/infra/sql/reminders.py`'s shape without the recurrence machinery:

```python
"""`ComputeJobStore` over SQLAlchemy Core — the Nora Compute outbox's SQL
implementation. Shaped like `nora.infra.sql.reminders`, minus everything
recurrence-related: a compute job never repeats.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.compute import (
    ComputeJob,
    ComputeJobState,
    ensure_utc_job,
    normalise_compute_job,
)
from nora.infra.sql.schema import COMPUTE_JOBS


def _row_to_job(r: Row[Any]) -> ComputeJob:
    return normalise_compute_job(
        ComputeJob(
            id=r.id, backend_job_id=r.backend_job_id, task=r.task,
            artifact_id=r.artifact_id, platform=r.platform,
            conversation_id=r.conversation_id,
            conversation_type=r.conversation_type,
            requester_person_id=r.requester_person_id,
            created_at=r.created_at, state=ComputeJobState(r.state),
            completed_at=r.completed_at, result_url=r.result_url,
            result_summary=r.result_summary, error=r.error,
        )
    )


class SqlComputeJobStore:
    """Satisfies `nora.domain.compute.ComputeJobStore`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(self, job: ComputeJob) -> None:
        row = normalise_compute_job(job)
        stmt = sa.insert(COMPUTE_JOBS).values(
            id=row.id, backend_job_id=row.backend_job_id, task=row.task,
            artifact_id=row.artifact_id, platform=row.platform,
            conversation_id=row.conversation_id,
            conversation_type=row.conversation_type,
            requester_person_id=row.requester_person_id,
            created_at=row.created_at, state=row.state.value,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get(self, job_id: UUID) -> ComputeJob | None:
        stmt = sa.select(COMPUTE_JOBS).where(COMPUTE_JOBS.c.id == job_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return None if row is None else _row_to_job(row)

    async def pending(self, *, limit: int) -> list[ComputeJob]:
        stmt = (
            sa.select(COMPUTE_JOBS)
            .where(COMPUTE_JOBS.c.state == ComputeJobState.PENDING.value)
            .order_by(COMPUTE_JOBS.c.created_at.asc(), COMPUTE_JOBS.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [_row_to_job(r) for r in rows]

    async def claim(self, job_id: UUID) -> bool:
        stmt = (
            sa.update(COMPUTE_JOBS)
            .where(
                COMPUTE_JOBS.c.id == job_id,
                COMPUTE_JOBS.c.state == ComputeJobState.PENDING.value,
            )
            .values(state=ComputeJobState.FIRING.value)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def mark(
        self, job_id: UUID, state: ComputeJobState, *,
        completed_at: Any = None, result_url: str | None = None,
        result_summary: str | None = None, error: str | None = None,
    ) -> bool:
        values: dict[str, Any] = {"state": state.value}
        if completed_at is not None:
            values["completed_at"] = ensure_utc_job(completed_at, field="completed_at")
        if result_url is not None:
            values["result_url"] = result_url
        if result_summary is not None:
            values["result_summary"] = result_summary
        if error is not None:
            values["error"] = error
        stmt = sa.update(COMPUTE_JOBS).where(COMPUTE_JOBS.c.id == job_id).values(**values)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def sweep_firing(self, *, now: Any) -> int:
        stmt = (
            sa.update(COMPUTE_JOBS)
            .where(COMPUTE_JOBS.c.state == ComputeJobState.FIRING.value)
            .values(
                state=ComputeJobState.FAILED.value,
                completed_at=ensure_utc_job(now, field="now"),
                error="swept at boot: process crashed mid-delivery",
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount or 0)

    async def aclose(self) -> None:
        await self._engine.dispose()
```

- [ ] **Step 4: Update the schema baseline**

Run whatever this repo's own process is for regenerating `tests/infra/pg_schema_baseline.sql` after a migration (check `tests/infra/test_schema_parity.py`'s own docstring for the exact regeneration command — this is the CLAUDE.md-flagged gate that `alembic check` is blind to).

- [ ] **Step 5: Run the schema parity and migration tests**

Run: `uv run pytest tests/infra/test_schema_parity.py -v`
Expected: PASS once the baseline is regenerated.

- [ ] **Step 6: Type-check and lint**

Run: `uv run mypy nora/infra && uv run ruff check nora/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add nora/infra/sql/schema.py nora/infra/sql/compute_jobs.py nora/infra/sql/migrations/versions/0008_compute_jobs.py tests/infra/pg_schema_baseline.sql
git commit -m "feat(compute): add SqlComputeJobStore and its migration"
```

---

### Task 3: `FakeComputeJobStore` + shared contract suite

**Files:**
- Modify: `nora/testing/fakes.py`
- Modify: `nora/testing/__init__.py`
- Create: `tests/contract/test_compute_job_store_contract.py`

**Interfaces:**
- Produces: `FakeComputeJobStore()`, bound to `SqlComputeJobStore` by the new contract suite — CLAUDE.md's own port discipline, same as every other port in this file.

- [ ] **Step 1: Write the failing contract tests**

Create `tests/contract/test_compute_job_store_contract.py`, parametrized the same way `test_artifact_ingest_contract.py` (Phase 1) is — but against a real Postgres-backed `SqlComputeJobStore` this time (matching `tests/contract/test_reminder_store_contract.py`'s own precedent for `ReminderStore`, which is itself DB-backed, unlike `ArtifactIngest`'s HTTP-double shape):

```python
"""One suite, both implementations of `ComputeJobStore` (Nora Compute spec
§4). Mirrors `tests/contract/test_reminder_store_contract.py`'s own shape —
check that file for the exact DB-fixture pattern (a real, migrated test
Postgres) and match it here rather than inventing a second one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.domain.compute import ComputeJob, ComputeJobState
from nora.testing import FakeComputeJobStore

# Mirror test_reminder_store_contract.py's fixture for the real store --
# confirm its exact shape (engine construction, migration application) before
# writing this fixture, rather than guessing at a shape that might not match
# how that suite already sets up a real Postgres-backed store for testing.


def _job(**overrides):
    defaults = dict(
        id=uuid4(), backend_job_id="job_x", task="write a haiku",
        artifact_id=None, platform="signal", conversation_id="conv-1",
        conversation_type="group", requester_person_id="person-1",
        created_at=datetime.now(UTC), state=ComputeJobState.PENDING,
        completed_at=None, result_url=None, result_summary=None, error=None,
    )
    defaults.update(overrides)
    return ComputeJob(**defaults)


@pytest.fixture(params=["fake"])  # add "sql" once the real-store fixture (Step 1's own note) exists
async def store(request):
    if request.param == "fake":
        yield FakeComputeJobStore()
        return
    raise NotImplementedError("wire the real SqlComputeJobStore fixture here")


async def test_create_then_get_round_trips(store):
    job = _job()
    await store.create(job)
    fetched = await store.get(job.id)
    assert fetched is not None
    assert fetched.backend_job_id == "job_x"


async def test_pending_returns_only_pending_rows(store):
    pending_job = _job()
    await store.create(pending_job)
    done_job = _job(id=uuid4(), state=ComputeJobState.DONE)
    await store.create(done_job)
    result = await store.pending(limit=10)
    ids = {j.id for j in result}
    assert pending_job.id in ids
    assert done_job.id not in ids


async def test_claim_is_a_compare_and_set_from_pending(store):
    job = _job()
    await store.create(job)
    assert await store.claim(job.id) is True
    assert await store.claim(job.id) is False  # already FIRING, second claim loses


async def test_mark_writes_the_terminal_result(store):
    job = _job()
    await store.create(job)
    await store.claim(job.id)
    changed = await store.mark(
        job.id, ComputeJobState.DONE, completed_at=datetime.now(UTC),
        result_url="https://nora.example.net/drop/x", result_summary="a haiku",
    )
    assert changed is True
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.DONE
    assert fetched.result_url == "https://nora.example.net/drop/x"


async def test_sweep_firing_resolves_to_failed(store):
    job = _job()
    await store.create(job)
    await store.claim(job.id)
    swept = await store.sweep_firing(now=datetime.now(UTC))
    assert swept == 1
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.FAILED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/contract/test_compute_job_store_contract.py -v`
Expected: FAIL — `ImportError: cannot import name 'FakeComputeJobStore' from 'nora.testing'`.

- [ ] **Step 3: Implement `FakeComputeJobStore`**

Add to `nora/testing/fakes.py`:

```python
class FakeComputeJobStore:
    """In-memory `ComputeJobStore`. Bound to `SqlComputeJobStore` by
    `tests/contract/test_compute_job_store_contract.py`."""

    def __init__(self) -> None:
        self._jobs: dict[UUID, ComputeJob] = {}

    async def create(self, job: ComputeJob) -> None:
        self._jobs[job.id] = normalise_compute_job(job)

    async def get(self, job_id: UUID) -> ComputeJob | None:
        return self._jobs.get(job_id)

    async def pending(self, *, limit: int) -> list[ComputeJob]:
        rows = [j for j in self._jobs.values() if j.state is ComputeJobState.PENDING]
        rows.sort(key=lambda j: (j.created_at, str(j.id)))
        return rows[:limit]

    async def claim(self, job_id: UUID) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.state is not ComputeJobState.PENDING:
            return False
        self._jobs[job_id] = replace(job, state=ComputeJobState.FIRING)
        return True

    async def mark(
        self, job_id: UUID, state: ComputeJobState, *,
        completed_at: Any = None, result_url: str | None = None,
        result_summary: str | None = None, error: str | None = None,
    ) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        self._jobs[job_id] = replace(
            job, state=state,
            completed_at=completed_at if completed_at is not None else job.completed_at,
            result_url=result_url if result_url is not None else job.result_url,
            result_summary=result_summary if result_summary is not None else job.result_summary,
            error=error if error is not None else job.error,
        )
        return True

    async def sweep_firing(self, *, now: Any) -> int:
        swept = 0
        for job_id, job in list(self._jobs.items()):
            if job.state is ComputeJobState.FIRING:
                self._jobs[job_id] = replace(
                    job, state=ComputeJobState.FAILED, completed_at=now,
                    error="swept at boot: process crashed mid-delivery",
                )
                swept += 1
        return swept

    async def aclose(self) -> None:
        pass
```

Add `ComputeJob`, `ComputeJobState`, `normalise_compute_job` to this file's imports from `nora.domain.compute`, and `from uuid import UUID` if not already imported. Export `FakeComputeJobStore` from `nora/testing/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/contract/test_compute_job_store_contract.py -v`
Expected: PASS for the `fake` param. The `sql` param stays `NotImplementedError`'d until Step 1's own flagged follow-up (matching `test_reminder_store_contract.py`'s real fixture) is wired in — do this before considering this task complete; it's the actual conformance check between the two implementations, not the fake alone.

- [ ] **Step 5: Wire the real store into the contract fixture**

Read `tests/contract/test_reminder_store_contract.py`'s own `store` fixture (or equivalent) for its exact real-Postgres setup pattern, and mirror it exactly for `SqlComputeJobStore` — likely a test database URL, running migrations up to (at least) `0008_compute_jobs`, and yielding `SqlComputeJobStore(engine)`. Replace this task's Step 1 `NotImplementedError` placeholder with that real fixture, and add `"sql"` to the `@pytest.fixture(params=[...])` list.

Run: `uv run pytest tests/contract/test_compute_job_store_contract.py -v`
Expected: PASS for both `fake` and `sql`.

- [ ] **Step 6: Type-check and lint**

Run: `uv run mypy nora/domain nora/infra && uv run ruff check nora/ tests/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add nora/testing/fakes.py nora/testing/__init__.py tests/contract/test_compute_job_store_contract.py
git commit -m "feat(compute): add FakeComputeJobStore and its shared contract suite"
```

---

### Task 4: `compute_occasion` + `ComputeJobPoller`

**Files:**
- Modify: `nora/app/fire.py`
- Create: `nora/app/compute_poller.py`
- Create: `tests/unit/test_compute_poller.py` (matching `tests/unit/test_version_announcer.py`'s own directory convention for a `FirePath`-consuming poller — confirm that directory exists and holds this shape of test before creating a new location)

**Interfaces:**
- Consumes: `ComputeBackend.poll`, `ComputeJobStore` (Task 1), `FirePath.speak` (existing).
- Produces: `compute_occasion(job: ComputeJob, result: JobResult) -> Occasion`; `ComputeJobPoller(store, backend, speak, slots, metrics, tick_seconds, clock)` with `.run()`.

- [ ] **Step 1: Add `compute_occasion` to `nora/app/fire.py`**

After `reminder_occasion` (before the `class FirePath:` line):

```python
def compute_occasion(job: ComputeJob, result: JobResult) -> Occasion:
    """A finished compute job, as the occasion it is (Nora Compute spec §2)
    — the THIRD `Occasion`, reusing `FirePath.speak` exactly as
    `VersionAnnouncer` (DEC-97) already established as the pattern for a
    second thing built on this turn beside reminders.

    `body` is the job's own task description PLUS its result summary —
    Smith's own account of what it built (Nora Compute spec §11: this text
    passes R-18 like any other reply; the artifact bytes themselves do not).
    Nora composes the actual group-facing message from this in her own
    voice; this function only assembles what she is TOLD, not what she says.
    """
    outcome = (
        f"It finished: {result.summary}\n{result.url}"
        if result.status is JobStatus.SUCCEEDED
        else f"It failed: {result.error or 'no reason given'}"
    )
    return Occasion(
        platform=job.platform,
        conversation_id=job.conversation_id,
        conversation_type=job.conversation_type,
        person_id=job.requester_person_id,
        body=f"(asked earlier) {job.task}\n\n{outcome}",
        header=lambda tagger, now: _compute_header(job, tagger, now),
        kind="compute",
    )


def _compute_header(job: ComputeJob, tagger: PersonRef | None, now: datetime) -> str:
    who = escape(tagger.name if tagger is not None else "")
    return (
        "You are in a group chat you have been reading all along. Nobody has "
        f"just called you. Earlier, {who} asked you to build something, and "
        "the job has now finished (or failed) — that is why this turn is "
        "happening, and it is the only reason.\n"
        "Tell them what you built (or what went wrong), in your own words, "
        "including the link if there is one. Do not just repeat the raw "
        "summary verbatim — say it the way you'd actually tell a friend."
    )
```

Add `from nora.domain.compute import ComputeJob` and `from nora.domain.ports import JobResult, JobStatus` to this file's imports.

- [ ] **Step 2: Write the failing poller test**

Create `tests/unit/test_compute_poller.py` (mirroring `test_version_announcer.py`'s own fixture shape for a fake clock/store/slots — read that file first for the exact pattern before writing this one, since it's the closest precedent for testing a `FirePath.speak`-based poller without a real model call):

```python
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nora.app.compute_poller import ComputeJobPoller
from nora.domain.compute import ComputeJob, ComputeJobState
from nora.domain.ports import JobResult, JobStatus
from nora.testing import FakeComputeBackend, FakeComputeJobStore

# Mirror tests/unit/test_version_announcer.py's own ConversationSlots fake
# and clock fixture here rather than reinventing one -- read that file
# before implementing this task.


def _pending_job(**overrides):
    defaults = dict(
        id=uuid4(), backend_job_id="job_1", task="write a haiku",
        artifact_id=None, platform="signal", conversation_id="conv-1",
        conversation_type="group", requester_person_id="person-1",
        created_at=datetime.now(UTC), state=ComputeJobState.PENDING,
        completed_at=None, result_url=None, result_summary=None, error=None,
    )
    defaults.update(overrides)
    return ComputeJob(**defaults)


async def test_a_still_running_job_is_left_pending(fake_slots, fake_metrics):
    store = FakeComputeJobStore()
    job = _pending_job()
    await store.create(job)
    backend = FakeComputeBackend()
    backend.set_result(
        job_id="job_1", result=JobResult(status=JobStatus.SUCCEEDED), polls_before_done=99,
    )
    spoken = []
    poller = ComputeJobPoller(
        store=store, backend=backend, speak=lambda occ: spoken.append(occ) or "sent",
        slots=fake_slots, metrics=fake_metrics, tick_seconds=1.0,
        clock=lambda: datetime.now(UTC),
    )
    await poller.tick()
    assert not spoken
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.PENDING


async def test_a_finished_job_is_delivered_and_marked_done(fake_slots, fake_metrics):
    store = FakeComputeJobStore()
    job = _pending_job()
    await store.create(job)
    backend = FakeComputeBackend()
    backend.set_result(
        job_id="job_1",
        result=JobResult(status=JobStatus.SUCCEEDED, url="https://x/drop/1", summary="a haiku"),
        polls_before_done=0,
    )
    spoken = []

    async def speak(occ):
        spoken.append(occ)
        return "sent"

    poller = ComputeJobPoller(
        store=store, backend=backend, speak=speak, slots=fake_slots,
        metrics=fake_metrics, tick_seconds=1.0, clock=lambda: datetime.now(UTC),
    )
    await poller.tick()
    assert len(spoken) == 1
    assert "a haiku" in spoken[0].body
    fetched = await store.get(job.id)
    assert fetched.state is ComputeJobState.DONE
    assert fetched.result_url == "https://x/drop/1"


async def test_a_job_never_delivered_twice(fake_slots, fake_metrics):
    """The compare-and-set claim (Global Constraints), tested directly rather
    than by calling `tick()` twice sequentially. An earlier draft of this
    test did exactly that — but by the second `tick()`, the job is already
    `DONE` and `pending()` correctly excludes it, so `_maybe_deliver` is
    never even called a second time; the test would pass identically even if
    `claim()` had no compare-and-set at all (i.e. always returned `True`),
    which is precisely the defect this test exists to catch (CLAUDE.md: "if
    reverting the code under test doesn't turn the test red, the test is
    decorative"). Racing two concurrent `_maybe_deliver` calls doesn't work
    either — `_maybe_deliver` acquires the conversation SLOT before it claims
    the row, so a second concurrent call would already be blocked by the slot
    gate regardless of whether `claim`'s own CAS works, testing the wrong
    thing.

    The real test: PRE-CLAIM the row directly (simulating "a claim already
    won it," the exact state `_maybe_deliver` must respect), then call
    `_maybe_deliver` on the still-PENDING-looking `job` value and confirm it
    never calls `speak`. If `claim()` had no compare-and-set — i.e. it
    unconditionally returned `True` regardless of the row's current state —
    this test fails, because `_maybe_deliver` would proceed to deliver a row
    something else already claimed.
    """
    store = FakeComputeJobStore()
    job = _pending_job()
    await store.create(job)
    backend = FakeComputeBackend()
    backend.set_result(
        job_id="job_1",
        result=JobResult(status=JobStatus.SUCCEEDED, url="https://x/drop/1", summary="x"),
        polls_before_done=0,
    )
    spoken = []

    async def speak(occ):
        spoken.append(occ)
        return "sent"

    poller = ComputeJobPoller(
        store=store, backend=backend, speak=speak, slots=fake_slots,
        metrics=fake_metrics, tick_seconds=1.0, clock=lambda: datetime.now(UTC),
    )

    # Simulate another poller pass (or another process) having already won
    # the claim on this exact row, between this test's own `poll` seeing it
    # SUCCEEDED and `_maybe_deliver` reaching its own `claim` call.
    assert await store.claim(job.id) is True

    await poller._maybe_deliver(job)
    assert spoken == []
```

(`fake_slots`/`fake_metrics` fixtures: reuse whatever `tests/unit/test_version_announcer.py` or `tests/unit/test_heartbeat.py` already define for `ConversationSlots`/`Metrics` fakes — do not invent a third copy of either.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_compute_poller.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nora.app.compute_poller'`.

- [ ] **Step 4: Implement `ComputeJobPoller`**

Create `nora/app/compute_poller.py`:

```python
"""`ComputeJobPoller` — delivers a finished compute job (Nora Compute spec
§4) through `FirePath.speak`, mirroring `VersionAnnouncer`'s shape (DEC-97)
rather than `HeartbeatLoop`'s: there is no `due_at` to compare against,
every `PENDING` row is a candidate every tick, and "is it done" is answered
by `ComputeBackend.poll`, not a clock.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from nora.app.fire import compute_occasion
from nora.domain.compute import ComputeJob, ComputeJobState, ComputeJobStore
from nora.domain.ports import ComputeBackend, JobStatus
from nora.engagement.slots import ConversationSlots, Key
from nora.obs.metrics import Metrics

log = logging.getLogger("nora.app.compute_poller")

BATCH_LIMIT = 50


class ComputeJobPoller:
    def __init__(
        self,
        *,
        store: ComputeJobStore,
        backend: ComputeBackend,
        speak: Callable[[Any], Awaitable[str]],
        slots: ConversationSlots,
        metrics: Metrics,
        tick_seconds: float,
        clock: Callable[[], datetime],
    ) -> None:
        self._store = store
        self._backend = backend
        self._speak = speak
        self._slots = slots
        self._metrics = metrics
        self._tick_seconds = tick_seconds
        self._clock = clock

    async def tick(self) -> None:
        pending = await self._store.pending(limit=BATCH_LIMIT)
        for job in pending:
            try:
                await self._maybe_deliver(job)
            except Exception:
                log.exception(
                    "compute job %s failed while polling/delivering; the rest "
                    "of the tick continues", job.id,
                )
                self._metrics.inc("nora_errors_total", stage="compute_poller")

    async def _maybe_deliver(self, job: ComputeJob) -> None:
        result = await self._backend.poll(job_id=job.backend_job_id)
        if result.status is JobStatus.RUNNING:
            return

        slot: Key = (job.platform, job.conversation_id)
        if not self._slots.try_acquire(slot):
            log.info("compute job %s deferred: slot held", job.id)
            return
        try:
            if not await self._store.claim(job.id):
                # Already claimed by an earlier tick's overlapping poll.
                return
            outcome = await self._speak(compute_occasion(job, result))
            state = ComputeJobState.DONE if outcome == "sent" else ComputeJobState.FAILED
            await self._store.mark(
                job.id, state, completed_at=self._clock(),
                result_url=result.url, result_summary=result.summary,
                error=result.error,
            )
            self._metrics.inc("nora_compute_jobs_delivered_total", outcome=outcome)
        finally:
            self._slots.release_and_dispatch(slot)

    async def run(self) -> None:
        log.info("compute job poller started: tick every %.1fs", self._tick_seconds)
        while True:
            await asyncio.sleep(self._tick_seconds)
            try:
                await self.tick()
            except Exception:
                log.exception("compute job poller tick failed")
                self._metrics.inc("nora_errors_total", stage="compute_poller")


async def recover(*, store: ComputeJobStore, metrics: Metrics, now: datetime) -> int:
    """Boot recovery, mirroring `nora.app.heartbeat.recover` — `FIRING` rows
    (a crash mid-delivery) resolve to `FAILED`, never re-delivered."""
    swept = await store.sweep_firing(now=now)
    if swept:
        log.warning(
            "boot recovery: %d compute job(s) were left FIRING and are now FAILED",
            swept,
        )
        metrics.inc("nora_compute_jobs_delivered_total", swept, outcome="failed_error")
    return swept
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_compute_poller.py -v`
Expected: PASS.

- [ ] **Step 6: Type-check and lint**

Run: `uv run mypy nora/app && uv run ruff check nora/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add nora/app/fire.py nora/app/compute_poller.py tests/unit/test_compute_poller.py
git commit -m "feat(compute): add compute_occasion and ComputeJobPoller"
```

---

### Task 5: `make_artifact` native tool

**Files:**
- Modify: `nora/agent/tools.py`
- Modify: `nora/agent/registry.py`

**Interfaces:**
- Consumes: `ComputeBackend` (Phase 2), `ComputeJobStore` (Task 1).
- Produces: the `make_artifact` tool, callable by the model — a dict result with `status` (matching `remind`'s own `ok`/`refused`/`unavailable` shape).

- [ ] **Step 1: Add the `NATIVE_TOOLS` entry**

In `nora/agent/tools.py`, add to the `NATIVE_TOOLS` table (near `remind`'s own entry, `tools.py:416-419`):

```python
"make_artifact": NativeTool(1, "someone wants you to build or research "
                               "something and hand back a file — a report, "
                               "a chart, a document — not just say it in "
                               "chat"),
```

- [ ] **Step 2: Write the tool function**

Add near `remind` in `tools.py` (inside `build_tool_specs`, alongside its other closures — add `compute_backend: ComputeBackend | None`, `compute_jobs: ComputeJobStore | None` to `build_tool_specs`'s own signature, mirroring `reminders`/`reminder_caps` at `tools.py:1137-1138`):

```python
_SYNC_POLL_SECONDS = 15.0
_SYNC_POLL_INTERVAL = 1.0


async def make_artifact(task: str, artifact_id: str | None = None) -> dict:
    """Build or research something and hand back a link to it — a report, a
    chart, a document. Use this when someone wants a FILE, not just an
    answer in chat. Takes anywhere from seconds to minutes; you may get the
    result back immediately, or a "still working" status if it's taking
    longer — in that case, tell them you're working on it and the result
    will follow as a separate message once it's ready.

    Args:
        task: what to build, in your own words — a clear, self-contained
            brief. This is ALL the compute job will see; it has no access
            to this conversation, so include everything it needs to know.
        artifact_id: set this to REVISE an existing artifact (from a
            previous make_artifact result) rather than creating a new one.
            Leave unset to create something new.
    """
    if compute_backend is None:
        return {"status": "unavailable"}

    job_id = await compute_backend.launch(task=task, artifact_id=artifact_id)
    elapsed = 0.0
    while elapsed < _SYNC_POLL_SECONDS:
        result = await compute_backend.poll(job_id=job_id)
        if result.status is not JobStatus.RUNNING:
            break
        await asyncio.sleep(_SYNC_POLL_INTERVAL)
        elapsed += _SYNC_POLL_INTERVAL
    else:
        result = await compute_backend.poll(job_id=job_id)

    if result.status is JobStatus.SUCCEEDED:
        return {"status": "ok", "url": result.url, "summary": result.summary}
    if result.status is JobStatus.FAILED:
        return {"status": "ok", "failed": True, "error": result.error}

    # Still running past the sync window -- fall to the slow path (Nora
    # Compute spec §4). A missing requester/conversation binding means there
    # is nowhere to deliver the eventual result, so this degrades to
    # "unavailable" rather than launching a job nobody can be told about.
    if compute_jobs is None or room.tagger is None:
        return {"status": "unavailable"}
    job = ComputeJob(
        id=uuid.uuid4(), backend_job_id=job_id, task=task, artifact_id=artifact_id,
        platform=room.platform, conversation_id=bound_conversation_id,
        conversation_type=conversation_type,
        requester_person_id=room.tagger.person_id,
        created_at=now_fn(), state=ComputeJobState.PENDING,
        completed_at=None, result_url=None, result_summary=None, error=None,
    )
    await compute_jobs.create(job)
    return {"status": "ok", "still_working": True}
```

**Verified against `remind`'s real closure** (`nora/agent/tools.py:2630-2824`, `build_tool_specs`'s real signature at `tools.py:1111-1185`) by an independent subagent review — the illustrative `conversation`/`clock` names in an earlier draft did not exist anywhere in the real code and this would not have run. The correct bindings, confirmed against source:

| illustrative name (earlier draft) | real binding | source |
|---|---|---|
| `conversation.tagger` | `room.tagger` | `Room`, a public property — `nora/agent/room.py:161-162` |
| `conversation.platform` | `room.platform` | `nora/agent/room.py:145-146` |
| `conversation.conversation_id` | `bound_conversation_id` | the captured-once value at `tools.py:1185` — using a bare `conversation_id` directly would reopen the cross-group leak DEC-24 fixed |
| `conversation.conversation_type` | `conversation_type` | already a bare closure parameter, no prefix needed |
| `conversation.tagger.person_id` | `room.tagger.person_id` | matches `remind`'s own `requester_person_id=tagger.person_id` at `tools.py:2781` |
| `clock()` | `now_fn()` | `now_fn = clock or utc_now` at `tools.py:1171` — bare `clock` can be `None` |

The tool body above has been corrected to use these real names directly, rather than left as illustrative placeholders needing a later substitution.

- [ ] **Step 3: Register the tool**

Add `_spec("make_artifact", make_artifact)` to the returned tuple in `build_tool_specs` (near `tools.py:3174-3198`, beside `_spec("remind", remind)`).

- [ ] **Step 4: Thread `ComputeBackend`/`ComputeJobStore` through `ToolRegistry`**

In `nora/agent/registry.py`:
- Add `from ..domain.compute import ComputeJobStore` and `from ..domain.ports import ComputeBackend` near the existing `from ..domain.reminders import ReminderCaps, ReminderStore` (`registry.py:33`).
- Add `compute_backend: ComputeBackend | None = None, compute_jobs: ComputeJobStore | None = None` to `ToolRegistry.__init__`'s parameters, near `reminders`/`reminder_caps` (`registry.py:82-83`).
- Store as `self._compute_backend = compute_backend`, `self._compute_jobs = compute_jobs`.
- Thread them into `build_tool_specs`'s call inside `specs()`, near `reminders=self._reminders, reminder_caps=self._caps` (`registry.py:467`): add `compute_backend=self._compute_backend, compute_jobs=self._compute_jobs,`.

- [ ] **Step 5: Write a test for the fast path**

Add to `tests/test_reminder_tools.py` (`remind`'s own real test file, confirmed by an independent subagent review — not the guessed "whatever file covers remind"), matching its real, working pattern for calling a native tool directly: build `{spec.name: spec.call for spec in specs}` from `registry.specs(...)`, then call the tool by name from that dict (`tests/test_reminder_tools.py:82-106`), rather than a `_call_tool` helper — no such helper exists anywhere in this test suite, and an earlier draft of this task cited one that would have sent an implementer hunting for something that isn't there:

```python
async def test_make_artifact_returns_the_result_inline_when_fast(registry_with_compute):
    registry, backend, jobs = registry_with_compute
    specs = registry.specs(...)  # match tests/test_reminder_tools.py's own real setup call
    tools = {spec.name: spec.call for spec in specs}
    result = await tools["make_artifact"](task="write a haiku")
    assert result["status"] == "ok"
    assert "url" in result


async def test_make_artifact_falls_back_to_still_working_when_slow(registry_with_compute):
    registry, backend, jobs = registry_with_compute
    # Arrange a job that stays RUNNING past the sync poll window -- see
    # FakeComputeBackend.set_result's polls_before_done, Phase 2 Task 1.
    specs = registry.specs(...)  # match tests/test_reminder_tools.py's own real setup call
    tools = {spec.name: spec.call for spec in specs}
    result = await tools["make_artifact"](task="a long research task")
    assert result == {"status": "ok", "still_working": True}
    assert len(jobs._jobs) == 1  # a durable row was written
```

- [ ] **Step 6: Run tests, type-check, lint**

Run: `uv run pytest -q -k make_artifact && uv run mypy nora/agent && uv run ruff check nora/`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add nora/agent/tools.py nora/agent/registry.py tests/
git commit -m "feat(compute): add the make_artifact native tool"
```

---

### Task 6: Composition wiring + boot recovery

**Files:**
- Modify: `nora/app/composition.py`
- Modify: `docker-compose.yml` (Nora's own service — `SMITH_SANDBOX_IMAGE`, `SMITH_JOB_TIMEOUT_SECONDS`, `SMITH_MAX_CONCURRENT_JOBS`, `GOOGLE_MAPS_API_KEY` env vars, matching whatever config-loading convention `cfg.compute` would need — check `nora/config.py` for the existing `[heartbeat]`/`[reminders]` section pattern and mirror it for a new `[compute]` section)

- [ ] **Step 1: Add a `[compute]` config section**

In `nora/config.py`, mirroring `[heartbeat]`'s own `Config` model shape:

```python
class ComputeConfig(BaseModel):
    enabled: bool = False
    sandbox_image: str = "smith:latest"
    job_timeout_seconds: int = 300
    max_concurrent_jobs: int = 2
    poll_tick_seconds: float = 10.0
```

Add `compute: ComputeConfig = ComputeConfig()` to the top-level `Config` model, matching wherever `heartbeat: HeartbeatConfig` is already declared.

- [ ] **Step 2: Wire `LocalDockerBackend`, `SqlComputeJobStore`, `ComputeJobPoller`**

In `nora/app/composition.py`, mirroring the reminders/heartbeat block exactly (store built unconditionally beside the other stores, since `make_artifact` needs `compute_jobs` whether or not the poller itself is enabled — the same reasoning `composition.py`'s own comment gives for `reminders` at the block starting `composition.py:204`):

```python
compute_jobs: ComputeJobStore = SqlComputeJobStore(engine)

compute_backend: ComputeBackend | None = None
if cfg.compute.enabled:
    import docker

    compute_backend = LocalDockerBackend(
        docker_client=docker.from_env(),
        drop_internal_url=f"http://drop:{cfg.drop.internal_port}",  # match whatever the actual drop config field is named
        drop_master_token=cfg.drop.token,
        # Smith's own model credential (Nora Compute Phase 2's own fix for a
        # gap an earlier draft of THAT plan shipped: Smith had no way to call
        # an LLM at all). Reuses the same OmniRoute-shaped values Nora's own
        # agent is configured with, not a second provider story.
        model_api_key=os.environ["OMNIROUTE_API_KEY"],
        model_base_url=cfg.llm.base_url,  # match whatever the actual field name is
        model_name=cfg.llm.model,  # match whatever the actual field name is
        google_maps_api_key=cfg.maps.api_key,
        sandbox_image=cfg.compute.sandbox_image,
        job_timeout_seconds=cfg.compute.job_timeout_seconds,
        max_concurrent_jobs=cfg.compute.max_concurrent_jobs,
        workspace_root=Path("/data/compute-workspaces"),  # match this repo's own data-volume convention
    )
```

Thread `compute_backend=compute_backend, compute_jobs=compute_jobs` into `ToolRegistry(...)`'s construction, near `reminders=reminders, reminder_caps=...` (`composition.py:545-577`).

**Boot recovery runs UNCONDITIONALLY, not gated behind `cfg.compute.enabled`** — placed right after `compute_jobs` is constructed above, alongside the existing unconditional `await recover(store=reminders, ...)` at `composition.py:637`, not inside the `if cfg.compute.enabled` block:

```python
await compute_poller_recover(store=compute_jobs, metrics=metrics, now=datetime.now(UTC))
```

This is not a style preference — `nora/app/heartbeat.py:432-463`'s own `recover()` docstring explains why reminders' sweep is unconditional: "a heartbeat that has been switched off is exactly the state in which a stranded FIRING row would otherwise sit forever." An earlier draft of this plan put the compute-job equivalent INSIDE the `cfg.compute.enabled` gate, which means a deployment that crashes mid-delivery and then flips `[compute] enabled=false` (to disable the feature, unrelated to the crash) leaves any `FIRING` `ComputeJob` rows stranded forever — exactly the failure mode reminders' own unconditional recovery exists to prevent, silently reopened. Fixed here per an independent subagent review that caught this by direct comparison against `heartbeat.py`'s real code and its own stated reasoning.

After `fire_path` is constructed (after the block at `composition.py:657-683`), add the poller itself — matching `VersionAnnouncer`'s exact wiring shape (`composition.py:702-734`). The poller's `run()` loop stays gated on `cfg.compute.enabled` (only the recovery sweep above is unconditional):

```python
if cfg.compute.enabled and fire_path is not None and compute_backend is not None:
    compute_poller = ComputeJobPoller(
        store=compute_jobs, backend=compute_backend,
        speak=fire_path.speak, slots=slots, metrics=metrics,
        tick_seconds=cfg.compute.poll_tick_seconds, clock=lambda: datetime.now(UTC),
    )
    background.spawn(compute_poller.run())
```

(Import `ComputeJobPoller` and its `recover` function — aliased `compute_poller_recover` to avoid a name collision with the existing `recover` imported from `nora.app.heartbeat` at `composition.py:80-81` — plus `SqlComputeJobStore`, `LocalDockerBackend`, `ComputeJobStore`, `ComputeBackend` at the top of the file, mirroring the existing import block shape.)

- [ ] **Step 3: Add the compose env vars**

In `docker-compose.yml`'s `nora` service `environment:` block (find via `grep -n "^  nora:" docker-compose.yml`):

```yaml
      - SMITH_SANDBOX_IMAGE=smith:latest
      - SMITH_JOB_TIMEOUT_SECONDS=300
      - SMITH_MAX_CONCURRENT_JOBS=2
      - GOOGLE_MAPS_API_KEY=${GOOGLE_MAPS_API_KEY:?set GOOGLE_MAPS_API_KEY in .env}
```

(Match this repo's own convention of whether these belong as env vars read directly by `composition.py`'s `os.environ`, or as `config.toml` values under a new `[compute]` table with `config.toml`'s own precedent — check how `[heartbeat]` itself is actually sourced before picking one; Step 1 assumed `config.toml`, confirm against the real pattern before finalizing.)

Also give `nora`'s own container access to the Docker socket, since `LocalDockerBackend` needs `docker.from_env()` to reach the host's Docker daemon:

```yaml
    volumes:
      # ...existing volumes...
      - /var/run/docker.sock:/var/run/docker.sock
```

**Named explicitly, not glossed over:** mounting the Docker socket gives Nora's own container the ability to launch (and, transitively given Docker's own model, potentially escape into) arbitrary containers on the host — a real widening of Nora's own container's privilege, beyond anything else this design has needed so far. This wasn't discussed anywhere in the Nora Compute design conversation and is a genuine gap this plan is surfacing, not solving — flagged in Self-Review below rather than shipped silently.

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest -q && uv run ruff check nora/ evals/ tests/ && uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence && uv run lint-imports`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add nora/config.py nora/app/composition.py docker-compose.yml
git commit -m "feat(compute): wire LocalDockerBackend and ComputeJobPoller into composition"
```

---

### Task 7: `design/09-decision-index.md` + `RULE_TESTS`

**Files:**
- Modify: `design/09-decision-index.md`
- Modify: `tests/test_acceptance_rules.py`

- [ ] **Step 1: Add `DEC-99`**

Following `DEC-97`'s exact format (a bold headline sentence, numbered sub-decisions, a trailing test-reference list), add after `DEC-97`:

```markdown
| DEC-99 | **`make_artifact` is a third `Occasion` on the fire path, not a fourth send mechanism (Nora Compute spec, following DEC-97's precedent).** Smith runs in an isolated sandbox on its own per-job credential and registers its own output with Drop directly; `make_artifact` polls for ~15s before falling to a durable `ComputeJob` row delivered later by `ComputeJobPoller` through `FirePath.speak` — the same `deliver.build`-through-`FirePath` path reminders and version announcements already use (R-1b). **(1) No new egress path** — `ComputeJobPoller` reuses `fire_path.speak`, never a new `Deliverer` caller. **(2) At-most-once via compare-and-set claim**, mirroring `ReminderStore.claim` without the recurrence half a compute job has no use for. **(3) The Docker-socket mount this needs on Nora's own container is a real privilege widening, named here rather than silently shipped** — LocalDockerBackend needs `docker.from_env()` to launch Smith's sandbox containers, and that access is not itself sandboxed. | `tests/unit/test_compute_poller.py::test_a_job_never_delivered_twice`, `tests/contract/test_compute_job_store_contract.py`, `tests/test_acceptance_rules.py::RULE_TESTS[R-43]` |
```

- [ ] **Step 2: Add `R-43`**

In `tests/test_acceptance_rules.py`, after the `"R-42"` entry (line 175):

```python
    # R-43: a compute job is delivered AT MOST ONCE, ever — the same
    # discipline R-37/R-37b hold for reminders, applied to the third
    # Occasion (DEC-99). The next free number after R-42.
    "R-43": "test_a_job_never_delivered_twice",
```

- [ ] **Step 3: Confirm the named test exists and is collected**

Run: `uv run pytest --collect-only -q | grep test_a_job_never_delivered_twice`
Expected: one match — `tests/unit/test_compute_poller.py::test_a_job_never_delivered_twice` (written in Task 4).

Run: `uv run pytest tests/test_acceptance_rules.py -v`
Expected: PASS — `test_every_enforced_rule_has_a_named_test` now includes `R-43` and finds it collected.

- [ ] **Step 4: Commit**

```bash
git add design/09-decision-index.md tests/test_acceptance_rules.py
git commit -m "docs(decisions): add DEC-99 and R-43 for the Nora Compute delivery path"
```

---

## Self-Review

**Spec coverage** (against `docs/superpowers/specs/2026-08-12-nora-compute-design.md` §2, §4, §9, §12):
- `make_artifact` hybrid sync/async, ~15s poll — Task 5. ✓
- Durable outbox for the slow path, mirroring the reminders/heartbeat shape — Tasks 1-4. ✓
- Delivery via `FirePath.speak` as a third `Occasion`, matching the `VersionAnnouncer`/DEC-97 precedent rather than inventing a new send path (R-1b) — Task 4. ✓
- `NATIVE_TOOL_BUDGETS` entry, decision-index entry, `RULE_TESTS` registration (flagged as open in the original spec's §12) — Tasks 5, 7. ✓
- `artifact_id` threading from `make_artifact` through to the job row, for the update path Phase 1 built — Task 5, `ComputeJob.artifact_id`. ✓

**Gap found and named, not silently fixed:** the Docker socket mount Task 6 needs (so `LocalDockerBackend` can launch containers) is a real widening of Nora's own container's privilege that no earlier phase or the original spec discussed. Named explicitly in Task 6's own step and carried into `DEC-99`'s own text as sub-decision (3), rather than a quiet `docker-compose.yml` line with no record of the tradeoff it represents. Whether this is acceptable, or whether `LocalDockerBackend` should instead run as a small separate privileged sidecar service Nora's own container talks to over a socket it doesn't itself hold, is a real open question this plan surfaces but does not resolve.

**Placeholder scan:** several steps in Tasks 5 and 6 explicitly say "confirm against the real X before finalizing" (the exact local variable names `remind` uses for conversation context; whether `[compute]` config is sourced from `config.toml` or environment variables the way `[heartbeat]` actually is) rather than asserting a shape this plan hasn't verified. These are flagged as verification steps with a concrete action, not vague placeholders — consistent with how Plans 2-3 handled the docker-py timeout exception type and the Strands SDK call shape.

**Type consistency:** `ComputeJob`/`ComputeJobState` used identically across Tasks 1 (domain), 2 (`SqlComputeJobStore`), 3 (`FakeComputeJobStore`), 4 (`ComputeJobPoller`), 5 (`make_artifact`). `compute_occasion`'s signature (`job: ComputeJob, result: JobResult`) matches what Task 4's `_maybe_deliver` actually calls it with.

**Fixed after independent subagent review, not shipped as originally drafted** — this review read the REAL `nora/domain/reminders.py`, `heartbeat.py`, `fire.py`, `tools.py`, `registry.py`, `composition.py`, `design/09-decision-index.md`, and `tests/test_acceptance_rules.py` in full, rather than trusting this plan's own claims about them:
1. **`DEC-99`, not `DEC-98`** — `DEC-98` is already taken (media_fetch keeps its name). Worth naming precisely: `DEC-97` itself carries a merge note recording that it was originally authored as `DEC-79` and collided with another decision, requiring a blind renumber that "rewrote seven unrelated location-trust references before the pre-deploy review caught it." This plan was about to repeat that exact, already-documented mistake. Fixed throughout — the decision-index entry, `R-43`'s own comment, and the commit message.
2. **`make_artifact`'s tool body used invented closure names that don't exist anywhere in the real code** — `conversation.tagger`, `conversation.platform`, bare `clock()`. Verified against `remind`'s real body and corrected to the actual bindings: `room.tagger`, `room.platform`, `bound_conversation_id` (using a bare `conversation_id` would reopen the cross-group leak DEC-24 fixed), `room.tagger.person_id`, `now_fn()`. As originally drafted, this code would not run.
3. **The at-most-once test (`test_a_job_never_delivered_twice`, the one `R-43` is pinned to) was decorative.** It called `tick()` twice sequentially after the job had already resolved to `DONE` — since `pending()` already excludes non-`PENDING` rows, the second call never even reached `_maybe_deliver`, so the test would pass identically even if `claim()`'s compare-and-set were entirely broken (e.g. always returning `True`). Fixed by pre-claiming the row directly (simulating a race already won elsewhere) and confirming `_maybe_deliver` then refuses to deliver it — a test that actually fails if the CAS is broken.
4. **Boot recovery was incorrectly gated behind `cfg.compute.enabled`**, breaking the exact discipline `nora/app/heartbeat.py`'s own `recover()` deliberately keeps unconditional: a deployment that crashes mid-delivery and later disables the compute feature (for reasons unrelated to the crash) would leave `FIRING` rows stranded forever. Fixed by pulling the recovery call out of the gate, run unconditionally right after `compute_jobs` is constructed — only the poller's own `run()` loop stays gated.
5. **The `_call_tool` test helper cited in Task 5 doesn't exist anywhere in the test suite.** Fixed to use the real, verified pattern from `tests/test_reminder_tools.py` (`{spec.name: spec.call for spec in specs}` via `registry.specs(...)`).

Also carried forward from Plan 2's own fixes: `LocalDockerBackend`'s constructor gained `model_api_key`/`model_base_url`/`model_name` parameters (Smith otherwise has no way to call an LLM at all) — this plan's own Task 6 composition-wiring code block has been updated to pass them.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-12-nora-compute-04-make-artifact.md`. This is the last of the four phased plans (Phases 1-4 now all exist). Two execution options, decided per-plan or across all four:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach, and starting with which plan?
