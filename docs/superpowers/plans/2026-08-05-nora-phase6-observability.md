---
kind: plan
title: "Nora Phase 6 — Observability: structlog, prometheus-client, OTLP → Langfuse"
description: "Swap the hand-rolled metrics/logging for prometheus-client and structlog, add an HTTP/protobuf OTLP exporter pointed at Langfuse, and delete the module-level singletons DEC-13 deferred here."
tags: [nora, plan, observability, structlog, prometheus, otlp, langfuse, phase6]
timestamp: '2026-08-05'
edges:
  - {dst: ../specs/2026-08-04-nora-architecture-design.md, rel: implements, provenance: asserted}
  - {dst: 2026-08-05-nora-phase5-tools.md, rel: follows, provenance: asserted}
---

# Nora Phase 6 — Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Replace the hand-rolled `Metrics` with **prometheus-client**, the stdlib logging setup with **structlog**, and the (uninstalled) gRPC OTLP exporter with an **HTTP/protobuf** one pointed at the live Langfuse on `:3100` — then delete the module-level singletons DEC-13 deferred to this phase.

**Tech Stack:** prometheus-client, structlog, opentelemetry HTTP/protobuf exporter, aiohttp, pytest.

## Global Constraints

- **`uv add` only, never `pip install`.** Bare `uv sync` drops the evals extra; recover with `uv sync --frozen --group dev --extra evals`.
- **Metric NAMES are append-only.** This is an SRE's repo and renames break dashboards. **Adding a label is backward-compatible and is required here** (spec §6).
- **`/health` JSON is additive only.** `sse` stays top-level carrying `last_event_age_seconds`, `account`, `platform` — pinned by `test_health_sse_key_set_is_pinned`. `deploy/watchdog.sh` parses `body['sse']['last_event_age_seconds']`.
- **Never `start_http_server()`.** Metrics render via `generate_latest()` into the **existing aiohttp app on port 9100**. A second server breaks `docker-compose.yml`'s healthcheck and `deploy/watchdog.sh`.
- **Honcho health is `GET /health`**, never `GET /v3/workspaces`. **The signal-cli daemon is never touched.**
- Only `nora.app`/`__main__` may import `nora.config`; **no `ignore_imports` exceptions exist and none may be added**. `nora/domain/` is stdlib-only. `nora.infra`/`nora.testing` may import neither `nora.obs` nor `nora.config`. Only `nora.agent.**` may import `strands`.
- **Module size:** soft 200 / hard 300 for `nora/`, enforced by `tests/architecture/test_module_size.py`.
- **Every gate must be OBSERVED FAILING against a deliberate probe before it is committed.**

**Per-task gate:**
```bash
uv run pytest -q
uv run ruff check nora/ evals/ tests/
uv run mypy nora/domain nora/config.py nora/app nora/infra
uv run lint-imports
```
**Baseline (Phase 5 merge):** 646 passed / 1 deselected; ruff clean; mypy Success on **23 source files**; lint-imports **5 kept**. Container healthy on `nora:p5`.

---

## Traps — all four verified against the tree on 2026-08-05

1. **`tests/conftest.py` has an autouse `METRICS.reset()` fixture.** prometheus-client's `CollectorRegistry` has **no `reset()`**. If the metrics swap lands without changing that fixture in the **same commit**, every test fails at **collection**, not as an assertion — the suite goes from 646 passing to zero collected. Spec §8 phase 6 calls this out explicitly.
2. **`nora/obs/tracing.py:36` imports the gRPC exporter** (`opentelemetry.exporter.otlp.proto.grpc.trace_exporter`), and **nothing OTLP is installed** — `uv.lock` has zero matches for `structlog`, `prometheus-client` or `opentelemetry-exporter-otlp`. Phase 4 added a narrow mypy override for exactly this missing module; **that override should come off once the real dependency lands.**
3. **Langfuse's OTLP endpoint is HTTP/protobuf, not gRPC.** Verified live: `http://localhost:3100/api/public/health` → **200**. The traces path is `/api/public/otel/v1/traces` and returns **401 without credentials** — so auth headers are mandatory.
4. **`TracingConfig` has no `headers_env`.** It is `enabled`, `exporter: Literal["console","otlp","file"]`, `endpoint` — with `extra="forbid"`, so adding a field is a deliberate schema change and `config.example.toml` plus `.env.example` must move with it. **Secrets are referenced by `*_env` naming, never inlined** (DEC-8); there is no `${VAR}` interpolation.

