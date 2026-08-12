"""Bearer auth for the internal listener (§5a) — two shapes now, not one.

The MASTER token (`NORA_DROP_TOKEN`) is Nora's own backend's credential —
only it may call `POST /api/tokens` (Task 2 of the Nora Compute Phase 2 plan)
and it remains a valid credential on the artifact write routes too, unchanged
from the Drop spec. A SCOPED job token (`mint_job_token`/`verify_job_token`)
is what `LocalDockerBackend` mints per job and hands to Smith's container —
narrower (create-only, or update-only-this-artifact_id) and short-lived (its
own `exp` claim, not an active revoke call — see the Phase 2 plan's own
reasoning for why TTL alone is judged sufficient here).

A network-level control (no `ports:` entry, no Tunnel ingress rule, §5a) is
still the PRIMARY control on this listener; both token shapes are defence in
depth on top of that, the same relationship the Drop spec already describes
for the single master token.
"""

from __future__ import annotations

import time
from enum import StrEnum

import jwt
from starlette.requests import Request


def bearer_ok(request: Request, *, token: str) -> bool:
    return request.headers.get("Authorization") == f"Bearer {token}"


class TokenScope(StrEnum):
    CREATE = "create"
    UPDATE = "update"


def mint_job_token(
    *, secret: str, scope: TokenScope, artifact_id: str | None, ttl_seconds: int,
) -> str:
    payload: dict[str, object] = {
        "scope": scope.value,
        "exp": int(time.time()) + ttl_seconds,
    }
    if artifact_id is not None:
        payload["artifact_id"] = artifact_id
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_job_token(
    *, token: str, secret: str, required_scope: TokenScope, artifact_id: str | None = None,
) -> bool:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return False
    if payload.get("scope") != required_scope.value:
        return False
    return not (required_scope is TokenScope.UPDATE and payload.get("artifact_id") != artifact_id)


def write_authorized(
    request: Request,
    *,
    master_token: str,
    job_secret: str,
    required_scope: TokenScope,
    artifact_id: str | None = None,
) -> bool:
    """A write route is authorized by EITHER the master token OR a scoped job
    token matching `required_scope` (and `artifact_id`, for updates)."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False
    presented = header[len("Bearer "):]
    if presented == master_token:
        return True
    return verify_job_token(
        token=presented, secret=job_secret, required_scope=required_scope,
        artifact_id=artifact_id,
    )
