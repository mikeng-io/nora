"""Authentication for Nora's own internal service calls.

The transport bridges listen on the private `nora` Docker network, not on a
published port. Network membership is not caller authentication: any container
co-networked with a bridge can reach it, and a compromised sidecar must not be
able to read private events or send as the account. Each bridge therefore
requires the shared bearer token in `NORA_BRIDGE_TOKEN` on every route that
carries account data, and Nora sends it from the same environment variable.
WhatsApp leaves only its counters-only `/health` open; Instagram protects
`/health` too because that body names the account.

An unset token returns no header. The bridge refuses to start without one, so
that is a deployment error that fails closed at the bridge, not a silent
unauthenticated mode.

The voice path uses the same bearer in both directions: nora calls the audio
controller with `service_auth_headers()`, and the wake gate calls nora's
`/app/v1/voice/utterance`, which checks it with `service_auth_ok`.
"""

from __future__ import annotations

import hmac
import os


def service_auth_headers() -> dict[str, str]:
    """`Authorization: Bearer <token>` for a call to one of Nora's bridges."""
    token = os.environ.get("NORA_BRIDGE_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def service_auth_ok(authorization: str | None) -> bool:
    """True only for the exact shared bearer. An unset token admits nothing,
    so a deployment that forgot it refuses rather than serving open."""
    token = os.environ.get("NORA_BRIDGE_TOKEN", "")
    if not token or not authorization:
        return False
    # Byte compare: compare_digest is constant-time and returns False, rather
    # than raising, on a length mismatch.
    return hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode())
