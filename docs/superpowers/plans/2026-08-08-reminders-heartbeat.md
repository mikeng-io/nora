# Reminders + Heartbeat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Nora her first unprompted-speech path — a background tick that fires stored reminders into the conversation they were set in, through a dedicated fire path that reuses the tag pipeline's stages without pretending to be a tag.

**Architecture:** A `reminders` table holds one-shot rows. A `HeartbeatLoop` in `nora.app` wakes on a fixed tick, re-checks `policy.toml`, acquires the conversation's serialisation slot, and runs one turn per conversation through stage functions borrowed from the engagement pipeline — deliberately skipping ingest gating, the trigger gate, the audit write, the memory feed, admission and reactions, because each is wrong for a synthetic turn. The tag pipeline's in-flight lock and FIFO queue are hoisted into a shared collaborator first, because both paths must contend for the same slot.

**Tech Stack:** Python 3.13, asyncio, SQLAlchemy Core + Alembic (Postgres), Strands Agents SDK, prometheus-client, pytest, mypy (strict on `nora.app`), import-linter.

**Source spec:** `docs/superpowers/specs/2026-08-08-reminders-heartbeat-design.md` (settled after six review rounds). Section references below (§N) point into it.

## Global Constraints

- **Dependencies via `uv add`, never `pip install`** (operational constraint 5).
- **Gate commands, all four, before every commit:**
  `uv run pytest -q` · `uv run ruff check nora/ evals/ tests/` · `uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence` · `uv run lint-imports`
- **Placement is `nora/app/`** — a new top-level `nora/reminders/` package is forbidden: it would be absent from every import-linter contract, and would trip `tests/test_architecture_doc.py`. `nora.app.*` is inside the mypy strict override, so everything here is fully typed. `nora.app` must not import `strands` directly (`tests/test_boundaries.py:54`).
- **Test-seam policy:** collaborators through `__init__` only — no `*_fn` hooks, no `_for_test` params, no module-level mutable singletons.
- **A port never gains a second implementation, including a test fake, without a shared contract suite both pass** (`tests/contract/`).
- **Migrations are hand-written.** The schema gate is `tests/infra/test_schema_parity.py`, not `alembic check`, which is blind to CHECK constraints.
- **Never trust an enumeration in the spec.** Six review rounds produced four separate under-counts of one metric's call sites. Where a task says "all N sites", **grep first and reconcile** — the grep is the authority, the number is a hint.
- **This branch is `design/reminders-heartbeat` in a worktree.** Another session is live on the main checkout. Never write to `/home/norty/workspace/nora`. Never commit to `master`.

---

## File Structure

**Created:**

| path | responsibility |
|---|---|
| `nora/engagement/slots.py` | `ConversationSlots` — R-10's in-flight lock + R-30's FIFO queue as one collaborator both paths share |
| `nora/domain/reminders.py` | `Reminder` dataclass + `ReminderStore` Protocol (the port) |
| `nora/infra/sql/reminders.py` | `SqlReminderStore` — the Postgres adapter |
| `nora/testing/reminders.py` | `FakeReminderStore` — the in-memory fake |
| `tests/contract/test_reminder_store_contract.py` | the shared contract suite both implementations pass |
| `nora/app/heartbeat.py` | `HeartbeatLoop` — the tick, policy re-check, slot acquisition, outcome recording |
| `nora/app/fire.py` | `FirePath` — one fire turn: `TagContext`, prefill, invoke, deliver |
| `nora/doctrine/silence.md` | the extracted `[tag]`-only silence rules |
| `migrations/versions/<rev>_reminders.py` | hand-written table + CHECK + named index |

**Modified:**

| path | change |
|---|---|
| `nora/engagement/pipeline.py` | takes `ConversationSlots` through `__init__`; loses `_in_flight`/`_queues`/`_dispatch_next` |
| `nora/engagement/stages/admission.py` | takes `slots` instead of a raw `in_flight` set |
| `nora/agent/prefill.py` | `build_prefill` gains a `header:` parameter |
| `nora/agent/room.py` | public `tagger` property |
| `nora/agent/tools.py` | `remind` / `list_reminders` / `cancel_reminder`; `fire_turn` keyword |
| `nora/agent/registry.py` | `reminders=` collaborator; `fire_turn` threaded through `specs()`/`build()` |
| `nora/doctrine/{identity,voice,conduct,constraints}.md` | `applies_to: [tag, heartbeat]`; conduct loses 3 sentences, keeps DEC-40 |
| `nora/app/composition.py` | wire slots, store, loop; second `applies_to` call site |
| `nora/app/cli.py` | `nora reminders list` (read-only) |
| `nora/obs/metric_specs.py` | four new series + `audience` label on `nora_silent_failures_total` |
| `nora/config.py`, `config.toml`, `config.example.toml` | `[heartbeat]`, `[reminders]` |
| `docs/runbook.md` | A8 expression filtered to `audience="tag"` |
| `ARCHITECTURE.md` | new modules |

---

### Task 1: Hoist conversation serialisation into `ConversationSlots`

**Why first and alone:** this is the only change to the live tag path in the whole design. It must land with no new behaviour beside it, so a reviewer can judge "did the FIFO guarantee survive?" without also judging reminders. It blocks Task 4 (the tick), not Tasks 2–3.

**Files:**
- Create: `nora/engagement/slots.py`
- Modify: `nora/engagement/pipeline.py:128-131` (attrs), `:156-172` (`run`), `:204-217` (`_dispatch_next`), `:370-380` (`finally`)
- Modify: `nora/engagement/stages/admission.py:66-108`
- Modify: `nora/app/composition.py:474`, `nora/app/offline.py:85`
- Test: `tests/unit/test_conversation_slots.py` (new), `tests/unit/test_engagement_pipeline.py` (existing, ~20 assertions)

**Interfaces:**
- Produces: `ConversationSlots(maxlen: int, spawn: Callable[[Coroutine[Any, Any, None]], Any])` with `maxlen: int` (property), `depth(key) -> int`, `held(key) -> bool`, `acquire(key) -> None`, `try_acquire(key) -> bool`, `enqueue(key, factory: Callable[[], Coroutine[Any, Any, None]]) -> None`, `release_and_dispatch(key) -> None`, `__len__() -> int`. Key is `tuple[str, str]` = `(platform, conversation_id)`.
- Consumes: `ConversationQueues` from `nora/engagement/queue.py`, unchanged.

