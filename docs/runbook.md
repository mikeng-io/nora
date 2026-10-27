# Nora operator runbook

Content filters default to `[content_checks] enabled = false`. To restore the
retained phone/UUID/coordinate, place/person-name and location audience checks,
set `enabled = true` in the deployment-owned config and restart through the
normal validated deployment procedure. This does not toggle credential or
doctrine protection, or grant a room/unit access. No live config is changed by
the source change. See [the decision](adr/0030-content-filters-are-disabled-by-default-and-reversible.md).

All example names, account IDs, nicknames and room mappings in this file are
synthetic pseudonyms; replace them with your own installation values.

Operational scenarios and reference commands. SSH examples use `nora-host`, an
alias you configure for your deployment host (see `AGENTS.md`, Reaching the
deployment). Paths under `~/workspace/` are example installation paths; substitute
your own. Loopback addresses refer to the host where each command runs.

Before deployment, config recreation or rollback, select the installed mode in
[the nora-deploy skill](../.agents/skills/nora-deploy/SKILL.md). A production Komodo
success receipt or pending attempt routes deployment and recovery through the
[native Stack](../deploy/komodo/README.md), including code, doctrine and recreation. The checkout build, source-pull, retag and
recreate procedures below apply only before that cutover; read-only diagnostics
still apply in either mode.

## 1. "Nora is silent — is she broken or just quiet?"

Silence is the default behavior. Tags, enabled participation/proactive paths and
scheduled delivery can all produce replies; each has its own health signals.
To tell "quiet" from "broken":

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

   The proactive occasion (design/dreamer-agent-loop/39, DEC-162; since
   2026-10-03, DEC-277, its reminder rows are written by the Cognition
   session's `remind`, and the fire path's act turn below is unchanged) is the fourth audience and has
   participation's exact shape: a `kind="proactive"` row's act turn that
   says nothing is the healthy outcome and counts on
   `nora_proactive_silence_total` (the row is DONE with `outcome="silent"`),
   a spoke turn on `nora_proactive_spoke_total`, and only a malfunction lands
   here under `audience="proactive"`. Its budget expression is the same
   ratio over its own two series. The heartbeat's own fired counter gains
   three outcomes for this kind — `silent`, and the fire-time screen's
   `cancelled_note_retired` / `cancelled_note_moved`. The decide pass and its
   `nora_proactive_decisions_total` / `nora_dream_run_total{adaptor="proactive"}`
   are gone (DEC-277); read the deciding side in the `cognition_wake_outcome` table.

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

