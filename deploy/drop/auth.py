"""Bearer auth for the internal listener (§5a).

One credential: the MASTER token (`NORA_DROP_TOKEN`), Nora's own backend's.
It authorizes every write route (`POST /api/links`, `POST /api/artifacts`,
`PATCH /api/artifacts/{id}`). The scoped per-job tokens and the route that
minted them existed only for the per-job sandbox containers retired in S3b
(design/sandbox-runtime decision 4): nothing mints or accepts a second
credential shape any more.

A network-level control (no `ports:` entry, no Tunnel ingress rule, §5a) is
still the PRIMARY control on this listener; the bearer token is defence in
depth on top of that.
"""

from __future__ import annotations

from starlette.requests import Request


def bearer_ok(request: Request, *, token: str) -> bool:
    return request.headers.get("Authorization") == f"Bearer {token}"
