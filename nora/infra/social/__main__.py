"""`python -m nora.infra.social` — run the host-side social bridge (DEC-151).

Binds `127.0.0.1` by default: loopback is the whole intended audience — there
is no authentication on `/read`, so the read-only allowlist (enforced in
`bridge_server`) is the boundary and exposure beyond loopback would extend it
to anyone who can reach the port. The deployed unit overrides this with
`NORA_SOCIAL_BRIDGE_HOST=127.0.0.1,172.17.0.1`: Nora's container is NOT on
host networking (it is on the private compose `nora` network), so the bridge
also binds the docker0 gateway (`172.17.0.1`, what `host.docker.internal`
resolves to inside the container) to be reachable at
`http://host.docker.internal:19826` — still never on a LAN interface.

This process reads NO `nora.config` — it is not a composition root. Env vars
only: `NORA_SOCIAL_BRIDGE_PORT` (default 19826), `NORA_SOCIAL_BRIDGE_HOST`
(comma-separated bind addresses, default `127.0.0.1`) and `OPENCLI_BIN`
(default `opencli`, resolved on PATH).
"""

from __future__ import annotations

import logging
import os

from aiohttp import web

from .bridge_server import SocialBridge


def parse_bind_hosts(raw: str) -> list[str]:
    """Split a comma-separated bind-host value; empty → the loopback default."""
    hosts = [h.strip() for h in raw.split(",") if h.strip()]
    return hosts or ["127.0.0.1"]


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("NORA_SOCIAL_BRIDGE_PORT", "19826"))
    hosts = parse_bind_hosts(os.environ.get("NORA_SOCIAL_BRIDGE_HOST", "127.0.0.1"))
    bridge = SocialBridge(opencli_bin=os.environ.get("OPENCLI_BIN", "opencli"))
    web.run_app(bridge.app(), host=hosts, port=port, print=None)


if __name__ == "__main__":
    main()
