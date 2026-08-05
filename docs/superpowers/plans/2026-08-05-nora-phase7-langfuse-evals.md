---
kind: plan
title: "Nora Phase 7 — Langfuse datasets and scores"
description: "DEC-6: strands-agents-evals stays the runner; Langfuse becomes the store. Push the eval corpora as datasets and each gate run as a dataset run with scores."
tags: [nora, plan, evals, langfuse, dec-6, phase7]
timestamp: '2026-08-05'
edges:
  - {dst: ../specs/2026-08-04-nora-architecture-design.md, rel: implements, provenance: asserted}
  - {dst: 2026-08-05-nora-phase6-observability.md, rel: follows, provenance: asserted}
---

# Nora Phase 7 — Langfuse Datasets and Scores

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** DEC-6 — `strands-agents-evals` produces results; **Langfuse stores and compares them** (scores, datasets, dataset runs). The eval corpora become Langfuse datasets, and each gate run becomes a dataset run carrying per-case scores.

**They are not competitors.** The runner already exists (`evals/run.py`, `evals/harness.py`, `evals/evaluators.py`, `evals/judge.py`). This phase adds a **sink**, and does not rewrite the runner.

**Tech Stack:** Langfuse 3.212.0 (live on `:3100`), aiohttp/httpx, pytest.

## Global Constraints

- **`uv add` only, never `pip install`.** Bare `uv sync` drops the evals extra; recover with `uv sync --frozen --group dev --extra evals`.
- **`evals/` may import `nora.app` and `nora.testing` and nothing else from `nora/`, and may not import `tests` at all.** Enforced by a 5th import-linter contract *and* an AST boundary test — both landed in Phase 3 and both must stay green.
- **Secrets by `*_env` naming, never inlined; no `${VAR}` interpolation** (DEC-8).
- **Metric names append-only; `/health` JSON additive only** (`sse` top-level with `last_event_age_seconds`, `account`, `platform`).
- Only `nora.app`/`__main__` may import `nora.config`; **no `ignore_imports` exceptions exist and none may be added**. `nora/domain/` is stdlib-only. Only `nora.agent.**` may import `strands`.
- **Module size:** soft 200 / hard 300 for `nora/`.
- **No module-level mutable singletons** — Phase 6 deleted the last ones and `tests/architecture/test_no_module_level_mutable_singletons.py` now catches new ones.
- **Every gate must be OBSERVED FAILING against a deliberate probe before it is committed.**

**Per-task gate:**
```bash
uv run pytest -q
uv run ruff check nora/ evals/ tests/
uv run mypy nora/domain nora/config.py nora/app nora/infra
uv run lint-imports
```
**Baseline (Phase 6 + tracing enablement):** 663 passed / 1 deselected; ruff clean; mypy Success on **23 source files**; lint-imports **5 kept**. Container healthy on `nora:p6`; **Langfuse tracing live and verified**.

---

## Verified environment facts (2026-08-05)

- Langfuse **3.212.0**, `GET /api/public/health` → 200, **1306 traces** already ingested.
- Credentials exist and work: `Authorization: Basic <base64(pk-lf-…:sk-lf-…)>`; `POST /api/public/otel/v1/traces` → **200** with them, **401** without. They live in the gitignored `.env` as `LANGFUSE_OTLP_HEADERS` and reach the container via `docker-compose.yml`.
- `GET /api/public/traces?limit=5` works with the same Basic header — the public API is reachable, so datasets and scores are too.
- **Zero datasets exist yet** (`listDatasets` → `totalItems: 0`). This phase creates the first.
- The corpora are five YAML files: `evals/cases/{trigger,no_trigger}.yaml` and `evals/adversarial/{injections,near_miss,transcript_injection}.yaml`.

**The credential is currently exposed to the app only as an OTLP *header* string.** The dataset/score API wants the same Basic auth. Decide deliberately whether to reuse `LANGFUSE_OTLP_HEADERS`, or add a separate `*_env` for the API — and say which and why. Reusing an OTLP-shaped, URL-encoded value for a plain API call is a trap: `Authorization=Basic%20…` is **not** a valid header value on its own.

---

## Task 1: `EvalSink` port and a Langfuse implementation

**Files:** `evals/sink.py` (new), `tests/test_evals_sink.py`.