---

## Task 1: prometheus-client, and the conftest fixture in the same commit

**Files:** `nora/obs/metrics.py`, `tests/conftest.py`, `nora/obs/health.py`, `tests/test_metrics_stub.py`, `tests/test_metrics_export.py`, `pyproject.toml`.

- [ ] `uv add prometheus-client`.
- [ ] Replace the hand-rolled counter/gauge/histogram store with prometheus-client collectors held on an **injected registry object**, not a module global. Every call site already passes `metrics` explicitly (Phase 3 made it required in engagement; ingest always had it).
- [ ] **Change `tests/conftest.py`'s autouse fixture in this same commit.** Replace `METRICS.reset()` with whatever the new object supports — most likely constructing a fresh `CollectorRegistry` per test rather than resetting a shared one.
- [ ] `/metrics` renders via `generate_latest()` into the **existing aiohttp app**. **Do not call `start_http_server()`.**
- [ ] **Every existing metric name is preserved exactly.** Enumerate them from the current `DECLARED` and assert the rendered output still contains each one.
- [ ] **Add the `platform` label** to `nora_tags_total`, `nora_replies_total`, `nora_silent_failures_total`, `nora_errors_total` (spec §6 — today only `nora_messages_total` has one, so with N adapters you cannot tell which platform is failing).
- [ ] **PROBE A:** rename one metric in the new implementation and confirm the name-preservation test fails. Restore.
- [ ] **PROBE B:** temporarily restore the old `METRICS.reset()` line in `conftest.py` and confirm the suite fails **at collection** — this is the trap, and seeing it fire is how you know the fixture change was load-bearing. Restore.

## Task 2: structlog

**Files:** `nora/obs/logging.py`, `tests/test_logging.py`, `pyproject.toml`.

- [ ] `uv add structlog`. Keep the stdlib bridge so third-party loggers still land in the same stream.
- [ ] **Redaction generalises to N declared secrets.** `setup_logging` already takes `secrets`; keep that contract. R-16 (*the LLM API key value never appears in a log line, trace attribute, exception message, or `raw_json`*) is a guardrail rule with a named test — `test_secret_never_logged_in_the_message` — whose **name must not change** (`RULE_TESTS` maps rules to bare test names).
- [ ] `tag_id` becomes a **contextvar** so every line inside a tag carries it without threading.
- [ ] Preserve the existing log format contract where anything parses it — check `deploy/` and `docs/runbook.md` before changing shape.
- [ ] **PROBE:** feed a secret through a log call and confirm the R-16 test fails when redaction is bypassed. Restore.

## Task 3: OTLP → Langfuse over HTTP/protobuf

**Files:** `nora/obs/tracing.py`, `nora/config.py`, `config.example.toml`, `.env.example`, `pyproject.toml`, `tests/test_tracing.py`.

