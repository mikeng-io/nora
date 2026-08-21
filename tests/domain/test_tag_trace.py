import pytest

from nora.domain.tag_trace import VALID_OUTCOMES, TagTrace


def test_a_tag_trace_holds_messages_transcript_and_outcome():
    trace = TagTrace(
        tag_id="t1",
        conversation_id="c1",
        messages=({"role": "user", "content": [{"text": "hi"}]},),
        transcript=(),
        outcome="send_reply",
        created_at=1_723_000_000_000,
    )
    assert trace.tag_id == "t1"
    assert trace.outcome == "send_reply"
    assert trace.messages == ({"role": "user", "content": [{"text": "hi"}]},)


def test_an_unknown_outcome_is_refused():
    with pytest.raises(ValueError, match="outcome"):
        TagTrace(
            tag_id="t1",
            conversation_id="c1",
            messages=(),
            transcript=(),
            outcome="made_up",
            created_at=1_723_000_000_000,
        )


def test_valid_outcomes_matches_the_spec_six():
    assert VALID_OUTCOMES == frozenset({
        "send_reply",
        "recovered",
        "silent",
        "vetoed",
        "timeout",
        "llm_error",
    })
