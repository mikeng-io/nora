"""signal-cli SSE envelope → NormalizedMessage (spec C.3, design/01).

Group messages only in v2; DMs and syncMessages are dropped. Attachments become
placeholders — no download (v2.1).
"""

from __future__ import annotations

import logging

from ...domain.models import NormalizedMessage

log = logging.getLogger("nora.signal.parser")


def _attachment_placeholder(att: dict) -> str:
    if str(att.get("contentType", "")).startswith("image/"):
        return "[image]"
    return f"[attachment: {att.get('filename') or att.get('id') or 'file'}]"


def _extract_envelope(event: dict) -> dict | None:
    """Return the envelope from either of signal-cli's two framings, or None.

    signal-cli frames the *same* envelope two different ways, and its
    `signal-cli-jsonrpc(5)` man page documents only the first:

        socket / TCP / stdin   {"method": "receive", "params": {"envelope": …}}
        HTTP GET /api/v1/events    {"envelope": …, "account": …}

    The SSE stream carries `receive` in the SSE ``event:`` field — which the
    listener strips along with the framing — and puts the envelope at the top
    level with **no** ``method`` key at all. Nora runs on the HTTP transport,
    so the old ``method != "receive"`` guard dropped every real message before
    it was ever looked at: signal-cli received, Nora ingested nothing, and
    under R-5 that is indistinguishable from a quiet group.

    Both framings are live — the socket transports really do send the
    JSON-RPC form — so this accepts either rather than trading one hardcoded
    assumption for the opposite one.
    """
    if "method" in event:
        if event.get("method") != "receive":
            return None                       # typing/receipt notification
        params = event.get("params") or {}
        # subscribeReceive (--receive-mode=manual) nests one level deeper.
        result = params.get("result")
        if isinstance(result, dict):
            return result.get("envelope") or {}
        return params.get("envelope") or {}
    env = event.get("envelope")
    return env if isinstance(env, dict) else None


def parse_event(event: dict, own_account: str) -> NormalizedMessage | None:
    try:
        env = _extract_envelope(event or {})
        if env is None:
            return None
        data = env.get("dataMessage")
        if not isinstance(data, dict):
            return None                       # syncMessage / typing / receipt / malformed

        group_id = (data.get("groupInfo") or {}).get("groupId")
        if not group_id:
            return None                       # DM — out of scope in v2

        # `sourceUuid` matters: a hidden Signal phone number means `source`
        # and `sourceNumber` are both empty, and the ACI (`sourceUuid`) is the
        # only id Nora ever sees for that sender — never falling back to it
        # meant such a sender could never resolve to a person at all
        # (design/10 §1.1, §1.2).
        source = env.get("source") or env.get("sourceNumber") or env.get("sourceUuid") or ""
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
    except Exception:
        log.exception("unparseable envelope, skipping")
        return None
