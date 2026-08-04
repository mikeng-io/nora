---
kind: plan
title: "Nora Phase 4 — Platforms: registry, resolve_identity(), SendResult"
description: "Close the four SignalAdapter-to-PlatformAdapter gaps, add an AdapterRegistry built from config, and move R-12 off a private adapter attribute onto PlatformIdentity.aliases."
tags: [nora, plan, platforms, phase4]
timestamp: '2026-08-05'
edges:
  - {dst: ../specs/2026-08-04-nora-architecture-design.md, rel: implements, provenance: asserted}
  - {dst: 2026-08-05-nora-phase3-pipelines.md, rel: follows, provenance: asserted}
---

# Nora Phase 4 — Platforms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Make `SignalAdapter` genuinely satisfy `PlatformAdapter`, add an `AdapterRegistry` built from config, and move R-12 from `adapter._account` (a private attribute) onto `PlatformIdentity.aliases` per DEC-9.

**Architecture:** The port already exists in `nora/domain/ports.py` and has since Phase 1b; four gaps between it and the implementation are pinned by deliberate known-gap tests in `tests/contract/test_port_conformance.py`. This phase closes all four and deletes those pins.

**Tech Stack:** Python 3.13, aiohttp, asyncio, pytest.

## Global Constraints

- **`uv add` only, never `pip install`.** A bare `uv sync` drops the evals extra; recover with `uv sync --frozen --group dev --extra evals`.
- **The signal-cli daemon is neutral and shared — NEVER touched.** `derive_account` reads from it; nothing writes to it.
- **Metric names are append-only.** `/health` JSON is **additive only** — `deploy/watchdog.sh` parses `body['sse']['last_event_age_seconds']`, and `sse` must stay a top-level key.
- **`nora/domain/` imports only the standard library.** `nora.infra`/`nora.testing` may import neither `nora.obs` nor `nora.config`. Only `nora.agent.**` may import `strands`. Only `nora.app` and `nora/__main__.py` may import `nora.config` — there are **no** `ignore_imports` exceptions left, and none may be re-added.
- **Module size:** soft cap 200, hard cap 300, enforced for `nora/` by `tests/architecture/test_module_size.py`.
- **Test-seam policy:** collaborators through `__init__` only; no module-level mutable singletons.
- **A port never gains a second implementation — including a fake — without a shared contract suite both pass.**
- **Every gate must be OBSERVED FAILING against a deliberate probe before it is committed.**

**Per-task gate:**
```bash
uv run pytest -q
uv run ruff check nora/ evals/ tests/
uv run mypy nora/domain nora/config.py nora/app nora/infra
uv run lint-imports
```
**Baseline (Phase 3 merge `82748bb`):** 619 passed / 1 deselected; ruff clean; mypy Success on **23 source files**; lint-imports **5 contracts kept**. Container healthy on `nora:p3`.

---

## The four gaps, and the two that carry real risk

Pinned today by `tests/contract/test_port_conformance.py`:

| # | Gap | Port declares | Implementation has |
|---|---|---|---|
| 1 | identity | `async def resolve_identity() -> PlatformIdentity` | nothing; `_account` is a private attribute |
| 2 | send | `async def send(*, target, text) -> SendResult` | `async def send(target, text, *, request_id="") -> bool` |
| 3 | health | `async def health() -> AdapterHealth` | `async def health() -> dict` |
| 4 | listen | `async def listen(*, on_message: Callable[[NormalizedMessage], Awaitable[None]])` | `async def listen(on_event: Callable[[dict], Awaitable[None]])` |

**Gaps 1 and 2 are mechanical. Gaps 3 and 4 are not — they touch a live contract and a pipeline boundary.**

### Trap A — `/health`'s `sse` block is an operator contract

`nora/obs/health.py:44` does `sse = await adapter.health()` and puts the result straight into the response body at `body["sse"]`. Today that dict is `{"connected", "last_event_age_seconds", "account"}`.

`AdapterHealth` (`nora/domain/ports.py:134-137`) has **`platform`, `connected`, `last_event_age_seconds` — no `account`**. A naive swap therefore **drops `account` from `/health`**, which violates the additive-only rule.

`deploy/watchdog.sh` parses `body['sse']['last_event_age_seconds']`, and `tests/test_health.py` asserts `set(body) >= {"sse", ...}`.

**Required:** `/health`'s `sse` object must keep every key it has today and gain `platform`. Decide deliberately whether `account` comes from a new `AdapterHealth` field or is added by `health.py` from the resolved identity — and **write a test that pins the full key set**, so a future change cannot silently drop one.

