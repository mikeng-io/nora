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
| `nora/infra/sql/migrations/versions/0004_reminders.py` | hand-written table + CHECK + named index |

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
# EDIT FRAGMENTS — not a whole file; `...` marks unchanged code.
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
# EDIT FRAGMENT — not a whole file.
# nora/app/composition.py — build the slots once, pass to the pipeline
slots = ConversationSlots(maxlen=cfg.engagement.queue.max_depth, spawn=background.spawn)
pipeline = EngagementPipeline(..., slots=slots, ...)   # was queue_max=...
```

Same shape in `nora/app/offline.py:85`, `tests/unit/test_engagement_pipeline.py:228`, `tests/architecture/test_no_retained_per_tag_state.py:113`. Keep `slots` in scope in `composition.py` — Task 4 needs the same instance.

**Three more callers the constructor list misses**, each passing the deleted `in_flight=` / `queue_depth=` / `queue_max=` parameters directly to `admission.run`:
- `tests/unit/test_engagement_admission.py:51-54` — the dedicated admission unit test
- `tests/test_signal_self_id.py:249, :283` — two direct `admission.run` calls
- `tests/integration/harness.py:151-152` — an `_in_flight` property proxying `self._engagement._in_flight`, backing five assertions in `tests/integration/test_tagging.py:124-154`

Also: add `from ..slots import ConversationSlots` to `admission.py`, and delete `DEFAULT_QUEUE_MAX` (`pipeline.py:64`), which becomes dead — ruff will not flag it.

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
- Create: `nora/infra/sql/migrations/versions/0004_reminders.py`
- Modify: `nora/infra/sql/schema.py`
- Regenerate: `tests/infra/pg_schema_baseline.sql` via `uv run python scripts/gen_pg_baseline.py` — `test_schema_parity.py:164` names this as the regeneration path
- Test: `tests/infra/test_schema_parity.py` (existing, must pass)

**Interfaces:**
- Produces: `Reminder` frozen dataclass with fields `id: UUID`, `requester_person_id: str`, `platform: str`, `conversation_id: str`, `conversation_type: str`, `text: str`, `created_at: datetime`, `due_at: datetime`, `state: ReminderState`, `fired_at: datetime | None`, `outcome: str | None`. `ReminderState` is a `StrEnum` of `PENDING|FIRING|DONE|CANCELLED|FAILED`.
- Produces: `ReminderStore` Protocol — `create(reminder) -> None`, `due(now, limit) -> Sequence[Reminder]`, `pending_for(conversation_id) -> Sequence[Reminder]`, `count_pending(requester_person_id) -> int`, `mark(id, state, *, fired_at=None, outcome=None) -> bool`, `cancel(id, conversation_id) -> bool`, `sweep_firing() -> int`, `aclose() -> None`.

`cancel` takes `conversation_id` because a uuid carries no scope of its own (§7.3) — the store enforces it, not the tool.

`mark` returns `bool` (did the row change) so the caller can distinguish a lost race from a no-op.

- [ ] **Step 1: Write the contract suite — fake only for now**

**Follow the house pattern exactly**: `tests/contract/test_audit_store_contract.py` uses a **parametrized `store` fixture** with module-level test functions. There is no base class and no `pg_engine` fixture. The SQL parameter is added in Step 6, after the adapter exists — importing it now makes the file fail collection with `ImportError` before a single test runs.

```python
# tests/contract/test_reminder_store_contract.py
"""Every ReminderStore implementation passes this. CLAUDE.md: a port never
gains a second implementation — including a test fake — without a shared
contract suite both pass."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from nora.domain.reminders import Reminder, ReminderState, ReminderStore
from nora.testing.reminders import FakeReminderStore

UTC = timezone.utc
T0 = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
LATER = T0 + timedelta(days=1)


def _row(**kw) -> Reminder:
    base = dict(
        id=uuid.uuid4(), requester_person_id="miles", platform="signal",
        conversation_id="conv-1", conversation_type="group", text="洗衫",
        created_at=T0, due_at=T0 + timedelta(minutes=40),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
    )
    base.update(kw)
    return Reminder(**base)


@pytest.fixture(params=["fake"])
async def store(request):
    s: ReminderStore = FakeReminderStore()
    yield s
    await s.aclose()


async def test_a_created_row_is_returned_once_due(store):
    r = _row()
    await store.create(r)
    assert await store.due(now=T0, limit=10) == []
    due = await store.due(now=r.due_at, limit=10)
    assert [x.id for x in due] == [r.id]


async def test_due_is_ordered_by_due_at(store):
    late = _row(due_at=T0 + timedelta(hours=2))
    early = _row(due_at=T0 + timedelta(hours=1))
    await store.create(late)
    await store.create(early)
    due = await store.due(now=T0 + timedelta(hours=3), limit=10)
    assert [x.id for x in due] == [early.id, late.id]


async def test_only_pending_rows_are_due(store):
    for state in (ReminderState.DONE, ReminderState.CANCELLED,
                  ReminderState.FAILED, ReminderState.FIRING):
        await store.create(_row(state=state))
    assert await store.due(now=LATER, limit=10) == []


async def test_mark_moves_state_and_reports_the_change(store):
    r = _row()
    await store.create(r)
    assert await store.mark(r.id, ReminderState.DONE,
                            fired_at=T0, outcome="sent") is True
    assert (await store.get(r.id)).state is ReminderState.DONE
    assert await store.mark(uuid.uuid4(), ReminderState.DONE) is False


async def test_cancel_is_scoped_to_the_conversation(store):
    r = _row(conversation_id="conv-1")
    await store.create(r)
    assert await store.cancel(r.id, conversation_id="conv-OTHER") is False
    assert await store.cancel(r.id, conversation_id="conv-1") is True
    assert (await store.get(r.id)).state is ReminderState.CANCELLED


async def test_pending_for_is_scoped_and_excludes_finished(store):
    mine = _row(conversation_id="conv-1")
    theirs = _row(conversation_id="conv-2")
    done = _row(conversation_id="conv-1", state=ReminderState.DONE)
    for r in (mine, theirs, done):
        await store.create(r)
    assert [x.id for x in await store.pending_for("conv-1")] == [mine.id]


async def test_count_pending_is_per_requester(store):
    await store.create(_row(requester_person_id="miles"))
    await store.create(_row(requester_person_id="miles"))
    await store.create(_row(requester_person_id="tyler"))
    assert await store.count_pending("miles") == 2
    assert await store.count_pending("tyler") == 1


async def test_sweep_resolves_firing_rows_to_failed(store):
    """§8.2: at-most-once. A crash mid-fire loses that reminder, visibly. It is
    NEVER re-queued — re-queueing double-sends anything that crashed after
    egress, and nothing links a row to its delivered message.

    The assertion reads the TERMINAL STATE via get(). An earlier draft asserted
    only that the row vanished from due(), which stays green if the sweep moves
    FIRING to anything at all — decorative, per CLAUDE.md."""
    stuck = _row(state=ReminderState.FIRING)
    ok = _row(state=ReminderState.PENDING)
    await store.create(stuck)
    await store.create(ok)

    assert await store.sweep_firing() == 1

    swept = await store.get(stuck.id)
    assert swept is not None
    assert swept.state is ReminderState.FAILED
    assert swept.outcome == "failed_error"
    assert (await store.get(ok.id)).state is ReminderState.PENDING
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd /tmp/nora-reminders && uv run pytest tests/contract/test_reminder_store_contract.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'nora.domain.reminders'`

- [ ] **Step 3: Write the port**

```python
# nora/domain/reminders.py
"""The reminders port. A reminder is a DEFERRED REQUEST: the row stores the
requester's own sentence, and at fire time it is put to Nora as a request."""

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
class ReminderCaps:
    """The three anti-runaway caps, as a DOMAIN value.

    Not `RemindersConfig` from `nora/config.py`: import-linter's "only app and
    __main__ read config" contract (`pyproject.toml:132-141`) lists `nora.agent`
    as a source module and `nora.config` as forbidden, so a config type on
    `ToolRegistry.__init__` fails `uv run lint-imports` outright. A
    `TYPE_CHECKING` guard does not rescue it — grimp's
    `exclude_type_checking_imports` defaults to False and this repo does not set
    it. `nora.app.composition` builds one from `cfg.reminders`."""

    max_pending_per_requester: int = 20
    min_lead_seconds: int = 120
    max_horizon_days: int = 30


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

    async def get(self, reminder_id: UUID) -> Reminder | None:
        """Read one row by id. Exists so the contract suite can assert the
        TERMINAL STATE of a swept or cancelled row — without it no test can
        distinguish `FIRING -> FAILED` from `FIRING -> anything else`, because
        `due()` excludes both, and the at-most-once decision rests on exactly
        that distinction."""
        ...

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
        """Scoped on purpose: a uuid carries none of its own."""
        ...

    async def sweep_firing(self) -> int:
        """Boot recovery. FIRING rows resolve to FAILED and are never re-fired
        (at-most-once). Returns how many."""
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

    async def get(self, reminder_id: UUID) -> Reminder | None:
        return self._rows.get(reminder_id)

    async def due(self, *, now: datetime, limit: int) -> Sequence[Reminder]:
        rows = [r for r in self._rows.values()
                if r.state is ReminderState.PENDING and r.due_at <= now]
        return sorted(rows, key=lambda r: r.due_at)[:limit]

    async def pending_for(self, conversation_id: str) -> Sequence[Reminder]:
        return sorted(
            (r for r in self._rows.values()
             if r.conversation_id == conversation_id
             and r.state is ReminderState.PENDING),
            key=lambda r: r.due_at,
        )

    async def count_pending(self, requester_person_id: str) -> int:
        return sum(1 for r in self._rows.values()
                   if r.requester_person_id == requester_person_id
                   and r.state is ReminderState.PENDING)

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
Expected: **8 passed** — one parameter (`fake`). This checkpoint is the point of
the fake-first ordering: the suite is proven to hold before a database is
involved, so any failure in Step 7 is unambiguously the adapter's.

