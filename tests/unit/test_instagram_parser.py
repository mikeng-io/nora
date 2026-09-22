"""`nora.platform.instagram.parser.parse_event` against the wire shapes
design/instagram-adoption/03-transport-contract.md pins.

The event bodies here are the contract's own, field for field — including the
five inbound media shapes and the `kind` discriminator — so a drift between
this suite and the bridge is a contract failure rather than a judgment call.
"""

from __future__ import annotations

from nora.platform.instagram.parser import parse_event

ROOM = "1009543911792316"
SENDER_FBID = "110966695387753"
NORA_FBID = "14963523149149302"


def _event(**over) -> dict:
    """One inbound `kind: "message"` event, exactly the shape `03` shows."""
    base = {
        "kind": "message",
        "room_id": ROOM,
        "message_id": "mid.$gAASG4nvQLWim9MCLrWgw3tChgAAq",
        "sender": {"fbid": SENDER_FBID, "igid": "221731317", "username": "example_handle"},
        "from_me": False,
        "timestamp_ms": 1789985625166,
        "text": "hello",
        "mentions": [],
        "reply_to": None,
        "media": None,
    }
    base.update(over)
    return base


# --- what is ingested --------------------------------------------------------


def test_a_plain_group_message_normalizes():
    msg = parse_event(_event())

    assert msg is not None
    assert msg.platform == "instagram"
    assert msg.conversation_id == ROOM
    assert msg.conversation_type == "group"
    assert msg.sender_id == SENDER_FBID
    assert msg.timestamp == 1789985625166
    assert msg.body == "hello"
    assert msg.message_id == "mid.$gAASG4nvQLWim9MCLrWgw3tChgAAq"


def test_the_sender_id_is_the_FBID_never_the_igid_or_the_username():
    """02's opening failure, asserted directly: the igid is the id a reader
    reaches for and it matches nothing a mention carries."""
    msg = parse_event(_event())

    assert msg is not None
    assert msg.sender_id == SENDER_FBID
    assert msg.sender_id != "221731317"
    assert msg.sender_id != "example_handle"


def test_the_username_rides_as_a_DISPLAY_name_only():
    msg = parse_event(_event())

    assert msg is not None
    assert msg.sender_name == "example_handle"
    # And it is nowhere any matching happens.
    assert "example_handle" not in msg.mentions
    assert msg.sender_id != msg.sender_name


def test_the_platform_message_id_is_carried_because_react_addresses_by_it():
    msg = parse_event(_event())

    assert msg is not None
    assert msg.message_id.startswith("mid.$")


# --- what is dropped ---------------------------------------------------------


def test_a_non_message_kind_is_dropped():
    """`kind`, not WhatsApp's `type`. The parser stays total on its own even
    though the listener swallows two of these."""
    for kind in ("heartbeat", "dropped", "reaction", "edit", "unsend", "typing"):
        assert parse_event(_event(kind=kind)) is None


def test_our_own_echo_is_dropped():
    """R-12's first pass. `from_me` is STRUCTURAL here — the bridge compares
    against `GetOwnFBID()` — so the adapter never infers its own echo from a
    config value."""
    assert parse_event(_event(from_me=True)) is None


def test_a_turn_with_neither_text_nor_media_is_dropped():
    """Which is also how the system deltas leave without a special case: an
    `ADD_PARTICIPANT_XMAT` arrives as an ordinary message carrying nothing."""
    assert parse_event(_event(text="", media=None, content_type="ADD_PARTICIPANT_XMAT")) is None


def test_an_event_with_no_room_id_is_dropped():
    assert parse_event(_event(room_id="")) is None
    assert parse_event(_event(room_id=None)) is None


def test_a_malformed_event_is_skipped_rather_than_raised():
    """A bad turn must never kill the stream behind it."""
    assert parse_event({"kind": "message", "room_id": ROOM, "sender": "not-a-dict"}) is None
    assert parse_event({}) is None


