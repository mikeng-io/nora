"""Bearer auth for the internal listener (§5a) — one check, shared by both
write routes. A network-level control (no `ports:` entry, no Tunnel ingress
rule, §5a) is the PRIMARY control here; this is defence in depth, the same
shape `NORA_SHORTENER_TOKEN` already is for `n.example.net`'s Worker.
"""

from __future__ import annotations

from starlette.requests import Request


def bearer_ok(request: Request, *, token: str) -> bool:
    return request.headers.get("Authorization") == f"Bearer {token}"