**The coroutine-factory rule:** `enqueue` takes a **factory**, not a coroutine. `_dispatch_next` currently does `self._spawn(self._run_queued(request, indicator))` — a direct call into a pipeline method. Storing a factory (`lambda: self._run_queued(request, indicator)`) means `ConversationSlots` needs only `spawn` and never a reference back to the pipeline, which is what keeps this inside CLAUDE.md's "no `*_fn` hooks" rule: `spawn` is a collaborator passed at construction, not a callback seam.

- [ ] **Step 1: Write the failing test for acquire/release/dispatch**

```python
# tests/unit/test_conversation_slots.py
import asyncio
import pytest
from nora.engagement.slots import ConversationSlots

KEY = ("signal", "conv-1")


def _slots(maxlen: int = 16) -> tuple[ConversationSlots, list]:
    spawned: list = []
    slots = ConversationSlots(maxlen=maxlen, spawn=lambda coro: spawned.append(coro))
    return slots, spawned


def test_acquire_then_held_then_release():
    slots, _ = _slots()
    assert slots.held(KEY) is False
    slots.acquire(KEY)
    assert slots.held(KEY) is True
    slots.release_and_dispatch(KEY)
    assert slots.held(KEY) is False


def test_try_acquire_is_false_while_held():
    slots, _ = _slots()
    assert slots.try_acquire(KEY) is True
    assert slots.try_acquire(KEY) is False
    slots.release_and_dispatch(KEY)
    assert slots.try_acquire(KEY) is True


def test_release_dispatches_the_next_factory_and_reacquires():
    slots, spawned = _slots()
    slots.acquire(KEY)
    ran: list[str] = []

    async def _turn(name: str) -> None:
        ran.append(name)

    slots.enqueue(KEY, lambda: _turn("first"))
    slots.enqueue(KEY, lambda: _turn("second"))
    assert slots.depth(KEY) == 2

    slots.release_and_dispatch(KEY)
    assert slots.held(KEY) is True          # re-acquired for the dequeued turn
    assert slots.depth(KEY) == 1
    assert len(spawned) == 1
    asyncio.run(spawned[0])
    assert ran == ["first"]


def test_release_with_empty_queue_leaves_the_slot_free():
    slots, spawned = _slots()
    slots.acquire(KEY)
    slots.release_and_dispatch(KEY)
    assert slots.held(KEY) is False
    assert spawned == []
    assert len(slots) == 0


def test_nothing_is_retained_once_drained():
    slots, spawned = _slots()
    slots.acquire(KEY)

    async def _noop() -> None:
        return None

    slots.enqueue(KEY, _noop)
    slots.release_and_dispatch(KEY)          # dequeues the only item
    slots.release_and_dispatch(KEY)          # that turn finishes, nothing behind it
    assert len(slots) == 0
    assert slots.held(KEY) is False
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd /tmp/nora-reminders && uv run pytest tests/unit/test_conversation_slots.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nora.engagement.slots'`

- [ ] **Step 3: Write `ConversationSlots`**

```python
# nora/engagement/slots.py
"""R-10's single-in-flight lock and R-30's per-conversation FIFO queue, as ONE
collaborator.

WHY THIS EXISTS
---------------
Both lived as private attributes of `EngagementPipeline`, with the lock's
ownership split across `admission.run` (acquires) and the pipeline's `finally`
(releases) — a split documented on both halves because getting it wrong
strands a queued tag forever.

The heartbeat fire path (`nora.app.heartbeat`) must contend for the SAME slot:
a reminder must not run while a tag is in flight for that conversation, and a
tag must not start while a reminder is firing. Reaching into the pipeline's
privates to do that would leave `_dispatch_next` uncalled on release, which
strands whatever queued behind the fire turn until the NEXT tag arrives — and
that tag would then jump the queue. So the pair moves here, behind one object
both paths hold.

WHY `enqueue` TAKES A FACTORY
-----------------------------
The queued work is a pipeline method (`_run_queued`). Storing a bound coroutine
would make this class hold a reference back into the pipeline; storing a
CALLBACK would be the `*_fn` hook CLAUDE.md's test-seam policy forbids. A
zero-argument factory is neither: the pipeline closes over its own state, and
this class needs nothing but `spawn`, which it takes at construction like any
other collaborator.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from .queue import ConversationQueues

Key = tuple[str, str]
Factory = Callable[[], Coroutine[Any, Any, None]]


class ConversationSlots:
    def __init__(
        self, *, maxlen: int, spawn: Callable[[Coroutine[Any, Any, None]], Any],
    ) -> None:
        self._in_flight: set[Key] = set()
        self._queues: ConversationQueues[Key, Factory] = ConversationQueues(maxlen=maxlen)
        self._spawn = spawn

    @property
    def maxlen(self) -> int:
        return self._queues.maxlen

    def depth(self, key: Key) -> int:
        return self._queues.depth(key)

    def held(self, key: Key) -> bool:
        return key in self._in_flight

    def acquire(self, key: Key) -> None:
        """R-10 acquire. The caller owns the matching `release_and_dispatch`."""
        self._in_flight.add(key)

    def try_acquire(self, key: Key) -> bool:
        """Acquire iff free. The heartbeat's entry point: it never queues and
        never waits — a tick that cannot get the slot leaves its rows `pending`
        and retries next tick (§5.3)."""
        if key in self._in_flight:
            return False
        self._in_flight.add(key)
        return True

    def enqueue(self, key: Key, factory: Factory) -> None:
        self._queues.enqueue(key, factory)

    def release_and_dispatch(self, key: Key) -> None:
        """Release the slot, then immediately start the next queued turn if
        there is one — re-acquiring the slot for it. Both operations are
        synchronous with no await between them, so no arriving tag can slip
        into the run-now path in the gap."""
        self._in_flight.discard(key)
        factory = self._queues.dequeue(key)
        if factory is None:
            return
        self._in_flight.add(key)
        self._spawn(factory())

    def __len__(self) -> int:
        """Conversations with at least one waiting turn — for the
        nothing-is-retained assertions."""
        return len(self._queues)
```

- [ ] **Step 4: Run and watch it pass**

