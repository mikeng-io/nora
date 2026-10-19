# deploy/session-probe — is the login still alive? (#628)

The OpenCLI Chrome (`nora-social-chrome.service`, profile `Profile 1` in its
own data folder `~/.local/share/nora-browser`, signed in as Nora's
own Google account) is the **single owner** of the
Instagram, X and LinkedIn sessions. The `instagram-bridge` container is a
**follower**: it holds a copy of Chrome's Instagram cookies in
`state/instagram/cookies.json`. This probe runs on the host every ~10 minutes
and answers two questions with evidence: is each session alive in Chrome, and
does the follower still agree with the owner.

It never logs in to anything. A dead session in Chrome needs a human in VNC.

## What one run does

1. Copies Chrome's `Cookies` SQLite file to a temp path, reads it, deletes the
   copy, and decrypts the `v10` values. Values are never logged.
2. Reads the bridge's `session` and `connected` fields with `docker exec instagram-bridge curl
   http://127.0.0.1:18082/health` (the bridge publishes no host port).
   `/health` answers **200 in every session state by contract**
   (`deploy/instagram-bridge/internal/domain/entities.go`), so the probe reads
   the body, never the status code. `session: "authenticated"` with
   `connected: false` is a bridge whose realtime socket Instagram refuses
   (`4003 Unauthorized 401`) while its own state still says authenticated (the
   2026-09-29 failure). Seen on **two consecutive runs** it becomes the session
   `disconnected` (one run is only recorded: a bridge that just restarted is
   briefly disconnected); `bridge_disconnected_runs` in `carry` counts the
   streak and resets on any run that is connected, unreachable or not
   authenticated, and after the probe's own restart (the fresh boot is
   disconnected for a moment too). **Known trade-off:** the bridge stays
   `authenticated` while it merely cannot reach Instagram, so a network outage
   that spans two runs 10 minutes apart, with Chrome's cookie differing from
   the bridge's (the normal state after any re-login in Chrome), makes the
   probe replace the bridge's jar with Chrome's. Both are the same account and
   the old jar is kept as `cookies.json.bak-*`; the worst case is Chrome's
   cookie already being revoked, which ends as an alert.
3. At most once an hour per network (with jitter), runs the read-only
   `opencli <twitter|linkedin> whoami`. HTTP 429 is recorded as ERROR, not
   «logged out». The time of the last check is carried in the state file, so
   the 10-minute timer does not repeat it. **Instagram has no such check**: its
   `whoami` answered 429 every time, and the bridge's `/health` plus the cookie
   comparison is the Instagram signal.
