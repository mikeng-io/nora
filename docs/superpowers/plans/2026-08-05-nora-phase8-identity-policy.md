---
kind: plan
title: "Nora Phase 8 — Identity, policy allowlists, and an operator control plane"
description: "Multi-id identity per platform, a two-allowlist policy.toml, the nora groups/persons CLI, and the read_buffer cross-group leak."
tags: [nora, plan, identity, policy, allowlist, control-plane, phase8]
timestamp: '2026-08-05'
edges:
  - {dst: ../../../design/10-identity-and-policy-schema.md, rel: implements, provenance: asserted}
  - {dst: 2026-08-05-nora-phase7-langfuse-evals.md, rel: follows, provenance: asserted}
---

# Nora Phase 8 — Identity, Policy and Control Plane

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Close a confirmed cross-group read leak; let one person hold many ids per platform so a hidden phone number still resolves; add a deny-by-default `policy.toml` with two allowlists; and give the operator a read-only CLI so identities and groups are discoverable without a Claude session.

**Schema is already settled** — implement `design/10-identity-and-policy-schema.md` exactly, including its §7 resolved decisions. Do not re-litigate it. Two validated preview files exist: `design/10-preview-identity.toml`, `design/10-preview-policy.toml`.

## Global Constraints

- **`uv add` only, never `pip install`.** Bare `uv sync` drops the evals extra; recover with `uv sync --frozen --group dev --extra evals`.
- **Never touch the signal-cli daemon.** `derive_account` reads; nothing writes. `listGroups` over `/api/v1/rpc` is a read and is permitted.
- **Metric names append-only.** `/health` JSON additive only; `sse` stays top-level with `last_event_age_seconds`, `account`, `platform` — pinned by `test_health_sse_key_set_is_pinned`.
- **Only `nora.app` and `nora/__main__.py` may import `nora.config`.** There are **no `ignore_imports` exceptions** and none may be added. `nora/domain/` is stdlib-only. `nora.infra`/`nora.testing`/`nora.ingest`/`nora.engagement` may import neither `nora.obs` nor `nora.config`. Only `nora.agent.**` may import `strands`.
- **Module size:** soft 200 / hard 300 for `nora/`, enforced by `tests/architecture/test_module_size.py`.
- **No module-level mutable singletons** — `tests/architecture/test_no_module_level_mutable_singletons.py` catches them.
- **`RULE_TESTS` maps rules to bare test names.** Renaming a named test silently drops a rule's coverage. Renaming is allowed only with the registry updated in the same commit.
- **Every gate must be OBSERVED FAILING against a deliberate probe before it is committed.**

**Per-task gate:**
```bash
uv run pytest -q
uv run ruff check nora/ evals/ tests/
uv run mypy nora/domain nora/config.py nora/app nora/infra
uv run lint-imports
```
**Baseline:** 719 passed / 1 deselected; ruff clean; mypy Success on **23 source files**; lint-imports **5 kept**. Container healthy on `nora:p7`.

---

## Task 1: close the `read_buffer` cross-group read leak

**This is a live security hole and goes first.** Verified by running it:

```
tools bound to GROUP-B; model asked for GROUP-A ->
    {'sender': 'Alice', 'body': 'Group A secret: the merger closes Friday', 'ts': 1}
```

`nora/agent/tools.py`'s `read_buffer` takes `conversation_id` as a **model-supplied argument**, which **shadows** the closure value `build_tools` was given, and passes it straight to `store.recent`. R-3 does not help: it guards the *send target*, and the send target is legitimately the tagged conversation. Nothing guards the *read source*.

Pre-existing — `git show cd71793:nora/agent/tools.py` has the identical shadowing — but real.

- [ ] Ignore the model's `conversation_id` and use the closure's bound value. **Keep the parameter in the signature** so the LLM-facing schema and its docstring are unchanged — those docstrings are the tool schema and rewording them is a prompt change with eval consequences.
- [ ] If the model supplies an id that differs from the bound conversation, **log it and count it**. A model probing another group is a signal worth seeing, and a silent correction is exactly the wrong-silence pattern. Use a new append-only metric name.
- [ ] Same review for `read_peer_card` — it hardcodes `"signal"` (a known Minor) and takes a model-supplied `sender_id`. Decide whether an arbitrary `sender_id` is a comparable leak and say so.
- [ ] **PROBE:** re-run the scenario above and confirm GROUP-A's row is no longer returned when tools are bound to GROUP-B. Paste before/after.

