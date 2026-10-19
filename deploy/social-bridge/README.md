# deploy/social-bridge — the host side of the X and LinkedIn tools

The `x_read` and `x_search` tools (DEC-201, which retired DEC-151's
`social_fetch`), and `linkedin_read` and `linkedin_search`, read X and
LinkedIn through OpenCLI, which
exists only on the host (daemon on `localhost:19825`, driving the
authenticated Chrome from `nora-social-chrome.service` via the Browser Bridge
extension). Nora runs in a Docker container on the private compose `nora`
network — NOT host networking — so this bridge is the seam: her tool POSTs
`{platform, what, target, limit}` to `http://host.docker.internal:19826/read`
(`host.docker.internal` is the docker0 gateway the container reaches the host
at), and this service runs `opencli <site> <command> <target> -f json` as a
subprocess and returns the JSON.

**The bridge is the read-only security boundary.** It re-enforces its own
frozen `(site, command)` allowlist host-side (`READ_ONLY_ALLOWLIST` in
`nora/infra/social/bridge_server.py`) and refuses everything else before any
subprocess is spawned — OpenCLI's write commands (`twitter post/like/follow/
delete`, `instagram post/like/follow/comment`, …) are unreachable through it,
by construction, even if the model-facing tool is prompt-injected by the
content it reads. There is no free-form command passthrough.

**It binds `127.0.0.1` plus the docker0 gateway (`172.17.0.1`).** There is no
authentication on `/read`; the allowlist is the boundary, and loopback is what
keeps that boundary from extending to anyone who can reach the port. The
docker0 gateway is added because Nora's container is not on host networking —
docker0 is host-internal (never the LAN), and it is the address
`host.docker.internal` resolves to inside the container. Neither bind ever
appears on a LAN interface.

**It runs the CHECKOUT's code** — `uv run --project ~/workspace/nora
python -m nora.infra.social` — so it tracks `master` the same way the running
container's bind mounts do: an edit landing on `master` is a deploy here after
a restart. It reads no `config.toml`; env vars only (`OPENCLI_BIN`,
`OPENCLI_PROFILE` (default `nora`: every read names Nora's Browser Bridge
profile, never OpenCLI's default, which is the operator's browser),
`NORA_SOCIAL_BRIDGE_PORT`, `NORA_SOCIAL_BRIDGE_HOST`). `OPENCLI_BIN` must name
the real binary (the template uses `%h/.local/bin/opencli`, expanded by systemd
to an absolute path) — the systemd USER service
environment does not carry the interactive shell's PATH, so a bare `opencli`
fails with `FileNotFoundError`.

Install (systemd **user** service, like the desktop/Chrome units). Commands use
the reference checkout `~/workspace/nora`; substitute your path. The service
template uses systemd `%h` for the deployment user's home directory, including
`Environment=OPENCLI_BIN=%h/.local/bin/opencli`. Keep `%h` in unit files:
systemd does not expand shell `$HOME` references in `Environment=`. Adjust the
Node binary path to match your OpenCLI installation before enabling it:

```bash
systemctl --user link ~/workspace/nora/deploy/social-bridge/nora-social-bridge.service
systemctl --user enable --now nora-social-bridge
curl -s http://127.0.0.1:19826/health    # {"ok": true}
```

See `docs/runbook.md` §10 for operating the whole social desktop (VNC, Chrome,
OpenCLI, this bridge).
