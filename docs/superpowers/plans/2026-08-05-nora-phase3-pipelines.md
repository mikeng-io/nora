---
kind: plan
title: "Nora Phase 3 — Pipelines: ingest/ + engagement/, Handler dissolved"
description: "Dissolve the 228-line Handler god object into two staged pipelines behind ports, move background-task ownership into app/runtime.py, close the four config-import offenders, and stop evals/ importing the test package."
tags: [nora, plan, pipelines, ingest, engagement, phase3]
timestamp: '2026-08-05'
edges:
  - {dst: ../specs/2026-08-04-nora-architecture-design.md, rel: implements, provenance: asserted}
  - {dst: 2026-08-05-nora-phase2-persistence.md, rel: follows, provenance: asserted}
---

# Nora Phase 3 — Pipelines Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dissolve `nora/handler.py`'s 228-line `Handler` into `ingest/` and `engagement/` pipelines of one-module-one-stage components behind the `domain/ports.py` Protocols, move background-task ownership to `app/runtime.py`, close the four config-import offenders, and make `evals/` stop importing `tests/`.

**Architecture:** Two pipelines, matching spec §4.3 and §4.4. `IngestPipeline` runs on every message and is deterministic — parse → audit → identity → memory-feed → engage-gate — with no Agent SDK anywhere on it (D1/R-1). `EngagementPipeline` runs only when the gate says engage, and is a fresh, stateless run per tag (D4). Stages receive **dependencies, not config** (spec §4.6); `app/composition.py` is the only module that reads `Config`.

**Tech Stack:** Python 3.13, asyncio, Strands Agents SDK (engagement only), pytest.

## Global Constraints

Copied verbatim from the spec and `CLAUDE.md`. Every task's requirements implicitly include this section.

- **Dependencies via `uv add`, never raw pip.** A bare `uv sync` silently drops the `evals` extra; restore with `uv sync --frozen --group dev --extra evals`.
- **`nora/domain/` imports only the standard library** plus `nora.domain.models`. Mechanically enforced.
- **`nora.infra` and `nora.testing` may import neither `nora.obs` nor `nora.config`** — both sit below `nora.obs` in the layers contract. This is why `SqlRecordStore` takes `metrics` by injection.
- **Only `nora.agent.**` may import `strands`** (`tests/test_boundaries.py::test_strands_import_boundary`).
- **The signal-cli daemon is neutral and shared — never touched.**
- **Honcho health is `GET /health`**, never `GET /v3/workspaces`.
- **Metric names are append-only.** `/health` JSON is additive only — `deploy/watchdog.sh` parses `body['sse']['last_event_age_seconds']`, and `sse` must stay top-level.
- **`docker build` REQUIRES `--network=host`.**
- **Module size:** soft cap 200 lines, hard cap 300. One stage, one module, one public `async def run(...)`.
- **Test-seam policy:** production classes take collaborators through `__init__` only — no `*_fn` hooks, no `_for_test` params, no module-level mutable singletons.
- **A port never gains a second implementation — including a test fake — without a shared contract suite both pass** (`tests/contract/`).
- **Every gate must be OBSERVED FAILING against a deliberate probe before it is committed.** A gate never seen to fail is not a gate. This project has shipped enforcement that silently did nothing more than twice.
- **A new behavioural rule gets an entry in `design/09-decision-index.md` and a named test in `tests/test_acceptance_rules.py::RULE_TESTS`.**

**Per-task gate** — all four green before any commit:

```bash
uv run pytest -q
uv run ruff check nora/ evals/ tests/
uv run mypy nora/domain nora/config.py nora/app nora/infra
uv run lint-imports
```

**Baseline at plan start (Phase 2 merge, `6f55bcd`):** 549 passed / 1 deselected / 1 xfailed; ruff clean; mypy clean (18 files); lint-imports 4 kept. Container healthy on `nora:p2`; production DB stamped `0001_baseline`.

---

## What this phase must not break

These are the properties the whole refactor exists to preserve. Every task that touches the tag path must leave them true, and the R-5 matrix is the tripwire.

| Rule | Statement | Named test |
|---|---|---|
| R-1 | Ambient traffic never reaches the agent; only tagging causes a send. | `test_ambient_never_constructs_agent` |
| R-2 / R-2b | `send_reply` runs at most once per tag, counting **attempts**; the `Deliverer` latch is single-shot. | `test_second_send_reply_is_cancelled`, `test_deliverer_latch_is_single_shot` |
| R-5 | No error text ever reaches the group — silent failure, always. | `test_silent_failure_matrix` (10 parametrized faults) |
| R-9 | Debounce drops a second tag inside `min_interval_seconds`. | `test_debounce_window` |
| R-10 / b / c | In-flight lock, released on **every** exit path including timeout and LLM error. | `test_concurrent_tags_same_conversation`, `test_lock_released_on_timeout`, `test_lock_released_on_llm_error` |
| R-12 | Nora never triggers herself. | `test_self_tag_ignored` |
| A9 | Record-first: the reply is audited `is_nora=1, delivery_status=pending` **before** `adapter.send` runs. | `tests/test_guards_deliverer.py`, exercised by R-5 |

