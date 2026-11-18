"""The admission use case's storage contract."""

from collections.abc import Mapping
from typing import Protocol

from nora.memory.domain.admission import Admitted
from nora.memory.domain.admission_proposals import PassContext, RowFacts


class AdmissionUnavailableError(Exception):
    def __init__(self, *, commit_uncertain: bool) -> None:
        self.commit_uncertain = commit_uncertain


class AdmissionStore(Protocol):
    async def read(self, unit: str, ids: frozenset[str]) -> Mapping[str, RowFacts]: ...

    async def persist_admitted_proposal(
        self, decision: Admitted, context: PassContext,
    ) -> None: ...
