"""`DiscordMediaSource` — sticker bytes off Discord's CDN. Stickers only.

**Attachments are explicitly refused, and that is the point of this file
existing at all.** Before it, `_build_media_custody` constructed one
`SignalMediaSource` and `nora.ingest.stages.media` did not gate on platform, so
a Discord attachment was fetched by asking signal-cli for a Discord snowflake —
which can only answer `not_found`, a reason that reads like "the platform lost
it" when the truth is "we asked the wrong daemon". `not_supported` is the
honest answer, it is counted, and it does not quietly widen this feature into
storing the group's photographs (spec §3.5).

A sticker URL is DERIVED here from the id and format the parser recorded, never
taken from the event. `StickerItem.url` is a platform-supplied string, and a
fetcher that follows one is a fetcher a payload can point anywhere; the id is an
integer and the format is one of four known words, so a URL built from them can
only address Discord's own sticker CDN.

Never raises: `nora.domain.ports.MediaSource`'s contract. The caller is the
ingest path, and an exception there costs the MESSAGE to save bytes that are
only ever a bonus.
"""

from __future__ import annotations

import logging

import aiohttp

from nora.domain.media import MediaBytes, MediaRef

log = logging.getLogger("nora.platform.discord.media")

DEFAULT_CDN_BASE = "https://media.discordapp.net"
# Matches `SignalMediaSource`'s reasoning: this runs on the ingest path the
# listener awaits, so a wedged CDN costs seconds and an honest miss, never the
# message.
DEFAULT_TIMEOUT_SECONDS = 8.0
# Discord's own sticker ceiling is 512 KiB; 2 MiB is slack for a format change
# without becoming a hole.
DEFAULT_MAX_BYTES = 2 * 1024 * 1024

# The file extension each container is actually served under.
_EXTENSION = {"png": "png", "apng": "png", "gif": "gif", "lottie": "json"}


class DiscordMediaSource:
    """Satisfies `nora.domain.ports.MediaSource` for Discord."""

    platform = "discord"

    def __init__(
        self,
        *,
        cdn_base: str = DEFAULT_CDN_BASE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._cdn_base = cdn_base.rstrip("/")
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes

    async def fetch(self, *, ref: MediaRef) -> MediaBytes:
        """Sticker bytes, or an unavailable answer saying why.

        `reason` is a closed vocabulary because it becomes a metric label:
        `not_supported`, `bad_token`, `unreachable`, `not_found`, `too_large`.
        """
        if not ref.sticker:
            return MediaBytes(available=False, reason="not_supported")
        parts = ref.fetch_token.split(":")
        if len(parts) != 3 or parts[0] != "sticker" or not parts[1].isdigit():
            log.warning("sticker token this adapter cannot read")
            return MediaBytes(available=False, reason="bad_token")
        extension = _EXTENSION.get(parts[2])
        if extension is None:
            return MediaBytes(available=False, reason="bad_token")

        url = f"{self._cdn_base}/stickers/{parts[1]}.{extension}"
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout)
                ) as session,
                session.get(url) as resp,
            ):
                if resp.status == 404:
                    return MediaBytes(available=False, reason="not_found")
                if resp.status != 200:
                    log.warning("sticker CDN: HTTP %s", resp.status)
                    return MediaBytes(available=False, reason="unreachable")
                declared = resp.content_length
                if declared is not None and declared > self._max_bytes:
                    return MediaBytes(available=False, reason="too_large")
                data = await resp.content.read(self._max_bytes + 1)
        except Exception as exc:                                   # noqa: BLE001
            # Blind and narrowly scoped: every transport failure is the same
            # answer to this caller, and the port forbids raising.
            log.warning("sticker CDN unreachable: %s", type(exc).__name__)
            return MediaBytes(available=False, reason="unreachable")

        if len(data) > self._max_bytes:
            # The declared length can be absent or wrong, so the real length is
            # checked too — the same belt-and-braces `SignalMediaSource._decode`
            # applies to its base64.
            return MediaBytes(available=False, reason="too_large")
        return MediaBytes(available=True, data=data)
