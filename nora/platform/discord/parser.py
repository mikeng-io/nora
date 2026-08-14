"""discord.py `Message` -> `NormalizedMessage` (design/discord-integration/02).

Guild channels only in v1 — a DM message (`message.guild is None`) is dropped,
mirroring Signal's own "DM — out of scope" rule
(`nora.platform.signal.parser.parse_event`). `conversation_id` is
`"<guild_id>/<channel_id>"`, the flattening `policy.example.toml` already
documents for Discord.

Built against a narrow structural Protocol, not `discord.py`'s own `Message`
type directly — same reasoning as `nora.platform.registry._SignalPlatformConfig`:
it states the dependency without forcing tests to construct a real
`discord.py` object, which needs internal `HTTPClient`/`ConnectionState`
wiring to build at all.
"""

from __future__ import annotations

import logging
from typing import Protocol

from ...domain.media import MediaRef, label_for, render_placeholder
from ...domain.models import NormalizedMessage, Quote
from .snowflake import timestamp_ms as _timestamp_ms

log = logging.getLogger("nora.discord.parser")


class _DiscordUser(Protocol):
    id: int
    display_name: str


class _DiscordGuild(Protocol):
    id: int


class _DiscordChannel(Protocol):
    id: int


class _DiscordAttachment(Protocol):
    id: int
    content_type: str | None
    filename: str
    size: int

    def is_voice_message(self) -> bool: ...


class _DiscordMessageReference(Protocol):
    # `resolved` is `Message | DeletedReferencedMessage | None` on the real
    # type; only the two attributes read below are used, structurally.
    resolved: object


class _DiscordMessage(Protocol):
    id: int
    author: _DiscordUser
    guild: _DiscordGuild | None
    channel: _DiscordChannel
    content: str
    mentions: list[_DiscordUser]
    attachments: list[_DiscordAttachment]
    reference: _DiscordMessageReference | None


def _media_ref(att: _DiscordAttachment) -> MediaRef:
    return MediaRef(
        platform_id=str(att.id),
        media_type=att.content_type or "",
        filename=att.filename or None,
        size=att.size if isinstance(att.size, int) else None,
        voice_note=att.is_voice_message(),
    )


def _body_parts(content: str, refs: tuple[MediaRef, ...]) -> list[str]:
    parts = []
    text = (content or "").strip()
    if text:
        parts.append(text)
    parts += [render_placeholder(label_for(ref)) for ref in refs]
    return parts


def _quoted(reference: _DiscordMessageReference | None) -> Quote | None:
    """The message this one replies to, if Discord resolved it.

    `reference.resolved` is only populated when Discord included the parent
    message in the gateway payload (the normal case for a reply to a recent,
    still-cached message) — absent, `None`, or a `DeletedReferencedMessage`
    all mean "no usable pointer", same as Signal's quote-with-no-id case.
    """
    if reference is None:
        return None
    resolved = reference.resolved
    author = getattr(resolved, "author", None)
    content = getattr(resolved, "content", None)
    resolved_id = getattr(resolved, "id", None)
    if author is None or content is None or resolved_id is None:
        return None
    return Quote(
        author_id=str(author.id), text=str(content).strip(), quoted_ts=_timestamp_ms(resolved_id)
    )


def parse_discord_message(
    message: _DiscordMessage | None, *, own_id: str
) -> NormalizedMessage | None:
    try:
        if message is None:
            return None
        if message.guild is None:
            return None  # DM — out of scope in v1 (design/discord-integration/00)
        if str(message.author.id) == own_id:
            return None  # never ingest Nora's own sends (Signal's R-12, mirrored)

        refs = tuple(_media_ref(a) for a in message.attachments)
        parts = _body_parts(message.content, refs)
        if not parts:
            return None  # no usable body

        return NormalizedMessage(
            platform="discord",
            conversation_id=f"{message.guild.id}/{message.channel.id}",
            conversation_type="group",
            sender_id=str(message.author.id),
            sender_name=message.author.display_name or None,
            timestamp=_timestamp_ms(message.id),
            body=" ".join(parts),
            # Not the literal gateway payload: discord.py's `Message` does not
            # expose one publicly once parsed. This is a reconstruction of the
            # fields this parser itself read — enough for `raw_json` to be
            # useful for debugging/audit, not a promise of byte-for-byte
            # fidelity to what Discord sent.
            raw={
                "id": message.id,
                "author_id": message.author.id,
                "guild_id": message.guild.id,
                "channel_id": message.channel.id,
                "content": message.content,
                "attachment_ids": [a.id for a in message.attachments],
            },
            mentions=frozenset(str(u.id) for u in message.mentions),
            quote=_quoted(message.reference),
            media=refs,
            # The snowflake, which is the ONLY way Discord's reaction endpoint
            # will accept a target — `(author, timestamp)` cannot reconstruct
            # it, because `snowflake.timestamp_ms` throws the low 22 bits away.
            message_id=str(message.id),
        )
    except Exception:
        log.exception("unparseable discord message, skipping")
        return None
