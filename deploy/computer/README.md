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

## Operating it

| want | run |
|---|---|
| state | `nora-computer status` |
| stop it | `systemctl --user stop nora-computer` FIRST, then `nora-computer stop`: while the unit runs, `Restart=always` and its `sbx exec` start the VM again within seconds |
| logs | `journalctl --user -u nora-computer -n 50`; computerd's own output is in the unit's journal (it runs in the foreground) |
| new image | `nora-computer build`, `nora-computer destroy`, then `systemctl --user restart nora-computer`. **Destroy deletes /workspace**: export what matters first |
| what the network refused | `sbx policy log nora-computer` |
| token rotated | `systemctl --user restart nora-computer` |

The CLI needs `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` when run by
hand on a headless host (the script sets it): the desktop keyring is locked.
Under sbx a TCP connect succeeds to any address, so health is always an HTTP
body from `/v1/health`, never a port check.


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
