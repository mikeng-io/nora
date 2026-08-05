# Nora — Architecture

This is the map, not the history. For the decisions behind it and their enforcing
tests, see `design/09-decision-index.md`. For the full rationale, see
`docs/superpowers/specs/2026-08-04-nora-architecture-design.md` (the current spec)
and `design/*.md` (the topology it amends). This file is pinned to reality by
`tests/test_architecture_doc.py`: it fails collection if a package under `nora/`
goes unmentioned here.

**Status as of Phase 4 (platforms):** `nora/ingest/` (four ambient stages now —
audit, identity, memory_feed, gate; parsing moved onto the adapter, see below)
and `nora/engagement/` (admission, context, invoke, deliver, feedback) exist,
glued together by `nora/app/dispatch.py::Dispatcher` and built by
`nora/app/composition.py::build_dependencies`. `nora/handler.py` and
`nora/models.py` are **deleted** — there is no re-export shim; every module
imports `nora.domain.models` directly. `nora/app/runtime.py`
(`BackgroundTasks`, the prune sweep, the debounce sweep, per-adapter
supervision) and `nora/app/offline.py` (the pipeline-wiring helper shared by
the integration test harness and `evals/`) landed in Phase 3, alongside
`nora/app/eval_support.py` (the facade `evals/` now reads config/agent/persona/tag
pieces through, in place of the direct imports it used to make). `nora/testing/`
(port fakes bound by shared contract suites) and `nora/infra/` (`sql/` —
SQLAlchemy Core + Alembic audit store; `honcho/` — the semantic-store client),
both built in Phase 2, are unchanged since. This phase closed every gap
between `nora/platform/signal/adapter.py::SignalAdapter` and
`nora.domain.ports.PlatformAdapter` — `isinstance(SignalAdapter(...),
PlatformAdapter)` is `True` — and added `nora/platform/registry.py::
AdapterRegistry`, which builds every enabled platform adapter and resolves
its identity at startup, failing fast. The rest of the package layout
described in the spec's target state (a `tools/` registry) has **not** been
built yet — that is the agent-tooling phase. This document describes the
tree as it exists today, with target-state notes called out where they
differ.

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
   → tools: now / read_buffer / read_peer_card / send_reply
            + room-scoped: group_members / lookup_person / recall
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
| `app/` | The composition root and everything that used to live inside `Handler`/`App`, factored into single-purpose modules: `composition.py` — `Dependencies` (frozen dataclass) and `build_dependencies(cfg) -> Dependencies`, the only function allowed to know `Config`'s shape besides `nora.__main__` itself; owns the ordered, fatal startup validation (`StartupError`, now including `nora.platform.registry.AdapterRegistry.from_config`'s fail-fast) and builds the full graph — both pipelines, the `Dispatcher`, `BackgroundTasks`, `Debounce`. `dispatch.py` — `Dispatcher.on_message`, the glue between `IngestPipeline` and `EngagementPipeline` that `Handler.on_event` used to be (renamed from `on_event` in the platforms phase: it now receives an already-parsed `NormalizedMessage`, not a raw dict — parsing moved onto the adapter). `runtime.py` — `BackgroundTasks` (the strong-reference task-holder `Handler._spawn` used), `prune_loop`, `debounce_sweep_loop`, `supervise_adapter`/`start_adapters` (per-adapter restart-with-backoff). `adoption.py` — `backfill`, the startup lazy-adoption sweep (DEC-16): audit rows whose sender was unmapped at ingest, and now resolves against `identity.toml`, are replayed into that person's Honcho peer and only then marked with their `person_id`. Spawned by `App.run` through `BackgroundTasks` exactly like `prune_loop`, and off the ingest write path for the same single-writer reason. `offline.py` — `build_offline_pipeline`, the one definition of "wire ingest+engagement+dispatcher from already-built collaborators" that `tests/integration/harness.py` and `evals/harness.py` both call instead of each keeping its own copy. `eval_support.py` — the facade `evals/` reads config/agent/persona/tag pieces through (see the `evals` import-linter contract below). `agent_runner.py` — `LiveAgentRunner`, the production `nora.engagement.stages.invoke.AgentRunner`, wrapping `nora.agent.factory`'s Strands plumbing so `nora.engagement` never imports `strands` itself. `adoption.py` — the lazy-adoption backfill (DEC-16), spawned at startup like `prune_loop`. **The control plane (DEC-20)**: `cli.py` — the `nora groups` / `nora persons pending` subcommands, strictly read-only (a `mode=ro` + `query_only` engine, and `load(..., require_secrets=False)` so listing group ids needs no LLM credential); `pending.py` — union-find correlation of one account's several id forms, merged from the audit envelopes, `listContacts` AND `listGroups` rosters; `report.py` — rendering, including paste-ready `[[persons...]]` and `[[conversations]]` stanzas. | `nora.config` (this is one of the two packages allowed to), plus whatever collaborator packages each module needs: `nora.agent`, `nora.domain`, `nora.engagement`, `nora.infra.honcho`, `nora.infra.sql`, `nora.ingest`, `nora.obs.metrics`, `nora.obs.health`, `nora.persona`, `nora.persons`, `nora.platform`, `nora.platform.signal`, `nora.tag`. |
| `agent/` | `factory.py` (Strands `Agent`/model/client construction), `guards.py` (`LeakGuard`, `Deliverer`, `ReplyGuard` — `ReplyGuard`'s per-tool budgets are injected, DEC-10), `ports.py` (`ToolSpec`, a plain frozen description of a tool, and `adapt()`, the only place one becomes a Strands tool object), `tools.py` (the native tools, as `ToolSpec`s — `build_tool_specs`/`build_tools`), `room.py` (`Room`/`RoomView`: this conversation's membership resolved to people, and the scope `group_members`/`lookup_person`/`recall` resolve names INSIDE — R-21, DEC-27), `registry.py` (`ToolRegistry`: `budgets()` feeds `ReplyGuard`, `build()`/`specs()` feed the engagement pipeline), `prefill.py` (R-13 escaping, R-15 header). | The only package allowed to import `strands`. Forbidden from importing `nora.config` (contract 3) — `factory.py` was the one former offender (it used to read a duck-typed `cfg` sub-config); it now takes typed scalar parameters instead. |
| `platform/` | `registry.py` — `AdapterRegistry.from_config`, builds every enabled platform adapter and calls `resolve_identity()` on each at startup, failing fast (fatal — routed through `nora.app.composition`'s `StartupError`); the fail-fast matters because R-12 depends on identity being known. `signal/` (`adapter.py::SignalAdapter` — the one `PlatformAdapter` implementation, `listener.py`, `parser.py`, `send.py`, `account.py`, `react.py`, `self_id.py`, `directory.py` — the read-only `listGroups`/`listContacts` allowlist the control plane uses, and `roster.py::SignalRoster`, the `RosterSource` implementation the room-scoped tools read membership through). The pre-ports-migration `base.py` (an older, duplicate `PlatformAdapter` Protocol) is deleted — the platforms phase closed every gap between `SignalAdapter` and the real port, `nora.domain.ports.PlatformAdapter`, so nothing needed it anymore. | `nora.domain`, `aiohttp`. Forbidden from importing `nora.config` (contract 3) — `registry.py` takes a narrow structural Protocol over `cfg.platforms` instead. |
| `infra/` | `sql/` — `schema.py` (the `MetaData`/`Table` source of truth), `engine.py` (`create_sql_engine`, WAL/`busy_timeout`/`foreign_keys` on a connect hook), `audit.py` (`SqlAuditStore`), `migrations_runner.py` (programmatic Alembic: `run_migrations`, `current_revision`, `head_revision`, `stamp_head`, `alembic_config`), `migrations/` (Alembic env + `versions/0001_baseline.py`, `versions/0002_person_id.py`); `honcho/` — `client.py::HonchoClient`, best-effort semantic store; a separate package from `sql/` because the two ports' error contracts are opposite — see `infra/__init__.py`'s docstring. | `sqlalchemy`, `alembic`, `aiohttp`, `nora.domain.models`, `nora.domain.ports` (`honcho/client.py` — `MemoryHealth`, the typed return of `HonchoClient.health()`). May **NOT** import `nora.obs` — `nora.infra` sits below `nora.obs` in the layers contract (contract 2), which is why `SqlAuditStore` takes `metrics` by injection rather than importing `nora.obs.metrics`; see `infra/__init__.py` and `audit.py`'s `__init__` docstring. Forbidden from importing `nora.config` (contract 3). |
| `persona/` | `loader.py` — `PersonaStore`, the only module that may read a `.md` persona file (enforced by `test_boundaries.py::test_persona_read_boundary`). | stdlib, YAML, `nora.obs.metrics` (`loader.py:20` — reload-failure counter). Forbidden from importing `nora.config`. |
| `obs/` | `logging.py` (`setup_logging`), `tracing.py` (`setup_tracing`, OTel), `metrics.py` (`Metrics` — the recording machinery; the `METRICS` module-level singleton was deleted in phase 6, see DEC-13), `metric_specs.py` (every declared metric name, label set and help text — append-only, enumerated by `tests/test_metrics_export.py`), `health.py` (`build_health_app` — `GET /health`, `GET /metrics`; takes `store`/`honcho` as injected dependencies, not a module import — the `/health` endpoint calls `store.stats()` and `honcho.health()`). | `aiohttp`, `opentelemetry`. Forbidden from importing `nora.config` — `logging.py::setup_logging`/`tracing.py::setup_tracing` take explicit scalar parameters instead of a duck-typed `cfg` sub-config; `health.py::build_health_app` was never an offender, it already took individually-extracted fields. |
| `ingest/` | The ambient path (spec §4.3): `pipeline.py::IngestPipeline` sequences five stages — `stages/scope.py` (the room half of the policy allowlist, deliberately ahead of the audit write so a conversation absent from `policy.toml` is never recorded — DEC-18), `stages/identity.py`, `stages/audit.py`, `stages/memory_feed.py`, `stages/gate.py` (the former `stages/parse.py` is gone; parsing moved into the adapter) — each with exactly one public `async def run(...)`. `policy.py::TriggerTextPolicy` is the `EngagePolicy` this phase ships (see the ports table below); it lives here, not in `engagement/`, because the gate it satisfies runs on the ingest pipeline. Replaces the ambient half of `Handler.on_event`. | `nora.domain`, `nora.tag`, `nora.persons`, `nora.obs.metrics`. Forbidden from importing `nora.config` or `strands`. |
| `engagement/` | The tag-response path (spec §4.4): `pipeline.py::EngagementPipeline` sequences `stages/admission.py` (R-9 debounce, R-10 in-flight lock, R-12 self-tag), `context.py::TranscriptInjection` (the `ContextAssembler` this phase ships — see the ports table below), persona lookup, `stages/deliver.py` (`build`/`check_outcome`, wrapping `nora.agent.guards.Deliverer`/`ReplyGuard`), `stages/invoke.py` (one bounded LLM call, R-5/R-8), `stages/feedback.py` (the post-reply Honcho feed). Replaces the tagging half of `Handler` (`_collect_context`, `_invoke`, `_feed_exchange`, and the debounce/in-flight/self-tag checks from `on_event`). | `nora.domain`, `nora.persons`, `nora.tag`, `nora.obs.metrics`. Forbidden from importing `nora.config` or `strands` — `Deliverer`/`ReplyGuard` stay in `nora.agent` (they are `strands.HookProvider`s) rather than moving here, for exactly that reason. |
| `persons.py` | `Persons`, `load_persons` — `identity.toml` → reverse index by `(platform, sender_id)`, built from every id form of every account. One person may hold many accounts per platform (design/10 §3, §7.4); they all resolve to one `person_id`, hence one Honcho peer (A2). | `nora.persons_file`, `nora.domain.models`. |
| `persons_file.py` | `identity.toml`'s on-disk shape: the pydantic models and their validators, and nothing else. Split from `persons.py` when that module reached 297 of the 300-line hard cap; the seam is real regardless — validating the file format is a separate job from building and serving the reverse index. Nothing here is public, `nora.persons` is the only import site. | pydantic, `nora.domain.identity`. |
| `tag.py` | `is_tagged` (R-11 trigger validation), `Debounce` (R-9, plus `sweep` for `nora.app.runtime.debounce_sweep_loop`'s TTL cleanup). | stdlib only. |
| `testing/` | `fakes.py` — `FakeAuditStore` (`AuditStore`'s second implementation, `fail_on_nora`/`fail_on_recent` switches), `FakeMemoryStore` (`MemoryStore`'s second implementation, a `fail` switch), `FakeAdapter` (keeps `.parse()` public and `.listen()` a no-op rather than mirroring `SignalAdapter.listen(*, on_message=...)`'s internal parse-and-drop — nothing drives it through a real SSE loop — so it is exempt from the contract-suite rule as a non-conforming double, not a second implementation). `scripted_agent.py` — `ScriptedTurn`/`FakeAgentRunner`/`group_event`, the scripted model double shared by `tests/` and `evals/`, satisfying `nora.engagement.stages.invoke.AgentRunner` structurally (a stage-level `Protocol`, not a `nora/domain/ports.py` port, so it is not subject to the contract-suite rule either). Load-bearing now: `tests/integration/harness.py` and `evals/harness.py` both build their pipeline from this package via `nora.app.offline.build_offline_pipeline`, closing the `evals/` → `tests/` coupling Task 10 removed. `FakeAuditStore` and `FakeMemoryStore` are each bound to a shared contract suite in `tests/contract/` that the matching production implementation also passes (CLAUDE.md's "a port never gains a second implementation without a shared contract suite"). | `nora.domain` only. Mechanically enforced: `nora.testing` shares `nora.infra`'s row in the layers contract and is listed in the config-forbidding contract, so it may import neither `nora.obs` nor `nora.config` — it ships inside the wheel, so a fake reaching into production config would ship too. |

## 3. Ports and their current implementations

Ports live in `nora/domain/ports.py` (framework-free, `Protocol`, keyword-only
after `self`). Conformance — where today's concrete classes do and do not satisfy
their port — is asserted by `tests/contract/test_port_conformance.py` for
`PlatformAdapter`, `MemoryStore`, and `AuditStore`; `EngagePolicy` and
`ContextAssembler` have no test in that file yet and are checked statically by
mypy instead (see their rows below).

| Port | Concrete implementation today | Known gap |
|---|---|---|
| `PlatformAdapter` | `nora/platform/signal/adapter.py::SignalAdapter` | None. `isinstance(SignalAdapter(...), PlatformAdapter)` is `True` (`tests/contract/test_port_conformance.py::test_signal_adapter_satisfies_platform_adapter`), the platforms phase's headline goal: `resolve_identity() -> PlatformIdentity`, `send(*, target, text) -> SendResult`, `health() -> AdapterHealth`, `listen(*, on_message) -> None` all match, and the port's own `name: str` (the outlier — every other type on the port already said `platform`) was renamed to `platform: str` to close the fifth gap, found only after the other four closed. A static conformance proof — `isinstance` against a `runtime_checkable` `Protocol` checks names, not signatures — lives at `nora.app.composition.build_dependencies`'s `_adapter_satisfies_platform_adapter` assignment, inside mypy's strict `nora.app` override. |
| `MemoryStore` | `nora/infra/honcho/client.py::HonchoClient` | None: method names match (`test_honcho_client_satisfies_memory_store_method_names`) and `ensure_peer`/`ensure_session` are idempotent under concurrency — each takes a per-peer `asyncio.Lock` (`HonchoClient._peer_lock`), closing the check-then-act window the port's docstring declares (spec §6). |
| `AuditStore` | `nora/infra/sql/audit.py::SqlAuditStore`, over SQLAlchemy Core. | None: method names match the port (`test_both_implementations_satisfy_the_port`, `tests/contract/test_audit_store_contract.py`), and static conformance is checked by mypy (`nora.infra` is inside the strict override). `nora/testing/fakes.py::FakeAuditStore` is the port's second implementation, bound to the same contract suite. |
| `EngagePolicy` | `nora/ingest/policy.py::TriggerTextPolicy` (`tag.is_tagged` moved off `Handler.on_event`, no other behaviour change; `tag.Debounce` moved to `nora/engagement/stages/admission.py` instead — `policy.py` never references it). | No dedicated `isinstance`/contract test yet — conformance is checked statically: `nora.app.composition.Dependencies.policy: EngagePolicy` is assigned a `TriggerTextPolicy` instance inside `build_dependencies`, and `nora.app` is inside mypy's strict override. `ClassifierPolicy` (D1's A3 amendment, `CompositePolicy([TriggerTextPolicy, ClassifierPolicy])`) is still not built. |
| `ContextAssembler` | `nora/engagement/context.py::TranscriptInjection` (replaces `Handler._collect_context`, preserving both its failure-shaped behaviours — see the module's own docstring). | Same as `EngagePolicy`: no dedicated contract test, checked statically via `Dependencies.assembler: ContextAssembler` inside mypy's strict `nora.app` override. `EpisodeContinuation` is still accepted-but-not-implemented (DEC-3). |

## 4. File-by-file walk

### One ambient message

Runs inside `nora/ingest/pipeline.py::IngestPipeline.run`, called from
`nora/app/dispatch.py::Dispatcher.on_message`, which is what a platform
adapter's `listen()` loop calls per already-parsed message (the direct
replacement for `Handler.on_event`). Parsing itself now happens one layer
earlier, inside the adapter's `listen()` — `PlatformAdapter` deliberately has
no `parse()` (`nora/domain/ports.py`'s module docstring: forcing every
adapter to pretend its transport is dict-shaped is wrong for Discord's
gateway events and Telegram's update batches), so `IngestPipeline` only ever
receives an already-normalized `NormalizedMessage`, never a raw dict.

1. `nora/platform/signal/listener.py::SignalListener._stream` — reads an SSE line, JSON-decodes it, calls an internal `_on_event(event)` callback.
2. `nora/platform/signal/adapter.py::SignalAdapter.listen`'s internal `_on_event` → `nora/platform/signal/parser.py::parse_event` — builds a `nora/domain/models.py::NormalizedMessage`, or drops the event (DM, sync message, self-sent, empty body — R-12 first pass). A `None` here is dropped silently — `on_message` (below) is never called for it.
3. `nora/app/dispatch.py::Dispatcher.on_message` — the entry point for anything that *did* parse; awaits `IngestPipeline.run(message=message)`.
4. `nora/ingest/stages/identity.py::run` → `nora/persons.py::Persons.resolve` — the mapped `PersonRef`, or `None` for an unmapped sender (D6). Runs BEFORE the audit write so the resolved `person_id` lands in that one INSERT rather than a second write; `resolve` is two dict lookups on a startup-built frozen mapping, so nothing fallible moved ahead of the record. An unmapped sender increments `nora_unmapped_senders_total{platform}` and logs a redacted warning — its symptom set is otherwise empty (still audited, gate still runs, prefill still shows the platform display name, nothing reaches Honcho).
5. `nora/ingest/stages/audit.py::run` → `nora/infra/sql/audit.py::SqlAuditStore.append` — SQLite audit write, carrying `person_id` (NULL when unmapped). On failure: log + `nora_errors_total{stage="ambient"}` + drop (`IngestResult(message=None, decision=None)`, pipeline ends here), never re-raise into the listener.
6. `nora/ingest/stages/memory_feed.py::run` — if mapped, schedules (never awaits inline) a background task → `nora/infra/honcho/client.py::HonchoClient.ensure_peer` + `.feed`.
7. `nora/ingest/stages/gate.py::run` → `nora/ingest/policy.py::TriggerTextPolicy.should_engage` → `nora/tag.py::is_tagged` — the trigger branch. Two policy gates bracket it: the room gate ran before the audit write, and the sender gate runs immediately after this one, downgrading `engage` to False with `reason="sender_not_allowed"` when the tagger is not in that conversation's `persons` list (DEC-18). `Dispatcher.on_message` returns without spawning anything if `decision.engage` is `False`. Ambient-only messages stop here.

### One tag

`Dispatcher.on_message` hands an admitted `EngagementRequest` (built from step
7's `EngageDecision`, plus a fresh `tag_id`) to
`nora/app/runtime.py::BackgroundTasks.spawn`, which runs
`nora/engagement/pipeline.py::EngagementPipeline.run` — fire-and-forget, the
same shape `Handler._spawn(self.handle_tag(msg))` had.

8. `nora/engagement/stages/admission.py::run` — R-12 (self-tag: `message.sender_id in identity.aliases`, where `identity` is resolved via `adapter.resolve_identity()`, memoized), R-9 (`nora/tag.py::Debounce.allow`), R-10 (in-flight set check + add). Any rejection returns `False` and `EngagementPipeline.run` stops without running the tag.
9. `nora/engagement/context.py::TranscriptInjection.assemble` — `nora/infra/sql/audit.py::SqlAuditStore.recent` (transcript) + `nora/infra/honcho/client.py::HonchoClient.peer_card` → a `TagContext` (`nora/domain/models.py`).
10. `nora/persona/loader.py::PersonaStore.get(applies_to="tag")` — hot-reloaded persona (mtime-checked).
11. `nora/engagement/stages/deliver.py::build` — constructs `nora/agent/guards.py::Deliverer` and `nora/agent/guards.py::ReplyGuard`, fresh, one per tag; `ReplyGuard` is constructed with `budgets=tool_registry.budgets()` (DEC-10).
12. `nora/agent/registry.py::ToolRegistry.build` — the native tools (`now`, `read_buffer`, `read_peer_card`, `send_reply`, plus the room-scoped `group_members`, `lookup_person`, `recall`), closures over this tag's dependencies, built as `nora/agent/ports.py::ToolSpec`s (`nora/agent/tools.py::build_tool_specs`) and adapted to Strands tool objects (`nora/agent/ports.py::adapt`). The three room-scoped ones close over one `nora/agent/room.py::Room`, which reads `listGroups` LAZILY — nothing touches the daemon unless one of them is actually called, and the read is cached for the tag (DEC-27).
13. `nora/agent/prefill.py::build_prefill` / `build_tag_message` — R-13 (XML-escape all group-derived text into an untrusted block), R-15 (header states what context is already present).
14. `nora/engagement/stages/invoke.py::run` → `nora/app/agent_runner.py::LiveAgentRunner.invoke` → `nora/agent/factory.py::create_agent` / `invoke` — the Strands `Agent` loop runs, bounded by `asyncio.wait_for(..., timeout=...)` (R-8/R-10's invocation timeout). `factory.create_client`/`create_model_provider` are called once at process start (`nora/app/composition.py::build_dependencies`), not per tag.
15. A `send_reply` tool call → `nora/agent/guards.py::Deliverer.deliver` — audit-first: `SqlAuditStore.append(..., is_nora=True, delivery_status=DeliveryStatus.PENDING)`, then `adapter.send(...)` (`SendResult`, keyword-only — `Deliverer` reads `.ok`, not truthiness), then `SqlAuditStore.set_delivery_status`.
16. `nora/engagement/stages/deliver.py::check_outcome` — confirms a reply actually went out; a model that never called `send_reply` is counted separately (`nora_silent_failures_total{reason="no_send_call"}`).
17. `nora/engagement/stages/feedback.py::run` — spawned as a background task if a tagger was mapped, one exchange fed to Honcho.
18. `nora/engagement/pipeline.py::EngagementPipeline._run_admitted`'s `finally` — releases the in-flight key (R-10, every exit path) and records `nora_tag_latency_seconds`/`nora_overhead_seconds`. Its enclosing `except BaseException` is what makes R-5's silence guarantee unconditional, including on faults that are not an `Exception` at all.

## 5. Where new code goes

From spec §10. Rows marked **(target)** name a package that does not exist yet
in this repo — they describe where the pipelines/agent/platforms phases will put
that kind of code, not a path you can use today.

| I want to… | Touch only |
|---|---|
| add a platform | `platform/<name>/` following `platform/signal/`'s shape (a `PlatformAdapter` implementation), one `[platforms.<name>]` config block, and one more `if platforms.<name>.enabled:` block in `nora/platform/registry.py::AdapterRegistry.from_config` (spec §8 phase 4; only Signal is wired today). |
| add a native tool | `nora/agent/tools.py`: write the closure (docstring is the model-facing schema) and add a `ToolSpec` for it — with a `budget` — to `build_tool_specs`'s return tuple, keeping `NATIVE_TOOL_BUDGETS` in sync. `nora/agent/registry.py::ToolRegistry` picks it up automatically: `budgets()` and `build()` both derive from `build_tool_specs`, so `ReplyGuard` gets the new tool's budget without any change to `nora/agent/guards.py` (DEC-10 — this is the defect that motivated the tool-registry phase). |
| add an MCP server | **(target)** `[tools.mcp.<name>]` — no Python. Not built yet (DEC-2). |
| change engagement decisions | `nora/ingest/policy.py::TriggerTextPolicy` (wraps `nora/tag.py::is_tagged`). A future classifier composes in as `CompositePolicy([TriggerTextPolicy, ClassifierPolicy])`, not a replacement — see A3. |
| add a persisted column | `nora/infra/sql/schema.py` (the `MetaData`/`Table` source of truth) + `uv run alembic revision --autogenerate`. **Caveat:** a revision touching `idx_messages_conv_ts` must be hand-written — it is excluded from autogenerate on purpose (see DEC-11), so autogenerate's output for it cannot be trusted. |
| add a behavioural rule | `design/09-decision-index.md` + a named test registered in `tests/test_acceptance_rules.py::RULE_TESTS`. |
