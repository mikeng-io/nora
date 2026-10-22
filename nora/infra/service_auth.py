"""Authentication for Nora's own internal service calls.

The transport bridges listen on the private `nora` Docker network, not on a
published port. Network membership is not caller authentication: any container
co-networked with a bridge can reach it, and a compromised sidecar must not be
able to read private events or send as the account. Each bridge therefore
requires the shared bearer token in `NORA_BRIDGE_TOKEN` on every route except
`/health`, and Nora sends it from the same environment variable.

An unset token returns no header. The bridge refuses to start without one, so
that is a deployment error that fails closed at the bridge, not a silent
unauthenticated mode.
"""

from __future__ import annotations

import os


def service_auth_headers() -> dict[str, str]:
    """`Authorization: Bearer <token>` for a call to one of Nora's bridges."""
    token = os.environ.get("NORA_BRIDGE_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}
