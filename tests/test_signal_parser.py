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


def test_a_sticker_only_payload_renders_a_placeholder():
    """Was dropped, which made a sticker-only message vanish entirely — the
    sender watched Nora ignore something they had clearly sent. A sticker is
    an image, and images already render `[image]`."""
    msg = parse_event(
        envelope(message=None, sticker={"packId": "x", "stickerId": 3}), OWN
    )
    assert msg is not None
    assert msg.body == "[sticker]"


def test_image_attachment_becomes_a_placeholder():
    msg = parse_event(
        envelope(message=None, attachments=[{"contentType": "image/jpeg", "id": "a1"}]), OWN
    )
    assert msg.body == "[image]"


def test_named_attachment_becomes_a_placeholder():
    msg = parse_event(
        envelope(message=None, attachments=[{"contentType": "application/pdf",
                                             "filename": "spec.pdf"}]), OWN
    )
    assert msg.body == "[attachment: spec.pdf]"


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