Run: `uv run pytest tests/unit/test_conversation_slots.py -v`
Expected: 5 passed

- [ ] **Step 5: Change `admission.run` to take `slots`**

Replace the `in_flight: set[tuple[str, str]]` parameter with `slots: ConversationSlots`, and the two usages:

```python
# nora/engagement/stages/admission.py
def run(
    *,
    message: NormalizedMessage,
    identity: PlatformIdentity,
    debounce: Debounce,
    slots: ConversationSlots,
    metrics: Any,
) -> Admission:
    """Classify a tag. On `RUN_NOW`, `key` has already been acquired in
    `slots` — the caller (the pipeline) owns releasing it. On `QUEUED`, the
    caller owns the actual enqueue. See the module docstring."""
    if message.sender_id in identity.aliases:               # R-12
        log.info("ignoring a trigger from our own account")
        return Admission.REJECTED

    key = (message.platform, message.conversation_id)
    if not debounce.allow(key):                             # R-9
        log.info("debounced tag in %s", message.conversation_id)
        metrics.inc("nora_tags_debounced_total")
        return Admission.REJECTED

    if slots.held(key):                                     # R-10: a turn is running
        depth, maximum = slots.depth(key), slots.maxlen
        if depth >= maximum:                                # R-30: flood guard
            log.warning(
                "tag_dropped_queue_full in %s (depth=%d, max=%d)",
                message.conversation_id, depth, maximum,
            )
            metrics.inc("nora_tags_dropped_total", reason="queue_full")
            return Admission.QUEUE_FULL
        log.info("tag_queued in %s (depth=%d)", message.conversation_id, depth)
        metrics.inc("nora_tags_total", platform=message.platform)
        metrics.inc("nora_tags_queued_total")
        return Admission.QUEUED

    slots.acquire(key)                                      # R-10: acquire
    metrics.inc("nora_tags_total", platform=message.platform)
    return Admission.RUN_NOW
```

`queue_depth` and `queue_max` parameters are **deleted** — `slots` carries both, and two sources for one fact is how they drift.

- [ ] **Step 6: Rewire `EngagementPipeline`**

Delete `self._in_flight` and `self._queues` from `__init__`; take `slots: ConversationSlots` instead of `queue_max: int`. Delete `_dispatch_next` entirely. Then three call sites:

```python
# run(): the admission call
verdict = admission.run(
    message=message, identity=identity, debounce=self._debounce,
    slots=self._slots, metrics=self._metrics,
)
...
if verdict is Admission.QUEUED:
    self._slots.enqueue(key, lambda: self._run_queued(request, indicator))
    return

# _run_admitted()'s finally: was `self._in_flight.discard(key)` then `self._dispatch_next(key)`
finally:
    self._slots.release_and_dispatch(key)
```

The `finally` is the load-bearing edit: release and dispatch were two statements and are now one call, which is the point — they can no longer be separated by an early return or a new exception path.

- [ ] **Step 7: Update the four constructors**

```python
# nora/app/composition.py:474 — build the slots once, pass to the pipeline
slots = ConversationSlots(maxlen=cfg.engagement.queue.max_depth, spawn=background.spawn)
pipeline = EngagementPipeline(..., slots=slots, ...)   # was queue_max=...
```

Same shape in `nora/app/offline.py:85`, `tests/unit/test_engagement_pipeline.py:228`, `tests/architecture/test_no_retained_per_tag_state.py:113`. Keep `slots` in scope in `composition.py` — Task 4 needs the same instance.

- [ ] **Step 8: Run the full suite; fix the ~20 assertions**

Run: `uv run pytest tests/unit/test_engagement_pipeline.py tests/architecture/ -v`
Expected: failures in assertions reading `pipeline._in_flight` / `pipeline._queues`. Rewrite each against `slots.held(...)` / `slots.depth(...)` / `len(slots)`. Do **not** re-add the private attributes to satisfy a test.

- [ ] **Step 9: Prove the FIFO guarantee survived**

Run: `uv run pytest -q -k "fifo or queue or in_flight or admission"`
Expected: PASS, including `test_three_tags_for_one_conversation_are_answered_in_fifo_order`. **This test is the whole point of the task.** If it fails, the hoist is wrong — do not adjust the test.

- [ ] **Step 10: All four gates, then commit**

```bash
uv run pytest -q && uv run ruff check nora/ evals/ tests/ \
  && uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence \
  && uv run lint-imports
git add nora/engagement/slots.py nora/engagement/pipeline.py \
        nora/engagement/stages/admission.py nora/app/composition.py \
        nora/app/offline.py tests/
git commit -m "refactor(engagement): hoist the in-flight lock and FIFO queue into ConversationSlots

R-10's lock and R-30's queue were private to EngagementPipeline, with
ownership split across admission (acquires) and the pipeline's finally
(releases). The heartbeat fire path must contend for the same slot, and an
outside acquirer that released without dispatching would strand queued tags
until the next tag arrived — which would then jump the queue.

release_and_dispatch is now one call rather than two statements, so the pair
cannot be separated by an early return. enqueue takes a zero-argument
coroutine factory, so the collaborator needs only spawn and never a reference
back into the pipeline: not a *_fn hook.

No behaviour change. test_three_tags_for_one_conversation_are_answered_in_fifo_order
is the gate."
```

---

### Task 2: `ReminderStore` — port, adapter, fake, contract suite

**Files:**
- Create: `nora/domain/reminders.py`, `nora/infra/sql/reminders.py`, `nora/testing/reminders.py`, `tests/contract/test_reminder_store_contract.py`
- Create: `migrations/versions/<rev>_reminders.py`
- Modify: `nora/infra/sql/schema.py`, `tests/infra/pg_schema_baseline.sql`
- Test: `tests/infra/test_schema_parity.py` (existing, must pass)

**Interfaces:**
- Produces: `Reminder` frozen dataclass with fields `id: UUID`, `requester_person_id: str`, `platform: str`, `conversation_id: str`, `conversation_type: str`, `text: str`, `created_at: datetime`, `due_at: datetime`, `state: ReminderState`, `fired_at: datetime | None`, `outcome: str | None`. `ReminderState` is a `StrEnum` of `PENDING|FIRING|DONE|CANCELLED|FAILED`.
- Produces: `ReminderStore` Protocol — `create(reminder) -> None`, `due(now, limit) -> Sequence[Reminder]`, `pending_for(conversation_id) -> Sequence[Reminder]`, `count_pending(requester_person_id) -> int`, `mark(id, state, *, fired_at=None, outcome=None) -> bool`, `cancel(id, conversation_id) -> bool`, `sweep_firing() -> int`, `aclose() -> None`.

