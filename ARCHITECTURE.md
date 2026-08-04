# Nora — Architecture

This is the map, not the history. For the decisions behind it and their enforcing
tests, see `design/09-decision-index.md`. For the full rationale, see
`docs/superpowers/specs/2026-08-04-nora-architecture-design.md` (the current spec)
and `design/*.md` (the topology it amends). This file is pinned to reality by
`tests/test_architecture_doc.py`: it fails collection if a package under `nora/`
goes unmentioned here.

**Status as of Phase 3 (pipelines):** `nora/ingest/` (five ambient stages —
parse, audit, identity, memory_feed, gate) and `nora/engagement/` (admission,
context, invoke, deliver, feedback) now exist, glued together by
`nora/app/dispatch.py::Dispatcher` and built by
`nora/app/composition.py::build_dependencies`. `nora/handler.py` and
`nora/models.py` are **deleted** — there is no re-export shim; every module
imports `nora.domain.models` directly. `nora/app/runtime.py`
(`BackgroundTasks`, the prune sweep, the debounce sweep, per-adapter
supervision) and `nora/app/offline.py` (the pipeline-wiring helper shared by
the integration test harness and `evals/`) landed this phase too, alongside
`nora/app/eval_support.py` (the facade `evals/` now reads config/agent/persona/tag
pieces through, in place of the direct imports it used to make). `nora/testing/`
(port fakes bound by shared contract suites) and `nora/infra/` (`sql/` —
SQLAlchemy Core + Alembic audit store; `honcho/` — the semantic-store client),
both built in Phase 2, are unchanged. The rest of the package layout described
in the spec's target state (a `platforms/` registry, a `tools/` registry) has
**not** been built yet — that is the platforms and agent-tooling phases. This
document describes the tree as it exists today, with target-state notes called
out where they differ.

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