### Trap B — gap 4 moves `parse` out of the pipeline

`IngestPipeline.run(*, raw_event: dict)` currently calls a `parse` stage which calls `adapter.parse(raw_event)`. If `listen()` emits `NormalizedMessage`, the adapter parses internally and **`IngestPipeline.run` takes a message, not a raw event** — stage 1 disappears from the pipeline.

That is what the spec intends: `PlatformAdapter` deliberately has **no `parse()`** (`domain/ports.py`), because forcing every adapter to pretend its transport is dict-shaped is wrong for Discord's gateway and Telegram's update batches. `platforms/signal/parser.py` stays a directly-tested module.

**Required:** keep `parse_event`'s behaviour identical (it drops DMs, sync messages, self-sends and empty bodies — R-12's first pass). Do not lose those drops. `tests/test_signal_parser.py` must keep passing untouched.

---

## Task 1: `resolve_identity()` and `PlatformIdentity.aliases`

**Files:** `nora/platform/signal/adapter.py`, `nora/platform/signal/account.py`; tests in `tests/test_signal_adapter.py`, `tests/contract/test_port_conformance.py`.

`PlatformIdentity.aliases` is a `frozenset[str]`, and `__post_init__` already folds `primary` into it. **It exists because signal-cli emits `source` *or* `sourceNumber` and sometimes a UUID** (`parser.py:35`), and Telegram has a numeric id *and* a username. R-12 must test **membership**, not equality, or self-recognition breaks the moment a second platform is wired in.

- [ ] `SignalAdapter.resolve_identity()` returns `PlatformIdentity(platform="signal", primary=<account>, aliases=frozenset({...}))`, populated with every id form the daemon reports for this account. Read `nora/platform/signal/account.py::derive_account` first — it already talks to the daemon; **read only, never write**.
- [ ] Tests: the primary is always in `aliases`; a UUID-form alias is included when the daemon reports one; `resolve_identity` is idempotent; it satisfies the port's shape.
- [ ] Delete `test_signal_adapter_gap_to_platform_adapter_is_known` — its docstring says to update it when the gap closes. **Do not weaken it into something that still passes**; it must go, replaced by a positive conformance assertion.
- [ ] **PROBE:** make `aliases` a single-element set omitting the UUID form, and confirm the alias test fails. Restore.

## Task 2: `SendResult`

**Files:** `nora/platform/signal/adapter.py`, `nora/platform/signal/send.py`, `nora/engagement/stages/deliver.py`, `nora/agent/guards.py`.

`SendResult(ok, retryable, retry_after)` replaces `bool` because Telegram answers rate limiting with 429 + `retry_after`, and **`Deliverer`'s latch counts attempts, not successes** — a bool burns the single allowed send on the most common transient failure.

- [ ] `send(*, target, text) -> SendResult`, keyword-only. Map a signal-cli HTTP 429 (or equivalent) to `retryable=True` with `retry_after` when the daemon supplies one; everything else to `retryable=False`.
- [ ] **`Deliverer` must keep its current behaviour exactly**: the latch still counts attempts, `delivery_status` still goes `SENT`/`FAILED`, and `nora_replies_total{status=...}` / `nora_silent_failures_total{reason="send_failed"}` keep their existing label values. **Do not add a retry** — v2 has no auto-retry and adding one here is out of scope.
- [ ] **PROBE:** make `send` return `SendResult(ok=False)` unconditionally and confirm `tests/test_guards_deliverer.py`'s failed-send test still fails correctly (i.e. the plumbing reads `.ok`, not truthiness of the object — a `SendResult` instance is always truthy, which is exactly the bug this probe catches). Restore.

## Task 3: `AdapterHealth` — and `/health`'s key set pinned

**Files:** `nora/platform/signal/adapter.py`, `nora/obs/health.py`, `nora/domain/ports.py` (only if adding a field), `tests/test_health.py`.

Read **Trap A** above before starting.

- [ ] `health() -> AdapterHealth`.
- [ ] `/health`'s `sse` object keeps `connected`, `last_event_age_seconds` and `account`, and gains `platform`. State in the code which route you chose for `account`.
- [ ] **Add a test pinning the full `sse` key set** — `set(body["sse"]) >= {"connected", "last_event_age_seconds", "account", "platform"}` — with a comment naming `deploy/watchdog.sh` as the consumer.
- [ ] Delete `test_signal_adapter_health_gap_to_adapter_health_is_known`.
- [ ] **PROBE:** drop `account` from the `sse` payload and confirm the new key-set test fails. Restore. This is the probe that matters most in the task — the additive-only rule has no other enforcement.

