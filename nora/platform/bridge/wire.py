"""The shared authenticated bridge API (design 02-wire §actions).

One vocabulary for POST /v1/actions, GET /v1/actions/{id}, GET /v1/self,
GET /v1/health, GET /v1/reads, POST /v1/reads, and attachment custody
GET/PUT /v1/attachments/{key}. Auth: Bearer token on every route.

Every outcome is typed and honest: `confirmed` means the platform API returned
success; `rejected` a definitive platform refusal; `unsupported` the op doesn't
exist on this platform; `uncertain` the upstream call was made (or may have
been) and its result could not be determined — never resolved to success.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from nora.domain.actions import OWNER_KINDS, ActionOwner

# `ActionOwner`/`OWNER_KINDS` are re-exported: the type is domain vocabulary
# (nora.domain.actions) so layers below platform may share it; existing
# `wire.ActionOwner` references keep working.
__all__ = ["OWNER_KINDS", "ActionOwner", "ActionRequest", "ActionResponse",
           "ActionState", "Outcome", "attachment_key_ok"]

#: The `{opaque_key}` grammar both custody verbs enforce — the SAME charset
#: the whatsapp bridge's `KEY_RE` and the instagram bridge's `custodyKeyRE`
#: implement natively. Inbound keys are sha256 hex; app-minted outbound keys
#: embed the action binding (`out-<action_id>-<i>`: colons and hyphens), so
#: an alnum-only rule refuses every key the client can mint. `/` is excluded
#: by the charset and `..` by the check below, so no key can name a path
#: outside the custody dir.
ATTACHMENT_KEY_RE = re.compile(r"[A-Za-z0-9._~:-]{1,128}\Z")


def attachment_key_ok(key: str) -> bool:
    return bool(key) and bool(ATTACHMENT_KEY_RE.fullmatch(key)) \
        and ".." not in key

Outcome = Literal["confirmed", "rejected", "unsupported", "uncertain"]
ActionState = Literal["prepared", "started", "confirmed", "rejected", "uncertain"]


@dataclass(frozen=True)
class ActionRequest:
    action_id: str
    owner: ActionOwner
    op: str  # "send" | "react" | "edit" | "typing"
    request: dict[str, Any]  # op-specific, hashed for reservation equality

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActionRequest:
        return cls(action_id=str(d["action_id"]),
                   owner=ActionOwner.from_dict(d["owner"]),
                   op=str(d["op"]), request=dict(d.get("request") or {}))


@dataclass(frozen=True)
class ActionResponse:
    action_id: str
    state: ActionState
    outcome: Outcome | None
    platform_ref: str | None = None
    platform_refs: tuple[str, ...] = ()
    # The pending outbound audit row the action bound at start — the
    # reconciliation handle a client uses to resolve ITS messages row when
    # the POST answer was lost; null on receipts and pre-binding rows.
    message_id: int | None = None
    error: dict[str, Any] | None = None  # classified, sanitized — never raw text

    def to_dict(self) -> dict[str, Any]:
        return {"action_id": self.action_id, "state": self.state,
                "outcome": self.outcome, "platform_ref": self.platform_ref,
                "platform_refs": list(self.platform_refs) or None,
                "message_id": self.message_id, "error": self.error}
