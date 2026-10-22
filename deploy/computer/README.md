# Nora's computer

The persistent microVM that Nora's computer-use tools run on
(`design/sandbox-runtime/00-design.md`). It is a Docker Sandboxes (sbx) VM
booted from `Dockerfile`, and it runs `computerd.py`, a small HTTP API with
Nora as its only client. Nora reaches it at `http://172.17.0.1:18765`
(docker0: reachable from the nora container, not from the LAN) with a bearer
token. `nora-computer` is the only thing that runs `sbx`.

## One-time setup on the deploy host

As the user that will own the VM:

```bash
sudo apt install docker-sbx && sudo usermod -aG kvm "$USER"   # then log in again
echo "<docker PAT>" | sbx login --username <docker-id> --password-stdin
echo "NORA_COMPUTER_TOKEN=$(openssl rand -hex 32)" >> ~/workspace/nora/.env
~/workspace/nora/deploy/computer/nora-computer build
mkdir -p ~/.config/systemd/user
ln -sf ~/workspace/nora/deploy/computer/nora-computer.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now nora-computer.service
# the nightly /workspace backup (see "Backup and restore"):
ln -sf ~/workspace/nora/deploy/computer/nora-computer-backup.service \
       ~/workspace/nora/deploy/computer/nora-computer-backup.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now nora-computer-backup.timer
```

User lingering must be on (`loginctl show-user "$USER" -p Linger`), or the
service stops at logout.

**Why detached, and why a long-running service:** sandboxd stops a local
sandbox 30 s after its last session disconnects, whatever still runs inside,
unless the sandbox was created detached (`sbx run -d`). The flag is set at
creation only, so a sandbox made by `sbx create` must be destroyed and
recreated (measured 2026-09-30). The service runs computerd in the foreground
of an `sbx exec` so systemd restarts it if it exits; the VM's life no longer
depends on that session. `sbx run` needs a read-write primary workspace: an
empty dedicated host directory under `~/.local/state/nora-computer/`, which
nothing on the host reads.

## Network egress: the public web, the private ranges denied

sbx's policy is deny-all (`sbx policy ls` shows `default-deny-all` and
`local-policy`). Nora's computer browses the public web (operator ruling,
2026-10-07, ADR 0022 Decision 2, which replaced the 2026-10-01 PyPI-and-Ubuntu
allowlist of DEC-260), so `nora-computer` adds these rules, **scoped to the
`nora-computer` sandbox** (`--sandbox`), from its `EGRESS_ALLOW` and
`EGRESS_DENY` lists, with no extra proxy:

- **allow** `**` (every host, TCP). It is a glob to bash as well: the script
  quotes it everywhere, and a test pins that sbx receives it as one literal argument.
- **deny** these ranges, which a deny rule keeps out of the `**` allow:

| range | what it keeps Nora's computer away from |
|---|---|
| `10.0.0.0/8` | private network |
| `172.16.0.0/12` | docker0 (`172.17.0.1`): computerd's own published port, OmniRoute |
| `192.168.0.0/16` | the LAN |
| `127.0.0.0/8` | the host's loopback: Ollama, anything bound to localhost |
| `100.64.0.0/10` | the tailnet (CGNAT) |
| `169.254.0.0/16` | link-local, cloud metadata |

**Known, accepted limit.** sbx applies a CIDR deny to **IP literals only**: a
public NAME that resolves to a private address (`127-0-0-1.sslip.io`) still
passes the deny and reaches the host's services. S1 measured it
(`design/browser-runtime/04-s1-runbook.md`, step 4) and the operator accepted the
risk rather than add a guard; there is none. `egress check` probes IP literals, so
it cannot see this. The network audit is `sbx policy log`.

The policy is part of `serve` (`prepare` applies it on every start, idempotently,
because a rule lives in sbx's daemon and not in the VM, so a recreate or a policy
reset loses it), and it is applied by hand with `nora-computer egress apply`.

`nora-computer egress apply` reads sbx's rule list first and adds only what is missing
(it never trusts `sbx policy allow` or `deny` to de-duplicate), reads the allow rule
back (a deny CIDR has no verdict of its own: `egress check` is what proves it works),
and exits 0
when every rule is in place and reads allowed, 1 when a rule could not be added or
reads denied, 2 when sbx would not give a verdict; `egress check` has the same
three codes.