2. Check `GET /health` — `503` means a required health check failed;
   `200` can still include a degraded platform:

   ```bash
   curl -s http://127.0.0.1:9100/health | python -m json.tool
   ```

   Look at `platforms.<name>.connected`, `store.reachable`, `honcho.reachable`, and
   `omniroute.last_success_at`. Any of those false/stale explains real silence: a
   platform's stream dropped, the DB is unreachable, Honcho is down, or the LLM hasn't
   answered recently.

   **Check `platforms` first, not `sse`.** `sse` is Signal's block and only Signal's;
   it is kept top-level because `/health` is additive-only — removing a key is a
   breaking change for anything reading it. (Until 2026-09-09 the stated reason was
   that `deploy/watchdog.sh` parsed it; that script is deleted, and the
   additive-only rule is the one that still holds. `nora_last_event_age_seconds`
   stays unlabelled and Signal's for the same reason — DEC-102.) `platforms` has one entry per ENABLED platform, and a disconnected, non-degraded
   platform makes health fail. A degraded adapter is reported in `degraded` and
   `detail` without making HTTP status fail, so inspect those fields even on 200. A platform that is not enabled is ABSENT from
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
   docker compose logs nora | grep nora.discord.roster   # JSON lines on stdout
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

   - `nora_denied_conversations_total` rising → the conversation is not in `policy.toml`,
     or a direct message came from somebody who is not in `identity.toml` (R-101; the log
     line says which, and never carries the number).
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
and `nora_identity_aliases` is above 1, the tag path shows no failure. Check the
enabled participation, proactive and scheduled paths before concluding she is quiet.

## 2. "A doctrine edit broke her"

Production Stack `nora-releases` uses image-baked doctrine and Dreamer. A host
`git pull` changes neither. Ship corrections through the release path below.
The hot-reload instructions that follow apply only to legacy source mounts.

### Legacy checkout mode: "A doctrine edit broke her"


A deployment that bind-mounts `nora/doctrine` can hot-reload its files on every
tag with `doctrine.reload = "mtime"`, without a restart. But a bad edit (broken
frontmatter, missing required key, unparseable YAML) fails to parse.

1. Check the reload-failure counter:

   ```bash
   curl -s http://127.0.0.1:9100/metrics | grep nora_doctrine_reload_failures_total
   ```

2. Confirm it in the logs:

   ```bash
   docker compose logs nora | grep doctrine_reload_failed
   ```

   A failed reparse **keeps the last good doctrine** — Nora keeps replying with the
   previous, working version, not with a crash or a half-parsed doctrine. The counter and
   log line are how you find out a bad edit is sitting there even though nothing looked
   broken from the group's side.

3. Fix it: revert the file to the last-good version (`git checkout -- nora/doctrine/<file>.md`
   or restore your edit). **No restart needed** — the next tag re-stats the directory,
   sees the corrected mtime, and reparses successfully.

## 3. "Rollback"

The normal path is the installed Komodo Stack `nora-releases`. A fix merges to
master, then the maintainer manually merges release-please's release PR while
the repo is private. The tag and GitHub Release trigger `release-images.yml`;
a complete image build commits `compose.json` and `release.json` to `deployments`.
The enabled `nora-release-poll` Procedure calls `DeployStackIfChanged` every ten
minutes. The installed wrapper deploys and writes `release-deploy.json`.
v6.5.0 deployed this way without host commands on 2026-10-21.

Rollback: revert the selecting commit on `deployments`, then wait for the poller
or run native `DeployStack` manually. The wrapper refuses only an identical failed
fingerprint and does not enforce version order. This does **not** roll back the
database schema (migrations upgrade to head on startup) or host config. Check
compatibility before selecting an older bundle; otherwise roll forward or use a
coordinated database restore (section 9). Preserve failed-attempt evidence;
see [Komodo recovery](../deploy/komodo/README.md#failure-and-recovery).

`~/workspace/nora` is still the live runtime root for private config, `.env`,
`data/` and `state/`; database, RustFS, Prometheus, Alertmanager and embedder still
use it as their Compose root, and the computer unit's `ExecStart` points there.
**Do not delete or reset it.**

### Legacy checkout mode: local image rollback

The following is checkout-era reference only; it does not apply to production's
installed release stack. See the [legacy deploy reference](../.agents/skills/nora-deploy/reference.md#legacy-checkout-mode).

Nora runs as a **Docker container**, not under systemd. The per-phase tags this
section used to retag (`nora:p0`, `nora:p1a`, …) no longer exist on the deploy
host, which held only `nora:latest` on 2026-09-24. A deploy run through the
`nora-deploy` agent skill tags every VERIFIED image `nora:sha-<sha>` and logs it
to `~/nora-backups/deploys.log`, so rolling back one deploy is a retag of the
previous logged line, no rebuild and no git checkout. The legacy reference's step 6 is the
procedure: it holds the deploy lock, and it refuses when a migration landed
between the two commits (the image runs `alembic upgrade head` at start, so an
older image cannot run on a newer schema; roll forward with a revert PR).

A config key the older image does not know makes it crash-loop: revert those
keys in `config.toml` first (section 3b).

`OMNIROUTE_API_KEY` must be available for the container to start, but nothing needs to
be exported by hand — it comes from the gitignored `.env` file in the repo root, which
`docker compose` reads automatically.

Historical SQLite-era rollback instructions do not apply to the current
PostgreSQL store. Do not clear Alembic's version table or check out `master`
expecting an old schema. Use a schema-compatible verified image, or roll forward
through a reviewed revert PR. A database restore requires a coordinated backup
and matching application/configuration revision; see section 9.

## 3b. After every Komodo deploy

Compare `deployments`' `release.json` (version, source SHA and image digests),
`/etc/komodo/nora-deploy/release-deploy.json`, all owned services' live images,
and Nora's `/app/nora/version.json` stamp. Confirm service health and zero
restarts after recreation. The wrapper performs these checks under the shared
lock; a publication workflow or a prior receipt alone is not live proof.
See [the installed deploy procedure](../.agents/skills/nora-deploy/SKILL.md).

### Legacy checkout mode: `scripts/verify-deploy.py`

The following rebuild/recreate commands and local-tag checks are checkout-era
reference only. Do not run them against the installed production release stack.

```bash
scripts/build-image.sh
docker compose up -d --force-recreate nora presence analytics
python3 scripts/verify-deploy.py        # LAST, and it must exit 0
```

Run it under the deploy lock and with the rollback tag: the `nora-deploy`
legacy reference's step 3 is this block with both.

**Three containers run `nora:latest`** — `nora`, `presence` and `analytics`,
same image, different commands. `--force-recreate nora` recreates ONE of them.
The other two keep running whatever image they were created from, and
`docker ps` shows `nora:latest` for all three because that is the reference
they asked for, not the one they are on.

**What that cost, 2026-09-16.** `config.toml` gained the voice work's
`[speech.tts]` keys. The two stale containers' Pydantic schema predates those
keys and rejects them (`extra_forbidden`), so both entered a restart loop:
**279 restarts, `analytics` down four days, `presence` a day.** The fix was one
`--force-recreate presence analytics` onto the image that was already built.

**The two voice containers are NOT on `nora:latest`** — `voice-sidecar` and
`voice-wake` build their own images, so `--force-recreate nora` does not touch
them and `verify-deploy.py` cannot see them (it only inspects containers on
`nora:latest`). After a change to either, recreate them by name and read the
image back per container:

```bash
docker compose up -d --force-recreate voice-sidecar voice-wake
docker inspect --format '{{.Image}}' voice-sidecar
docker image inspect voice-sidecar:latest --format '{{.Id}}'    # must match
```

There is no per-guild container: one sidecar serves every server Nora holds
voice on, and one wake gate serves every sidecar connection
(design/voice-channel/07 §12.5).

**THE THREE VOICE IMAGES MOVE TOGETHER.** `nora`, `voice-sidecar` and
`voice-wake` speak two seams that have both changed inside one release:

- sidecar → gate: the audio frames, including the hello that names the server
- gate → nora: the transcript POST body
- nora → sidecar: the control plane

A partial deploy fails in the far container, quietly. An old gate reads a new
hello as audio from an empty speaker and tears the connection down in a loop; a
new gate drops every frame from an old sidecar and still reports `healthy` with
`clients: 1` — only its `guilds: []` says otherwise. Rebuild and recreate all
three, or none.

**Rolling `nora` back also needs `config.toml` and `policy.toml` reverted.**
`SpeechTtsConfig` is `extra = "forbid"`, so an older nora meets the newer
`sidecars` key and crash-loops rather than misbehaving — loud, but it will not
start until the bind-mounted files match the image.

`verify-deploy.py` asks the two questions the deploy itself cannot: is every
container that asked for this image actually ON the image just built, and is it
up a grace period later. It exits non-zero on either, so it can be the last
line of a deploy and mean something. Falsified against both constructed
failures, 2026-09-16 — a retagged-underneath container reports `STALE`, a
container that exits on startup reports `DOWN`, each exit 1.

**Why a check and not another alert.** Detection was never missing.
`TargetDown` fired the entire four days, Alertmanager routed it, and the bridge
delivered it to Discord — the chain worked perfectly. It was not ACTED on,
because `PlatformSilent` trips on 900 seconds of ordinary quiet and flaps all
day, so the channel delivers every ~25 minutes and a four-day outage is
invisible inside it. `deploy/alertmanager/alertmanager.yml` predicts exactly
this in its own comment: *"a repeat every few minutes is how an alert channel
becomes a muted one."* A new alert would have joined the noise; a check that
fails the deploy does not compete for attention that is already spent.

**Resolved 2026-10-01: Signal's age now counts the keep-alive.** `PlatformSilent`
fired on ordinary quiet because Signal's `last_event_at` moved only on a
message: 108 episodes in 14 days, Signal firing about 140 hours of them, each
resolved when a message finally arrived (read back from Prometheus on nora-host).
signal-cli sends a `:` comment line about every 18 seconds (5 in 90 seconds,
probed the same day), and `SignalListener` discarded it. It now advances
`last_event_at` without reaching `on_event`, as the WhatsApp and Instagram
heartbeats already do, so an age over 900 seconds on any platform means a
silent socket, not a quiet room. The same clock feeds `state_of`'s `stale`.
What it cannot see: a signal-cli that stays connected, keeps sending its
keep-alive and delivers no message. That reads as alive, exactly as a quiet room
does; only a message or a failing send shows it.

(An earlier version of this paragraph said the metric carries **no `signal`
series at all**. That was wrong: Signal emitted continuously 09-09 to 09-16
10:36. It is absent only after a restart until the first SSE envelope arrives,
because `__main__` skips a `None` age and Prometheus creates a labelled series
on first observation — the same lazy-series effect that hides the gate's `go`
counter. Absence here means «no event yet», never «not instrumented».)

## 4. "An alert arrived in the Discord review channel"

Prometheus evaluates the rules in `deploy/prometheus/rules/`, Alertmanager groups and
throttles them, and `deploy/alert-bridge/` posts the result into the review channel.
`PlatformSilent` is the SSE-silence one — no adapter event for 900 seconds — and the
rest are in `docs/observability.md`'s table with the incident behind each.

Until 2026-09-09 this section described a shell script polling `/health` on a timer
that was never created, whose alert channel was `echo >&2`. It was deleted; the
commands below are unchanged and still the right first moves.

1. Check signal-cli first — it's the more common cause:

   ```bash
   docker compose exec -T nora /app/.venv/bin/python -c \
     'import urllib.request; print(urllib.request.urlopen("http://signal-cli:18080/api/v1/check", timeout=5).status)'
   ```

   If that's not a healthy 200, restart or investigate signal-cli itself before touching
   Nora.

2. If signal-cli looks fine, check Nora's own logs:

   ```bash
   docker compose logs -n 100 -f nora
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
the image precisely because node:24-trixie-slim ships none). The real detector is
Nora `/health`'s per-platform `last_event_age_seconds` — the bridge emits
SSE heartbeats so a dead WhatsApp socket ages that value in a quiet room,
and Prometheus's `PlatformSilent` rule alerts on it (`deploy/prometheus/rules/`,
delivered to the Discord review channel; the old `watchdog.sh` was deleted 2026-09-09). `dropped_events` on the bridge's
`/health` (and a WARNING in Nora's log) means inbound events were discarded
while Nora was disconnected — audit rows were lost; that is counted, never
silent.

## 4c. "Nora says everyone is home" — Life360 refused the collector's login

The presence collector stops recording, and every `where_is`/`where_been`
answer freezes on the last fix. In `docker logs presence`:

```
life360 auth failed: status=403 json=True
life360 read banned/refused: initial authentication failed (failures=N)
```

Read the token endpoint's body once from inside the container. If it says
`{"errorMessage":"Invalid login and password. ..."}` while the password in
`.env` is right, Life360 is refusing the **password grant** for this account.
Once an account's phone number is verified, Life360 blocks the password grant
for it (pnbruckner/life360 README). On 2026-09-23 the phone and email on Nora's
account were verified in the afternoon. The collector's cached token kept
working until Life360 revoked it at 00:12 HKT on 09-24, and every re-auth after
that got this 403. Changing the password does not bring it back. Switch the
collector to **token mode**:

1. In a browser, sign in at <https://life360.com/login> as **Nora's** Life360
   account (the one `LIFE360_USERNAME` names), not your own.
2. Open DevTools, then Application → Storage → Cookies → `life360.com`, and copy
   the value of `LIFE360_AUTH_TOKEN`.
3. On nora-host, add it to the deploy checkout's `.env` (never commit it):

   ```bash
   LIFE360_AUTH_TOKEN=<the cookie value>
   ```

4. Recreate the collector. A plain `restart` keeps the old environment:

   ```bash
   docker compose up -d --force-recreate presence
   ```

5. Read it back. `docker logs presence` shows `life360 auth: token mode`, and new
   rows land in `presence_fixes` within a cadence or two.

In token mode the password is never POSTed. When Life360 stops accepting the
token, the log reads `access token rejected — replace LIFE360_AUTH_TOKEN` and
`nora_presence_auth_alarms_total{reason="token_rejected"}` climbs. The collector
backs off rather than falling back to the password, so repeat steps 1–4. Unset
the variable to go back to password mode.

## 4c-2. "She can't open shared files" — Nora's Google sign-in

Nora has ONE Google identity (`[google]` in `config.toml`, three variables in
`.env`: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
`GOOGLE_OAUTH_REFRESH_TOKEN`), shared by every Google service she uses
(shared files today, design/external-files §4). API keys such as the Maps key
are separate and live with their feature.

- **Every Google service answers `unavailable`** and
  `nora_google_invalid_grant_total` is rising (or the log says `invalid_grant`):
  the refresh token is revoked or expired. A consent screen left in Testing
  expires it after 7 days; set it to In production.
- **One service answers `unavailable` and the others work**: the log says the
  sign-in "lacks the scope …". The refresh token was granted without that
  service's scope (usually a service added since the last sign-in).
- **The fix for both:** on a machine with a browser, run
  `uv run python scripts/google_auth.py` with the same client, sign in as
  the dedicated Google account configured for your deployment, approve every scope it lists, and put the printed token in
  nora-host's `.env` as `GOOGLE_OAUTH_REFRESH_TOKEN`. Then recreate the container
  (nora-deploy): `.env` is read at start.

## 4d. "The cognition loop is not healthy"

There is no health command and no cognition alert (removed 2026-10-03). Read the rows: `cognition_wake_outcome` (outcome,
cause, detail per wake), `cognition_session`, and `stimulus` for what waits. The gauge
`nora_stimulus_pending_age_seconds{unit}` is the oldest stimulus that could wake a unit.
Use `/app/.venv/bin/python`, not `uv run`, in the container: `uv run` installs the dev
extras over the live venv.

**What a restart does to a session (since the cut-over's PR-7).** Before it, nora had
no SIGTERM handler: `docker stop` killed the process on the spot, `App.shutdown` never
ran, and a session in flight left its wake open, holding its unit for the rest of
`[cognition] session_timeout_seconds` (3,900 s) and then counting as a failed session.
Now `nora.__main__.run_until_stopped` handles SIGTERM and SIGINT: the adapters and
loops stop (no new wake starts), a session already running gets 30 s
(`SESSION_SHUTDOWN_GRACE_SECONDS`) to finish, then it is cancelled and records its
wake `cancelled` with a `restart` detail, which frees its unit and returns its stimuli
to pending; the model client and the store close. Compose gives the `nora` service
`stop_grace_period: 75s` for that. A **second** signal ends the process at once. If
the process was killed anyway (SIGKILL after the grace, the host rebooting), the next
process **reclaims at boot**: every wake with no outcome that was recorded before it
started is recorded `cancelled` / `restart` (`nora_cognition_wake_reclaimed_total`).
A `cancelled` outcome neither counts toward the failure backoff nor strikes a
stimulus, so a restart never penalises a unit. Read the cause in
`cognition_wake_outcome`: `outcome = 'cancelled' AND detail LIKE 'restart%'`. **Not
covered:** a tag turn or a Dreamer pass in flight at the stop ends with the process, as
before; the stop does not wait out a 360 s turn.

Monitoring reload remains outside the Komodo application release path.

**Putting a rule change live.** The rules are bind-mounted
(`./deploy/prometheus/rules:/etc/prometheus/rules`) and Prometheus runs with
`--web.enable-lifecycle`, so after the deploy checkout has the new file:

```bash
docker compose exec -T prometheus promtool check rules /etc/prometheus/rules/nora.yml
curl -fsS -X POST http://127.0.0.1:9190/-/reload
curl -fsS http://127.0.0.1:9190/api/v1/rules | jq -r '[.data.groups[].rules[].name] | length'   # 16
```

**Known limits.** There is no dead-man switch (`docs/observability.md`, «What this
pipeline cannot say»): nothing says so if Prometheus, Alertmanager or the bridge
stops, and the bridge answers 202 to Alertmanager when its Discord POST fails.

## 5. "Which doctrine produced this reply?"

Every reply's log line and trace carry `doctrine_sha256` — the sha256 of the doctrine version
active for that specific tag, computed at `DoctrineStore.get()` time. Find the tag by its
`tag_id` (or by time) and read the hash straight off that line:

```bash
docker compose logs nora | grep doctrine_sha256 | tail -1
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

**Status as of 2026-08-05: ENABLED and verified end-to-end** — against Langfuse
directly. **Superseded 2026-09-04:** the container now exports to an
OpenTelemetry collector (`http://host.docker.internal:4318/v1/traces`), which
fans out to Langfuse (`:3100`) and Jaeger (`:16686`); `docs/observability.md`
«How a span reaches a collector» carries the live read-back and the two
filters on that path. `headers_env = "LANGFUSE_OTLP_HEADERS"` still names the
credential, which lives in the gitignored `.env` and is passed to the container
by `docker-compose.yml`. The verification steps below are the 2026-08-05 ones
and still work against Langfuse's own endpoint.

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

## 6b. "Is the ambient path actually working?" — `nora measure participation`

Four measures the rooms produce for free (`design/culture-tier/02-measurement`).
No model, no behaviour change, read-only.

```bash
docker compose exec -T nora uv run --no-sync nora measure participation --days 7
```

Why it exists: **today the verdict IS the measurement.** A model agreeing with
the instruction someone wrote it is not evidence about the room, and a `no_go`
is unfalsifiable by construction — she stays silent, nothing happens, and
silence is also what success looks like. A refusal is indistinguishable from
health in every other log the system keeps, which is how a 1% admission rate
went unremarked for days.

| measure | fires when |
|---|---|
| `expiry_without_action` | a note passed its anchor day while open and she never raised it |
| `holding_refusals` | the gate refused room R while she held an open note born in R |
| `overrides` | a human tagged her in R shortly after a `no_go` |
| `ignored` | she spoke unprompted and the next human line did not engage |

**Read the ceiling printed under each number, every time.** They are blind to
different things — one is a lower bound by construction, one over-reports, one
is missing the arm that carries what she owes a room (#222) — so the ceilings
are per measure rather than a shared footnote. `n/a (nothing considered)` is
NOT zero: it means the measure did not run.

### The two refusal-side measures need a file

`holding_refusals` and `overrides` need refusal TIMES, and those live only in a
Prometheus counter. Produce the file on the host, where Prometheus is
reachable, then hand it in:

```bash
python3 scripts/gate_refusals.py --range 7d > /tmp/refusals.json
docker cp /tmp/refusals.json nora:/tmp/refusals.json
docker compose exec -T nora uv run --no-sync \
    nora measure participation --days 7 --refusals /tmp/refusals.json
```

Without it those two report `0 / 0`, which reads as «did not run» and not as
«never happens» — that distinction is the point.

**The command REFUSES a refusal file that does not span the window asked for.**
A short list makes both measures look clean, and clean is also what they would
say if the gate had refused nothing; those two readings must never be
confusable. If it refuses, widen `--range` or lower `--days`.

**The counter is the weak half and the script says so.** Prometheus labels
lazily and the process restarts a few times a day, so a refusal never scraped
before a restart leaves no trace, and times are accurate only to the scrape
interval. `02-measurement §3` names the fix — a durable per-verdict row, which
DEC-179 excluded from `decision_log` on purpose. Until that exists, `overrides`
is approximate and is a lower bound.

## 6d. "Did the transport throw away her reasoning?" — `reasoning_strip_log`

**The defect** (as it stood until DEC-208). Strands' Chat Completions formatter
does not carry `reasoningContent` in a multi-turn position. When a loop goes multi-turn — on
the agent path that means it reached for a tool — Strands drops the reasoning
out of the request, logs one WARNING, and **continues**. In production it does
not raise: the turn runs on and she speaks with her reasoning gone (#325, read
back 2026-09-17). ADR 0011 measured the same transport limitation on the
relevance gate as a **safety breach**, not merely as degradation.

**Carried since #325's fix (2026-09-25, DEC-208).** Every provider `nora.agent.factory`
builds now puts each assistant message's reasoning back on the wire as the
`reasoning` field (`nora.agent.reasoning_carry`); the upstream reads that field
and discards `reasoning_content`, measured by `prompt_tokens` on direct
requests. The watch judges the request the model actually formats (a block
counts as carried only when its text is in a `reasoning` field), so from the
deploy of that change a `dreamer:*` row at `low` reads `strips = 0` with
`chances > 0`: that is the fix, not a blind instrument. A non-zero `strips`
after it is a block the carry could not place (a reasoning-only or redacted
block), and is news.

**Read the table, not the counter.** `nora_reasoning_strip_total` exists and is
useful inside one uptime window, but it resets on every deploy, and an absent
series there means "nothing since the last restart" — never "never happened".
The durable answer is in Postgres.

```bash
docker exec nora-db psql -U nora -d nora -c "
  SELECT path, model, reasoning_effort,
         count(*)                   AS turns,
         sum(continuation_calls)    AS chances,
         sum(strip_calls)           AS strips,
         sum(stripped_blocks)       AS blocks,
         round(100.0 * sum(strip_calls) / nullif(sum(continuation_calls),0), 1)
                                    AS pct
  FROM reasoning_strip_log
  WHERE created_at > now() - interval '7 days'
  GROUP BY 1,2,3
  ORDER BY 1,2,3;"
```

`path` is one of `tag`, `heartbeat`, `participation`, `proactive` or
`dreamer:<adaptor>` — every loop that speaks, plus one value per Dreamer
adaptor. **`proactive` and `dreamer:proactive` are different loops sharing a
word**: the first is a self-written reminder coming due and may speak into a
room; the second is a background research pass that holds no act verb.

**How to read it, and the four traps.**

- **`chances` is the denominator and it is the point.** `strips = 2` is a
  catastrophe at `chances = 2` and a rounding error at `chances = 200`. The
  production reading that opened #325 had no denominator at all, which is why
  it settled nothing.
- **`pct = 0` at `reasoning_effort = 'none'` means IMPOSSIBLE, not healthy.**
  A path emitting no reasoning cannot have any stripped. As of 2026-09-17 the
  tag and participation paths are at `none` and every Dreamer adaptor is at
  `low`, so a clean tag row is expected. Before the carry landed a clean
  `dreamer:*` row at `low` would have been news; after it, it is the expected
  reading (see above).
- **No rows for a path is not a zero.** A row is written only for a turn that
  went MULTI-TURN, so a path with no rows had no tool-using turns in the
  window — check `nora_tool_call_log_writes_total` or the tool log before
  concluding anything.
- **`model` is the OmniRoute ALIAS and cannot see a combo repoint.** It is
  `[llm] model`, copied into the provider at startup, so when the combo behind
  `nora` is repointed upstream this column reads `nora` on both sides. The
  served model id does not reach the seam that writes these rows at all. Use
  OmniRoute's own logs for that question; use this column only for a change of
  ALIAS (a second tier, an eval run against `nora-gate`).

**`strips` is how OFTEN, `blocks` is how MUCH.** They are different questions
and the second has no other source: `strips` counts requests the transport
stripped, `blocks` counts the DISTINCT reasoning blocks lost. Two strips of one
block is a short loop re-sending the same reasoning; two strips of two blocks
is a loop that reasoned twice and lost both. `blocks < strips` is ordinary and
means re-drops; `blocks` climbing faster than `strips` means longer loops.

**After an effort or config change**, the same query with
`date_trunc('hour', created_at)` in the GROUP BY is the before/after —
`reasoning_effort` records what the request actually carried rather than what
config said, which is the half of 2026-09-17 that cost two hours to
reconstruct. For an upstream combo repoint (the other half, and the one this
table cannot see) the before/after is the timestamp alone, read against
OmniRoute's routing log.

**Is the table growing faster than you want?** A row lands for every turn with
at least one continuation call, on every speaking path — not one per adaptor per
day, which is the Dreamer's rate alone. The volume is unmeasured until this
ships; check it and decide retention against a number:

```bash
docker exec nora-db psql -U nora -d nora -c "
  SELECT path, count(*) FROM reasoning_strip_log
  WHERE created_at > now() - interval '1 day' GROUP BY 1 ORDER BY 2 DESC;"
```

The row is ten small columns and one index entry: on the order of 100 MB a year
at 1,000 qualifying turns a day, 10 MB at 100. There is no sweep, deliberately —
pruning is a retention judgement, and the window this table answers over is
longer than one deploy.

Nothing acts on this signal, deliberately: the drop is deterministic, and a
retry would turn a request-shape incompatibility into an apparently
intermittent one. The fix is the request shape itself (the carry above). See `docs/adr/0013-the-reasoning-strip-is-counted-in-postgres.md`.

## 7. "Where do eval results live?"

The current entry point is `uv run python -m evals <corpus-name>`.
`uv run python -m evals --list` lists the selectable corpora without running one;
`--class` and `--all` select broader runs. Live runs need the configured model,
source clone and backing services and can spend model calls. Use the
[eval SOP](eval-sop.md) for setup and the results/trace readback procedure.
The runner prints measurements; retain its output and exit code with the source
revision. Langfuse traces are separate evidence, not a substitute for the runner's
verdict. A timeout or error is never a pass.

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

**`nora memory backfill --unit <unit>`** (DEC-214) replays one unit's recorded
human messages into that unit's own Honcho workspace — for a unit that has just
moved to a Nora workspace of its own, whose Honcho workspace starts empty. It is
a DRY RUN unless `--apply`; it refuses a session that already holds messages
unless `--resume` (which skips what it holds), refuses a policy with no
`[units]`, and refuses the unit's workspace when that is `honcho.workspace_id`
(the history is already there), and refuses a Honcho workspace another
application uses. It reads the record store read-only, writes only Honcho,
and deletes nothing. It replays PEOPLE's messages only — Nora's own replies are
skipped, as live ingestion skips them — so in the new workspace a message that
answered her arrives without what she said. `nora.app.memory_backfill` has the
rest.

**`nora memory carry-conclusions --workspace <workspace> --before <cutover>`**
(DEC-214) copies the facts Nora was told before unit scopes existed
(`remember`, note discharge — Nora's conclusions with no session, created
before the cutover) into every unit scope of that workspace, where the units'
reads now look — only facts about someone who belongs to that workspace alone;
the rest are listed by id for you. One-time, a dry run unless `--apply`, and
idempotent. `nora.app.memory_carry` has the rest.

**`nora memory detach-unit`** (DEC-214) takes a moved unit's rooms out of the
Honcho workspace it left, in two steps. `--unit <u> --from-honcho-workspace
<id> --peers-file <path> --apply` deletes the unit's sessions — and, through
Honcho's delete queue, everything stored under them — after recording the
sessions and their peers in `<path>` (merged across runs; after the delete
Honcho can no longer name them). `--refresh-cards --peers-file <path>` then
waits until Honcho reports no conclusion left under each deleted session and
rebuilds those peers' cards; it refuses rather than refresh early, and can be
run again. Irreversible; step 1 is refused until a backfill dry run for the
unit would feed nothing. `nora.app.memory_detach` has the rest.

**Historical, one-time DEC-214 migration notes — not the current release path.**

**The DEC-214 cutover, in this order.** On `master`'s image, `policy.toml` and
`identity.toml` are `extra = "forbid"`, so writing `[units]`/`[workspaces]`
into the LIVE files before the new image runs crash-loops the old one on its
next restart. Never edit them first. Every file this writes lives under the
checkout's `state/` — gitignored, and bind-mounted into the `nora` container at
`/app/state` — never loose in the deploy checkout, because two of them are
copies of `identity.toml`. Every command is on the deploy host, in
`~/workspace/nora`, under `nora-deploy`'s lock:

```bash
D=state/dec214; mkdir -p "$D"

# 1. Build the new image. Do NOT recreate yet (legacy nora-deploy reference step 3, build line only).
docker compose build nora > /tmp/nora-build.log 2>&1; echo "BUILD_RC=$?"

# 2. Write the new config under $D, and a config that points at it. A config's
#    relative paths resolve against ITS OWN directory, so the three that are not
#    in $D are made absolute (container paths).
cp policy.toml "$D/policy.toml.next" && cp identity.toml "$D/identity.toml.next"
#    ...append the [units] block to $D/policy.toml.next and the [workspaces]
#    block to $D/identity.toml.next...
sed -e 's|^identity_file = .*|identity_file = "identity.toml.next"|' \
    -e 's|^policy_file = .*|policy_file = "policy.toml.next"|' \
    -e 's|^data_dir = "data"|data_dir = "/app/data"|' \
    -e 's|^dir = "nora/doctrine"|dir = "/app/nora/doctrine"|' \
    -e 's|^dir = "nora/dreamer"|dir = "/app/nora/dreamer"|' \
    config.toml > "$D/config.next.toml"
grep -cE '\.next"|"/app/' "$D/config.next.toml"      # must print 5

# 3. Dry-run both commands on the NEW image; $D is already inside the container
#    through the ./state mount. `--name` is explicit because the service sets
#    `container_name: nora`: Compose v2 is understood not to apply it to `run`
#    containers, but a name clash would be the first thing this dry run shows.
NEXT="docker compose run --rm --no-deps --name nora-dec214-dryrun \
  --entrypoint /app/.venv/bin/nora nora --config $D/config.next.toml"
$NEXT memory backfill --unit ridgeline
$NEXT memory carry-conclusions --workspace 67 --before "$(date -Iseconds)"

# 4. Swap the files IN PLACE (cp keeps the bind-mounted inode) and recreate, in one command.
date -Iseconds > "$D/cutover.txt"
cp policy.toml "$D/policy.toml.pre-dec214" && cp identity.toml "$D/identity.toml.pre-dec214" \
  && cp "$D/policy.toml.next" policy.toml && cp "$D/identity.toml.next" identity.toml \
  && docker compose up -d --force-recreate nora presence analytics; echo "UP_RC=$?"

# 5. Replay ridgeline's history (live traffic since step 4 is already in its
#    sessions). Run it again until it reports "would feed 0" without --apply.
docker compose exec -T nora /app/.venv/bin/nora --config config.toml \
  memory backfill --unit ridgeline --resume --apply </dev/null

# 6. Put the pre-cutover facts back into 67's unit scopes (dry run first, then --apply).
docker compose exec -T nora /app/.venv/bin/nora --config config.toml \
  memory carry-conclusions --workspace 67 --before "$(cat "$D/cutover.txt")" --apply </dev/null

# 7. Wait for Honcho's scope backfill: every entry's "state" must read "completed".
for ws_scope in nora/friends nora/wedding nora-ridgeline/ridgeline; do
  docker compose exec -T nora curl -fsS \
    "http://honcho:8000/v3/workspaces/${ws_scope%%/*}/scopes/${ws_scope#*/}/status" </dev/null; echo
done

# 8. ONLY with the operator's approval (irreversible): dry run, then --apply,
#    then — once Honcho's delete queue has run — the cards.
DETACH="docker compose exec -T nora /app/.venv/bin/nora --config config.toml memory detach-unit"
$DETACH --unit ridgeline --from-honcho-workspace nora --peers-file $D/detach-ridgeline.json </dev/null
$DETACH --unit ridgeline --from-honcho-workspace nora --peers-file $D/detach-ridgeline.json --apply </dev/null
$DETACH --refresh-cards --peers-file $D/detach-ridgeline.json </dev/null
```

Rollback before step 5: `cp state/dec214/policy.toml.pre-dec214 policy.toml && cp
state/dec214/identity.toml.pre-dec214 identity.toml`, then nora-deploy's rollback
(retag the previous image, recreate). After step 8's `--apply` nothing
restores the detached sessions.

One ordering effect to expect: a Honcho document's own `created_at` is when it
was INSERTED, not when its message was said, so the documents live ridgeline
traffic produced after step 4 rank OLDER than those the step-5 replay derives
from months-old history, in the "recent" channel of `representation`. It
evens out as new traffic arrives; the replayed MESSAGES themselves carry their
real dates.

**Nora refuses to start on a Honcho workspace that is not hers**: a bound
`honcho_workspace` that holds sessions and lacks her `nora_owner` metadata
marker belongs to another application on the same Honcho. Bind the workspace
to another id in identity.toml. `honcho.workspace_id` is exempt (it predates
the marker).

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
ALLOWED  Example group
         id = <group-id-from-your-signal-daemon>
         member=yes  members=3
         policy: labelled "Example group", persons = ['example-user']
```

If the daemon is unreachable, `groups` exits 1 with the two commands to run;
`pending` still works but loses display names and says so — with no roster to
correlate against, one person's ids may appear as several entries.

## 8b. "She says the wrong version" (or can't check)

Compare the selected `deployments` branch's `release.json` version and source
SHA with `/etc/komodo/nora-deploy/release-deploy.json` and the live image stamp:

```bash
docker exec nora /app/.venv/bin/python -c \
  'import json; print(json.load(open("/app/nora/version.json")))'
```

All three must identify the selected release; also compare manifest image digests
with every owned service's running image and health. A newer GitHub Release may
still be building or awaiting the next ten-minute poll. Read publication and
Komodo failure state before claiming success; do not rebuild the host checkout.

### Legacy checkout mode: version diagnosis

In legacy checkout mode, she reads her version off `nora/version.json`, baked by `scripts/build-image.sh`
from `git describe --tags --match 'v[0-9]*'` (DEC-314). She posts nothing about
a new version: the startup upgrade note was retired with DEC-314, and
`version_announcements` is a retired table nothing reads. Read the stamp first:

```bash
docker exec nora /app/.venv/bin/python -c \
  'import json; print(json.load(open("/app/nora/version.json"))["version"])'
git -C ~/workspace/nora describe --tags --match 'v[0-9]*'
```

| what you see | what happened | what to do |
|---|---|---|
| no file | the image was built UNSTAMPED: a dirty tree, no release tag reachable, or a plain `docker build` | read the build log's refusal, then rebuild with `scripts/build-image.sh`. Unstamped is not an error state: she answers «I can't check» |
| `vX.Y.Z-N-g<sha>` | the checkout is N commits past the release | nothing, if that is what is deployed. The release itself is `nora-release` |
| an older `vX.Y.Z` than GitHub shows | the tag was not fetched when the image was built | `build-image.sh` fetches tags; a failed fetch warns and stamps from local tags. Fetch, then rebuild |

## 8c. Deferred delivery (`compute_jobs`)

Sub-agent jobs and MiniMax video generation write the deferred-delivery
outbox when their backends are enabled. `ComputeJobPoller` polls those backends
and delivers completed work through the fire path in the originating room.
Inspect both the job row and delivery counter when a promised result is missing.

```bash
curl -s localhost:9100/metrics | grep nora_compute_jobs_delivered_total
docker compose exec -T db psql -U nora -d nora -c \
  'select id, state, created_at, completed_at, error from compute_jobs
   order by created_at desc limit 10;'
```

| outcome | what happened | what to do |
|---|---|---|
| `sent` | she delivered it | nothing |
| `deferred` | a tag held the conversation on that tick | nothing — the row is still `PENDING` and the next tick retries. Climbing without ever reaching `sent` means the slot is never freeing; look at the tag path, not at compute |
| `failed_error` | the turn raised, **or** boot recovery resolved the row. `error` on the row says which: `process crashed mid-delivery` (`FIRING`) or `lost track of this job across a restart` (`PENDING`) | a `PENDING` row is swept at every boot, because a backend's job registry is in-memory. Nothing retries; the request has to be made again |
| `failed_policy` / `failed_timeout` | the fire turn itself was refused or cut off | same reading as `nora_reminders_fired_total`'s own — §8.1's outcomes, not compute-specific |

**Nothing here is retried.** One job, one delivery, ever — the same
at-most-once rule reminders hold.

The poller needs `[compute] enabled = true`, an enabled backend and a live fire
path. `[subagent]` needs its Dreamer model and budgets; `[video_gen]` needs the
MiniMax key and media storage. Enabling compute alone with no backend logs a
warning and starts no poller. Check the relevant backend's configuration first.

## 9. "Where does everything live?" (read this first, months later)

One tree. If it is not under `~/workspace/nora/`, Nora does not own it.

```
~/workspace/nora/
  docker-compose.yml        every service: nora, db, signal-cli, whatsapp-bridge,
                            presence, analytics, rustfs, stt,
                            prometheus, alertmanager, alert-bridge
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
  data/                     Nora's own state — media blobs; the record
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

**Storage and credentials.** Compose mounts `./data` at `/app/data` and the
whole `./state` directory at `/app/state` in Nora. Signal session material is
therefore within that mount too; do not assume the container cannot read it.
PostgreSQL uses the `db-data` named volume; RustFS blobs use `rustfs-data`.
OAuth refresh state and messaging sessions must be preserved as credentials.
Optional audio-controller state is in its own named volume.

**Back up a coordinated set.** Stop writers, dump PostgreSQL and copy both bind
mounts and object storage. Keep the backup outside the checkout, access-restricted
and encrypted at rest. Record the deployed image/source revision with it. The
commands below assume the default `./data` bind source; include any configured
`NORA_DATA_HOST_ROOT` and additional external stores separately.

```bash
BACKUP="$HOME/backups/nora-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP"
chmod 700 "$BACKUP"
docker compose stop
docker compose start db
docker compose exec -T db pg_dump -U nora -d nora -Fc > "$BACKUP/postgres.dump"
docker compose stop db
tar czf "$BACKUP/config-state-data.tar.gz" .env config.toml identity.toml policy.toml state data
docker compose cp rustfs:/data "$BACKUP/rustfs"
# If voice is installed, preserve its desired-state volume too:
# docker compose cp voice-sidecar:/var/lib/audio-controller "$BACKUP/audio-controller"
```

Check every command's exit code and inspect the archive and dump before resuming
the installed deployment mode. Back up the external memory backend and sandbox
workspace through their own procedures; neither is included in this Compose dump.
A restore rehearsal on isolated storage is the verification that the set works.

**Restore:** stop the stack and use the matching image/configuration revision.
Keep the current data until the restore is verified. Restore the archive in the
installation directory, then copy the RustFS contents into its stopped service
and restore the database (containers/volumes must already exist):

```bash
tar xzf "$BACKUP/config-state-data.tar.gz"
docker compose cp "$BACKUP/rustfs/." rustfs:/data
# If saved: docker compose cp "$BACKUP/audio-controller/." voice-sidecar:/var/lib/audio-controller
docker compose start db
docker compose exec -T db pg_restore -U nora -d nora --clean --if-exists --no-owner < "$BACKUP/postgres.dump"
```

Resume through the installed deployment mode, then verify health, account linkage
and a permitted message. Preserve the complete `state/signal-cli/` directory;
selecting individual account files can lose the device registration.

**Before a signal-cli upgrade:** create and verify a backup outside the checkout,
for example `~/backups/signal-cli-<timestamp>/signal-cli-data.tar.gz`. Store
migrations can be one-way: a backup taken before upgrading is required to return
to the previous version.

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
- **Cross-check identity metrics against your private configuration.** Phone,
  ACI and PNI are three matchable identity forms, so a configured single-account
  installation normally reports `nora_identity_aliases` as 3. Signal usernames
  are not collected as matchable identities.
- **Group state can go stale on a linked device.** If `listGroups` returns `[]`
  for a group you know exists, call `sendSyncRequest` over `/api/v1/rpc`.
- **Use a dedicated Signal daemon.** Keep its account and device registration
  separate from other applications, and confirm its bind address and client
  configuration before changing it.
- **Name the linked device clearly**, using `updateAccount --device-name`, so
  an operator can distinguish it from other linked devices.
- **The account is a LINKED DEVICE**, not a standalone registration. The primary
  phone can be off for normal operation — Signal delivers to each device
  independently — but Signal unlinks devices after prolonged inactivity, and
  going standalone would require re-registering the number, which logs the phone
  out.

## 10. "The social desktop: VNC, Chrome, OpenCLI"

The browser runtime behind the X and LinkedIn tools (DEC-201) is four pieces on the
host, all systemd **user** services or user installs — none of them is the
Nora container, and Nora runs fine with all of them down (the tool answers
`unavailable`). See [Architecture — State and trust boundaries](../ARCHITECTURE.md#state-and-trust-boundaries).

The Tailscale address, the OpenCLI profile alias and the host user in the unit
names below are placeholders; the real ones are on the deploy host
(`tailscale ip -4`, `systemctl --user list-units '*-desktop*' '*-chrome*'`, `opencli profile list`).

- `nora-social-desktop.service` — `tigervncserver :2 -fg` (Xtigervnc,
  1920x1080, Openbox), VNC bound to the Tailscale interface only
  (`-interface <tailscale-ip> -localhost=0`, port 5902, `-SecurityTypes VncAuth`:
  macOS Screen Sharing cannot speak TLSVnc, and Tailscale already encrypts the
  link; `-localhost=0` must come AFTER `-interface`, which otherwise resets it
  and binds loopback only). Enabled with user
  lingering, so it starts at boot with no login. This display is NORA'S
  ONLY: nothing of the operator's runs on it.
- `nora-social-chrome.service` — headed Chrome on `DISPLAY=:2`
  (`--ozone-platform=x11`) with its OWN data folder,
  `--user-data-dir=/home/<deploy-user>/.local/share/nora-browser
  --profile-directory="Profile 1"`: one profile, signed into Chrome as Nora's
  own Google account, holding the Instagram, X and LinkedIn sessions and the
  OpenCLI extension. None of the operator's extensions or sites are in it. Never point it back at `~/.config/google-chrome`:
  that folder is the operator's browser, and a shared folder means a shared
  process, encryption key and "last used profile" (split 2026-10-10). The unit has `Requires=`/`After=` on the desktop
  service, so systemd already orders Chrome after `:2` exists.
- OpenCLI — install it for the deployment user and put `opencli` on PATH;
  daemon on `localhost:19825`. Register Nora's Chrome with the Browser Bridge
  extension and profile alias `nora`. If other Chrome profiles share the daemon,
  Nora must still select her own: the bridge and session probe pass
  `--profile nora` on every call (`OPENCLI_PROFILE`, default `nora`). An
  unknown or disconnected profile fails with exit 69 rather than falling back.
  After reinstalling the extension a profile gets a new id: re-alias it with
  `opencli profile list` and `opencli profile rename <id> nora`.

- `nora-social-bridge.service` — `python -m nora.infra.social` (unit in
  `deploy/social-bridge/`), the read-only HTTP seam the container calls on
  `http://host.docker.internal:19826` (the docker0 gateway — Nora's container
  is on the private `nora` compose network, not host networking). The unit
  sets `OPENCLI_BIN=/home/<deploy-user>/.local/bin/opencli` (the systemd user
  environment has no interactive PATH) and binds `127.0.0.1` + the docker0
  gateway `172.17.0.1` — loopback and docker0 are both host-internal, never a
  LAN interface, because there is no authentication on `/read`; the allowlist
  is the boundary. It runs the CHECKOUT's code, so it tracks master — an edit
  on master is a deploy here after a restart.

The host social bridge remains outside the Komodo release path.

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

`x_read` or `x_search` answering `unavailable` → check the bridge first (the status
and health curl above), then `opencli doctor` (below).

After any Chrome restart, confirm the Browser Bridge extension reconnected with
`opencli doctor` (below) before assuming reads will work.

**Occasional visual access / login renewal:** connect a VNC viewer to
`<tailscale-ip>:5902`. VNC listens on the Tailscale interface only — not LAN,
not localhost — so the viewer machine must be on the tailnet. Disconnecting the
viewer kills nothing: Xtigervnc, Chrome and OpenCLI keep running. Use this when
a read-only probe reports `logged_in=false` for Instagram or X — log in once by
hand in that Chrome and the session persists in the profile. The VNC password
lives only in the service's private configuration — never paste it into the
repo, an issue, or a chat log.

**Keep any operator desktop separate.** Use independent browser data, VNC
displays and credentials for human browsing. Nora's services must point only at
her dedicated display and profile.

**Are the logins still alive?** `nora-session-probe.timer` (unit in
`deploy/session-probe/`) checks Chrome's Instagram, X and LinkedIn session
cookies every ~10 minutes, keeps the `instagram-bridge` cookie jar in step with
Chrome's, and alerts to the review channel when a session needs a human login
in VNC. Evidence is one JSON line per run in
`~/.local/state/nora/session-probe.jsonl`; see `deploy/session-probe/README.md`.

**Diagnose OpenCLI:**

```bash
opencli doctor     # daemon on :19825, Browser Bridge extension connectivity
```

A passing doctor plus a logged-in profile is the whole health story today —
read-only probes confirmed Instagram and X as `logged_in=true` after the Chrome
migration. Threads is not checkable at all: OpenCLI 1.8.7 has no native Threads
adapter, so Threads support is unimplemented, not broken.

## 11. "Honcho answers anyone" — closing it without authentication (#633, DEC-259)

Honcho has no authentication, and the operator ruled (2026-10-01) that it
will not get any: the fix is **where it listens**. Measured 2026-09-30 on nora-host:
`honcho-api-1` published `0.0.0.0:8000`, so any container that could name the
host (`host.docker.internal`) and anything on the LAN read and wrote Nora's
memory with one unauthenticated `POST /v3/workspaces/list`.

**Honcho is not in this repository.** Its compose project is
`~/workspace/honcho/docker-compose.yml` on nora-host, and nothing here edits it.
What this repo holds is nora's half (the `honcho` network in
`docker-compose.yml`, the host-side rewrite in `nora/subject/obtain.py`) and the
probe, `deploy/honcho-closed-probe.sh`. Who reaches Honcho, read on nora-host
2026-10-01:

| caller | route today | after |
|---|---|---|
| nora (the tag path, the Dreamer, `nora memory ...` in the container) | `host.docker.internal:8000` | `honcho:8000` on `honcho_memory` |
| the eval suite, host-side scripts, a host `curl` | `127.0.0.1:8000` | unchanged (loopback still published) |
| another machine on the tailnet (the Mac's Honcho plugin) | `tailscale serve` forwards `:18000` to `localhost:8000` | unchanged |
| presence, analytics, voice, the portal | none: no environment or config names Honcho | none |

The tailnet forward is deliberate and stays unauthenticated, as it was; that is
the operator's tailnet, not the LAN. A caller found later that is not in this
table is reading Honcho through the open port and will break at step 4: the
access log's source class before and after (below) is how to find one first.

### The order, and why it is this order

Each step leaves every caller working; the door closes last. **Run from
`~/workspace/nora` and `~/workspace/honcho` on nora-host. Nothing here is done by
the PR merging.**

```bash
# 0. BEFORE the nora deploy of this change. The network is owned by neither
#    stack (both mark it external), so `down` on either never removes it. If it
#    is missing, EVERY `docker compose up` that names nora fails at once and
#    names it (the running container is not touched), so nora-deploy runs this
#    line first, idempotently:
docker network inspect honcho_memory >/dev/null 2>&1 || docker network create --internal honcho_memory
#    --internal is not optional. A container's default route goes through the
#    first-sorting non-internal network it joins, so joining an ordinary
#    network that sorts before `nora_nora` would move nora's route to the
#    internet onto it. An internal network has no gateway: nora's route stays
#    on its compose network, and container-to-container traffic on it (nora to
#    `honcho:8000`) works. Measured on a throwaway local docker 2026-10-01:
#    two containers on an --internal network resolved and reached each other
#    by name, the one also on an ordinary network kept its default route and
#    its egress there, and the other's loopback publish still answered.

# 1. Deploy nora from this branch (nora-deploy). It now also joins honcho_memory.
#    Nothing has changed for it yet: its config still says host.docker.internal.

# 2. Honcho's compose: add the network and an alias, KEEP the old publish for now.
#    In ~/workspace/honcho/docker-compose.yml, service `api`:
#
#        networks:
#          default: {}
#          memory:
#            aliases: [honcho]
#
#    and at the bottom of the file:
#
#        networks:
#          memory:
#            external: true
#            name: honcho_memory
#
cd ~/workspace/honcho && docker compose up -d api </dev/null
docker exec nora curl -fsS -m 5 http://honcho:8000/health </dev/null   # {"status":"ok"}

# 3. nora's live config: [honcho] base_url = "http://honcho:8000". This is a
#    config-only edit: follow the validated-copy procedure under the lock in
#    nora-deploy step 3 (linked below), then release the lock before running
#    native DeployStack on nora-releases. Read it back from the container:
docker exec nora /app/.venv/bin/python -c \
  'from pathlib import Path; from nora.config import load; print(load(Path("config.toml"), require_secrets=False).honcho.base_url)' </dev/null

# 4. Only now close the door. In the honcho compose, service `api`:
#        ports: ["127.0.0.1:8000:8000"]        # was "8000:8000"
cd ~/workspace/honcho && docker compose up -d api </dev/null
```

For step 3, follow [nora-deploy’s config-only procedure](../.agents/skills/nora-deploy/SKILL.md#3-config-only-change).

Doing step 4 before step 3 shuts nora out of memory: every peer write fails,
and the failure reads as Honcho being down.

### Read-back

```bash
cd ~/workspace/nora && deploy/honcho-closed-probe.sh; echo "PROBE_RC=$?"
```

`PROBE_RC=0` is every check passing: a throwaway container on the default bridge
is REFUSED on port 8000 at every IPv4 address the host owns (only curl's exit 7,
connection refused, counts: a timeout or a reset is an ERROR, as is an empty
result, and each address gets its own control on a port that is open), every
listening socket on `:8000` is loopback in BOTH families (this is what covers
IPv6, which the default bridge often cannot route to), a loopback check proves
Honcho is alive and not merely unreachable, nora reaches it by name, the tailnet
forward still answers, and the Docker Engine is 28 or newer. `1` is an open door
or a shut-out caller; `2` is a probe that cannot tell; `3` is a WARN only: on an
engine older than 28 a loopback-published port can be reached from the LAN, which
a probe run on the host cannot see, so from a machine on the LAN
`curl -m 4 http://<deployment-host-lan-address>:8000/health` must fail. The same probe by hand:

```bash
docker run --rm --network bridge --entrypoint curl nora:latest \
  -s -m 4 -o /dev/null -w '%{http_code}\n' http://172.17.0.1:8000/health </dev/null   # 000 = refused
```

A second reading, from Honcho's own access log: nora's calls arrived from the
compose gateway (`172.21.x.x`, the userland proxy) before step 4 and arrive from
the `honcho_memory` subnet after it. Counts only, never paths:

```bash
docker logs --since 1h honcho-api-1 </dev/null 2>&1 \
  | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+ - "(GET|POST|PUT|DELETE)' \
  | awk '{split($1,a,"."); print a[1]"."a[2]".x.x"}' | sort | uniq -c | sort -rn
```

**Rollback**, in this order (the order is the whole point):

1. Put `[honcho] base_url` back to `"http://host.docker.internal:8000"` in nora's
   live `config.toml` (the config-only edit, `nora-deploy` step 3) and recreate
   nora on it. Do this FIRST.
2. Only then publish `8000:8000` again in the honcho compose and `up -d api`.
3. Only after that recreate nora from an OLD compose file if you must.

Each step leaves every caller working. Out of order it does not: nora on an old
compose has no `honcho` network, so a config still saying `http://honcho:8000`
resolves nothing and every memory call fails (peer writes, the self card, recall)
until the config is put back; and reopening the port before nora has moved back
is fine, but moving nora back before the port is open shuts it out. Nora on the
name keeps working while the door is open, so reopening the door alone (step 2)
is the minimal rollback if the problem is a caller of the old port.


## Typed Profile historical recovery

Keep `[memory.profile] read = false` until source coverage and the reply gates are
accepted. The typed migration is nullable and retains every legacy row; a
successful old shadow session is not historical coverage. The person adaptor
now walks bounded oldest-first pages through a frozen Record boundary using
its existing schedule and budget, independently of recent-message updates.

The Character-card spike is an explicit operator command; it never runs on
startup or changes `[memory.profile] read`. Dates are inclusive HKT dates, and keys
may be repeated. Dry-run constructs no runtime and writes no rows or cursors:

```bash
nora memory profile-history --key '<unit>#character:<person>' --since 2026-10-01 --until 2026-10-02
# Only when the operator chooses to run the spike:
nora memory profile-history --key '<unit>#character:<person>' --since 2026-10-01 --until 2026-10-02 --apply
nora memory profile-report --key '<unit>#character:<person>' --since 2026-10-01 --until 2026-10-02
nora memory profile-report --key '<unit>#character:<person>' --format card
nora memory profile-report --key '<unit>#character:<person>' --judge
```

The window reads messages/mentions oldest-first in day-sized pages, with cached
media and bounded social/presence snapshots for that day, through the same
writer. It retains the normal history cursor rather than resetting it. As with
normal sessions, input budgets bound source delivery; this is a monitored spike,
not evidence of a completed full historical recovery. Inspect the report and
session traces before widening the window. No window is run by the build.

The report reads the database through a read-only engine and prints counts by
default. `--format card` explicitly prints private card prose on the operator's
machine. `--judge` spends calls on `[evals] judge_model`, refuses the writer's
configured model, and reports pattern/event/unsupported/unavailable separately.
This classification never writes a hypothesis verdict or promotes a claim.
Usage and refusal counts come from recorded Langfuse observations; absent or
inaccessible receipts are null, never zero. `--sessions /private/observations.json`
can instead read an exported observation list (or its `data` envelope). Keep
source text, cards and trace exports on owner hardware. No public receipt contains
private rows. Migration downgrade refuses dimension rows rather than rewriting
append-only history; preserve a database backup for schema rollback.

`nora memory profile-history` prints per-account source boundary/progress,
eligible source counts, missing fields, untyped legacy heads and disputes,
without message text. Completion means source exhaustion, not factual parity.
Inspect original source messages for missing/ambiguous facts; never fill them
from uncited memory conclusions or infer a birthday from an occasion date.

To repeat an incomplete old shadow pass, use
`nora memory profile-history --key '<unit>#character:<person>' --restart --apply`.
Without `--apply` this is a dry run. Only the historical cursors reset; Profile
claim history and the recent watermark remain. Failures, conflicts and
unsettled sessions retain the page. A crash after claim commit but before
cursor settlement repeats evidence. Runtime traces and watermark row history
retain per-page provenance. Render-on retires legacy model writers and reads
Profile room accounts for person/unit context; render-off restores the old
configured writer/read surface. No legacy tables or Honcho client are removed.

After fixing a cached-media reader, inspect one account's actual rendered input
offline before spending another model pass. To wake just that account without
resetting history or lowering the global interval, use
`nora memory profile-retry-media --key '<unit>#character:<person>'` (read-only),
then add `--apply` to queue one attempt. The command requires a listed account,
completed history and pending cached media; it never composes a second Nora or
calls a model. Duplicate pending submissions coalesce. The normal running
Dreamer consumes the request before reading its next batch, under its existing
per-key exclusion, schedule batch size and model budget. A failed/interrupted
attempt consumes it too: there is no automatic paid drain loop. The normal
schedule and other wake signals remain active. A request arriving during an
in-flight pass waits for the next eligible tick.

The request token and its claim reuse a separate
`profile-person:media-retry` consolidation namespace, retained in row history;
they never acknowledge a source or alter existing cursor timestamps. A claimed
request proves only that an attempt began. Read the normal Dreamer trace and
`profile-history` cached-media receipts afterwards; `unavailable` is not success,
and no-change can settle rendered inputs without adding any identity fact.
Stop after the first account to assess progress before requesting another.

## Extraction history backfill

`scripts/extraction_backfill.py` queues human messages from the last 61 days
(default; override with `--days N`). It never calls a model itself. The existing
worker drains them when `[memory.extraction] enabled = true`; processing incurs
that worker's ordinary model usage. Use the current deployed policy file: room
ownership, including aliases, is resolved exactly as live enqueue resolves it
**today**. Unmapped rooms/platforms, Nora's replies, hidden and deleted messages
are skipped. Unattributed humans and media-only messages remain eligible.
Already queued rows and replay tombstones are preserved, never reassigned.

From a checkout with `DATABASE_URL` already set, first inspect counts:

```bash
uv run python scripts/extraction_backfill.py --policy policy.toml --days 61 --dry-run
```

Output is JSON containing only window timestamps, eligible/already queued/would
enqueue/enqueued counts and skips by reason; no message text. Dry-run uses a
PostgreSQL read-only transaction. Copy its `before_ms` value to freeze the same
half-open timestamp window for execution and later completion checks:

```bash
uv run python scripts/extraction_backfill.py --policy policy.toml --days 61 --before-ms <before_ms>
uv run python scripts/extraction_backfill.py --status
```

The enqueue is idempotent, oldest message timestamp first (id breaks ties).
Live sources have priority over historical sources, including **due historical
retries**; a live message arriving during a historical call is eligible at the
next worker take after its normal minimum age. Calls already in flight finish
normally. History remains oldest-first, uses the existing snapshot/context
builder and retry allowance, and may wait during sustained live traffic. No
second worker or new configuration is required.

Status covers **all rows marked historical**, regardless of the command's time
window or current policy, and counts queue rows by `unbatched`, `pending`, `done`,
`failed` and `retired`. `claims_written` counts their currently active claims.
Each failed batch is listed by id with failure-stage attempt counts; old attempts
and expired leases without a recorded stage are `unknown`, never inferred.
Pruned source tombstones without a batch count as retired. A batch may contain
many source rows, so these row counts are not batch counts.

**Done:** the same frozen-window dry-run reports `would_enqueue = 0`, and status
reports zero `unbatched`, `pending` **and** `failed` rows. Review any retired rows:
withdrawal/edit/pruning can invalidate a reading, so zero claims is not proof of
success. Claims can legitimately be empty when a completed batch asserted
nothing; check `done` as well. A failed batch is not done. After repairing its
cause, retain the failed attempt history and release its sources with:

```bash
uv run python scripts/extraction_requeue.py <failed_batch_id>
uv run python scripts/extraction_backfill.py --status
```

Requeue keeps historical priority on those sources. Repeat status after the
worker has processed them; do not mark a failed or unavailable read as complete.
