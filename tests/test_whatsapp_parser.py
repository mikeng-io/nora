"""`nora.platform.whatsapp.parser.parse_event` — the executable half of the
WhatsApp bridge's transport contract. The field names in the fixtures below
are the bridge's, verbatim; a drift in either direction is a failure here, not
a judgment call."""

from nora.domain.identity import Identifier
from nora.domain.models import EditTarget
from nora.platform.whatsapp.jid import lid_form, phone_form
from nora.platform.whatsapp.parser import parse_event

GROUP_JID = "152099000000000000@g.us"
ALICE_PHONE = "85290000100"
ALICE_LID = "60000000000001"


def event(**over) -> dict:
    e = {
        "type": "message",
        "id": "3EB0F1A2B3C4",
        "chat_jid": GROUP_JID,
        "is_group": True,
        "from_me": False,
        "sender_jid": f"{ALICE_PHONE}@s.whatsapp.net",
        "sender_phone": ALICE_PHONE,
        "sender_lid": ALICE_LID,
        "push_name": "Alice",
        "text": "hello",
        "timestamp_ms": 1756963200000,
        "mentioned": [],
        "quoted": None,
        "media": [],
        "edit_of": None,
    }
    e.update(over)
    return e


# ── the ordinary group message ───────────────────────────────────────────────


def test_parses_a_group_message_with_the_chat_jid_verbatim():
    """A group's JID is already the room's one name — policy.toml matches it
    exact-string, so normalization must NOT touch it."""
    msg = parse_event(event())
    assert msg is not None
    assert msg.platform == "whatsapp"
    assert msg.conversation_id == GROUP_JID
    assert msg.conversation_type == "group"
    assert msg.body == "hello"
    assert msg.timestamp == 1756963200000
    assert msg.message_id == "3EB0F1A2B3C4"
    assert msg.raw is not None and msg.raw["id"] == "3EB0F1A2B3C4"


def test_raw_carries_the_full_event():
    e = event()
    assert parse_event(e).raw is e


def test_sender_name_is_the_push_name_or_none():
    assert parse_event(event()).sender_name == "Alice"
    assert parse_event(event(push_name=None)).sender_name is None


# ── sender_id normalization (phone form → lid form → raw JID) ────────────────


def test_sender_id_prefers_the_phone_form():
    assert parse_event(event()).sender_id == "+85290000100"


def test_a_lid_only_sender_lands_on_the_lid_form():
    """Baileys delivers many group senders as LID JIDs — without LID
    resolution, a known person fails resolution and hits R-31's refusal."""
    msg = parse_event(event(
        sender_jid=f"{ALICE_LID}@lid", sender_phone=None, sender_lid=ALICE_LID,
    ))
    assert msg.sender_id == "60000000000001"


def test_a_sender_with_no_resolution_falls_back_to_the_raw_jid():
    """The last resort matches NOTHING in identity.toml — fail closed, which
    is the correct answer for a sender Nora cannot name."""
    msg = parse_event(event(
        sender_jid="60000000000001@lid", sender_phone=None, sender_lid=None,
    ))
    assert msg.sender_id == "60000000000001@lid"


# ── conversation_id for 1:1 chats ────────────────────────────────────────────


def test_a_dm_with_a_phone_jid_normalizes_to_phone_at_server():
    msg = parse_event(event(
        chat_jid="86183254648@s.whatsapp.net", is_group=False,
        sender_jid="86183254648@s.whatsapp.net",
        sender_phone="86183254648", sender_lid=None,
    ))
    assert msg.conversation_type == "dm"
    assert msg.conversation_id == "86183254648@s.whatsapp.net"


def test_a_dm_on_a_lid_jid_resolves_to_the_phone_form_when_the_map_knows_it():
    """The bridge's LID→phone map is what makes this a stable room id — an
    exact-string policy entry cannot follow Baileys flipping the JID form."""
    msg = parse_event(event(
        chat_jid=f"{ALICE_LID}@lid", is_group=False,
        sender_jid=f"{ALICE_LID}@lid", sender_phone=ALICE_PHONE, sender_lid=ALICE_LID,
    ))
    assert msg.conversation_id == "85290000100@s.whatsapp.net"


def test_a_dm_on_an_unresolved_lid_jid_keeps_the_raw_jid():
    msg = parse_event(event(
        chat_jid="96850026752@lid", is_group=False,
        sender_jid="96850026752@lid", sender_phone=None, sender_lid="96850026752",
    ))
    assert msg.conversation_type == "dm"
    assert msg.conversation_id == "96850026752@lid"


# ── mentions: both forms, matching the self-alias contract ───────────────────


