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

## Network egress: PyPI and the Ubuntu archive, nothing else

sbx's policy is deny-all (`sbx policy ls` shows `default-deny-all` and
`local-policy`). The VM may fetch packages and nothing more (operator ruling,
2026-10-01), so `nora-computer` adds these allow rules, **scoped to the
`nora-computer` sandbox** (`--sandbox`), in its `EGRESS_ALLOW` list:

| host | port | for |
|---|---|---|
| `pypi.org` | 443 | pip: the index |
| `files.pythonhosted.org` | 443 | pip: the wheels and sdists |
| `archive.ubuntu.com` | 80, 443 | apt (its default sources are plain HTTP) |
| `security.ubuntu.com` | 80, 443 | apt security updates |

Nothing of Nora's own services is on it: not computerd's own port, Honcho,
OmniRoute, Langfuse, the LAN or the tailnet. The list is part of `serve`
(`prepare` applies it on every start, idempotently, because a rule lives in
sbx's daemon and not in the VM, so a recreate or a policy reset loses it), and
it is applied by hand with `nora-computer egress apply`.

Read it back, both halves (the allowlist reads **allowed**, what must stay shut
reads **denied**), from sbx's own authorizer:

```bash
nora-computer egress check; echo "RC=$?"     # 0 all as required, 1 a violation, 2 sbx would not say
nora-computer status                         # ... egress=ok|WRONG|unknown
sbx policy ls --wide                         # the rules themselves, with ids
sbx policy log nora-computer                 # what the proxy allowed and refused, with counts
```

A fetch from inside is the end-to-end proof (`pip download` of a small package
as a scope user; a `curl` to `example.com` must get HTTP 403). `egress check` is
the read-back that does not depend on a package existing. The policy log's
refusals of `pypi.org` and the Ubuntu hosts stop appearing once the rules are in.

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
- Each event is then replayed into `computer_events` through
  `docker compose exec db psql` (`ON CONFLICT (operation_id, phase) DO NOTHING`,
  so replaying the whole spool is safe at any time). A db that is not up yet
  logs `not yet in Postgres`; `nora-computer sync-events` replays it by hand.
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
- **Not in it:** each room's `browser/` directory (a Chromium profile is a
  credential store; its cookies are never backed up, design §7) and computerd's
  `.tmux` sockets. A project folder that happens to be called `browser` is kept.
- **It is rollback, not off-host backup** (design §7): the same machine, the same
  disk. Copy `~/nora-backups` off the host if the host's loss matters.
- **`nora-computer destroy` takes a backup first and refuses if it cannot**
  (exit 9, the VM untouched). `nora-computer destroy --no-backup` deletes
  `/workspace` without one, on purpose, and the audit row says so.
- A failed run leaves the unit `failed`: `systemctl --user --failed`,
  `journalctl --user -u nora-computer-backup -n 30`.

Restore (after a recreate the VM's `/workspace` is empty):

```bash
ls -lt ~/nora-backups/computer-workspace/ | head                   # pick an archive
deploy/computer/nora-computer restore ~/nora-backups/computer-workspace/workspace-<time>.tar.gz
```

It needs the archive's `.sha256` beside it and checks it, refuses when the VM's
`/workspace` already holds rooms (restoring over them would overwrite files; `--force`
if that is meant), extracts as root preserving modes, and gives each room's
directory to `c-<first 12 hex of the scope>`, creating that user the way computerd
does if it is not there yet: the uids inside an archive mean nothing in a new VM.
Every backup, restore and destroy is a lifecycle audit row.

Read it back (the nightly job ran, and the archive is a good one):

```bash
systemctl --user list-timers nora-computer-backup.timer          # LAST and NEXT
ls -l ~/nora-backups/computer-workspace/ | tail -n 4
f=$(ls -t ~/nora-backups/computer-workspace/workspace-*.tar.gz | head -n 1)
(cd "$(dirname "$f")" && sha256sum -c "$(basename "$f").sha256") && tar -tzf "$f" | head
```

## Operating it

| want | run |
|---|---|
| state | `nora-computer status` |
| stop it | `systemctl --user stop nora-computer` FIRST, then `nora-computer stop`: while the unit runs, `Restart=always` and its `sbx exec` start the VM again within seconds |
| logs | `journalctl --user -u nora-computer -n 50`; computerd's own output is in the unit's journal (it runs in the foreground) |
| new image | `nora-computer build`, `nora-computer destroy`, `systemctl --user restart nora-computer`, then `nora-computer restore <the archive destroy printed>`. **Destroy deletes /workspace**: it takes a verified backup first and refuses without one, unless you pass `--no-backup` (see "Backup and restore") |
| back up now / restore | `nora-computer backup`; `nora-computer restore ARCHIVE [--force]` |
| what the network refused | `nora-computer policy-log` (the audited form of `sbx policy log nora-computer`) |
| token rotated | `systemctl --user restart nora-computer` |
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
