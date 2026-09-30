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
ln -sf ~/workspace/nora/deploy/computer/nora-computer.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now nora-computer.timer
```

User lingering must be on (`loginctl show-user "$USER" -p Linger`), or the
timer stops at logout.

## Operating it

| want | run |
|---|---|
| state | `nora-computer status` |
| logs | `journalctl --user -u nora-computer -n 50`; computerd's own: `sbx exec -u root nora-computer cat /var/log/computerd.log` |
| new image | `nora-computer build`, then `nora-computer destroy`; the timer recreates it within a minute. **Destroy deletes /workspace**: export what matters first |
| what the network refused | `sbx policy log nora-computer` |

The CLI needs `DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent` when run by
hand on a headless host (the script sets it): the desktop keyring is locked.
Under sbx a TCP connect succeeds to any address, so health is always an HTTP
body from `/v1/health`, never a port check.