def test_mentions_carry_phone_and_lid_forms_together():
    """`MentionPolicy` membership-tests this set against
    `registry.self_aliases()`, and which form that set carries depends on
    `GET /self` — so BOTH normalized forms ride every resolved entry."""
    msg = parse_event(event(mentioned=[
        {"jid": "86183254648@s.whatsapp.net", "phone": "86183254648",
         "lid": "60000000000001"},
    ]))
    assert msg.mentions == frozenset({"+86183254648", "60000000000001"})


def test_a_mention_of_the_bot_matches_the_alias_self_id_would_derive():
    """THE MentionPolicy contract, end to end: the parser's mention value for
    the bot's own JID must equal the `Identifier.value` that
    `nora.platform.whatsapp.self_id.derive_identity` produces for the same
    account — otherwise Nora never sees her own tag.

    Asserted against `Identifier` itself (not a re-typed literal) so the two
    ends of the contract share one normalization and cannot drift apart."""
    bot_phone, bot_lid = "85251112233", "11122233344455"
    expected = {
        Identifier("whatsapp", "phone", phone_form(bot_phone)).value,
        Identifier("whatsapp", "lid", lid_form(bot_lid)).value,
    }
    msg = parse_event(event(mentioned=[
        {"jid": f"{bot_lid}@lid", "phone": bot_phone, "lid": bot_lid},
    ]))
    assert expected <= msg.mentions


def test_an_unresolved_mention_normalizes_off_its_own_jid():
    """No phone/lid fields — a `@s.whatsapp.net` mention still yields its
    phone form; a `@lid` mention its lid form."""
    msg = parse_event(event(mentioned=[
        {"jid": "86183254648@s.whatsapp.net", "phone": None, "lid": None},
        {"jid": "60000000000001@lid", "phone": None, "lid": None},
    ]))
    assert msg.mentions == frozenset({"+86183254648", "60000000000001"})


# ── quote: the ReplyToNoraPolicy shape ────────────────────────────────────────


def test_a_quote_normalizes_its_author_and_carries_the_parent_ts():
    msg = parse_event(event(quoted={
        "id": "3EB0AA", "participant_jid": "86183254648@s.whatsapp.net",
        "participant_phone": "86183254648", "participant_lid": None,
        "timestamp_ms": 1756963100000, "text": "earlier message",
    }))
    assert msg.quote is not None
    assert msg.quote.author_id == "+86183254648"
    assert msg.quote.text == "earlier message"
    assert msg.quote.quoted_ts == 1756963100000


def test_a_quote_whose_parent_predates_the_bridge_cache_degrades_to_ts_zero():
    """The contract's pinned degradation: the reply RELATION survives, the
    parent pointer does not — a `by_ts` lookup misses on 0 and renders no
    parent, rather than rendering a wrong one."""
    msg = parse_event(event(quoted={
        "id": "3EB0AA", "participant_jid": "86183254648@s.whatsapp.net",
        "participant_phone": "86183254648", "participant_lid": None,
        "timestamp_ms": None, "text": "earlier message",
    }))
    assert msg.quote is not None
    assert msg.quote.quoted_ts == 0


def test_a_lid_only_quoted_participant_normalizes_to_the_lid_form():
    msg = parse_event(event(quoted={
        "id": "3EB0AA", "participant_jid": "60000000000001@lid",
        "participant_phone": None, "participant_lid": "60000000000001",
        "timestamp_ms": 1756963100000, "text": "earlier message",
    }))
    assert msg.quote.author_id == "60000000000001"


# ── media ─────────────────────────────────────────────────────────────────────


def test_a_voice_note_only_turn_survives_as_a_placeholder_body():
    """The contract's engagement section: a voice note rides to the ambient
    tier as `MediaRef(voice_note=True)` — dropped nowhere. Without this, a
    member who SPOKE to the group would vanish from the transcript."""
    msg = parse_event(event(text=None, media=[
        {"kind": "audio", "mimetype": "audio/ogg; codecs=opus",
         "voice_note": True, "seconds": 4, "size": 12345,
         "caption": None, "filename": None},
    ]))
    assert msg is not None
    assert msg.body == "[voice note]"
    assert len(msg.media) == 1
    ref = msg.media[0]
    assert ref.voice_note is True
    assert ref.media_type == "audio/ogg; codecs=opus"
    assert ref.size == 12345


def test_media_refs_carry_no_custody_handle_and_no_sender_filename():
    """The parser does not take custody: the event carries the FACT of media, never
    the blob, so `platform_id`/`fetch_token` are empty. And sender-supplied
    filenames never render (`nora/domain/media.py`'s rule) — the ref does
    not even record one."""
    msg = parse_event(event(media=[
        {"kind": "image", "mimetype": "image/jpeg", "voice_note": False,
         "seconds": None, "size": 999, "caption": None, "filename": "vacation.jpg"},
    ]))
    ref = msg.media[0]
    assert ref.platform_id == ""
    assert ref.fetch_token == ""
    assert ref.filename is None


