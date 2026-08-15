import pytest

from nora.platform.signal.parser import parse_event

OWN = "+85290000100"


def envelope(**data_over) -> dict:
    data = {"message": "hello", "groupInfo": {"groupId": "Z3JvdXA="}, "timestamp": 1700}
    data.update(data_over)
    return {
        "method": "receive",
        "params": {
            "envelope": {
                "source": "+85212345678",
                "sourceName": "Miles",
                "timestamp": 1700,
                "dataMessage": data,
            }
        },
    }


def test_parses_a_group_text_message():
    msg = parse_event(envelope(), OWN)
    assert msg is not None
    assert msg.platform == "signal"
    assert msg.conversation_id == "Z3JvdXA="
    assert msg.conversation_type == "group"
    assert msg.sender_id == "+85212345678"
    assert msg.sender_name == "Miles"
    assert msg.timestamp == 1700
    assert msg.body == "hello"


def test_raw_carries_the_full_event():
    assert parse_event(envelope(), OWN).raw["method"] == "receive"


def test_drops_a_non_receive_method():
    e = envelope()
    e["method"] = "typing"
    assert parse_event(e, OWN) is None


def test_drops_a_sync_message():
    e = envelope()
    e["params"]["envelope"] = {"syncMessage": {"sentMessage": {"message": "x"}}}
    assert parse_event(e, OWN) is None


def test_drops_a_dm():
    assert parse_event(envelope(groupInfo=None), OWN) is None


def test_drops_our_own_message():
    e = envelope()
    e["params"]["envelope"]["source"] = OWN
    assert parse_event(e, OWN) is None


def test_drops_our_own_message_by_source_number():
    e = envelope()
    e["params"]["envelope"].pop("source")
    e["params"]["envelope"]["sourceNumber"] = OWN
    assert parse_event(e, OWN) is None


def test_drops_our_own_message_by_source_uuid():
    e = envelope()
    e["params"]["envelope"].pop("source")
    e["params"]["envelope"]["sourceUuid"] = OWN
    assert parse_event(e, OWN) is None


def test_a_number_hidden_sender_resolves_via_source_uuid():
    """design/10 §1.1/§1.2 PROBE: with the phone number hidden, signal-cli
    sends neither `source` nor `sourceNumber` — only `sourceUuid`. Before this
    fix, `parser.py` never read that field and such a sender's `sender_id`
    came back empty, so no identity.toml mapping could ever match them."""
    e = envelope()
    e["params"]["envelope"].pop("source")
    e["params"]["envelope"]["sourceUuid"] = "8185b8fa-359a-4c0a-c6ed-24814dc655bb"
    msg = parse_event(e, OWN)
    assert msg is not None
    assert msg.sender_id == "8185b8fa-359a-4c0a-c6ed-24814dc655bb"


def test_source_is_preferred_over_source_uuid_when_both_are_present():
    e = envelope()
    e["params"]["envelope"]["sourceUuid"] = "8185b8fa-359a-4c0a-c6ed-24814dc655bb"
    msg = parse_event(e, OWN)
    assert msg.sender_id == "+85212345678"


def test_drops_a_reaction_only_payload():
    assert parse_event(envelope(message=None, reaction={"emoji": "👍"}), OWN) is None


def test_a_sticker_becomes_a_fetchable_ref_not_a_bare_string():
    """A sticker used to render `[sticker]` with no `MediaRef`, because the
    parser's comment said there was 'no attachment id to fetch'. That was true
    of `getAttachment` and false of the daemon: `getSticker(packId, stickerId)`
    is verified to exist on 0.14.7 and to return bytes (spec §1). Without a ref
    the sticker never enters custody and can never be read."""
    msg = parse_event(
        envelope(message=None, sticker={"packId": "x", "stickerId": 3}), OWN
    )
    assert msg is not None
    assert msg.body == "[sticker]"
    assert len(msg.media) == 1
    assert msg.media[0].sticker is True
    assert msg.media[0].fetch_token == "sticker:x:3"  # noqa: S105
    assert msg.media[0].platform_id == "", "a sticker has no attachment id"


