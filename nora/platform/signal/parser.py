"""signal-cli SSE envelope → NormalizedMessage (spec C.3, design/01).

Group messages only in v2; DMs and syncMessages are dropped. Attachments become
placeholders — no download (v2.1).
"""

from __future__ import annotations

import logging

from ...models import NormalizedMessage

log = logging.getLogger("nora.signal.parser")


def _attachment_placeholder(att: dict) -> str:
    if str(att.get("contentType", "")).startswith("image/"):
        return "[image]"
    return f"[attachment: {att.get('filename') or att.get('id') or 'file'}]"


def parse_event(event: dict, own_account: str) -> NormalizedMessage | None:
    try:
        if (event or {}).get("method") != "receive":
            return None
        env = ((event.get("params") or {}).get("envelope")) or {}
        data = env.get("dataMessage")
        if not isinstance(data, dict):
            return None                       # syncMessage / typing / receipt / malformed

        group_id = (data.get("groupInfo") or {}).get("groupId")
        if not group_id:
            return None                       # DM — out of scope in v2

        source = env.get("source") or env.get("sourceNumber") or ""
        if own_account and source == own_account:
            return None                       # R-12: Nora never ingests her own sends

        parts = []
        text = (data.get("message") or "").strip()
        if text:
            parts.append(text)
        parts += [_attachment_placeholder(a) for a in (data.get("attachments") or [])]
        if not parts:
            return None                       # reaction / sticker / empty — no usable body

        return NormalizedMessage(
            platform="signal",
            conversation_id=group_id,
            conversation_type="group",
            sender_id=source,
            sender_name=env.get("sourceName") or None,
            timestamp=int(env.get("timestamp") or data.get("timestamp") or 0),
            body=" ".join(parts),
            raw=event,
        )
    except Exception:                          # noqa: BLE001 — one bad event never kills the stream
        log.exception("unparseable envelope, skipping")
        return None
