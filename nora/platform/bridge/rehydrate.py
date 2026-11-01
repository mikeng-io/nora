"""`NormalizedMessage` <-> the envelope's `message` dict, and `Observation`
<-> the envelope's `observation` dict.

The bridge parses upstream JSON ONCE (the comprehend-once rule at the boundary)
and the app rehydrates the typed value — it never re-interprets `raw`. `raw`
rides along opaquely for `raw_json` persistence only.
"""

from __future__ import annotations

from typing import Any

from nora.domain.media import MediaRef
from nora.domain.models import EditTarget, NormalizedMessage, Quote, Touch
from nora.domain.observations import (
    OBSERVATION_CONTRACT,
    Coverage,
    Observation,
    SourceScope,
    payload_from_dict,
)


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


def observation_to_dict(o: Observation) -> dict[str, Any]:
    """An `Observation` as the envelope's `observation` field — the typed
    wire record `nora.domain.observations` defines, `contract` versioned so
    an unrecognized payload contract fails retained, never best-effort."""
    return {
        "contract": OBSERVATION_CONTRACT,
        "kind": o.object_kind,
        "scope": {"platform": o.scope.platform,
                  "account_key": o.scope.account_key,
                  "conversation_id": o.scope.conversation_id,
                  "thread": o.scope.thread},
        "object_ref": o.object_ref,
        "target": _target_to_dict(o.target),
        "operation": o.operation,
        "subject_ref": o.subject_ref,
        "actor_id": o.actor_id,
        "actor_status": o.actor_status,
        "occurred_ms": o.occurred_ms,
        "received_ms": o.received_ms,
        "time_quality": o.time_quality,
        "source_revision": o.source_revision,
        "source_position": o.source_position,
        "revision_scope": o.revision_scope,
        "coverage": o.coverage.to_dict(),
        "payload": o.payload.to_dict(),
    }


def observation_from_dict(
    d: dict[str, Any], *, expect_scope: SourceScope | None = None,
) -> Observation:
    """`observation_to_dict`'s inverse. Validates the full contract —
    contract version, kind/op legality, actor/time invariants — at the
    boundary, so a malformed or future payload fails closed rather than
    degrading into a partially-typed state.

    `expect_scope` binds the observation to the envelope it rode in on: the
    observation's declared scope must equal the envelope's
    platform/account/conversation/thread — an observation for another room
    or account is a wrong-scope failure, not a record."""
    contract = d.get("contract")
    if contract != OBSERVATION_CONTRACT:
        raise ValueError(f"unsupported observation contract {contract!r}")
    scope = d.get("scope") or {}
    if expect_scope is not None and (
        str(scope.get("platform") or "") != expect_scope.platform
        or str(scope.get("account_key") or "") != expect_scope.account_key
        or str(scope.get("conversation_id") or "")
        != expect_scope.conversation_id
        or str(scope.get("thread") or "") != expect_scope.thread
    ):
        raise ValueError("observation scope does not match the envelope's")
    return Observation(
        object_kind=d["kind"],
        scope=SourceScope(
            platform=str(scope["platform"]),
            account_key=str(scope["account_key"]),
            conversation_id=str(scope["conversation_id"]),
            thread=str(scope.get("thread") or "")),
        object_ref=str(d["object_ref"]),
        target=_target_from_dict(d.get("target")),
        operation=d["operation"],
        payload=payload_from_dict(str(d["kind"]), dict(d.get("payload") or {})),
        subject_ref=str(d.get("subject_ref") or ""),
        actor_id=str(d.get("actor_id") or ""),
        actor_status=d.get("actor_status") or "not_supplied",
        occurred_ms=d.get("occurred_ms"),
        received_ms=int(d.get("received_ms") or 0),
        time_quality=d.get("time_quality") or "unknown",
        source_revision=d.get("source_revision"),
        source_position=d.get("source_position"),
        revision_scope=str(d.get("revision_scope") or ""),
        coverage=Coverage.from_dict(dict(d.get("coverage") or {})),
    )