- [ ] **Step 6: Add the table to `schema.py` and hand-write the migration**

House style: `METADATA` is uppercase, table constants are uppercase, the module imports **only** `sqlalchemy as sa` (no `postgresql` import — use `sa.Uuid`), and indexes are module-level constants *after* the table. Compare `MESSAGES` / `IDX_CONV_TS`.

```python
# nora/infra/sql/schema.py — append
REMINDERS = sa.Table(
    "reminders", METADATA,
    sa.Column("id", sa.Uuid, primary_key=True),
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
)

IDX_REMINDERS_DUE = sa.Index("idx_reminders_due", REMINDERS.c.state, REMINDERS.c.due_at)
```

The migration is hand-written for the same reason `idx_messages_conv_ts` is: `alembic check` cannot see the CHECK constraint, so an autogenerated revision would look correct and drift.

- [ ] **Step 7: Write `SqlReminderStore`, then widen the fixture to both**

Follow `nora/infra/sql/audit.py` (engine through `__init__`, `async with self._engine.begin()`).

Then widen the *existing* fixture — do not add a class:

```python
# tests/contract/test_reminder_store_contract.py — replace the fixture
from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.migrations_runner import run_migrations   # NOT nora.infra.sql.migrate
from nora.infra.sql.reminders import SqlReminderStore


@pytest.fixture(params=["sql", "fake"])
async def store(request):
    if request.param == "sql":
        pg_url = request.getfixturevalue("pg_url")
        run_migrations(pg_url)
        s: ReminderStore = SqlReminderStore(create_sql_engine(pg_url))
    else:
        s = FakeReminderStore()
    yield s
    await s.aclose()
```

