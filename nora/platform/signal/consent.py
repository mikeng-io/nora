"""Private link decisions from signal-cli transport envelopes, never room text.

The link service still verifies the nonce, current request, exact account,
revisions and expiry. Parsing a decision grants no authority by itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nora.domain.identity import Identifier

from .parser import extract_envelope

_DECISION = re.compile(r"!!!link (approve|refuse) (\S+) (\S+)", re.ASCII)


@dataclass(frozen=True)
class SignalLinkDecision:
    sender: frozenset[Identifier]
    timestamp: int
    request_id: str
    nonce: str
    approve: bool


def parse_link_decision(
    event: dict, *, own_account: str,
) -> SignalLinkDecision | None:
    """Read only fresh private data messages on the trusted daemon connection.

    The caller must supply the configured service account, not a client claim.
    Sender aliases are retained together for binding-store conflict checking;
    neither names nor body content can supply a sender identifier.
    """
    if not own_account or event.get("account", own_account) != own_account:
        return None
    try:
        env = extract_envelope(event)
    except (AttributeError, TypeError):
        return None
    if not isinstance(env, dict) or any(
        key in env for key in ("syncMessage", "editMessage")
    ):
        return None
    data = env.get("dataMessage")
    if not isinstance(data, dict) or any(
        key in data for key in (
            "groupInfo", "quote", "reaction", "remoteDelete", "editMessage",
        )
    ):
        return None
    body = data.get("message")
    if not isinstance(body, str) or (match := _DECISION.fullmatch(body)) is None:
        return None
    timestamp = env.get("timestamp")
    if type(timestamp) is not int or timestamp <= 0:
        return None
    identifiers = frozenset(
        Identifier("signal", kind, value)
        for field, kind in (
            ("sourceNumber", "phone"), ("sourceUuid", "uuid"),
        )
        if isinstance(value := env.get(field), str) and value.strip()
    )
    if not identifiers or any(i.value == own_account for i in identifiers):
        return None
    return SignalLinkDecision(
        sender=identifiers, timestamp=timestamp,
        request_id=match[2], nonce=match[3], approve=match[1] == "approve",
    )
