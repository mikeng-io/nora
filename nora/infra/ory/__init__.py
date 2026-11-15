"""Minimal typed HTTP clients for the pinned self-hosted Ory services
(`design/managed-identity-access/identity.md`, `authorization.md`).

Public surface: `KetoClient`, `KratosClient`, `OryTransportError` and the
`HttpStatus`/`HttpResult` transport vocabulary. No generated SDK: the clients
name exactly the endpoints the managed backend uses and map every transport
shape — 2xx, absent, denied verdict, conflict, redirect, malformed body,
timeout, outage — onto typed results the services branch on.
"""

from nora.infra.ory.http import HttpResult, HttpStatus, OryTransportError, request
from nora.infra.ory.keto import KetoClient, RelationTuple
from nora.infra.ory.kratos import KratosClient, KratosIdentity

__all__ = [
    "HttpResult",
    "HttpStatus",
    "KetoClient",
    "KratosClient",
    "KratosIdentity",
    "OryTransportError",
    "RelationTuple",
    "request",
]
