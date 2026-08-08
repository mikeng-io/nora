# Nora operator runbook

Five scenarios an operator actually hits, each with the exact command to run.

## 1. "Nora is silent — is she broken or just quiet?"

Silence is the default behavior (S1) — Nora only ever speaks when tagged, so most silence
is correct. To tell "quiet" from "broken":

1. Check the silent failure rate:
   `nora_silent_failures_total{audience="tag"} / nora_tags_total`, scraped
   from `GET /metrics`. **Target < 1%** (spec E.6) — this is the escape hatch for
   "invisible when broken": failures stay invisible to the group but must stay *visible*
   to the operator. A sustained rise above 1% means tags are failing, not that nobody
   tagged her.

   **The `audience="tag"` filter is load-bearing, not tidiness.** The heartbeat's
   fire path (reminders) shares this series and is labelled `audience="heartbeat"`;
   unfiltered, its failures are charged to a <1% budget whose denominator counts
   only tags, so a broken heartbeat reads as a broken conversation path and a
   handful of failed reminders can blow an alarm nobody can then diagnose. The
   heartbeat's own health is a separate expression —
   `nora_silent_failures_total{audience="heartbeat"}` alongside
   `nora_reminders_fired_total{outcome}` and `nora_heartbeat_last_tick_age_seconds`.

   **Those two can disagree, and the disagreement is a reading, not a bug.** A
   reminder that delivered message 1 of 3 and then timed out increments
   `nora_silent_failures_total{audience="heartbeat",reason="invocation_timeout"}`
   *and* resolves `nora_reminders_fired_total{outcome="sent"}` — because both are
   true: the invocation was abandoned, and the group did hear something.
   `nora_partial_replies_total{reason="invocation_failed"}` is the series that
   says which one it was. Every increment there is a message the group was meant
   to receive, so read it whenever the two above do not agree:

   ```bash
   curl -s http://127.0.0.1:9100/metrics | grep nora_partial_replies_total
   ```

   ```bash
   curl -s http://127.0.0.1:9100/metrics \
     | grep -E 'nora_silent_failures_total\{audience="tag"|nora_tags_total'
   ```

2. Check `GET /health` — `200` means healthy, `503` means degraded:

   ```bash
   curl -s http://127.0.0.1:9100/health | python -m json.tool
   ```

   Look at `sse.connected`, `store.reachable`, `honcho.reachable`, and
   `omniroute.last_success_at`. Any of those false/stale explains real silence: the SSE
   stream dropped, the DB is unreachable, Honcho is down, or the LLM hasn't answered
   recently.

3. Check whether she is **refusing on purpose**. Deny-by-default means a correct,
   healthy Nora can be completely silent because `policy.toml` does not list the room or
   the person. Both refusals are counted, so this is one command:

   ```bash
   curl -s http://127.0.0.1:9100/metrics | grep -E 'nora_denied_(conversations|senders)_total'
   ```

   - `nora_denied_conversations_total` rising → the conversation is not in `policy.toml`.
     Nothing from it is recorded either (the room gate runs before the audit write), so
     the audit DB will show no trace of it at all. Run `nora groups` to see the group's
     name beside the id you need to add.
   - `nora_denied_senders_total` rising → the room is allowed but the sender is not in
     that entry's `persons` list. The message **is** audited, so it is visible in
     `messages`; it just was not answered.

4. Check whether she is answering but **remembering nothing**:

   ```bash
   curl -s http://127.0.0.1:9100/metrics | grep nora_unmapped_senders_total
   ```

   A rising count means senders are resolving to no person in `identity.toml`. This one
   is deceptive: Nora still replies and still addresses them by their Signal profile
   name, so she looks completely normal while writing nothing to Honcho. If you have
   edited `identity.toml`, **restart her** — mapping is startup-only, so an edit alone
   changes nothing.

