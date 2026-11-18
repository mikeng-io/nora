"""An active account resolution keeps absence, refusal and failure distinct."""

from dataclasses import dataclass
from enum import Enum


class ResolutionStatus(Enum):
    OK = "ok"
    ABSENT = "nothing_found"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class IdentityResolution:
    status: ResolutionStatus
    person_id: str | None = None

    def __post_init__(self) -> None:
        if self.status is ResolutionStatus.OK:
            if not isinstance(self.person_id, str) or not self.person_id.strip():
                raise ValueError("a resolved identity names a canonical person")
        elif self.person_id is not None:
            raise ValueError("an unresolved identity cannot disclose a person")
