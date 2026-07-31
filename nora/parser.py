"""Parse a signal-cli SSE envelope into a message row (DESIGN.md §5.1)."""

import json
from dataclasses import dataclass


@dataclass
class Message:
    group_id: str
    sender_id: str
    sender_name: str
    ts: int
    body: str
    raw_json: str


def _attachment_placeholder(att: dict) -> str:
    if str(att.get("contentType", "")).startswith("image/"):
        return "[image]"
    return f"[attachment: {att.get('filename') or att.get('id') or 'file'}]"


def parse_event(event: dict, own_account: str) -> Message | None:
    """Return a Message for group text/attachment messages, else None.

    Drops: non-receive events, sync messages, DMs, own messages, and
    reaction/sticker-only payloads (no usable body).
    """
    if event.get("method") != "receive":
        return None
    env = (event.get("params") or {}).get("envelope") or {}
    data = env.get("dataMessage")
    if not data:  # syncMessage / typing / receipt — ignored in v1
        return None

    group = data.get("groupInfo") or {}
    group_id = group.get("groupId")
    if not group_id:  # DM — out of scope for v1
        return None

    source = env.get("source") or env.get("sourceNumber") or ""
    if source == own_account:  # don't double-append Nora's own sends
        return None

    parts = []
    text = (data.get("message") or "").strip()
    if text:
        parts.append(text)
    parts += [_attachment_placeholder(a) for a in data.get("attachments") or []]
    if not parts:
        return None

    return Message(
        group_id=group_id,
        sender_id=source,
        sender_name=env.get("sourceName") or source,
        ts=int(env.get("timestamp") or data.get("timestamp") or 0),
        body=" ".join(parts),
        raw_json=json.dumps(event),
    )
