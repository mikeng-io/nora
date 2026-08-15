"""`nora.platform.discord.parser.parse_discord_message` — a discord.py-shaped
`Message` -> `NormalizedMessage`, mirroring `tests/test_signal_parser.py`'s
coverage depth for the behaviors that carry over."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from discord import StickerFormatType

from nora.platform.discord.parser import parse_discord_message
from nora.platform.discord.snowflake import DISCORD_EPOCH_MS

OWN_ID = "999"

# id = 12345 << 22, so the snowflake timestamp math is exact and easy to check
# by hand: (id >> 22) + DISCORD_EPOCH_MS == 12345 + DISCORD_EPOCH_MS.
MESSAGE_ID = 12345 << 22
EXPECTED_TIMESTAMP_MS = 12345 + DISCORD_EPOCH_MS


@dataclass
class FakeUser:
    id: int
    display_name: str = "someone"


@dataclass
class FakeGuild:
    id: int


@dataclass
class FakeChannel:
    id: int


@dataclass
class FakeAttachment:
    id: int
    content_type: str | None = None
    filename: str = ""
    size: int = 0
    voice: bool = False

    def is_voice_message(self) -> bool:
        return self.voice


@dataclass
class _FakeSticker:
    id: int = 900
    name: str = "wumpus cheers"
    format: object = None  # set per test to a StickerFormatType


def _sticker(fmt) -> _FakeSticker:
    return _FakeSticker(format=fmt)


@dataclass
class FakeResolved:
    id: int
    author: FakeUser
    content: str


@dataclass
class FakeReference:
    resolved: object = None


@dataclass
class FakeMessage:
    id: int
    author: FakeUser
    guild: FakeGuild | None
    channel: FakeChannel
    content: str = ""
    mentions: list = field(default_factory=list)
    attachments: list = field(default_factory=list)
    reference: object = None
    # The content-less turns. Defaulted empty so every existing construction is
    # untouched, and present at all because the REAL `discord.Message` carries
    # them — a double without them could not express the message shapes this
    # parser has to survive.
    stickers: list = field(default_factory=list)
    embeds: list = field(default_factory=list)
    poll: object = None
    message_snapshots: list = field(default_factory=list)


def _message(**overrides) -> FakeMessage:
    defaults = {
        "id": MESSAGE_ID,
        "author": FakeUser(id=42),
        "guild": FakeGuild(id=1),
        "channel": FakeChannel(id=2),
        "content": "hello",
    }
    defaults.update(overrides)
    return FakeMessage(**defaults)


def test_parses_a_plain_text_message_in_a_guild_channel():
    msg = _message()

    result = parse_discord_message(msg, own_id=OWN_ID)

    assert result is not None
    assert result.platform == "discord"
    assert result.conversation_id == "1/2"
    assert result.conversation_type == "group"
    assert result.sender_id == "42"
    assert result.sender_name == "someone"
    assert result.timestamp == EXPECTED_TIMESTAMP_MS
    assert result.body == "hello"
    # The platform's OWN handle for this message, as a string. Discord's
    # reaction endpoint accepts nothing else — `(author, timestamp)` cannot
    # reconstruct a snowflake, because `snowflake.timestamp_ms` discards the
    # low 22 bits — so dropping this here makes every Discord reaction a
    # silent no-op with nothing else in the system able to notice.
    assert result.message_id == "51778682880"


def test_dm_messages_are_dropped():
    msg = _message(guild=None)

    assert parse_discord_message(msg, own_id=OWN_ID) is None


def test_own_messages_are_not_ingested():
    msg = _message(author=FakeUser(id=999))

    assert parse_discord_message(msg, own_id=OWN_ID) is None


def test_mentions_are_collected_as_ids():
    msg = _message(mentions=[FakeUser(id=7), FakeUser(id=8)])

    result = parse_discord_message(msg, own_id=OWN_ID)

    assert result.mentions == frozenset({"7", "8"})


def test_an_attachment_becomes_a_placeholder_and_a_media_ref():
    msg = _message(
        content="",
        attachments=[FakeAttachment(id=55, content_type="image/png", filename="a.png", size=100)],
    )

    result = parse_discord_message(msg, own_id=OWN_ID)

    assert result.body == "[image]"
    assert len(result.media) == 1
    assert result.media[0].platform_id == "55"
    assert result.media[0].media_type == "image/png"


def test_text_and_attachment_both_appear_in_the_body():
    msg = _message(
        content="check this out",
        attachments=[FakeAttachment(id=1, content_type="image/png")],
    )

    result = parse_discord_message(msg, own_id=OWN_ID)

    assert result.body == "check this out [image]"


def test_empty_message_with_no_text_and_no_attachments_is_dropped():
    msg = _message(content="")

    assert parse_discord_message(msg, own_id=OWN_ID) is None


def test_a_reply_with_a_resolved_reference_produces_a_quote():
    resolved = FakeResolved(id=999999, author=FakeUser(id=7), content="original text")
    msg = _message(reference=FakeReference(resolved=resolved))

    result = parse_discord_message(msg, own_id=OWN_ID)

    assert result.quote is not None
    assert result.quote.author_id == "7"
    assert result.quote.text == "original text"


def test_an_unresolved_reference_produces_no_quote_and_does_not_crash():
    msg = _message(reference=FakeReference(resolved=None))

    result = parse_discord_message(msg, own_id=OWN_ID)

    assert result is not None
    assert result.quote is None


def test_unparseable_input_is_skipped_not_raised():
    assert parse_discord_message(None, own_id=OWN_ID) is None


# ---------------------------------------------------------------------------
# turns that carry no text and no attachment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"stickers": [_sticker(StickerFormatType.png)]}, "[sticker]"),
        ({"poll": object()}, "[poll]"),
        ({"message_snapshots": [object()]}, "[forwarded message]"),
        ({"embeds": [object()]}, "[embed]"),
    ],
)
def test_a_turn_with_no_text_is_rendered_not_dropped(kwargs, expected):
    """The defect this closes, found by review 2026-08-14.

    `_body_parts` was a copy of Signal's MINUS its sticker branch — and that
    branch exists on the Signal side precisely because dropping a sticker made
    a sticker-only message vanish and the sender watched Nora ignore something
    they had clearly sent. Discord has more ways to send a text-less turn than
    Signal does, so the same defect was reintroduced here and was wider.

    The cost is not one ignored message: a dropped turn never reaches the audit
    store, so Nora later reasons over a transcript with a hole she cannot see.
    """
    result = parse_discord_message(_message(content="", **kwargs), own_id=OWN_ID)

    assert result is not None, "a text-less turn must still become a message"
    assert result.body == expected


def test_a_turn_with_genuinely_nothing_is_still_dropped():
    """The placeholders must not become a way to invent a turn. A message with
    no text, no attachment, no sticker, no poll, no forward and no embed is
    nothing to render, and rendering it would add a line the room never saw."""
    assert parse_discord_message(_message(content=""), own_id=OWN_ID) is None


def test_a_captioned_sticker_keeps_both_halves():
    """OVERTURNS the previous behaviour deliberately (spec §3.2). This test
    used to assert the body was `"haha"` alone — the sticker vanished — on the
    premise that a placeholder is 'the fallback for an empty body, not an
    annotation on every message'. That premise was right while a sticker was an
    unreadable blank. Once it is understood it is not an annotation, it is
    content, and the message means something different without it. A caption
    plus a booster is the exact case this feature was asked for."""
    result = parse_discord_message(
        _message(content="haha", stickers=[_sticker(StickerFormatType.png)]),
        own_id=OWN_ID,
    )

    assert result is not None
    assert result.body == "haha [sticker]"
    assert len(result.media) == 1
    assert result.media[0].sticker is True


def test_a_lottie_sticker_is_carried_as_json_not_as_an_image():
    """Lottie is a JSON animation, not a picture. Recording it as `image/*`
    would send JSON to a vision model; recording it as `""` would leave the
    stored row unable to say what the bytes are."""
    result = parse_discord_message(
        _message(content="", stickers=[_sticker(StickerFormatType.lottie)]),
        own_id=OWN_ID,
    )

    assert result is not None
    assert result.media[0].media_type == "application/json"
    assert result.body == "[sticker]"


def test_a_sticker_name_never_reaches_the_body():
    """A guild sticker's name is unbounded text set by a server admin — the
    same object `nora/domain/media.py` stopped rendering when it removed
    sender-supplied filenames, wearing a friendlier hat."""
    result = parse_discord_message(
        _message(content="", stickers=[_FakeSticker(name="call +85212345678 now",
                                                    format=StickerFormatType.png)]),
        own_id=OWN_ID,
    )

    assert result is not None
    assert "85212345678" not in result.body
    assert result.body == "[sticker]"
