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