`tests/test_acceptance_rules.py::RULE_TESTS` is the registry that maps each rule to a **bare test name**, and `test_every_enforced_rule_has_a_named_test` checks every name still exists.

**Verified 2026-08-05 — the registry is path-independent.** `_collected()` shells out to `pytest --collect-only -q` and keeps only `line.split("::")[-1]`, so a test may move between files freely. What breaks it is **renaming or deleting** a named test. Spec §8's warning that "9 of its 21 named tests live in `tests/integration/`" is about where the risk concentrates, not about moves being unsafe. So: reorganising test files is free; renaming `test_silent_failure_matrix` is not. If a rename is genuinely warranted, update `RULE_TESTS` in the same commit — never by deleting the entry, which would silently drop a rule's coverage while leaving the suite green.

## Traps specific to this phase

Paid for already; do not rediscover.

1. **`Handler` carries six test-only attributes, two of which are a live memory leak.** `_invoke_fn`, `_create_agent_fn`, `_last_prefill`, `_last_limits`, `_prefills`, `_guards` (`handler.py:41-46`). `_prefills` and `_guards` are **appended per tag and never cleared**, and each retained `ReplyGuard` pins a `Deliverer` → persona → adapter chain. Dissolving `Handler` must delete all six, and a test must prove the leak is gone — not merely that the attributes are absent.
2. **`nora/agent/factory.py` is the one documented `ignore_imports` exception in the config contract.** It does `from ..config import LimitsConfig, LLMConfig, LLMTierConfig`. Closing it means injecting an already-built model provider instead of passing config classes through — and **the `ignore_imports` entry in `pyproject.toml` must be deleted in the same commit**, or the contract keeps a hole nothing needs.
3. **`tests/test_composition.py::test_only_app_and_main_import_config` is `xfail(strict=True)`.** `strict=True` means that when the offenders are fixed, the test **passes unexpectedly and therefore FAILS the suite**. That is intentional — it is a tripwire telling you to convert it into a real assertion. Expect it and handle it deliberately.
4. **`adapter._account` (`handler.py:82`) is private-attribute access for R-12.** Spec §8 assigns `resolve_identity()` to **phase 4**, not this one. Carry the access forward into whichever stage inherits it, leave a comment naming phase 4, and do **not** opportunistically fix it here — that is a different phase's scope and its own risk.
5. **`Debounce._last` is an unbounded dict** (`tag.py:63`) that now grows with platforms × conversations. Spec §4.6 assigns its TTL sweep to `app/runtime.py`, which this phase creates. Land it here.
6. **`evals/harness.py:28` imports `tests.integration.harness`.** Spec §8 moved this into phase 3 explicitly, because otherwise `evals/` stays broken for four phases. `evals/` may import `nora.app` and `nora.testing` — nothing else.
7. **`nora/models.py` is a re-export shim** for `nora.domain.models`, kept so Phase 1b did not have to rewrite ~20 import sites. This phase deletes it once callers move to `nora.domain.models`.
8. **`asyncio` holds only weak references to tasks.** `Handler._spawn`'s strong-reference set (`handler.py:40`, `_bg`) exists for that reason and the requirement outlives `Handler`. `app/runtime.py`'s `BackgroundTasks` must keep it.
9. **`handler.py`'s `except BaseException`** in `handle_tag` (line 216) is deliberate — it is what guarantees R-10's lock release on *every* exit path. Preserve that breadth wherever the `finally` lands, and keep `CancelledError` propagating correctly (it is a `BaseException`, so a bare `except Exception` would not catch it, but the `finally` still runs).

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `nora/ingest/__init__.py` | Package docstring + public surface. |
| `nora/ingest/pipeline.py` | `IngestPipeline` — a readable table of contents over the five stages. |
| `nora/ingest/policy.py` | `TriggerTextPolicy` — the `EngagePolicy` port implementation. |
| `nora/ingest/stages/parse.py` | Raw transport event → `NormalizedMessage \| None`. |
| `nora/ingest/stages/audit.py` | Append to `RecordStore`; on failure count and drop, never re-raise. |
| `nora/ingest/stages/identity.py` | `Persons.resolve` → `PersonRef \| None`. |
| `nora/ingest/stages/memory_feed.py` | Mapped senders only; off-path; never blocks ingest. |
| `nora/ingest/stages/gate.py` | Runs the `EngagePolicy`; a policy error becomes `EngageDecision(engage=False, reason="policy_error")`. |
| `nora/engagement/__init__.py` | Package docstring + public surface. |
| `nora/engagement/pipeline.py` | `EngagementPipeline` — admission → context → persona → tools → invoke → deliver → feedback. (Not `routing`: a `routing` stage was planned here but deliberately deferred — `EngageDecision.tier` is set by `policy.py:35` and read nowhere yet; see spec §7/§11's "real `Routing`" deferral, also noted at this document's line 596.) |
| `nora/engagement/context.py` | `TranscriptInjection` — the `ContextAssembler` port implementation. |
| `nora/engagement/stages/{admission,persona,tools,invoke,deliver,feedback}.py` | One stage each. |
| `nora/app/runtime.py` | `BackgroundTasks`, the prune sweep, the `Debounce` TTL sweep, adapter supervision. |
| `tests/unit/`, `tests/architecture/` | New test homes per spec §10's layout. |