def test_text_and_media_render_text_first_placeholders_last():
    msg = parse_event(event(text="look at this", media=[
        {"kind": "image", "mimetype": "image/jpeg", "voice_note": False,
         "seconds": None, "size": 999, "caption": None, "filename": None},
        {"kind": "video", "mimetype": "video/mp4", "voice_note": False,
         "seconds": 3, "size": 9999, "caption": None, "filename": None},
    ]))
    assert msg.body == "look at this [image] [video]"


# ── the dropped-before-NormalizedMessage list ────────────────────────────────


def test_from_me_is_dropped():
    """R-12: Nora never ingests her own sends."""
    assert parse_event(event(from_me=True)) is None


def test_status_broadcast_is_dropped():
    assert parse_event(event(chat_jid="status@broadcast", is_group=False)) is None


def test_a_newsletter_is_dropped():
    assert parse_event(event(chat_jid="152099000000000000@newsletter",
                             is_group=False)) is None


def test_a_turn_with_neither_text_nor_media_is_dropped():
    assert parse_event(event(text=None, media=[])) is None
    assert parse_event(event(text="   ", media=[])) is None


def test_non_message_types_are_dropped():
    """Heartbeat/dropped are listener-level events; the parser still answers
    None for them so it stays total on its own."""
    assert parse_event({"type": "heartbeat", "ts_ms": 1}) is None
    assert parse_event({"type": "dropped", "dropped_total": 17}) is None


# ── edits and malformed input ────────────────────────────────────────────────


def test_an_edit_names_the_stanza_it_edits():
    """`edit_of` names the original stanza; `text` is the NEW content. It
    becomes `NormalizedMessage.edit_of`, and `message_id` stays the edit's own
    stanza id — the original's is what `edit_of` carries."""
    msg = parse_event(event(
        id="3EB0F2", edit_of="3EB0F1A2B3C4", text="hello, corrected",
    ))
    assert msg is not None
    assert msg.edit_of == EditTarget(message_id="3EB0F1A2B3C4")
    assert msg.message_id == "3EB0F2"
    assert msg.body == "hello, corrected"


def test_an_ordinary_message_is_not_an_edit():
    msg = parse_event(event(id="3EB0F2", text="hi"))
    assert msg is not None and msg.edit_of is None


def test_a_malformed_event_is_none_not_an_exception():
    """Never raises: one bad event is logged and skipped, and the stream
    behind it lives on."""
    assert parse_event({}) is None
    assert parse_event(None) is None
    assert parse_event({"type": "message"}) is None
    assert parse_event(event(timestamp_ms="junk")) is not None  # degrades to 0
    assert parse_event(event(mentioned="not-a-list")) is not None


# ── reactions and revokes ────────────────────────────────────────────────────


def _touch_event(kind: str, **over) -> dict:
    e = {
        "type": kind, "id": "3EB0F2", "chat_jid": GROUP_JID, "is_group": True,
        "from_me": False, "sender_jid": f"{ALICE_PHONE}@s.whatsapp.net",
        "sender_phone": ALICE_PHONE, "sender_lid": ALICE_LID, "push_name": "Alice",
        "target_id": "3EB0F1A2B3C4", "timestamp_ms": 1756963200500,
    }
    if kind == "reaction":
        e.update(emoji="👍", removed=False)
    e.update(over)
    return e


def test_a_reaction_is_a_touch_on_the_stanza_it_names():
    from nora.domain.models import EditTarget

    msg = parse_event(_touch_event("reaction"))
    assert msg is not None and msg.body == "" and msg.edit_of is None
    assert msg.conversation_id == parse_event(event()).conversation_id
    assert msg.sender_id == parse_event(event()).sender_id
    assert msg.touch is not None
    assert (msg.touch.kind, msg.touch.emoji, msg.touch.removed) == ("reaction", "👍", False)
    assert msg.touch.target == EditTarget(message_id="3EB0F1A2B3C4")
    assert msg.timestamp == 1756963200500


def test_an_empty_reaction_is_a_removal_with_no_emoji_claimed():
    msg = parse_event(_touch_event("reaction", emoji="", removed=True))
    assert msg is not None and msg.touch is not None
    assert (msg.touch.emoji, msg.touch.removed) == ("", True)


def test_a_revoke_is_a_delete_touch():
    msg = parse_event(_touch_event("delete"))
    assert msg is not None and msg.touch is not None and msg.touch.kind == "delete"


def test_nora_s_own_touch_and_one_naming_nothing_are_dropped():
    assert parse_event(_touch_event("reaction", from_me=True)) is None
    assert parse_event(_touch_event("delete", target_id="")) is None