Run: `uv run pytest tests/contract/test_reminder_store_contract.py tests/infra/test_schema_parity.py -v`
Expected: **16 passed** (8 tests × 2 parameters) + parity green.

- [ ] **Step 8: Gates, then commit**

```bash
uv run pytest -q && uv run ruff check nora/ evals/ tests/ \
  && uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence \
  && uv run lint-imports
git add nora/domain/reminders.py nora/infra/sql/ nora/testing/reminders.py \
        tests/contract/ tests/infra/
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

- [ ] **Step 1: Add a `doctrine_store` fixture — it does not exist**

`tests/conftest.py` defines only `anyio_backend`, `_pg_container`, `pg_admin`, `pg_url`. Add this to `tests/conftest.py`. **The `canary`, `display_name` and `trigger` values must be identical here and in Step 2's capture script** — `Doctrine.text` appends a `<runtime_context>` block built from them (`nora/doctrine/loader.py:525-531`), so a mismatch fails the body test for a reason that has nothing to do with doctrine.

```python
# tests/conftest.py — append
from pathlib import Path
from nora.agent.registry import ToolRegistry
from nora.doctrine.loader import DoctrineStore
from nora.obs.metrics import Metrics

DOCTRINE_CANARY = "test-canary"
DOCTRINE_DISPLAY_NAME = "Nora"
DOCTRINE_TRIGGER = "nora.000"


def build_test_doctrine_store() -> DoctrineStore:
    """Module-level so tests/fixtures/gen_tag_doctrine_bodies.py can import it
    and produce a byte-comparable capture."""
    return DoctrineStore(
        Path("nora/doctrine"), strict=True, reload=False,
        canary=DOCTRINE_CANARY, display_name=DOCTRINE_DISPLAY_NAME,
        trigger=DOCTRINE_TRIGGER, language_hint="zh-HK",
        tool_names=ToolRegistry().tool_names(),
        capability=ToolRegistry().capability(),
        metrics=Metrics(),
    )


@pytest.fixture
def doctrine_store() -> DoctrineStore:
    return build_test_doctrine_store()
```

- [ ] **Step 2: Capture the pre-split fixture — BEFORE touching any doctrine file**

`tests/fixtures/` does not exist; create it. This order is the only one that works: the fixture is the *pre-split* truth, and capturing it afterwards proves nothing.

```bash
cd /tmp/nora-reminders && mkdir -p tests/fixtures && uv run python - <<'PYEOF'
import re, pathlib, sys
sys.path.insert(0, "tests")
from conftest import build_test_doctrine_store
text = build_test_doctrine_store().get(applies_to="tag").text
bodies = re.sub(r"<section[^>]*>|</section>|<nora_doctrine>|</nora_doctrine>", "", text)
pathlib.Path("tests/fixtures/tag_doctrine_bodies.txt").write_text(bodies)
print(len(bodies.split()), "tokens captured")
PYEOF
```

- [ ] **Step 3: Write the failing tests**

```python
# tests/test_doctrine_audiences.py
import re
from pathlib import Path

from nora.doctrine.loader import DoctrineStore

AUTHORED = ("identity", "voice", "conduct", "constraints")
MULTI_MESSAGE = "never split one answer across several"
SILENCE = "That's the only reason you ever speak"
NEW_HEADING_TOKENS = {"#", "One", "answer,", "however", "many", "messages"}


def _bodies(store: DoctrineStore, audience: str) -> str:
    text = store.get(applies_to=audience).text
    return re.sub(r"<section[^>]*>|</section>|<nora_doctrine>|</nora_doctrine>", "", text)


def test_the_heartbeat_audience_is_not_degenerate(doctrine_store):
    """All four authored files must reach the fire turn. Without this she
    delivers with no persona and no constraints, and LeakGuard's verbatim-leak
    corpus shrinks to the capability block."""
    text = doctrine_store.get(applies_to="heartbeat").text
    for name in AUTHORED:
        assert f'name="{name}"' in text, f"{name} missing from the heartbeat audience"


def test_silence_reaches_tags_only(doctrine_store):
    assert SILENCE in _bodies(doctrine_store, "tag")
    assert SILENCE not in _bodies(doctrine_store, "heartbeat")


def test_the_multi_message_rule_survives_in_both_audiences(doctrine_store):
    """DEC-40 lived inside the silence section but is not a silence rule, and is
    true on a fire turn. Splitting on the heading would have deleted it.

    The needle is a fragment that does NOT span a line break in either the
    current wrapping or the replacement — a longer phrase fails on whitespace,
    not on meaning."""
    assert MULTI_MESSAGE in " ".join(_bodies(doctrine_store, "tag").split())
    assert MULTI_MESSAGE in " ".join(_bodies(doctrine_store, "heartbeat").split())


