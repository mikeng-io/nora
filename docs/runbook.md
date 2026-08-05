# Nora operator runbook

Five scenarios an operator actually hits, each with the exact command to run.

## 1. "Nora is silent — is she broken or just quiet?"

Silence is the default behavior (S1) — Nora only ever speaks when tagged, so most silence
is correct. To tell "quiet" from "broken":

1. Check the silent failure rate: `nora_silent_failures_total / nora_tags_total`, scraped
   from `GET /metrics`. **Target < 1%** (spec E.6) — this is the escape hatch for
   "invisible when broken": failures stay invisible to the group but must stay *visible*
   to the operator. A sustained rise above 1% means tags are failing, not that nobody
   tagged her.

   ```bash
   curl -s http://127.0.0.1:9100/metrics | grep -E 'nora_silent_failures_total|nora_tags_total'
   ```

2. Check `GET /health` — `200` means healthy, `503` means degraded:

   ```bash
   curl -s http://127.0.0.1:9100/health | python -m json.tool
   ```

   Look at `sse.connected`, `sqlite.reachable`, `honcho.reachable`, and
   `omniroute.last_success_at`. Any of those false/stale explains real silence: the SSE
   stream dropped, the DB is unreachable, Honcho is down, or the LLM hasn't answered
   recently.

If both the silent-failure rate is low and `/health` is `200`, she's just quiet — nobody
has tagged her.

## 2. "A persona edit broke her"

Persona files (`nora/persona/*.md`) are hot-reloaded on every tag when
`persona.reload: mtime` — editing them never needs a restart. But a bad edit (broken
frontmatter, missing required key, unparseable YAML) fails to parse.

1. Check the reload-failure counter:

   ```bash
   curl -s http://127.0.0.1:9100/metrics | grep nora_persona_reload_failures_total
   ```

2. Confirm it in the logs:

   ```bash
   grep persona_reload_failed data/nora.log
   ```

   A failed reparse **keeps the last good persona** — Nora keeps replying with the
   previous, working version, not with a crash or a half-parsed persona. The counter and
   log line are how you find out a bad edit is sitting there even though nothing looked
   broken from the group's side.

3. Fix it: revert the file to the last-good version (`git checkout -- nora/persona/<file>.md`
   or restore your edit). **No restart needed** — the next tag re-stats the directory,
   sees the corrected mtime, and reparses successfully.

## 3. "Rollback"

Nora runs as a **Docker container**, not under systemd — the container is built from
images tagged per phase (`nora:p0`, `nora:p1a`, …). Rolling back one phase is a
two-command retag, no rebuild and no git checkout required:

```bash
docker tag nora:p0 nora:latest
docker compose up -d --no-build
```

`OMNIROUTE_API_KEY` must be available for the container to start, but nothing needs to
be exported by hand — it comes from the gitignored `.env` file in the repo root, which
`docker compose` reads automatically.

### Rolling further back, to the pre-rewrite v1 codebase

A phase retag only reaches back as far as the images that were built. Going all the way
back to the pre-Strands-rewrite v1 codebase means checking out the old source and
rebuilding, not just swapping tags — this needs network access and takes minutes, not
seconds:

```bash
docker compose stop nora
git checkout master
cp data/messages.db.v1.bak data/messages.db      # restore the pre-migration v1 database
docker build --network=host -t nora:latest .     # plain `docker compose build`/`up --build`
                                                  # stalls: the default BuildKit bridge can't
                                                  # resolve deb.debian.org on this host
docker compose up -d --no-build
```

`git checkout master` returns the working tree to the v1 codebase. The v2 migration
(the schema Alembic now manages, previously the retired `nora.store.db.init`) is additive,
so `messages.db` after v2 has run may contain columns/rows the v1 binary doesn't expect —
restore `messages.db.v1.bak` (the backup taken before the v1→v2 schema migration, per
Task 44 Step 1) rather than pointing v1 at a v2-migrated file.

### Rolling back the persistence phase (Alembic)

The persistence phase replaced the additive `nora/store/db.py` migration with versioned
Alembic revisions. Rolling back *this* phase specifically — not all the way to v1 — is:

```bash
docker compose stop nora
git revert <persistence-phase-merge-commit>
docker build --network=host -t nora:latest .
docker compose up -d --no-build
sqlite3 data/messages.db "DROP TABLE alembic_version"
```

Do **not** clear `PRAGMA user_version` — it is still `1` from the pre-Alembic era, and
leaving it untouched is what makes this rollback safe: if the reverted (pre-Alembic)
binary's old migration path ever runs again, it reads `user_version == 1`, sees the schema
it expects, and short-circuits instead of trying to re-migrate a database Alembic already
brought to head. Dropping `alembic_version` is enough on its own to undo the phase, because
it was the only new object this phase added — see
`tests/test_migration.py::test_the_alembic_version_table_is_the_only_new_object`.

