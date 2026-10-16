"""`DiscordMediaSource` — sticker AND attachment bytes off Discord's own CDN.

Discord media must not be fetched through `SignalMediaSource`: signal-cli asked
for a Discord snowflake can only answer `not_found`, a reason that reads like
"the platform lost it" when the truth is "wrong daemon". This module fetches
stickers and, per DEC-118, attachments, mirroring `SignalMediaSource.fetch`.

A sticker URL is DERIVED here from the id and format the parser recorded, never
taken from the event: `StickerItem.url` is a platform-supplied string, and a
fetcher that follows one is a fetcher a payload can point anywhere; the id is an
integer and the format is one of four known words, so a URL built from them can
only address Discord's own sticker CDN. An attachment has no such derivation —
Discord's real per-file URL cannot be reconstructed from an id alone, so it has
to come from `Attachment.url` on the event itself (`fetch_token`, DEC-118).
That reopens exactly the worry the paragraph above raises, so it is mitigated
instead of derived away: `_ATTACHMENT_HOSTS` allowlists the only hosts Discord
itself ever serves attachments from, and anything else is `bad_token` before a
socket opens. This is the same trust Signal already extends to signal-cli's
`platform_id` — the platform's own client library, not arbitrary input.

**Sticker URL shape is asserted, not probed:** the URL shape
(`{cdn_base}/stickers/{id}.{ext}` on `media.discordapp.net`), the claim that
`lottie` is served as `.json` rather than under its own extension, and the
CDN's 404/error behaviour come from public Discord documentation, not from a
live probe of the sticker path.

Never raises: `nora.domain.ports.MediaSource`'s contract. The caller is the
ingest path, and an exception there costs the MESSAGE to save bytes that are
only ever a bonus.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import aiohttp

from nora.domain.media import MediaBytes, MediaRef

log = logging.getLogger("nora.platform.discord.media")

DEFAULT_CDN_BASE = "https://media.discordapp.net"
# Matches `SignalMediaSource`'s reasoning: this runs on the ingest path the
# listener awaits, so a wedged CDN costs seconds and an honest miss, never the
# message.
DEFAULT_TIMEOUT_SECONDS = 8.0
# Sized for stickers (Discord's own ceiling is 512 KiB) at the class default;
# `nora.app.composition` wires the real deployment to `media.max_bytes`
# instead — the same 100 MiB ceiling `SignalMediaSource` uses — since an
# attachment is not bounded the way a sticker is.
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
# The only hosts Discord itself ever serves attachment bytes from. An
# `attachment:` token whose host is anything else is refused before a socket
# opens — see the module docstring.
DEFAULT_ATTACHMENT_HOSTS = frozenset({"cdn.discordapp.com", "media.discordapp.net"})

_ATTACHMENT_PREFIX = "attachment:"

# The file extension each sticker container is actually served under.
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
        attachment_hosts: frozenset[str] | set[str] = DEFAULT_ATTACHMENT_HOSTS,
    ) -> None:
        self._cdn_base = cdn_base.rstrip("/")
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._attachment_hosts = attachment_hosts

    async def fetch(self, *, ref: MediaRef) -> MediaBytes:
        """Sticker or attachment bytes, or an unavailable answer saying why.

        `reason` is a closed vocabulary because it becomes a metric label:
        `not_supported`, `bad_token`, `unreachable`, `not_found`, `too_large`.
        """
        if ref.sticker:
            parts = ref.fetch_token.split(":")
            if len(parts) != 3 or parts[0] != "sticker" or not parts[1].isdigit():
                log.warning("sticker token this adapter cannot read")
                return MediaBytes(available=False, reason="bad_token")
            extension = _EXTENSION.get(parts[2])
            if extension is None:
                return MediaBytes(available=False, reason="bad_token")
            return await self._get(f"{self._cdn_base}/stickers/{parts[1]}.{extension}")

        if ref.fetch_token.startswith(_ATTACHMENT_PREFIX):
            url = ref.fetch_token[len(_ATTACHMENT_PREFIX):]
            if urlsplit(url).hostname not in self._attachment_hosts:
                log.warning("attachment token off Discord's own CDN host")
                return MediaBytes(available=False, reason="bad_token")
            return await self._get(url)

        # Not a sticker and no attachment URL was recorded — an embed, or some
        # future Discord object `_media_ref`/`_sticker_ref` do not cover.
        return MediaBytes(available=False, reason="not_supported")

    async def _get(self, url: str) -> MediaBytes:
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
                    log.warning("discord CDN: HTTP %s", resp.status)
                    return MediaBytes(available=False, reason="unreachable")
                declared = resp.content_length
                if declared is not None and declared > self._max_bytes:
                    return MediaBytes(available=False, reason="too_large")
                data = await _read_limited(resp.content, self._max_bytes)
        except Exception as exc:                                   # noqa: BLE001
            # Blind and narrowly scoped: every transport failure is the same
            # answer to this caller, and the port forbids raising.
            log.warning("discord CDN unreachable: %s", type(exc).__name__)
            return MediaBytes(available=False, reason="unreachable")

        if len(data) > self._max_bytes:
            # The declared length can be absent or wrong, so the real length is
            # checked too — the same belt-and-braces `SignalMediaSource._decode`
            # applies to its base64.
            return MediaBytes(available=False, reason="too_large")
        return MediaBytes(available=True, data=data)


async def _read_limited(stream: aiohttp.StreamReader, limit: int) -> bytes:
    """Read a response body up to `limit` bytes, WITHOUT truncating early.

    `StreamReader.read(n)` returns as soon as the FIRST buffered chunk
    arrives — it does not wait for `n` bytes or EOF. Discord's CDN streams
    attachments in ~1.4 KB packets, so a single `read(max_bytes + 1)` would
    return a truncated body that is stored as if complete (DEC-118). Loop until EOF or the
    ceiling, so a
    body larger than `limit` is detected by MEASUREMENT, not by luck.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            return b"".join(chunks) + chunk
        chunks.append(chunk)
    return b"".join(chunks)