def test_a_captioned_sticker_keeps_both_halves():
    """The booster used to be the half that was lost: `_body_parts` appended
    `[sticker]` only when nothing else had rendered, so a sticker sent WITH a
    caption vanished from the record entirely. That is precisely the case this
    feature exists for — several messages, then a sticker as a booster."""
    msg = parse_event(
        envelope(message="haha", sticker={"packId": "x", "stickerId": 3}), OWN
    )
    assert msg is not None
    assert msg.body == "haha [sticker]"


def test_a_malformed_sticker_payload_is_not_a_ref():
    """A sticker object missing either field cannot be fetched, so inventing a
    ref for it would manufacture a custody failure to count on every one."""
    msg = parse_event(envelope(message="hi", sticker={"packId": "x"}), OWN)
    assert msg is not None
    assert msg.media == ()
    assert msg.body == "hi"


def test_image_attachment_becomes_a_placeholder():
    msg = parse_event(
        envelope(message=None, attachments=[{"contentType": "image/jpeg", "id": "a1"}]), OWN
    )
    assert msg.body == "[image]"


def test_named_attachment_becomes_a_placeholder():
    """The filename is NOT rendered any more (DEC-43). A filename is
    sender-supplied text of unbounded shape — it can carry a phone number, an
    ACI, or a prompt — and it was going straight into the body Nora reasons
    over. What she needs is what KIND of thing arrived, which the registered
    media type already says."""
    msg = parse_event(
        envelope(message=None, attachments=[{"contentType": "application/pdf",
                                             "filename": "spec.pdf"}]), OWN
    )
    assert msg.body == "[document]"


def test_a_voice_note_says_that_somebody_spoke():
    """The defect DEC-43 exists for. `_attachment_placeholder` special-cased
    `image/` and nothing else, so a voice note — the second most common thing
    sent in this group — reached her as `[attachment: 1754…aac]`: a filename,
    with no way to tell that a human had spoken."""
    msg = parse_event(
        envelope(message=None, attachments=[
            {"contentType": "audio/aac", "filename": "1754521883.aac",
             "voiceNote": True, "id": "att1"},
        ]), OWN
    )
    assert msg.body == "[voice note]"


def test_audio_that_is_not_a_voice_note_is_not_called_one():
    """A shared song is not somebody speaking. Same top-level type, different
    thing, and conflating them would put words in a member's mouth."""
    msg = parse_event(
        envelope(message=None, attachments=[
            {"contentType": "audio/mpeg", "filename": "track.mp3", "id": "att2"},
        ]), OWN
    )
    assert msg.body == "[audio]"


def test_video_and_document_are_distinguished():
    msg = parse_event(
        envelope(message=None, attachments=[
            {"contentType": "video/mp4", "id": "v"},
            {"contentType": "text/plain", "filename": "notes.txt", "id": "d"},
        ]), OWN
    )
    assert msg.body == "[video] [document]"


def test_a_media_type_with_parameters_is_still_parsed():
    """RFC 6838 §4.3: a content type may carry parameters. Prefix matching on
    the raw string got this right by accident for `image/`; the taxonomy must
    get it right on purpose for everything else."""
    msg = parse_event(
        envelope(message=None, attachments=[
            {"contentType": "AUDIO/OGG; codecs=opus", "voiceNote": True, "id": "o"},
        ]), OWN
    )
    assert msg.body == "[voice note]"


def test_an_attachment_with_no_content_type_falls_back_to_the_filename():
    """`mimetypes` — the stdlib's own registry — rather than a second hand-rolled
    extension table."""
    msg = parse_event(
        envelope(message=None, attachments=[{"filename": "holiday.jpeg", "id": "n"}]), OWN
    )
    assert msg.body == "[image]"


def test_an_unidentifiable_attachment_still_renders_something():
    msg = parse_event(envelope(message=None, attachments=[{"id": "mystery"}]), OWN)
    assert msg.body == "[file]"