Enforced today by five import-linter contracts (`pyproject.toml`
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
  `nora.persona`, `nora.platform`, `nora.domain`, `nora.persons`, `nora.tag`,
  `nora.infra`, `nora.testing`, `nora.ingest`, `nora.engagement` may not import
  `nora.config`. No exceptions — `pyproject.toml`'s contract has no
  `ignore_imports` entries. `nora.agent.factory` (the one former offender, which
  used to read a duck-typed `cfg` sub-config) now takes typed scalar
  collaborators through `__init__` instead; `tests/test_composition.py::
  test_only_app_and_main_import_config` asserts the same rule at the AST level,
  with no allowlist, and is a real (non-`xfail`) assertion.
- **Contract 4 — "the nora package's own `__init__` does not read config either."**
  Same rule as contract 3, applied to `nora/__init__.py` itself, as a separate
  `forbidden` contract with `as_packages = false`. It has to be separate: listing
  the root package `nora` as a source module in contract 3 would be a silent
  no-op there, because import-linter's `forbidden` contract skips any
  source/forbidden pair where one module is a subpackage of the other — and
  `nora.config` is a subpackage of `nora`. `as_packages = false` compares both
  sides as single literal modules instead, which is what actually catches it.
- **Contract 5 — "evals may only reach `nora.app` and `nora.testing`."** A
  `forbidden` contract (`allow_indirect_imports = "True"`, load-bearing — see
  the comment above it in `pyproject.toml`) listing every other `nora`
  subpackage, plus `tests`, as forbidden for `evals`' own direct imports.
  Closes the `evals/` → `tests/` coupling Task 10 removed
  (`evals/harness.py` no longer imports `tests.integration.harness`).
  Backstopped by `tests/test_boundaries.py::test_evals_only_imports_nora_app_and_nora_testing`
  and `test_evals_never_imports_tests`.
- `tests/test_boundaries.py::test_strands_import_boundary` — only `nora/agent/**`
  may import `strands`.

| Package / module | What lives here today | What it may import |
|---|---|---|
| `domain/` | Framework-free contracts: `models.py` (platform-neutral data), `ports.py` (Protocol seams — `PlatformAdapter`, `AuditStore`, `MemoryStore`, `EngagePolicy`, `ContextAssembler`), `errors.py` (`FailureClass`, `NoraError`, DEC-4). | Standard library only, plus its own sibling `domain.models`. Nothing else in `nora/`. |
| `config.py` | `Config` — the frozen pydantic tree, `load()`, `ConfigError`. The only reader of `config.toml`. | pydantic, stdlib. Importable only by `app/` and `nora/__main__.py` (contract 3) — no exceptions. |
| `app/` | The composition root and everything that used to live inside `Handler`/`App`, factored into single-purpose modules: `composition.py` — `Dependencies` (frozen dataclass) and `build_dependencies(cfg) -> Dependencies`, the only function allowed to know `Config`'s shape besides `nora.__main__` itself; owns the ordered, fatal startup validation (`StartupError`) and builds the full graph — both pipelines, the `Dispatcher`, `BackgroundTasks`, `Debounce`. `dispatch.py` — `Dispatcher.on_event`, the glue between `IngestPipeline` and `EngagementPipeline` that `Handler.on_event` used to be. `runtime.py` — `BackgroundTasks` (the strong-reference task-holder `Handler._spawn` used), `prune_loop`, `debounce_sweep_loop`, `supervise_adapter`/`start_adapters` (per-adapter restart-with-backoff). `offline.py` — `build_offline_pipeline`, the one definition of "wire ingest+engagement+dispatcher from already-built collaborators" that `tests/integration/harness.py` and `evals/harness.py` both call instead of each keeping its own copy. `eval_support.py` — the facade `evals/` reads config/agent/persona/tag pieces through (see the `evals` import-linter contract below). `agent_runner.py` — `LiveAgentRunner`, the production `nora.engagement.stages.invoke.AgentRunner`, wrapping `nora.agent.factory`'s Strands plumbing so `nora.engagement` never imports `strands` itself. | `nora.config` (this is one of the two packages allowed to), plus whatever collaborator packages each module needs: `nora.agent`, `nora.domain`, `nora.engagement`, `nora.infra.honcho`, `nora.infra.sql`, `nora.ingest`, `nora.obs.metrics`, `nora.obs.health`, `nora.persona`, `nora.persons`, `nora.platform.signal`, `nora.tag`. |
| `agent/` | `factory.py` (Strands `Agent`/model/client construction), `guards.py` (`LeakGuard`, `Deliverer`, `ReplyGuard`), `tools.py` (the three native tools), `prefill.py` (R-13 escaping, R-15 header). | The only package allowed to import `strands`. Forbidden from importing `nora.config` (contract 3) — `factory.py` was the one former offender (it used to read a duck-typed `cfg` sub-config); it now takes typed scalar parameters instead. |
| `platform/` | `base.py`, `signal/` (`adapter.py`, `listener.py`, `parser.py`, `send.py`, `account.py`) — the one `PlatformAdapter`-shaped implementation, Signal. | `nora.domain.models`, `aiohttp`. Forbidden from importing `nora.config` (contract 3). |
| `infra/` | `sql/` — `schema.py` (the `MetaData`/`Table` source of truth), `engine.py` (`create_sql_engine`, WAL/`busy_timeout`/`foreign_keys` on a connect hook), `audit.py` (`SqlAuditStore`), `migrations_runner.py` (programmatic Alembic: `run_migrations`, `current_revision`, `head_revision`, `stamp_head`, `alembic_config`), `migrations/` (Alembic env + `versions/0001_baseline.py`); `honcho/` — `client.py::HonchoClient`, best-effort semantic store; a separate package from `sql/` because the two ports' error contracts are opposite — see `infra/__init__.py`'s docstring. | `sqlalchemy`, `alembic`, `aiohttp`, `nora.domain.models`, `nora.domain.ports` (`honcho/client.py` — `MemoryHealth`, the typed return of `HonchoClient.health()`). May **NOT** import `nora.obs` — `nora.infra` sits below `nora.obs` in the layers contract (contract 2), which is why `SqlAuditStore` takes `metrics` by injection rather than importing `nora.obs.metrics`; see `infra/__init__.py` and `audit.py`'s `__init__` docstring. Forbidden from importing `nora.config` (contract 3). |
| `persona/` | `loader.py` — `PersonaStore`, the only module that may read a `.md` persona file (enforced by `test_boundaries.py::test_persona_read_boundary`). | stdlib, YAML, `nora.obs.metrics` (`loader.py:20` — reload-failure counter). Forbidden from importing `nora.config`. |
| `obs/` | `logging.py` (`setup_logging`), `tracing.py` (`setup_tracing`, OTel), `metrics.py` (`METRICS` singleton, `Metrics`), `health.py` (`build_health_app` — `GET /health`, `GET /metrics`; takes `store`/`honcho` as injected dependencies, not a module import — the `/health` endpoint calls `store.stats()` and `honcho.health()`). | `aiohttp`, `opentelemetry`. Forbidden from importing `nora.config` — `logging.py::setup_logging`/`tracing.py::setup_tracing` take explicit scalar parameters instead of a duck-typed `cfg` sub-config; `health.py::build_health_app` was never an offender, it already took individually-extracted fields. |
| `ingest/` | The ambient path (spec §4.3): `pipeline.py::IngestPipeline` sequences five stages — `stages/parse.py`, `stages/audit.py`, `stages/identity.py`, `stages/memory_feed.py`, `stages/gate.py` — each with exactly one public `async def run(...)`. `policy.py::TriggerTextPolicy` is the `EngagePolicy` this phase ships (see the ports table below); it lives here, not in `engagement/`, because the gate it satisfies runs on the ingest pipeline. Replaces the ambient half of `Handler.on_event`. | `nora.domain`, `nora.tag`, `nora.persons`, `nora.obs.metrics`. Forbidden from importing `nora.config` or `strands`. |
| `engagement/` | The tag-response path (spec §4.4): `pipeline.py::EngagementPipeline` sequences `stages/admission.py` (R-9 debounce, R-10 in-flight lock, R-12 self-tag), `context.py::TranscriptInjection` (the `ContextAssembler` this phase ships — see the ports table below), persona lookup, `stages/deliver.py` (`build`/`check_outcome`, wrapping `nora.agent.guards.Deliverer`/`ReplyGuard`), `stages/invoke.py` (one bounded LLM call, R-5/R-8), `stages/feedback.py` (the post-reply Honcho feed). Replaces the tagging half of `Handler` (`_collect_context`, `_invoke`, `_feed_exchange`, and the debounce/in-flight/self-tag checks from `on_event`). | `nora.domain`, `nora.persons`, `nora.tag`, `nora.obs.metrics`. Forbidden from importing `nora.config` or `strands` — `Deliverer`/`ReplyGuard` stay in `nora.agent` (they are `strands.HookProvider`s) rather than moving here, for exactly that reason. |
| `persons.py` | `Persons`, `load_persons` — `identity.toml` → reverse index by `(platform, sender_id)`. | pydantic, `nora.domain.models`. |
| `tag.py` | `is_tagged` (R-11 trigger validation), `Debounce` (R-9, plus `sweep` for `nora.app.runtime.debounce_sweep_loop`'s TTL cleanup). | stdlib only. |
| `testing/` | `fakes.py` — `FakeAuditStore` (`AuditStore`'s second implementation, `fail_on_nora`/`fail_on_recent` switches), `FakeMemoryStore` (`MemoryStore`'s second implementation, a `fail` switch), `FakeAdapter` (mirrors `SignalAdapter`'s current not-yet-`PlatformAdapter`-conforming shape, so it is exempt from the contract-suite rule for the same reason `SignalAdapter` itself is). `scripted_agent.py` — `ScriptedTurn`/`FakeAgentRunner`/`group_event`, the scripted model double shared by `tests/` and `evals/`, satisfying `nora.engagement.stages.invoke.AgentRunner` structurally (a stage-level `Protocol`, not a `nora/domain/ports.py` port, so it is not subject to the contract-suite rule either). Load-bearing now: `tests/integration/harness.py` and `evals/harness.py` both build their pipeline from this package via `nora.app.offline.build_offline_pipeline`, closing the `evals/` → `tests/` coupling Task 10 removed. `FakeAuditStore` and `FakeMemoryStore` are each bound to a shared contract suite in `tests/contract/` that the matching production implementation also passes (CLAUDE.md's "a port never gains a second implementation without a shared contract suite"). | `nora.domain` only. Mechanically enforced: `nora.testing` shares `nora.infra`'s row in the layers contract and is listed in the config-forbidding contract, so it may import neither `nora.obs` nor `nora.config` — it ships inside the wheel, so a fake reaching into production config would ship too. |

## 3. Ports and their current implementations

Ports live in `nora/domain/ports.py` (framework-free, `Protocol`, keyword-only
after `self`). Conformance — where today's concrete classes do and do not satisfy
their port — is asserted by `tests/contract/test_port_conformance.py` for
`PlatformAdapter`, `MemoryStore`, and `AuditStore`; `EngagePolicy` and
`ContextAssembler` have no test in that file yet and are checked statically by
mypy instead (see their rows below).

| Port | Concrete implementation today | Known gap |
|---|---|---|
| `PlatformAdapter` | `nora/platform/signal/adapter.py::SignalAdapter` | **`SignalAdapter.send` returns a bare `bool`, not the port's `SendResult`.** The port and its rationale (Telegram's 429/`retry_after`, `Deliverer`'s attempt-counting latch — see `domain/ports.py`'s module docstring) are documented; nothing else in the repo records that the implementation hasn't caught up. `SignalAdapter` also has no `resolve_identity()` yet. Two more gaps: `SignalAdapter.health()` returns a plain `dict`, not the port's `AdapterHealth`; and `SignalAdapter.listen()` takes a positional `on_event: Callable[[dict], Awaitable[None]]` callback receiving a raw dict, not the port's keyword-only `on_message: Callable[[NormalizedMessage], Awaitable[None]]`. All four close in the **platforms phase** (spec §8 phase 4). Pinned explicitly by `test_signal_adapter_gap_to_platform_adapter_is_known`. |
| `MemoryStore` | `nora/infra/honcho/client.py::HonchoClient` | None: method names match (`test_honcho_client_satisfies_memory_store_method_names`) and `ensure_peer`/`ensure_session` are idempotent under concurrency — each takes a per-peer `asyncio.Lock` (`HonchoClient._peer_lock`), closing the check-then-act window the port's docstring declares (spec §6). |
| `AuditStore` | `nora/infra/sql/audit.py::SqlAuditStore`, over SQLAlchemy Core. | None: method names match the port (`test_both_implementations_satisfy_the_port`, `tests/contract/test_audit_store_contract.py`), and static conformance is checked by mypy (`nora.infra` is inside the strict override). `nora/testing/fakes.py::FakeAuditStore` is the port's second implementation, bound to the same contract suite. |
| `EngagePolicy` | `nora/ingest/policy.py::TriggerTextPolicy` (`tag.is_tagged` moved off `Handler.on_event`, no other behaviour change; `tag.Debounce` moved to `nora/engagement/stages/admission.py` instead — `policy.py` never references it). | No dedicated `isinstance`/contract test yet — conformance is checked statically: `nora.app.composition.Dependencies.policy: EngagePolicy` is assigned a `TriggerTextPolicy` instance inside `build_dependencies`, and `nora.app` is inside mypy's strict override. `ClassifierPolicy` (D1's A3 amendment, `CompositePolicy([TriggerTextPolicy, ClassifierPolicy])`) is still not built. |
| `ContextAssembler` | `nora/engagement/context.py::TranscriptInjection` (replaces `Handler._collect_context`, preserving both its failure-shaped behaviours — see the module's own docstring). | Same as `EngagePolicy`: no dedicated contract test, checked statically via `Dependencies.assembler: ContextAssembler` inside mypy's strict `nora.app` override. `EpisodeContinuation` is still accepted-but-not-implemented (DEC-3). |

## 4. File-by-file walk

### One ambient message

Runs inside `nora/ingest/pipeline.py::IngestPipeline.run`, called from
`nora/app/dispatch.py::Dispatcher.on_event`, which is what a platform
adapter's `listen()` loop invokes per raw event (the direct replacement for
`Handler.on_event`).