**Modified:** `nora/app/composition.py`, `nora/__main__.py`, `nora/agent/factory.py`, `nora/obs/logging.py`, `nora/obs/tracing.py`, `nora/tag.py`, `pyproject.toml`, `evals/harness.py`, `evals/run.py`, `nora/testing/`, `tests/integration/harness.py`, `tests/test_acceptance_rules.py`, `tests/test_composition.py`, `tests/test_boundaries.py`, `ARCHITECTURE.md`, `design/09-decision-index.md`, `CLAUDE.md`.

**Deleted:** `nora/handler.py`, `nora/models.py`.

---

## Task 1: `nora/ingest/` skeleton and `TriggerTextPolicy`

**Files:**
- Create: `nora/ingest/__init__.py`, `nora/ingest/policy.py`
- Test: `tests/unit/__init__.py`, `tests/unit/test_ingest_policy.py`

**Interfaces:**
- Produces: `nora.ingest.policy.TriggerTextPolicy(trigger_text: str)`, satisfying `nora.domain.ports.EngagePolicy` — `async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision`.

- [ ] **Step 1: Write the failing test**

```python
"""TriggerTextPolicy — the deterministic gate that ships now.

Deliberately has no LLM and cannot fail: spec §4.3 notes stage 5 had no
failure-behaviour row precisely because this policy cannot raise. The future
`ClassifierPolicy` can, which is why `EngagePolicy` is async today.
"""

from __future__ import annotations

import pytest

from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngageDecision, EngagePolicy, ModelTier
from nora.ingest.policy import TriggerTextPolicy


def _msg(body: str) -> NormalizedMessage:
    return NormalizedMessage(
        platform="signal", conversation_id="g1", conversation_type="group",
        sender_id="+852111", sender_name="Miles", timestamp=1, body=body, raw={},
    )


async def test_a_genuine_trigger_engages():
    policy = TriggerTextPolicy("nora.000")
    decision = await policy.should_engage(message=_msg("hey nora.000 what's up"))
    assert decision.engage is True
    assert decision.tier is ModelTier.FLASH


async def test_an_untagged_message_does_not_engage():
    policy = TriggerTextPolicy("nora.000")
    decision = await policy.should_engage(message=_msg("just chatting"))
    assert decision.engage is False
    assert decision.reason


async def test_the_reason_is_a_stable_category_not_free_text():
    """`reason` is a category consumed by metrics; `rationale` is the free-text
    field. Swapping them would put unbounded cardinality into a label."""
    policy = TriggerTextPolicy("nora.000")
    engaged = await policy.should_engage(message=_msg("nora.000 hi"))
    skipped = await policy.should_engage(message=_msg("hi"))
    assert engaged.reason == "trigger_text"
    assert skipped.reason == "no_trigger"
    assert engaged.rationale is None


async def test_trigger_validation_is_delegated_to_tag_is_tagged():
    """R-11's word-boundary, code-span and Unicode-normalisation rules live in
    `nora.tag.is_tagged` and are tested there by `test_trigger_matrix`. This
    policy must not reimplement them — a second implementation would drift."""
    policy = TriggerTextPolicy("nora.000")
    assert (await policy.should_engage(message=_msg("`nora.000`"))).engage is False
    assert (await policy.should_engage(message=_msg("nora.0000"))).engage is False


def test_it_satisfies_the_engage_policy_port():
    policy: EngagePolicy = TriggerTextPolicy("nora.000")
    assert isinstance(policy, EngagePolicy)


async def test_it_returns_an_engage_decision():
    policy = TriggerTextPolicy("nora.000")
    assert isinstance(await policy.should_engage(message=_msg("hi")), EngageDecision)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_ingest_policy.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'nora.ingest'`.

- [ ] **Step 3: Implement**

Create `nora/ingest/__init__.py`:

```python
"""The ambient path: every message, deterministic, no Agent SDK — ever (D1/R-1).

Five stages (spec §4.3): parse, audit, identity, memory-feed, engage-gate. The
pipeline's only branch is the last one, and its output is an `EngageDecision`,
not a bool, so a future classifier can carry a rationale and a confidence
without changing the seam.

`EngagePolicy` lives here rather than in `engagement/` because the gate runs on
the INGEST pipeline — placing it the other way round inverts the dependency.

May import: `nora.domain`, `nora.tag`, `nora.obs.metrics`.
May NOT import: `nora.config`, `strands`.
"""

from nora.ingest.policy import TriggerTextPolicy

__all__ = ["TriggerTextPolicy"]
```