def test_the_parser_carries_the_attachment_refs_alongside_the_body():
    """The placeholder is for Nora; the ref is for custody. Both come off the
    same envelope so the ingest stage never re-parses one."""
    msg = parse_event(
        envelope(message=None, attachments=[
            {"contentType": "image/jpeg", "id": "att-1", "filename": "a.jpg", "size": 42},
        ]), OWN
    )
    assert len(msg.media) == 1
    ref = msg.media[0]
    assert ref.platform_id == "att-1"
    assert ref.media_type == "image/jpeg"
    assert ref.filename == "a.jpg"
    assert ref.size == 42


def test_text_and_attachment_are_joined():
    msg = parse_event(envelope(attachments=[{"contentType": "image/png", "id": "a"}]), OWN)
    assert msg.body == "hello [image]"


def test_missing_timestamp_falls_back_to_the_data_message_timestamp():
    e = envelope(timestamp=2500)
    e["params"]["envelope"].pop("timestamp")
    assert parse_event(e, OWN).timestamp == 2500


def test_missing_source_name_leaves_sender_name_none():
    e = envelope()
    e["params"]["envelope"].pop("sourceName")
    assert parse_event(e, OWN).sender_name is None


@pytest.mark.parametrize(
    "event",
    [{}, {"method": "receive"}, {"method": "receive", "params": None},
     {"method": "receive", "params": {"envelope": None}},
     {"method": "receive", "params": {"envelope": {"dataMessage": "not-a-dict"}}}],
)
def test_a_malformed_envelope_never_raises(event):
    assert parse_event(event, OWN) is None


def test_whitespace_only_message_with_no_attachment_is_dropped():
    assert parse_event(envelope(message="   "), OWN) is None


# ── transport framing ────────────────────────────────────────────────────────
# signal-cli frames the same envelope two ways and the man page documents only
# the JSON-RPC one. Nora runs on the HTTP SSE transport, which uses the other.
# Every test above builds the JSON-RPC form, which is exactly why a parser that
# understood ONLY that form passed the whole suite while ingesting nothing in
# production for 61 days. These pin the shape actually observed on the wire.


def sse_envelope(**data_over) -> dict:
    """The literal shape captured from GET /api/v1/events on signal-cli 0.14.7.

    No `method`, no `params` — envelope at the top level, `receive` having been
    carried in the SSE `event:` field that the listener strips.
    """
    data = {"message": "hello", "groupInfo": {"groupId": "Z3JvdXA="}, "timestamp": 1700}
    data.update(data_over)
    return {
        "envelope": {
            "source": "+85212345678",
            "sourceName": "Miles",
            "timestamp": 1700,
            "dataMessage": data,
        },
        "account": OWN,
    }


def test_parses_the_http_sse_framing_with_no_method_key():
    msg = parse_event(sse_envelope(), OWN)
    assert msg is not None, "the HTTP SSE framing must parse — this is the live transport"
    assert msg.body == "hello"
    assert msg.conversation_id == "Z3JvdXA="
    assert msg.sender_id == "+85212345678"


def test_sse_framing_still_drops_a_self_send():
    e = sse_envelope()
    e["envelope"]["source"] = OWN
    assert parse_event(e, OWN) is None      # R-12 holds on both framings


def test_sse_framing_still_drops_a_dm():
    assert parse_event(sse_envelope(groupInfo=None), OWN) is None


def test_sse_framing_still_drops_a_typing_notification():
    e = sse_envelope()
    e["envelope"].pop("dataMessage")
    e["envelope"]["typingMessage"] = {"action": "STARTED", "groupId": "Z3JvdXA="}
    assert parse_event(e, OWN) is None


def test_parses_the_subscribe_receive_framing():
    """--receive-mode=manual nests the envelope under params.result."""
    inner = envelope()["params"]["envelope"]
    e = {"method": "receive", "params": {"subscription": 0, "result": {"envelope": inner}}}
    assert parse_event(e, OWN).body == "hello"


def test_drops_an_event_that_is_neither_framing():
    assert parse_event({"jsonrpc": "2.0", "result": 0, "id": "x"}, OWN) is None
    assert parse_event({"envelope": "not-a-dict"}, OWN) is None
    assert parse_event({}, OWN) is None


