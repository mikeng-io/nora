"""`python -m nora.infra.social` — run the host-side social bridge (DEC-151).

Binds `127.0.0.1` ONLY: Nora reaches it over host networking from her
container, and loopback is the whole intended audience — there is no
authentication on `/read`, so the read-only allowlist (enforced in
`bridge_server`) is the boundary and exposure beyond loopback would extend it
to anyone who can reach the port.

This process reads NO `nora.config` — it is not a composition root. Env vars
only: `NORA_SOCIAL_BRIDGE_PORT` (default 19826) and `OPENCLI_BIN` (default
`opencli`, resolved on PATH).
"""

from __future__ import annotations

import logging
import os

from aiohttp import web

from .bridge_server import SocialBridge


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("NORA_SOCIAL_BRIDGE_PORT", "19826"))
    bridge = SocialBridge(opencli_bin=os.environ.get("OPENCLI_BIN", "opencli"))
    web.run_app(bridge.app(), host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
