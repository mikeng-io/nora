import dataclasses

import pytest

from nora.models import ConversationTarget, NormalizedMessage, PersonRef, TagContext


def _msg(**over) -> NormalizedMessage:
    base = {
        "platform": "signal",
        "conversation_id": "Z3JvdXA=",
        "conversation_type": "group",
        "sender_id": "+85212345678",
        "sender_name": "Miles",
        "timestamp": 1_700_000_000_000,
        "body": "hello",
        "raw": {"method": "receive"},
    }
    return NormalizedMessage(**{**base, **over})


def test_normalized_message_is_frozen():
    msg = _msg()
    with pytest.raises(dataclasses.FrozenInstanceError):
        msg.body = "changed"  # type: ignore[misc]


def test_normalized_message_target_projects_a_conversation_target():
    target = _msg().target()
    assert target == ConversationTarget(
        platform="signal", conversation_id="Z3JvdXA=", conversation_type="group"
    )


def test_conversation_target_is_hashable_so_it_can_key_locks_and_debounce():
    a = ConversationTarget("signal", "g1", "group")
    b = ConversationTarget("signal", "g1", "group")
    assert {a, b} == {a}


def test_person_ref_peer_id_equals_person_id():
    ref = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
    assert ref.honcho_peer_id == ref.person_id


def test_tag_context_holds_immutable_collections():
    ctx = TagContext(
        tag_id="0f2c",
        message=_msg(),
        tagger=PersonRef("miles", "Miles", "miles"),
        transcript=({"sender_name": "Miles", "body": "hi", "is_nora": 0},),
        peer_card=("likes espresso",),
        started_at=1.0,
    )
    assert isinstance(ctx.transcript, tuple)
    assert isinstance(ctx.peer_card, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.tag_id = "other"  # type: ignore[misc]


def test_tag_context_tagger_may_be_none_for_an_unmapped_sender():
    ctx = TagContext("t", _msg(), None, (), (), 0.0)
    assert ctx.tagger is None
