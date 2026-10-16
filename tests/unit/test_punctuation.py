"""`nora.agent.punctuation`: no em dash reaches the model.

The reason is in the module docstring: she copies the dashes she is shown,
so these pin that the REQUEST is clean, not that a rule asks her to be.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from nora.agent.factory import create_model_provider
from nora.agent.punctuation import undash
from nora.agent.structured import build_structured_model

DASH = "—"
FW_COMMA = "，"


def test_a_cantonese_pause_becomes_the_full_width_comma() -> None:
    assert undash(f"申報唔清{DASH}{DASH}集運商求其填") == f"申報唔清{FW_COMMA}集運商求其填"


def test_an_english_aside_becomes_a_comma() -> None:
    assert undash(f"Send a message {DASH} the only way") == "Send a message, the only way"


def test_the_en_dash_is_a_range_and_is_left_alone() -> None:
    assert undash("2–5 個工作天") == "2–5 個工作天"


def _tag_model():
    return create_model_provider(
        model="nora", max_tokens=10, temperature=0.0,
        client=AsyncOpenAI(api_key="x", base_url="http://127.0.0.1:1"),
    )


def _request(model) -> dict:
    return model.format_request(
        [{"role": "user", "content": [{"text": f"佢貼咗條 link{DASH}{DASH}冇人接"}]},
         {"role": "assistant", "content": [{"toolUse": {
             "toolUseId": "t1", "name": "send_reply",
             "input": {"text": f"已經 send 咗{DASH}{DASH}唔改"}}}]}],
        [{"name": "send_reply", "description": f"Send it {DASH} now",
          "inputSchema": {"json": {"type": "object", "properties": {
              "text": {"type": "string", "description": f"One message {DASH} yours"}}}}}],
        f"你係 Nora{DASH}{DASH}一個朋友",
    )


def test_nothing_she_reads_on_the_tag_path_carries_an_em_dash() -> None:
    request = _request(_tag_model())
    read = [m for m in request["messages"] if m["role"] != "assistant"], request["tools"]
    assert DASH not in repr(read)
    assert f"佢貼咗條 link{FW_COMMA}冇人接" in repr(read)
    assert "Send it, now" in repr(read)


def test_what_she_already_sent_is_replayed_verbatim() -> None:
    calls = [m for m in _request(_tag_model())["messages"] if m.get("tool_calls")]
    assert f"已經 send 咗{DASH}{DASH}唔改" in calls[0]["tool_calls"][0]["function"]["arguments"]


def test_the_structured_calls_that_write_what_she_later_reads_are_clean_too() -> None:
    model = build_structured_model(
        base_url="http://127.0.0.1:1", api_key="x", model_id="nora",
        max_tokens=10, temperature=0.0,
    )
    assert DASH not in repr(model.format_request(
        [{"role": "user", "content": [{"text": f"一句{DASH}兩句"}]}], None, f"系統{DASH}提示",
    ))
