# Nora — Architecture

This is the map, not the history. For the decisions behind it and their enforcing
tests, see `design/09-decision-index.md`. For the full rationale, see
`docs/superpowers/specs/2026-08-04-nora-architecture-design.md` (the current spec)
and `design/*.md` (the topology it amends). This file is pinned to reality by
`tests/test_architecture_doc.py`: it fails collection if a package under `nora/`
goes unmentioned here.

**Status as of Phase 2 (persistence):** `nora/domain/`, `nora/config.py`,
`nora/app/composition.py`, mechanically-enforced boundaries (mypy, import-linter),
and `nora/infra/` (`sql/` — SQLAlchemy Core + Alembic audit store; `honcho/` —
the semantic-store client) now exist, alongside `nora/testing/` (a port fake bound
by a shared contract suite). `nora/store/` is gone. The rest of the package layout
described in the spec's target state (`ingest/`, `engagement/`, a `platforms/`
registry, a `tools/` registry) has **not** been built yet — that is the pipelines,
platforms, and agent phases. This document describes the tree as it exists today,
with target-state notes called out where they differ.

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
  today's real cross-package edges (`agent` → `persona` → `obs` → `infra`;
  `platform` is independent of the other four) so the contract enforces
  domain's isolation without also relitigating those packages' existing,
  legitimate imports of each other.