5. Check whether she still knows **who she is**. This one does not make her silent — it
   makes her answer *herself*, which is worse:

   ```bash
   curl -s http://127.0.0.1:9100/metrics | grep -E 'nora_identity_aliases|nora_identity_enrichment_degraded_total'
   ```

   `nora_identity_aliases{platform="signal"}` is the number of id forms Nora recognises
   as her own. **Alert on `== 1`.** One means enrichment produced nothing beyond the
   phone number in `platforms.signal.account`, so R-12 ("never treat my own message as a
   tag") has collapsed from a set-membership test to a plain string equality — and a
   self-send arriving under her ACI or username would be handled as somebody else's tag.
   A healthy Signal start reads **4**: phone, ACI, username, PNI. The
   `..._degraded_total` counter's `reason` label says which half failed: `unreachable`
   (the daemon), `no_self_contact_row` or `no_phone_number_identity` (the daemon answered
   but not in the shape we parse — suspect a signal-cli upgrade).

   **This is a fixed regression, recorded because it ran for the deployment's whole
   life.** The old code read `GET /api/v1/accounts`, which signal-cli **has never
   served** — see "Facts that cost hours to learn" below — so `aliases` fell back to
   `{primary}` on every single start, with one `log.warning` and no metric. Both series
   above exist so that state can never be invisible again.

If the silent-failure rate is low, `/health` is `200`, neither denial counter is moving,
and `nora_identity_aliases` is above 1, she's just quiet — nobody has tagged her.

## 2. "A doctrine edit broke her"

Doctrine files (`nora/doctrine/*.md`) are hot-reloaded on every tag when
`doctrine.reload: mtime` — editing them never needs a restart. But a bad edit (broken
frontmatter, missing required key, unparseable YAML) fails to parse.

1. Check the reload-failure counter:

   ```bash
   curl -s http://127.0.0.1:9100/metrics | grep nora_doctrine_reload_failures_total
   ```

2. Confirm it in the logs:

   ```bash
   grep doctrine_reload_failed data/nora.log
   ```

   A failed reparse **keeps the last good doctrine** — Nora keeps replying with the
   previous, working version, not with a crash or a half-parsed doctrine. The counter and
   log line are how you find out a bad edit is sitting there even though nothing looked
   broken from the group's side.

3. Fix it: revert the file to the last-good version (`git checkout -- nora/doctrine/<file>.md`
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

> **Historical note (pre-Postgres):** the v1 path above is SQLite-era in full — v1 ran
> against `data/messages.db`, so restoring its `.bak` is still exactly right FOR v1.
> Everything recorded after the 2026-08-07 Postgres cutover (DEC-51) lives in the `db`
> service's volume and would not travel back with it.

### Rolling back the persistence phase (Alembic)

The persistence phase replaced the additive `nora/store/db.py` migration with versioned
Alembic revisions. Rolling back *this* phase specifically — not all the way to v1 — is:

```bash
docker compose stop nora
git revert <persistence-phase-merge-commit>
docker build --network=host -t nora:latest .
docker compose up -d --no-build
docker compose exec db psql -U nora -d nora -c 'DROP TABLE alembic_version'
```

> **Historical note (pre-Postgres):** this procedure was written for the SQLite era
> (`sqlite3 data/messages.db "DROP TABLE alembic_version"`, plus a warning not to clear
> `PRAGMA user_version`). The store moved to PostgreSQL on 2026-08-07 (DEC-51); the
> `user_version` guard was a SQLite concept and no longer applies. Rolling back past the
> Postgres cutover itself is a different operation: the old `data/messages.db` file is
> still on disk, untouched by the cutover, but everything recorded AFTER the cutover
> lives only in Postgres.

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

## 5. "Which doctrine produced this reply?"

Every reply's log line and trace carry `doctrine_sha256` — the sha256 of the doctrine version
active for that specific tag, computed at `DoctrineStore.get()` time. Find the tag by its
`tag_id` (or by time) and read the hash straight off that line:

```bash
grep doctrine_sha256 data/nora.log | tail -1
```

The same value is also reported live in `GET /health` (`doctrine_sha256` field) and as an
OpenTelemetry span attribute, so you can confirm whether the *current* running doctrine
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
  dedicated test. `doctrine_sha256` on every span is DEC-5's trace-to-prompt linkage.

**If traces stop appearing:** check the container log first. A 401 or a connection
refusal is logged there and nowhere else, precisely because tracing failures are
non-fatal — so the symptom is silence, not an error.

**Rollback:** set `exporter = "file"` in `config.toml` and restart. No data migration,
no schema change.

## 7. "Where do eval results live?"

**Langfuse, as of 2026-08-05 — verified end-to-end.** DEC-6: `strands-agents-evals`
stays the *runner*; Langfuse is the *store* for datasets, dataset runs and scores.

**Datasets** (one per corpus, seeded from the YAML in `evals/`):
`nora-trigger`, `nora-no-trigger`, `nora-adversarial` — 12 items each, keyed
`<corpus>/<case-name>`. Langfuse item IDs are unique **per project across all
datasets**, which is why the ids are namespaced; a bare case name would let one
corpus silently overwrite another's item.

**Exporting a run** needs **both** switches — belt and braces, so an ordinary local
eval never talks to the network:
```bash
uv run python -m evals.run --suite all --export-langfuse   # CLI flag
# and config.toml: [evals.langfuse] enabled = true
```
`config.example.toml` deliberately keeps `enabled = false`: a fresh install should
not assume a Langfuse exists.

**Reading results:** each run becomes a dataset run; each evaluator contributes a
BOOLEAN score (`exactly_once_send_reply`, `no_send_without_trigger`, `tool_budget`,
`no_redundant_read`, `honcho_fed_once_per_reply`), plus `a8_silent_failure` for the
A8 SLO. Langfuse's mean-of-run aggregation turns that last one into the
`<1% silent failure` ratio directly. Re-running with the same run name **updates in
place** rather than duplicating — ids are deterministic UUID5 over
`(run_id, item_id, name)`.

**The export can never fail the gate.** `NullEvalSink` is the default and
`LangfuseEvalSink` swallows every transport error; a run against an unreachable
Langfuse still prints its verdict and exits with the same code. That independence
is deliberate — an observability store must not be able to break CI.

**Known: `--suite all` currently reports GATE FAILED, and has since before this
work.** The `trigger` suite's live-model path is deferred (see `evals/run.py`'s
module docstring), so `ExactlyOnceSendReply` sees zero `send_reply` calls and
scores `false`. Confirmed identical on the pre-Phase-7 commit. The export is
working correctly — it is faithfully recording a real failing verdict.

**Two wire-format traps, both found only by pushing to the live instance:**
- `POST /api/public/dataset-run-items` returns **404** without a `traceId`. The
  sink now supplies a deterministic one.
- Scores go to `/api/public/scores`. `/api/public/v2/scores` exists but has **no
  POST route** (405). Both failures were silent, because the sink swallows errors
  by design — which is why the sink now logs the response body, not just the status.


## 8. "Who is this, and which group is that?" — the control plane

Two read-only commands. Neither writes a file, neither writes to the daemon,
and both work with the Nora container **stopped** — the 3am path is docker, an
editor and these. Neither needs `OMNIROUTE_API_KEY`; listing group ids has
nothing to do with the LLM.

```bash
uv run nora groups              # every group, its id, and whether policy allows it
uv run nora persons pending     # senders Nora could not map, with paste-ready stanzas
```

(`nora` is a console script declared in `pyproject.toml`; `uv run` is what puts
it on PATH from a bare shell. `uv run python -m nora …` is identical.)

**`nora groups`** answers "I cannot tell which group id is which". It prints
each group's name beside the base64 `conversation_id` you need for
`policy.toml`, marks it ALLOWED or DENIED, and emits a paste-ready
`[[conversations]]` block for the denied ones.

**`nora persons pending`** answers "who is this ACI?". It lists every sender in
the audit DB that maps to no person, correlated into **accounts** rather than
raw ids — under sealed sender the same account arrives as a phone number on one
message and an ACI on another, and filing those as two pending entries would
walk you straight into the duplicate-block startup error. Correlation uses the
group rosters as well as `listContacts`, which matters more than it sounds: on
this deployment only **3 of 83** contacts carry both a number and a uuid, while
every group member does.

**Adopting somebody is just editing the file.** Paste the stanza into
`identity.toml` and restart. Lazy adoption (DEC-16) then backfills everything
they said *before* you mapped them into their Honcho peer — Nora never writes
`identity.toml` herself (D6).

```bash
$ uv run nora groups
ALLOWED  Ridgeline
         id = Lf2L9zqeNUahRsOz6BA+sTB8aWfbuvqu9vOhpvmJi34=
         member=yes  members=3
         policy: labelled "Ridgeline", persons = ['miles']
```

If the daemon is unreachable, `groups` exits 1 with the two commands to run;
`pending` still works but loses display names and says so — with no roster to
correlate against, one person's ids may appear as several entries.

## 9. "Where does everything live?" (read this first, months later)

One tree. If it is not under `~/workspace/nora/`, Nora does not own it.

```
~/workspace/nora/
  docker-compose.yml        both services: nora + signal-cli
  deploy/signal-cli/        Dockerfile + fetch.sh (16K; the 115MB tarball is
                            fetched on demand, never committed)
  config.toml               deploy cadence: ports, models, timeouts
  identity.toml             WHO PEOPLE ARE. gitignored. One
                            [[persons.<id>.<platform>]] block per ACCOUNT — a
                            person may hold several on one platform, and they
                            all resolve to one person_id (= their Honcho peer).
  policy.toml               WHO MAY TALK TO HER, AND WHERE. gitignored, deny by
                            default. She REFUSES TO START if it is missing or
                            allows nothing — running mute would be
                            indistinguishable from working (R-5).
  data/                     Nora's own state — nora.log, media blobs; the record
                            store itself is the db service's Postgres volume
                            (bind-mounted into the nora container as /app/data)
  state/signal-cli/         SIGNAL ACCOUNT: identity keys, sessions, group and
                            contact caches. 1.4MB. gitignored.
```

**Three config files, three lifetimes, on purpose.** `config.toml` changes when the
deployment changes; `identity.toml` when a human joins; `policy.toml` when TRUST
changes. Keeping them apart is what stops an allowlist from being buried among port
numbers where nobody reviews it. Both `identity.toml` and `policy.toml` are
gitignored and bind-mounted read-only into the container.

**Editing either one requires a restart.** Mapping and policy are both loaded once at
startup (Q4). An edit alone changes nothing, and nothing warns you — this is the single
most likely way to lose an hour. `nora_unmapped_senders_total` is the tell.

**`data/` vs `state/` is deliberate.** Nora's container mounts `./data`; it does
NOT mount `./state`. She has no business reading Signal identity keys, so they
sit outside her reach.

**What is irreplaceable:** `state/signal-cli/data/` — that is the device
registration. Lose it and you re-link a device (QR scan from the primary phone).
Everything else regenerates: `stickers/` and `avatars/` are caches that were
7.6MB and 560KB and were pruned on 2026-08-05; signal-cli refetches them on
demand.

**Back up:**
```bash
tar czf ~/backups/nora-state-$(date +%Y%m%d).tar.gz -C ~/workspace/nora state data
```

**Restore:** stop the stack, extract over `state/`, start. The account is
identified by `state/signal-cli/data/accounts.json` plus the `892678*` files
beside it.

**Existing backup:** `/home/norty/backups/signal-cli-20260805-173329/signal-cli-data.tar.gz`
— taken *before* the 0.14.4.1 → 0.14.7 migration. signal-cli migrates its store
schema one-way on first run of a new version, so that archive is the only way
back to 0.14.4.1.

### Facts that cost hours to learn

- **`--receive-mode=on-start` is mandatory.** Without it signal-cli serves every
  JSON-RPC call correctly and never opens its websocket to Signal. It ran that
  way on the host for 61 days: `/health` green, `nora_messages_total` at `0.0`,
  not one message delivered. Under R-5 that is indistinguishable from a quiet
  group.
- **Java 25 is required** (class file version 69). Temurin 21 dies at class load.
- **`GET /api/v1/accounts` does not exist and never did.** signal-cli's `--http` mode
  serves exactly three endpoints — `POST /api/v1/rpc`, `GET /api/v1/events`,
  `GET /api/v1/check` — per `signal-cli-jsonrpc(5)`, shipped in the image:

  ```bash
  docker exec signal-cli sh -c 'zcat /opt/signal-cli-0.14.7/man/man5/signal-cli-jsonrpc.5.gz'
  ```

  Nora read `/api/v1/accounts` for alias enrichment until 2026-08-05 and got a 404 every
  start. Note that `/api/v1/check`, not `/health`, is the daemon's own liveness route.
- **The JSON-RPC that actually answers "who am I".** There is no single call:
  - `listAccounts` → `-32601 Method not implemented`. It only exists in MULTI-account
    mode (daemon started with no `-a`). Ours is pinned to one account, so **no RPC
    reveals which account that is** — `platforms.signal.account` in `config.toml` is
    load-bearing, not a convenience override.
  - `listContacts` with `{"recipient": "<number>"}` → her own row, a purely local store
    read: `number`, `uuid` (her **ACI**), `username`.
  - `getUserStatus` with `{"recipient": ["<number>"]}` → **the PNI, not the ACI.** It
    answers a phone-number lookup, so the `uuid` it returns is the phone-number identity.
    Cross-checked against the account store, where `aciAccountData.serviceId` and
    `pniAccountData.serviceId` are two different UUIDs. Wiring only `getUserStatus` —
    the obvious one-call fix — collects the wrong UUID and still looks like it worked.
    `nora/platform/signal/self_id.py` batches both calls into one POST and keeps both.
- **Nora's Signal identity, for cross-checking a metric:** phone `+85290000100`,
  username `nora.000`, ACI `a1000000-0000-4000-8000-000000000000`, PNI
  `a10000ff-0000-4000-8000-0000000000ff`. Four id forms, which is why a healthy
  `nora_identity_aliases` reads 4.
- **Group state can go stale on a linked device.** If `listGroups` returns `[]`
  for a group you know exists, call `sendSyncRequest` over `/api/v1/rpc` — that
  is what made `Ridgeline` appear.
- **The daemon is Nora's, not shared.** It was documented as "neutral and
  shared" from the Hermes era; hermes-agent runs profiles `attest`/`bento`/
  `default` and has no `nora` profile. signal-cli *binds* `:18080`; Nora is its
  only client.
- **Device 2 is named `nora`** (renamed from `HermesAgent`, 2026-08-05, via
  `updateAccount --device-name`).
- **The account is a LINKED DEVICE**, not a standalone registration. The primary
  phone can be off for normal operation — Signal delivers to each device
  independently — but Signal unlinks devices after prolonged inactivity, and
  going standalone would require re-registering the number, which logs the phone
  out.