1. `nora/platform/signal/listener.py::SignalListener._stream` — reads an SSE line, JSON-decodes it, calls `on_event(event)`.
2. `nora/app/dispatch.py::Dispatcher.on_event` — the entry point; awaits `IngestPipeline.run(raw_event=raw_event)`.
3. `nora/ingest/stages/parse.py::run` → `adapter.parse(raw_event)` → `nora/platform/signal/adapter.py::SignalAdapter.parse` → `nora/platform/signal/parser.py::parse_event` — builds a `nora/domain/models.py::NormalizedMessage`, or drops the event (DM, sync message, self-sent, empty body — R-12 first pass). `None` here ends the pipeline (`IngestResult(message=None, decision=None)`).
4. `nora/ingest/stages/audit.py::run` → `nora/infra/sql/audit.py::SqlAuditStore.append` — SQLite audit write. On failure: log + `nora_errors_total{stage="ambient"}` + drop (pipeline ends here too), never re-raise into the listener.
5. `nora/ingest/stages/identity.py::run` → `nora/persons.py::Persons.resolve` — the mapped `PersonRef`, or `None` for an unmapped sender (D6); the audit write in step 4 already happened either way.
6. `nora/ingest/stages/memory_feed.py::run` — if mapped, schedules (never awaits inline) a background task → `nora/infra/honcho/client.py::HonchoClient.ensure_peer` + `.feed`.
7. `nora/ingest/stages/gate.py::run` → `nora/ingest/policy.py::TriggerTextPolicy.should_engage` → `nora/tag.py::is_tagged` — the only branch in the pipeline. `Dispatcher.on_event` returns without spawning anything if `decision.engage` is `False`. Ambient-only messages stop here.