# --- mentions ----------------------------------------------------------------


def test_a_mention_of_nora_lands_as_her_fbid():
    msg = parse_event(_event(
        text="@not_____nora hi",
        mentions=[{"fbid": NORA_FBID, "offset": 0, "length": 13, "range_type": "PROFILE"}],
    ))

    assert msg is not None
    assert msg.mentions == frozenset({NORA_FBID})


def test_a_mention_of_SOMEBODY_ELSE_is_carried_and_is_not_nora():
    """Measured in 02: two messages seconds apart in one room, one tagging
    Nora and one tagging another member, discriminated by the fbid alone."""
    msg = parse_event(_event(
        text="@example_handle GH",
        mentions=[{"fbid": SENDER_FBID, "offset": 0, "length": 15, "range_type": "PROFILE"}],
    ))

    assert msg is not None
    assert msg.mentions == frozenset({SENDER_FBID})
    assert NORA_FBID not in msg.mentions


def test_an_EVERYONE_mention_does_not_put_a_room_id_in_the_person_set():
    """A `THREAD` range's `fbid` is the CHAT's id. Admitting it would make a
    room id a matchable person-shaped value."""
    msg = parse_event(_event(
        text="@everyone",
        mentions=[{"fbid": ROOM, "offset": 0, "length": 9, "range_type": "THREAD"}],
    ))

    assert msg is not None
    assert msg.mentions == frozenset()


def test_a_mention_with_no_range_type_is_still_a_mention():
    """The field is `omitempty`; refusing an absent one would drop real tags."""
    msg = parse_event(_event(mentions=[{"fbid": NORA_FBID, "offset": 0, "length": 13}]))

    assert msg is not None
    assert msg.mentions == frozenset({NORA_FBID})


def test_mentions_that_are_not_a_list_degrade_to_empty():
    msg = parse_event(_event(mentions=None))

    assert msg is not None
    assert msg.mentions == frozenset()


# --- replies -----------------------------------------------------------------


def test_a_reply_carries_its_parents_author_and_timestamp():
    msg = parse_event(_event(reply_to={
        "message_id": "mid.$parent", "sender_fbid": NORA_FBID,
        "timestamp_ms": 1789985600000, "text": "the parent",
    }))

    assert msg is not None
    assert msg.quote is not None
    assert msg.quote.author_id == NORA_FBID
    assert msg.quote.text == "the parent"
    assert msg.quote.quoted_ts == 1789985600000


def test_a_reply_whose_parent_body_is_gone_keeps_the_RELATION():
    """`replyOf` reports the relation even when the delta carried only the id
    — the same honest degradation WhatsApp takes on an aged-out quote. The
    reply survives; the parent pointer does not, and a `by_ts` lookup misses
    on 0 rather than rendering a wrong parent."""
    msg = parse_event(_event(reply_to={"message_id": "mid.$parent"}))

    assert msg is not None
    assert msg.quote is not None
    assert msg.quote.quoted_ts == 0
    assert msg.quote.text == ""


def test_no_reply_is_no_quote():
    msg = parse_event(_event(reply_to=None))

    assert msg is not None
    assert msg.quote is None


# --- media: the five inbound shapes ------------------------------------------


def test_an_image_renders_a_placeholder_after_the_text():
    msg = parse_event(_event(text="look", media={
        "kind": "image", "url": "https://cdn.example/x.jpg",
        "attachment_fbid": "970288316113989", "width": 720, "height": 960,
    }))

    assert msg is not None
    assert msg.body == "look [image]"
    assert len(msg.media) == 1


def test_a_media_only_turn_survives_as_a_bare_placeholder():
    msg = parse_event(_event(text="", media={"kind": "video", "url": "https://cdn/x"}))

    assert msg is not None
    assert msg.body == "[video]"