A verdict is the first line of `sbx policy check network` **and** its exit
status: the real tool exits 0 with `Allowed:` for an allowed host and **1** with
`Denied:` for a denied one (measured 2026-10-01), so the script reads `Allowed`
at exit 0 as allowed, `Denied` at exit 0 or 1 as denied, and everything else
(empty output, a crashed sbx, an `Allowed` line with a failing exit) as unknown,
which is never an allow.

Read it back, both halves, from sbx's own authorizer. Allowed: `pypi.org:443`,
`archive.ubuntu.com:80`, `example.com:443`. Denied, as IP literals: computerd's own
port (`172.17.0.1:18765`), OmniRoute (`172.17.0.1:20128`), Ollama on the loopback
(`127.0.0.1:11434`), and one address in every other denied range (`10.0.0.1:80`,
`192.168.1.1:80`, `100.64.0.1:80`, `169.254.169.254:80`), so one missing deny shows.

**`apply` never adds `**` unless all six denies read back from `sbx policy ls` as
this sandbox's own** (a rule of `nora-computer-eval` is not ours): an add that
failed, one that reported success but did not land, or a rule list sbx will not
print each leave the web closed and exit 1. A `**` already in place from an
earlier apply is not removed by that; `egress check` and `status` report the
missing deny. `serve` runs the check after every apply.

```bash
nora-computer egress check; echo "RC=$?"     # 0 all as required, 1 a violation, 2 sbx would not say
nora-computer status                         # ... egress=ok|WRONG|unknown
sbx policy ls --wide                         # the rules themselves, with ids
sbx policy log nora-computer                 # what the proxy allowed and refused, with counts
```

A fetch from inside is the end-to-end proof (`curl` to `https://example.com` as a
scope user must succeed; to `http://172.17.0.1:18765` it must be refused).
`egress check` is the read-back that does not depend on a fetch.

## Lifecycle audit: who did what to the machine

The computer's audit (design/sandbox-runtime §12) covers every tool call, and
until 2026-10-01 nothing else: a VM create, a recreate, a computerd restart or a
token rotation left no row. `nora-computer` runs on the host as the operator,
outside the nora container and outside the VM, so it cannot append to Postgres
the way the tools do; it keeps a **host-side spool first** and replays it into
`computer_events`:

- The spool is `~/.local/state/nora-computer/lifecycle-events.jsonl`, one JSON
  line per event, written before anything else. It survives Postgres being down
  at boot, which is the ordinary case: the unit starts before the db is healthy.
- Each event is then synced into `computer_events` through
  `docker compose exec db psql` (`ON CONFLICT DO NOTHING` with no conflict target,
  so a row already there is skipped on whichever unique key it hits, and two
  syncs racing on the same event cannot fail each other). A sync sends only what
  is **after the offset the last good sync reached** (`lifecycle-events.synced`
  beside the spool, moved forward only after psql succeeded), not the whole
  history. A db that is not up yet logs `not yet in Postgres` and leaves the
  offset alone; `nora-computer sync-events` retries by hand, and
  `sync-events --all` replays everything. A line in the spool that is not a valid
  event is logged and skipped, never raised, so one bad line cannot park the
  events after it; a last line with no newline is a write in progress and is left
  for the next sync.
- Rows have `actor = 'operator'` (a value the table's constraint already
  allowed: no migration), `tool = 'computer_vm'`, and the event in `request`
  (`{"event": ..., "name": ..., "host": ...}`) with its facts in `result`. The
  append-only triggers are untouched: nothing here updates or deletes.
- The kinds: `create`, `recreate` (a create after an earlier one: `/workspace` was
  lost to it), `destroy`, `computerd_start` (every `serve` start: a crash restart,
  a deploy, a boot), `token_rotation`, `policy_log_read`, `egress_apply`,
  `backup`, `restore`. A rotation is the token's SHA-256 fingerprint (12 hex,
  never the token) differing from the last start's.
- `nora-computer policy-log [--json]` is the audited way to read `sbx policy log`:
  it records the read first and does not read if it could not record it.

Read it back:

```bash
cd ~/workspace/nora && docker compose exec -T db psql -U nora -d nora -Atc \
  "select created_at, request->>'event', status, result::text from computer_events where tool = 'computer_vm' order by created_at desc limit 10" </dev/null
tail -n 3 ~/.local/state/nora-computer/lifecycle-events.jsonl      # the spool
```