### One tag

`Dispatcher.on_event` hands an admitted `EngagementRequest` (built from step
7's `EngageDecision`, plus a fresh `tag_id`) to
`nora/app/runtime.py::BackgroundTasks.spawn`, which runs
`nora/engagement/pipeline.py::EngagementPipeline.run` — fire-and-forget, the
same shape `Handler._spawn(self.handle_tag(msg))` had.

8. `nora/engagement/stages/admission.py::run` — R-12 (self-tag, `adapter._account` check), R-9 (`nora/tag.py::Debounce.allow`), R-10 (in-flight set check + add). Any rejection returns `False` and `EngagementPipeline.run` stops without running the tag.
9. `nora/engagement/context.py::TranscriptInjection.assemble` — `nora/infra/sql/audit.py::SqlAuditStore.recent` (transcript) + `nora/infra/honcho/client.py::HonchoClient.peer_card` → a `TagContext` (`nora/domain/models.py`).
10. `nora/persona/loader.py::PersonaStore.get(applies_to="tag")` — hot-reloaded persona (mtime-checked).
11. `nora/engagement/stages/deliver.py::build` — constructs `nora/agent/guards.py::Deliverer` and `nora/agent/guards.py::ReplyGuard`, fresh, one per tag.
12. `nora/agent/tools.py::build_tools` — the three native tools (`read_buffer`, `read_peer_card`, `send_reply`), closures over this tag's dependencies.
13. `nora/agent/prefill.py::build_prefill` / `build_tag_message` — R-13 (XML-escape all group-derived text into an untrusted block), R-15 (header states what context is already present).
14. `nora/engagement/stages/invoke.py::run` → `nora/app/agent_runner.py::LiveAgentRunner.invoke` → `nora/agent/factory.py::create_agent` / `invoke` — the Strands `Agent` loop runs, bounded by `asyncio.wait_for(..., timeout=...)` (R-8/R-10's invocation timeout). `factory.create_client`/`create_model_provider` are called once at process start (`nora/app/composition.py::build_dependencies`), not per tag.
15. A `send_reply` tool call → `nora/agent/guards.py::Deliverer.deliver` — audit-first: `SqlAuditStore.append(..., is_nora=True, delivery_status=DeliveryStatus.PENDING)`, then `adapter.send(...)` (today returns `bool` — see §3 gap), then `SqlAuditStore.set_delivery_status`.
16. `nora/engagement/stages/deliver.py::check_outcome` — confirms a reply actually went out; a model that never called `send_reply` is counted separately (`nora_silent_failures_total{reason="no_send_call"}`).
17. `nora/engagement/stages/feedback.py::run` — spawned as a background task if a tagger was mapped, one exchange fed to Honcho.
18. `nora/engagement/pipeline.py::EngagementPipeline._run_admitted`'s `finally` — releases the in-flight key (R-10, every exit path) and records `nora_tag_latency_seconds`/`nora_overhead_seconds`. Its enclosing `except BaseException` is what makes R-5's silence guarantee unconditional, including on faults that are not an `Exception` at all.

## 5. Where new code goes

From spec §10. Rows marked **(target)** name a package that does not exist yet
in this repo — they describe where the pipelines/agent/platforms phases will put
that kind of code, not a path you can use today.

| I want to… | Touch only |
|---|---|
| add a platform | **(target)** `platforms/<name>/` + one `[platforms.<name>]` config block. Today: extend `platform/` following `platform/signal/`'s shape. |
| add a native tool | **(target)** `agent/tools/native/<name>.py` + a registry entry. Today: `nora/agent/tools.py::build_tools`. |
| add an MCP server | **(target)** `[tools.mcp.<name>]` — no Python. Not built yet (DEC-2). |
| change engagement decisions | `nora/ingest/policy.py::TriggerTextPolicy` (wraps `nora/tag.py::is_tagged`). A future classifier composes in as `CompositePolicy([TriggerTextPolicy, ClassifierPolicy])`, not a replacement — see A3. |
| add a persisted column | `nora/infra/sql/schema.py` (the `MetaData`/`Table` source of truth) + `uv run alembic revision --autogenerate`. **Caveat:** a revision touching `idx_messages_conv_ts` must be hand-written — it is excluded from autogenerate on purpose (see DEC-11), so autogenerate's output for it cannot be trusted. |
| add a behavioural rule | `design/09-decision-index.md` + a named test registered in `tests/test_acceptance_rules.py::RULE_TESTS`. |