Create `nora/ingest/policy.py`:

```python
"""The engagement gate that ships now.

`TriggerTextPolicy` is deterministic and does no I/O, so it cannot fail — which
is why spec §4.3's failure table has no row for stage 5. `EngagePolicy` is
nonetheless `async` because the `ClassifierPolicy` that joins it later is a
network call, and a sync-to-async change would break every call site on the
ingest pipeline.

When the classifier lands it is `CompositePolicy([TriggerTextPolicy,
ClassifierPolicy])` — first-match-wins, deterministic trigger first and
short-circuiting — NOT a replacement. Replacing this gate would mean an LLM
outage silently stops explicit `nora.000` tags from working, which under R-5 is
strictly worse than today.
"""

from __future__ import annotations

from nora.domain.models import NormalizedMessage
from nora.domain.ports import EngageDecision, ModelTier
from nora.tag import is_tagged


class TriggerTextPolicy:
    """Satisfies `nora.domain.ports.EngagePolicy`."""

    def __init__(self, trigger_text: str) -> None:
        self._trigger_text = trigger_text

    async def should_engage(self, *, message: NormalizedMessage) -> EngageDecision:
        # R-11's matching rules (word boundary, code spans, Unicode
        # normalisation) live in `nora.tag.is_tagged` and are pinned by
        # `test_trigger_matrix`. Reimplementing them here would create a second
        # implementation free to drift from the one the rule is tested against.
        if is_tagged(message.body, self._trigger_text):
            return EngageDecision(engage=True, reason="trigger_text", tier=ModelTier.FLASH)
        return EngageDecision(engage=False, reason="no_trigger")
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_ingest_policy.py -q`
Expected: 6 passed.

- [ ] **Step 5: OBSERVE THE DELEGATION GATE FAILING (mandatory)**

`test_trigger_validation_is_delegated_to_tag_is_tagged` is the test that stops R-11 gaining a second implementation. Prove it discriminates: replace the `is_tagged(...)` call with a naive `self._trigger_text in message.body`.

Run: `uv run pytest tests/unit/test_ingest_policy.py -q`
Expected: `test_trigger_validation_is_delegated_to_tag_is_tagged` FAILS on both the code-span and the `nora.0000` case. Restore.

- [ ] **Step 6: Full gate and commit**

```bash
uv run pytest -q && uv run ruff check nora/ evals/ tests/ \
  && uv run mypy nora/domain nora/config.py nora/app nora/infra && uv run lint-imports
git add nora/ingest/ tests/unit/
git commit -m "feat(ingest): TriggerTextPolicy behind the EngagePolicy port

Deterministic, no LLM on the ambient path. R-11's matching rules stay in
nora.tag.is_tagged rather than being reimplemented — observed failing against
a naive substring check before landing."
```

---

## Task 2: Ingest stages and `IngestPipeline`

**Files:**
- Create: `nora/ingest/stages/__init__.py`, `.../parse.py`, `.../audit.py`, `.../identity.py`, `.../memory_feed.py`, `.../gate.py`, `nora/ingest/pipeline.py`
- Test: `tests/unit/test_ingest_stages.py`, `tests/unit/test_ingest_pipeline.py`

**Interfaces:**
- Consumes: `TriggerTextPolicy` (Task 1); ports `RecordStore`, `MemoryStore`, `EngagePolicy`; `nora.persons.Persons`.
- Produces: `IngestPipeline(*, adapter, audit, memory, persons, policy, metrics, spawn)` with `async def run(self, *, raw_event: dict) -> IngestResult`, where `IngestResult` is a frozen dataclass `(message: NormalizedMessage | None, decision: EngageDecision | None)`. `spawn` is a `Callable[[Coroutine], None]` supplied by the runtime so off-path work never blocks the listener.

**Failure behaviour is the specification here** (spec §4.3). Each stage's behaviour on failure is a rule, not an implementation detail:

| # | Stage | On failure |
|---|---|---|
| 1 | `parse` | `None` → drop silently (a DM, a sync message, a self-send, an empty body) |
| 2 | `audit` | log + `nora_errors_total{stage="ambient"}` + **drop the message**; never re-raise into the listener |
| 3 | `identity` | unmapped → `None`, continue (D6) |
| 4 | `memory_feed` | mapped senders only; off-path via `spawn`; never blocks ingest |
| 5 | `gate` | policy error → `EngageDecision(engage=False, reason="policy_error")` + `nora_errors_total{stage="engage_gate"}` |

- [ ] **Step 1: Write the failing stage tests**

Create `tests/unit/test_ingest_stages.py`. Each test asserts one row of that table. Use `nora.testing.FakeRecordStore` for the store and a local `FakeMemory` double for Honcho.