`cancel` takes `conversation_id` because a uuid carries no scope of its own (§7.3) — the store enforces it, not the tool.

`mark` returns `bool` (did the row change) so the caller can distinguish a lost race from a no-op.

- [ ] **Step 1: Write the contract suite first — it is the spec for both implementations**

```python
# tests/contract/test_reminder_store_contract.py
"""Every ReminderStore implementation passes this. CLAUDE.md: a port never
gains a second implementation — including a test fake — without a shared
contract suite both pass."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from nora.domain.reminders import Reminder, ReminderState

UTC = timezone.utc
T0 = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _row(**kw) -> Reminder:
    base = dict(
        id=uuid.uuid4(), requester_person_id="miles", platform="signal",
        conversation_id="conv-1", conversation_type="group", text="洗衫",
        created_at=T0, due_at=T0 + timedelta(minutes=40),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
    )
    base.update(kw)
    return Reminder(**base)


class ReminderStoreContract:
    """Subclass and provide `store`."""

    @pytest.mark.asyncio
    async def test_a_created_row_is_returned_once_due(self, store):
        r = _row()
        await store.create(r)
        assert await store.due(now=T0, limit=10) == []
        due = await store.due(now=r.due_at, limit=10)
        assert [x.id for x in due] == [r.id]

    @pytest.mark.asyncio
    async def test_due_is_ordered_by_due_at(self, store):
        late = _row(due_at=T0 + timedelta(hours=2))
        early = _row(due_at=T0 + timedelta(hours=1))
        await store.create(late)
        await store.create(early)
        due = await store.due(now=T0 + timedelta(hours=3), limit=10)
        assert [x.id for x in due] == [early.id, late.id]

    @pytest.mark.asyncio
    async def test_only_pending_rows_are_due(self, store):
        for state in (ReminderState.DONE, ReminderState.CANCELLED,
                      ReminderState.FAILED, ReminderState.FIRING):
            await store.create(_row(state=state))
        assert await store.due(now=T0 + timedelta(days=1), limit=10) == []

    @pytest.mark.asyncio
    async def test_mark_moves_state_and_reports_the_change(self, store):
        r = _row()
        await store.create(r)
        assert await store.mark(r.id, ReminderState.DONE,
                                fired_at=T0, outcome="sent") is True
        assert await store.due(now=T0 + timedelta(days=1), limit=10) == []
        assert await store.mark(uuid.uuid4(), ReminderState.DONE) is False

    @pytest.mark.asyncio
    async def test_cancel_is_scoped_to_the_conversation(self, store):
        r = _row(conversation_id="conv-1")
        await store.create(r)
        assert await store.cancel(r.id, conversation_id="conv-OTHER") is False
        assert await store.cancel(r.id, conversation_id="conv-1") is True
        assert await store.pending_for("conv-1") == []

    @pytest.mark.asyncio
    async def test_pending_for_is_scoped_and_excludes_finished(self, store):
        mine = _row(conversation_id="conv-1")
        theirs = _row(conversation_id="conv-2")
        done = _row(conversation_id="conv-1", state=ReminderState.DONE)
        for r in (mine, theirs, done):
            await store.create(r)
        assert [x.id for x in await store.pending_for("conv-1")] == [mine.id]

    @pytest.mark.asyncio
    async def test_count_pending_is_per_requester(self, store):
        await store.create(_row(requester_person_id="miles"))
        await store.create(_row(requester_person_id="miles"))
        await store.create(_row(requester_person_id="tyler"))
        assert await store.count_pending("miles") == 2
        assert await store.count_pending("tyler") == 1

    @pytest.mark.asyncio
    async def test_sweep_resolves_firing_rows_to_failed(self, store):
        """§8.2: at-most-once. A crash mid-fire loses that reminder, visibly.
        It is NEVER re-queued — re-queueing double-sends anything that crashed
        after egress, and nothing links a row to its delivered message."""
        stuck = _row(state=ReminderState.FIRING)
        ok = _row(state=ReminderState.PENDING)
        await store.create(stuck)
        await store.create(ok)
        assert await store.sweep_firing() == 1
        assert await store.due(now=T0 + timedelta(days=1), limit=10) == [
            x for x in await store.due(now=T0 + timedelta(days=1), limit=10)
            if x.id == ok.id
        ]
```

- [ ] **Step 2: Bind the suite to the fake and watch it fail**

```python
# append to tests/contract/test_reminder_store_contract.py
from nora.testing.reminders import FakeReminderStore


class TestFakeReminderStore(ReminderStoreContract):
    @pytest.fixture
    def store(self):
        return FakeReminderStore()
```

Run: `uv run pytest tests/contract/test_reminder_store_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nora.domain.reminders'`

- [ ] **Step 3: Write the port**

```python
# nora/domain/reminders.py
"""The reminders port. A reminder is a DEFERRED REQUEST: the row stores the
requester's own sentence, and at fire time it is put to Nora as a request
(spec §2). One-shot — a row fires once, ever."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID


class ReminderState(StrEnum):
    PENDING = "pending"
    FIRING = "firing"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Reminder:
    id: UUID
    requester_person_id: str
    platform: str
    conversation_id: str
    conversation_type: str
    text: str
    created_at: datetime
    due_at: datetime
    state: ReminderState
    fired_at: datetime | None
    outcome: str | None


class ReminderStore(Protocol):
    async def create(self, reminder: Reminder) -> None: ...

    async def due(self, *, now: datetime, limit: int) -> Sequence[Reminder]:
        """PENDING rows whose `due_at` has passed, oldest first."""
        ...

    async def pending_for(self, conversation_id: str) -> Sequence[Reminder]: ...

    async def count_pending(self, requester_person_id: str) -> int: ...

    async def mark(
        self, reminder_id: UUID, state: ReminderState, *,
        fired_at: datetime | None = None, outcome: str | None = None,
    ) -> bool:
        """True if a row changed. False means it was already resolved."""
        ...

    async def cancel(self, reminder_id: UUID, *, conversation_id: str) -> bool:
        """Scoped on purpose: a uuid carries none of its own (§7.3)."""
        ...

    async def sweep_firing(self) -> int:
        """Boot recovery. FIRING rows resolve to FAILED and are never
        re-fired (§8.2, at-most-once). Returns how many."""
        ...

    async def aclose(self) -> None: ...
```

