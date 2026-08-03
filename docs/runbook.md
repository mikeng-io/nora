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

## 3. "Rollback to v1"

```bash
sudo systemctl stop nora
git checkout master
cp data/messages.db.v1.bak data/messages.db      # restore the pre-migration v1 database
sudo systemctl start nora
```

`git checkout master` returns the working tree to the v1 codebase. The v2 migration
(`nora.store.db.init`) is additive, so `messages.db` after v2 has run may contain
columns/rows the v1 binary doesn't expect — restore `messages.db.v1.bak` (the backup taken
before the v1→v2 schema migration, per Task 44 Step 1) rather than pointing v1 at a
v2-migrated file.

## 4. "The watchdog alerted"

`deploy/watchdog.sh` polls `/health` and alerts if the SSE listener has been silent for more
than 300 seconds (no ingest event in that window).

1. Check signal-cli first — it's the more common cause:

   ```bash
   curl -i http://127.0.0.1:18080/api/v1/check
   ```

   If that's not a healthy 200, restart or investigate signal-cli itself before touching
   Nora.

2. If signal-cli looks fine, check Nora's own logs via journalctl (she runs under systemd):

   ```bash
   journalctl -u nora -n 200 --no-pager
   ```

   Look for reconnect-backoff messages, unhandled exceptions in the SSE listen loop, or a
   process restart. SSE reconnects automatically with exponential backoff, so a brief blip
   self-heals; a sustained >300s silence with `sse.connected: false` in `/health` after that
   means the reconnect loop itself is stuck and the process likely needs a restart
   (`sudo systemctl restart nora`).

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