```python
async def test_audit_failure_drops_the_message_and_never_raises(...):
    """The listener must survive a full disk. `on_event` is awaited by the SSE
    reader, so a raise here stops ingest for every conversation on this
    adapter — which is why stage 2's documented behaviour is drop, not fail."""

async def test_audit_failure_counts_nora_errors_total_stage_ambient(...):

async def test_an_unmapped_sender_is_audited_but_never_fed(...):
    """D6: peer mapping is manual and unmapped senders are never fed to the
    semantic store. The audit row is still written — the audit record is not
    conditional on identity."""

async def test_memory_feed_runs_off_the_listener_path(...):
    """Asserts `spawn` was called rather than the coroutine awaited inline.
    `on_event` is awaited by the SSE reader (listener.py), so an inline
    200-800ms Honcho call stalls the socket read for every adapter in the
    process."""

async def test_a_policy_error_becomes_a_no_engage_decision(...):
    """TriggerTextPolicy cannot raise, but ClassifierPolicy will. Under R-5 a
    gate that propagates would take the listener down; the decision must
    degrade closed."""

async def test_a_policy_error_counts_nora_errors_total_stage_engage_gate(...):
```

Write each body in full against the real stage signatures — no `pass` bodies, no TODOs.

- [ ] **Step 2: Run to verify they fail**

Expected: `ModuleNotFoundError: No module named 'nora.ingest.stages'`.

- [ ] **Step 3: Implement the five stages**

Each is one module with one public `async def run(...)`. Keep them small — the largest should be well under 60 lines. `audit.py`, for example:

```python
"""Stage 2 — the audit write. Failure drops the message; it never re-raises.

`on_event` is awaited by the SSE reader (`platform/signal/listener.py`), so a
raise here would stop ingest for every conversation on this adapter. Dropping
one message is the smaller loss, and it is counted so the loss is visible.
"""

from __future__ import annotations

import logging
from typing import Any

from nora.domain.models import NormalizedMessage
from nora.domain.ports import RecordStore

log = logging.getLogger("nora.ingest.audit")


async def run(*, message: NormalizedMessage, audit: RecordStore, metrics: Any) -> bool:
    """Return True if the message was recorded and ingest should continue."""
    try:
        await audit.append(message=message)
    except Exception:                       # noqa: BLE001 — see module docstring
        log.exception("audit append failed; dropping message")
        metrics.inc("nora_errors_total", stage="ambient")
        return False
    return True
```

- [ ] **Step 4: Implement `IngestPipeline`**

