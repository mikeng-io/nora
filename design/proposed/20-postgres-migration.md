---
kind: design
title: "Nora — SQLite → PostgreSQL: one `nora` database on a private network"
description: "The record store is SQLAlchemy Core (portable), so the engine swap is small — but it is NOT free: `ts` overflows INT4 and crashes every write, the Alembic baseline's `datetime('now')` fails at CREATE TABLE, `lastrowid` is SQLite-only, and the DEC-11 parity gate is built on `PRAGMA`. This spec fixes those and puts every Nora-owned container on one private network, external services reached via `host.docker.internal`."
tags: [nora, design, postgres, sqlalchemy, migration, record-store, presence, networking]
timestamp: '2026-08-07'
status: proposed
---

# SQLite → PostgreSQL

Operator decision, 2026-08-07: migrate to PostgreSQL, in the same
`docker-compose.yml`, into **one database named `nora`**, with **every
Nora-owned container on a private `nora` network and every external service
reached via `host.docker.internal`** (operator direction). This targets the store
that **R-2 and A9 guarantee** — the migration must preserve the record exactly —
so it earns a spec and two reviews (Claude subagent audit + Kimi external,
2026-08-07), not a reflex.

**The first draft claimed "just a driver+URL swap." Both reviews refuted it:**

- **`ts` INT4 overflow is a crash.** `ts`/`quoted_ts` are `sa.Integer` → INT4;
  Signal's millisecond timestamps (~1.78 × 10¹²) overflow INT4's 2.1 × 10⁹
  ceiling. On SQLite `INTEGER` is 8-byte so it never showed; on Postgres **every
  message write raises `NumericValueOutOfRange`**, silently under R-5. (§2)
- **The Alembic baseline cannot replay on Postgres.** `0001_baseline.py:41` emits
  `DEFAULT (datetime('now'))`; Postgres has no `datetime()` and type-checks default
  expressions at `CREATE TABLE`, so the baseline **errors on the first migration**.
  It must be squashed to a Postgres-native baseline. (§2)
- The previous draft's `created_at` fix (`sa.func.now()`) was *itself* a DDL error
  (`now()` is `timestamptz`, no implicit cast into a `text` column). Correct render
  is `sa.text("now()::text")`. (§2)

The swap is small *in surface* and *not* free *in substance*. §2 is the list of
things that are genuinely not portable, each with its fix. Both reviews' full
finding sets are folded in below.

## 0. Why now, and why it is small in surface — corrected in the room

- **"Multi-day migration" was wrong. There is no data migration.** The live store
  is **41 rows, ~60 KB.** §5 scripts a one-time load; nothing is *moved*.
- **"It's complicated" was wrong. The store is SQLAlchemy Core.** The *queries*
  are portable by construction; what is not portable is the short, specific list
  in §2 — one a crash, one a chain-stopper.

The decisive positive, confirmed by both reviews: `audit.py` is full of **WAL
single-writer workarounds**. Every one exists because SQLite serialises writers;
**PostgreSQL MVCC removes the entire class**, so the migration *deletes* contention
logic. (Both reviews confirmed `prune_all`'s `rowcount` is safe on asyncpg — the
only real driver break is `lastrowid`.)

The trigger is the presence collector (design/19): a **separate entry point**
(`python -m nora.presence`) in the **same codebase** that must write a record a
stateless agent also reads. Two processes sharing a SQLite *file* across
containers is fragile; two entry points sharing one Postgres *database* on a
private network is ordinary.

## 1. Scope — an engine change behind a port, the non-portable fixes, one rename

- **The store *port* is unchanged.** Only the concrete engine changes — the seam
  that makes a backend swap a composition-root change. The Postgres store passes
  the SAME `tests/contract/` suite the SQLite one does; that is the correctness
  proof (CLAUDE.md port rule).
- **`sqlite+aiosqlite:///…` → `postgresql+asyncpg://nora:…@nora-db/nora`**.
  `uv add asyncpg` (never pip).
- **`engine.py` is rewritten, not "one function."** Kimi finding #5 is right: the
  module is SQLite-shaped end to end — `Path(db_path).parent.mkdir` (meaningless
  for a URL), the `journal_mode=WAL`/`busy_timeout`/`query_only` connect hooks, and
  the `mode=ro` read-only URI. All of it goes; the read-only path becomes a
  Postgres **role**, not a `mode=ro` engine (see §3).