## Task 4: keyword-only `listen(on_message=...)` and `parse` off the pipeline

**Files:** `nora/platform/signal/adapter.py`, `nora/platform/signal/listener.py`, `nora/ingest/pipeline.py`, `nora/ingest/stages/parse.py` (deleted or reduced), `nora/app/runtime.py`, `nora/app/composition.py`, `nora/app/offline.py`, `tests/integration/harness.py`.

Read **Trap B** above before starting.

- [ ] `listen(*, on_message: Callable[[NormalizedMessage], Awaitable[None]])`. The adapter parses internally via `parse_event` and **drops** anything that parses to `None` — DMs, sync messages, self-sends, empty bodies.
- [ ] `IngestPipeline.run(*, message: NormalizedMessage)`. Remove the parse stage from the pipeline; `nora/platform/signal/parser.py` and `tests/test_signal_parser.py` stay exactly as they are.
- [ ] `SignalAdapter.parse` comes off the public surface — the port deliberately has no `parse()`.
- [ ] Update `app/runtime.py`'s `supervise_adapter`, the composition root, `offline.py` and the integration harness.
- [ ] **The R-5 matrix must still pass, all ten faults.** Verify each still injects by neutering it and watching the matching test fail.
- [ ] **PROBE:** make the adapter forward unparseable events instead of dropping them, and confirm a parser drop-case test fails. Restore.

## Task 5: `AdapterRegistry` and R-12 off the private attribute

**Files:** `nora/platform/registry.py` (new), `nora/engagement/stages/admission.py`, `nora/app/composition.py`, `nora/config.py` (only if `[platforms.*]` needs generalising).

- [ ] `AdapterRegistry.from_config(...)` builds enabled adapters and calls `resolve_identity()` on each **at startup, failing fast** if an enabled adapter cannot establish who it is. That fail-fast is the point: R-12 depends on identity being known.
- [ ] **R-12 moves off `adapter._account`.** `nora/engagement/stages/admission.py:48` currently reads a private attribute through a narrow structural type. Replace with membership in the resolved `PlatformIdentity.aliases`, and delete the `_account` structural shim and the phase-4 TODO comment beside it.
- [ ] `test_self_tag_ignored` (R-12's named test in `RULE_TESTS`) must keep its **name** — renaming it breaks the registry. Its body may change.
- [ ] **Nora's own account is never written to `identity.toml`** — that file describes other people (DEC-9).
- [ ] **PROBE:** make R-12 compare against `identity.primary` only instead of `aliases` membership, add a test where the sender arrives under a non-primary alias, and confirm it fails. Restore. This is the whole reason `aliases` exists.
- [ ] Optionally adopt `account = ""` (derive from the daemon) per spec §7 — **only if** it does not add a new fatal startup dependency that the current pinned value avoids. If in doubt, leave the pinned value and say so.

## Task 6: docs, contract cleanup, container gate

- [ ] `ARCHITECTURE.md`: the `PlatformAdapter` row's "Known gap" column becomes **none**; the ports table and package table gain `platform/registry.py`; the file-by-file walk changes where `parse` moved.
- [ ] `design/09-decision-index.md`: DEC-9's "not built" note becomes the real enforcing tests; A1's "none dedicated" row can now name the conformance test.
- [ ] Confirm **no known-gap pins remain** in `tests/contract/test_port_conformance.py` for `PlatformAdapter`, and that a positive conformance assertion replaced them.
- [ ] **Container gate (controller runs this):** `docker build --network=host -t nora:p4 .` → `docker compose up -d --no-build` → `/health` 200 with `sqlite.reachable: true`, top-level `sse` carrying `last_event_age_seconds` **and `account`**. Tag `nora:p4`.

---

## Self-Review

**Spec coverage:** §8 phase 4 lists "registry, `resolve_identity()`, `PlatformIdentity.aliases`, `SendResult`. Optionally adopt `account = ""`." Tasks 1–5 cover all of it; the optional account change is explicitly optional and defaulted to *don't*.

**Deliberately NOT here:** the tool registry and DEC-10 budgets (phase 5); structlog/prometheus-client/OTLP and `/health`'s `mcp[]`/503-on-wedged-adapter (phase 6); Langfuse datasets (phase 7); `METRICS`/`_CLIENT` deletion (deferred to phase 6 as DEC-13).

**Risk:** Task 4 is the largest — it changes the pipeline's entry type and touches the harness, so the R-5 matrix is the tripwire. Task 3's `/health` probe is the highest-value one, because the additive-only rule has no enforcement other than the test this task adds.
