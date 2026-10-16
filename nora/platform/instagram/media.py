"""`InstagramMediaSource` — attachment bytes off Instagram's own CDN.

Before this existed there was no `MediaSource` for this platform at all, so
`nora.ingest.stages.media` resolved `sources.for_platform("instagram")` to
`None` and counted every attachment `not_supported`. An image Miles sent to the
group was recorded as a bare `[image]` — a label with no handle — and Nora
answered, correctly, that the image had no code she could read. Signal and
Discord record `[image c14dca]`; Instagram recorded half of it.

**The URL IS the handle, and that is a MEASUREMENT rather than an argument.**
`parser._media_ref` used to record no `fetch_token` on the reasoning that the
bridge exposes no media-fetch route, so any handle would name something no code
could resolve. The first half is true and the conclusion does not follow: the
bridge hands over `media.url`, a directly fetchable CDN url whose query string
carries its own signature, and those urls were measured returning `200` with
**no cookies, no user-agent and no referer** — the signature in the url is the
authorization. So there is nothing to ask the bridge for; there is something to
GET. `03-transport-contract.md`'s media object is the contract.

That makes this the same trust `DiscordMediaSource` takes on `Attachment.url`
and it carries the same exposure: the url comes off the EVENT, so a forged or
redirect-poisoned payload is a fetcher pointed anywhere. `_host_allowed` is the
mitigation and it runs before a socket opens.

**The allowlist is by SUFFIX, not by exact host, and Instagram is why.** Discord
serves attachments from two fixed names. Instagram's CDN names its edge in the
host itself — `scontent.fhkg3-1.fna.fbcdn.net`, `instagram.fhkg3-1.fna.fbcdn.
net`, `scontent.cdninstagram.com`, `external.fhkg3-1.fna.fbcdn.net` were all
observed on one account in one week — so an exact set is a list that goes stale
the first time the account is served from another PoP, and it goes stale as a
SILENT loss of every image. Two registrable domains are matched instead, on a
leading-dot suffix so `notfbcdn.net` and `fbcdn.net.evil.example` are both
refused, and anything else is `bad_token` before a socket opens.

Never raises: `nora.domain.ports.MediaSource`'s contract. The caller is the
ingest path, and an exception there costs the MESSAGE to save bytes that are
only ever a bonus.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import aiohttp

from nora.domain.media import MediaBytes, MediaRef

log = logging.getLogger("nora.platform.instagram.media")

# Matches `DiscordMediaSource`'s reasoning: this runs on the ingest path the
# listener awaits, so a wedged CDN costs seconds and an honest miss, never the
# message.
DEFAULT_TIMEOUT_SECONDS = 8.0
# The class default is a floor for tests and direct construction;
# `nora.app.composition` wires the real deployment to `media.max_bytes`, the
# same 100 MiB ceiling Signal and Discord get.
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
# The two registrable domains Instagram serves media bytes from — matched as
# SUFFIXES, see the module docstring. A bare entry (no leading dot) is matched
# exactly as well, which is what lets a test point this at a loopback host.
DEFAULT_ATTACHMENT_HOST_SUFFIXES = frozenset({".fbcdn.net", ".cdninstagram.com"})

_ATTACHMENT_PREFIX = "attachment:"


def _host_allowed(host: str | None, suffixes: frozenset[str] | set[str]) -> bool:
    """Whether `host` sits under one of the allowed registrable domains.

    A leading-dot suffix (`.fbcdn.net`) admits any subdomain and refuses both
    the bare domain typed as a prefix of something else (`notfbcdn.net`) and a
    domain that merely CONTAINS it (`fbcdn.net.evil.example`) — the two ways a
    substring check would have been forged. An entry with no leading dot is an
    exact host, which is how a test names its own loopback server.
    """
    if not host:
        return False
    host = host.lower()
    return any(
        host.endswith(suffix) if suffix.startswith(".") else host == suffix
        for suffix in suffixes
    )


class InstagramMediaSource:
    """Satisfies `nora.domain.ports.MediaSource` for Instagram."""

    platform = "instagram"

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        attachment_host_suffixes: frozenset[str] | set[str] = (
            DEFAULT_ATTACHMENT_HOST_SUFFIXES
        ),
    ) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._host_suffixes = attachment_host_suffixes

    async def fetch(self, *, ref: MediaRef) -> MediaBytes:
        """Attachment bytes, or an unavailable answer saying why.

        `reason` is a closed vocabulary because it becomes a metric label:
        `not_supported`, `bad_token`, `unreachable`, `not_found`, `too_large`
        — the same five `DiscordMediaSource` reports, so one dashboard reads
        both platforms.

        One path for every `media.kind`, including `animated`: an Instagram
        sticker is a url like any other attachment, unlike a Discord sticker
        whose url is DERIVED from an id. There is nothing here to branch on.
        """
        if not ref.fetch_token.startswith(_ATTACHMENT_PREFIX):
            # `one_time` is the case this exists for: a RAVEN delta carries
            # `attachment: null` BY DESIGN, so the parser records no token and
            # nothing here must invent one. Fetching view-once media IS
            # viewing it, and it is visible to the sender — an operator's
            # consent decision, not a fetcher's default.
            return MediaBytes(available=False, reason="not_supported")

        url = ref.fetch_token[len(_ATTACHMENT_PREFIX):]
        if not _host_allowed(urlsplit(url).hostname, self._host_suffixes):
            log.warning("attachment token off Instagram's own CDN hosts")
            return MediaBytes(available=False, reason="bad_token")
        return await self._get(url)

    async def _get(self, url: str) -> MediaBytes:
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self._timeout)
                ) as session,
                # NO headers of any kind, and that is measured rather than
                # assumed: these urls answered 200 with no cookie, no
                # user-agent and no referer. Sending the session's cookies to
                # a CDN would be handing an account credential to a host the
                # allowlist admits by suffix.
                session.get(url) as resp,
            ):
                if resp.status == 404:
                    return MediaBytes(available=False, reason="not_found")
                if resp.status != 200:
                    log.warning("instagram CDN: HTTP %s", resp.status)
                    return MediaBytes(available=False, reason="unreachable")
                declared = resp.content_length
                if declared is not None and declared > self._max_bytes:
                    return MediaBytes(available=False, reason="too_large")
                data = await _read_limited(resp.content, self._max_bytes)
        except Exception as exc:                                   # noqa: BLE001
            # Blind and narrowly scoped: every transport failure is the same
            # answer to this caller, and the port forbids raising.
            log.warning("instagram CDN unreachable: %s", type(exc).__name__)
            return MediaBytes(available=False, reason="unreachable")

        if len(data) > self._max_bytes:
            # The declared length can be absent or wrong, so the real length is
            # checked too.
            return MediaBytes(available=False, reason="too_large")
        return MediaBytes(available=True, data=data)


async def _read_limited(stream: aiohttp.StreamReader, limit: int) -> bytes:
    """Read a response body up to `limit` bytes, WITHOUT truncating early.

    A copy of `nora.platform.discord.media._read_limited`, deliberately, and
    the reason is the defect it encodes rather than tidiness. `StreamReader.
    read(n)` returns as soon as the FIRST buffered chunk arrives — it does not
    wait for `n` bytes or EOF — so `read(max_bytes + 1)` on a 2.3 MB image
    returned 1.4 KB and the truncated body was stored as if complete (DEC-118).
    Each adapter owns its own fetch loop here
    (Signal's is a base64 decode and shares nothing with either), and the one
    thing that must never be re-derived from scratch is this loop; the
    contract suite runs a chunked-body case against BOTH copies so a drift is
    red rather than silent.
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