- [ ] `uv add opentelemetry-exporter-otlp-proto-http` (the **HTTP** variant — the gRPC one cannot talk to Langfuse).
- [ ] Add an `otlp_http` exporter branch. Decide whether it replaces the existing `otlp` value or joins it, and say why; if you keep `otlp` meaning gRPC, note that nothing installs it.
- [ ] **Add `headers_env: str` to `TracingConfig`** — the name of an environment variable holding the auth header value. Secrets are referenced by `*_env`, never inlined, and there is no `${VAR}` interpolation (DEC-8). Update `config.example.toml` and `.env.example`. `extra="forbid"` means a stale key is a hard startup failure, so keep the live `config.toml` in step.
- [ ] **Remove Phase 4's narrow mypy override** for `opentelemetry.exporter.otlp.*` once the real package is installed, and confirm mypy still passes without it. If the HTTP package still lacks stubs, keep a narrowed override and say so.
- [ ] **`trace_attributes` must keep emitting provenance only, never message content** — there is a dedicated test for this. `persona_sha256` linkage (DEC-5) stays.
- [ ] **Do not enable OTLP in the live `config.toml` as part of this task.** Spec §7 is explicit that flipping the exporter is its own commit with its own verification: the current value is `file`, and switching it silently is exactly the kind of behaviour change §7 says not to smuggle into a structural phase. Land the capability; leave the switch to Task 5.
- [ ] **PROBE:** point the exporter at an unreachable endpoint and confirm tracing failures do not crash the app (tracing is never load-bearing). Restore.

## Task 4: delete the singletons DEC-13 deferred here

**Files:** `nora/obs/metrics.py`, `nora/agent/factory.py`, `design/09-decision-index.md`.

DEC-13 (recorded 2026-08-05) deferred four module-level mutable singletons to this phase: `nora/obs/metrics.py`'s `METRICS` / `DECLARED` / `_BUCKETS`, and `nora/agent/factory.py`'s `_CLIENT`.

- [ ] Delete `METRICS`, `DECLARED`, `_BUCKETS` — Task 1 should already have made them unnecessary.
- [ ] Replace `_CLIENT` with an explicit lifecycle object threaded through `Dependencies`, closed by `App.shutdown()`. CLAUDE.md's test-seam policy bans module-level mutable singletons; this is the last one.
- [ ] **Update DEC-13 in place** to record that it is now built, with its enforcing tests — do not delete the entry, which would erase the reasoning.
- [ ] **PROBE:** re-introduce a module-level `METRICS = Metrics()` and confirm a test catches it (add one if none does — a grep-based architecture test in `tests/architecture/` is acceptable and matches the project's existing style).

## Task 5: enable OTLP against live Langfuse, docs, container gate

- [ ] **Its own commit, with its own verification** (spec §7). Flip `config.toml`'s tracing exporter to the HTTP OTLP value, set `headers_env`, and put the credential in the gitignored `.env`.
- [ ] **Verify against the live instance**: Langfuse is at `:3100`, `/api/public/health` → 200, `/api/public/otel/v1/traces` → 401 without credentials. Confirm a trace actually lands — a 401 or a silent drop means the wiring is wrong, and `R-5`-style silence would hide it.
- [ ] `ARCHITECTURE.md`, `design/09-decision-index.md` (A5, A6, DEC-5, DEC-13), `CLAUDE.md`, `docs/runbook.md` — held to the project's accuracy standard: **every present-tense claim must be true of the tree as it stands**.
- [ ] **Container gate (controller runs):** `docker build --network=host -t nora:p6 .` → compose up → `/health` 200 with `sqlite.reachable: true` and the full `sse` key set; `/metrics` still served on 9100. Tag `nora:p6`.

---

## Self-Review

**Spec coverage:** §8 phase 6 is "Obs: structlog, prometheus-client, OTLP→Langfuse. `tests/conftest.py`'s autouse `METRICS.reset()` must change in the same commit. Rewrites `test_metrics_stub`, `test_metrics_export`, `test_logging`." Tasks 1–5 cover all of it, plus DEC-13's deferred singletons which spec §4.5 assigns to the refactor and Phase 3 explicitly parked here.

**Deliberately NOT here:** `/health`'s `mcp[]` array and 503-on-wedged-adapter (both depend on MCP providers, deferred by §11); Langfuse datasets and scores (phase 7); DEC-4's failure disclosure.

**Risk:** the highest of the remaining phases for the *running service*, because it changes what the operator sees. Two things protect it: metric names are asserted preserved, and `/health`'s key set is already pinned. The conftest trap is the one that turns a mistake into a total collection failure rather than a visible test failure — which is why Task 1 probes for it deliberately.
