"""Exercise Nora's actual clients against the disposable loopback Ory stack."""

from __future__ import annotations

import argparse
import asyncio
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp

from nora.domain.managed import OutcomeStatus, Verdict
from nora.domain.management import Scope, ScopeKind
from nora.infra.ory.http import HttpStatus, request
from nora.infra.ory.keto import KetoClient, RelationTuple
from nora.infra.ory.kratos import KratosClient


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise AssertionError(detail)


async def check_clients(kratos_url: str, keto_read: str, keto_write: str) -> None:
    for url in (kratos_url, keto_read, keto_write):
        parsed = urlsplit(url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("client contracts require a disposable loopback Ory stack")
    external = "generated-contract-" + uuid4().hex
    scope = Scope(ScopeKind.ROOM, external)
    grant = RelationTuple("Room", scope.id, "grant_view", subject_id=external)
    restriction = RelationTuple("Room", scope.id, "deny_view", subject_id=external)
    async with aiohttp.ClientSession() as session:
        kratos = KratosClient(session, kratos_url)
        keto = KetoClient(session, keto_read, keto_write)
        created, identity = await kratos.create_identity(
            external_id=external,
            traits={"display_name": "Generated contract person"},
        )
        if created.status is not OutcomeStatus.COMPLETE or identity is None:
            raise AssertionError("create identity")
        try:
            by_id, found = await kratos.get_identity(identity.identity_id)
            require(
                by_id.status is OutcomeStatus.COMPLETE and found == identity,
                "read identity",
            )
            require(
                (await kratos.state_of(external)).verdict is Verdict.ALLOWED,
                "read identity state",
            )
            require(
                (await kratos.set_state(identity_id=identity.identity_id, active=False)).status
                is OutcomeStatus.COMPLETE,
                "set identity state",
            )
            require(
                (await kratos.state_of(external)).verdict is Verdict.DENIED,
                "read identity state",
            )
            require(
                (await kratos.set_state(identity_id=identity.identity_id, active=True)).status
                is OutcomeStatus.COMPLETE,
                "set identity state",
            )
            require(
                (await kratos.drop_sessions(identity_id=identity.identity_id)).status
                is OutcomeStatus.COMPLETE,
                "revoke sessions",
            )
            require(
                (await keto.check(scope=scope, relation="view", person_id=external)).verdict
                is Verdict.DENIED,
                "check deny",
            )
            require(
                (await keto.put_tuple(grant)).status is OutcomeStatus.COMPLETE,
                "write tuple",
            )
            require(
                (await keto.check(scope=scope, relation="view", person_id=external)).verdict
                is Verdict.ALLOWED,
                "check allow",
            )
            require(
                (await keto.put_tuple(restriction)).status is OutcomeStatus.COMPLETE,
                "write tuple",
            )
            require(
                (await keto.check(scope=scope, relation="view", person_id=external)).verdict
                is Verdict.DENIED,
                "check deny",
            )
            rows = await keto.tuples_on(namespace="Room", object_id=scope.id)
            require(
                rows == frozenset({grant, restriction}),
                "read relationships",
            )
        finally:
            for row in (restriction, grant):
                require(
                    (await keto.drop_tuple(row)).status is OutcomeStatus.COMPLETE,
                    "remove tuple",
                )
            deleted = await request(
                session,
                "DELETE",
                f"{kratos_url}/admin/identities/{identity.identity_id}",
                expected={204: HttpStatus.OK},
            )
            require(
                deleted.status is HttpStatus.OK,
                "identity cleanup",
            )
        absent, found = await kratos.get_by_external_id(external)
        require(
            absent.status is OutcomeStatus.DENIED and found is None,
            "read absence",
        )
        require(
            await keto.tuples_on(namespace="Room", object_id=scope.id) == frozenset(),
            "read absence",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kratos-url", required=True)
    parser.add_argument("--keto-read-url", required=True)
    parser.add_argument("--keto-write-url", required=True)
    args = parser.parse_args()
    asyncio.run(check_clients(args.kratos_url, args.keto_read_url, args.keto_write_url))
    print("Ory client contracts passed: identity lifecycle, strict policy and cleanup")