- [ ] **Step 4: Write the fake**

```python
# nora/testing/reminders.py
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from nora.domain.reminders import Reminder, ReminderState


class FakeReminderStore:
    """In-memory ReminderStore. Passes tests/contract/test_reminder_store_contract.py."""

    def __init__(self) -> None:
        self._rows: dict[UUID, Reminder] = {}

    async def create(self, reminder: Reminder) -> None:
        self._rows[reminder.id] = reminder

    async def due(self, *, now: datetime, limit: int) -> Sequence[Reminder]:
        rows = [
            r for r in self._rows.values()
            if r.state is ReminderState.PENDING and r.due_at <= now
        ]
        return sorted(rows, key=lambda r: r.due_at)[:limit]

    async def pending_for(self, conversation_id: str) -> Sequence[Reminder]:
        return sorted(
            (r for r in self._rows.values()
             if r.conversation_id == conversation_id
             and r.state is ReminderState.PENDING),
            key=lambda r: r.due_at,
        )

    async def count_pending(self, requester_person_id: str) -> int:
        return sum(
            1 for r in self._rows.values()
            if r.requester_person_id == requester_person_id
            and r.state is ReminderState.PENDING
        )

    async def mark(
        self, reminder_id: UUID, state: ReminderState, *,
        fired_at: datetime | None = None, outcome: str | None = None,
    ) -> bool:
        row = self._rows.get(reminder_id)
        if row is None:
            return False
        self._rows[reminder_id] = replace(
            row, state=state,
            fired_at=fired_at if fired_at is not None else row.fired_at,
            outcome=outcome if outcome is not None else row.outcome,
        )
        return True

    async def cancel(self, reminder_id: UUID, *, conversation_id: str) -> bool:
        row = self._rows.get(reminder_id)
        if row is None or row.conversation_id != conversation_id:
            return False
        if row.state is not ReminderState.PENDING:
            return False
        self._rows[reminder_id] = replace(row, state=ReminderState.CANCELLED)
        return True

    async def sweep_firing(self) -> int:
        stuck = [r for r in self._rows.values() if r.state is ReminderState.FIRING]
        for r in stuck:
            self._rows[r.id] = replace(r, state=ReminderState.FAILED,
                                       outcome="failed_error")
        return len(stuck)

    async def aclose(self) -> None:
        return None
```

- [ ] **Step 5: Run the contract suite against the fake**

Run: `uv run pytest tests/contract/test_reminder_store_contract.py -v`
Expected: 8 passed

- [ ] **Step 6: Add the table to `schema.py` and hand-write the migration**

```python
# nora/infra/sql/schema.py — append
reminders = sa.Table(
    "reminders", metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("requester_person_id", sa.Text, nullable=False),
    sa.Column("platform", sa.Text, nullable=False),
    sa.Column("conversation_id", sa.Text, nullable=False),
    sa.Column("conversation_type", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("outcome", sa.Text, nullable=True),
    sa.CheckConstraint(
        "state IN ('pending','firing','done','cancelled','failed')",
        name="ck_reminders_state",
    ),
    sa.Index("idx_reminders_due", "state", "due_at"),
)
```

The migration is hand-written for the same reason `idx_messages_conv_ts` is: `alembic check` cannot see the CHECK constraint, so an autogenerated revision would look correct and drift.

- [ ] **Step 7: Write `SqlReminderStore`, bind it to the contract suite, run both**

Follow the existing adapter pattern in `nora/infra/sql/audit.py` (engine through `__init__`, `async with self._engine.begin()`). Then:

```python
class TestSqlReminderStore(ReminderStoreContract):
    @pytest.fixture
    async def store(self, pg_engine):
        return SqlReminderStore(engine=pg_engine)
```

Run: `uv run pytest tests/contract/test_reminder_store_contract.py tests/infra/test_schema_parity.py -v`
Expected: 16 passed (8 per implementation) + parity green.

- [ ] **Step 8: Gates, then commit**

```bash
uv run pytest -q && uv run ruff check nora/ evals/ tests/ \
  && uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence \
  && uv run lint-imports
git add nora/domain/reminders.py nora/infra/sql/ nora/testing/reminders.py \
        tests/contract/ migrations/ tests/infra/
git commit -m "feat(reminders): ReminderStore port, SQL adapter, fake, contract suite

One-shot rows keyed by (platform, conversation_id, conversation_type) — every
conversation key in this codebase is a tuple, and a row storing conversation_id
alone can build neither a ConversationTarget, nor the slots key, nor a
Policy.is_allowed call.

cancel() is scoped to a conversation because a uuid carries none of its own.
sweep_firing() resolves crashed rows to FAILED and never re-fires: at-most-once
is a decision, not an oversight — nothing links a row to its delivered message,
so re-queueing would double-send anything that crashed after egress.

Migration hand-written: the schema gate is test_schema_parity, not alembic
check, which is blind to the CHECK constraint."
```

---

### Task 3: The `heartbeat` doctrine audience

**Why before the fire turn:** today `get(applies_to="heartbeat")` returns only the generated capability section. A fire turn built first would deliver messages with no identity, voice, conduct or constraints — and hand that near-empty doctrine to `LeakGuard`, gutting R-6's verbatim-leak corpus on the very step that first sends.

