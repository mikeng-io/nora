# Nora — Architecture

This document follows [arc42](https://arc42.org/): its section numbers are
arc42's, so a reader who knows the template knows where to look. Several
sections are satisfied by a document that already exists and are pointers here
rather than copies — a second copy of the constraints or the decisions would
drift from the one that is enforced.

This is the map, not the history. It is pinned to reality by
`tests/test_architecture_doc.py`, which fails if a package under `nora/` goes
unmentioned here.

| arc42 section | where it lives |
|---|---|
| 1. Introduction and goals | below, and `README.md` |
| 2. Constraints | `AGENTS.md` — the five operational constraints |
| 3. Context and scope | below |
| 4. Solution strategy | `AGENTS.md` — the comprehend → measure → serialize spine |
| 5. Building block view | below |
| 6. Runtime view | below |
| 7. Deployment view | `README.md` deployment; `AGENTS.md` «Reaching the deployment» |
| 8. Crosscutting concepts | below |
| 9. Architecture decisions | `design/09-decision-index.md` (the index) and `docs/adr/` (the reasoning) |
| 10. Quality requirements | `evals/`, and `make gate` |
| 11. Risks and technical debt | GitHub issues on `mikeng-io/nora` |
| 12. Glossary | `GLOSSARY.md` |

## 1. Introduction and goals
This is the map, not the history. For the decisions behind it and their enforcing
tests, see `design/09-decision-index.md`; for the full rationale, `design/*.md`
and `docs/adr/`. (An earlier line here called
`docs/superpowers/specs/2026-08-04-nora-architecture-design.md` "the current
spec" — that tree was deleted 2026-09-08 and the path resolves only in git
history, so it is named here as the historical citation it now is, not as a
pointer. Every «spec §n» below is the same kind of citation.) This file is pinned to reality by
`tests/test_architecture_doc.py`: it fails collection if a package under `nora/`
goes unmentioned here.

**Status as of 2026-09-09.** Every package below exists and is wired; the
three shipped-off paths that speak uncalled — the heartbeat's reminders, the
participation occasion (design/dreamer-agent-loop/35) and the proactive
adaptor (design/dreamer-agent-loop/39, DEC-162) — are granted per room in
`policy.toml` and per section in `config.toml`, and the Dreamer runs three
adaptors (`note-candidates`, `note-curation`, `proactive`) over the note tier
and Standing. The paragraph that follows is the Phase 4 status this file was
first written at, kept because its «not built yet» claims were later closed
one by one and the closings are recorded where they happened.

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
`nora/app/eval_support.py` (the facade `evals/` now reads config/agent/doctrine/tag
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


## 2. Constraints

The five operational constraints — the container build's `--network=host`,
Honcho's `GET /health`, signal-cli's `--receive-mode=on-start` and Java 25, the
archived legacy gateway path, and `uv add` over pip — are stated in `AGENTS.md`
and carried in full, with the incident behind each, in `docs/CONVENTIONS.md`.
They are not restated here: a constraint that appears twice is a constraint that
will eventually disagree with itself.

## 3. Context and scope: two paths, one boundary

```
Signal group
   │  SSE push (every message)
   ▼
① AMBIENT PATH (always, no Agent SDK)
   event → parser → NormalizedMessage → Postgres audit + Honcho feed (mapped senders only)
   │
   └─ trigger text present? ── no ──▶ done, ambient only — recorded, never
                              │       answered. One governed exception
                              │       (design/dreamer-agent-loop/35): in a room
                              │       the operator opted in (`participation =
                              │       true` in policy.toml), the message is also
                              │       NOTED with `nora.app.participation`'s
                              │       quiescence trigger; when the room goes
                              │       quiet for `[participation] settle_seconds`,
                              │       ONE determination runs through the fire
                              │       path (③, README), with the right to stay
                              │       silent. Off by default.
                              └─ yes ──▶
② ENGAGEMENT / TAGGING PATH (trigger only, via the Strands Agents SDK)
   collect context (transcript window, <standing>, <notes>, <self_card>,
                    the tagger's <peer_history>†, doctrine)
   → Agent(messages=[pre-filled context]) → agent.invoke_async(tag_message)
   → the native tools (`ToolRegistry.tool_names()`, ~50, rendered into the
     doctrine's generated capability section) — `load_transcript`/`who_said`/
     `search_memory`, `about`/`ask_memory`/`remember`, the room-scoped
     `group_members`/`lookup_person`/`where_is`/`route`…, the note verbs, the
     event verbs, `remind`, the web/media/social reads, and the terminal
     `send_reply`/`send_gif` plus `send_to` (a relay to a sibling room)
   → reply → audit row (delivery_status=pending → sent/failed) → Honcho feed

   † `<peer_history>` is Honcho's `representation` (nora → person), not the
     peer-card endpoint, which returned null here (DEC-27).

④ THE DREAMER (background, never speaks — `nora/app/runtime.py::dream_loop`)
   per adaptor, on its own schedule: a deterministic `inputs()` read, and a
   bounded research loop ONLY when that read rendered something.
   note-candidates (hourly, per room) → stages notes from new messages
   note-curation   (daily, per workspace, off by default) → retires notes
   proactive       (hourly tick, renders on change or a daily floor) → may
                    write ONE kind of row: a `kind="proactive"` reminder for ③
```

The boundary is still two senders (R-1b): ① never speaks, ④ never speaks, and
③'s occasions are enumerated — a reminder, a gathering, an announcement, a
compute job, a relay, participation and proactive.


## 4. Solution strategy

Every path in this repo runs one loop — **comprehend → measure → serialize** —
and it is a constraint rather than a procedure: not how to build a thing, but
what each step must preserve. Interpret raw input once into a computable model;
reduce that model to a decidable, fail-closed signal (this repo's signature is
the three-state `ok` / `nothing_found` / `unavailable` tool result, where an
error is never a pass); fix the result into canonical, typed state with
provenance. `AGENTS.md` carries the full statement and the three rules that make
it hold.

## 5. Building block view — packages, and what each may import

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
  today's real cross-package edges (`agent` → `doctrine` → `obs` → `infra`;
  `platform` is independent of the other four) so the contract enforces
  domain's isolation without also relitigating those packages' existing,
  legitimate imports of each other.
- **Contract 3 — "only app and `__main__` read config."** `nora.agent`, `nora.obs`,
  `nora.doctrine`, `nora.platform`, `nora.domain`, `nora.persons`, `nora.tag`,
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
  (Nora's own loop) and `nora/compute/**` (Smith's separate sandbox image,
  built and run outside Nora's own process — see the `compute/` row below)
  may import `strands`.

| Package / module | What lives here today | What it may import |
|---|---|---|
| `domain/` | Framework-free contracts: `models.py` (platform-neutral data), `ports.py` (Protocol seams — `PlatformAdapter`, `RecordStore`, `MemoryStore`, `EngagePolicy`, `ContextAssembler`), `errors.py` (`FailureClass`, `NoraError`, DEC-4), `media.py` (`MediaRef`/`MediaBlob`/`MediaBytes` plus the RFC 6838 taxonomy and the placeholder renderer — DEC-45), `reminders.py` (`Reminder`/`ReminderState`/`ReminderStore` — the reminders port, DEC-69 — plus `ReminderCaps`, §9's three anti-runaway numbers as a DOMAIN value rather than a config type, because `nora.agent` may not import `nora.config`; and `ensure_utc`/`normalise_reminder`/`check_limit`, the UTC-aware and positive-`limit` rules both implementations call so they cannot answer them differently), `compute.py` (`ComputeJob`/`ComputeJobState`/`ComputeJobStore` — the compute-job outbox port, with its own UTC rule kept local rather than imported from `reminders.py`, since the two ports are independent even where the rule is identical), `prompt.py` (`escape`/`escape_attr` — R-13's rule, shared because `nora.agent.prefill` and `nora.doctrine.loader` both render into XML-ish attributes and the layer contract runs agent -> doctrine, so `domain` is the one place both may import), `utf16.py` (`utf16_len` — the unit every range Signal carries is measured in; it lived in `nora.agent.outbound` while @-mentions were the only ranges and moved down here when `TextStyle` became a second producer, because the alternative was `nora.agent.markdown` importing it through an import cycle or keeping a second copy of the arithmetic — DEC-82/DEC-86). **`language_scoring.py` and the fact/lexeme domain types were DELETED on 2026-09-07** with the knowledge and language tiers (issue #115). | Standard library only, plus its own siblings `domain.models`, `domain.identity` and `domain.utf16`. Nothing else in `nora/`. |
| `config.py` | `Config` — the frozen pydantic tree, `load()`, `ConfigError`. The only reader of `config.toml`. | pydantic, stdlib. Importable only by `app/` and `nora/__main__.py` (contract 3) — no exceptions. |
| `app/` | The composition root and everything that used to live inside `Handler`/`App`, factored into single-purpose modules: `composition.py` — `Dependencies` (frozen dataclass) and `build_dependencies(cfg) -> Dependencies`, the only function allowed to know `Config`'s shape besides `nora.__main__` itself; owns the ordered, fatal startup validation (`StartupError`, now including `nora.platform.registry.AdapterRegistry.from_config`'s fail-fast) and builds the full graph — both pipelines, the `Dispatcher`, `BackgroundTasks`, `Debounce`. `dispatch.py` — `Dispatcher.on_message`, the glue between `IngestPipeline` and `EngagementPipeline` that `Handler.on_event` used to be (renamed from `on_event` in the platforms phase: it now receives an already-parsed `NormalizedMessage`, not a raw dict — parsing moved onto the adapter), plus the participation branch: a `participation=True` engage verdict is NOTED with the trigger, never admitted to the tag path. `runtime.py` — `BackgroundTasks` (the strong-reference task-holder `Handler._spawn` used), `prune_loop`, `debounce_sweep_loop`, `supervise_adapter`/`start_adapters` (per-adapter restart-with-backoff). `adoption.py` — `backfill`, the startup lazy-adoption sweep (DEC-16): audit rows whose sender was unmapped at ingest, and now resolves against `identity.toml`, are replayed into that person's Honcho peer and only then marked with their `person_id`. Spawned by `App.run` through `BackgroundTasks` exactly like `prune_loop`, and off the ingest write path for the same single-writer reason. `offline.py` — `build_offline_pipeline`, the one definition of "wire ingest+engagement+dispatcher from already-built collaborators" that `tests/integration/harness.py` and `evals/harness.py` both call instead of each keeping its own copy. `eval_support.py` — the facade `evals/` reads config/agent/doctrine/tag pieces through (see the `evals` import-linter contract below). **The unprompted-speech path (DEC-69)**: `heartbeat.py` — `HeartbeatLoop` (the tick: due rows, `policy.toml` re-checked at FIRE time, the conversation slot taken with `try_acquire` so a reminder never queues behind a tag, and the row's terminal state DERIVED from the outcome — only `sent` is `done`), plus the module-level `recover` (boot sweep, run UNCONDITIONALLY, because a stranded `firing` row most needs sweeping on the deployment that has the tick switched off) and `publish_idle_gauge`; `fire.py` — `FirePath`, one UNPROMPTED turn (`TagContext`, the occasion's doctrine audience — `heartbeat`, or `participation` for the fifth `Event` — prefill with its own header, invoke under `[heartbeat] timeout_seconds`, deliver; a participation turn runs on its own `tool_choice="auto"` runner, never recovers a written draft, and answers `silent` rather than a failure when it declines the floor) reusing the engagement stages without pretending to be a tag. What differs between its kinds is the domain `Event` it takes (where she speaks, on whose authority, the words, the header; DEC-139): `speak(event)` is the turn, `run(rows)` the heartbeat's reminder-shaped adapter onto it. `announce.py` — `VersionAnnouncer` (DEC-97), the boot sweep that hands `FirePath.speak` the operator's release note for a version nobody has been told about, once per room ever (R-42: the claim is an INSERT on `version_announcements`' primary key, taken BEFORE the turn). It builds no outbound path of its own — R-1b forbids a third caller of `deliver.build`, which is exactly why the `Event` seam exists. `compute_poller.py` — `ComputeJobPoller` (the THIRD thing built on `Event`, shaped like `announce.py` rather than `heartbeat.py`: no `due_at` to compare against, so every `PENDING` row is polled against `ComputeBackend` on every tick, the conversation slot is taken with `try_acquire` exactly as the other two do, and the row's terminal state is DERIVED from the outcome — only `sent` is `done`), plus its own module-level `recover`, run UNCONDITIONALLY beside the heartbeat's and sweeping BOTH `firing` (a crash mid-delivery) and `pending` (any restart at all: the backend's job registry is in memory, so a surviving pending row names a job the new process cannot identify and must never speak about). `proactive_screen.py` — `ProactiveScreen`, the fire-time screen for a `kind="proactive"` row (design/dreamer-agent-loop/39 D5/D11, DEC-162): the room's `proactive` flag instead of persons membership, and the note the row is about — retired, or revised on the tag path since the row was linked — cancels it before any model is called; an unreadable premise fails closed. `participation.py` — `ParticipationTrigger`, the participation occasion's quiescence trigger (design/dreamer-agent-loop/35): per-room watermarks (last message, last window end, last send), one evaluation per settle-closed window through `FirePath.speak`, the slot taken with `try_acquire` exactly as the heartbeat's is, and a SEND cooldown that caps what the room hears without ever skipping an evaluation; `runtime.py` gained `participation_loop`, the prune_loop-shaped tick that closes due windows and sweeps stale rooms. `agent_runner.py` — `LiveAgentRunner`, the production `nora.engagement.stages.invoke.AgentRunner`, wrapping `nora.agent.factory`'s Strands plumbing so `nora.engagement` never imports `strands` itself. `adoption.py` — the lazy-adoption backfill (DEC-16), spawned at startup like `prune_loop`. **The control plane (DEC-20)**: `cli.py` — the `nora groups` / `nora persons pending` / `nora presence members` / `nora reminders list` subcommands, strictly read-only (there is deliberately no `nora reminders cancel`: the engine is `SELECT`-only, so the operator's kill switch is `[heartbeat] enabled = false` plus a restart, and cancelling ONE row is the model's job — §7.4) (a `mode=ro` + `query_only` engine, and `load(..., require_secrets=False)` so listing group ids needs no LLM credential); `pending.py` — union-find correlation of one account's several id forms, merged from the audit envelopes, `listContacts` AND `listGroups` rosters; `report.py` — rendering, including paste-ready `[[persons...]]` and `[[conversations]]` stanzas. | `nora.config` (this is one of the two packages allowed to), plus whatever collaborator packages each module needs: `nora.agent`, `nora.domain`, `nora.engagement`, `nora.infra.honcho`, `nora.infra.sql`, `nora.ingest`, `nora.obs.metrics`, `nora.obs.health`, `nora.doctrine`, `nora.persons`, `nora.platform`, `nora.platform.signal`, `nora.tag`. |
| `agent/` | `factory.py` (Strands `Agent`/model/client construction), `guards.py` (`LeakGuard`, `Deliverer`, `ReplyGuard` — `ReplyGuard`'s per-tool budgets are injected, DEC-10), `ports.py` (`ToolSpec`, a plain frozen description of a tool, and `adapt()`, the only place one becomes a Strands tool object), `tools.py` (the native tools, as `ToolSpec`s — `build_tool_specs`/`build_tools`), `arithmetic.py` (the `ast`-allowlist, `Decimal`-throughout evaluator behind `calculate`; pure, never raises, and the one place the remainder of an uneven split is made visible — DEC-35), `room.py` (`Room`/`RoomView`: this conversation's membership resolved to people, and the scope `group_members`/`lookup_person`/`about` resolve names INSIDE — R-21, DEC-27), `registry.py` (`ToolRegistry`: `budgets()` feeds `ReplyGuard`, `build()`/`specs()` feed the engagement pipeline, and it holds the process-lifetime collaborators a tool needs across turns — the transit catalogue, the reminders store, §9's `ReminderCaps` and the clock). The reminder verbs `remind`/`list_reminders`/`cancel_reminder` live in `tools.py` with the rest (DEC-69): none is terminal, all three are bound to the tag's conversation by the closure (§7.2, DEC-24 — there is no `target` parameter), and `remind` reads `build_tool_specs`'s `fire_turn` flag to REFUSE inside a fire turn, which is the structural bar on recurrence (R-39), `prefill.py` (R-13 escaping, R-15 header), `markdown.py` (`render_markdown` — DEC-82's grammar and the ONLY producer of a `Styled`: it turns `**bold**` into a real Signal style range and deletes the syntax, so the emphasis survives and the asterisks do not; `strip_markdown` is literally its text half, which is what stops `LeakGuard`'s haystack from speaking a different Markdown dialect from the reply it screens), `outbound.py` (`OutboundComposer`/`Outbound` — the wire form of one message: the @-mention ranges and the preview card that ride beside the text, DEC-86/DEC-87). | The only package allowed to import `strands`. Forbidden from importing `nora.config` (contract 3) — `factory.py` was the one former offender (it used to read a duck-typed `cfg` sub-config); it now takes typed scalar parameters instead. |
| `platform/` | `registry.py` — `AdapterRegistry.from_config`, builds every enabled platform adapter and calls `resolve_identity()` on each at startup, failing fast (fatal — routed through `nora.app.composition`'s `StartupError`); the fail-fast matters because R-12 depends on identity being known. `signal/` (`adapter.py::SignalAdapter` — the FIRST `PlatformAdapter` implementation, `listener.py`, `parser.py`, `send.py`, `account.py`, `react.py`, `self_id.py`, `directory.py` — the read-only `listGroups`/`listContacts` allowlist the control plane uses, and `roster.py::SignalRoster`, the `RosterSource` implementation the room-scoped tools read membership through). `discord/` — the SECOND (DEC-102, 2026-08-14): `adapter.py::DiscordAdapter`, `listener.py` (a Gateway wrapper over `discord.py`; registers `on_ready`/`on_resumed`/`on_disconnect`, and `on_resumed` is load-bearing — a RESUME is not a READY, and observing only the latter latched `connected=False` forever), `parser.py`, `snowflake.py` (id → timestamp, one-way), `send.py`, `react.py::DiscordReactor`, `self_id.py`, and `roster.py::DiscordRoster` (the guild members list, `RosterSource`'s second implementation). The pre-ports-migration `base.py` (an older, duplicate `PlatformAdapter` Protocol) is deleted — the platforms phase closed every gap between `SignalAdapter` and the real port, `nora.domain.ports.PlatformAdapter`, so nothing needed it anymore. | `nora.domain`, `aiohttp`. Forbidden from importing `nora.config` (contract 3) — `registry.py` takes a narrow structural Protocol over `cfg.platforms` instead. |
| `infra/` | `sql/` — `schema.py` (the `MetaData`/`Table` source of truth), `engine.py` (`create_sql_engine`, WAL/`busy_timeout`/`foreign_keys` on a connect hook), `audit.py` (`SqlRecordStore`), `migrations_runner.py` (programmatic Alembic: `run_migrations`, `current_revision`, `head_revision`, `stamp_head`, `alembic_config`), `migrations/` (Alembic env + `versions/0001_baseline.py`, `versions/0002_person_id.py`); `honcho/` — `client.py::HonchoClient`, best-effort semantic store; a separate package from `sql/` because the two ports' error contracts are opposite — see `infra/__init__.py`'s docstring; `transit/` — the two-operator client behind the `transit` tool (DEC-40): `client.py::TransitClient` (the one public entry point, `arrivals(route, near="")`, never raises), `catalogue.py::RouteCatalogue` (the KMB route list and MTR's route CSV, fetched concurrently and cached for the process — the thing that makes «no such route» distinguishable from «no buses» under R-27, because nothing in either ETA response can tell them apart), `places.py::StopDirectory`/`resolve_place` (both operators' stop datasets, cached the same way, and the resolver that turns a place somebody NAMED into that operator's own stop ids — the place catalogue behind `transit`'s `near` (DEC-41): the scope boundary design/14 §5.2 demanded, places only and never a person, no stop id or coordinate in any schema, and «this route does not go there» kept distinct from «no stop by that name» under R-27b), and `arrivals.py` (pure, never raises, the one place either operator's wire format is read — `nora.agent.arithmetic`'s shape for a tool that does have I/O). Held by `ToolRegistry`, not by a tag, so the catalogue and stop-directory caches outlive one question. | `sqlalchemy`, `alembic`, `aiohttp`, `nora.domain.models`, `nora.domain.ports` (`honcho/client.py` — `MemoryHealth`, the typed return of `HonchoClient.health()`). May **NOT** import `nora.obs` — `nora.infra` sits below `nora.obs` in the layers contract (contract 2), which is why `SqlRecordStore` takes `metrics` by injection rather than importing `nora.obs.metrics`; see `infra/__init__.py` and `audit.py`'s `__init__` docstring. Forbidden from importing `nora.config` (contract 3). |
| `infra/` | `sql/` — `schema.py` (the `MetaData`/`Table` source of truth), `engine.py` (`create_sql_engine`, WAL/`busy_timeout`/`foreign_keys` on a connect hook), `audit.py` (`SqlRecordStore`), `migrations_runner.py` (programmatic Alembic: `run_migrations`, `current_revision`, `head_revision`, `stamp_head`, `alembic_config`), `migrations/` (Alembic env + `versions/0001_baseline.py` … `versions/0004_media_json.py`), `reminders.py` (`SqlReminderStore` — the reminders table, whose `claim`/`cancel`/`sweep_firing` each carry a STATE predicate in the WHERE clause so taking a row is a compare-and-set rather than a read-then-write, R-37), `compute_jobs.py` (`SqlComputeJobStore` — the compute-job outbox, whose `claim` and both sweeps carry the same STATE predicate in the WHERE clause for R-37's reason), `announcements.py` (`SqlAnnouncementLog` — the version-announcement ledger, whose `claim` is `INSERT … ON CONFLICT DO NOTHING` so the PRIMARY KEY is the at-most-once mechanism, R-42/DEC-97); `version.py` (`VersionManifest` — the baked build stamp `nora/version.json`, read once per process and answering R-27's three states, where an absent or malformed file is `unavailable` and never `nothing_found`; generated at build time by `scripts/gen_version_manifest.py` and gitignored, so a source checkout reads `unavailable` by design); `compute/` — `docker_backend.py::LocalDockerBackend`, the sandbox runner (`docker-py` against the HOST daemon; it takes the workspace root TWICE, once as this process sees it and once as the host does, because a bind-mount source is resolved on the host and an in-container path silently resolves to an empty directory there); `blobs/` — `filesystem.py::FilesystemBlobStore`, the `BlobStore` port over `data/media/<first2>/<rest>` (its docstring holds the RustFS decision; do not re-litigate without evidence); `honcho/` — `client.py::HonchoClient`, best-effort semantic store; a separate package from `sql/` because the two ports' error contracts are opposite — see `infra/__init__.py`'s docstring; `social/` — the `social_fetch` seam (DEC-151): `bridge_client.py::OpenCliBridgeClient` (the container's `SocialProvider`, POSTing to the host bridge, never raises) and `bridge_server.py::SocialBridge` (the host-side half, `python -m nora.infra.social`, owning the read-only `(site, command)` allowlist that makes OpenCLI's write verbs unreachable). | `sqlalchemy`, `alembic`, `aiohttp`, `nora.domain.models`, `nora.domain.ports` (`honcho/client.py` — `MemoryHealth`, the typed return of `HonchoClient.health()`). May **NOT** import `nora.obs` — `nora.infra` sits below `nora.obs` in the layers contract (contract 2), which is why `SqlRecordStore` takes `metrics` by injection rather than importing `nora.obs.metrics`; see `infra/__init__.py` and `audit.py`'s `__init__` docstring. Forbidden from importing `nora.config` (contract 3). |
| `doctrine/` | `loader.py` — `DoctrineStore`, the only module that may read a `.md` doctrine file (enforced by `test_boundaries.py::test_doctrine_read_boundary`), and the `kind` gates DEC-29 added: a file with no `kind`, an `identity`/`conduct` file naming a tool, a file claiming the DERIVED `capability`/`situation` kinds, an incoherent `authority_above`/`authority_below` pair, an empty `not_here` or an unknown `change_policy` all refuse to load. The `capability` section has no file — the composition root hands the store `ToolRegistry.capability()`. | stdlib, YAML, `nora.obs.metrics` (reload-failure counter), `nora.domain.prompt` (attribute escaping). Forbidden from importing `nora.config`. |
| `dreamer/` | **Text, not code — there is no `__init__.py` here and there must not be.** The Dreamer's model-facing instructions as versioned artifacts: `doctrine/dreamer.md` (exactly one file — who the Dreamer is on every run: a read-only research agent, what counts as evidence, that a source which does not mention something has not said it is false, when to stop, and that «I could not check» is a result to report) and `objectives/*.md` (one per adaptor — what that one job is trying to find out). Read by `nora.instructions.dreamer.DreamerInstructions`, which assembles doctrine + one objective and hashes the pair into the `instruction_sha256` every row that adaptor writes carries. Bind-mounted read-only into the container beside `nora/doctrine`, so an operator edit is live on the next tick with no rebuild — and, exactly like the doctrine, an edit on `master` IS a deploy. The doctrine loader's gates (kind boundaries, authority graph, RFC 2119 register) do NOT run on these files. | none — it is a directory of Markdown. |
| `language/` | The Dreamer's extraction side. **The lexeme/exchange/portrait half (the language tier) and the fact half (the knowledge tier) were DEPRECATED and DELETED on 2026-09-07 — see issue #115 and `design/dreamer-agent-loop/20-knowledge-tier.md` §0; their database tables are not dropped.** What remains is the NOTE tier and Standing's rendering: `note_candidates.py` (the note extraction adaptor; its create gate matches on anchor date), `note_curator.py` (retire, R-74/R-75 — its merge half and the `note_entity_adaptor.py` entity-resolution pass were deleted 2026-09-08, since nothing minted an entity once the knowledge tier was gone), `proactive.py` (the proactive adaptor — Nora's other half, design/dreamer-agent-loop/39, DEC-162: workspace-keyed, standing in a policy-named home room, every read verb and exactly one act verb, `remind`, whose sink writes only `record_wake`), `note_render.py` and `standing_render.py` (the prefill blocks). Nothing here touches `nora.infra` or a database itself — the adaptors return domain candidates and a store writes them. | stdlib plus `nora.domain.models`, `nora.obs.metrics` (candidate-drop counter). Forbidden from importing `nora.config` (contract 3) and `strands` (contract in `tests/test_boundaries.py`) — the one model call is expressed through an injected structural client, never `nora.agent.factory`'s Strands plumbing. |
| `skills/` | The on-demand skill layer (design/12 §1, DEC-48) — NOT doctrine: a skill's body must not sit in the prompt on every turn, only its name and when-to-load line do (progressive disclosure), and it activates on demand. `loader.py::SkillStore` reads the AgentSkills.io SKILL.md shape (`name` + `description` frontmatter) from one operator-configured directory; `resolve(name)` is a pure name lookup that can never turn message content into a loaded skill (R-29 — a skill source is operator config only). `navigation/SKILL.md` is the first skill (wayfinding judgement). Shipped INERT: nothing in `nora.app.composition` constructs a `SkillStore` yet, because activation needs a model-facing tool with an R-7 budget in `nora.agent.tools.NATIVE_TOOLS`. | stdlib, YAML. Forbidden from importing `nora.config` (contract 3) and `strands` — framework-free so the files also load unchanged under the vended `AgentSkills` plugin when activation is wired. |
| `instructions/` | The parse/hash/reload core shared by every instruction corpus: `frontmatter.py::parse_document` (frontmatter split, YAML load, required-key and name-charset checks, `sha256` over the BODY only — the frontmatter carries versions and descriptions that never reach a model) and `reload.py::DirectoryWatcher` (mtime+size fingerprint, generic over what a scan produces, keeping the last good value when a live edit breaks and counting the failure on the CALLER's own counter — but raising on the FIRST scan, because a process that starts with no instructions and says nothing about it is `unavailable` reported as `nothing_found`). Extracted out of `nora.doctrine.loader` rather than forked: `_FRONTMATTER_RE` was already duplicated in `nora.skills.loader`, and the reload machine was unshared, so a copy would have been its first second implementation. The doctrine's gates, its `<section>` rendering and its audience filter did NOT move. `frontmatter.py` is the package's only `.md` reader — allowlisted in `test_boundaries.py::test_doctrine_read_boundary` for that reason. | stdlib, YAML, `nora.obs.metrics`. Forbidden from importing `nora.config` — the directory arrives as a `Path` from the composition root. |
| `obs/` | `logging.py` (`setup_logging`), `tracing.py` (`setup_tracing`, OTel), `metrics.py` (`Metrics` — the recording machinery; the `METRICS` module-level singleton was deleted in phase 6, see DEC-13), `metric_specs.py` (every declared metric name, label set and help text — append-only, enumerated by `tests/test_metrics_export.py`), `health.py` (`build_health_app` — `GET /health`, `GET /metrics`; takes `store`/`honcho` as injected dependencies, not a module import — the `/health` endpoint calls `store.stats()` and `honcho.health()`). | `aiohttp`, `opentelemetry`. Forbidden from importing `nora.config` — `logging.py::setup_logging`/`tracing.py::setup_tracing` take explicit scalar parameters instead of a duck-typed `cfg` sub-config; `health.py::build_health_app` was never an offender, it already took individually-extracted fields. |
| `web/` | `GET /app/v1/status` and `GET /app/v1/memory` for the friend-group web portal (`app/`, a separate Next.js app — designed in the 2026-08-17 web spec, since deleted; git history), registered onto the SAME aiohttp app `obs/health.py::build_health_app` builds via `api.py::register_web_routes(app, ...)` — no second port. `status.py::build_status_payload` re-projects `/health`'s store/Honcho reachability plus a reminders summary (`nora.domain.reminders.ReminderStore.all_pending`, due times and count only, never a reminder's own text). `memory.py::build_memory_payload` returns every `identity.toml` person's Honcho `representation` (observer=the AI peer, observed=that person — the same verb `engagement/context.py`'s `_recall_self`/`_recall_tagger_card` use), carrying R-27's `reachable`/`conclusions` split through rather than collapsing "Honcho is down" into "nothing known." | `nora.domain.ports` (`MemoryStore`, `RecordStore`), `nora.domain.reminders`, `nora.persons`, `aiohttp`. |
| `ingest/` | The ambient path (spec §4.3): `pipeline.py::IngestPipeline` sequences six stages — `stages/scope.py` (the room half of the policy allowlist, deliberately ahead of the audit write so a conversation absent from `policy.toml` is never recorded — DEC-18), `stages/identity.py`, `stages/media.py` (byte custody, INLINE and ahead of the audit write — DEC-45), `stages/audit.py`, `stages/memory_feed.py`, `stages/gate.py` (the former `stages/parse.py` is gone; parsing moved into the adapter) — each with exactly one public `async def run(...)`. `policy.py::TriggerTextPolicy` is the `EngagePolicy` this phase ships (see the ports table below); it lives here, not in `engagement/`, because the gate it satisfies runs on the ingest pipeline. Replaces the ambient half of `Handler.on_event`. | `nora.domain`, `nora.tag`, `nora.persons`, `nora.obs.metrics`. Forbidden from importing `nora.config` or `strands`. |
| `engagement/` | The tag-response path (spec §4.4): `pipeline.py::EngagementPipeline` sequences `stages/admission.py` (R-9 debounce, R-10 in-flight lock, R-12 self-tag), `slots.py::ConversationSlots` (R-10's per-conversation in-flight lock and R-30's FIFO queue as ONE collaborator, hoisted out of the pipeline so the heartbeat's fire path contends for the SAME slot — `release_and_dispatch` is one call rather than two statements so an early return cannot separate them), `context.py::TranscriptInjection` (the `ContextAssembler` this phase ships — see the ports table below), doctrine lookup, `stages/deliver.py` (`build`/`check_outcome`, wrapping `nora.agent.guards.Deliverer`/`ReplyGuard`), `stages/invoke.py` (one bounded LLM call, R-5/R-8), `stages/feedback.py` (the post-reply Honcho feed — the tagger's words only, DEC-36). Replaces the tagging half of `Handler` (`_collect_context`, `_invoke`, `_feed_exchange`, and the debounce/in-flight/self-tag checks from `on_event`). | `nora.domain`, `nora.persons`, `nora.tag`, `nora.obs.metrics`. Forbidden from importing `nora.config` or `strands` — `Deliverer`/`ReplyGuard` stay in `nora.agent` (they are `strands.HookProvider`s) rather than moving here, for exactly that reason. |
| `presence/` | The presence collector (design/19, DEC-54/55) — a SECOND top-level composition root and a SEPARATE process, `python -m nora.presence`. `collector.py::PresenceCollector` runs the per-circle change-recorder loop (read Life360, count each member's `wire_state`, write a fix only when `ts` advances, publish a liveness heartbeat, back off on auth failures) and the canary (non-JSON / empty-circle). `composition.py::build_collector(cfg)` is the only place besides `nora.presence.__main__` allowed to read `Config`; it is inert (returns no collector) when `[presence] enabled = false`. `__main__.py` serves the daemon's own `/metrics` + `/health` and runs the loops. Shares only the `nora` database (`nora.infra.sql.presence.PresenceStore`) and the Life360 client (`nora.infra.life360`) with Nora; either can be down without the other. `whereis` (the read) is `nora.agent.whereis`, not here — it belongs beside `Room` at the assembly seam (R-21). | `nora.config` (a composition root, like `nora.app`), `nora.infra.sql`, `nora.infra.life360`, `nora.obs.metrics`, `nora.domain.presence`. Top layer in the import-linter contract, alongside `nora.app`. |
| `analytics/` | The presence analytics substrate (DEC-115) — a THIRD top-level composition root and a SEPARATE process, `python -m nora.analytics`. `rollup.py::MobilityRollup` runs the per-subject tick: three fire conditions (`safety_lag` backlog threshold, `settled_seconds` quiet boundary, `max_age_seconds` firing at zero lag on purpose) drive three speeds of work — stay segmentation every cycle, a cheap label-freshness recheck every cycle, and DBSCAN clustering only when the dirty-set marker is behind. `composition.py::build_rollup(cfg)` is the only place besides `nora.analytics.__main__` allowed to read `Config`; inert (returns no rollup) when `[analytics] enabled = false`. `__main__.py` serves the daemon's own `/metrics` + `/health`, evaluates the fire conditions per subject per tick, and isolates one subject's failure from the rest. Consumer-neutral by construction: `nora.analytics` and `nora.domain.mobility` are forbidden by an import-linter contract from importing `nora.agent`/`app`/`platform`/`tag`/`doctrine`/`ingest`/`engagement`/`skills` — it emits typed facts only, never prose. Shares only the `nora` database (`nora.infra.sql.mobility.MobilityStore`) with Nora and the presence collector; any of the three can be down without the others. | `nora.config` (a composition root, like `nora.app`/`nora.presence`), `nora.infra.sql`, `nora.obs.metrics`, `nora.domain.mobility`, `nora.persons`. Top layer in the import-linter contract, alongside `nora.app`/`nora.presence`. |
| `compute/` | Smith's sandbox image source, not Nora's own runtime (Nora Compute spec §3): `smith/agent.py` (the image's `ENTRYPOINT` — bootstraps a bare Strands `Agent` from the `DROP_JOB_TOKEN`/`DROP_INTERNAL_URL`/`TASK_FILE`/`ARTIFACT_ID`/`SMITH_MODEL_*` env vars `nora.infra.compute.docker_backend.LocalDockerBackend._start_container` sets, gives it a `run_python` code-exec tool and a `register_artifact` tool that calls Drop's `POST`/`PATCH /api/artifacts` directly, and writes `/workspace/url.txt` + `/workspace/summary.txt` for `LocalDockerBackend._read_result` to read back), `smith/doctrine.md` (Smith's own persona/governing prompt — NOT Nora's doctrine; a second, exempted `test_boundaries.py::test_doctrine_read_boundary` reader for exactly that reason), `smith/pyproject.toml`/`smith/Dockerfile` (Smith's OWN pinned dependency set and image build — a separate Python environment baked into its own Docker image, deliberately not importing anything from the rest of `nora`). Built once via `docker build --network=host -t smith:latest nora/compute/smith/`, never per job. | Nothing else in `nora/` — `smith/agent.py` imports only `strands`/`requests`/stdlib, from ITS OWN `pyproject.toml`-pinned environment inside its own image, never the top-level `nora` package's dependencies. The one other package `test_boundaries.py::test_strands_import_boundary` allows to import `strands`, alongside `agent/` — allowed by package, not because it shares anything with Nora's own agent loop. |
| `persons.py` | `Persons`, `load_persons` — `identity.toml` → reverse index by `(platform, sender_id)`, built from every id form of every account. One person may hold many accounts per platform (design/10 §3, §7.4); they all resolve to one `person_id`, hence one Honcho peer (A2). | `nora.persons_file`, `nora.domain.models`. |
| `persons_file.py` | `identity.toml`'s on-disk shape: the pydantic models and their validators, and nothing else. Split from `persons.py` when that module reached 297 of the 300-line hard cap; the seam is real regardless — validating the file format is a separate job from building and serving the reverse index. Nothing here is public, `nora.persons` is the only import site. | pydantic, `nora.domain.identity`. |
| `tag.py` | `is_tagged` (R-11 trigger validation), `Debounce` (R-9, plus `sweep` for `nora.app.runtime.debounce_sweep_loop`'s TTL cleanup). | stdlib only. |
| `testing/` | `fakes.py` — `FakeRecordStore` (`RecordStore`'s second implementation, `fail_on_nora`/`fail_on_recent` switches), `FakeMemoryStore` (`MemoryStore`'s second implementation, a `fail` switch), `FakeMediaSource`/`FakeBlobStore` (`MediaSource`/`BlobStore`'s second implementations — DEC-45; the media fake keys off the attachment id signal-cli actually issued and answers `not_found` otherwise, rather than saying yes to everything), `FakeAdapter` (keeps `.parse()` public and `.listen()` a no-op rather than mirroring `SignalAdapter.listen(*, on_message=...)`'s internal parse-and-drop — nothing drives it through a real SSE loop — so it is exempt from the contract-suite rule as a non-conforming double, not a second implementation). `reminders.py` — `FakeReminderStore` (`ReminderStore`'s second implementation; the three rules Postgres gives for free and a dict does not — UTC normalisation, the `id` tiebreak, insert-only `create` — are spelled out there because each was a real divergence). `scripted_agent.py` — `ScriptedTurn`/`FakeAgentRunner`/`group_event`, the scripted model double shared by `tests/` and `evals/`, satisfying `nora.engagement.stages.invoke.AgentRunner` structurally (a stage-level `Protocol`, not a `nora/domain/ports.py` port, so it is not subject to the contract-suite rule either). Load-bearing now: `tests/integration/harness.py` and `evals/harness.py` both build their pipeline from this package via `nora.app.offline.build_offline_pipeline`, closing the `evals/` → `tests/` coupling Task 10 removed. `FakeRecordStore` and `FakeMemoryStore` are each bound to a shared contract suite in `tests/contract/` that the matching production implementation also passes (AGENTS.md's "a port never gains a second implementation without a shared contract suite"). | `nora.domain` only. Mechanically enforced: `nora.testing` shares `nora.infra`'s row in the layers contract and is listed in the config-forbidding contract, so it may import neither `nora.obs` nor `nora.config` — it ships inside the wheel, so a fake reaching into production config would ship too. |

## 6. Runtime view — a file-by-file walk

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
5. `nora/ingest/stages/media.py::run` → `nora/platform/signal/media.py::SignalMediaSource.fetch` + `nora/infra/blobs/filesystem.py::FilesystemBlobStore.put` — attachment bytes into Nora's own custody, content-addressed. Runs BEFORE the audit write, INLINE rather than through `spawn`, because a handle in the body (`[image a3f2c1]`) is a promise the bytes exist and both must land in one INSERT (DEC-45). No attachments means one `if` and no calls. Any failure leaves the placeholder without a handle, records the message anyway, and increments `nora_media_custody_total{status}`; `nora_media_attachments_total` counts what was SEEN, so zero custody is distinguishable from zero media. **Not wired in `composition.py` yet** — see `design/18-media-custody.md` §9.
6. `nora/ingest/stages/audit.py::run` → `nora/infra/sql/audit.py::SqlRecordStore.append` — Postgres audit write, carrying `person_id` (NULL when unmapped). On failure: log + `nora_errors_total{stage="ambient"}` + drop (`IngestResult(message=None, decision=None)`, pipeline ends here), never re-raise into the listener.
7. `nora/ingest/stages/memory_feed.py::run` — if mapped, schedules (never awaits inline) a background task → `nora/infra/honcho/client.py::HonchoClient.ensure_peer` + `.feed`.
8. `nora/ingest/stages/gate.py::run` → `nora/ingest/policy.py::TriggerTextPolicy.should_engage` → `nora/tag.py::is_tagged` — the trigger branch. Two policy gates bracket it: the room gate ran before the audit write, and the sender gate runs immediately after this one, downgrading `engage` to False with `reason="sender_not_allowed"` when the tagger is not in that conversation's `persons` list (DEC-18). `Dispatcher.on_message` returns without spawning anything if `decision.engage` is `False`. Ambient-only messages stop here.

### One tag

`Dispatcher.on_message` hands an admitted `EngagementRequest` (built from step
7's `EngageDecision`, plus a fresh `tag_id`) to
`nora/app/runtime.py::BackgroundTasks.spawn`, which runs
`nora/engagement/pipeline.py::EngagementPipeline.run` — fire-and-forget, the
same shape `Handler._spawn(self.handle_tag(msg))` had.

8. `nora/engagement/stages/admission.py::run` — R-12 (self-tag: `message.sender_id in identity.aliases`, where `identity` is resolved via `adapter.resolve_identity()`, memoized), R-9 (`nora/tag.py::Debounce.allow`), R-10 (in-flight set check + add). Any rejection returns `False` and `EngagementPipeline.run` stops without running the tag.
9. `nora/engagement/context.py::TranscriptInjection.assemble` — `nora/infra/sql/audit.py::SqlRecordStore.recent` (transcript) + `nora/infra/honcho/client.py::HonchoClient.peer_card` → a `TagContext` (`nora/domain/models.py`).
10. `nora/doctrine/loader.py::DoctrineStore.get(applies_to="tag")` — hot-reloaded doctrine (mtime-checked). Six sections on a tag: five authored files ordered by frontmatter `order`, plus the GENERATED `kind: capability` section at order 35 (`nora/agent/registry.py::ToolRegistry.capability()`, handed to the store by the composition root) — DEC-29. **The audience is a filter, not a constant**: the four occasion-neutral files (`identity`, `voice`, `conduct`, `constraints`) are `applies_to: [tag, heartbeat, participation, proactive]`, so a turn nobody called still gets identity, voice, conduct and constraints rather than the capability block by itself — which would also have gutted `LeakGuard`'s verbatim-leak corpus (R-6) on the paths that speak unprompted; `silence.md` (order 25) is `[tag, participation, proactive]` because its may-speak half is true of an offered floor and false of a reminder owed to whoever asked; `participation-conduct.md` is `[participation]` alone; and `participation.md` is the Relevance Gate's instruction on `[relevance_gate]`, an audience no turn of hers reads. `tests/test_doctrine_content.py` pins the table. DEC-40's multi-message rule lived inside `# Silence is the default` and is true on any turn, so it stayed in `conduct.md` under its own heading: the extraction is smaller than the section.
11. `nora/engagement/stages/deliver.py::build` — constructs `nora/agent/guards.py::Deliverer` and `nora/agent/guards.py::ReplyGuard`, fresh, one per tag; `ReplyGuard` is constructed with `budgets=tool_registry.budgets()` (DEC-10) and `terminal=tool_registry.terminal_tools()` (DEC-32), and `Deliverer`'s `message_cap` is read out of that same `budgets` mapping — `send_reply`'s budget IS the message cap and there is only one of it (DEC-32, DEC-40).
12. `nora/agent/registry.py::ToolRegistry.build` — the native tools (`now`, `calculate`, `load_transcript`, `send_reply`, the room-scoped `group_members`, `lookup_person`, `about`, `where_is`, and the rest of `NATIVE_TOOLS`), closures over this tag's dependencies, built as `nora/agent/ports.py::ToolSpec`s (`nora/agent/tools.py::build_tool_specs`) and adapted to Strands tool objects (`nora/agent/ports.py::adapt`). The three room-scoped ones close over one `nora/agent/room.py::Room`, which reads `listGroups` LAZILY — nothing touches the daemon unless one of them is actually called, and the read is cached for the tag (DEC-27).
13. `nora/agent/prefill.py::build_prefill` / `build_tag_message` — R-13 (XML-escape all group-derived text into an untrusted block), R-15 (header states what context is already present).
14. `nora/engagement/stages/invoke.py::run` → `nora/app/agent_runner.py::LiveAgentRunner.invoke` → `nora/agent/factory.py::create_agent` / `invoke` — the Strands `Agent` loop runs, bounded by `asyncio.wait_for(..., timeout=...)` (R-8/R-10's invocation timeout). `factory.create_client` is called once at process start (`nora/app/composition.py::build_dependencies`), never per tag — that is audit T2's guarantee and it is what pools connections. The `OpenAIModel` **is** built per tag, by `factory.ModelSource.create()`, because `tool_choice` is per-turn state: `required` until she has spoken, `auto` after, flipped by `ReplyGuard._before_model` via `nora/agent/tool_choice.py::relax` (DEC-40). One shared model would apply one tag's relaxation to a concurrent tag's first call, which sends nothing.
15. A `send_reply` tool call → `nora/agent/guards.py::Deliverer.deliver` — audit-first: `SqlRecordStore.append(..., is_nora=True, delivery_status=DeliveryStatus.PENDING)`, then `adapter.send(...)` (`SendResult`, keyword-only — `Deliverer` reads `.ok`, not truthiness), then `SqlRecordStore.set_delivery_status`.
16. `nora/engagement/stages/deliver.py::check_outcome` — confirms a reply actually went out; a model that never called `send_reply` is counted separately (`nora_silent_failures_total{reason="no_send_call"}`).
17. `nora/engagement/stages/feedback.py::run` — spawned as a background task if a tagger was mapped, and it feeds the TAGGER'S message body under the tagger's peer, nothing else. Not the exchange: a two-speaker transcript under one peer id made Nora's own replies evidence about the person she was answering, because Honcho's deriver reads that stream as the target peer's own messages (DEC-36). Her reply is in Postgres and reachable through `load_transcript`, which is where ADR 0001 says it belongs.
18. `nora/engagement/pipeline.py::EngagementPipeline._run_admitted`'s `finally` — releases the in-flight key (R-10, every exit path) and records `nora_tag_latency_seconds`/`nora_overhead_seconds`/`nora_messages_per_tag` (the last one 0 for a silent turn, so every admitted tag contributes exactly one observation — DEC-31) and, for a turn that sent SOME of its messages and then stopped early, `nora_partial_replies_total{reason}` (R-25/DEC-40 — such a turn is not silent, so nothing R-23 built can see it). Its enclosing `except BaseException` is what makes R-5's silence guarantee unconditional, including on faults that are not an `Exception` at all.


## 7. Deployment view

Nora runs on the `norty` host as a compose project; development happens on the
MacBook, and the host is a deploy target rather than a dev checkout. `README.md`
carries the build, start, rollback and version procedures; `AGENTS.md`
«Reaching the deployment» carries the read-back commands and the rules that
make them safe (key-only SSH, the live untracked `config.toml`, the db reached
through compose, and why `docker compose logs nora` is empty).


## 8. Crosscutting concepts

### Observability

Three signals with a boundary that was measured rather than chosen: Prometheus
answers "how many, how often, with what outcome", Langfuse holds the traces with
a model call in them, and JSON-line logs are forensic. **`docs/observability.md`
is the map** — the signal boundary, the two-filter path a span takes to a
collector, how to instrument a model call, and how to read it back.

### The three memory layers

Nora has one record and one interpretation of it, and they are frequently
confused — including in this document until 2026-08-06.

| layer | store | what it holds | how she reads it |
|---|---|---|---|
| **hot** | Postgres | the last 40 messages, verbatim | injected into every tag prompt |
| **deeper** | **the same Postgres** | everything older, verbatim | `load_transcript` — a wider window, **not a second system** |
| **derived** | Honcho | conclusions *about people* | `about` |

**Postgres is the RECORD; Honcho is the INTERPRETATION.** Postgres stores what was
literally said, is authoritative, and is the only thing R-2/A9 guarantee.

**Media is part of the record, not a fourth layer** (DEC-45,
`design/18-media-custody.md`). An attachment's BYTES live in
`data/media/<first2>/<rest>`, content-addressed by sha256, and the row that
mentions them carries the hash in `messages.media_json` — written by the same
INSERT as the body. Before that they were a placeholder plus a pointer into
signal-cli's own store, under signal-cli's own retention, which made A9 false
for every image ever sent. The transcript shows a type and a durable handle
(`[image a3f2c1]`); the bytes never enter a prompt. Honcho
stores what a deriver concluded from it, is best-effort, and is allowed to be
absent — `nora.infra`'s docstring explains why the two clients have opposite
error contracts for exactly this reason.

The consequence worth stating: `load_transcript` returning nothing means *nothing
was said*. `about` returning nothing means *nothing was concluded*, which is a
different and much weaker claim — hence `Recollection.reachable`, so "I could
not ask" never collapses into "there is nothing".

**Two more layers arrived after that table was written, and neither is
memory in its sense.** The **note tier** (`design/dreamer-agent-loop/27`, `33`;
tables `note`/`note_revision`/`note_candidate`) holds the group's OPEN
QUESTIONS per workspace — written by people on a tag and by the
`note-candidates` adaptor, retired by the curator, and never by anything Nora
herself says (her rows are excluded from what the tier reads: the one
structural brake on her talking to herself, 39 §1). **Standing** (`design/
dreamer-agent-loop/26`; table `standing`) is her own one-paragraph position
per room, folded after each tag turn, rewritten in place, and injected as
`<standing>` — a stance, not a record. The **proactive adaptor** (`39`) reads
both, plus its own decision log (her `kind="proactive"` reminder rows) and
`<self_card>`, standing in a `home_room` `policy.toml` names per workspace.

The 2026-08-06 measurement of `representation` (~88% events, not traits) was
recorded here as «do not build further on Honcho»; DEC-66 and DEC-96 then
built on it deliberately — `(observer, observed)`-keyed reads, and a self
card framed as a record she may contradict — and the measurement stands as
the reason for that framing.

**D1 — the boundary rule.** The Agent SDK never sees ambient-only traffic. It is
constructed, invoked, and discarded once per tag. Ambient ingest is a pure,
deterministic write path (Postgres audit + Honcho feed) with exactly one branch:
trigger detection. See `design/00-overview.md` D1 and `design/09-decision-index.md`.

**The browser boundary (OpenCLI / social reads) is outside both paths.** The
host runs a persistent virtual desktop — Xtigervnc on display `:2` with Openbox,
headed Chrome on that display, and OpenCLI 1.8.7 (`localhost:19825`) driving it
through the Browser Bridge extension, all as systemd *user* services (see
`docs/runbook.md` §10). None of it is ambient ingest: it writes nothing to the
record. The surface is a single constrained, read-only tool, `social_fetch`
(DEC-151), covering X and Instagram reads (a post on X, a profile, an account's
posts, search) — never posting, liking, following, commenting or deleting. The
boundary is enforced at the tool's closed `(platform, what)` vocabulary AND
re-enforced by the host-side bridge's own allowlist (`nora/infra/social/
bridge_server.py`, bound to loopback + the docker0 gateway so the container —
on the private `nora` network, not host networking — can reach it; see
`deploy/social-bridge/`), so a write verb is unreachable even under prompt
injection; the tool ships inert behind
`[social] enabled`. Instagram post-by-URL is unsupported (OpenCLI 1.8.7 has no
caption-by-URL command) and Threads support waits on a native OpenCLI adapter
(1.8.7 has none). The virtual display itself is load-bearing rather than
incidental: Browser Bridge drives Chrome window APIs, and GNOME RDP reports
zero monitors while disconnected, which failed window creation — so RDP is
optional administration only, never the runtime.

### Ports and their current implementations

Ports live in `nora/domain/ports.py` (framework-free, `Protocol`, keyword-only
after `self`). Conformance — where today's concrete classes do and do not satisfy
their port — is asserted by `tests/contract/test_port_conformance.py` for
`PlatformAdapter`, `MemoryStore`, and `RecordStore`; `EngagePolicy` and
`ContextAssembler` have no test in that file yet and are checked statically by
mypy instead (see their rows below).

| Port | Concrete implementation today | Known gap |
|---|---|---|
| `PlatformAdapter` | `nora/platform/signal/adapter.py::SignalAdapter`, `nora/platform/discord/adapter.py::DiscordAdapter` | `DiscordAdapter` is the second implementation (DEC-102); both pass the shared `tests/contract/test_send_contract.py` and `test_reaction_contract.py` suites alongside `FakeAdapter`. `isinstance(SignalAdapter(...), PlatformAdapter)` is `True` (`tests/contract/test_port_conformance.py::test_signal_adapter_satisfies_platform_adapter`), the platforms phase's headline goal: `resolve_identity() -> PlatformIdentity`, `send(*, target, text, mentions=(), preview=None) -> SendResult`, `health() -> AdapterHealth`, `listen(*, on_message) -> None` all match, and the port's own `name: str` (the outlier — every other type on the port already said `platform`) was renamed to `platform: str` to close the fifth gap, found only after the other four closed. A static conformance proof — `isinstance` against a `runtime_checkable` `Protocol` checks names, not signatures — lives at `nora.app.composition.build_dependencies`'s `_adapter_satisfies_platform_adapter` assignment, inside mypy's strict `nora.app` override. |
| `RosterSource` | `nora/platform/signal/roster.py::SignalRoster` (signal-cli's read-only `listGroups`), `nora/platform/discord/roster.py::DiscordRoster` (the guild members list). | Deliberately NOT on `PlatformAdapter` — membership is a directory read, and putting it there would oblige every adapter and every fake to implement a roster it may have no endpoint for. One source PER PLATFORM since DEC-102: a single `SignalRoster` served every platform until 2026-08-14, so a Discord room was looked up in signal-cli, came back `available=False`, and every room-scoped tool — `where_is` included — answered «I couldn't look» forever. Fail-closed, and therefore invisible. Discord's roster reports the GUILD's members: Discord exposes no per-channel membership, and over-reporting is the safe direction for R-21's subset check (it can only make disclosure refuse, never permit). |
| `MemoryStore` | `nora/infra/honcho/client.py::HonchoClient` | None: method names match (`test_honcho_client_satisfies_memory_store_method_names`) and `ensure_peer`/`ensure_session` are idempotent under concurrency — each takes a per-peer `asyncio.Lock` (`HonchoClient._peer_lock`), closing the check-then-act window the port's docstring declares (spec §6). |
| `RecordStore` | `nora/infra/sql/audit.py::SqlRecordStore`, over SQLAlchemy Core. | None: method names match the port (`test_both_implementations_satisfy_the_port`, `tests/contract/test_audit_store_contract.py`), and static conformance is checked by mypy (`nora.infra` is inside the strict override). `nora/testing/fakes.py::FakeRecordStore` is the port's second implementation, bound to the same contract suite. |
| `MediaSource` | `nora/platform/signal/media.py::SignalMediaSource` — **Signal only; a KNOWN GAP since DEC-102.** `nora.ingest.stages.media` calls it for every message regardless of platform, so a Discord attachment is fetched from signal-cli and counted `not_found` when the honest answer is «asked the wrong platform» (R-27's distinction). Discord attachment custody is unbuilt. Original note:, over signal-cli's `getAttachment` JSON-RPC (verified to exist on 0.14.7 against a negative control — `design/18-media-custody.md` §4). | None at the name level (`test_the_media_ports_conform_in_both_implementations`); behaviour is `tests/contract/test_media_source_contract.py`. `nora.platform` is outside mypy's scoped strict override, so there is no static shape proof the way `SqlRecordStore` has one — the contract suite stands in for it. |
| `BlobStore` | `nora/infra/blobs/filesystem.py::FilesystemBlobStore` | None: inside mypy's strict override, plus `tests/contract/test_blob_store_contract.py` over both implementations. No `delete` on the port on purpose — retention is undecided (DEC-45 §8); the concrete class has it, exactly as `SqlRecordStore.prune_all` sits off `RecordStore`. |
| `ReminderStore` | `nora/infra/sql/reminders.py::SqlReminderStore`, over SQLAlchemy Core; `nora/testing/reminders.py::FakeReminderStore` in memory. | None: names by `test_the_reminder_stores_conform_to_the_port`, behaviour by `tests/contract/test_reminder_store_contract.py` over BOTH, shape by mypy for the SQL side. The port lives in `nora/domain/reminders.py` rather than `ports.py` because it arrived with its own value types (`Reminder`, `ReminderState`, `ReminderCaps`) and the UTC/limit rules both stores share. |
| `ComputeJobStore` | `nora/infra/sql/compute_jobs.py::SqlComputeJobStore`, over SQLAlchemy Core; `nora/testing/fakes.py::FakeComputeJobStore` in memory. | None: behaviour by `tests/contract/test_compute_job_store_contract.py` over BOTH, shape by mypy for the SQL side. Lives in `nora/domain/compute.py` for `ReminderStore`'s own reason (it arrived with `ComputeJob`/`ComputeJobState` and its own UTC rule), and it is deliberately NOT a second `ReminderStore`: one job, one delivery, ever — no `due_at`, no recurrence, and a `sweep_pending` its sibling has no use for, because a compute job's `backend_job_id` is a handle into an IN-MEMORY backend registry that no restart survives (`nora.app.compute_poller.recover`). |
| `ComputeBackend` | `nora/infra/compute/docker_backend.py::LocalDockerBackend` (one container per job, built only when `[compute] enabled`); `nora/testing/fakes.py::FakeComputeBackend`. | None at the name level (`tests/contract/test_compute_backend_contract.py` drives both). The gap that is RECORDED rather than closed: the job registry is per-process memory, so a restart orphans both the handle and any running container — boot recovery resolves the ROW and cannot touch the container. |
| `LinkPreviewSource` | `nora/infra/preview.py::OpenGraphPreviewSource` (DEC-87), constructed only when `[preview].enabled`; `nora/testing/fakes.py::FakeLinkPreviewSource` is the second implementation. | None at the name level (`tests/contract/test_link_preview_contract.py` asserts the keyword-only signature and `isinstance` for both), and the SSRF guard's refusals are held to the same answers across both. The gap that is REAL and recorded rather than closed: the guard refuses a non-globally-routable destination, and a local test server is one, so the fetch-and-parse path has no end-to-end assertion — the module is split into pure seams (`card_from_html`, `next_hop`, `_safe`) that are asserted directly, plus a loopback positive control proving the guard is in the path. The byte ceiling, the mid-flight content-type refusal and the `og:image` download remain live-smoke items. |
| `EngagePolicy` | `nora/ingest/policy.py::TriggerTextPolicy` (`tag.is_tagged` moved off `Handler.on_event`, no other behaviour change; `tag.Debounce` moved to `nora/engagement/stages/admission.py` instead — `policy.py` never references it). | No dedicated `isinstance`/contract test yet — conformance is checked statically: `nora.app.composition.Dependencies.policy: EngagePolicy` is assigned a `TriggerTextPolicy` instance inside `build_dependencies`, and `nora.app` is inside mypy's strict override. `ClassifierPolicy` (D1's A3 amendment, `CompositePolicy([TriggerTextPolicy, ClassifierPolicy])`) is still not built. |
| `ContextAssembler` | `nora/engagement/context.py::TranscriptInjection` (replaces `Handler._collect_context`, preserving both its failure-shaped behaviours — see the module's own docstring). | Same as `EngagePolicy`: no dedicated contract test, checked statically via `Dependencies.assembler: ContextAssembler` inside mypy's strict `nora.app` override. `EpisodeContinuation` is still accepted-but-not-implemented (DEC-3). |


## 9. Architecture decisions

Two artifacts, deliberately not merged. `design/09-decision-index.md` is the
**index**: one line per decision across the live identifier namespaces (`D*`,
`A*`, `R-*`, `DEC-*`), naming the document that made it and the test that
enforces it. `docs/adr/` holds **Nygard-form ADRs** for decisions with a cost
worth recording — an alternative rejected, a trade accepted. The index answers
"which test guards this?"; an ADR answers "why, and what did we give up?".
`docs/adr/README.md` explains when each is warranted; `CONTRIBUTING.md` places
both against a GitHub issue, which records a candidate rather than a decision.

## 10. Quality requirements

`make gate` is the release gate — pytest, ruff, mypy (strict, scoped) and
import-linter, run serially, and it is the final check rather than the iteration
check (`make test-affected` is the fast loop). `evals/` is the layer above it:
the same comprehend → measure → serialize loop applied to a real user journey —
a typed case, a live model trajectory measured fail-closed (empty reply, wrong
path or blown budget is a FAIL; a timeout is an ERROR and never a PASS), and a
verdict bound to its Langfuse trace. `AGENTS.md` carries the gate commands.

## 11. Risks and technical debt

Tracked as GitHub issues on `mikeng-io/nora` rather than in this file, so that
an item has one home with its scoping and status. Two standing entries worth
naming here because they shape reading: the knowledge tier (the "fact tier") and
the language tier were **deprecated 2026-09-07** and their code deleted (issue
#115), and the note tier's entity-resolution chain and the curator's merge half
were **deprecated 2026-09-08**. Their database tables are deliberately not
dropped. Design documents describing them carry `DEPRECATED` banners, and their
decision-index rows are marked rather than renumbered.

## 12. Glossary

`GLOSSARY.md`.

## Appendix — where new code goes

From spec §10. Rows marked **(target)** name a package that does not exist yet
in this repo — they describe where the pipelines/agent/platforms phases will put
that kind of code, not a path you can use today.

| I want to… | Touch only |
|---|---|
| add a platform | `platform/<name>/` following `platform/signal/`'s shape (a `PlatformAdapter` implementation), one `[platforms.<name>]` config block, and one more `if platforms.<name>.enabled:` block in `nora/platform/registry.py::AdapterRegistry.from_config`. Discord (DEC-102) is the worked example. Four things are easy to forget because they are NOT on `PlatformAdapter`: a `RosterSource` (room-scoped tools go blind without one, honestly but silently), a `MediaSource` (attachment custody), the per-platform entry in `/health`'s `platforms` block, and `identity.toml`'s id-kind vocabulary in `nora.domain.identity`. |
| add a native tool | `nora/agent/tools.py`: write the closure (docstring is the model-facing schema), add its `NATIVE_TOOLS` row (`budget`, `read_only`, `when`, `terminal`, `time_shiftable`, side-effect fields — `_spec()` copies them, so a new tool cannot silently take a default) and a `_spec()` line in `build_tool_specs`'s return tuple. `nora/agent/registry.py::ToolRegistry` picks it up automatically: `budgets()`, `build()`, `read_only_tools()` and `capability()` all derive from the table, so `ReplyGuard` gets the budget and the Dreamer's ceiling learns whether it may hold the tool without any change elsewhere (DEC-10, DEC-133). Then classify it in `tests/unit/test_dreamer_runtime.py::test_every_read_only_tool_is_classified_workspace_safe_or_room_scoped`, which goes red by name until you do. |
| add an MCP server | `[tools.mcp.<name>]` in `config.toml` (`nora/agent/mcp.py::McpMount`), and list the server under a room's `tools = [...]` in `policy.toml` — per-conversation scope is the enforcement point (DEC-71). |
| add a Dreamer adaptor | `nora/language/<name>.py` implementing `nora.domain.ports.DreamAdaptor` (declare `tools` by NAME; the runtime refuses anything outside the read-only ceiling unless an `AdaptorGrant` names it), an objective file under `nora/dreamer/objectives/` (declared native in `tests/unit/test_dreamer_instructions.py`), an `AdaptorSchedule` and the append to `dream_adaptors` in `nora/app/composition.py`, and an `IMPLEMENTATIONS` entry in `tests/contract/test_dream_adaptor_contract.py`. Make `inputs()` return `""` when there is nothing to reason about — that is the fire condition, and it is what keeps the tier cheap. |
| let her speak on a new occasion | an `Event` factory in `nora/app/fire.py` and — if a stored row drives it — a `kind` in `event_registry`. NEVER a new caller of `deliver.build` (R-1b): every occasion is a fifth thing on the second path, not a third path. |
| change engagement decisions | `nora/ingest/policy.py::TriggerTextPolicy` (wraps `nora/tag.py::is_tagged`). A future classifier composes in as `CompositePolicy([TriggerTextPolicy, ClassifierPolicy])`, not a replacement — see A3. |
| change what version she reports | nothing in `nora/` — `scripts/gen_version_manifest.py` stamps it and the Dockerfile gate refuses a build whose stamp and `--build-arg NORA_VERSION` disagree. Build with `scripts/build-image.sh V3` (DEC-97). |
| add a persisted column | `nora/infra/sql/schema.py` (the `MetaData`/`Table` source of truth) + `uv run alembic revision --autogenerate`. **Caveat:** a revision touching `idx_messages_conv_ts` must be hand-written — it is excluded from autogenerate on purpose (see DEC-11), so autogenerate's output for it cannot be trusted. |
| add a behavioural rule | `design/09-decision-index.md` + a named test registered in `tests/test_acceptance_rules.py::RULE_TESTS`. |
