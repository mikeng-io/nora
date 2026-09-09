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

   The participation occasion (design/dreamer-agent-loop/35, once enabled) is the
   third audience on this series, and it changes what "silent" MEANS: a
   participation turn that says nothing is the HEALTHY outcome, so it never lands
   here — it counts on `nora_participation_silence_total`, and a spoke turn on
   `nora_participation_spoke_total` (their ratio is how often the occasion finds
   grounded value). What lands here under `audience="participation"` is only a
   malfunction of the ordinary kind — a budget-cut turn or a delivery failure —
   so participation's own budget expression is
   `nora_silent_failures_total{audience="participation"} / (nora_participation_silence_total + nora_participation_spoke_total)`,
   NOT a share of the tag budget.

   The proactive occasion (design/dreamer-agent-loop/39, DEC-162 — wired
   when `policy.toml` names a `home_room`) is the fourth audience and has
   participation's exact shape: a `kind="proactive"` row's act turn that
   says nothing is the healthy outcome and counts on
   `nora_proactive_silence_total` (the row is DONE with `outcome="silent"`),
   a spoke turn on `nora_proactive_spoke_total`, and only a malfunction lands
   here under `audience="proactive"`. Its budget expression is the same
   ratio over its own two series. The heartbeat's own fired counter gains
   three outcomes for this kind — `silent`, and the fire-time screen's
   `cancelled_note_retired` / `cancelled_note_moved` — and the decide turn's
   own reading is `nora_proactive_decisions_total{action}` beside the
   Dreamer's `nora_dream_run_total{adaptor="proactive"}`.

   **A reminder can also be hours late with all three of those reading healthy.**
   A tick that finds the conversation's slot held by a tag leaves the rows
   `pending` and moves on: nothing fired, nothing failed, and the tick itself
   completed, so the fired counter is flat and the tick-age gauge drops back to
   zero. `nora_heartbeat_deferrals_total` is the only signal — a sustained rate
   means a busy group is starving its own reminders. It counts deferred *turns
   per tick*, so the same rows re-count each tick; `nora reminders list` is
   where you find out which rows, and marks them `OVERDUE`.

   ```bash
   curl -s http://127.0.0.1:9100/metrics | grep nora_heartbeat_deferrals_total
   ```

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

   Look at `platforms.<name>.connected`, `store.reachable`, `honcho.reachable`, and
   `omniroute.last_success_at`. Any of those false/stale explains real silence: a
   platform's stream dropped, the DB is unreachable, Honcho is down, or the LLM hasn't
   answered recently.

   **Check `platforms` first, not `sse`.** `sse` is Signal's block and only Signal's;
   it is kept top-level because `deploy/watchdog.sh` parses it and `/health` is
   additive-only. `platforms` has one entry per ENABLED platform, and **every one of
   them counts toward the status code** — so a 503 with a perfectly green `sse` means a
   non-Signal platform is down, and reading only the four keys above would leave you
   concluding nothing explains it. A platform that is not enabled is ABSENT from
   `platforms` entirely; a platform that is enabled and has never connected is present
   with `connected: false`. Those are different facts and the block distinguishes them.

   ```jsonc
   "platforms": {
     "signal":  {"connected": true,  "last_event_age_seconds": 4.2,  "account": "+852…"},
     "discord": {"connected": false, "last_event_age_seconds": null, "account": "1504…"}
   }
   ```

   **Per-platform liveness, from the metrics endpoint:**

   ```bash
   curl -s http://127.0.0.1:9100/metrics | grep nora_adapter_last_event_age_seconds
   ```

   `nora_adapter_last_event_age_seconds{platform="…"}` is seconds since that
   platform last showed a sign of life — a delivered message, or the gateway
   connecting/resuming. One series per enabled adapter.

   **Alert on the series being ABSENT, not only on it being large.** A
   prometheus label-child is created on first write, so a platform that has
   never once been alive publishes nothing at all — and `grep` returning
   nothing reads as "no problem" when it means "no instrument". Use
   `absent(nora_adapter_last_event_age_seconds{platform="discord"})` alongside
   any staleness threshold.

   The two platforms' ages do NOT mean the same thing. Signal's ticks on every
   SSE envelope, receipts and typing included, so a large age means WEDGED.
   Discord's ticks on messages and on connect/resume, so in a quiet channel a
   large age can simply mean nobody talked — pair it with
   `platforms.discord.connected` from `/health` before concluding anything.

   The older `nora_last_event_age_seconds` (unlabelled) still exists and is
   still **Signal's alone**. It was deliberately not relabelled: `/health`'s
   `sse` block and this series are the pair a Signal-era dashboard already
   watches, and adding a label changes a series' identity. Read the labelled one
   for anything platform-specific; the unlabelled one is a compatibility
   surface, not the general answer.

   **If every room-scoped tool answers «I can't check» on Discord** — `where_is`,
   `group_members`, `lookup_person` all `unavailable`, while `/health` shows
   `platforms.discord.connected: true` and messages are clearly arriving — the
   roster read is failing, not the gateway. It has its own failure modes and
   they are invisible in `/health`:

   ```bash
   docker compose logs nora | grep 'nora.discord.roster'
   ```

   - `roster read forbidden for guild …` → the **GUILD_MEMBERS privileged
     intent** is off for the application in the Discord Developer Portal. This
     is the single most likely Discord misconfiguration and nothing else
     surfaces it: the gateway is healthy, messages flow, and only the REST
     members endpoint 403s.
   - `has at least 1000 members — this roster does not paginate` → the guild is
     too large for the current unpaginated read, and every room-scoped tool
     stays `unavailable` there until pagination lands.
   - `is not '<guild_id>/<channel_id>'` → a malformed `id` in `policy.toml`.

   All three degrade honestly (R-5) rather than guessing — which is exactly why
   they are indistinguishable from the guard working correctly without reading
   the log.

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

     **Do not go looking in Langfuse for any of this.** An ingest contains no model
     call, so its trace is plumbing and is never exported (R-76). Every ambient
     message reports its ending on `nora_ingest_outcomes_total`, labelled by platform,
     conversation type and outcome — that counter is where «what happened to the
     messages» is answered, and it is the one that distinguishes an ordinary recorded
     message from a tag, a refusal, or a dropped audit write:

     ```bash
     curl -s http://127.0.0.1:9100/metrics | grep '^nora_ingest_outcomes_total'
     ```
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
   self-send arriving under her ACI would be handled as somebody else's tag.
   A healthy Signal start reads **3**: phone, ACI, PNI. (It read 4 until
   2026-08-14, when `username` stopped being a matchable identity on every
   platform — a handle its owner can rename, and that somebody else can then
   claim, must never resolve a person. If you are looking at an older dashboard
   note expecting 4, 3 is the new healthy.) The
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

