"""The `managed_admission` concrete seams — `SqlAdmissionProbe` over the
managed PostgreSQL tables (`nora/infra/sql/managed_handoff.py` holds the
queries) and `KratosAdmission` over the pinned Kratos admin client, the
same way `KratosIdentityRegistry` adapts that client to its port."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.managed import ManagedPerson, Outcome
from nora.infra.ory import KratosClient, KratosIdentity
from nora.infra.sql import managed_handoff
from nora.people.app.managed_admission import AdmissionFacts
from nora.people.app.managed_admission_identity import AdmissionIdentity


class SqlAdmissionProbe:
    """Probe the concrete installation target — rows the port reads hide."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def facts(self) -> AdmissionFacts:
        rows = await managed_handoff.admission_facts(self._engine)
        return AdmissionFacts(
            persons=rows["persons"],
            accounts=rows["accounts"],
            identifiers=rows["identifiers"],
            aliases=rows["aliases"],
            memberships=rows["memberships"],
            supports=rows["supports"],
            operations=rows["operations"],
            holds=rows["holds"],
            blocks=rows["blocks"],
            link_rows=rows["link_rows"],
        )

    async def insert_person(self, person: ManagedPerson) -> Outcome:
        return await managed_handoff.insert_person(self._engine, person)


def _view(identity: KratosIdentity | None) -> AdmissionIdentity | None:
    if identity is None:
        return None
    return AdmissionIdentity(
        identity_id=identity.identity_id,
        external_id=identity.external_id,
        state=identity.state,
        traits=identity.traits,
        metadata_admin=identity.metadata_admin,
    )


class KratosAdmission:
    """`AdmissionKratos` over the pinned Kratos admin client — create
    carrying the import's durable admin-metadata marker, read the concrete
    record back by `external_id`. Ownership judgement lives with the
    caller; this seam only moves typed values."""

    def __init__(self, client: KratosClient) -> None:
        self._client = client

    async def create(
        self, *, external_id: str, traits: dict[str, Any], owner: dict[str, Any]
    ) -> tuple[Outcome, AdmissionIdentity | None]:
        outcome, identity = await self._client.create_identity(
            external_id=external_id, traits=traits, metadata_admin=owner)
        return outcome, _view(identity)

    async def read(
        self, *, external_id: str
    ) -> tuple[Outcome, AdmissionIdentity | None]:
        outcome, identity = await self._client.get_by_external_id(external_id)
        return outcome, _view(identity)