Rows count only; read the table, not the spool, for what the record says.
`nora-computer sync-events` and a re-read are how a row that is in the spool and
not in the table is closed.

## Backup and restore

A **recreate deletes /workspace**, and the audit of 2026-10-01 saw three of them in
24 hours: the VM's disk is the only copy of every room's files. So:

- **Nightly**, `nora-computer-backup.timer` (03:20, persistent: a night the host was
  off is made up) runs `nora-computer backup`: a tar of `/workspace` made inside
  the VM as root, copied out with `sbx cp`, **verified** (the digest computed in
  the VM must equal the copy's, and the copy must read back as a gzip tar, or it is
  deleted and the run fails), and written to
  `~/nora-backups/computer-workspace/workspace-<UTC time>.tar.gz` with a
  `.sha256` beside it, mode 700. The newest 14 are kept
  (`NORA_COMPUTER_BACKUP_KEEP`; a zero or a non-number prunes nothing, and a failed
  run never removes an older archive). A stopped VM is skipped, not started: `sbx
  exec` would start it, and a stopped disk has not changed.
- **Inside the VM the archive is never in the shared `/tmp`.** It is made under
  `umask 077` in a `mktemp -d` directory (root's, mode 0700), copied out, and the
  whole directory is removed and checked gone; a directory that cannot be removed
  fails the backup (exit 5) and names the path to remove by hand, because a
  leftover archive of every room's files is the exposure.
- **A VM with no rooms is not backed up.** After a recreate `/workspace` is empty;
  a backup of that would verify, count toward the 14, and in 14 nights push out the
  last backup that holds any room. It logs `holds no rooms; nothing to back up`,
  makes no archive and prunes nothing.
- **Not in it:** each room's `browser/` directory (a Chromium profile is a
  credential store; its cookies are never backed up, design §7) and computerd's
  `.tmux` sockets. A project folder that happens to be called `browser` is kept.
- **It is rollback, not off-host backup** (design §7): the same machine, the same
  disk. Copy `~/nora-backups` off the host if the host's loss matters.
- **`nora-computer destroy` takes a backup first and refuses if it cannot**
  (exit 9, the VM untouched). That backup is filed under
  `~/nora-backups/computer-workspace/destroy/`, OUTSIDE the nightly rotation, so
  nothing ever prunes it. `nora-computer destroy --no-backup` deletes `/workspace`
  without one, on purpose, and the audit row says so.
- A failed run leaves the unit `failed`: `systemctl --user --failed`,
  `journalctl --user -u nora-computer-backup -n 30`.