- **Contract 3 — "only app and `__main__` read config."** `nora.agent`, `nora.obs`,
  `nora.persona`, `nora.platform` (plus, as of this task,
  `nora.domain`, `nora.handler`, `nora.models`, `nora.persons`, `nora.tag`, `nora.infra`) may not
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
| `config.py` | `Config` — the frozen pydantic tree, `load()`, `ConfigError`. The only reader of `config.toml`. | pydantic, stdlib. Importable only by `app/` and `nora/__main__.py` (contract 3) — plus the one documented exception below. |
| `app/` | `composition.py` — `Dependencies` (frozen dataclass) and `build_dependencies(cfg) -> Dependencies`, the seam later phases wire the real graph through. **Not yet load-bearing**: `nora/__main__.py` still builds its own graph directly. | `nora.config` (this is the one package allowed to), `nora.obs.metrics`, `nora.persons`, `nora.infra.honcho`, `nora.infra.sql`, `nora.domain.ports`. |
| `agent/` | `factory.py` (Strands `Agent`/model/client construction), `guards.py` (`LeakGuard`, `Deliverer`, `ReplyGuard`), `tools.py` (the three native tools), `prefill.py` (R-13 escaping, R-15 header). | The only package allowed to import `strands`. `factory.py` also imports `nora.config` directly — the one documented `ignore_imports` exception in contract 3 (see §3). |
| `platform/` | `base.py`, `signal/` (`adapter.py`, `listener.py`, `parser.py`, `send.py`, `account.py`) — the one `PlatformAdapter`-shaped implementation, Signal. | `nora.models` (the domain re-export shim), `aiohttp`. Forbidden from importing `nora.config` (contract 3). |
| `infra/` | `sql/` — `schema.py` (the `MetaData`/`Table` source of truth), `engine.py` (`create_sql_engine`, WAL/`busy_timeout`/`foreign_keys` on a connect hook), `audit.py` (`SqlAuditStore`), `migrations_runner.py` (programmatic Alembic: `run_migrations`, `current_revision`, `head_revision`, `stamp_head`, `alembic_config`), `migrations/` (Alembic env + `versions/0001_baseline.py`); `honcho/` — `client.py::HonchoClient`, best-effort semantic store; a separate package from `sql/` because the two ports' error contracts are opposite — see `infra/__init__.py`'s docstring. | `sqlalchemy`, `alembic`, `aiohttp`, `nora.domain.models`, `nora.domain.ports` (`honcho/client.py` — `MemoryHealth`, the typed return of `HonchoClient.health()`). May **NOT** import `nora.obs` — `nora.infra` sits below `nora.obs` in the layers contract (contract 2), which is why `SqlAuditStore` takes `metrics` by injection rather than importing `nora.obs.metrics`; see `infra/__init__.py` and `audit.py`'s `__init__` docstring. Forbidden from importing `nora.config` (contract 3). |
| `persona/` | `loader.py` — `PersonaStore`, the only module that may read a `.md` persona file (enforced by `test_boundaries.py::test_persona_read_boundary`). | stdlib, YAML, `nora.obs.metrics` (`loader.py:20` — reload-failure counter). Forbidden from importing `nora.config`. |
| `obs/` | `logging.py` (`setup_logging`), `tracing.py` (`setup_tracing`, OTel), `metrics.py` (`METRICS` singleton, `Metrics`), `health.py` (`build_health_app` — `GET /health`, `GET /metrics`; takes `store`/`honcho` as injected dependencies, not a module import — the `/health` endpoint calls `store.stats()` and `honcho.health()`). | `aiohttp`, `opentelemetry`. Forbidden from importing `nora.config` — but see §3, `logging.py`/`tracing.py` take config-shaped parameters instead. |
| `handler.py` | `Handler` — ambient ingest + tag orchestration in one 229-line class (spec's own evidence of POC shape; dissolves into `ingest/`+`engagement/` stages in the pipelines phase). Takes its `AuditStore` as an injected `store` constructor parameter, not an import. | Everything: `agent`, `obs`, `tag`, `models`. Takes the whole `Config` object as `cfg` — a known gap, see §3. |
| `models.py` | Re-export shim for `nora.domain.models` (`ConversationTarget`, `NormalizedMessage`, `PersonRef`, `TagContext`) — see module docstring. Kept so an earlier phase didn't have to rewrite ~20 import sites across `handler.py`, `platform/`, `agent/`, and the now-deleted `store/`. **The pipelines phase deletes it**, once those call sites are migrated to import `nora.domain.models` directly. | `nora.domain.models` only. |
| `persons.py` | `Persons`, `load_persons` — `identity.toml` → reverse index by `(platform, sender_id)`. | pydantic, `nora.models`. |
| `tag.py` | `is_tagged` (R-11 trigger validation), `Debounce` (R-9). | stdlib only. |
| `testing/` | `fakes.py` — `FakeAuditStore`, an in-memory `AuditStore` with `fail_on_nora`/`fail_on_recent` switches intended for the integration harness to inject audit failures with. **Not yet load-bearing**: the harness adopts it in the pipelines rewiring; `evals/` still imports `tests/` today, not this package. Bound by the shared contract suite in `tests/contract/test_audit_store_contract.py`, which both `FakeAuditStore` and `nora/infra/sql/audit.py::SqlAuditStore` must pass (CLAUDE.md's "a port never gains a second implementation without a shared contract suite"). | `nora.domain` only. Mechanically enforced: `nora.testing` shares `nora.infra`'s row in the layers contract and is listed in the config-forbidding contract, so it may import neither `nora.obs` nor `nora.config` — it ships inside the wheel, so a fake reaching into production config would ship too. |

## 3. Ports and their current implementations

Ports live in `nora/domain/ports.py` (framework-free, `Protocol`, keyword-only
after `self`). Conformance — where today's concrete classes do and do not satisfy
their port — is asserted by `tests/contract/test_port_conformance.py`.

| Port | Concrete implementation today | Known gap |
|---|---|---|
| `PlatformAdapter` | `nora/platform/signal/adapter.py::SignalAdapter` | **`SignalAdapter.send` returns a bare `bool`, not the port's `SendResult`.** The port and its rationale (Telegram's 429/`retry_after`, `Deliverer`'s attempt-counting latch — see `domain/ports.py`'s module docstring) are documented; nothing else in the repo records that the implementation hasn't caught up. `SignalAdapter` also has no `resolve_identity()` yet. Two more gaps: `SignalAdapter.health()` returns a plain `dict`, not the port's `AdapterHealth`; and `SignalAdapter.listen()` takes a positional `on_event: Callable[[dict], Awaitable[None]]` callback receiving a raw dict, not the port's keyword-only `on_message: Callable[[NormalizedMessage], Awaitable[None]]`. All four close in the **platforms phase** (spec §8 phase 4). Pinned explicitly by `test_signal_adapter_gap_to_platform_adapter_is_known`. |
| `MemoryStore` | `nora/infra/honcho/client.py::HonchoClient` | None: method names match (`test_honcho_client_satisfies_memory_store_method_names`) and `ensure_peer`/`ensure_session` are idempotent under concurrency — each takes a per-peer `asyncio.Lock` (`HonchoClient._peer_lock`), closing the check-then-act window the port's docstring declares (spec §6). |
| `AuditStore` | `nora/infra/sql/audit.py::SqlAuditStore`, over SQLAlchemy Core. | None: method names match the port (`test_both_implementations_satisfy_the_port`, `tests/contract/test_audit_store_contract.py`), and static conformance is checked by mypy (`nora.infra` is inside the strict override). `nora/testing/fakes.py::FakeAuditStore` is the port's second implementation, bound to the same contract suite. |
| `EngagePolicy` | Inline in `nora/handler.py::on_event` (`tag.is_tagged` + `tag.Debounce`), not a class. | No `TriggerTextPolicy` class yet. Closes in the pipelines phase (spec §4.3), which also adds the future `ClassifierPolicy` (D1's A3 amendment). |
| `ContextAssembler` | Inline in `nora/handler.py::_collect_context`. | No `TranscriptInjection` class yet; `EpisodeContinuation` is accepted-but-not-implemented (DEC-3). Closes in the pipelines phase. |

**Config-import offenders**: closed. `tests/test_composition.py::test_only_app_and_main_import_config`
is a real, positive (non-`xfail`) assertion — every module under `nora/` except
`nora/app/**` and `nora/__main__.py` must not import `nora.config`, with no
allowlist — and import-linter's contract 3 has no `ignore_imports` entries. The
former offenders: `nora/agent/factory.py` (moved onto typed collaborators,
pipelines phase) and `nora/handler.py` (deleted; the composition root now
builds the full graph) closed earlier in phase 3; `nora/obs/logging.py::setup_logging`
and `nora/obs/tracing.py::setup_tracing` (both took a duck-typed `cfg`
sub-config) closed by taking explicit scalar parameters instead. `nora/obs/health.py`
was never an offender — `build_health_app(...)` already took individually-extracted
fields (`store`, `adapter`, `honcho`, `persona_store`, `metrics`, `started_at`,
`llm_state`), not a `cfg`.

## 4. File-by-file walk

### One ambient message

1. `nora/platform/signal/listener.py::SignalListener._stream` — reads an SSE line, JSON-decodes it, calls `on_event(event)`.
2. `nora/handler.py::Handler.on_event` — the entry point; calls `self.adapter.parse(raw_event)`.
3. `nora/platform/signal/adapter.py::SignalAdapter.parse` → `nora/platform/signal/parser.py::parse_event` — builds a `nora/models.py` (→ `nora/domain/models.py`) `NormalizedMessage`, or drops the event (DM, sync message, self-sent, empty body — R-12 first pass).
4. `nora/handler.py::Handler.on_event` → `nora/infra/sql/audit.py::SqlAuditStore.append` — SQLite audit write. On failure: log + `nora_errors_total{stage="ambient"}` + drop, never re-raise into the listener.
5. `nora/persons.py::Persons.resolve` → if mapped, `nora/handler.py::Handler._feed_ambient` spawned as a background task → `nora/infra/honcho/client.py::HonchoClient.ensure_peer` + `.feed`.
6. `nora/tag.py::is_tagged` — if the trigger text is not present, `on_event` returns. Ambient-only messages stop here.

### One tag

7. `nora/tag.py::is_tagged` fires → `nora/tag.py::Debounce.allow` (R-9) → in-flight set check (R-10) → `Handler._spawn(self.handle_tag(msg))`.
8. `nora/handler.py::Handler._collect_context` — `nora/infra/sql/audit.py::SqlAuditStore.recent` (transcript) + `nora/infra/honcho/client.py::HonchoClient.peer_card` → a `TagContext` (`nora/domain/models.py`).
9. `nora/persona/loader.py::PersonaStore.get(applies_to="tag")` — hot-reloaded persona (mtime-checked).
10. `nora/agent/guards.py::Deliverer` and `nora/agent/guards.py::ReplyGuard` constructed fresh, one per tag.
11. `nora/agent/tools.py::build_tools` — the three native tools (`read_buffer`, `read_peer_card`, `send_reply`), closures over this tag's dependencies.
12. `nora/agent/prefill.py::build_prefill` / `build_tag_message` — R-13 (XML-escape all group-derived text into an untrusted block), R-15 (header states what context is already present).
13. `nora/agent/factory.py::create_client` / `create_model_provider` / `create_agent` / `invoke` — the Strands `Agent` loop runs, bounded by `asyncio.wait_for(..., timeout=...)` (R-10's invocation timeout).
14. A `send_reply` tool call → `nora/agent/guards.py::Deliverer.deliver` — audit-first: `SqlAuditStore.append(..., is_nora=True, delivery_status=DeliveryStatus.PENDING)`, then `adapter.send(...)` (today returns `bool` — see §3 gap), then `SqlAuditStore.set_delivery_status`.
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
| add a persisted column | `nora/infra/sql/schema.py` (the `MetaData`/`Table` source of truth) + `uv run alembic revision --autogenerate`. **Caveat:** a revision touching `idx_messages_conv_ts` must be hand-written — it is excluded from autogenerate on purpose (see DEC-11), so autogenerate's output for it cannot be trusted. |
| add a behavioural rule | `design/09-decision-index.md` + a named test registered in `tests/test_acceptance_rules.py::RULE_TESTS`. |
