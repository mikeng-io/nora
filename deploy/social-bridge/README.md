# deploy/social-bridge — the host side of `social_fetch`

The `social_fetch` tool (DEC-151) reads X and Instagram through OpenCLI, which
exists only on the host (daemon on `localhost:19825`, driving the
authenticated Chrome from `nora-social-chrome.service` via the Browser Bridge
extension). Nora runs in a Docker container with host networking, so this
bridge is the seam: her tool POSTs `{platform, what, target, limit}` to
`http://127.0.0.1:19826/read`, and this service runs
`opencli <site> <command> <target> -f json` as a subprocess and returns the
JSON.

**The bridge is the read-only security boundary.** It re-enforces its own
frozen `(site, command)` allowlist host-side (`READ_ONLY_ALLOWLIST` in
`nora/infra/social/bridge_server.py`) and refuses everything else before any
subprocess is spawned — OpenCLI's write commands (`twitter post/like/follow/
delete`, `instagram post/like/follow/comment`, …) are unreachable through it,
by construction, even if the model-facing tool is prompt-injected by the
content it reads. There is no free-form command passthrough.

**It binds `127.0.0.1` only.** There is no authentication on `/read`; the
allowlist is the boundary, and loopback binding is what keeps that boundary
from extending to anyone who can reach the port.

**It runs the CHECKOUT's code** — `uv run --project /home/norty/workspace/nora
python -m nora.infra.social` — so it tracks `master` the same way the running
container's bind mounts do: an edit landing on `master` is a deploy here after
a restart. It reads no `config.toml`; env vars only (`OPENCLI_BIN`,
`NORA_SOCIAL_BRIDGE_PORT`).

Install (systemd **user** service, like the desktop/Chrome units):

```bash
systemctl --user link /home/norty/workspace/nora/deploy/social-bridge/nora-social-bridge.service
systemctl --user enable --now nora-social-bridge
curl -s http://127.0.0.1:19826/health    # {"ok": true}
```

See `docs/runbook.md` §10 for operating the whole social desktop (VNC, Chrome,
OpenCLI, this bridge).
