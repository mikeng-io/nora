"""`AdmissionProbe` over the managed PostgreSQL tables —
`nora/infra/sql/managed_handoff.py` holds the queries; this adapter is the
`app/managed_admission.py`-facing seam, the same way `KratosIdentityRegistry`
adapts the infra Kratos client to its port."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.managed import ManagedPerson, Outcome
from nora.infra.sql import managed_handoff
from nora.people.app.managed_admission import AdmissionFacts


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
