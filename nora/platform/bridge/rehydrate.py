"""`NormalizedMessage` <-> the envelope's `message` dict.

The bridge parses upstream JSON ONCE (the comprehend-once rule at the boundary)
and the app rehydrates the typed value — it never re-interprets `raw`. `raw`
rides along opaquely for `raw_json` persistence only.
"""

from __future__ import annotations

from typing import Any

from nora.domain.media import MediaRef
from nora.domain.models import EditTarget, NormalizedMessage, Quote, Touch


def _target_to_dict(t: EditTarget | None) -> dict[str, Any] | None:
    if t is None:
        return None
    return {"message_id": t.message_id, "sent_ts": t.sent_ts,
            "mentions_known": t.mentions_known}


def _target_from_dict(d: dict[str, Any] | None) -> EditTarget | None:
    if not d:
        return None
    return EditTarget(message_id=d.get("message_id", ""),
                      sent_ts=d.get("sent_ts"),
                      mentions_known=bool(d.get("mentions_known", True)))


def message_to_dict(m: NormalizedMessage) -> dict[str, Any]:
    return {
        "platform": m.platform, "conversation_id": m.conversation_id,
        "conversation_type": m.conversation_type, "sender_id": m.sender_id,
        "sender_name": m.sender_name, "timestamp": m.timestamp, "body": m.body,
        "raw": m.raw, "mentions": sorted(m.mentions),
        "quote": ({"quoted_ts": m.quote.quoted_ts,
                   "author_id": m.quote.author_id,
                   "text": m.quote.text} if m.quote else None),
        "media": [{"platform_id": r.platform_id, "media_type": r.media_type,
                   "filename": r.filename, "size": r.size,
                   "voice_note": r.voice_note, "sticker": r.sticker,
                   "fetch_token": r.fetch_token, "on_demand": r.on_demand}
                  for r in m.media],
        "message_id": m.message_id,
        "media_blobs": [], "external_files": [],
        "voice": m.voice, "thread": m.thread,
        "edit_of": _target_to_dict(m.edit_of),
        "touch": ({"kind": m.touch.kind, "target": _target_to_dict(m.touch.target),
                   "target_author": m.touch.target_author, "emoji": m.touch.emoji,
                   "removed": m.touch.removed} if m.touch else None),
    }


def message_from_dict(d: dict[str, Any]) -> NormalizedMessage:
    q = d.get("quote")
    t = d.get("touch")
    return NormalizedMessage(
        platform=d["platform"], conversation_id=d["conversation_id"],
        conversation_type=d["conversation_type"], sender_id=d["sender_id"],
        sender_name=d.get("sender_name"), timestamp=int(d["timestamp"]),
        body=d["body"], raw=dict(d.get("raw") or {}),
        mentions=frozenset(d.get("mentions") or ()),
        quote=(Quote(quoted_ts=int(q["quoted_ts"]), author_id=q["author_id"],
                     text=q.get("text") or "") if q else None),
        media=tuple(MediaRef(
            platform_id=str(a["platform_id"]), media_type=a.get("media_type", ""),
            filename=a.get("filename"), size=a.get("size"),
            voice_note=bool(a.get("voice_note")), sticker=bool(a.get("sticker")),
            fetch_token=a.get("fetch_token", ""),
            on_demand=bool(a.get("on_demand"))) for a in d.get("media") or ()),
        message_id=d.get("message_id", ""),
        voice=bool(d.get("voice")), thread=d.get("thread", ""),
        edit_of=_target_from_dict(d.get("edit_of")),
        touch=(Touch(kind=t["kind"],
                     target=_target_from_dict(t["target"]) or EditTarget(),
                     target_author=t.get("target_author", ""),
                     emoji=t.get("emoji", ""),
                     removed=bool(t.get("removed"))) if t else None),
    )
