"""The subject and recipient of a scheduled delivery."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class SubjectEdge:
    relation: str
    head: str
    asserted_at: datetime
    pointer_kind: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryIdentity:
    subject: tuple[str, ...]
    owed_to: str


@dataclass(frozen=True, slots=True)
class ResolvedDelivery:
    identity: DeliveryIdentity | None
    current_since: datetime


def resolve_delivery(
    row: Mapping[str, Any], edges: Iterable[SubjectEdge],
) -> ResolvedDelivery:
    subjects = {
        edge.head: edge.asserted_at for edge in edges
        if edge.relation == "serves" or (edge.relation == "about" and edge.pointer_kind is None)
    }
    if not subjects:
        return ResolvedDelivery(None, row["asserted_at"])
    return ResolvedDelivery(
        DeliveryIdentity(tuple(sorted(subjects)), row["owed_to"]), max(subjects.values()),
    )


class DuplicateDeliveryError(Exception):
    def __init__(self, intention: str) -> None:
        self.intention = intention
        super().__init__(intention)