def test_an_animated_sticker_is_labelled_a_STICKER_not_an_image():
    """`animated` is the GIF/sticker shape, and `nora.domain.media` already
    has a `sticker` axis for "an image that is pack art rather than somebody's
    photograph"."""
    msg = parse_event(_event(text="", media={"kind": "animated", "url": "https://cdn/x.mp4"}))

    assert msg is not None
    assert msg.body == "[sticker]"
    assert msg.media[0].sticker is True


def test_audio_is_NOT_claimed_to_be_a_voice_note():
    """Instagram does not distinguish a shared song from somebody speaking,
    and calling a song a voice note puts words in a member's mouth."""
    msg = parse_event(_event(text="", media={"kind": "audio", "url": "https://cdn/x.m4a"}))

    assert msg is not None
    assert msg.body == "[audio]"
    assert msg.media[0].voice_note is False


def test_view_once_media_says_something_arrived_and_claims_nothing_about_it():
    """`RAVEN_*` with `attachment: null` carries no kind and no bytes — v1
    does not fetch, because on Instagram fetching IS viewing and is visible to
    the sender. So the turn must still say a thing arrived, and must not name
    a kind it never saw."""
    msg = parse_event(_event(text="", media={
        "kind": "one_time", "url": None, "one_time": True, "view_mode": 0,
    }))

    assert msg is not None
    assert msg.body == "[file]"
    assert msg.media[0].media_type == ""


def test_media_carries_the_cdn_url_as_its_fetch_token():
    """The live defect this replaced: recording no token made every image Miles
    sent a bare `[image]` with no handle, and Nora answered — correctly — that
    the image had no code she could read. The bridge has no media-fetch route,
    which is true and is not the same as there being nothing to fetch:
    `media.url` is directly fetchable and `InstagramMediaSource` resolves it.
    `attachment_fbid` is Instagram's own id for the stored attachment."""
    msg = parse_event(_event(media={
        "kind": "image",
        "url": "https://scontent.fhkg3-1.fna.fbcdn.net/v/t51.2885-15/x.jpg?oh=00_AF",
        "attachment_fbid": "970288316113989",
    }))

    assert msg is not None
    url = "https://scontent.fhkg3-1.fna.fbcdn.net/v/t51.2885-15/x.jpg?oh=00_AF"
    assert msg.media[0].fetch_token == f"attachment:{url}"
    assert msg.media[0].platform_id == "970288316113989"


def test_one_time_media_gets_no_token_because_it_carries_no_url():
    """`RAVEN_*` arrives with `attachment: null` and the bridge emits
    `url: null` for it. Fetching view-once media IS viewing it and is visible
    to the sender, so the ref must stay unfetchable — this is the decision,
    not a gap to close later."""
    msg = parse_event(_event(text="", media={
        "kind": "one_time", "url": None, "one_time": True, "view_mode": 0,
    }))

    assert msg is not None
    assert msg.media[0].fetch_token == ""
    assert msg.media[0].platform_id == ""


def test_an_animated_sticker_is_fetchable_by_the_same_token_shape():
    """Unlike a Discord sticker, whose url is DERIVED from an id, an Instagram
    animated image is a url like any other attachment — so there is no second
    token shape and no branch in the fetcher."""
    msg = parse_event(_event(text="", media={
        "kind": "animated", "url": "https://external.fhkg3-1.fna.fbcdn.net/x.mp4",
    }))

    assert msg is not None
    assert msg.media[0].sticker is True
    url = "https://external.fhkg3-1.fna.fbcdn.net/x.mp4"
    assert msg.media[0].fetch_token == f"attachment:{url}"


def test_a_media_object_with_no_kind_is_not_invented_into_one():
    msg = parse_event(_event(text="hi", media={"url": "https://cdn/x"}))

    assert msg is not None
    assert msg.body == "hi"
    assert msg.media == ()


# --- conversation_type: the field the wire cannot ground ---------------------


