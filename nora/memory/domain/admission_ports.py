"""The admission use case's storage contract."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from nora.memory.domain.admission import Admitted, Refusal
from nora.memory.domain.admission_proposals import PassContext, RowFacts


class AdmissionUnavailableError(Exception):
    def __init__(self, *, commit_uncertain: bool) -> None:
        self.commit_uncertain = commit_uncertain


@dataclass(frozen=True, slots=True)
class Stored:
    id: str
    base: str | None = None


class AdmissionStore(Protocol):
    async def read(self, unit: str, ids: frozenset[str]) -> Mapping[str, RowFacts]: ...

    async def close(
        self, intention: str, witness: str, context: PassContext,
    ) -> Stored | Refusal: ...

    async def persist_admitted_proposal(
        self, decision: Admitted, context: PassContext, stored: Stored,
    ) -> None: ...