def test_tag_audience_words_are_unchanged_except_the_new_heading(doctrine_store):
    """The split adds one <section> boundary and one heading; it must not change
    or lose any RULE. Byte-identity is impossible — _assemble wraps per file —
    so compare token multisets and allow exactly the heading's tokens."""
    from collections import Counter
    before = Counter(Path("tests/fixtures/tag_doctrine_bodies.txt").read_text().split())
    after = Counter(_bodies(doctrine_store, "tag").split())
    added = after - before
    removed = before - after
    assert not removed, f"tag audience LOST tokens: {sorted(removed)}"
    assert set(added) <= NEW_HEADING_TOKENS, f"unexpected additions: {sorted(added)}"
```

The fourth test is the one that changed from an earlier draft. It previously asserted the token lists were *equal* — which Step 5's mandated new heading makes unsatisfiable, while Step 6 told the implementer to "fix the doctrine, not the test". An instruction that cannot be obeyed is worse than no instruction. The rule that actually matters is **nothing is lost**; additions are bounded to the heading.

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

- [ ] **Step 5b: Add spec §10.2's voice rules to `voice.md`**

Unimplemented by any earlier draft of this plan, and it is the operator's own correction: without it she states exact fire times back to the user.

**`voice.md` is `kind: identity`, which forbids BOTH absolute and advisory RFC-2119 keywords** (`loader.py:112`) — no MUST, no SHOULD, no MAY, uppercase. Write it as description:

```markdown
# Times you are given, and times you say back

When somebody asks you to remember something for later, you answer in the register they
asked in. 「等陣提我洗衫」 gets 「好, 等陣提你」 — not 「好, 19:47 提你」. 「七點提我出門」 gets
「好, 七點」. The exact moment is your problem, not theirs; saying a timestamp back at
someone who said 「等陣」 is the machinery showing through.

You still say something back, so a wrong guess dies now instead of at the wrong hour. And
you only ask 「幾點?」 when there is genuinely nothing sensible to assume — a friend who
answers 「等陣提我洗衫」 with 「幾點? 15 分鐘定 30 分鐘?」 is not being careful, they are being
a judge.
```

Add its tokens to `NEW_HEADING_TOKENS` in Step 3's test, or that test will report unexpected additions — it is bounded on purpose.

- [ ] **Step 6: Run**

Run: `uv run pytest tests/test_doctrine_audiences.py -v`
Expected: 4 passed. If `test_tag_audience_words_are_unchanged_except_the_new_heading` reports LOST tokens, a rule went missing — fix the doctrine, not the test. If it reports unexpected additions, you rewrote something you were only meant to move.

- [ ] **Step 7: Add the second `applies_to` call site**

`nora/app/composition.py:228` and `nora/engagement/pipeline.py:264` both hardcode `"tag"`. Leave both — Task 5 adds the `"heartbeat"` call in the fire path. `evals/run.py:232` is correct as-is.

- [ ] **Step 8: Gates, commit**

```bash
uv run pytest -q && uv run ruff check nora/ evals/ tests/ \
  && uv run mypy nora/domain nora/config.py nora/app nora/infra nora/presence \
  && uv run lint-imports
git add nora/doctrine/ tests/conftest.py tests/test_doctrine_audiences.py tests/fixtures/
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
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from nora.app.heartbeat import HeartbeatLoop
from nora.domain.reminders import Reminder, ReminderState
from nora.obs.metrics import Metrics
from nora.engagement.slots import ConversationSlots
from nora.testing.reminders import FakeReminderStore

UTC = timezone.utc
T0 = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)
KEY = ("signal", "conv-1")


class _AllowAll:
    def is_allowed(self, *, platform, conversation_id, person_id) -> bool:
        return True


class _DenyAll:
    def is_allowed(self, *, platform, conversation_id, person_id) -> bool:
        return False


def _row(**kw) -> Reminder:
    base = dict(
        id=uuid.uuid4(), requester_person_id="miles", platform="signal",
        conversation_id="conv-1", conversation_type="group", text="洗衫",
        created_at=T0 - timedelta(hours=1), due_at=T0 - timedelta(minutes=1),
        state=ReminderState.PENDING, fired_at=None, outcome=None,
    )
    base.update(kw)
    return Reminder(**base)


def _build(*, store, policy=None, fire=None, slots=None, metrics=None):
    """One constructor for every test — the loop takes all collaborators
    through __init__, including the clock, so nothing here needs a real clock,
    a real model, or a real adapter."""
    fired: list[list[Reminder]] = []

    async def _default_fire(rows):
        fired.append(list(rows))
        return "sent"

    loop = HeartbeatLoop(
        store=store,
        slots=slots or ConversationSlots(maxlen=16, spawn=lambda c: None),
        policy=policy or _AllowAll(),
        clock=lambda: T0,
        metrics=metrics or Metrics(),
        tick_seconds=60.0,
        fire=fire or _default_fire,
    )
    return loop, fired


async def test_a_due_row_fires_and_is_marked_done():
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    loop, fired = _build(store=store)

    await loop.tick()

    assert [r.id for r in fired[0]] == [row.id]
    assert (await store.get(row.id)).state is ReminderState.DONE
    assert (await store.get(row.id)).outcome == "sent"


async def test_a_row_not_yet_due_does_not_fire():
    store = FakeReminderStore()
    await store.create(_row(due_at=T0 + timedelta(minutes=1)))
    loop, fired = _build(store=store)

    await loop.tick()

    assert fired == []