- [ ] A small `EvalSink` Protocol: `upsert_dataset`, `upsert_item`, `start_run`, `record_score`. Keep it **narrow** — this is a sink, not a Langfuse SDK wrapper.
- [ ] A `LangfuseEvalSink` implementing it over the public API, plus a **`NullEvalSink`** that does nothing.
- [ ] **`NullEvalSink` is the default.** Evals must run with no Langfuse configured — the gate is `uv run pytest`-adjacent and CI must not require a network service. A missing credential degrades to "no export", never to a failure.
- [ ] **Two implementations of one port means a shared contract suite** (`tests/contract/`) that both pass — the project rule, enforced since Phase 2.
- [ ] Config: `[evals.langfuse]` with `enabled`, `host`, and an `*_env` naming the credential. `extra="forbid"` means every new key must reach `config.example.toml`.
- [ ] **PROBE:** make `NullEvalSink` raise instead of no-op, and confirm an evals test fails — proving the default path is actually exercised rather than skipped.

## Task 2: corpora → Langfuse datasets

**Files:** `evals/datasets.py` (new), `tests/test_evals_datasets.py`.

- [ ] Map each YAML corpus to one dataset: `nora-trigger`, `nora-no-trigger`, `nora-adversarial`. One dataset **item** per case, `input` = the case, `expectedOutput` = whatever the corpus asserts.
- [ ] **Item IDs are unique per project across all datasets** (Langfuse's own constraint) — derive stable, collision-proof ids, e.g. `<corpus>/<case-name>`. A collision silently overwrites another dataset's item.
- [ ] Idempotent: re-running upserts rather than duplicating.
- [ ] **R-14's adversarial corpus is load-bearing.** `test_r14_is_covered_by_the_adversarial_corpus` pins those YAML files as present, and `RULE_TESTS` maps R-14 to it. Do not move or rename the corpus files.
- [ ] **PROBE:** give two cases the same derived id and confirm a test catches the collision.

## Task 3: gate runs → dataset runs + scores

**Files:** `evals/run.py`, `tests/test_evals_gate.py`.

- [ ] `python -m evals.run --suite …` gains an opt-in export: each run creates a Langfuse **dataset run**, and each case contributes a **score**.
- [ ] Scores must be typed correctly — Langfuse wants a JSON **number** for `NUMERIC`/`BOOLEAN` (the string `"0.92"` is invalid) and a **string** for `CATEGORICAL`/`TEXT`. Choose per evaluator and say why.
- [ ] Carry the existing evaluators' verdicts through unchanged: `ExactlyOnceSendReply`, `NoSendWithoutTrigger`, `ToolBudget`, `NoRedundantRead`, `HonchoFedOncePerReply`. **This phase adds a sink; it does not change what the evaluators decide.**
- [ ] The **A8 silent-failure SLO** (`tags with no reply / total tags`, target <1%) is the observability escape hatch for R-5. If a score can express it, wire it; if not, say so.
- [ ] **PROBE:** point the sink at an unreachable Langfuse and confirm the eval gate still returns its verdict — the store must never decide whether the gate passes.

## Task 4: live verification, docs, container gate

- [ ] **Actually push to the live instance and verify.** Zero datasets exist today; after this task `listDatasets` must show the three, with items, and at least one dataset run carrying scores. **Read them back** — do not assume a 200 means stored (Langfuse score reads are *eventually consistent*; retry briefly before concluding failure).
- [ ] `design/09-decision-index.md`: DEC-6's "Langfuse wiring not built" becomes the real tests. A8's "metric only; no assertion test" row updates if a score now expresses it.
- [ ] `ARCHITECTURE.md`, `CLAUDE.md`, `docs/runbook.md` — **accuracy standard**: every present-tense claim must be true of the tree as it stands. Add a runbook section on reading eval results in Langfuse.
- [ ] **Container gate (controller runs):** `docker build --network=host -t nora:p7 .` → compose up → `/health` 200 with the full `sse` key set. Tag `nora:p7`.

---

## Self-Review

**Spec coverage:** §8 phase 7 is "Langfuse datasets + scores (DEC-6). Risk: low." DEC-6 says `strands-agents-evals` is the runner and Langfuse the store for results, datasets and dataset runs. Tasks 1–4 cover it.

**Deliberately NOT here:** MCP providers, the `deep` tier, Episodes, DEC-4's failure disclosure, `/health`'s `mcp[]` array — all deferred by spec §11 and untouched by this phase.

**Risk:** genuinely low, with one sharp edge — **the eval gate's verdict must never depend on Langfuse being reachable**. A store that can fail the gate turns an observability nicety into a CI outage. Task 3's probe exists for exactly that, and `NullEvalSink` being the default is the structural guarantee.