**Files:**
- Create: `nora/doctrine/silence.md`
- Modify: `nora/doctrine/{identity,voice,conduct,constraints}.md`
- Test: `tests/test_doctrine_audiences.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_doctrine_audiences.py
import pytest
from nora.doctrine.loader import DoctrineStore

AUTHORED = ("identity", "voice", "conduct", "constraints")


def test_the_heartbeat_audience_is_not_degenerate(doctrine_store: DoctrineStore):
    """§10.1: all four authored files must reach the fire turn. Without this
    she delivers with no persona and no constraints, and LeakGuard's
    verbatim-leak corpus shrinks to the capability block."""
    text = doctrine_store.get(applies_to="heartbeat").text
    for name in AUTHORED:
        assert f'name="{name}"' in text, f"{name} missing from the heartbeat audience"


def test_silence_reaches_tags_only(doctrine_store: DoctrineStore):
    assert 'name="silence"' in doctrine_store.get(applies_to="tag").text
    assert 'name="silence"' not in doctrine_store.get(applies_to="heartbeat").text


def test_the_multi_message_rule_survives_in_both_audiences(doctrine_store: DoctrineStore):
    """DEC-40 lived inside the silence section but is not a silence rule, and
    is true on a fire turn. Splitting on the heading would have deleted it."""
    needle = "you never split one answer across several"
    assert needle in doctrine_store.get(applies_to="tag").text
    assert needle in doctrine_store.get(applies_to="heartbeat").text


def test_tag_audience_bodies_are_unchanged(doctrine_store: DoctrineStore):
    """The split adds one <section> boundary; it must not change any WORDS.
    Byte-identity is impossible — _assemble wraps per file — so assert the
    concatenated bodies instead."""
    import re
    text = doctrine_store.get(applies_to="tag").text
    bodies = re.sub(r"<section[^>]*>|</section>|<nora_doctrine>|</nora_doctrine>", "", text)
    expected = (pytest.importorskip("pathlib").Path(
        "tests/fixtures/tag_doctrine_bodies.txt").read_text())
    assert bodies.split() == expected.split()
```

- [ ] **Step 2: Capture the fixture BEFORE editing any doctrine**

```bash
uv run python -c "
from nora.app.composition import build_doctrine_store
import re, pathlib
t = build_doctrine_store().get(applies_to='tag').text
b = re.sub(r'<section[^>]*>|</section>|<nora_doctrine>|</nora_doctrine>', '', t)
pathlib.Path('tests/fixtures/tag_doctrine_bodies.txt').write_text(b)
"
```

This is the only order that works: the fixture is the *pre-split* truth. Capturing it after editing proves nothing.

- [ ] **Step 3: Run and watch three of four fail**

Run: `uv run pytest tests/test_doctrine_audiences.py -v`
Expected: `test_tag_audience_bodies_are_unchanged` PASSES (nothing edited yet); the other three FAIL.

- [ ] **Step 4: Create `silence.md` — first three sentences only**

```markdown
---
name: silence
description: When Nora speaks at all — she is called, or she is silent.
order: 25
applies_to: [tag]
version: 1
kind: conduct
authority_above: [constraints, identity, voice]
authority_below: [capability, situation, message-content, tool-output]
not_here: >-
  Not how many messages one answer may become — that is conduct.md, and it is
  true whether she was called or a reminder came due. Only the question of
  whether this turn should happen at all.
change_policy: agent-may-edit
---

# Silence is the default

You're only reading this because someone called you. That's the only reason you ever speak.
You never reply to a message that didn't call you, and once your turn is done you never
follow up on your own reply.
```

`order: 25` sits in the free range 21–29 (10/20/30/40 taken, 35 reserved for the generated capability section) and sorts before `conduct.md`, holding the text's position. All ten `_REQUIRED` keys are present.

- [ ] **Step 5: Edit the four existing files**

In `conduct.md`: delete the three sentences above from the `# Silence is the default` section; **keep the remaining sentence** under a new heading:

```markdown
# One answer, however many messages

Within a single call you usually say one thing; you may send a separate message when that
one call genuinely addresses more than one person — but you never split one answer across
several.
```

Then in all four: `applies_to: [tag]` → `applies_to: [tag, heartbeat]`. Bump `conduct.md`'s `version: 5` → `6`. Add `silence` to `authority_below` in `identity.md`, `voice.md` and `constraints.md` — `check_authority_chain` is symmetric and startup fails otherwise.

- [ ] **Step 6: Run**

Run: `uv run pytest tests/test_doctrine_audiences.py -v`
Expected: 4 passed. If `test_tag_audience_bodies_are_unchanged` fails, a word moved that shouldn't have — fix the doctrine, not the test.

- [ ] **Step 7: Add the second `applies_to` call site**

`nora/app/composition.py:228` and `nora/engagement/pipeline.py:264` both hardcode `"tag"`. Leave both — Task 5 adds the `"heartbeat"` call in the fire path. `evals/run.py:232` is correct as-is.

- [ ] **Step 8: Gates, commit**

```bash
uv run pytest -q && uv run ruff check nora/ evals/ tests/ && uv run lint-imports
git add nora/doctrine/ tests/test_doctrine_audiences.py tests/fixtures/
git commit -m "feat(doctrine): a heartbeat audience, and extract silence to tags only

All four authored files gain the audience. Splitting only conduct.md would
have run the fire turn with no persona and no constraints.

The extraction is smaller than the section: '# Silence is the default' is not
homogeneous — its last sentence is DEC-40's multi-message rule, which is true
on a fire turn. Splitting on the heading would have deleted it from the
heartbeat audience. Only the first three sentences move.

Byte-identity for the tag audience is impossible (_assemble wraps per file), so
the gate asserts the concatenated BODIES are unchanged and the fixture is
captured pre-split."
```

---

### Task 4: The tick — `HeartbeatLoop`

**Files:**
- Create: `nora/app/heartbeat.py`
- Modify: `nora/config.py`, `config.toml`, `config.example.toml`, `nora/obs/metric_specs.py`, `nora/app/composition.py`
- Test: `tests/unit/test_heartbeat_loop.py`

**Interfaces:**
- Consumes: `ConversationSlots` (Task 1), `ReminderStore` (Task 2), `Policy.is_allowed`.
- Produces: `HeartbeatLoop(store, slots, policy, clock, metrics, fire, tick_seconds)` where `fire: Callable[[Sequence[Reminder]], Awaitable[str]]` returns an outcome string. Task 5 supplies the real `fire`; this task tests against a stub.

`clock` is a `Callable[[], datetime]` through `__init__` — the entire tick is fake-clock testable with zero model calls.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_heartbeat_loop.py
import pytest
from datetime import datetime, timedelta, timezone
from nora.app.heartbeat import HeartbeatLoop
from nora.domain.reminders import ReminderState
from nora.testing.reminders import FakeReminderStore
from nora.engagement.slots import ConversationSlots