# ── edited messages ─────────────────────────────────────────────────────────
# signal-cli nests an edit as `envelope.editMessage.dataMessage` (JsonEditMessage,
# alongside targetSentTimestamp), NOT as `envelope.dataMessage`. Reading only the
# top-level key made every edit silently invisible — the same wrong-silence shape
# as the framing bug above, one key deeper.


def sse_edit(message: str = "corrected", **over) -> dict:
    inner = {
        "message": message,
        "groupInfo": {"groupId": "Z3JvdXA=", "groupName": "Ridgeline", "type": "DELIVER"},
        "timestamp": 1800,
    }
    inner.update(over)
    return {
        "envelope": {
            "source": "+85212345678",
            "sourceName": "Miles",
            "timestamp": 1800,
            "editMessage": {"targetSentTimestamp": 1700, "dataMessage": inner},
        },
        "account": OWN,
    }


def test_an_edited_message_is_ingested():
    msg = parse_event(sse_edit(), OWN)
    assert msg is not None, "an edit must not be silently dropped"
    assert msg.body == "corrected"
    assert msg.conversation_id == "Z3JvdXA="


def test_an_edit_still_obeys_the_self_send_rule():
    e = sse_edit()
    e["envelope"]["source"] = OWN
    assert parse_event(e, OWN) is None       # R-12 holds for edits too


def test_an_edit_of_a_dm_is_still_dropped():
    assert parse_event(sse_edit(groupInfo=None), OWN) is None


def test_an_empty_edit_is_dropped():
    assert parse_event(sse_edit(message="   "), OWN) is None


def test_an_edit_in_the_jsonrpc_framing_also_parses():
    inner = sse_edit()["envelope"]["editMessage"]
    e = {"method": "receive", "params": {"envelope": {
        "source": "+85212345678", "timestamp": 1800, "editMessage": inner}}}
    assert parse_event(e, OWN).body == "corrected"


# ── @-mentions ──────────────────────────────────────────────────────────────
# Captured from the live group: Signal replaces the mentioned name in `body`
# with U+FFFC (OBJECT REPLACEMENT CHARACTER) and carries who was mentioned in
# a parallel array. A mention is therefore structurally invisible to any
# substring trigger match, which is why tagging Nora the obvious way produced
# ingest, attribution and total silence.


def test_mentions_are_extracted_in_every_id_form():
    e = sse_envelope()
    e["envelope"]["dataMessage"]["message"] = "￼ who are u"
    e["envelope"]["dataMessage"]["mentions"] = [{
        "name": "+85290000100", "number": "+85290000100",
        "uuid": "a1000000-0000-4000-8000-000000000000", "start": 0, "length": 1,
    }]
    msg = parse_event(e, OWN)
    assert msg is not None
    assert "a1000000-0000-4000-8000-000000000000" in msg.mentions, (
        "the ACI is the id Signal actually sends under sealed sender"
    )
    assert "+85290000100" in msg.mentions


def test_a_message_with_no_mentions_has_an_empty_set():
    assert parse_event(sse_envelope(), OWN).mentions == frozenset()


def test_a_malformed_mentions_entry_is_skipped_not_fatal():
    e = sse_envelope()
    e["envelope"]["dataMessage"]["mentions"] = ["not-a-dict", {"uuid": "u1"}, {}]
    assert parse_event(e, OWN).mentions == frozenset({"u1"})


# ── the quote is a typed parent pointer (design/11 §2.1, §2.8) ───────────────
# Bug A: signal-cli's shipped `quote.schema.json` carries `id` (REQUIRED),
# `author`, `authorNumber`, `authorUuid`, `text`, `attachments`, `mentions` and
# `textStyles` — and NO `authorName`. `_quoted`'s `q.get("authorName") or ""`
# therefore yielded `""` on every real message, and `<replying_to author="">`
# rendered "replying to nobody" for the whole life of that feature.


