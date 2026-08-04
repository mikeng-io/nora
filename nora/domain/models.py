"""Platform-neutral data contracts (spec C.2). Every downstream module speaks these."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class ConversationTarget:
    """Where a reply goes. Hashable so it can key per-conversation locks and debounce."""

    platform: str
    conversation_id: str
    conversation_type: str


@dataclass(frozen=True)
class NormalizedMessage:
    platform: str                  # "signal"
    conversation_id: str           # base64 group id for Signal — platform-neutral key
    conversation_type: str         # "group" | "dm"
    sender_id: str                 # platform sender id (E.164 for Signal)
    sender_name: str | None        # platform-provided display name
    timestamp: int                 # ms epoch, platform-provided
    body: str                      # text + attachment placeholders
    raw: dict[str, Any]            # full envelope, persisted as raw_json

    def target(self) -> ConversationTarget:
        return ConversationTarget(self.platform, self.conversation_id, self.conversation_type)


@dataclass(frozen=True)
class PersonRef:
    person_id: str
    name: str
    honcho_peer_id: str            # == person_id, by construction (spec D.2)


class DeliveryStatus(Enum):
    """A transcript row's send state. Mirrors `nora.domain.errors.FailureClass`'s
    reasoning: this was stringly-typed over `"pending"`/`"sent"`/`"failed"`
    (the retired `nora/store/db.py::_VALID_STATUS`) in the same phase that typed
    the adjacent failure taxonomy as an enum. `nora/infra/sql/audit.py::SqlAuditStore`
    is the persistence phase's adapter onto this type: it stores `.value` and
    round-trips through `DeliveryStatus(...)`, which raises `ValueError` on an
    unrecognized string on the way back out.
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True)
class TranscriptRow:
    """One row as returned by `AuditStore.recent`. Replaces the untyped
    `dict[str, Any]` rows the retired `nora/store/db.py::recent` used to
    return; `nora.infra.sql.audit.SqlAuditStore` is what produces this shape
    at runtime now.
    """

    id: int
    sender_id: str
    sender_name: str | None
    ts: int
    body: str | None
    is_nora: bool
    delivery_status: DeliveryStatus
    raw_json: str | None


@dataclass(frozen=True)
class TagContext:
    """Everything the tagging path assembles before the model is constructed."""

    tag_id: str                    # uuid4 hex — the correlation id
    message: NormalizedMessage
    tagger: PersonRef | None       # None = unmapped sender
    transcript: tuple[TranscriptRow, ...]   # oldest-first rows from db.recent
    peer_card: tuple[str, ...]
    started_at: float              # perf_counter, for the latency histogram
