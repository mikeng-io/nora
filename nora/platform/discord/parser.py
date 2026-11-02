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
from ...domain.observations import (
    ArtifactPayload,
    Coverage,
    MembershipPayload,
    MessageMetadataPayload,
    Observation,
    PinPayload,
    PollPayload,
    ReactionPayload,
    RoomPayload,
    SourceScope,
)
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

        # A THREAD is never a room: `conversation_id` keeps naming the PARENT
        # channel — a forum post is a thread whose parent is the forum — and
        # `thread` carries the thread's own channel id so the reply can land
        # back inside it. `parent_id` is how discord.py marks a Thread (a
        # `TextChannel` has none), which the same read serves for both.
        parent_id = getattr(message.channel, "parent_id", None)
        room_id = parent_id if parent_id is not None else message.channel.id
        return NormalizedMessage(
            platform="discord",
            conversation_id=f"{message.guild.id}/{room_id}",
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
            thread="" if parent_id is None else str(message.channel.id),
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
    payload: Any, *, own_id: str, removed: bool, room_channel_id: str | None = None,
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
        room_id = room_channel_id if room_channel_id is not None else channel_id
        return NormalizedMessage(
            platform="discord",
            conversation_id=f"{guild_id}/{room_id}",
            conversation_type="group",
            thread="" if room_channel_id is None else str(channel_id),
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


def parse_discord_delete(
    payload: Any, *, room_channel_id: str | None = None,
) -> NormalizedMessage | None:
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
        room_id = room_channel_id if room_channel_id is not None else channel_id
        return NormalizedMessage(
            platform="discord",
            conversation_id=f"{guild_id}/{room_id}",
            conversation_type="group",
            thread="" if room_channel_id is None else str(channel_id),
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


# ── platform observations (design/platform-observations) ───────────────────
# One parse function per Gateway family. Each answers a typed `Observation`
# — never a fabricated `NormalizedMessage` — and is bridge-mode only: the
# `PlatformAdapter` message API is unchanged. Every event without its own
# source time carries `occurred_ms=None`/`time_quality="unknown"`; a source
# position is attached only where `bridge_app` joins the dispatch's session
# `s` — never invented here.


def _message_scope(message: Any, account_key: str) -> SourceScope:
    """The room scope of a message-shaped event: the thread's PARENT is the
    room (a thread is a span inside it — `parse_discord_message`'s own
    convention). The observation wire names the room only — `Envelope`
    carries no thread sub-scope and `WorkRunner`'s `expect_scope` binds to
    `thread=""` — so the thread's identity stays on the observation's
    refs (object/target), never in `scope.thread`."""
    parent_id = getattr(message.channel, "parent_id", None)
    room_id = parent_id if parent_id is not None else message.channel.id
    return SourceScope(
        platform="discord", account_key=account_key,
        conversation_id=f"{message.guild.id}/{room_id}")


def _event_scope(
    guild_id: Any, channel_id: Any, room_channel_id: str | None,
    account_key: str,
) -> SourceScope:
    """The scope of a channel-naming raw event (votes, clears): the channel
    is a room itself unless the caller resolved it to a thread's parent —
    `room_channel_id` non-None then means the channel IS a thread, and the
    same room-scope rule as `_message_scope` applies."""
    room_id = room_channel_id if room_channel_id is not None else str(
        channel_id)
    return SourceScope(
        platform="discord", account_key=account_key,
        conversation_id=f"{guild_id}/{room_id}")


def _poll_object(message_id: Any) -> str:
    return f"poll:{message_id}"


def parse_discord_poll_definition(
    message: Any, *, own_id: str, account_key: str,
) -> Observation | None:
    """`MESSAGE_CREATE` carrying a poll → `poll/create`, the typed sidecar
    that rides the message's own envelope. Discord polls are never
    anonymous: every voter is a `user_id` on `MESSAGE_POLL_VOTE_*`.

    The reads name the HYDRATED discord.py object's attributes — the
    `Message.poll` a `MESSAGE_CREATE` arrives holding: `Poll.question`
    (a str), `Poll.multiple`, `PollAnswer.id`/`media.text` — never the
    gateway payload's own field names, which the SDK does not re-expose."""
    poll = getattr(message, "poll", None)
    if (message is None or poll is None
            or getattr(message, "guild", None) is None
            or str(getattr(message.author, "id", "")) == own_id):
        return None
    options = tuple(
        (ref, label) for ref, label in (
            (str(getattr(a, "id", "") or ""),
             str(getattr(getattr(a, "media", None), "text", "") or ""))
            for a in getattr(poll, "answers", None) or ())
        if ref)
    if not options:
        return None
    expires = getattr(poll, "expires_at", None)
    try:
        close_ms = int(expires.timestamp() * 1000) if expires is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        close_ms = None
    return Observation(
        object_kind="poll", scope=_message_scope(message, account_key),
        object_ref=_poll_object(message.id), operation="create",
        payload=PollPayload(
            question=str(getattr(poll, "question", "") or ""),
            options=options,
            multi_select=bool(getattr(poll, "multiple", False)),
            anonymous=False, state="open", close_ms=close_ms,
            parent_ref=f"msg:{message.id}"),
        target=EditTarget(message_id=str(message.id)),
        actor_id=str(message.author.id), actor_status="known",
        occurred_ms=_timestamp_ms(message.id), time_quality="upstream")


def parse_discord_poll_close(
    message: Any, *, account_key: str,
) -> tuple[Observation, ...]:
    """A `MESSAGE_UPDATE` whose poll finalized → the close.

    Discord delivers finalized tallies through the update seam WITHOUT an
    `edited_timestamp`, which is exactly why `parse_discord_edit` answers
    None for it — the close is detected on `Poll.is_finalized()`, never on
    the edit marker. Two observations because they are two distinct facts:
    the definition's `state` revising to closed, and the final option
    counts the snapshot reports (never voter identities — Discord supplies
    none here). The hydrated `Poll` folds the gateway's `results` block
    INTO the answers: `is_finalized()` is a method and each `PollAnswer`
    carries its own `vote_count`. A finalized tally of all zeros is a
    genuinely empty result — still a fact, still a snapshot; a unique
    `voter_count` is attached only when `multiple` is KNOWN false, since
    summing option votes across a multi-select is votes, not people."""
    poll = getattr(message, "poll", None)
    if (message is None or getattr(message, "guild", None) is None
            or poll is None):
        return ()
    finalized = getattr(poll, "is_finalized", None)
    try:
        closed = bool(finalized() if callable(finalized) else finalized)
    except Exception:
        log.exception("unreadable discord poll finalization flag, skipping")
        closed = False
    if not closed:
        return ()
    expires = getattr(poll, "expires_at", None)
    try:
        close_ms = int(expires.timestamp() * 1000) if expires is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        close_ms = None
    scope = _message_scope(message, account_key)
    counts = tuple(
        (ref, count) for ref, count in (
            (str(getattr(a, "id", "") or ""), getattr(a, "vote_count", None))
            for a in getattr(poll, "answers", None) or ())
        if ref and isinstance(count, int) and not isinstance(count, bool))
    voter_count = None
    if getattr(poll, "multiple", None) is False:
        total = getattr(poll, "total_votes", None)
        if isinstance(total, int) and not isinstance(total, bool):
            voter_count = total
    obs: list[Observation] = [
        Observation(
            object_kind="poll", scope=scope,
            object_ref=_poll_object(message.id), operation="revise",
            payload=PollPayload(state="closed", close_ms=close_ms),
            target=EditTarget(message_id=str(message.id)),
            actor_status="not_applicable", time_quality="unknown")]
    if counts:
        obs.append(Observation(
            object_kind="poll", scope=scope,
            object_ref=_poll_object(message.id), operation="snapshot",
            payload=PollPayload(
                option_counts=counts, voter_count=voter_count),
            target=EditTarget(message_id=str(message.id)),
            actor_status="not_applicable", time_quality="unknown",
            coverage=Coverage(covered="option_counts")))
    return tuple(obs)


def parse_discord_poll_vote(
    payload: Any, *, own_id: str, account_key: str, added: bool,
    room_channel_id: str | None = None,
) -> Observation | None:
    """`RawPollVoteActionEvent` → a one-answer `poll` add/remove delta.

    The voter is `user_id` (never anonymous on Discord); the poll is keyed
    by its parent message's snowflake. No source time — the event carries
    none. A DM vote (`guild_id` None) answers None: the admission boundary,
    not a parse accident."""
    try:
        guild_id = getattr(payload, "guild_id", None)
        if guild_id is None:
            return None
        user_id = str(getattr(payload, "user_id", "") or "")
        if not user_id or user_id == own_id:
            return None
        message_id = str(getattr(payload, "message_id", "") or "")
        answer_id = getattr(payload, "answer_id", None)
        if not message_id or answer_id is None:
            return None
        return Observation(
            object_kind="poll",
            scope=_event_scope(guild_id, getattr(payload, "channel_id", ""),
                               room_channel_id, account_key),
            object_ref=_poll_object(message_id),
            operation="add" if added else "remove",
            payload=PollPayload(voter_ref=user_id, option_ref=str(answer_id)),
            target=EditTarget(message_id=message_id),
            actor_id=user_id, actor_status="known", time_quality="unknown")
    except Exception:
        log.exception("unparseable discord poll vote, skipping")
        return None


def parse_discord_reaction_clear(
    payload: Any, *, account_key: str, emoji: str = "",
    room_channel_id: str | None = None,
) -> Observation | None:
    """`MESSAGE_REACTION_REMOVE_ALL`/`REMOVE_EMOJI` → `reaction/clear`.

    The covered scope is what the event itself names: the whole emoji set
    for remove-all (`covered_emojis=()` — empties all observed), exactly the
    one emoji for remove-emoji. The moderator who cleared is never named —
    `actor_status="not_supplied"`, and no per-user removals are invented."""
    try:
        guild_id = getattr(payload, "guild_id", None)
        message_id = str(getattr(payload, "message_id", "") or "")
        if guild_id is None or not message_id:
            return None
        return Observation(
            object_kind="reaction",
            scope=_event_scope(guild_id, getattr(payload, "channel_id", ""),
                               room_channel_id, account_key),
            object_ref=f"msg:{message_id}", operation="clear",
            payload=ReactionPayload(
                covered_emojis=(emoji,) if emoji else ()),
            target=EditTarget(message_id=message_id),
            actor_status="not_supplied", time_quality="unknown",
            coverage=Coverage(covered="reactions"))
    except Exception:
        log.exception("unparseable discord reaction clear, skipping")
        return None


def parse_discord_pin_message(
    message: Any, *, own_id: str, account_key: str,
) -> Observation | None:
    """A type-6 `CHANNEL_PINNED_MESSAGE` service message → `pin/add`.

    This is the REAL pin evidence: it names the pinned message
    (`message_reference.message_id`) and the pinner (`author`), unlike
    `CHANNEL_PINS_UPDATE`. The service message itself never becomes an
    utterance — the observation is the event."""
    mtype = getattr(message, "type", None)
    mtype = getattr(mtype, "value", mtype)
    if message is None or mtype != 6 or getattr(message, "guild", None) is None:
        return None
    if str(getattr(message.author, "id", "")) == own_id:
        return None
    ref = getattr(message, "reference", None)
    target_id = getattr(ref, "message_id", None) if ref is not None else None
    if not target_id:
        return None                       # a pin notice naming nothing
    return Observation(
        object_kind="pin", scope=_message_scope(message, account_key),
        object_ref=f"pin_set:{message.channel.id}", operation="add",
        payload=PinPayload(message=EditTarget(message_id=str(target_id))),
        target=EditTarget(message_id=str(target_id)),
        actor_id=str(message.author.id), actor_status="known",
        occurred_ms=_timestamp_ms(message.id), time_quality="upstream")


def parse_discord_pins_update(
    channel: Any, last_pin: Any, *, account_key: str,
) -> Observation | None:
    """`CHANNEL_PINS_UPDATE` → an INCOMPLETE pin-set marker.

    The event names only the channel and the latest pin's time — no pinned
    message and no actor. A partial snapshot records that the set changed
    with `targets`/`actor` as named gaps; the type-6 system message and any
    later read are the real target evidence. An empty `pins` merges nothing
    and can never claim the set emptied. The pin set is named for the
    channel the event names — a thread's pins are its own set, folded
    under the parent room's scope, never merged into the parent's."""
    guild = getattr(channel, "guild", None)
    if channel is None or guild is None:
        return None
    parent_id = getattr(channel, "parent_id", None)
    room_id = parent_id if parent_id is not None else channel.id
    return Observation(
        object_kind="pin",
        scope=SourceScope(
            platform="discord", account_key=account_key,
            conversation_id=f"{guild.id}/{room_id}"),
        object_ref=f"pin_set:{channel.id}", operation="snapshot",
        payload=PinPayload(pins=()),
        actor_status="not_supplied", time_quality="unknown",
        coverage=Coverage(
            complete=False, covered="pin_set",
            gaps=("targets", "actor"),
            reason="CHANNEL_PINS_UPDATE names no message or actor"))


def parse_discord_message_metadata(
    message: Any, *, account_key: str,
) -> Observation | None:
    """A forward (`message_snapshots` + `message_reference`) →
    `message_metadata/create` with the origin triple, riding the message's
    own envelope. A snapshot with no resolvable reference stays only the
    `[forwarded message]` marker — no synthetic origin is fabricated."""
    snapshots = getattr(message, "message_snapshots", None) or ()
    if (message is None or not snapshots
            or getattr(message, "guild", None) is None):
        return None
    ref = getattr(message, "reference", None)
    ref_id = getattr(ref, "message_id", None) if ref is not None else None
    if not ref_id:
        return None
    origin = "discord:{}/{}".format(
        getattr(ref, "guild_id", None) or "",
        getattr(ref, "channel_id", None) or "",
    ).rstrip("/") + f"/{ref_id}"
    return Observation(
        object_kind="message_metadata",
        scope=_message_scope(message, account_key),
        object_ref=f"msg:{message.id}", operation="create",
        payload=MessageMetadataPayload(forward_origin=origin),
        target=EditTarget(message_id=str(message.id)),
        actor_id=str(message.author.id), actor_status="known",
        occurred_ms=_timestamp_ms(message.id), time_quality="upstream")


def parse_discord_thread_create(
    thread: Any, *, account_key: str,
) -> Observation | None:
    """`THREAD_CREATE` → `room/create` for the thread — the lifecycle event,
    not a roster read. `owner_id` is the creator (the `d.member.user_id`
    Discord also sends is the BOT'S join, a different fact)."""
    guild = getattr(thread, "guild", None)
    if thread is None or guild is None:
        return None
    owner = getattr(thread, "owner_id", None)
    fields: dict[str, Any] = {
        "name": getattr(thread, "name", None),
        "owner_id": str(owner) if owner is not None else None,
        "archived": getattr(thread, "archived", None),
        "locked": getattr(thread, "locked", None),
        "auto_archive_duration": getattr(thread, "auto_archive_duration", None),
        "rate_limit_per_user": getattr(thread, "slowmode_delay", None),
        "type": getattr(getattr(thread, "type", None), "value",
                        getattr(thread, "type", None)),
    }
    parent_id = getattr(thread, "parent_id", None)
    fields["parent_id"] = (
        str(parent_id) if parent_id is not None else None)
    return Observation(
        object_kind="room",
        scope=SourceScope(
            platform="discord", account_key=account_key,
            conversation_id=f"{guild.id}/{parent_id or thread.id}"),
        object_ref=f"thread:{thread.id}", operation="create",
        payload=RoomPayload(
            fields={k: v for k, v in fields.items() if v is not None}),
        actor_id=str(owner) if owner is not None else "",
        actor_status="known" if owner is not None else "not_supplied",
        occurred_ms=_timestamp_ms(thread.id), time_quality="upstream")


def parse_discord_thread_update(
    data: Any, *, account_key: str,
) -> Observation | None:
    """`RawThreadUpdateEvent.data` → `room/snapshot` of the AFTER state.

    Discord carries only the AFTER object — "what changed" is a diff
    against captured state, never a field of the event — so this is a
    snapshot of the covered field vocabulary, not an invented delta."""
    try:
        guild_id = str(data.get("guild_id") or "")
        thread_id = str(data.get("id") or "")
        if not guild_id or not thread_id:
            return None
        meta = data.get("thread_metadata") or {}
        fields = {
            "name": data.get("name"),
            "parent_id": (str(data["parent_id"])
                          if data.get("parent_id") is not None else None),
            "archived": meta.get("archived"),
            "locked": meta.get("locked"),
            "auto_archive_duration": meta.get("auto_archive_duration"),
            "rate_limit_per_user": data.get("rate_limit_per_user"),
        }
        return Observation(
            object_kind="room",
            scope=SourceScope(
                platform="discord", account_key=account_key,
                conversation_id=(
                    f"{guild_id}/{data.get('parent_id') or thread_id}")),
            object_ref=f"thread:{thread_id}", operation="snapshot",
            payload=RoomPayload(
                fields={k: v for k, v in fields.items() if v is not None}),
            actor_status="not_supplied", time_quality="unknown",
            coverage=Coverage(covered="fields"))
    except Exception:
        log.exception("unparseable discord thread update, skipping")
        return None


def parse_discord_thread_delete(
    raw: Any, *, account_key: str,
) -> Observation | None:
    """`RawThreadDeleteEvent` → `room/withdraw`. The event names no actor —
    a deletion never invents one, and prior messages keep their thread tag."""
    try:
        guild_id = str(getattr(raw, "guild_id", "") or "")
        thread_id = str(getattr(raw, "thread_id", "") or "")
        if not guild_id or not thread_id:
            return None
        return Observation(
            object_kind="room",
            scope=SourceScope(
                platform="discord", account_key=account_key,
                conversation_id=(
                    f"{guild_id}/{getattr(raw, 'parent_id', None) or thread_id}")),
            object_ref=f"thread:{thread_id}", operation="withdraw",
            payload=RoomPayload(fields={}),
            actor_status="not_supplied", time_quality="unknown")
    except Exception:
        log.exception("unparseable discord thread delete, skipping")
        return None


def parse_discord_channel_update(
    channel: Any, *, account_key: str,
) -> Observation | None:
    """`guild_channel_update`'s AFTER channel → `room/snapshot` of the
    covered channel fields — the AFTER object, never a guessed delta."""
    guild = getattr(channel, "guild", None)
    if channel is None or guild is None:
        return None
    overwrites = {}
    for target, ow in (getattr(channel, "permission_overwrites", None)
                       or {}).items():
        try:
            allow, deny = ow.pair()
        except (AttributeError, TypeError, ValueError):
            continue
        overwrites[str(getattr(target, "id", target))] = {
            "allow": allow.value, "deny": deny.value}
    fields = {
        "name": getattr(channel, "name", None),
        "topic": getattr(channel, "topic", None),
        "position": getattr(channel, "position", None),
        "parent_id": (str(getattr(channel, "parent_id", None) or "")
                      or None),
        "nsfw": getattr(channel, "nsfw", None),
        "rate_limit_per_user": getattr(channel, "slowmode_delay", None),
        "type": getattr(getattr(channel, "type", None), "value",
                        getattr(channel, "type", None)),
        "permission_overwrites": overwrites,
    }
    return Observation(
        object_kind="room",
        scope=SourceScope(
            platform="discord", account_key=account_key,
            conversation_id=f"{guild.id}/{channel.id}"),
        object_ref=f"channel:{channel.id}", operation="snapshot",
        payload=RoomPayload(
            fields={k: v for k, v in fields.items() if v is not None}),
        actor_status="not_supplied", time_quality="unknown",
        coverage=Coverage(covered="fields"))


def parse_discord_role_update(
    role: Any, *, account_key: str,
) -> Observation | None:
    """`guild_role_update`'s AFTER role → `room/snapshot` at GUILD scope —
    a role belongs to the guild, not to any one channel's room."""
    guild = getattr(role, "guild", None)
    if role is None or guild is None:
        return None
    permissions = getattr(getattr(role, "permissions", None), "value", None)
    color = getattr(getattr(role, "color", None), "value", None)
    fields = {
        "name": getattr(role, "name", None),
        "permissions": permissions,
        "position": getattr(role, "position", None),
        "color": color,
        "hoist": getattr(role, "hoist", None),
        "mentionable": getattr(role, "mentionable", None),
        "managed": getattr(role, "managed", None),
    }
    return Observation(
        object_kind="room",
        scope=SourceScope(
            platform="discord", account_key=account_key,
            conversation_id=str(guild.id)),
        object_ref=f"role:{role.id}", operation="snapshot",
        payload=RoomPayload(
            fields={k: v for k, v in fields.items() if v is not None}),
        actor_status="not_supplied", time_quality="unknown",
        coverage=Coverage(covered="fields"))


def parse_discord_member_add(
    member: Any, *, account_key: str,
) -> Observation | None:
    """`GUILD_MEMBER_ADD` → `membership/add` on the GUILD member set.

    The object is the guild's member collection (`guild:{id}`), which the
    design deliberately keeps distinct from any channel's audience — a
    guild roster is never channel audience."""
    guild = getattr(member, "guild", None)
    if member is None or guild is None:
        return None
    user_id = str(getattr(member, "id", "") or "")
    if not user_id:
        return None
    roles = ",".join(str(getattr(r, "id", r)) for r in
                     getattr(member, "roles", None) or ())
    joined = getattr(member, "joined_at", None)
    try:
        occurred = int(joined.timestamp() * 1000) if joined is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        occurred = None
    return Observation(
        object_kind="membership",
        scope=SourceScope(
            platform="discord", account_key=account_key,
            conversation_id=str(guild.id)),
        object_ref=f"guild:{guild.id}", operation="add",
        payload=MembershipPayload(role=roles, state="member"),
        subject_ref=user_id,
        actor_status="not_supplied",
        occurred_ms=occurred,
        time_quality="upstream" if occurred is not None else "unknown")


def parse_discord_member_remove(
    raw: Any, *, account_key: str,
) -> Observation | None:
    """`RawMemberRemoveEvent` → `membership/remove` on the guild set. The
    event names no actor and a removal establishes no voluntary departure —
    `state="left"` records only that the member is gone."""
    try:
        guild_id = str(getattr(raw, "guild_id", "") or "")
        user = getattr(raw, "user", None)
        user_id = str(getattr(user, "id", "") or "")
        if not guild_id or not user_id:
            return None
        return Observation(
            object_kind="membership",
            scope=SourceScope(
                platform="discord", account_key=account_key,
                conversation_id=guild_id),
            object_ref=f"guild:{guild_id}", operation="remove",
            payload=MembershipPayload(state="left"),
            subject_ref=user_id,
            actor_status="not_supplied", time_quality="unknown")
    except Exception:
        log.exception("unparseable discord member remove, skipping")
        return None


def parse_discord_member_update(
    member: Any, *, account_key: str,
) -> Observation | None:
    """`guild_member_update`'s AFTER member → `membership/revise` of that
    subject's entry on the guild set (roles as reported)."""
    guild = getattr(member, "guild", None)
    if member is None or guild is None:
        return None
    user_id = str(getattr(member, "id", "") or "")
    if not user_id:
        return None
    roles = ",".join(str(getattr(r, "id", r)) for r in
                     getattr(member, "roles", None) or ())
    return Observation(
        object_kind="membership",
        scope=SourceScope(
            platform="discord", account_key=account_key,
            conversation_id=str(guild.id)),
        object_ref=f"guild:{guild.id}", operation="revise",
        payload=MembershipPayload(role=roles, state="member"),
        subject_ref=user_id,
        actor_status="not_supplied", time_quality="unknown")


def _ev_ms(value: Any) -> int | None:
    """A scheduled-event datetime as epoch ms, or None."""
    try:
        return int(value.timestamp() * 1000) if value is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


_SCHEDULED_EVENT_FIELDS = (
    "name", "description", "entity_type", "status", "location_name",
    "channel_name",
)


def _scheduled_event_fields(ev: Any) -> dict[str, Any]:
    """A `ScheduledEvent`'s readable fields as the typed room field dict —
    user-authored names/times/status only. The entity URL (an external join
    link) and the cover image are withheld, recorded as coverage gaps by the
    caller."""
    fields: dict[str, Any] = {}
    name = getattr(ev, "name", None)
    if isinstance(name, str) and name:
        fields["name"] = name
    desc = getattr(ev, "description", None)
    if isinstance(desc, str) and desc:
        fields["description"] = desc
    et = getattr(ev, "entity_type", None)
    et_name = getattr(et, "name", None) or (
        str(et) if et is not None else "")
    if et_name:
        fields["entity_type"] = et_name
    status = getattr(ev, "status", None)
    st_name = getattr(status, "name", None) or (
        str(status) if status is not None else "")
    if st_name:
        fields["status"] = st_name
    start = _ev_ms(getattr(ev, "start_time", None))
    if start is not None:
        fields["start_ms"] = start
    end = _ev_ms(getattr(ev, "end_time", None))
    if end is not None:
        fields["end_ms"] = end
    meta = getattr(ev, "entity_metadata", None)
    loc = getattr(meta, "location", None) if meta is not None else None
    if isinstance(loc, str) and loc:
        fields["location_name"] = loc
    channel = getattr(ev, "channel", None)
    ch_name = getattr(channel, "name", None) if channel is not None else None
    if isinstance(ch_name, str) and ch_name:
        fields["channel_name"] = ch_name
    return fields


def parse_discord_scheduled_event(
    ev: Any, *, operation: str, account_key: str,
) -> Observation | None:
    """A guild scheduled event's create/update/delete dispatch → an
    `artifact/event` observation on the GUILD scope (its configured rooms
    bind downstream; a guild event is never a channel's audience).

    create carries the full reported field set, update only the changed
    fields (discord.py hands `before`/`after`; callers pass `after` and the
    diff is computed against `before` when supplied), delete is `withdraw`.
    The event URL/cover stay withheld — recorded as coverage gaps."""
    guild = getattr(ev, "guild", None)
    ev_id = str(getattr(ev, "id", "") or "")
    if ev is None or guild is None or not ev_id:
        return None
    object_ref = f"event:{ev_id}"
    if operation == "withdraw":
        metadata: dict[str, Any] = {"name": str(getattr(ev, "name", "") or "")}
        op = "withdraw"
    else:
        fields = _scheduled_event_fields(ev)
        metadata = {
            "name": fields.pop("name", ""),
            "status": str(fields.pop("status", "") or ""),
            "entity_type": str(fields.pop("entity_type", "") or ""),
            "description": str(fields.pop("description", "") or ""),
            "location_name": str(fields.pop("location_name", "") or ""),
            "channel_name": str(fields.pop("channel_name", "") or ""),
        }
        if fields.get("start_ms") is not None:
            metadata["start_time_s"] = fields["start_ms"] // 1000
        if fields.get("end_ms") is not None:
            metadata["end_time_s"] = fields["end_ms"] // 1000
        metadata = {k: v for k, v in metadata.items() if v != "" and v is not None}
        op = operation
    return Observation(
        object_kind="artifact",
        scope=SourceScope(
            platform="discord", account_key=account_key,
            conversation_id=str(guild.id)),
        object_ref=object_ref, operation=op,
        payload=ArtifactPayload(
            artifact_kind="event", metadata=metadata, custody="declared"),
        actor_status="not_supplied", time_quality="unknown",
        coverage=Coverage(
            complete=False, covered="fields",
            gaps=("join_url", "image"),
            reason="external join URL and cover withheld by policy"))


def parse_discord_scheduled_event_user(
    ev: Any, user: Any, *, added: bool, account_key: str,
) -> Observation | None:
    """`scheduled_event_user_add`/`remove` → an `artifact/event` revise
    carrying one responder's RSVP under `response` — folded per-responder,
    never a last-response scalar. Discord has one RSVP vocabulary
    (`interested`); a remove is a withdrawn response."""
    guild = getattr(ev, "guild", None)
    ev_id = str(getattr(ev, "id", "") or "")
    user_id = str(getattr(user, "id", "") or "")
    if ev is None or guild is None or not ev_id or not user_id:
        return None
    return Observation(
        object_kind="artifact",
        scope=SourceScope(
            platform="discord", account_key=account_key,
            conversation_id=str(guild.id)),
        object_ref=f"event:{ev_id}", operation="revise",
        payload=ArtifactPayload(
            artifact_kind="event",
            metadata={"response": "interested" if added else ""},
            custody="declared"),
        actor_id=user_id, actor_status="known",
        time_quality="unknown", coverage=Coverage(covered="response"))
