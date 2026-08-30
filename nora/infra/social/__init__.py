"""Social reads behind the `social_fetch` tool (DEC-151) — X and Instagram,
READ-ONLY, via OpenCLI on the host.

Two halves with a loopback HTTP seam between them:

* `bridge_client.py::OpenCliBridgeClient` — the `SocialProvider` the container
  composes behind `[social] enabled`; POSTs `{platform, what, target, limit}`
  to the bridge and never raises.
* `bridge_server.py::SocialBridge` — the host-side half, run as
  `python -m nora.infra.social` (a systemd user service,
  `deploy/social-bridge/`). It owns the read-only `(site, command)` allowlist
  — the security boundary — and spawns `opencli … -f json` as an argv vector,
  never a shell.

The allowlist lives host-side so a write verb stays unreachable even if the
model-facing tool is prompt-injected by the content it reads."""

from .bridge_client import OpenCliBridgeClient

__all__ = ["OpenCliBridgeClient"]
