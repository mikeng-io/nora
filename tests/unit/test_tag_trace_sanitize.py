from nora.agent.tag_trace_sanitize import strip_synthetic_artifacts, cap_messages

def test_the_reply_guard_veto_message_is_dropped():
    messages = [
        {"role": "user", "content": [{"text": "hi"}]},
        {"role": "assistant", "content": [
            {"text": "Reply already sent — the turn is finished."}
        ]},
    ]
    out = strip_synthetic_artifacts(messages)
    assert out == [{"role": "user", "content": [{"text": "hi"}]}]

def test_a_cancelled_tool_result_block_is_dropped():
    messages = [{
        "role": "user",
        "content": [{"toolResult": {
            "toolUseId": "x", "status": "error",
            "content": [{"text": "Tool execution cancelled"}],
        }}],
    }]
    assert strip_synthetic_artifacts(messages) == []

def test_a_max_tokens_recovery_placeholder_is_dropped():
    messages = [{
        "role": "assistant",
        "content": [
            {"text": "real reasoning"},
            {"text": (
                "The selected tool calculator's tool use was incomplete due "
                "to maximum token limits being reached."
            )},
        ],
    }]
    out = strip_synthetic_artifacts(messages)
    assert out == [{"role": "assistant", "content": [{"text": "real reasoning"}]}]

def test_real_content_survives_untouched():
    messages = [
        {"role": "user", "content": [{"text": "what's the weather"}]},
        {"role": "assistant", "content": [
            {"reasoningContent": {"reasoningText": {"text": "let me check"}}},
            {"toolUse": {"name": "weather", "toolUseId": "1", "input": {}}},
        ]},
    ]
    assert strip_synthetic_artifacts(messages) == messages

def test_cap_messages_truncates_assistant_narration_and_pins_human_turns():
    # A long assistant text block gets truncated; the user's own words never do.
    long_text = "x" * 5000
    messages = [
        {"role": "user", "content": [{"text": long_text}]},
        {"role": "assistant", "content": [{"text": long_text}]},
    ]
    capped, truncated = cap_messages(messages, max_chars=1000)
    assert capped[0]["content"][0]["text"] == long_text  # user turn: untouched
    assert len(capped[1]["content"][0]["text"]) < len(long_text)  # assistant: cut
    assert truncated == 1

def test_cap_messages_reports_zero_truncations_when_nothing_is_cut():
    messages = [{"role": "user", "content": [{"text": "hi"}]}]
    capped, truncated = cap_messages(messages, max_chars=1000)
    assert truncated == 0
    assert capped == messages