def test_the_bridges_is_group_field_is_HONOURED_when_present():
    """The wire carries no group/DM discriminator today. When it grows one,
    this parser already reads it — there is no second place to change."""
    assert parse_event(_event(is_group=False)).conversation_type == "dm"
    assert parse_event(_event(is_group=True)).conversation_type == "group"


# --- shared reels and posts (the bridge's `shared` object) --------------------
#
# The failure these cover is the invisible one. `MessageContentXMA` had no case
# in the bridge's content switch, so a shared reel produced no text and no
# media, and `parse_event` dropped the turn for having neither. Nora did not
# fail to READ the reel — she never learned one had been posted, and nothing
# anywhere recorded that anything had happened.
#
# The rendered line is the FREE depth of design/instagram-adoption/13: caption
# and author are already in the delta, so a room where people share reels all
# day costs nothing extra to follow. Only a direct question about one post
# reaches for the thumbnail or the video.


def test_a_shared_reel_is_no_longer_dropped():
    """The whole point. Before `shared`, this event had no text and no media
    and left through the "neither text nor media" line as if nothing had been
    said."""
    msg = parse_event(_event(
        text="",
        shared={
            "author": "someaccount",
            "caption": "the caption as its author wrote it",
            "target_url": "https://www.instagram.com/reel/ABC123/",
        },
    ))

    assert msg is not None, "a shared reel with no covering text was dropped"
    assert msg.body == "[shared post by someaccount — the caption as its author wrote it]"


def test_the_sharers_words_come_before_the_shared_post():
    """In the order they happened: somebody typed something, about a post,
    which carries a thumbnail. The placeholder run must stay TRAILING, because
    `body_with_handles` rewrites it in place once custody has the bytes."""
    url = "https://cdn.example/thumb.jpg"
    msg = parse_event(_event(
        text="look at this",
        shared={"author": "someaccount", "caption": "the caption"},
        media={"kind": "image", "url": url},
    ))

    assert msg is not None
    assert msg.body == "look at this [shared post by someaccount — the caption] [image]"
    # The thumbnail is fetchable, which is what makes the middle depth possible
    # at all: she can say what the reel looks like without fetching the video.
    assert msg.media[0].fetch_token == f"attachment:{url}"


def test_the_shared_author_is_not_the_sender():
    """`sender` shared it; `shared.author` posted it. They are different people
    and the body has to keep them apart — a line that blurred them would
    attribute a stranger's reel to a member of the room."""
    msg = parse_event(_event(
        text="",
        shared={"author": "a_stranger", "caption": "not mine"},
    ))

    assert msg is not None
    assert msg.sender_id == SENDER_FBID
    assert "a_stranger" in msg.body
    assert SENDER_FBID not in msg.body


def test_a_shared_post_renders_with_whatever_half_it_has():
    """XMA is unmeasured, so every partial shape has to produce a sentence
    rather than an exception or an empty body."""
    cases = {
        "[shared post by someaccount]": {"author": "someaccount"},
        "[shared post — just words]": {"caption": "just words"},
        # `title` is sometimes the only text present; the library declares both
        # and no probe has shown which a live delta populates.
        "[shared post — a title]": {"title": "a title"},
        # Something WAS shared. Silence is what this function exists to end.
        "[shared post]": {"author": "", "caption": ""},
    }
    for want, shared in cases.items():
        msg = parse_event(_event(text="", shared=shared))
        assert msg is not None, f"dropped for {shared}"
        assert msg.body == want, f"{shared} rendered {msg.body!r}"


def test_a_message_with_no_shared_object_is_unchanged():
    """Every non-XMA message must keep exactly the body it had. A renderer that
    leaked an empty marker into ordinary traffic would be worse than the bug it
    replaced, because it would fire on every message rather than on reels."""
    for shared in (None, "", [], {}, 0):
        msg = parse_event(_event(text="hello", shared=shared))
        assert msg is not None
        assert msg.body == "hello", f"shared={shared!r} changed an ordinary body"