UTC = timezone.utc
T0 = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)


class _AllowAll:
    def is_allowed(self, *, platform, conversation_id, person_id): return True


class _DenyAll:
    def is_allowed(self, *, platform, conversation_id, person_id): return False


@pytest.mark.asyncio
async def test_a_due_row_fires_and_is_marked_done(store, slots, metrics):
    fired = []
    loop = HeartbeatLoop(
        store=store, slots=slots, policy=_AllowAll(), clock=lambda: T0,
        metrics=metrics, tick_seconds=60,
        fire=lambda rows: _record(fired, rows, "sent"),
    )
    r = await _create_due(store)
    await loop.tick()
    assert [x.id for x in fired[0]] == [r.id]
    assert await store.due(now=T0, limit=10) == []


@pytest.mark.asyncio
async def test_a_row_not_yet_due_does_not_fire(store, slots, metrics):
    ...  # due_at = T0 + 1min; assert fire was never called


@pytest.mark.asyncio
async def test_rows_are_grouped_by_conversation_one_turn_each(store, slots, metrics):
    """§5.2: a turn has one conversation scope. Two conversations due in the
    same tick are two turns, never one — sharing would leak one room's
    reminder into the other."""
    ...  # two rows, two conversation_ids -> fire called twice, disjoint


@pytest.mark.asyncio
async def test_a_revoked_conversation_never_fires(store, slots, metrics):
    """§5.1: policy.toml is loaded at startup and a row may be 30 days old."""
    loop = HeartbeatLoop(..., policy=_DenyAll(), ...)
    r = await _create_due(store)
    await loop.tick()
    assert fired == []
    row = (await store.pending_for(r.conversation_id))
    assert row == []                       # resolved, not left pending
    assert metrics.get("nora_reminders_fired_total", outcome="failed_policy") == 1


@pytest.mark.asyncio
async def test_a_held_slot_defers_to_the_next_tick(store, slots, metrics):
    """§5.3: never shed, never wait. The row stays pending and the tick moves on."""
    key = ("signal", "conv-1")
    slots.acquire(key)                      # a tag is in flight
    r = await _create_due(store)
    await loop.tick()
    assert fired == []
    assert [x.id for x in await store.due(now=T0, limit=10)] == [r.id]


@pytest.mark.asyncio
async def test_the_slot_is_released_even_when_fire_raises(store, slots, metrics):
    loop = HeartbeatLoop(..., fire=_raises, ...)
    await loop.tick()
    assert slots.held(("signal", "conv-1")) is False
    assert metrics.get("nora_reminders_fired_total", outcome="failed_error") == 1


@pytest.mark.asyncio
async def test_boot_sweeps_firing_rows_to_failed(store, slots, metrics):
    """§8.2 at-most-once."""
    await store.create(_row(state=ReminderState.FIRING))
    await loop.recover()
    assert await store.due(now=T0 + timedelta(days=1), limit=10) == []
```

- [ ] **Step 2: Run, watch it fail** — `ModuleNotFoundError: nora.app.heartbeat`

- [ ] **Step 3: Add config**

```toml
# config.toml AND config.example.toml — both, by hand.
# Nothing diffs them; tests/test_config.py loads the EXAMPLE and asserts values,
# so any value asserted there must match what is added.
[heartbeat]
enabled = false          # ships inert, like [presence] and [search]
tick_seconds = 60
timeout_seconds = 120    # NOT [llm] timeout_seconds = 60: no human is waiting

[reminders]
max_pending_per_requester = 20
min_lead_seconds = 120   # > tick_seconds, so a minimum-lead row cannot land in the same tick
max_horizon_days = 30
```

- [ ] **Step 4: Add the metrics**

Append to `_COUNTERS`/`_GAUGES` in `nora/obs/metric_specs.py`:
`nora_reminders_created_total{status}`, `nora_reminders_fired_total{outcome}`, `nora_reminders_pending` (gauge), `nora_heartbeat_last_tick_age_seconds` (gauge).

**The liveness gauge must not be published from inside the tick body.** Store `last_tick_at` on the loop and compute the age at scrape time. The presence collector publishes from inside its loop, so a dead loop freezes its gauge — the opposite of what a liveness signal is for. Copy the intent, not the code.

- [ ] **Step 5: Write `HeartbeatLoop`** with `tick()`, `recover()` and `run()` (the `while True` that `BackgroundTasks.spawn` drives, following `prune_loop`'s never-die discipline at `nora/app/runtime.py:109-117`).

- [ ] **Step 6: Run** — Expected: 7 passed

- [ ] **Step 7: Wire into `composition.py`** — same `slots` instance as the pipeline; only spawn the loop when `cfg.heartbeat.enabled`.

- [ ] **Step 8: Gates, commit.**

---

### Task 5: The fire turn — `FirePath`

**Files:**
- Create: `nora/app/fire.py`
- Modify: `nora/agent/prefill.py` (header parameter), `nora/agent/room.py` (tagger property), `nora/obs/metric_specs.py` (`audience` label), `nora/engagement/stages/{deliver,invoke}.py` + `nora/engagement/pipeline.py` + `nora/agent/guards.py` (pass `audience`), `tests/test_metrics_export.py`, `tests/test_metrics_stub.py`, `docs/runbook.md`
- Test: `tests/unit/test_fire_path.py`

- [ ] **Step 1: Grep before trusting the spec's list of nine**

```bash
grep -rn 'nora_silent_failures_total' nora/ evals/ | grep '\.inc('
```

Expected nine sites: `guards.py:146,155,169,193,220`, `invoke.py:110,117`, `deliver.py:156`, `pipeline.py:359`. **If the grep returns a different set, the grep wins.** Four separate hand-enumerations of this list came up short during design review; that is why this is step 1 and not an assumption.

- [ ] **Step 2: Write the failing test for the synthetic context**

```python
def test_the_fire_turn_carries_the_requester(fire_path, reminder):
    """The trap this test exists for: blanking sender_id is correct for
    reactions (indicate.begin returns None on a falsy author) and wrong for
    everything that reads it. A blank sender_id resolves to tagger=None, which
    empties peer_card, makes Room.scope() unavailable, and makes search_memory
    refuse."""
    ctx = fire_path.build_context(reminder)
    assert ctx.tagger is not None
    assert ctx.tagger.person_id == reminder.requester_person_id
    assert ctx.message.body == reminder.text          # the request itself
    assert ctx.message.sender_name != ""              # NOT Persons.display_name
    assert ctx.message.sender_id == ""                # so no reaction is placed
    assert ctx.message.timestamp is None