## Task 2: typed identifiers in the domain

**Files:** `nora/domain/models.py` (or a new `nora/domain/identity.py`), tests in `tests/unit/`.

- [ ] `Membership` enum: exactly `ADMIN` and `MEMBER`. Owner/creator/superadmin collapse into `ADMIN` — signal-cli cannot distinguish a creator from any other admin, so a three-value set would ship a value that silently never matches on Signal (§4.4). Telegram's `restricted`/`left`/`kicked` are *absence*, not a grade, and map to no value.
- [ ] Descriptive only: **policy does not grant on membership.** Carry it for the control plane.
- [ ] A typed identifier concept — `(platform, kind, value)` — with the fixed per-platform kind vocabulary from §3.1. `display_name` is **never matchable**; supplying one is an error, not an ignored key.
- [ ] `nora/domain/` is stdlib-only. Keep it that way.

## Task 3: multi-id `identity.toml` + the parser's missing `sourceUuid`

**Files:** `nora/persons.py`, `nora/platform/signal/parser.py`, `identity.example.toml`, `tests/test_persons.py`, `tests/test_signal_parser.py`.

**Why this matters concretely:** the operator hides his Signal phone number. `parser.py:35` reads `source or sourceNumber` and **never reads `sourceUuid`**, and `persons.py:55` allows **one id per platform**. Together: with a hidden number the operator cannot author a working mapping at all, because the only id Nora sees is one Signal never shows him.

- [ ] `platforms: dict[str, str | int]` becomes per-platform typed maps, matching `design/10-preview-identity.toml` exactly.
- [ ] Reverse index `(platform, id_value) -> person_id` built from **every** kind. Keep the duplicate-detection hard error (`persons.py:100`) — an ambiguous identity is worse than none.
- [ ] **Hard break** on the old shorthand (§7.3): `identity.toml` is gitignored, single-operator, one placeholder entry. Raise a readable error naming the new form rather than supporting two shapes forever.
- [ ] Parser: read `sourceUuid` as well, so a number-hidden sender resolves. Preserve every existing drop (DM, sync, self-send, empty body) — `tests/test_signal_parser.py` must keep passing.
- [ ] **PROBE:** a sender arriving only as a UUID resolves to the right person; remove the uuid kind from the index and confirm the test fails.

## Task 4: `policy.toml` — two allowlists, deny by default

**Files:** new `nora/policy.py` (loader) — note it may **not** import `nora.config`; `policy.example.toml`; wire through `nora/app/composition.py`.

Implement `design/10-preview-policy.toml` exactly.

- [ ] `default = "deny"`. **An empty or missing allowlist refuses to start**, with a readable `StartupError` naming the `listGroups` recipe. Not silent denial: under R-5 a silently-denied Nora is indistinguishable from a working one, and this codebase's own `.env` comment states the principle — *"You get a loud error instead of a silent mute."*
- [ ] `extra = "forbid"`, so a typo is a startup failure rather than a silently-open allowlist.
- [ ] `[dm].persons` is parsed and validated now but **DMs are still dropped** at `parser.py:33`. Warn at startup if it is non-empty, or it silently does nothing.
- [ ] `also_ids` per conversation (Telegram supergroup migration changes `chat.id`; a stale allowlist silently stops matching).
- [ ] `label` is never matched on.
- [ ] Grant predicate exactly as §4.3.

## Task 5: enforce policy on the ingest path

**Files:** `nora/ingest/` (a stage or the `EngagePolicy`), `nora/engagement/stages/admission.py`.