Restore (after a recreate the VM's `/workspace` is empty):

```bash
ls -lt ~/nora-backups/computer-workspace/ | head                   # pick an archive
deploy/computer/nora-computer restore ~/nora-backups/computer-workspace/workspace-<time>.tar.gz
```

It needs the archive's `.sha256` beside it and checks it, then **reads the archive on
the host and refuses it whole** if any member could write outside a room: an absolute
or `..` name, a top level that is not a room directory, a device node or fifo, a
link that leaves its own room (to `/etc`, to another room, to anywhere above), or a
member written through a link in the archive (`archive_check.py`; exit 3, nothing
copied to the VM). It refuses when the VM's `/workspace` already holds rooms
(restoring over them would overwrite files; `--force` if that is meant). It copies the
archive into a private `mktemp -d` directory in the VM (never a fixed shared path),
checks the digest AGAIN inside the VM on the file tar will read (exit 4 if it
differs, nothing extracted), extracts as root preserving modes into a private
staging directory, and removes it. Each room is then REPLACED, not deleted first:
the installer holds `/workspace/.restore.lock` exclusively (every computerd room
operation holds it shared, so no command is mid-write), renames the old room aside,
renames the staged tree onto the EXACT destination (`mv -T`; a directory a writer
recreated there makes it fail rather than nest the new tree inside it), and only
after `chown -hR` to `c-<first 12 hex of the scope>` succeeds deletes the old copy
(a link is never followed). A failed move or chown puts the old room back; if the
destination is occupied by a writer outside computerd, the old data is kept in a
`.restore-old.*` directory and the failure line names it. A process that outlives
its command (`nohup`, a tmux pane) is outside that lease, so stop long-lived room
work before a `--force` restore; the old tree is still preserved on failure.
Every backup, restore and destroy is a lifecycle audit row.

Read it back (the nightly job ran, and the archive is a good one):

```bash
systemctl --user list-timers nora-computer-backup.timer          # LAST and NEXT
ls -l ~/nora-backups/computer-workspace/ | tail -n 4
f=$(ls -t ~/nora-backups/computer-workspace/workspace-*.tar.gz | head -n 1)
(cd "$(dirname "$f")" && sha256sum -c "$(basename "$f").sha256") && tar -tzf "$f" | head
```

## Rotating the token

computerd reads its bearer token when `nora-computer serve` starts it, and **nora
reads `NORA_COMPUTER_TOKEN` from the environment when her container is created**
(`docker-compose.yml` passes it through `environment:`). So restarting the unit
alone, which this file used to say, leaves nora holding the old token: every
computer call is a 401 and `nora_computer_up` goes to 0 (`ComputerDown` fires after
5 minutes). `/v1/health` stays green throughout, which is why `nora-computer
status` reads `computerd=up` and not `healthy` in that state.

One procedure, in this order, on the deploy host (take `nora-deploy`'s lock first:
step 4 recreates nora):

1. Make the new token, and keep the old one for step 6. Neither is printed.
   ```bash
   cd ~/workspace/nora && umask 077
   OLD=$(grep -E '^NORA_COMPUTER_TOKEN=' .env | tail -n 1 | cut -d= -f2-)
   NEW=$(openssl rand -hex 32)
   cp .env ~/nora-backups/env.$(date +%Y%m%dT%H%M%S)          # the rollback copy
   ```
2. Write it to `.env` (the unit and compose both read it) **without the token on
   any command line**: `printf` is a shell builtin, so the value never reaches
   an `argv` another user's `ps` or `/proc` can read (a `sed` whose
   expression holds `$NEW` would). The new file is made under the `umask 077` from step 1 and copied
   over `.env` so the file keeps its inode and mode:
   ```bash
   { grep -v '^NORA_COMPUTER_TOKEN=' .env; printf 'NORA_COMPUTER_TOKEN=%s\n' "$NEW"; } > .env.new \
     && cat .env.new > .env && rm -f .env.new
   grep -c '^NORA_COMPUTER_TOKEN=' .env                       # 1
   ```
3. Restart computerd's unit, so it starts with the new token:
   ```bash
   systemctl --user restart nora-computer
   ```
4. **Recreate nora**, because a restart keeps the old environment:
   ```bash
   docker compose up -d --force-recreate nora </dev/null
   ```
5. Wait for both to be up: `deploy/computer/nora-computer status` reads
   `computerd=healthy` (it asks something that needs the token).
6. Verify from both sides, with the tokens in header files so neither is on a
   command line:
   ```bash
   printf 'Authorization: Bearer %s\n' "$OLD" > /tmp/h.old
   printf 'Authorization: Bearer %s\n' "$NEW" > /tmp/h.new
   B='{"scope": "0000000000000000", "pattern": "*"}'
   U=http://172.17.0.1:18765/v1/files
   curl -s -o /dev/null -w 'old token: %{http_code}\n' -H @/tmp/h.old -H 'Content-Type: application/json' -d "$B" $U   # 401
   curl -s -o /dev/null -w 'new token: %{http_code}\n' -H @/tmp/h.new -H 'Content-Type: application/json' -d "$B" $U   # 200
   rm -f /tmp/h.old /tmp/h.new
   curl -s localhost:9100/metrics | grep '^nora_computer_up'  # ... 1.0, from nora's own side
   ```
   Old token 401 and new token 200 means computerd took it; `nora_computer_up` 1.0
   means **nora holds it** (the gauge reads 1 only after a call or the 30-second
   probe got an answer, so give it a minute).
7. The rotation is an audit row: `token_rotation` with the two fingerprints (never a
   token) in `computer_events` (see "Lifecycle audit"). A rotation that left nora
   on the old token shows as `ComputerDown`, not silence.

Rollback is the same procedure with the rollback copy of `.env`.

## Operating it

