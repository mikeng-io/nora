# Resume prompt — Nora architecture refactor

Paste the block below into a fresh Claude Code session in `~/workspace/nora`.
It is self-contained: it names the state, the rules, and the next unit of work.

---

```
Continue the Nora architecture refactor. Read these first, in order:

  docs/superpowers/specs/2026-08-04-nora-architecture-design.md   (revision 2 — the controlling spec)
  ARCHITECTURE.md                                                  (the map)
  CLAUDE.md                                                        (gates + operational constraints)
  design/09-decision-index.md                                      (every D/A/R/DEC identifier)

DONE and merged to master: Phase 0 (prep), Phase 1a (config -> pydantic + TOML),
Phase 1b (domain layer, ports, mechanical enforcement).
Baseline: 483 passed / 1 deselected / 1 xfailed.

REMAINING, in order — spec §8:
  2  Persistence: SQLAlchemy Core + Alembic behind RecordStore, baseline stamp,
     retire store/migrate.py, rewrite test_migration.py.   RISK: HIGH
  3  Pipelines: ingest/ + engagement/, Handler dissolved, evals stop importing
     tests/.                                                RISK: HIGHEST
  4  Platforms: registry, resolve_identity(), PlatformIdentity.aliases, SendResult
  5  Agent: tool registry, ToolSpec, DEC-10 ReplyGuard budgets from the registry
  6  Obs: structlog, prometheus-client, OTLP -> Langfuse (:3100)
  7  Langfuse datasets + scores

METHOD — follow it for every phase:
  1. Write a plan to docs/superpowers/plans/YYYY-MM-DD-nora-phaseN-*.md using the
     superpowers:writing-plans skill. Bite-sized TDD tasks, real code in every step,
     no placeholders. Commit the plan.
  2. Execute with superpowers:subagent-driven-development on a branch
     refactor/phaseN-*: fresh implementer per task, task review after each, one
     final whole-branch review on the most capable model, one fix wave, one scoped
     re-review. Keep a ledger at .superpowers/sdd/<plan-basename>/progress.md.
  3. Verify the load-bearing claims YOURSELF between tasks — do not take a
     subagent's word for a gate being green.
  4. Merge to master with --no-ff, tag the image nora:pN, delete the branch.

NON-NEGOTIABLES:
  - Every gate must be OBSERVED FAILING against a deliberate probe before you
    commit it. A gate never seen to fail is not a gate. This project has already
    shipped enforcement that silently did nothing — twice.
  - Per-phase gates: uv run pytest -q · ruff check nora/ evals/ tests/ ·
    mypy nora/domain nora/config.py nora/app · lint-imports ·
    docker build --network=host && docker compose up -d --no-build &&
    curl :9100/health -> 200 with sqlite.reachable true AND a top-level sse key.
  - docker build REQUIRES --network=host (BuildKit cannot resolve deb.debian.org here).
  - The signal-cli daemon is neutral and shared — NEVER touch it.
  - Honcho health is GET /health, never GET /v3/workspaces.
  - Dependencies via `uv add`, never pip.
  - Metric names are append-only; /health JSON is additive only (deploy/watchdog.sh
    parses body['sse']['last_event_age_seconds']).
  - nora/domain/ imports only the standard library. Enforced; keep it that way.

TRAPS ALREADY PAID FOR — do not rediscover these:
  - `alembic check` FAILS on the production DB while PASSING in CI: SQLite's
    `id INTEGER PRIMARY KEY AUTOINCREMENT` has notnull=0, but SQLAlchemy's
    primary_key=True implies nullable=False. Declare
    Column("id", Integer, primary_key=True, autoincrement=True, nullable=True).
  - `alembic check` is BLIND to the CHECK constraint, the ts DESC index ordering,
    and server defaults, and reports permanent phantom drift on the expression
    index. It is NOT the schema gate — write a schema-parity test that asserts
    against sqlite_master.sql and PRAGMA index_xinfo.
  - Set sqlite_autoincrement=True or every fresh test DB diverges from production.
  - Run alembic check against a COPY of data/messages.db, never only a fresh DB.
  - ReplyGuard.BUDGETS (guards.py) is a hardcoded allowlist that vetoes ANY unknown
    tool under R-7. It must take budgets from the ToolRegistry (DEC-10) or every
    new tool is rejected before it runs.
  - tests/conftest.py has an autouse METRICS.reset() fixture; prometheus_client's
    CollectorRegistry has no reset(). Change it in the SAME commit as the metrics
    swap or all tests fail to COLLECT.
  - opentelemetry-exporter-otlp is NOT installed and obs/tracing.py imports the
    gRPC exporter, but Langfuse's endpoint is HTTP/protobuf. Phase 6 needs a new
    dependency, a new exporter branch, and a headers_env config field.
  - db.append runs an INSERT plus a correlated-subquery prune in ONE transaction
    per message; under WAL + N adapters that yields SQLITE_BUSY and silent audit
    loss. Make append a bare INSERT and prune periodically.
  - HonchoClient.ensure_session/ensure_peer are check-then-act; two concurrent
    misses create two sessions for one peer. Per-peer asyncio.Lock.
  - An import-linter `forbidden` contract silently no-ops when one module is a
    subpackage of the other. Use as_packages = false for those.
  - mypy 2.3.0 leaks `strict = true` from [[tool.mypy.overrides]] to every module
    in the run. pyproject.toml hand-expands the 13 flags; re-diff on each upgrade.
  - nora/agent/prefill.py has two KNOWN mypy errors under full `mypy nora` —
    TagContext.transcript is typed TranscriptRow while db.py still returns dicts.
    Phase 2 closes this.

OPEN WORK LIST for the platforms phase (pinned in ARCHITECTURE.md + tests/contract/):
  SignalAdapter vs PlatformAdapter — 4 gaps: no resolve_identity(); send() returns
  bool not SendResult; health() returns dict not AdapterHealth; listen() takes a
  positional raw-dict callback, not keyword-only on_message.

CONFIG-IMPORT OFFENDERS for the pipelines phase (import-linter has one documented
ignore_imports for the first):
  nora/agent/factory.py (literal import), nora/handler.py, nora/obs/logging.py,
  nora/obs/tracing.py (config-shaped params). obs/health.py is NOT one.

Start with Phase 2. Report at each phase boundary with verification evidence,
then continue without waiting.
```