- **Delete the WAL apparatus** in `audit.py` — `_note_busy`,
  `nora_store_busy_total`, the pragma hook. MVCC makes them dead code (both
  reviews confirmed the deletion list is complete).
- **Apply the §2 fixes.** Two are hard failures.
- **`AuditStore` → `RecordStore`** — its own commit within the branch.

## 2. The genuinely non-portable things — each with its fix

- **`ts` and `quoted_ts` → `sa.BigInteger` (BLOCKER — the crash).** A migration
  test inserts a Signal-magnitude `ts` and reads it back unchanged; reverting the
  type turns it red. Both reviews confirmed this is the **complete** overflow fix —
  the only other numeric columns are `id` (INT4 identity, bounded by the
  500/conversation retention) and `is_nora` (0/1).
- **Squash to a Postgres-native Alembic baseline (BLOCKER — the chain won't
  replay).** `0001_baseline.py:41` fails at `CREATE TABLE` on Postgres. Do **not**
  claim `0001…0004` replays. Replace the chain with a single baseline carrying
  BigInteger `ts`/`quoted_ts`, identity `id`, and the `now()::text` default. (The
  SQLite chain stays in git for the archived `.db`.)
- **`RETURNING`, not `lastrowid`.** asyncpg has no `lastrowid` (it is `None`). Use
  `insert(...).returning(MESSAGES.c.id)` + `result.scalar_one()`; the contract
  suite asserts the id round-trips to `recent`.
- **`created_at` default → `sa.text("now()::text")`.** Stays `TEXT`; only the
  default changes. `sa.func.now()` would be a DDL type error. **The format change
  is harmless because `created_at` is write-only** — no `TranscriptRow`/
  `UnattributedRow` field, never `SELECT`ed, ordering on `ts`/`id` only (both
  reviews verified by reference trace; the `created_at` hits in the Honcho client
  are a different column). No hedge — the fact is checked, not assumed.
