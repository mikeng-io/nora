# Stack Refactor Brief — Nora v2 persistence & observability

> Handoff brief for Claude Code. Miles drives directly. Decisions here are
> already made (with rationale); do not re-litigate without asking Miles.

## 1. Goal

Nora's SQLite persistence, config, metrics, and logging currently hand-roll
what mature libraries already provide. Replace with standard libraries so
schema changes and maintenance stay cheap. **The core pain: changing the
schema today means hand-editing `store/migrate.py` — a one-shot v1→v2 rebuild.
Miles wants a real migration system now.**

## 2. Scope

| # | Replace | With | Why |
|---|---|---|---|
| 1 | `nora/store/db.py` + `nora/store/migrate.py` (raw aiosqlite + one-shot migration) | SQLAlchemy **Core** + Alembic — **or** Tortoise ORM 1.x native migrations (see §3, OPEN) | Real migration management: history, autogenerate, drift check |
| 2 | `nora/obs/metrics.py` (138-line hand-rolled Prometheus) | `prometheus-client` (official) | Escaping/thread-safety/histogram render free; our version has edge-case bugs |
| 3 | `nora/config.py` (201-line hand-rolled strict loader) | `pydantic-settings` (already in dep tree via Strands) | Zero new cost; `extra='forbid'` keeps strictness; env override free |
| 4 | `nora/obs/logging.py` JSON formatter + redaction | `structlog` (stdlib bridge) | JSON render/time/level processors free; secret redaction stays custom |
| 5 | `nora/persons.py` validation layer | pydantic models (piggyback on #3) | Identity-mapping domain logic stays custom; validation moves |

Keep as-is (already standard): aiohttp, opentelemetry-sdk, openai, Strands,
PyYAML, pytest stack, ruff.

## 3. OPEN DECISION — ORM (Miles must confirm before implementation)

**Recommendation: SQLAlchemy Core + Alembic.**
- Alembic: 10+ years production history; documented SQLite batch mode
  (`render_as_batch=True` handles SQLite's ALTER limitations); `alembic check`
  for CI drift detection.
- Core, not ORM: `store/db.py` is deliberately dumb ("No semantics live
  here" — `db.py:3`). Core keeps Table/Column metadata for autogenerate
  without session/Unit-of-Work ceremony.

**Rejected: Tortoise ORM 1.x native migrations.** Async-native and fastest
async ORM on SQLite, but native migrations shipped Feb 2026 — 6 months old,
with real bugs fixed only in recent releases (tortoise-orm#2068: `Meta.indexes`
AttributeError, broken `SQLOperation`). For a migration tool the maturity
horizon is the deciding factor.

**If Miles says Tortoise:** accept the young-migration risk; baseline with
`--fake`/stamp; verify `tortoise makemigrations --check` clean.

## 4. Current state & touch points (verified 2026-08-04)

DB: SQLite at `data/messages.db` (from config `buffer.db_path`), **0 rows**,
v2 schema (platform/conversation_id/conversation_type/delivery_status),
WAL mode, `PRAGMA user_version=1`.

Production call sites:
- `nora/__main__.py:51` — `db.init(cfg.buffer.db_path)` startup
- `nora/__main__.py:95` — health app gets `db_path`
- `nora/handler.py:70` — ambient `db.append`
- `nora/handler.py:105` — tag transcript `db.recent`
- `nora/agent/tools.py:44` — read_buffer tool
- `nora/agent/guards.py:132,148` — reply audit append + delivery status
- `nora/obs/health.py:53` — `db.stats()`
- `nora/models.py:47` — `TagContext.transcript` type

Tests touching db: `tests/test_main.py`, `tests/test_guards_deliverer.py`,
`tests/test_tools.py`, `tests/test_migration.py`.

## 5. Migration baseline strategy

Production DB exists (0 rows, v2 schema). Do **not** drop/rebuild:
1. Write the initial migration matching current v2 schema.
2. **Stamp/fake it as applied** (Alembic: `alembic stamp head`; Tortoise:
   equivalent) so the existing file is the baseline and audit trail survives.
3. `migrate.py` is retired (its v1→v2 job is done; history lives in git).
   Delete it. Its one-time logic must not run again — `db.init` must no
   longer call it.

## 6. CA/DDD minimal (only what earns its place)

- Domain entity: Tortoise/SQLAlchemy model for `messages` — single source of
  truth for schema.
- `store/db.py` keeps its facade functions (`append`, `recent`, `stats`,
  `set_delivery_status`, `prune`) so application code (`handler`, `tools`,
  `guards`, `health`) does not change semantics.
- No aggregates/repositories/domain events. One table, one store — ceremony
  is waste at this scale.
- Call-site signatures may change (e.g. drop `path` param once a global
  engine/pool exists) — update all call sites in the same change; grep to
  verify zero stragglers.

## 7. Constraints & pitfalls (learned, do not regress)

- **Container build requires `--network=host`** — BuildKit's default bridge
  can't resolve deb.debian.org on this host (systemd-resolved stub
  `127.0.0.53`); `apt-get update` stalls ~30s/repo. Already noted in
  `docker-compose.yml` header. Rebuild with `docker build --network=host`.
- **Health probe fixed recently** (`nora/store/honcho.py:114`): Honcho health
  uses `GET /health`, not `GET /v3/workspaces` (POST-only → 405 → forever
  `health:starting`). Don't regress.
- **signal-cli daemon (PID 4164282) is neutral/shared** — never touch it;
  it's not owned by Nora, logs to a shell redirect artifact only.
- Legacy Hermes gateway is archived at
  `/home/<deploy-user>/backups/nora-legacy-profile-20260804/` — do not reference the
  old `~/.hermes/profiles/nora/` path.
- Deps are managed by `uv` (`pyproject.toml` + `uv.lock`, `uv sync --frozen`
  in Dockerfile). Add new deps via `uv add`, not raw pip.
- Tests: `addopts = "-m 'not network'"` — network-marked tests excluded by
  default; DB tests should use in-memory SQLite where possible.

## 8. Verification gates (per phase, before moving on)

1. `pytest` green (rewrite `test_migration.py` for the new migration system;
   keep behavior assertions identical).
2. `ruff check` clean.
3. Migration drift check clean (`alembic check` / `makemigrations --check`).
4. Container rebuild (`--network=host`) + `docker compose up -d --no-build`.
5. `curl http://127.0.0.1:9100/health` → HTTP 200, `sqlite.reachable: true`.
6. `git log` clean, one concern per commit.

## 9. Suggested phase order

1. **Persistence swap** (ORM + migrations + baseline stamp) — biggest risk,
   own verification cycle.
2. **Config + persons validation** (pydantic-settings).
3. **Metrics** (prometheus-client).
4. **Logging** (structlog).

Each phase: commit → tests green → container healthy → report to Miles.