async def test_rows_are_grouped_by_conversation_one_turn_each():
    """§5.2: a turn has one conversation scope — one Room, one policy decision,
    one Deliverer binding. Two conversations due in the same tick are two
    turns; sharing one would leak one room's reminder into the other."""
    store = FakeReminderStore()
    a1 = _row(conversation_id="conv-1")
    a2 = _row(conversation_id="conv-1")
    b1 = _row(conversation_id="conv-2")
    for r in (a1, a2, b1):
        await store.create(r)
    loop, fired = _build(store=store)

    await loop.tick()

    groups = {rows[0].conversation_id: {r.id for r in rows} for rows in fired}
    assert groups == {"conv-1": {a1.id, a2.id}, "conv-2": {b1.id}}


async def test_a_revoked_conversation_never_fires():
    """§5.1: policy.toml loads once at startup and a row may be 30 days old, so
    trust withdrawn in between must bite at fire time."""
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    metrics = Metrics()
    loop, fired = _build(store=store, policy=_DenyAll(), metrics=metrics)

    await loop.tick()

    assert fired == []
    assert (await store.get(row.id)).state is ReminderState.FAILED
    assert (await store.get(row.id)).outcome == "failed_policy"
    assert metrics.get("nora_reminders_fired_total", outcome="failed_policy") == 1


async def test_a_held_slot_defers_to_the_next_tick():
    """§5.3: never shed, never wait. The row stays PENDING and the tick moves on
    — a reminder is not second-critical and the row persists."""
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    slots = ConversationSlots(maxlen=16, spawn=lambda c: None)
    slots.acquire(KEY)                       # a tag is in flight
    loop, fired = _build(store=store, slots=slots)

    await loop.tick()

    assert fired == []
    assert (await store.get(row.id)).state is ReminderState.PENDING


async def test_the_slot_is_released_even_when_fire_raises():
    store = FakeReminderStore()
    row = _row()
    await store.create(row)
    slots = ConversationSlots(maxlen=16, spawn=lambda c: None)
    metrics = Metrics()

    async def _boom(rows):
        raise RuntimeError("model exploded")

    loop, _ = _build(store=store, slots=slots, fire=_boom, metrics=metrics)

    await loop.tick()

    assert slots.held(KEY) is False
    assert (await store.get(row.id)).state is ReminderState.FAILED
    assert (await store.get(row.id)).outcome == "failed_error"
    assert metrics.get("nora_reminders_fired_total", outcome="failed_error") == 1


# NOTE: there is deliberately no `HeartbeatLoop.recover()`. Boot recovery is
# `ReminderStore.sweep_firing()`, called directly in composition.py and
# UNCONDITIONALLY — see Step 7. Putting it on the loop would make it dead code
# whenever [heartbeat] enabled = false, which is exactly when a stranded FIRING
# row most needs sweeping. Its behaviour is covered by
# tests/contract/test_reminder_store_contract.py::test_sweep_resolves_firing_rows_to_failed.
```

Every fixture, helper and name above is defined in the file. `_build` exists so no test constructs `HeartbeatLoop` positionally with `Ellipsis`, which an earlier draft did.

- [ ] **Step 2: Run, watch it fail** — `ModuleNotFoundError: nora.app.heartbeat`

- [ ] **Step 3: Add config — pydantic models FIRST, then TOML**

Every model in `nora/config.py` sets `extra="forbid"`. Adding `[heartbeat]`/`[reminders]` to the TOML **without** matching models makes the whole config fail to load, and every config-touching test goes red at once with an error that points at the TOML, not at the missing model.

```python
# nora/config.py — two models plus two Config fields
class HeartbeatConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    tick_seconds: float = 60.0
    timeout_seconds: float = 120.0


class RemindersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_pending_per_requester: int = 20
    min_lead_seconds: int = 120
    max_horizon_days: int = 30


# on Config:
    heartbeat: HeartbeatConfig = HeartbeatConfig()
    reminders: RemindersConfig = RemindersConfig()
```

Then the TOML, in **both** files:

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

- [ ] **Step 5: Write `HeartbeatLoop`** with `tick()` and `run()` (the `while True` that `BackgroundTasks.spawn` drives, following `prune_loop`'s never-die discipline at `nora/app/runtime.py:109-117`).

- [ ] **Step 6: Run** — Expected: 6 passed

- [ ] **Step 7: Wire into `composition.py`**

Same `slots` instance as the pipeline. Two things an earlier draft got wrong:

- **`FirePath` does not exist until Task 5.** Wire the loop with a stub that records and returns `"sent"`, and leave a one-line comment naming Task 5 as its replacement. Do not forward-reference `nora.app.fire`.
- **`recover()` runs unconditionally at startup**, *not* behind `cfg.heartbeat.enabled`. Gating it means that when the heartbeat is switched off, `FIRING` rows left by a crash stay stuck forever and §8.2's at-most-once guarantee silently stops applying. Only the `run()` loop is gated.

```python
# REUSE the engine already in scope at composition.py:188. That module
# documents "ONE engine (one pool) shared by the record store and the presence
# reader", and audit.aclose() disposes it for everything — a second
# create_sql_engine() forks the pool and its aclose() tears down the shared one.
# (`cfg.database.url` does not exist; Config has `store: StoreConfig`.)
reminders = SqlReminderStore(engine)
await reminders.sweep_firing()          # §8.2 boot recovery — ALWAYS, not gated
# `allowlist`, NOT `policy`. At composition.py:269 the name `policy` is a
# CompositePolicy (the TRIGGER policy) with no is_allowed at all. The
# policy.toml object with is_allowed(*, platform, conversation_id, person_id)
# is bound to `allowlist` (composition.py:162) — and §5.1's revocation check is
# the entire point of this parameter. Also import datetime/timezone into
# composition.py, and place this AFTER `slots` exists (~:474), not at :188.
if cfg.heartbeat.enabled:
    heartbeat = HeartbeatLoop(store=reminders, slots=slots, policy=allowlist,
                              clock=lambda: datetime.now(timezone.utc),
                              metrics=metrics, tick_seconds=cfg.heartbeat.tick_seconds,
                              fire=_stub_fire)   # Task 5 replaces this with FirePath.run
    background.spawn(heartbeat.run())