- **`id` — `GENERATED BY DEFAULT AS IDENTITY`, `nullable=False`.** The SQLite
  `nullable=True` override (for `AUTOINCREMENT`'s `notnull=0`) is removed. **`BY
  DEFAULT`, not `ALWAYS`** — the §5 load inserts explicit ids, which `ALWAYS`
  rejects without `OVERRIDING SYSTEM VALUE`. Reseed after load with `ALTER TABLE
  messages ALTER COLUMN id RESTART WITH :max+1`. `sqlite_autoincrement` dropped.
  `id` stays INT4 (forever at message volume); the `ts` BigInteger / `id` INT4
  asymmetry is deliberate.
- **The schema-parity gate (DEC-11) — rebuilt, with an honest oracle note.** The
  current gate reads `PRAGMA index_xinfo` AND diffs a fresh-migrated DB against
  **the production `.db` file in the tree** — that production oracle catches *live*
  drift, and Postgres has no in-repo file. **Stated honestly (both reviews):** a
  checked-in `pg_dump` baseline is frozen and catches only drift-from-baseline, not
  live production drift; reaching *live* production for the dump now requires
  `docker exec nora-db pg_dump` (the DB is otherwise unreachable, §3). So the
  Postgres gate is **metadata + baseline parity**, and the live-production half is
  a `docker exec` job — named, not silently dropped. Normalisation is specified:
  `pg_dump --schema-only --no-owner --no-privileges`, strip `SET`/version-comment
  preamble, sort. It must still assert:
    - the **CHECK constraint** (`conversation_type IN ('group','dm')`) is present;
    - `idx_messages_conv_ts` is **`(conversation_id, ts DESC)`** — descending flag
      from the per-column **`indoption` DESC bit** (`indoption[i] & 1`), **not**
      equality to `1` (a `DESC NULLS FIRST` column is `1|2=3`, so `== 1` false-
      fails — both reviews confirmed the bitmask is the correct read) and **not**
      `information_schema` (which does not expose index column ordering);
    - **the same checks for `presence_fixes`** (design/19) — its
      `(circle_id, person_id, ts DESC)` index is the identical alembic-check-blind
      shape. The gate is parameterised over every table+index in `nora`.
  A deliberate drift (drop the CHECK, flip an index to ASC) must turn it red.
- **`idx_messages_conv_ts` stays hand-written**, authored in the squashed baseline,
  never autogenerated.

## 3. Compose — one `nora` network; external services via `host.docker.internal`

Operator direction, dead-simple: **every container Nora owns lives on a private
`nora` network; every service outside that compose is reached via
`host.docker.internal`.** `docker ps` + `ss -tlnp` confirm this is buildable —
**there is no blocker** — because the external services bind `0.0.0.0`:

| service | bind (verified `ss`) | how Nora reaches it |
|---|---|---|
| omniroute | `0.0.0.0:20128` | `host.docker.internal:20128` |
| honcho | `0.0.0.0:8000` | `host.docker.internal:8000` |
| langfuse | `0.0.0.0:3100` | `host.docker.internal:3100` |
| searxng | `127.0.0.1:8080` (own bridge) | `host.docker.internal:8080` *(note)* |
| firecrawl | `127.0.0.1:3002` (own bridge) | `host.docker.internal:3002` *(note)* |
| **signal-cli** | `127.0.0.1:18080` (loopback) | **on `nora`, by name `signal-cli:18080`** |

    networks:
      nora:                                  # private; every container Nora owns

    services:
      nora-db:     networks: [nora]          # no published port; by name nora-db:5432
      signal-cli:  networks: [nora]          # moved off host-net; by name signal-cli:18080
      nora:
        networks: [nora]
        extra_hosts: ["host.docker.internal:host-gateway"]
      presence:
        networks: [nora]
        extra_hosts: ["host.docker.internal:host-gateway"]

- **`nora-db` and `signal-cli` publish nothing** — reachable only on `nora`, by
  name. `nora-db` is off the host entirely (the "don't expose it" intent, by
  construction).
- **The five external services** are reached via `host.docker.internal:PORT`.
  `config.toml` base_urls change from `127.0.0.1:*` to `host.docker.internal:*` for
  those, and to `nora-db` / `signal-cli` for the two on-network services.
- **signal-cli is the ONE service that can't use `host.docker.internal`** (loopback
  bind, unreachable from a bridge). It doesn't need to — it is Nora's own
  container, so it co-networks and is reached by name. **This is the entire content
  of the "blocker" both reviews raised**, and it dissolves the moment signal-cli
  joins `nora`.
- **searxng/firecrawl publish `127.0.0.1`-scoped** today, so `host.docker.internal`
  reaches them only once those publishes widen to `0.0.0.0` (or they join a shared
  network). That is task #24's build, not this one — flagged so it is not a
  surprise.
- **Credentials name env vars, never inlined** (DEC-8).
- **A read-only Postgres role for the control plane** — a `GRANT SELECT`-only role;
  the write role is Nora's alone. This **replaces** `create_readonly_sql_engine`.

**Two real consequences — handled, not blockers:**

1. **signal-cli off host networking is a change to a load-bearing constraint
   (CLAUDE.md #3) — a ship-condition, verified live.** Its host-net loopback bind
   existed so its no-auth HTTP stayed off `0.0.0.0` behind a port-map (which
   disables its host-header check). On `nora` **with no published port**, it binds
   a netns reachable *only* by `nora`/`presence` — a *stricter* trust set than
   host-loopback (any host process reaches loopback), and the host-header check
   guards browser DNS-rebinding that can't reach an internal-only network. Verify
   live: signal-cli serves on the bridge AND `--receive-mode=on-start` still opens
   its websocket (`nora_messages_total` advances, `sse.last_event_age_seconds`
   low). Rewrite the compose comment from "loopback keeps it" to "private network,
   no published port, keeps it".
2. **The host-side operator CLI loses its DB path (Kimi #5).** `nora/app/cli.py`
   (`nora groups`, `nora persons pending`) runs on the host via
   `create_readonly_sql_engine` against the DB *file*. With `nora-db` bridge-only,
   a host process has no route. Fix: run the CLI via **`docker exec` into a
   `nora`-network container** (or ship it as its own `nora`-network entrypoint),
   connecting as the read-only role to `nora-db:5432`. The operator must know this
   is how the control plane runs after cutover.

Also enumerated (both reviews): the metric/health `:9100` is served in-container
and the container healthcheck curls `127.0.0.1:9100` — fine bridged. But
`deploy/watchdog.sh` curls `127.0.0.1:9100` **from the host** — the deployment's
real SSE-silence detector. If the watchdog stays on the host, publish
`127.0.0.1:9100:9100`; else move it onto `nora`. Named so it is not the next
silent gap.

## 4. Test isolation — every temp `.db` becomes a temp Postgres

- A compose test service or a fixture-spun instance (testcontainers). Only
  `pytest -q` touches a DB; `ruff`/`mypy`/`lint-imports` are static (the earlier
  "four gates run against Postgres" phrasing was sloppy).
- **Per-test isolation is a fresh database (or schema) per test**, dropped after —
  never a shared mutable DB.
- The `tests/contract/` suite runs the SAME cases against the Postgres store.

## 5. Cutover and rollback — 41 rows, not hand-waved

- **Load:** insert 41 rows with **ids preserved** (legal under `BY DEFAULT`
  identity), then `ALTER TABLE messages ALTER COLUMN id RESTART WITH :max+1`.
  Verified by a post-load insert landing at `MAX+1`.
- **Order:** build `nora-db`; run the squashed baseline; run the load; bring Nora
  up against Postgres; verify live (`stats()` reads 41, a real tag round-trips, a
  Signal-magnitude `ts` writes without overflow, signal-cli reachable on `nora`).
  **Read the state back — never claim cutover from exit 0.**
- **Rollback:** the SQLite `data/messages.db` is moved aside, intact, until
  Postgres has run clean long enough to retire it. Rollback is repointing the URL
  and reverting the branch.

## 6. What must be true before it ships

1. `ts`/`quoted_ts` are BigInteger; a Signal-magnitude timestamp round-trips and
   reverting the type turns a test red. **(Blocker.)**
2. The chain is squashed to a Postgres-native baseline that builds on fresh
   Postgres (no `datetime('now')`). **(Blocker.)**
3. `append` uses `RETURNING`; the id round-trips via the contract suite.
4. `created_at` default is `now()::text`; `id` is `BY DEFAULT` identity,
   `nullable=False`, reseeded with `RESTART WITH`; no `sqlite_autoincrement`, no
   nullable-PK override; schema.py comments describe the Postgres shape.
5. The Postgres store passes the same `tests/contract/` suite.
6. The parity gate is rebuilt on `pg_index`/`indoption` + normalised `pg_dump`,
   covers `messages` **and** `presence_fixes`, still catches the CHECK constraint
   and `ts DESC`, a deliberate drift turns it red, and its metadata+baseline (vs
   `docker exec` live-production) scope is stated honestly.
7. `idx_messages_conv_ts` is hand-written in the baseline.
8. Every WAL workaround is deleted; a probe shows writers no longer serialise.
9. `AuditStore→RecordStore` is a separate, complete commit; GLOSSARY updated.
10. Tests run against real Postgres with per-test isolation; gates green.
11. Compose has the `nora` network; `nora-db` and `signal-cli` on it with **no
    published ports**; Nora+collector reach the five external services via
    `host.docker.internal`; a read-only role replaces `create_readonly_sql_engine`;
    the operator CLI runs via `docker exec`; the watchdog's `:9100` path is
    resolved. **The signal-cli move is verified live** (serves on the bridge AND
    the receive-mode websocket opens). Rebuilt and read back.
12. The 41 rows load with ids preserved and reseeded, verified by a post-load
    insert at `MAX+1`. The old `.db` is moved aside, not deleted.

## 7. Decision-index entries this creates

DEC-50 is already claimed by the `skills` activation (shipped 2026-08-07, commit
`cc97520`) — these continue from DEC-51:

- **DEC-51** — the record store is Postgres; the migration fixed the non-portable
  defects the "driver swap" framing hid (INT4 `ts` overflow, the `datetime('now')`
  baseline, `lastrowid`). Criteria 1–4.
- **DEC-52** — every Nora-owned container (`nora`, `presence`, `nora-db`,
  `signal-cli`) shares a private `nora` network; `nora-db` and `signal-cli` publish
  nothing; external services are reached via `host.docker.internal`; signal-cli is
  moved off host networking (verified live); the operator CLI runs via `docker
  exec`. Criterion 11.
- **DEC-53** — DEC-11's parity gate is rebuilt on Postgres catalogs (`indoption`
  DESC bit), covers every table in `nora`, and its oracle scope is honestly
  metadata+baseline with a `docker exec` live-production half. Criterion 6.
- Each gets a RULE test in `tests/test_acceptance_rules.py::RULE_TESTS`.