## 4b. WhatsApp (whatsapp-bridge)

The bridge (`deploy/whatsapp-bridge/`, design/whatsapp-adoption/) is the
transport twin of signal-cli: same private network, same no-published-ports
posture, session on `./state/whatsapp` (chmod 700, never commit). It is only
relevant when `[platforms.whatsapp] enabled = true`.

**Pairing (first run, and after any session invalidation):**

```bash
docker compose run --rm whatsapp-bridge --pair --phone <digits, no +>
```

Type the printed pairing code into the bot number's phone app (Settings →
Linked Devices → Link with phone number). Normal `docker compose up -d`
then boots with the saved session. If you mess it up, re-run the same
command — no cleanup needed. Losing `./state/whatsapp` loses the pairing,
not history (the audit DB is the record).

**Admitting a WhatsApp room:** get the group JID (`...@g.us`) from the audit
DB or the bridge's logs, add a `[[conversations]]` entry with
`platform = "whatsapp"` to policy.toml, then `docker compose up -d
--force-recreate nora` — a RECREATE, not a restart (the single-file
bind-mount inode gotcha, `nora/obs/health.py:14-21`). Confirm `/health`'s
`config.policy.disk_mtime` matches the edited file. 1:1 chats stay
deny-by-default until the DM study lands.

| Failure | Symptom | Action |
|---|---|---|
| WhatsApp protocol update | bridge reconnect loop / messages stop | bump the Baileys pin in deploy/whatsapp-bridge/package.json, rebuild, re-pair if the session was invalidated |
| Phone offline >~14 days (operational lore, not an SLA) | device unlinked | re-pair with the command above |
| Session dir lost/corrupt | `paired: false` in the bridge's /health | re-pair (no data loss) |
| Account restricted (ban) | login throttled/rejected | stop using the number; evaluate cause (design/whatsapp-adoption/02 §risk posture) |
| Network blip | transient disconnect | self-heals; the bridge reconnects with backoff |