```

- [ ] **Step 8: Gates, commit.**

---

### Task 5: The fire turn — `FirePath`

**Files:**
- Create: `nora/app/fire.py`
- Modify: `nora/app/composition.py` (**replace Task 4's `_stub_fire` — Step 8**), `nora/agent/prefill.py` (header parameter), `nora/agent/registry.py` + `nora/agent/tools.py` (the `fire_turn` keyword — **Task 5 owns it, not Task 6**, because Task 5 is the first caller), `nora/obs/metric_specs.py` (`audience` label), `nora/engagement/stages/{deliver,invoke}.py` + `nora/engagement/pipeline.py` + `nora/agent/guards.py` (pass `audience`), `tests/test_metrics_export.py`, `tests/test_metrics_stub.py`, `docs/runbook.md`
- Test: `tests/unit/test_fire_path.py`

- [ ] **Step 1: Enumerate the call sites with AST, not grep**

```bash
cd /tmp/nora-reminders && python3 - <<'PY'
import ast, pathlib
hits = []
for p in sorted(pathlib.Path("nora").rglob("*.py")) + sorted(pathlib.Path("evals").rglob("*.py")):
    for n in ast.walk(ast.parse(p.read_text())):
        if (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "inc"
                and n.args and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "nora_silent_failures_total"):
            hits.append(f"{p}:{n.lineno}")
print(f"{len(hits)} sites")
for h in hits: print(" ", h)
PY
```

Expected output: **9 sites** — `guards.py:145,154,168,192,219`, `pipeline.py:358`, `deliver.py:156`, `invoke.py:109,116`.

**Do not substitute a grep.** An earlier draft of this plan mandated
`grep -rn 'nora_silent_failures_total' nora/ evals/ | grep '\.inc('` and gave it
authority over the spec's list. It returns **one** site of nine, because
`metrics.inc(` sits on the *previous* line in eight of them. A `-B2` variant
returns twelve, three of which are adjacent calls to *different* metrics. The
calls are multi-line, so only a parser is reliable here.

If the AST script disagrees with the nine above, **the script wins** — but read
its output rather than its count.

- [ ] **Step 2: Write the failing test for the synthetic context**

`FirePath`'s constructor is declared here rather than in Step 7 so this test can
exist first — a red checkpoint you cannot write until after the implementation
is not a checkpoint.

```python
# tests/unit/test_fire_path.py
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from nora.app.fire import FirePath
from nora.domain.reminders import Reminder, ReminderState

UTC = timezone.utc
T0 = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)


def _reminder() -> Reminder:
    return Reminder(
        id=uuid.uuid4(), requester_person_id="miles", platform="signal",
        conversation_id="conv-1", conversation_type="group",
        text="幫我睇下 Tyler 喺邊", created_at=T0 - timedelta(hours=1),
        due_at=T0, state=ReminderState.FIRING, fired_at=None, outcome=None,
    )


@pytest.fixture
def fire_path(persons, record_store, memory_store, doctrine_store, metrics) -> FirePath:
    """Only `build_context` is exercised here, so the model-side collaborators
    (`runner`, `adapter`, `registry`) may be None — the test never calls
    `run()`. Step 7's constructor takes them all."""
    return FirePath(
        persons=persons, records=record_store, memory=memory_store,
        doctrine_store=doctrine_store, metrics=metrics,
        runner=None, adapter=None, registry=None,
        timeout_seconds=120.0, clock=lambda: T0,
    )


async def test_the_fire_turn_carries_the_requester(fire_path: FirePath) -> None:
    """The trap this test exists for: blanking sender_id is correct for
    reactions (indicate.begin returns None on a falsy author) and wrong for
    everything that reads it. A blank sender_id resolved through
    ContextAssembler gives tagger=None, which empties peer_card, makes
    Room.scope() unavailable, and makes search_memory refuse."""
    reminder = _reminder()

    ctx = await fire_path.build_context([reminder])

    assert ctx.tagger is not None
    assert ctx.tagger.person_id == reminder.requester_person_id
    assert ctx.message.body == reminder.text          # the request itself
    assert ctx.message.sender_name != ""              # NOT Persons.display_name
    assert ctx.message.sender_id == ""                # so no reaction is placed
    assert ctx.message.timestamp == 0                 # falsy AND an int — models.py:63
