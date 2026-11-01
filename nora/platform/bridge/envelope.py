"""The v1 inbound envelope and canonical source identity.

One envelope per upstream transition (message/edit/delete/reaction/summons).
`source_key` is the canonical, un-hashed source tuple — private, it never
appears in telemetry labels or the API's public identifiers. `event_id` is the
opaque public identity, HMAC-derived under a versioned correlation key so a key
rotation changes aliases without changing the canonical uniqueness the
`bridge_event` table enforces.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any

ENVELOPE_VERSION = 1
KINDS = frozenset({"message", "edit", "delete", "reaction", "summons"})
_SEP = "\x1f"


def canonical_source_key(
    platform: str, account_key: str, conversation_id: str, kind: str, upstream: str
) -> str:
    """The key-version-independent source identity. `upstream` is the platform's
    own transition identity (message id + revision, receipt identity, ...)."""
    if kind not in KINDS:
        raise ValueError(f"unknown bridge kind {kind!r}")
    parts = (platform, account_key, conversation_id, kind, upstream)
    if any(_SEP in p or not p for p in parts):
        raise ValueError("source identity parts must be non-empty and separator-free")
    return _SEP.join(parts)


def canonical_digest(source_key: str) -> str:
    """The DB-level uniqueness digest — unkeyed on purpose: uniqueness must be
    independent of the correlation-key version."""
    return hashlib.sha256(source_key.encode()).hexdigest()


def event_id(source_key: str, *, key: bytes, key_version: int) -> str:
    """`v{key_version}:{hmac}` — an opaque, unguessable alias of the source."""
    digest = hmac.new(key, source_key.encode(), hashlib.sha256).hexdigest()
    return f"v{key_version}:{digest}"


@dataclass(frozen=True)
class Envelope:
    """The complete stream field payload (design 02-wire §envelope)."""

    platform: str
    account_key: str
    kind: str
    source_key: str  # canonical tuple, private
    event_id: str
    conversation_id: str
    conversation_type: str
    sender_id: str
    sender_name: str | None
    sent_ms: int | None  # upstream author time; None = bridge never saw it
    received_ms: int  # bridge-local receive time
    queued_ms: int  # enqueue time (== received_ms unless journal replay)
    time_quality: str  # "upstream" | "bridge_local"
    message: dict[str, Any]  # serialized NormalizedMessage fields
    attachments: tuple[dict[str, Any], ...] = ()
    trace: dict[str, str] = field(default_factory=dict)  # traceparent/tracestate only
    receipt: dict[str, str] | None = None  # optional enqueue receipt {emoji}
    # The admission-projection revision the bridge applied to this event; the
    # app re-checks CURRENT policy before acting — this is provenance, not authority.
    admission_rev: int | None = None

    def to_fields(self) -> dict[str, Any]:
        return {
            "v": ENVELOPE_VERSION,
            "platform": self.platform,
            "account_key": self.account_key,
            "kind": self.kind,
            "source_key": self.source_key,
            "event_id": self.event_id,
            "conversation_id": self.conversation_id,
            "conversation_type": self.conversation_type,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "sent_ms": self.sent_ms,
            "received_ms": self.received_ms,
            "queued_ms": self.queued_ms,
            "time_quality": self.time_quality,
            "message": self.message,
            "attachments": list(self.attachments),
            "trace": self.trace,
            "receipt": self.receipt,
            "admission_rev": self.admission_rev,
        }

    @classmethod
    def from_fields(cls, fields: dict[str, Any]) -> Envelope:
        if fields.get("v") != ENVELOPE_VERSION:
            raise ValueError(f"unsupported envelope version {fields.get('v')!r}")
        trace = fields.get("trace") or {}
        trace = {k: v for k, v in trace.items() if k in ("traceparent", "tracestate")}
        return cls(
            platform=str(fields["platform"]),
            account_key=str(fields["account_key"]),
            kind=str(fields["kind"]),
            source_key=str(fields["source_key"]),
            event_id=str(fields["event_id"]),
            conversation_id=str(fields["conversation_id"]),
            conversation_type=str(fields["conversation_type"]),
            sender_id=str(fields["sender_id"]),
            sender_name=fields.get("sender_name"),
            sent_ms=fields.get("sent_ms"),
            received_ms=int(fields["received_ms"]),
            queued_ms=int(fields["queued_ms"]),
            time_quality=str(fields.get("time_quality") or "bridge_local"),
            message=dict(fields["message"]),
            attachments=tuple(dict(a) for a in fields.get("attachments") or ()),
            trace=trace,
            receipt=fields.get("receipt"),
            admission_rev=fields.get("admission_rev"),
        )


def validate_traceparent(value: str) -> bool:
    """W3C `traceparent` — version, 32-hex trace id, 16-hex span id, flags."""
    parts = value.split("-")
    return (
        len(parts) == 4
        and len(parts[1]) == 32
        and len(parts[2]) == 16
        and all(c in "0123456789abcdef" for c in parts[1] + parts[2])
        and parts[0] != "ff"
    )