## 4. "The watchdog alerted"

`deploy/watchdog.sh` polls `/health` and alerts if the SSE listener has been silent for more
than 300 seconds (no ingest event in that window). This same script also ships with the
bare-metal/systemd deployment path documented in `deploy/README.md`, but the live instance
runs as a **Docker container**, not under systemd — the commands below are for that
container.

1. Check signal-cli first — it's the more common cause:

   ```bash
   curl -i http://127.0.0.1:18080/api/v1/check
   ```

   If that's not a healthy 200, restart or investigate signal-cli itself before touching
   Nora.

2. If signal-cli looks fine, check Nora's own logs:

   ```bash
   docker logs nora --tail 100 -f
   ```

   Look for reconnect-backoff messages, unhandled exceptions in the SSE listen loop, or a
   process restart. SSE reconnects automatically with exponential backoff, so a brief blip
   self-heals; a sustained >300s silence with `sse.connected: false` in `/health` after that
   means the reconnect loop itself is stuck and the container likely needs a restart:

   ```bash
   docker compose restart nora
   ```

## 5. "Which persona produced this reply?"

Every reply's log line and trace carry `persona_sha256` — the sha256 of the persona version
active for that specific tag, computed at `PersonaStore.get()` time. Find the tag by its
`tag_id` (or by time) and read the hash straight off that line:

```bash
grep persona_sha256 data/nora.log | tail -1
```

The same value is also reported live in `GET /health` (`persona_sha256` field) and as an
OpenTelemetry span attribute, so you can confirm whether the *current* running persona
matches the one that produced a given historical reply, or whether it has since been
hot-reloaded to something else.

## Note: logger/message names changed in the pipelines refactor (2026-08-05)

`nora/handler.py` is deleted; its logging moved with it. If you have log-based alerting
or saved `grep`/dashboard queries built against the pre-refactor names, they will
silently stop matching — update them:

- **Logger name**: `nora.handler` → per-stage loggers instead of one monolith, e.g.
  `nora.ingest.audit`, `nora.ingest.gate`, `nora.engagement.pipeline`,
  `nora.engagement.admission`, `nora.engagement.context`, `nora.engagement.deliver`,
  `nora.engagement.invoke`, `nora.app.runtime`. `grep '"logger": "nora.handler"'` now
  matches nothing; grep `"logger": "nora\.` (the JSON log line's `logger` field) instead
  of a single fixed name.
- **Message text**: the ambient audit-write failure log line changed from
  `"db append failed"` to `"audit append failed"` (`nora/ingest/stages/audit.py:24`).
  Update any saved query matching the old string.

Metrics are unaffected by this — `nora_errors_total{stage="ambient"}` and every other
series kept both their names and their label values; this note is about log-text
alerting specifically.

## 6. Langfuse tracing

**Status as of 2026-08-05: ENABLED and verified end-to-end.** `config.toml` has
`exporter = "otlp_http"` pointed at `http://127.0.0.1:3100/api/public/otel/v1/traces`,
with `headers_env = "LANGFUSE_OTLP_HEADERS"`. The credential lives in the gitignored
`.env` and is passed to the container by `docker-compose.yml`.

**How it was verified** (do the same after any change here — do not assume):
1. `POST /api/public/otel/v1/traces` with the auth header → **200** (it is **401** without).
2. A span emitted through Nora's own `setup_tracing()` appeared in Langfuse as
   `nora-phase6-smoke`. Traces are listable via
   `GET /api/public/traces?limit=5` with the same Basic header.

**Design points worth not breaking:**
- The exporter is **HTTP/protobuf**, not gRPC. Langfuse does not speak the gRPC OTLP
  protocol, and the gRPC exporter package is not installed.
- `headers_env` names an environment **variable**, never the value (DEC-8). There is no
  `${VAR}` interpolation in this config system.
- Compose passes `LANGFUSE_OTLP_HEADERS` with `:-` (optional), not `:?` (required),
  because **tracing is never load-bearing**: an unset credential must degrade to
  no-auth, not refuse to boot. `test_otlp_http_setup_does_not_crash_on_an_unreachable_endpoint`
  pins the same property for a dead collector.
- `trace_attributes` emits **provenance only, never message content** — there is a
  dedicated test. `persona_sha256` on every span is DEC-5's trace-to-prompt linkage.

**If traces stop appearing:** check the container log first. A 401 or a connection
refusal is logged there and nowhere else, precisely because tracing failures are
non-fatal — so the symptom is silence, not an error.

**Rollback:** set `exporter = "file"` in `config.toml` and restart. No data migration,
no schema change.
