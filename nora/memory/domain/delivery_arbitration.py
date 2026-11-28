"""The recorded evidence required to arbitrate a scheduled delivery."""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class DeliveryEvidence:
    current: bool
    origin_room: str | None
    prior: Literal["already_delivered", "uncertain_prior"] | None = None


class DeliveryHistory(Protocol):
    async def read(
        self, unit: str, intention: str, destination: str, *, occurrence: str | None = None,
    ) -> DeliveryEvidence | None: ...
