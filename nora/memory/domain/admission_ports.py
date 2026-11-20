"""The admission use case's storage contract."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from nora.domain.doctrine_check import CheckVerdict, LawRule, Tension
from nora.memory.domain.admission import Admitted, Refusal
from nora.memory.domain.admission_proposals import PassContext, RowFacts
from nora.memory.domain.lesson_admission import LessonInventory


class AdmissionUnavailableError(Exception):
    def __init__(self, *, commit_uncertain: bool) -> None:
        self.commit_uncertain = commit_uncertain


@dataclass(frozen=True, slots=True)
class Stored:
    id: str
    base: str | None = None
    other_deliveries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Unavailable:
    id: str
    commit_uncertain: bool


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    outcomes: tuple[Stored | Refusal | Unavailable, ...]
    revisions: tuple[Stored, ...]
    retirements: tuple[Stored, ...]


class RoomSlots(Protocol):
    def resolve(self, room: str, unit: str) -> str | None: ...
    def held(self, room: str) -> bool: ...


class LessonChecker(Protocol):
    @property
    def model(self) -> str: ...
    def rules(self) -> tuple[LawRule, ...]: ...
    async def judge(self, lesson: str) -> CheckVerdict: ...


class TensionWriter(Protocol):
    async def record(self, tension: Tension) -> str: ...


class AdmissionStore(Protocol):
    async def lessons(self, unit: str) -> LessonInventory: ...

    async def read(self, unit: str, ids: frozenset[str]) -> Mapping[str, RowFacts]: ...

    async def close(
        self, intention: str, witness: str, context: PassContext,
    ) -> Stored | Refusal: ...

    async def persist_admitted_proposal(
        self, decision: Admitted, context: PassContext, stored: Stored,
    ) -> None: ...