The container healthcheck only proves the HTTP layer serves (curl exists in
the image precisely because node:20-slim ships none). The real detector is
Nora `/health`'s per-platform `last_event_age_seconds` — the bridge emits
SSE heartbeats so a dead WhatsApp socket ages that value in a quiet room,
and `deploy/watchdog.sh` alerts on it. `dropped_events` on the bridge's
`/health` (and a WARNING in Nora's log) means inbound events were discarded
while Nora was disconnected — audit rows were lost; that is counted, never
silent.

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

`nora groups` and `nora persons pending` are read-only. Neither writes a file,
neither writes to the daemon, and both work with the Nora container **stopped**
— the 3am path is docker, an editor and these. Neither needs
`OMNIROUTE_API_KEY`; listing group ids has nothing to do with the LLM.

`nora config set/get/unset/validate/path --config <path>` is the WRITE surface (`path` prints the resolved config location)
(deploy convention §4.5, 2026-09-08): schema-validated, atomic
(tmp + rename), prints the diff, and prints the restart command without
executing it. It never touches `.env` — secrets stay there. A config edit
still takes effect only on restart.

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

## 8b. "The version note never appeared" (or appeared twice)

The upgrade note is one turn per room on the boot after a deploy, and R-5 keeps
every way it can fail OUT of the room — so the counter and the ledger are the
only evidence. Read them in that order.

```bash
curl -s localhost:9100/metrics | grep nora_version_announcements_total
docker exec nora-db psql -U nora -d nora -c \
  'select * from version_announcements order by announced_at desc limit 10;'
```

`status` on the counter says which of these it was:

| status | what happened | what to do |
|---|---|---|
| `already_announced` | the version was already taken for that room | nothing — this is R-42 working, and it climbs on every restart |
| `unavailable` | this image carries no readable build stamp | rebuild with `scripts/build-image.sh V3`. A plain `docker build` (or `docker compose build` with no `NORA_VERSION`) SUCCEEDS and is the normal way to pick up a config change — it just produces an UNSTAMPED image, which announces nothing and answers «I can't check» to «咩version». It is not an error state, it is an untagged build |
| `no_note` | the tag has no annotation, so there is nothing anybody wrote to relay | `git tag -a V3 -m "…" -f` and rebuild — nothing was claimed, so it will still announce |
| `deferred` | a tag held the conversation when the sweep ran | nothing — the next boot retries, because nothing was said |
| `failed_*` | the turn ran and the group heard nothing | the row records which; **it is not retried** (at-most-once, R-42) — announce the next version, or say it yourself |

**Twice is a bug, not a config.** The claim is a primary key, so a repeat means
the ledger row was deleted or the database was restored from before the
announcement. Check `version_announcements` before looking anywhere else.

## 8c. "She never came back with the file" (`make_artifact`)

A compute job that outruns `make_artifact`'s inline window becomes a durable
`compute_jobs` row, and `ComputeJobPoller` delivers it later as its own turn.
R-5 keeps every failure of that delivery out of the room, so — exactly as with
8b — the counter and the table are the only evidence.

```bash
curl -s localhost:9100/metrics | grep nora_compute_jobs_delivered_total
docker exec nora-db psql -U nora -d nora -c \
  'select id, state, created_at, completed_at, error from compute_jobs
   order by created_at desc limit 10;'
```

| outcome | what happened | what to do |
|---|---|---|
| `sent` | she delivered it | nothing |
| `deferred` | a tag held the conversation on that tick | nothing — the row is still `PENDING` and the next tick retries. Climbing without ever reaching `sent` means the slot is never freeing; look at the tag path, not at compute |
| `failed_error` | the turn raised, **or** boot recovery resolved the row. `error` on the row says which: `process crashed mid-delivery` (`FIRING`) or `lost track of this job across a restart` (`PENDING`) | the second one is expected on **every** restart with jobs in flight — the backend's job registry is in-memory, so a restart orphans both the handle and, possibly, a still-running sandbox container (`docker ps` for a `job_*` name). Nothing retries; the request has to be made again |
| `failed_policy` / `failed_timeout` | the fire turn itself was refused or cut off | same reading as `nora_reminders_fired_total`'s own — §8.1's outcomes, not compute-specific |

**Nothing here is retried.** One job, one delivery, ever — the same
at-most-once rule reminders and announcements hold.

**`[compute] enabled = true` and she refuses to start** is the design, not a
regression: `NORA_DATA_HOST_ROOT` (the host-side path of the `/app/data` mount,
needed because the Docker daemon resolves a sandbox's bind-mount source on the
HOST) plus the Drop, Maps and model credentials are all checked at boot. The
startup message names exactly what is missing. They are checked there because
each one, unset, would otherwise fail every job silently at run time, in front
of the group.

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
    read: `number`, `uuid` (her **ACI**), `username`. Only the first two are
    COLLECTED — `username` is deliberately dropped (see the alias-count note in
    §1); it is readable, not matchable.
  - `getUserStatus` with `{"recipient": ["<number>"]}` → **the PNI, not the ACI.** It
    answers a phone-number lookup, so the `uuid` it returns is the phone-number identity.
    Cross-checked against the account store, where `aciAccountData.serviceId` and
    `pniAccountData.serviceId` are two different UUIDs. Wiring only `getUserStatus` —
    the obvious one-call fix — collects the wrong UUID and still looks like it worked.
    `nora/platform/signal/self_id.py` batches both calls into one POST and keeps both.
- **Nora's Signal identity, for cross-checking a metric:** phone `+85290000100`,
  ACI `a1000000-0000-4000-8000-000000000000`, PNI
  `a10000ff-0000-4000-8000-0000000000ff`. Three MATCHABLE id forms, which is why
  a healthy `nora_identity_aliases` reads 3. Her Signal username (`nora.000`,
  which is also the wake word) is not one of them and is not collected.
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

## 10. "The social desktop: VNC, Chrome, OpenCLI"

The browser runtime behind `social_fetch` (DEC-151) is four pieces on the
host, all systemd **user** services or user installs — none of them is the
Nora container, and Nora runs fine with all of them down (the tool answers
`unavailable`). See `ARCHITECTURE.md` §1's browser-boundary note.

- `nora-social-desktop.service` — `tigervncserver :2 -fg` (Xtigervnc,
  1920x1080, Openbox), VNC bound to the Tailscale interface only
  (`-interface 100.91.183.127`, port 5902, TLSVnc). Enabled with user
  lingering, so it starts at boot with no login.
- `nora-social-chrome.service` — headed Chrome on `DISPLAY=:2`
  (`--ozone-platform=x11`) with the authenticated profile holding the
  Instagram and X sessions. The unit has `Requires=`/`After=` on the desktop
  service, so systemd already orders Chrome after `:2` exists.
- OpenCLI 1.8.7 — installed under `~/.hermes/node`, on PATH as `opencli`;
  daemon on `localhost:19825`, Browser Bridge extension connected to that
  Chrome.
- `nora-social-bridge.service` — `python -m nora.infra.social` (unit in
  `deploy/social-bridge/`), the read-only HTTP seam the container calls on
  `http://host.docker.internal:19826` (the docker0 gateway — Nora's container
  is on the private `nora` compose network, not host networking). The unit
  sets `OPENCLI_BIN=/home/norty/.local/bin/opencli` (the systemd user
  environment has no interactive PATH) and binds `127.0.0.1` + the docker0
  gateway `172.17.0.1` — loopback and docker0 are both host-internal, never a
  LAN interface, because there is no authentication on `/read`; the allowlist
  is the boundary. It runs the CHECKOUT's code, so it tracks master — an edit
  on master is a deploy here after a restart.

The virtual display is the fix, not a preference: Browser Bridge drives real
Chrome window APIs, and GNOME RDP reports zero monitors while no client is
connected, so window creation failed under RDP. RDP is optional administration
only now — the old RDP-based workaround is retired, and disconnecting RDP
changes nothing.

**Check or restart:**

```bash
systemctl --user status nora-social-desktop nora-social-chrome nora-social-bridge
systemctl --user restart nora-social-chrome        # ordinary case — extension reconnects itself
systemctl --user restart nora-social-desktop       # display itself; Chrome is ordered after it
systemctl --user restart nora-social-bridge        # the /read seam; cheap and safe
curl -s http://127.0.0.1:19826/health              # {"ok": true} when the bridge is up
```

`social_fetch` answering `unavailable` → check the bridge first (the status
and health curl above), then `opencli doctor` (below).

After any Chrome restart, confirm the Browser Bridge extension reconnected with
`opencli doctor` (below) before assuming reads will work.

**Occasional visual access / login renewal:** connect a VNC viewer to
`100.91.183.127:5902`. VNC listens on the Tailscale interface only — not LAN,
not localhost — so the viewer machine must be on the tailnet. Disconnecting the
viewer kills nothing: Xtigervnc, Chrome and OpenCLI keep running. Use this when
a read-only probe reports `logged_in=false` for Instagram or X — log in once by
hand in that Chrome and the session persists in the profile. The VNC password
lives only in the service's private configuration — never paste it into the
repo, an issue, or a chat log.

**Diagnose OpenCLI:**

```bash
opencli doctor     # daemon on :19825, Browser Bridge extension connectivity
```

A passing doctor plus a logged-in profile is the whole health story today —
read-only probes confirmed Instagram and X as `logged_in=true` after the Chrome
migration. Threads is not checkable at all: OpenCLI 1.8.7 has no native Threads
adapter, so Threads support is unimplemented, not broken.
