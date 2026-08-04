# Nora — Architecture

This is the map, not the history. For the decisions behind it and their enforcing
tests, see `design/09-decision-index.md`. For the full rationale, see
`docs/superpowers/specs/2026-08-04-nora-architecture-design.md` (the current spec)
and `design/*.md` (the topology it amends). This file is pinned to reality by
`tests/test_architecture_doc.py`: it fails collection if a package under `nora/`
goes unmentioned here.

**Status as of Phase 1b:** `nora/domain/`, `nora/config.py`, `nora/app/composition.py`
and mechanically-enforced boundaries (mypy, import-linter) exist. The rest of the
package layout described in the spec's target state (`ingest/`, `engagement/`,
`infra/`, a `platforms/` registry, a `tools/` registry) has **not** been built yet —
that is the pipelines, persistence, platforms, and agent phases. This document
describes the tree as it exists today, with target-state notes called out where
they differ.

## 1. Topology: two paths, one boundary

```
Signal group
   │  SSE push (every message)
   ▼
① AMBIENT PATH (always, no Agent SDK)
   event → parser → NormalizedMessage → SQLite audit + Honcho feed (mapped senders only)
   │
   └─ trigger text present? ── no ──▶ done, ambient only
                              └─ yes ──▶
② ENGAGEMENT / TAGGING PATH (trigger only, via the Strands Agents SDK)
   collect context (recent buffer rows, tagger's Honcho peer card, persona)
   → Agent(messages=[pre-filled context]) → agent.invoke_async(tag_message)
   → tools: read_buffer / read_peer_card / send_reply
   → reply → audit row (delivery_status=pending → sent/failed) → Honcho feed
```

**D1 — the boundary rule.** The Agent SDK never sees ambient-only traffic. It is
constructed, invoked, and discarded once per tag. Ambient ingest is a pure,
deterministic write path (SQLite audit + Honcho feed) with exactly one branch:
trigger detection. See `design/00-overview.md` D1 and `design/09-decision-index.md`.

## 2. Packages — what lives here, what it may import

Enforced today by four import-linter contracts (`pyproject.toml`
`[tool.importlinter]`) plus `tests/test_boundaries.py`:

- **Contract 1 — "domain is framework-free."** `nora.domain` may not import
  `aiohttp`, `strands`, `sqlalchemy`, `pydantic`, `prometheus_client`,
  `opentelemetry`, `yaml`, or `openai`. A deny-list: it catches a hardcoded
  set of third-party names, nothing else — see the next bullet for what closes
  the gap that leaves.
- **Contract 2 — "layered architecture."** A `layers` contract with `nora.domain`
  as the bottom layer: `nora.domain` may import nothing else in `nora`, full
  stop — not a list of banned names, an allow-list of one (the standard
  library). Backstopped by `tests/test_boundaries.py::test_domain_imports_only_the_standard_library`,
  which asserts the same property at the file level (an allow-list, not the
  deny-list contract 1 alone used to be). The middle layer is ordered to match
  today's real cross-package edges (`agent` → `persona` → `obs` → `store`;
  `platform` is independent of the other four) so the contract enforces
  domain's isolation without also relitigating those packages' existing,
  legitimate imports of each other.
- **Contract 3 — "only app and `__main__` read config."** `nora.agent`, `nora.obs`,
  `nora.persona`, `nora.platform`, `nora.store` (plus, as of this task,
  `nora.domain`, `nora.handler`, `nora.models`, `nora.persons`, `nora.tag`) may not
  import `nora.config`. One documented exception: `nora.agent.factory` (see §3
  known gaps).
- **Contract 4 — "the nora package's own `__init__` does not read config either."**
  Same rule as contract 3, applied to `nora/__init__.py` itself, as a separate
  `forbidden` contract with `as_packages = false`. It has to be separate: listing
  the root package `nora` as a source module in contract 3 would be a silent
  no-op there, because import-linter's `forbidden` contract skips any
  source/forbidden pair where one module is a subpackage of the other — and
  `nora.config` is a subpackage of `nora`. `as_packages = false` compares both
  sides as single literal modules instead, which is what actually catches it.