- [ ] Decide **where** the check belongs and justify it: the ingest gate (stage 5, `EngagePolicy`) or engagement admission. Consider that a denied conversation should arguably not even be **audited** — or should it? Audit-everything is what makes `nora persons pending` work at all (Task 6 depends on unmapped senders being in the DB). **State the decision and its consequence for the control plane.**
- [ ] A denied message must be **counted**, not silently dropped. New metric, append-only.
- [ ] R-1, R-5, R-9, R-10, R-12 unchanged. The R-5 matrix is the tripwire.
- [ ] **PROBE:** a message from an allowlisted group is admitted; the same message from an unlisted group is refused and counted; remove the check and confirm the test fails.

## Task 6: the operator control plane — `nora groups`, `nora persons pending`

**Files:** `nora/app/cli.py` (new), `nora/__main__.py` subcommands.

This is spec §11's deferred item, now requested: *"a `nora persons suggest` CLI emitting `identity.toml` stubs for operator confirmation. **Does not violate D6.**"* The file stays authoritative and hand-edited; Nora only suggests.

**It needs no new storage.** Audit runs at ingest stage 2, identity at stage 3, so **unmapped senders are already in the DB with the full envelope in `raw_json`.** Verified.

- [ ] `nora groups` — every group from signal-cli's `listGroups` (JSON-RPC `POST /api/v1/rpc`, read-only) with **name + canonical id + allowed/denied** per `policy.toml`. This is the answer to *"I can't tell which group id is which."*
- [ ] `nora persons pending` — unmapped senders from `messages.raw_json`: **every id form seen** (uuid, phone if visible, username) next to the display name the operator recognises, plus a paste-ready `identity.toml` stanza.
- [ ] **Read-only. Neither command writes any file**, and neither writes to the daemon.
- [ ] Must work with the container **stopped** — the operator's 3am path is `docker`, `curl`, an editor, and this CLI. Do not require a running Nora.
- [ ] Both are new `argparse` subcommands; `--config` must keep working unchanged.

## Task 7: Nora's own identity, and the silent degradation

**Files:** `nora/platform/signal/account.py`, `nora/domain/ports.py`.

`derive_aliases` reads `/api/v1/accounts`, which **404s on this daemon** — verified live. `aliases` degrades to `{primary}` on every start, so R-12's membership test collapses to equality and DEC-9's improvement is inert. It degrades with a log line and **no metric**.

- [ ] Use the same typed-identifier model as Task 2 so "Nora's ids" and "a person's ids" are one concept with one matching rule.
- [ ] **Count the degradation.** A silently-dead security feature is the pattern this project keeps shipping.
- [ ] Find the endpoint that actually works on this daemon, or record plainly in the code and the runbook that alias enrichment is unavailable here and why.
- [ ] **PROBE:** with enrichment unavailable, the counter increments and R-12 still refuses a self-tag by primary id.

## Task 8: docs, decision index, container gate

- [ ] `ARCHITECTURE.md`, `CLAUDE.md`, `docs/runbook.md` (a control-plane section), `design/09-decision-index.md`: D6's amendment by this schema, and a new DEC for the two-allowlist policy.
- [ ] **Accuracy standard:** every present-tense claim must be true of the tree as it stands. This project shipped eight-plus untrue doc claims across phases 2–7; where you say a test enforces something, open it and confirm.
- [ ] **Container gate (controller runs):** `docker build --network=host -t nora:p8 .` → compose up → `/health` 200 with the full `sse` key set. Tag `nora:p8`. **Note the container will now need a valid `policy.toml`** or it refuses to start — that is the intended behaviour, and the gate must prove the happy path works.

---

## Self-Review

**Ordering rationale:** Task 1 is a live leak and independent, so it goes first. Tasks 2–3 are prerequisites for 4–5 (policy references `person_id`, which requires identity to resolve at all). Task 6 depends on 3 and 4 for its output to be meaningful. Task 7 is independent and could move.

**Risk:** Task 5 is the highest — it adds a refusal path to ingest, and a mistake there makes Nora mute, which under R-5 looks identical to working. The R-5 matrix and a "denied is counted" metric are the tripwires. Task 4's refuse-to-start is deliberate for the same reason: loud beats mute.

**Deliberately NOT here:** Telegram/Discord/WhatsApp adapters (schema only, per the operator's deferral); DM ingest (`parser.py:33` still drops them); membership-based grants (§4.4); a web UI (would amend D6); the tracing-docstring fix (backlogged by the operator).