```

The `persons`, `record_store`, `memory_store` and `metrics` fixtures do not
exist in `tests/conftest.py` — build them in this file from
`nora.persons.Persons`, `nora.testing`'s fakes and `nora.obs.metrics.Metrics`,
the same way Task 4's `_build()` constructs everything it needs in-file.
`doctrine_store` comes from Task 3's conftest addition.

- [ ] **Step 2b: Run it and watch it fail**

Run: `uv run pytest tests/unit/test_fire_path.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'nora.app.fire'`

- [ ] **Step 3: Add the `audience` label to all nine sites**, defaulting to `"tag"` at the tag-path call sites and `"heartbeat"` in the fire path. Missing one leaves `audience=""`, which is worse than no label.

- [ ] **Step 4: Fix the three exact-label assertions**

`tests/test_metrics_export.py:76`, `:152`, and `tests/test_metrics_stub.py:48`. `generate_latest` emits labels **sorted**, so `audience` renders first: `nora_silent_failures_total{audience="tag",platform="signal",reason="llm_error"}`. `test_metrics_stub.py`'s `snapshot()` keys on `tuple(sorted(labels.items()))`, so its exact-2-tuple subscript raises `KeyError` until updated. Every other assertion reads through `Metrics.get`, which subset-matches, and needs no change.

- [ ] **Step 5: Update `docs/runbook.md:10` and `:17`** — the A8 ratio becomes `nora_silent_failures_total{audience="tag"} / nora_tags_total`, and the `curl | grep` recipe follows. Without this the runbook's first diagnostic silently mixes heartbeat failures into a tag-only denominator.

- [ ] **Step 6: `build_prefill` gains `header:`** — parameterise, do not duplicate. R-13/R-13b escaping is pinned to `build_prefill` by `tests/test_acceptance_rules.py:27-28`; a hand-rolled fire prefill would sit outside both named tests. The set-at and due-at facts go in the header, never the body — the body stays the requester's sentence or §2's premise breaks.

- [ ] **Step 6b: Thread `fire_turn` through the registry — Task 5 owns this**

Nothing carries turn provenance today. `ToolRegistry.build` is
`build(self, *, store, conversation_id, conversation_type, persons, honcho, deliverer, metrics, room, question="", context_window=40)`
— no `fire_turn`. Add `fire_turn: bool = False` to `build_tool_specs`,
`ToolRegistry.specs()` and `ToolRegistry.build()`, defaulting False so every
existing caller is unchanged.

This is legitimate against the registry docstring's "must not become one" bar:
that bar is about registry-owned **collaborators**, not per-turn values.

Task 6 consumes the flag (`remind` refuses when it is True). It cannot be added
there — Task 5 runs first and calls `build(..., fire_turn=True)`.

- [ ] **Step 7: Write `FirePath` — `build_context(rows)` and `async run(rows) -> str`**

Two public methods, because the test in Step 2 exercises context assembly
without a model:
- `async build_context(rows: Sequence[Reminder]) -> TagContext` — §6.4's table.
  **Async, necessarily**: it sources `transcript` from `RecordStore.recent`
  (`nora/domain/ports.py:355`) and `peer_card` from `MemoryStore.peer_card`
  (`:392`), and both are `async`. A synchronous signature would push an
  implementer toward empty `transcript`/`peer_card` — a fire turn with no
  context and every gate green.
- `async run(rows: Sequence[Reminder]) -> str` — returns the §8.1 outcome, and
  is what Task 4's `fire` parameter is typed against (`Awaitable[str]`) — build `TagContext`, `MentionRenderer.rehydrate`, doctrine at `applies_to="heartbeat"`, `ToolRegistry.build(..., fire_turn=True)`, `runner.invoke` inside its own `asyncio.wait_for` with `[heartbeat] timeout_seconds`, `deliver.build` + `Deliverer`, `feedback.run`. Do **not** call `invoke.run` or `deliver.check_outcome` — both own metric branches the fire path must account for itself.

- [ ] **Step 8: Replace Task 4's stub in `composition.py` — Task 5 owns this**

Task 4 wired `fire=_stub_fire`. **No other task replaces it**, so without this
step every gate stays green and the heartbeat delivers a stubbed `"sent"`
forever — the feature ships inert and looks finished.

```python
fire_path = FirePath(...)                 # collaborators as in Step 7
heartbeat = HeartbeatLoop(..., fire=fire_path.run)
```

Delete `_stub_fire` in the same commit.

- [ ] **Step 9: Run, gates, commit.**

---

### Task 6: The tools

**Files:** Modify `nora/agent/tools.py`, `nora/agent/registry.py`, **`nora/app/composition.py`**, `nora/app/cli.py`, `ARCHITECTURE.md`; Test: `tests/test_reminder_tools.py`

**`composition.py` is not optional.** The live `ToolRegistry` is constructed at
`nora/app/composition.py:443`; without `reminders=reminders` there it keeps
`None` and `remind` answers `unavailable` in production while every test passes
against a registry built with the fake. Pass the same `SqlReminderStore`
instance Task 4 created.

**The caps and the clock reach the closure the same way.** Four of the six
refusal conditions need `cfg.reminders.*` and a "now": add **two** parameters
to `ToolRegistry.__init__` alongside `reminders=`, both stated explicitly
because neither exists today:

```python
reminder_caps: ReminderCaps = ReminderCaps(),
clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
```

**`ReminderCaps`, not `RemindersConfig`.** `nora/domain/reminders.py` defines it
(Task 2); `nora/config.py`'s `RemindersConfig` must not cross into
`nora/agent/`. Import-linter's *"only app and `__main__` read config"* contract
(`pyproject.toml:132-141`) names `nora.agent` as a source module and
`nora.config` as forbidden, so the config type there fails
`uv run lint-imports` — and a `TYPE_CHECKING` guard does not help, because
grimp's `exclude_type_checking_imports` defaults to `False` and this repo does
not set it. `nora/app/composition.py` converts:
`ReminderCaps(**cfg.reminders.model_dump())`.

**There is no "existing clock convention" in the registry** — an earlier draft
said to reuse one. `registry.py:66-76` takes
`transit, route, vision, blobs, search, presence, circles, refresher, shortener,
ai_peer` and imports no `datetime`; the only `clock` in the file is a local
**string** of capability prose at `:229`. Three of the six refusal conditions
(`when < now`, `min_lead_seconds`, `max_horizon_days`) depend on the clock, so
an implementer who greps for the "convention" finds a sentence and stalls.

Both are process-lifetime values, which is what that constructor is for
(`registry.py:44-66`).

- [ ] **Step 1: Failing tests**

```python
async def test_remind_refuses_on_a_fire_turn():
    """§7.1: without this she re-arms her own reminder each time one fires —
    recurrence through the back door, and none of §9's caps stop it because
    max_pending_per_requester counts PENDING rows and each fire frees one."""
    result = await remind_on_fire_turn(when=..., text="洗衫")
    assert result["status"] == "refused"