`pipeline.py` stays a readable table of contents — it calls the five stages in order and returns an `IngestResult`. It contains no business logic of its own; anything that looks like a decision belongs in a stage. Target: under 80 lines.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/unit/ -q`
Expected: all pass.

- [ ] **Step 6: OBSERVE TWO GATES FAILING (mandatory)**

- **Probe A — remove `audit.py`'s try/except** so a failure propagates. Expected: `test_audit_failure_drops_the_message_and_never_raises` FAILS. Restore.
- **Probe B — await the memory feed inline** instead of calling `spawn`. Expected: `test_memory_feed_runs_off_the_listener_path` FAILS. Restore.

Paste both outputs. These two are the stages whose failure behaviour is load-bearing for R-5 and for ingest liveness.

- [ ] **Step 7: Full gate and commit**

---

## Task 3: `TranscriptInjection` and the engagement package

**Files:**
- Create: `nora/engagement/__init__.py`, `nora/engagement/context.py`
- Test: `tests/unit/test_engagement_context.py`

**Interfaces:**
- Produces: `TranscriptInjection(*, audit: RecordStore, memory: MemoryStore, persons: Persons, context_window: int)` satisfying `ContextAssembler` — `async def assemble(self, *, request: EngagementRequest) -> TagContext`.

This replaces `Handler._collect_context`. Preserve its two behaviours exactly:
- A transcript read failure logs, counts `nora_errors_total{stage="context"}`, and **continues with an empty transcript** — a thinner reply beats no reply.
- An unmapped tagger yields `tagger=None` and an empty peer card, without an extra Honcho call.

`ContextAssembler` takes an `EngagementRequest`, not `(msg, episode)`, because the old signature forced the caller to fetch an episode before knowing whether the assembler wanted one. `EngagementRequest` carries `(message, decision, tag_id)` — and `decision` is the only route by which a future classifier's `rationale` reaches the prefill, which `design/02 §True Ambient Hooks` requires.

- [ ] **Step 1: Write the failing tests**, including:

```python
async def test_a_transcript_read_failure_yields_an_empty_transcript_not_an_error(...):
async def test_a_transcript_read_failure_counts_nora_errors_total_stage_context(...):
async def test_an_unmapped_tagger_yields_no_peer_card_and_no_honcho_call(...):
async def test_the_transcript_is_oldest_first_and_window_bounded(...):
def test_it_satisfies_the_context_assembler_port(...):
```

- [ ] **Step 2–4:** run/fail, implement, run/pass.
- [ ] **Step 5: OBSERVE THE GATE FAILING** — remove the try/except around the transcript read; `test_a_transcript_read_failure_yields_an_empty_transcript_not_an_error` must FAIL. Restore.
- [ ] **Step 6: Full gate and commit.**

---

## Task 4: Engagement stages — admission, persona, tools

**Files:**
- Create: `nora/engagement/stages/__init__.py`, `.../admission.py`, `.../persona.py`, `.../tools.py`
- Test: `tests/unit/test_engagement_admission.py`

**`admission.py` owns R-9, R-10 and R-12** — the three rules currently inline in `Handler.on_event` (lines 82-97). This is the highest-risk module in the task, because R-10's lock must be released on every exit path.

- **R-12**: `message.sender_id == adapter._account`. **Keep the private-attribute access and add a comment naming phase 4**, which replaces it with `resolve_identity()` per DEC-9. Do not fix it here.
- **R-9**: `Debounce.allow(key)` where `key = (platform, conversation_id)`.
- **R-10**: the in-flight set. Admission *acquires*; the pipeline's `finally` *releases*. Make that ownership explicit in both docstrings — a lock acquired in one module and released in another is exactly the shape that grows leaks.

- [ ] Tests must cover: a self-tag is refused; a debounced tag is refused and counts `nora_tags_debounced_total`; a second concurrent tag for the same conversation is refused and counts `nora_tags_dropped_total{reason="in_flight"}`; a different conversation is admitted concurrently.
- [ ] **OBSERVE FAILING**: remove the self-tag check → `test_self_tag_ignored`'s unit analogue FAILS. Restore.
- [ ] Full gate and commit.

---

## Task 5: `invoke.py` and closing the `factory.py` config offender

**Files:**
- Create: `nora/engagement/stages/invoke.py`
- Modify: `nora/agent/factory.py`, `pyproject.toml` (delete the `ignore_imports` entry)
- Test: `tests/unit/test_engagement_invoke.py`, `tests/test_factory.py`

**This task closes trap 2.** `nora/agent/factory.py` currently does `from ..config import LimitsConfig, LLMConfig, LLMTierConfig`, the single documented `ignore_imports` exception. Replace those parameters with narrow, framework-free inputs — the composition root builds the model provider and the limits, and hands them over already built.

- [ ] **Step 1:** change `factory.create_client` / `create_model_provider` / `build_limits` to take explicit scalars (base_url, api_key, model name, max_tokens, temperature, timeout_seconds, and the four limit values) rather than config objects. Update `tests/test_factory.py`.
- [ ] **Step 2: DELETE the `ignore_imports` entry** from `pyproject.toml`'s config contract. This is the point of the task — leaving it means the contract keeps a hole nothing needs.
- [ ] **Step 3: OBSERVE THE CONTRACT FAILING (mandatory).** Re-add `from ..config import LLMConfig` to `factory.py`, run `uv run lint-imports`, and confirm "only app and `__main__` read config" is now **BROKEN** naming `nora.agent.factory -> nora.config`. Before this task the same import was *allowed* by the exception, so this probe is what proves the exception is genuinely gone rather than merely edited. Restore and confirm 4 kept.
- [ ] **Step 4:** implement `invoke.py`, preserving the timeout/`TimeoutError` → `nora_silent_failures_total{reason="invocation_timeout"}` and generic-exception → `{reason="llm_error"}` behaviour exactly. Both are R-5 cases with named tests.
- [ ] Full gate and commit.

---

## Task 6: `deliver.py`, `feedback.py`, and `EngagementPipeline`

**Files:**
- Create: `nora/engagement/stages/deliver.py`, `.../feedback.py`, `nora/engagement/pipeline.py`
- Test: `tests/unit/test_engagement_pipeline.py`

`deliver.py` wraps the existing `Deliverer` + `ReplyGuard` (which stay in `nora/agent/` — they are `strands.HookProvider`s and moving them would drag strands into `engagement/` and widen the boundary allowlist). `feedback.py` owns the single post-reply exchange feed.

**`EngagementPipeline.run` must reproduce `handle_tag`'s exit-path guarantees exactly:**

```python
        finally:
            self._in_flight.discard(key)     # R-10: released on EVERY exit path
            total = time.perf_counter() - started
            metrics.observe("nora_tag_latency_seconds", total)
            metrics.observe("nora_overhead_seconds", max(0.0, total - llm_elapsed))