- `tests/test_boundaries.py::test_strands_import_boundary` — only `nora/agent/**`
  may import `strands`.

| Package / module | What lives here today | What it may import |
|---|---|---|
| `domain/` | Framework-free contracts: `models.py` (platform-neutral data), `ports.py` (Protocol seams — `PlatformAdapter`, `AuditStore`, `MemoryStore`, `EngagePolicy`, `ContextAssembler`), `errors.py` (`FailureClass`, `NoraError`, DEC-4). | Standard library only, plus its own sibling `domain.models`. Nothing else in `nora/`. |
| `config.py` | `Config` — the frozen pydantic tree, `load()`, `ConfigError`. The only reader of `config.toml`. | pydantic, stdlib. Importable only by `app/` and `nora/__main__.py` (contract 2) — plus the one documented exception below. |
| `app/` | `composition.py` — `Dependencies` (frozen dataclass) and `build_dependencies(cfg) -> Dependencies`, the seam later phases wire the real graph through. **Not yet load-bearing**: `nora/__main__.py` still builds its own graph directly. | `nora.config` (this is the one package allowed to), `nora.obs.metrics`, `nora.persons`, `nora.store.honcho`. |
| `agent/` | `factory.py` (Strands `Agent`/model/client construction), `guards.py` (`LeakGuard`, `Deliverer`, `ReplyGuard`), `tools.py` (the three native tools), `prefill.py` (R-13 escaping, R-15 header). | The only package allowed to import `strands`. `factory.py` also imports `nora.config` directly — the one documented `ignore_imports` exception in contract 2 (see §3). |
| `platform/` | `base.py`, `signal/` (`adapter.py`, `listener.py`, `parser.py`, `send.py`, `account.py`) — the one `PlatformAdapter`-shaped implementation, Signal. | `nora.models` (the domain re-export shim), `aiohttp`. Forbidden from importing `nora.config` (contract 2). |
| `store/` | `db.py` (SQLite audit — module-level functions, `db_path` threaded through each call), `honcho.py` (`HonchoClient`, best-effort), `migrate.py` (legacy `PRAGMA user_version` migration, spec §4.5 marks this for retirement in the persistence phase). | `aiosqlite` / `aiohttp`, `nora.models`, `nora.domain.ports` (`honcho.py` — `MemoryHealth`, the typed return of `HonchoClient.health()`). Forbidden from importing `nora.config`. |
| `persona/` | `loader.py` — `PersonaStore`, the only module that may read a `.md` persona file (enforced by `test_boundaries.py::test_persona_read_boundary`). | stdlib, YAML, `nora.obs.metrics` (`loader.py:20` — reload-failure counter). Forbidden from importing `nora.config`. |
| `obs/` | `logging.py` (`setup_logging`), `tracing.py` (`setup_tracing`, OTel), `metrics.py` (`METRICS` singleton, `Metrics`), `health.py` (`build_health_app` — `GET /health`, `GET /metrics`). | `aiohttp`, `opentelemetry`, `nora.store.db` (`health.py:10` — the `/health` endpoint reads audit-store stats directly). Forbidden from importing `nora.config` — but see §3, `logging.py`/`tracing.py` take config-shaped parameters instead. |
| `handler.py` | `Handler` — ambient ingest + tag orchestration in one 229-line class (spec's own evidence of POC shape; dissolves into `ingest/`+`engagement/` stages in the pipelines phase). | Everything: `agent`, `obs`, `store`, `tag`, `models`. Takes the whole `Config` object as `cfg` — a known gap, see §3. |
| `models.py` | Re-export shim for `nora.domain.models` (`ConversationTarget`, `NormalizedMessage`, `PersonRef`, `TagContext`) — see module docstring. Kept so this phase didn't have to rewrite ~20 import sites across `handler.py`, `platform/`, `store/`, `agent/`. **The pipelines phase deletes it**, once those call sites are migrated to import `nora.domain.models` directly. | `nora.domain.models` only. |
| `persons.py` | `Persons`, `load_persons` — `identity.toml` → reverse index by `(platform, sender_id)`. | pydantic, `nora.models`. |
| `tag.py` | `is_tagged` (R-11 trigger validation), `Debounce` (R-9). | stdlib only. |

## 3. Ports and their current implementations

Ports live in `nora/domain/ports.py` (framework-free, `Protocol`, keyword-only
after `self`). Conformance — where today's concrete classes do and do not satisfy
their port — is asserted by `tests/contract/test_port_conformance.py`.

| Port | Concrete implementation today | Known gap |
|---|---|---|
| `PlatformAdapter` | `nora/platform/signal/adapter.py::SignalAdapter` | **`SignalAdapter.send` returns a bare `bool`, not the port's `SendResult`.** The port and its rationale (Telegram's 429/`retry_after`, `Deliverer`'s attempt-counting latch — see `domain/ports.py`'s module docstring) are documented; nothing else in the repo records that the implementation hasn't caught up. `SignalAdapter` also has no `resolve_identity()` yet. Two more gaps: `SignalAdapter.health()` returns a plain `dict`, not the port's `AdapterHealth`; and `SignalAdapter.listen()` takes a positional `on_event: Callable[[dict], Awaitable[None]]` callback receiving a raw dict, not the port's keyword-only `on_message: Callable[[NormalizedMessage], Awaitable[None]]`. All four close in the **platforms phase** (spec §8 phase 4). Pinned explicitly by `test_signal_adapter_gap_to_platform_adapter_is_known`. |
| `MemoryStore` | `nora/store/honcho.py::HonchoClient` | Method names match (`test_honcho_client_satisfies_memory_store_method_names`); `ensure_*` is documented as needing to be idempotent under concurrency (a per-peer lock) but is still check-then-act today — closes in the persistence phase (DEC-11/§6). |
| `AuditStore` | `nora/store/db.py` — module-level functions, not a class; `db_path` threaded as a de-facto connection handle rather than an injected object. | Not yet an object satisfying the Protocol at all. Closes in the persistence phase, alongside the SQLAlchemy Core + Alembic migration (DEC-7/DEC-11). |
| `EngagePolicy` | Inline in `nora/handler.py::on_event` (`tag.is_tagged` + `tag.Debounce`), not a class. | No `TriggerTextPolicy` class yet. Closes in the pipelines phase (spec §4.3), which also adds the future `ClassifierPolicy` (D1's A3 amendment). |
| `ContextAssembler` | Inline in `nora/handler.py::_collect_context`. | No `TranscriptInjection` class yet; `EpisodeContinuation` is accepted-but-not-implemented (DEC-3). Closes in the pipelines phase. |

**Config-import offenders** (contract 2's target, currently a documented gap —
`tests/test_composition.py::test_only_app_and_main_import_config` is `xfail(strict=True)`
pinning exactly this list):

- `nora/agent/factory.py` — literal `from ..config import LimitsConfig, LLMConfig, LLMTierConfig`. The one `ignore_imports` exception in import-linter's contract 2.
- `nora/handler.py` — `Handler.__init__` takes the *whole* `Config` object as `cfg` and reaches deep into it.
- `nora/obs/logging.py` — `setup_logging(cfg, ...)` takes a duck-typed `LoggingConfig`-shaped `cfg`.
- `nora/obs/tracing.py` — `setup_tracing(cfg, ...)` takes a duck-typed `TracingConfig`-shaped `cfg`.
- `nora/obs/health.py` is **not** an offender — `build_health_app(...)` already takes individually-extracted fields (`db_path`, `adapter`, `honcho`, `persona_store`, `metrics`, `started_at`, `llm_state`), not a `cfg`.

All four offenders close in the **pipelines phase**: `factory.py` moves onto typed
collaborators, and `Handler`/`setup_logging`/`setup_tracing` take a
`Dependencies`-shaped or narrowly-typed parameter instead of `cfg`.

## 4. File-by-file walk

### One ambient message

1. `nora/platform/signal/listener.py::SignalListener._stream` — reads an SSE line, JSON-decodes it, calls `on_event(event)`.
2. `nora/handler.py::Handler.on_event` — the entry point; calls `self.adapter.parse(raw_event)`.
3. `nora/platform/signal/adapter.py::SignalAdapter.parse` → `nora/platform/signal/parser.py::parse_event` — builds a `nora/models.py` (→ `nora/domain/models.py`) `NormalizedMessage`, or drops the event (DM, sync message, self-sent, empty body — R-12 first pass).
4. `nora/handler.py::Handler.on_event` → `nora/store/db.py::append` — SQLite audit write. On failure: log + `nora_errors_total{stage="ambient"}` + drop, never re-raise into the listener.
5. `nora/persons.py::Persons.resolve` → if mapped, `nora/handler.py::Handler._feed_ambient` spawned as a background task → `nora/store/honcho.py::HonchoClient.ensure_peer` + `.feed`.
6. `nora/tag.py::is_tagged` — if the trigger text is not present, `on_event` returns. Ambient-only messages stop here.

### One tag

7. `nora/tag.py::is_tagged` fires → `nora/tag.py::Debounce.allow` (R-9) → in-flight set check (R-10) → `Handler._spawn(self.handle_tag(msg))`.
8. `nora/handler.py::Handler._collect_context` — `nora/store/db.py::recent` (transcript) + `nora/store/honcho.py::HonchoClient.peer_card` → a `TagContext` (`nora/domain/models.py`).
9. `nora/persona/loader.py::PersonaStore.get(applies_to="tag")` — hot-reloaded persona (mtime-checked).
10. `nora/agent/guards.py::Deliverer` and `nora/agent/guards.py::ReplyGuard` constructed fresh, one per tag.
11. `nora/agent/tools.py::build_tools` — the three native tools (`read_buffer`, `read_peer_card`, `send_reply`), closures over this tag's dependencies.
12. `nora/agent/prefill.py::build_prefill` / `build_tag_message` — R-13 (XML-escape all group-derived text into an untrusted block), R-15 (header states what context is already present).
13. `nora/agent/factory.py::create_client` / `create_model_provider` / `create_agent` / `invoke` — the Strands `Agent` loop runs, bounded by `asyncio.wait_for(..., timeout=...)` (R-10's invocation timeout).
14. A `send_reply` tool call → `nora/agent/guards.py::Deliverer.deliver` — audit-first: `db.append(..., is_nora=1, delivery_status="pending")`, then `adapter.send(...)` (today returns `bool` — see §3 gap), then `db.set_delivery_status`.
15. `nora/handler.py::Handler._feed_exchange` — spawned as a background task if a tagger was mapped, one exchange fed to Honcho.

## 5. Where new code goes

From spec §10. Rows marked **(target)** name a package that does not exist yet
in this repo — they describe where the pipelines/agent/platforms phases will put
that kind of code, not a path you can use today.

| I want to… | Touch only |
|---|---|
| add a platform | **(target)** `platforms/<name>/` + one `[platforms.<name>]` config block. Today: extend `platform/` following `platform/signal/`'s shape. |
| add a native tool | **(target)** `agent/tools/native/<name>.py` + a registry entry. Today: `nora/agent/tools.py::build_tools`. |
| add an MCP server | **(target)** `[tools.mcp.<name>]` — no Python. Not built yet (DEC-2). |
| change engagement decisions | **(target)** `ingest/policy.py`. Today: `nora/tag.py` + `nora/handler.py::Handler.on_event`. |
| add a persisted column | **(target)** `infra/sql/schema.py` + `alembic revision --autogenerate`. Today: `nora/store/db.py::SCHEMA`. |
| add a behavioural rule | `design/09-decision-index.md` + a named test registered in `tests/test_acceptance_rules.py::RULE_TESTS`. |