```

- [ ] **Step 3: Add the `audience` label to all nine sites**, defaulting to `"tag"` at the tag-path call sites and `"heartbeat"` in the fire path. Missing one leaves `audience=""`, which is worse than no label.

- [ ] **Step 4: Fix the three exact-label assertions**

`tests/test_metrics_export.py:76`, `:152`, and `tests/test_metrics_stub.py:48`. `generate_latest` emits labels **sorted**, so `audience` renders first: `nora_silent_failures_total{audience="tag",platform="signal",reason="llm_error"}`. `test_metrics_stub.py`'s `snapshot()` keys on `tuple(sorted(labels.items()))`, so its exact-2-tuple subscript raises `KeyError` until updated. Every other assertion reads through `Metrics.get`, which subset-matches, and needs no change.

- [ ] **Step 5: Update `docs/runbook.md:10` and `:17`** — the A8 ratio becomes `nora_silent_failures_total{audience="tag"} / nora_tags_total`, and the `curl | grep` recipe follows. Without this the runbook's first diagnostic silently mixes heartbeat failures into a tag-only denominator.

- [ ] **Step 6: `build_prefill` gains `header:`** — parameterise, do not duplicate. R-13/R-13b escaping is pinned to `build_prefill` by `tests/test_acceptance_rules.py:27-28`; a hand-rolled fire prefill would sit outside both named tests. The set-at and due-at facts go in the header, never the body — the body stays the requester's sentence or §2's premise breaks.

- [ ] **Step 7: Write `FirePath.run(rows) -> str`** — build `TagContext`, `MentionRenderer.rehydrate`, doctrine at `applies_to="heartbeat"`, `ToolRegistry.build(..., fire_turn=True)`, `runner.invoke` inside its own `asyncio.wait_for` with `[heartbeat] timeout_seconds`, `deliver.build` + `Deliverer`, `feedback.run`. Do **not** call `invoke.run` or `deliver.check_outcome` — both own metric branches the fire path must account for itself.

- [ ] **Step 8: Run, gates, commit.**

---

### Task 6: The tools

**Files:** Modify `nora/agent/tools.py`, `nora/agent/registry.py`, `nora/app/cli.py`, `ARCHITECTURE.md`; Test: `tests/test_reminder_tools.py`

- [ ] **Step 1: Failing tests**

```python
def test_remind_refuses_on_a_fire_turn():
    """§7.1: without this she re-arms her own reminder each time one fires —
    recurrence through the back door, and none of §9's caps stop it because
    max_pending_per_requester counts PENDING rows and each fire frees one."""
    result = await remind_on_fire_turn(when=..., text="洗衫")
    assert result["status"] == "refused"


def test_remind_refuses_an_unmapped_requester(): ...
def test_remind_refuses_past_the_pending_cap(): ...
def test_remind_refuses_a_time_in_the_past(): ...
def test_remind_refuses_inside_min_lead_seconds(): ...
def test_remind_refuses_beyond_max_horizon_days(): ...
def test_list_reminders_is_scoped_to_this_conversation(): ...
def test_cancel_refuses_an_id_from_another_conversation(): ...
```

- [ ] **Step 2:** Add `tagger` property to `Room`; thread `fire_turn: bool = False` through `build_tool_specs` / `ToolRegistry.specs()` / `build()`; add `reminders=` to `ToolRegistry.__init__` alongside `transit`/`route`/`search`/`presence`.

- [ ] **Step 3:** Implement the three tools with budgets — `remind` 2, `list_reminders` 2, `cancel_reminder` 2.

- [ ] **Step 4:** `nora reminders list` in `cli.py` — **read-only only**. The module opens the store through `create_readonly_sql_engine` with a `SELECT`-only grant; a `cancel` subcommand would break both. The operator's kill switch is `[heartbeat] enabled = false` plus a restart.

- [ ] **Step 5:** Amend **R-1** in `design/09-decision-index.md` and add the replacement named test to `RULE_TESTS`: *the tag path and the fire path are the only two senders.* R-1's existing test asserts both its clauses but only over ambient input, so it stays green untouched — amending the row alone would delete the only written brake on a third send path with nothing going red.

- [ ] **Step 6:** Update `ARCHITECTURE.md` (`test_architecture_doc.py` fails on any new top-level module under `nora/`).

- [ ] **Step 7:** All four gates, commit.

---

## Self-Review

**Spec coverage.** §4 → Task 2. §5 (loop, policy, batching, slots, timeout, reactions) → Tasks 1, 4. §6 (`required`, outcome accounting, header, `TagContext`) → Task 5. §7 (tools, no target, scoped list/cancel, read-only CLI) → Task 6. §8 (typed outcome, at-most-once, cancel-in-flight) → Tasks 2, 4. §9 caps → Tasks 4, 6. §10/§16.1 doctrine → Task 3. §12 mapped identity → Task 6. §13 metrics → Tasks 4, 5. §14 gates → every task's final step. §16 build order → task order.

**Not covered, and deliberately:** §11 phase 2 (the staleness check) is out of scope — it is gated on a measurement that has not been taken. §15's accepted limits are documentation, not work.

**Type consistency.** `ReminderState` is a `StrEnum` used identically in Tasks 2, 4, 6. `ConversationSlots`'s key is `tuple[str, str]` in Tasks 1 and 4. `fire: Callable[[Sequence[Reminder]], Awaitable[str]]` in Task 4 matches `FirePath.run(rows) -> str` in Task 5. `mark()` returns `bool` everywhere.

**One known gap, called out rather than hidden:** Task 5's steps 7–8 and Task 6's steps 2–3 describe implementations by their interfaces and constraints rather than showing every line. They are the two tasks whose exact code depends on shapes the implementer will read in the files they are editing (`deliver.build`'s eleven arguments, `build_tool_specs`'s closure body). Every *decision* is specified — what to call, what not to call, what must not be duplicated and why — but an implementer will write more original code in these two than in Tasks 1–4.
