"""Identity of a due occurrence and its proactive claim."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

RECHECK_UNAVAILABLE = "recheck_unavailable"


@dataclass(frozen=True, slots=True)
class DueIntention:
    unit: str
    intention: str
    occurrence: str
    eligible_at: datetime
    origin_room: str | None
    owed_to: str


@dataclass(frozen=True, slots=True)
class ProactiveClaim:
    due: DueIntention
    claimed: datetime

    @property
    def pass_id(self) -> str:
        key = (self.due.unit, "intention_due", self.due.intention, self.due.occurrence,
               "proactive", self.claimed.astimezone(UTC).isoformat())
        return f"proactive:{hashlib.sha256(json.dumps(key).encode()).hexdigest()}"