```

and the outer `except BaseException` that counts `nora_silent_failures_total{reason="handler_error"}`. Keep `BaseException`, not `Exception` — trap 9.

- [ ] Tests: the lock is released on success, on timeout, on LLM error, and on an arbitrary exception; the latency and overhead histograms are observed on every path; `no_send_call` is counted when the model never calls `send_reply`.
- [ ] **OBSERVE FAILING**: narrow `except BaseException` to `except Exception` and confirm a `BaseException`-raising fault leaves the in-flight key stuck. Restore.
- [ ] Full gate and commit.

---

## Task 7: `app/runtime.py`

**Files:**
- Create: `nora/app/runtime.py`
- Modify: `nora/tag.py` (expose a sweep hook on `Debounce`)
- Test: `tests/unit/test_runtime.py`

`app/runtime.py` takes over what `Handler` and `App` owned:

- **`BackgroundTasks`** — a set holding **strong** references. asyncio holds only weak ones, so a bare `create_task` can be garbage-collected mid-flight (trap 8). Carry `Handler._spawn`'s done-callback logging too.
- **The prune sweep** — moved from `__main__._prune_loop`, unchanged in behaviour.
- **The `Debounce._last` TTL sweep** — trap 5. It is an unbounded dict growing with platforms × conversations.
- **Per-adapter supervision** — restart with backoff, `nora_adapter_restarts_total{platform}`. A wedged adapter must drive `/health` 503; a naive `asyncio.gather` either kills the process on one adapter's bug or hides a dead listener behind a 200.

- [ ] Tests must prove: a cancelled task is discarded without logging an error; a failing task logs and is discarded; the prune sweep survives a failing sweep and keeps looping; the debounce sweep actually removes stale entries (**assert the dict shrinks**, not that the coroutine exists); an adapter that raises is restarted and counted.
- [ ] **OBSERVE FAILING**: drop the strong reference (use a bare `asyncio.create_task` and force a GC) and confirm the task-tracking test FAILS. Restore.
- [ ] Full gate and commit. **`nora_adapter_restarts_total` is a new metric — names are append-only, so add, never rename.**

---

## Task 8: Composition root, `__main__` slimmed, `Handler` deleted

**Files:**
- Modify: `nora/app/composition.py`, `nora/__main__.py`
- Delete: `nora/handler.py`, `nora/models.py`
- Test: `tests/test_composition.py`, `tests/test_main.py`, `tests/integration/harness.py`

**This is the atomic cut of Phase 3** — the equivalent of Phase 2's Task 8, and the same rule applies: it cannot land half-done, because the R-5 matrix runs through the harness.

- [ ] `build_dependencies` grows the full graph: `audit`, `memory`, `persons`, `metrics`, plus `policy`, `assembler`, `ingest`, `engagement`, `runtime`. It stays the only reader of `Config`.
- [ ] `__main__.build_app` becomes thin: load config → `build_dependencies` → `App`. Keep the ordered, fatal `StartupError` validation exactly as it is — each `except` clause there is a readable operator message.
- [ ] **Delete all six test-only attributes with `Handler`.** Faking the LLM becomes a fake model provider injected by `build_dependencies`, per spec §10's test-seam policy — not an `_invoke_fn` hook.
- [ ] **Prove the leak is gone (trap 1).** Add `tests/architecture/test_no_retained_per_tag_state.py`: drive N tags through the pipeline and assert that no per-tag object is retained afterwards — e.g. take `gc` references to `ReplyGuard` instances and assert the count does not grow with N. Assert on observable behaviour, not on an attribute's absence: `_prefills`/`_guards` existed precisely because tests asserted on retained constructed objects.
- [ ] **OBSERVE FAILING**: reintroduce a `self._guards.append(guard)` line and confirm the new leak test FAILS. Restore. This is the probe that matters most in this task — the leak is the thing the phase exists to remove.
- [ ] `tests/integration/harness.py` moves onto the pipelines and the injected fake model provider. Preserve every one of the 10 R-5 fault cases, and **verify each still injects** by neutering it and watching the matching test fail.
- [ ] Update `tests/test_acceptance_rules.py::RULE_TESTS` deliberately for any renamed test. Never delete an entry to make the registry pass.
- [ ] Full gate. Run `uv run pytest tests/integration/ -q` explicitly and report 48/48.

---

## Task 9: `obs/logging.py`, `obs/tracing.py`, and flipping the xfail

**Files:**
- Modify: `nora/obs/logging.py`, `nora/obs/tracing.py`, `tests/test_composition.py`
- Test: `tests/test_logging.py`, `tests/test_tracing.py`

- [ ] `setup_logging(cfg, ...)` and `setup_tracing(cfg, ...)` take duck-typed config-shaped objects. Replace with explicit parameters (level, path, format; enabled, exporter, endpoint).
- [ ] **Trap 3 fires here.** `tests/test_composition.py::test_only_app_and_main_import_config` is `xfail(strict=True)`, so the moment the last offender closes it **passes unexpectedly and fails the suite**. Convert it into a real, positive assertion: every module under `nora/` except `nora/app/**` and `nora/__main__.py` must not import `nora.config`, with no allowlist left.
- [ ] **OBSERVE FAILING**: add `from nora.config import Config` to `nora/obs/logging.py` and confirm the now-strict test FAILS. Restore.
- [ ] Confirm `pyproject.toml`'s config contract has **no** `ignore_imports` entries remaining, and that `uv run lint-imports` still reports 4 kept.
- [ ] Full gate and commit.

---

## Task 10: `evals/` stops importing `tests/`

**Files:**
- Modify: `evals/harness.py`, `evals/run.py`, `nora/testing/`
- Test: `tests/test_boundaries.py`, `tests/test_evals_gate.py`

**Trap 6.** `evals/harness.py:28` does `from tests.integration.harness import ScriptedTurn, build_handler, group_event`. Spec §8 pulled this into phase 3 explicitly so `evals/` is not broken for four phases.

- [ ] Move `ScriptedTurn`, the scripted-LLM fake, and the event builder into `nora/testing/`. Both `tests/` and `evals/` import from there.
- [ ] **`nora/testing/` gains a second port implementation, so the contract rule applies.** If the scripted model provider is a second implementation of a port, it needs a shared contract suite. If it is not a port implementation, say so explicitly in the report rather than skipping the question.
- [ ] Add a boundary test: **`evals/` may import `nora.app` and `nora.testing` and nothing else** from `nora/`, and may not import `tests`. Add `evals` to the import-linter contracts if it can be expressed there.
- [ ] **OBSERVE FAILING**: re-add `from tests.integration.harness import ScriptedTurn` to `evals/harness.py` and confirm the new boundary test FAILS. Restore.
- [ ] Run the evals suite (`uv run pytest tests/test_evals_gate.py tests/test_evals_rubric.py tests/test_evals_adversarial.py -q`) and report.
- [ ] Full gate and commit.

---

## Task 11: Documentation, decision index, container gate

**Files:** `ARCHITECTURE.md`, `CLAUDE.md`, `design/09-decision-index.md`, `docs/runbook.md`

**Accuracy standard.** Phase 2 produced **six** separate cases of documentation asserting things that were not true. Every present-tense claim must be true of the tree as it stands; anything that only becomes true in a later phase must say so explicitly. Where you state a test enforces a decision, open the test and confirm it.

- [ ] `ARCHITECTURE.md`: the package table gains `ingest/` and `engagement/`, loses `handler.py` and `models.py`; the ports table's `EngagePolicy` and `ContextAssembler` rows change from "inline in `Handler`" to their real classes; the file-by-file walk is rewritten around the two pipelines; §5's "change engagement decisions" row moves from **(target)** to the live `nora/ingest/policy.py`.
- [ ] `design/09-decision-index.md`: DEC-1's "structural — no single test" entry can now name the pipelines; A3's classifier note should point at `TriggerTextPolicy` as the composite's first element; R-9/R-10/R-12's enforcing tests move if their tests moved.
- [ ] `CLAUDE.md`: update the reading order if package names changed; add any new never-do learned this phase.
- [ ] **Container gate (controller runs this, not the implementer):** `docker build --network=host -t nora:p3 .` → `docker compose up -d --no-build` → `curl :9100/health` → 200 with `sqlite.reachable: true` and a top-level `sse` carrying `last_event_age_seconds`.
- [ ] Tag `nora:p3`.

---

## Self-Review

**Spec coverage** — every phase-3 item in spec §8 maps to a task:

| Spec requirement | Task |
|---|---|
| `ingest/` package + stages (§4.3) | 1, 2 |
| `EngagePolicy` → `TriggerTextPolicy` | 1 |
| `engagement/` package + stages (§4.4) | 3, 4, 5, 6 |
| `ContextAssembler` → `TranscriptInjection` | 3 |
| `Handler` dissolved; six test-only attrs deleted; the leak closed | 8 |
| `app/runtime.py`: BackgroundTasks, prune sweep, Debounce TTL, adapter supervision | 7 |
| Four config-import offenders closed; `ignore_imports` deleted | 5, 8, 9 |
| `evals/` stops importing `tests/` | 10 |
| `RULE_TESTS` updated deliberately | 8 |
| `nora/models.py` shim deleted | 8 |
| Container gate + `nora:p3` | 11 |

**Deliberately NOT in this phase** (spec §7/§11): `Episode`/`EpisodeStore` in any form; MCP providers; the `deep` tier and real `Routing`; `resolve_identity()` and `SendResult` (phase 4); the tool registry and DEC-10 budgets (phase 5); structlog/prometheus-client/OTLP (phase 6).

**Risk note for the executor:** Task 8 is the atomic cut and the one that cannot be partially landed. If it needs splitting mid-flight, the seam is the harness (integration tests) versus the production graph — but both must land in the same commit or the R-5 matrix breaks. Prefer finishing it whole. Task 5's contract probe and Task 8's leak probe are the two most important observations in the phase: the first proves the config exception is genuinely gone, the second proves the leak the phase exists to remove is actually removed.
