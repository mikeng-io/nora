"""Discord REST send: `POST /channels/{id}/messages` (design/discord-integration/02).

Mirrors `nora.platform.signal.send.SignalSender` deliberately — same session
construction, same status handling, same "log and return, never raise"
contract — for the same reason that module gives: a reader who has read one
should not have to re-read the other.

`mentions`/`styles`/`preview`/`attachments` are the port's DECORATION
parameters (`nora.domain.ports.PlatformAdapter.send`'s own docstring: "a
platform with no mention concept ignores them"). Discord expresses mentions
and rich text inline in `content` itself (`<@id>`, markdown) — a different
mechanism from Signal's parallel-array approach — and wiring that up is not
in v1 scope (design/discord-integration/00-overview.md). This sends plain
text only; the four decoration parameters exist so this satisfies the port's
signature and are otherwise unused.
"""

from __future__ import annotations

import logging

import aiohttp

from ...domain.models import ConversationTarget
from ...domain.ports import LinkPreview, Mention, OutboundAttachment, SendResult, TextStyle
from .snowflake import timestamp_ms

log = logging.getLogger("nora.discord.send")

DEFAULT_API_BASE = "https://discord.com/api/v10"


class DiscordSender:
    def __init__(
        self, token: str, *, base_url: str = DEFAULT_API_BASE, timeout: float = 10.0
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    async def send(
        self,
        target: ConversationTarget,
        text: str,
        *,
        mentions: tuple[Mention, ...] = (),
        styles: tuple[TextStyle, ...] = (),
        preview: LinkPreview | None = None,
        attachments: tuple[OutboundAttachment, ...] = (),
        request_id: str = "",
    ) -> SendResult:
        if target.conversation_type != "group":
            log.error(
                "refusing to send to conversation_type=%r (v1 is guild channels only)",
                target.conversation_type,
            )
            return SendResult(ok=False)
        # conversation_id is "<guild_id>/<channel_id>" (policy.example.toml);
        # the REST endpoint below addresses the channel alone.
        channel_id = target.conversation_id.rsplit("/", 1)[-1]
        headers = {"Authorization": f"Bot {self._token}"}
        try:
            async with (
                aiohttp.ClientSession(timeout=self._timeout) as session,
                session.post(
                    f"{self._base_url}/channels/{channel_id}/messages",
                    headers=headers,
                    json={"content": text},
                ) as resp,
            ):
                if resp.status == 429:
                    body = await resp.json()
                    retry_after = body.get("retry_after") if isinstance(body, dict) else None
                    log.error(
                        "send rate-limited: HTTP 429 (channel=%s, id=%s)", channel_id, request_id
                    )
                    return SendResult(
                        ok=False,
                        retryable=True,
                        retry_after=float(retry_after) if retry_after is not None else None,
                    )
                if resp.status not in (200, 201):
                    log.error(
                        "send failed: HTTP %s (channel=%s, id=%s)",
                        resp.status, channel_id, request_id,
                    )
                    return SendResult(ok=False)
                body = await resp.json()
        except Exception as exc:  # noqa: BLE001 — a send failure is never fatal
            log.error(
                "send failed: %s (channel=%s, id=%s)", type(exc).__name__, channel_id, request_id
            )
            return SendResult(ok=False)
        message_id = body.get("id") if isinstance(body, dict) else None
        log.info("sent %d chars to %s (id=%s)", len(text), target.conversation_id, request_id)
        return SendResult(
            ok=True,
            timestamp=timestamp_ms(int(message_id)) if message_id is not None else None,
        )