def test_a_quote_becomes_a_typed_pointer_at_the_parent_message():
    from nora.domain.models import Quote

    msg = parse_event(envelope(quote={
        "id": 1785950108291, "author": "u-nora", "authorNumber": "+85290000100",
        "text": "早晨", "attachments": [],
    }), OWN)
    assert msg is not None
    assert msg.quote == Quote(author_id="u-nora", text="早晨", quoted_ts=1785950108291)


def test_a_quote_with_no_id_is_not_threadable_and_is_dropped():
    """`id` is REQUIRED in signal-cli's quote schema. A quote without one carries
    no pointer at a parent, so there is nothing threading could ever resolve."""
    msg = parse_event(envelope(quote={"author": "u-nora", "text": "早晨"}), OWN)
    assert msg is not None
    assert msg.quote is None


def test_quote_carries_no_author_name_because_signal_cli_never_sends_one():
    """A field that is provably always empty is exactly how the `peer_card` bug
    happened. The parent ROW's own `sender_name` is the attribution now."""
    from dataclasses import fields

    from nora.domain.models import Quote

    assert {f.name for f in fields(Quote)} == {"author_id", "text", "quoted_ts"}


def test_a_quote_with_a_non_integer_id_is_dropped_rather_than_raising():
    msg = parse_event(envelope(quote={"id": "not-a-number", "text": "早晨"}), OWN)
    assert msg is not None
    assert msg.quote is None


# ---- mention_spans (DEC-34) ------------------------------------------------


def _mention(**over) -> dict:
    m = {"name": "+852222", "number": "+852222",
         "uuid": "a1000000-0000-4000-8000-000000000000", "start": 0, "length": 1}
    m.update(over)
    return m


def test_mention_spans_reads_the_identity_the_body_does_not_carry():
    from nora.platform.signal.parser import MentionSpan, mention_spans

    event = envelope(message="￼ who are u", mentions=[_mention()])
    assert mention_spans(event) == (
        MentionSpan(start=0, length=1,
                    ids=("a1000000-0000-4000-8000-000000000000", "+852222")),
    )


def test_mention_spans_never_offers_the_name_field():
    """`name` is the sender's PHONE NUMBER on every row in `data/messages.db`.

    `_mentioned_ids` keeps it — a superset costs nothing when the question is
    "was Nora tagged" — and this must not, because its output is rendered.
    """
    from nora.platform.signal.parser import mention_spans

    spans = mention_spans(envelope(
        message="￼ hi", mentions=[_mention(name="+85288888888")]))
    assert "+85288888888" not in spans[0].ids


def test_mention_offsets_are_rebased_onto_the_body_that_was_stored():
    """`start` indexes `dataMessage.message`; the stored body is that string
    STRIPPED. Two leading spaces and the raw offset lands two characters late,
    which the renderer then refuses (it is not on a placeholder) and the
    mention silently stays a void."""
    from nora.platform.signal.parser import mention_spans

    raw = "  ￼ hello"
    spans = mention_spans(envelope(message=raw, mentions=[_mention(start=2)]))
    assert spans[0].start == 0
    assert raw.strip()[spans[0].start] == "￼"


def test_mention_spans_reads_an_edited_message_from_the_same_place_its_body_came_from():
    from nora.platform.signal.parser import mention_spans

    event = {"envelope": {"source": "+852111", "timestamp": 1, "editMessage": {
        "targetSentTimestamp": 1,
        "dataMessage": {"message": "￼ fixed", "groupInfo": {"groupId": "g1"},
                        "timestamp": 2, "mentions": [_mention()]}}}}
    assert len(mention_spans(event)) == 1


@pytest.mark.parametrize("event", [
    {},
    {"method": "typing"},
    envelope(message="no mentions here"),
    envelope(message="￼ hi", mentions=["not a dict"]),
    envelope(message="￼ hi", mentions=[_mention(start="x")]),
    envelope(message="", attachments=[{"contentType": "image/png"}],
             mentions=[_mention()]),
])
def test_mention_spans_is_total(event):
    """A caller must never have to ask whether this platform's convention
    applies — the last case is a body with no text part at all, whose offsets
    would index an attachment placeholder."""
    from nora.platform.signal.parser import mention_spans

    assert mention_spans(event) == ()
