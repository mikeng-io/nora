"""The bridge's `summons` SSE event → `SummonsCard`, parsed once

A comment that tags her reaches her as a DM card from the commenter; the
bridge refetches the card's thread to find the post it points at and emits
this event IN ADDITION to the ordinary message event:

    {"kind": "summons", "message_id": "mid.$…", "room_id": "<short thread id>",
     "sender": {"fbid": "…", "igid": "…", "username": "…"},
     "text": "<the comment text>", "shortcode": "<may be empty>",
     "media_id": "<numeric pk, may be empty>", "received_at_ms": <int>,
     "resolved": <bool>}

**Fail closed, field by field.** A card with no usable `message_id`, no
`received_at_ms`, or a `resolved` that is not a JSON boolean is `None`: no
row, no answer, counted `malformed`. A `resolved: true` card whose shortcode
or media id is not one is read as UNRESOLVED (recorded, never answered)
rather than trusted. A sender igid that is not decimal becomes "", which
matches nobody at the summoner gate. Nothing here logs a field's value.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from ...domain.instagram_links import SHORTCODE_RE, is_handle
from ...domain.summons import SummonsCard
from .ids import conversation_id_for

#: The one event kind this module reads.
SUMMONS_KIND = "summons"

#: A DM message id as the bridge sends it (`mid.$cAAA…`): printable, no
#: whitespace, bounded. It is the ledger's key, so a malformed one is refused
#: rather than stored.
_MESSAGE_ID_RE = re.compile(r"[A-Za-z0-9._$:+/=-]{1,256}")
_DECIMAL_RE = re.compile(r"[0-9]{1,25}")
_MAX_TEXT = 2200

#: Start of 2020 in ms: a `received_at_ms` before it is not a time this bridge
#: could have stamped (a seconds value, a zero, a sentinel), and a card with
#: no believable time is refused rather than read as "now".
_MIN_RECEIVED_MS = 1_577_836_800_000


def _decimal(value: Any) -> str:
    return value if isinstance(value, str) and _DECIMAL_RE.fullmatch(value) else ""


def parse_summons(event: Any) -> SummonsCard | None:
    """One `kind: "summons"` event → a card, or `None` if it cannot be one."""
    if not isinstance(event, dict) or event.get("kind") != SUMMONS_KIND:
        return None
    message_id = event.get("message_id")
    if not isinstance(message_id, str) or not _MESSAGE_ID_RE.fullmatch(message_id):
        return None
    resolved = event.get("resolved")
    if not isinstance(resolved, bool):
        return None
    received_ms = event.get("received_at_ms")
    if (
        isinstance(received_ms, bool) or not isinstance(received_ms, int)
        or received_ms < _MIN_RECEIVED_MS
    ):
        return None
    try:
        received_at = datetime.fromtimestamp(received_ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
    sender = event.get("sender")
    sender = sender if isinstance(sender, dict) else {}
    username = sender.get("username")
    text = event.get("text")
    shortcode = event.get("shortcode")
    shortcode = shortcode if isinstance(shortcode, str) and SHORTCODE_RE.fullmatch(
        shortcode,
    ) else ""
    media_id = _decimal(event.get("media_id"))
    # Resolved only when the bridge said so AND named a post this side can
    # address: a "resolved" card with no usable post is recorded, not answered.
    resolved = resolved and bool(shortcode) and bool(media_id)
    return SummonsCard(
        message_id=message_id,
        room_id=conversation_id_for(event.get("room_id")),
        sender_igid=_decimal(sender.get("igid")),
        sender_username=username if isinstance(username, str) and is_handle(username) else "",
        text=text[:_MAX_TEXT] if isinstance(text, str) else "",
        shortcode=shortcode if resolved else "",
        media_id=media_id if resolved else "",
        received_at=received_at,
        resolved=resolved,
    )