| want | run |
|---|---|
| state | `nora-computer status` |
| stop it | `systemctl --user stop nora-computer` FIRST, then `nora-computer stop`: while the unit runs, `Restart=always` and its `sbx exec` start the VM again within seconds |
| logs | `journalctl --user -u nora-computer -n 50`; computerd's own output is in the unit's journal (it runs in the foreground) |
| new image | `nora-computer build`, `nora-computer destroy`, `systemctl --user restart nora-computer`, then `nora-computer restore <the archive destroy printed>`. **Destroy deletes /workspace**: it takes a verified backup first and refuses without one, unless you pass `--no-backup` (see "Backup and restore") |
| back up now / restore | `nora-computer backup`; `nora-computer restore ARCHIVE [--force]` |
| what the network refused | `nora-computer policy-log` (the audited form of `sbx policy log nora-computer`) |
| token rotated | **two steps, not one**: see "Rotating the token" |
| a lifecycle row is missing in Postgres | `nora-computer sync-events` |

The CLI needs `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` when run by
hand on a headless host (the script sets it): the desktop keyring is locked.
Under sbx a TCP connect succeeds to any address, so health is always an HTTP
body from `/v1/health`, never a port check.


## nora-render

`nora-render chart|mermaid IN OUT` (`render/`) is Smith's image renderers as
one CLI: each makes a picture (SVG, or PNG for a link or the chat), never a
page. A document, maps included, is the document path's (ADR 0020), not the
computer's. Both run offline: `chart` is standard-library Python (its PNG
through librsvg's `rsvg-convert`), and `mermaid` is mermaid-cli with its own
Chromium, both in the image. Measured 2026-10-01 in a throwaway `docker run
--network=none`. There is no map renderer, so the computer needs no Google
host allowed and no Maps key.

## The browser (B1, ADR 0022)

Google Chrome stable (pinned by version and sha256 in the `Dockerfile`) and
Xvfb are in the image. computerd starts one Chrome per room on the room's
first connection to `GET /v1/browser?scope=<scope>`, a websocket behind the
same bearer token: each text frame is one CDP message, relayed to Chrome's
`--remote-debugging-pipe`.

- **No debugging port.** Chrome runs headed on its own Xvfb display
  (`-nolisten tcp`), both as the room's user, and computerd holds the pipe.
  Read on a VM built from this image (2026-10-07): only port 80 listened,
  and another room's command could read neither the room's display cookie
  (`browser/.Xauthority`, `0600`), its profile (`browser/default`) nor
  Chrome's file descriptors.
- **One client per room's browser;** a second is `denied`. At most two rooms
  have a browser at once (`MAX_BROWSERS`); a third stops the least recently
  used idle one, or is `denied` when both have a client. A browser idle for
  10 minutes with no client is stopped. `POST /v1/browser_stop` stops one,
  and `/v1/state` reports `browser.running`.
- **What a room's command could plant is never trusted.** `/tmp/.X11-unix`
  is made by the image and checked at computerd's start without following a
  link (one that is not root's own is refused, never adopted); leftover or
  planted names at a display number are removed before it is used; our Xvfb
  is ready only when its socket is a socket owned by the room's user;
  `chrome.log` is created fresh and renamed into place, so a FIFO or link
  there is replaced, never opened.
- A client that reconnects to a running browser may receive late answers
  and events meant for the previous client; CDP ids restart at 1, so a client
  re-attaches to its targets rather than trusting an early answer.
- Chrome's stderr is in the room's `browser/chrome.log`, emptied at each
  start. `navigator.webdriver` reads `false`
  (`--disable-blink-features=AutomationControlled`).
- Nothing uses it yet: `computer_browse` is B2. Pages load only from hosts
  the egress policy allows.

## Upgrading from the S2a timer (once)

S2a made the sandbox with `sbx create`, which sandboxd auto-stops, and ran a
minute timer. The new unit needs a sandbox created detached:

```bash
systemctl --user disable --now nora-computer.timer 2>/dev/null
rm -f ~/.config/systemd/user/nora-computer.timer
deploy/computer/nora-computer build
deploy/computer/nora-computer destroy          # deletes /workspace: export anything that matters first
ln -sf ~/workspace/nora/deploy/computer/nora-computer.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now nora-computer.service
deploy/computer/nora-computer status           # sandbox=running computerd=healthy
```

Destroying is also what makes the per-room users clean: a workspace made
before them would keep files owned by the shared `computer` user.