# The remaining seven follow the same shape: build the tool through
# ToolRegistry.build(...), call it, assert status == "refused" and that a note
# is present. Each names the ONE condition it exercises:
#   test_remind_refuses_an_unmapped_requester         -> room.tagger is None
#   test_remind_refuses_past_the_pending_cap          -> count_pending >= 20
#   test_remind_refuses_a_time_in_the_past            -> when < now
#   test_remind_refuses_inside_min_lead_seconds       -> when < now + 120s
#   test_remind_refuses_beyond_max_horizon_days       -> when > now + 30d
#   test_list_reminders_is_scoped_to_this_conversation -> a row in conv-2 is absent
#   test_cancel_refuses_an_id_from_another_conversation -> store.cancel returns False
```

- [ ] **Step 2:** Add `tagger` property to `Room`; add `reminders=` to `ToolRegistry.__init__` alongside `transit`/`route`/`search`/`presence`.

- [ ] **Step 3: Implement the three tools**

Follow the closure shape already in `build_tool_specs` — each tool closes over
`conversation_id`, `room` and the registry's collaborators; none takes a
conversation argument (DEC-24). Exact signatures and return shapes:

```python
async def remind(when: str, text: str) -> dict:
    """when: ISO-8601 with offset. Returns
    {"status": "ok", "due_at": "...", "id": "..."} or
    {"status": "refused", "note": "<a sentence she can act on>"}."""

async def list_reminders() -> dict:
    """Scoped by closure to this conversation. Returns
    {"status": "ok", "reminders": [{"id": ..., "text": ..., "due_at": ...}]}."""

async def cancel_reminder(reminder_id: str) -> dict:
    """Returns {"status": "ok"} or {"status": "refused", "note": ...}.
    Scope is re-checked INSIDE the store (`cancel(id, conversation_id=...)`) —
    a uuid carries none of its own."""
```

Every refusal path returns `status: "refused"` with a `note`, matching
`arithmetic.py` and `whereis.py`. The six refusal cases are Step 1's six tests:
fire turn, unmapped requester, pending cap, past time, inside `min_lead_seconds`,
beyond `max_horizon_days`.

Register in `NATIVE_TOOLS` with budgets `remind` 2, `list_reminders` 2,
`cancel_reminder` 2, each with a one-line `when` (the generated capability block
reads it) and `terminal=False` — **none of these is a terminal verb**; adding one
would break `_terminal_budget_spent`'s AND and the pinned
`terminal_tools() == frozenset({"send_reply"})`.

- [ ] **Step 4:** `nora reminders list` in `cli.py` — **read-only only**. The module opens the store through `create_readonly_sql_engine` with a `SELECT`-only grant; a `cancel` subcommand would break both. The operator's kill switch is `[heartbeat] enabled = false` plus a restart.

- [ ] **Step 5:** Amend **R-1** in `design/09-decision-index.md` and add the replacement named test to `RULE_TESTS`: *the tag path and the fire path are the only two senders.* R-1's existing test asserts both its clauses but only over ambient input, so it stays green untouched — amending the row alone would delete the only written brake on a third send path with nothing going red.

- [ ] **Step 6:** Update `ARCHITECTURE.md` (`test_architecture_doc.py` fails on any new top-level module under `nora/`).

- [ ] **Step 7:** All four gates, commit.

---

## Self-Review

**Spec coverage.** §4 → Task 2. §5 (loop, policy, batching, slots, timeout, reactions) → Tasks 1, 4. §6 (`required`, outcome accounting, header, `TagContext`) → Task 5. §7 (tools, no target, scoped list/cancel, read-only CLI) → Task 6. §8 (typed outcome, at-most-once, cancel-in-flight) → Tasks 2, 4. §9 caps → Tasks 4, 6. §10/§16.1 doctrine → Task 3. §12 mapped identity → Task 6. §13 metrics → Tasks 4, 5. §14 gates → every task's final step. §16 build order → task order.

**Not covered, and deliberately:** §11 phase 2 (the staleness check) is out of scope — it is gated on a measurement that has not been taken. §15's accepted limits are documentation, not work.

**Type consistency.** `ReminderState` is a `StrEnum` used identically in Tasks 2, 4, 6. `ConversationSlots`'s key is `tuple[str, str]` in Tasks 1 and 4. `fire: Callable[[Sequence[Reminder]], Awaitable[str]]` in Task 4 matches `FirePath.run(rows) -> str` in Task 5. `mark()` returns `bool` everywhere.

**Known gaps, called out rather than hidden:** Task 6's **Step 1** gives one worked test and seven condition-named comments rather than eight written tests — each names its exact refusal condition, so it is actionable, but the failing-test step is prose and an executing agent should know that going in. Task 5's step 7 and Task 6's steps 2–3 describe implementations by their interfaces and constraints rather than showing every line. They are the two tasks whose exact code depends on shapes the implementer will read in the files they are editing (`deliver.build`'s eleven arguments, `build_tool_specs`'s closure body). Every *decision* is specified — what to call, what not to call, what must not be duplicated and why — but an implementer will write more original code in these two than in Tasks 1–4.
