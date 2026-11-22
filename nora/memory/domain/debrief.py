"""The durable identity of a finished pass and its attempted commitments."""

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from nora.memory.domain.admission_proposals import Occasion

MAX_SLOT_ATTEMPTS = 3


class ProposalId(TypedDict):
    id: str
    witness: str | None


@dataclass(frozen=True, slots=True)
class DebriefObligation:
    unit: str
    room: str
    pass_id: str
    occasion: Occasion
    intention: str | None
    created: datetime
    first_message_id: int | None = None
    last_message_id: int | None = None
    proposal_ids: tuple[ProposalId, ...] | None = None