4. **First, recovery**: if the marker exists, runs `docker start` (idempotent)
   before reading health. rc 0 removes the marker (`recovery_start: "ok"` in
   the run's line). A failure keeps it, records `recovery_start:
   "start_failed:rc=N"`, and raises ERROR plus an alert
   `bridge_stopped_by_probe_start_failed`: the probe itself stopped the bridge
   and could not start it, start it by hand (`docker start instagram-bridge`).
   No marker means the probe never starts the bridge: one a human stopped stays
   stopped and reads ERROR `bridge_unreachable`. The run then continues; health
   is read after the start, so it may still say unreachable for a few seconds
   while the bridge boots.
5. Decides (`nora/infra/social/session_probe/decide.py`, pure) and acts:
   - bridge `logged_out`/`checkpointed`/`disconnected` and Chrome's Instagram cookie differs:
     **SYNC** — `docker stop instagram-bridge` (its shutdown flushes its old
     jar over `cookies.json`, so the write must come after), back up
     `cookies.json` (last 5 kept), merge Chrome's cookies over it, atomic
     write at mode 0600, `docker start instagram-bridge`.
   - **The stop marker.** Before `docker stop` the probe writes
     `bridge-stopped-by-probe` (mode 0600, content = UTC timestamp) next to the
     state file, and removes it only after `docker start` returns rc 0. A
     crash, a timed-out stop (rc 124, which the daemon may still finish), a
     failed start, or a failed start after a failed write all leave the marker.
     Runs are exclusive (`probe.lock` beside the state file): a run started by
     hand while the timer's run is going exits ERROR `probe_already_running`
     and touches nothing. To keep the bridge stopped on purpose, delete
     `bridge-stopped-by-probe` first, or the next run will start it.
     compose has `restart: unless-stopped`, so docker will not revive a
     stopped container by itself, and later health reads would only say
     «unreachable».
   - **CAPPED** after 3 consecutive syncs that did not reach `authenticated`
     and connected:
     no more restarts, an alert instead. A changed Chrome cookie (a human
     logged in again) restarts the count.
6. Appends ONE JSON line to the state file.

## Install

Commands use the reference checkout `~/workspace/nora`; substitute your path.
The service template uses systemd `%h` for the deployment user's home directory
in `ExecStart` and `Environment` values. Keep `%h` in unit files: systemd does
not expand shell `$HOME` references in `Environment=`. Adjust the Node binary
path to match your OpenCLI installation before enabling the service.

```bash
mkdir -p ~/.config/systemd/user
cp ~/workspace/nora/deploy/session-probe/nora-session-probe.{service,timer} \
   ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nora-session-probe.timer
systemctl --user start nora-session-probe.service     # one run now
journalctl --user -u nora-session-probe -n 20 --no-pager
```

Like the social bridge, it runs the CHECKOUT's code: an edit on `master` is a
deploy after the next run. Needs `docker` access for the deployment user and
reads `Cookies` while Chrome is running.

The service template explicitly sets `NORA_PROBE_CHROME_DB` to the deployment
user's Chrome cookie database and `NORA_PROBE_BRIDGE_DIR` to its bridge state.
Adjust these template values for your installation; they are not portable
application defaults.

Overrides (environment, all optional): `NORA_PROBE_CHROME_DB`,
`NORA_PROBE_BRIDGE_DIR` (the service template sets `%h/workspace/nora/state/instagram`),
`NORA_PROBE_BRIDGE_CONTAINER`, `NORA_PROBE_STATE_FILE`, `OPENCLI_BIN`,
`OPENCLI_PROFILE` (default `nora`; blank refuses to start),
`NORA_PROBE_LIVE_INTERVAL_S`.

## Reading the state file

`~/.local/state/nora/session-probe.jsonl`, one line per run, mode 0600.

```bash
tail -n 1 ~/.local/state/nora/session-probe.jsonl | jq .
jq -c '[.ts,.verdict,.bridge_session,.newer_sessionid,(.decisions|map(.action)|join(","))]' \
   ~/.local/state/nora/session-probe.jsonl | tail -n 30
```

Fields: `verdict` (PASS / FAIL / ERROR), `bridge_session` (`disconnected`
only after the two-run debounce), `bridge_connected` (bool, or null when
/health carried none or the bridge was unreachable), `chrome` (per network:
cookie `created` / `expires`), `instagram_sessionid.{chrome,bridge}` (sha256
prefixes, 8 hex), `newer_sessionid` (`chrome` / `bridge` / `same` / `unknown`,
by Chrome's creation time against the jar's mtime), `live`, `decisions`,
`sync`, `recovery_start` (only on a run that found the marker), `alerts_sent`, and `carry` (the state passed to the next run). To
measure session lifetime, watch `chrome.instagram.created` change and the gap
between changes.

Exit codes: 0 PASS, 1 FAIL (unit still counts as success), 2 ERROR (unit
fails). ERROR means the probe could not measure something (Chrome DB
unreadable, bridge unreachable, an x/linkedin opencli error or 429). It is never a PASS.

## What an ALERT means

Alerts go to the Discord review channel through `deploy/alert-bridge`
(Alertmanager-shaped payload, posted from inside its container), and each
`network:reason` is repeated at most every 12 hours.

| reason | meaning |
|---|---|
| `chrome_session_missing` / `_expired` / `_near_expiry` (<72h) | Chrome itself has no live session for that network |
| `live_check_logged_out` | Chrome has a cookie but the site says logged out |
| `bridge_logged_out_same_cookie` / `bridge_checkpointed_same_cookie` | the bridge already has Chrome's exact cookie and is still refused; a sync cannot help |
| `bridge_disconnected_same_cookie` | the bridge says authenticated but has stayed disconnected for two runs, and already holds Chrome's exact cookie; a sync cannot help |
| `bridge_stopped_by_probe_start_failed` | the probe stopped the bridge and `docker start` failed (this run or a recovery run); the marker stays until a start succeeds, so start it by hand |
| `sync_capped` | 3 syncs in a row did not restore the bridge; the probe stopped restarting it |

**The fix for every one of them is a human login in Chrome over VNC**
(`<tailscale-ip>:5902`, see `docs/runbook.md` §10). Once Chrome holds a fresh
session, the next run syncs the bridge by itself.
