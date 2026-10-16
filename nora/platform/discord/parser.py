"""discord.py `Message` -> `NormalizedMessage`.

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

import dataclasses
import logging
import re
from typing import Any, Protocol

from ...domain.media import MediaRef, label_for, render_placeholder
from ...domain.models import EditTarget, NormalizedMessage, Quote, Touch
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
    url: str

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
    # The turns that carry NO `content` and NO attachment. Read structurally
    # with `getattr` at the call site rather than required here, so a double
    # that predates them still satisfies this Protocol — but declared, because
    # a reader has to know they exist to know why `_body_parts` looks at them.
    stickers: list[object]
    embeds: list[object]


def _media_ref(att: _DiscordAttachment) -> MediaRef:
    # DEC-118: `att.url` is Discord's own CDN URL for this attachment, not
    # something derivable from the id — `DiscordMediaSource` allowlists the
    # host before following it (see that module's docstring).
    return MediaRef(
        platform_id=str(att.id),
        media_type=att.content_type or "",
        filename=att.filename or None,
        size=att.size if isinstance(att.size, int) else None,
        voice_note=att.is_voice_message(),
        fetch_token=f"attachment:{att.url}" if att.url else "",
    )


# Discord's four sticker containers, mapped to what the bytes actually are.
# `lottie` is a JSON animation and is recorded as such rather than as an image
# or as an empty type: the first would send JSON to a vision model, the second
# would leave the stored row unable to say what it holds.
_STICKER_MEDIA_TYPE = {
    "png": "image/png",
    "apng": "image/apng",
    "gif": "image/gif",
    "lottie": "application/json",
}


def _sticker_ref(item: object) -> MediaRef | None:
    """One `MediaRef` per `StickerItem`, or None if it is not one.

    `item.name` is deliberately unread. A guild sticker's name is unbounded
    text a server admin chose, and putting it in the body is what
    `nora/domain/media.py` avoids for sender-supplied
    filenames — "a filename is unbounded text: it can hold a phone number, an
    ACI, or an instruction."
    """
    sticker_id = getattr(item, "id", None)
    fmt = getattr(getattr(item, "format", None), "name", None)
    if not isinstance(sticker_id, int) or not isinstance(fmt, str):
        return None
    return MediaRef(
        platform_id=str(sticker_id),
        media_type=_STICKER_MEDIA_TYPE.get(fmt, ""),
        fetch_token=f"sticker:{sticker_id}:{fmt}",
        sticker=True,
    )


_MENTION_TOKEN = re.compile(r"<@!?(\d+)>")


def _resolve_inbound_mentions(content: str, mentions: list[_DiscordUser]) -> str:
    """Rewrite Discord's `<@id>`/`<@!id>` wire syntax to `@display_name`.

    discord.py already resolved WHO these ids are — `message.mentions` — so
    this is a pure text substitution, no extra I/O. Doing it here, once, at
    comprehend time, means everything that reads `NormalizedMessage.body`
    downstream (the model's own transcript in `nora.agent.prefill`, the
    Honcho feed in `nora.ingest.stages.memory_feed`) sees a name instead of a
    bare snowflake nobody — human or model — can read.

    An id present in `content` but absent from `mentions` (a shape Discord
    should not send) is left as its raw token: guessing a name for it would
    be worse than the unreadable-but-honest original (R-5's shape applied to
    inbound normalization).
    """
    if not mentions:
        return content
    names = {str(u.id): (u.display_name or "someone") for u in mentions}

    def _sub(m: re.Match[str]) -> str:
        name = names.get(m.group(1))
        return f"@{name}" if name is not None else m.group(0)

    return _MENTION_TOKEN.sub(_sub, content)


def _body_parts(message: _DiscordMessage, refs: tuple[MediaRef, ...]) -> list[str]:
    """The renderable body, or an empty list if there is genuinely nothing.

    THE PLACEHOLDERS ARE NOT DECORATION. Dropping a content-less turn makes a
    sticker-only message vanish entirely, so the sender watches Nora ignore
    something they clearly sent. Discord has more ways to send a turn with an
    empty `content` than Signal does (a poll, a forward, an embed-only post).

    The cost is worse than one ignored message: a dropped turn never reaches
    the audit store, so Nora later reasons over a transcript with a hole she
    cannot see — the room said something and her computable model of it
    silently disagrees.

    Bare placeholders, like Signal's `[sticker]`: the name of a sticker or the
    contents of a poll would be a claim about what was sent, and rendering
    something Nora has not actually read is the guess R-5 forbids. `[…]` says
    a turn happened and what kind, which is exactly what the transcript needs
    to stop lying by omission.

    Stickers are not in this chain: they are ordinary `MediaRef`s (`_sticker_ref`),
    so they render through the same `render_placeholder` line as an attachment,
    in `refs` order, above. What remains here is ordered most specific first;
    a message carries at most one of these in practice.
    """
    parts = []
    text = _resolve_inbound_mentions(message.content or "", message.mentions).strip()
    if text:
        parts.append(text)
    parts += [render_placeholder(label_for(ref)) for ref in refs]
    if parts:
        return parts
    # Nothing rendered yet — so this is one of the content-less turns.
    if getattr(message, "poll", None) is not None:
        parts.append("[poll]")
    elif getattr(message, "message_snapshots", None):
        parts.append("[forwarded message]")
    elif getattr(message, "embeds", None):
        parts.append("[embed]")
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
            return None  # DM — out of scope in v1
        if str(message.author.id) == own_id:
            return None  # never ingest Nora's own sends (Signal's R-12, mirrored)

        refs = tuple(_media_ref(a) for a in message.attachments)
        stickers = tuple(
            r for r in (_sticker_ref(s) for s in getattr(message, "stickers", None) or ())
            if r is not None
        )
        refs = (*refs, *stickers)
        parts = _body_parts(message, refs)
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
                "sticker_ids": [
                    getattr(s, "id", None) for s in getattr(message, "stickers", None) or ()
                ],
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


def parse_discord_edit(
    message: _DiscordMessage | None, *, own_id: str
) -> NormalizedMessage | None:
    """An edited message (`on_raw_message_edit`'s `payload.message`) → an
    edit of the message it is.

    The same parse as a new message, so the edit's body, mentions and media
    are built by exactly the rules the original's were — then marked with
    `edit_of` (its own snowflake: a Discord edit keeps its id) and stamped
    with `edited_at`, the time the TEXT changed.

    `MESSAGE_UPDATE` is not only an edit. It also fires when a link preview
    resolves, a message is pinned, a thread is started from it or a poll ends.
    A message its author NEVER edited has no `edited_at`, so such an update is
    not an edit and answers None here, before custody fetches anything. It is
    also what closes a race: a link preview's update can arrive while the
    message it updates is still being written, and would otherwise look like
    an edit of nothing. An update to a message that WAS edited earlier still
    carries that older `edited_at`; ingest ends it `edit_stale`
    or `edit_unchanged`."""
    edited_at = getattr(message, "edited_at", None) if message is not None else None
    if edited_at is None:
        return None
    parsed = parse_discord_message(message, own_id=own_id)
    if parsed is None or message is None:
        return None
    try:
        ts = int(edited_at.timestamp() * 1000)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return dataclasses.replace(
        parsed, timestamp=ts, edit_of=EditTarget(message_id=str(message.id)),
    )


def parse_discord_reaction(
    payload: Any, *, own_id: str, removed: bool,
) -> NormalizedMessage | None:
    """`on_raw_reaction_add`/`on_raw_reaction_remove`'s
    `RawReactionActionEvent` → a `Touch` on the message it names.

    The RAW events, for `on_raw_message_edit`'s reason: the cached ones fire
    only for a message still in discord.py's cache. Guild channels only (a DM
    reaction has no `guild_id`); Nora's own reaction is dropped (R-12). The
    Gateway gives a reaction no time of its own, so `timestamp` is 0 and ingest
    stamps arrival."""
    try:
        guild_id = getattr(payload, "guild_id", None)
        if guild_id is None:
            return None
        user_id = str(getattr(payload, "user_id", "") or "")
        if not user_id or user_id == own_id:
            return None
        emoji = str(getattr(payload, "emoji", "") or "")
        message_id = str(getattr(payload, "message_id", "") or "")
        if not emoji or not message_id:
            return None
        channel_id = getattr(payload, "channel_id", None)
        return NormalizedMessage(
            platform="discord",
            conversation_id=f"{guild_id}/{channel_id}",
            conversation_type="group",
            sender_id=user_id,
            sender_name=None,
            timestamp=0,
            body="",
            raw={"message_id": message_id, "guild_id": guild_id,
                 "channel_id": channel_id, "user_id": user_id, "emoji": emoji,
                 "removed": removed},
            touch=Touch(kind="reaction", target=EditTarget(message_id=message_id),
                        emoji=emoji, removed=removed),
        )
    except Exception:
        log.exception("unparseable discord reaction, skipping")
        return None


def parse_discord_delete(payload: Any) -> NormalizedMessage | None:
    """`on_raw_message_delete`'s `RawMessageDeleteEvent` → a delete `Touch`.

    The event names the message and its channel and NOT who deleted it (an
    author or a moderator), so the sender is `""` and ingest attributes the
    row to the target's author, keeping `sender_id` empty rather than claim
    more. A bulk delete (`on_raw_bulk_message_delete`, a moderation purge) is
    not recorded: it names no one and a purge is not a member withdrawing
    something they said."""
    try:
        guild_id = getattr(payload, "guild_id", None)
        if guild_id is None:
            return None
        message_id = str(getattr(payload, "message_id", "") or "")
        if not message_id:
            return None
        channel_id = getattr(payload, "channel_id", None)
        return NormalizedMessage(
            platform="discord",
            conversation_id=f"{guild_id}/{channel_id}",
            conversation_type="group",
            sender_id="",
            sender_name=None,
            timestamp=0,
            body="",
            raw={"message_id": message_id, "guild_id": guild_id,
                 "channel_id": channel_id},
            touch=Touch(kind="delete", target=EditTarget(message_id=message_id)),
        )
    except Exception:
        log.exception("unparseable discord delete, skipping")
        return None
