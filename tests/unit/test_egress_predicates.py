"""The two egress predicates.

`scan_identifiers` is the floor: an E.164 number, an ACI/PNI, or a key-shaped
string is unsafe *wherever* it goes — into the group, into a tool result the
model then reasons over, or out in a tool ARGUMENT to a third party.

`scan_reply` is the same identifier floor, scoped to text addressed to the
GROUP (R-6/R-18). It once ALSO refused the name of the model she runs on
(`_MODELNAME`) — that veto is DISABLED by operator decision (2026-08-31) after
`social_fetch` (DEC-151) relayed third-party posts that legitimately name
models and the whole-reply veto cost the answer (see `egress.py`). The two
functions remain distinct names because the reply path is a different audience
with a different fail-closed cost, even though today they check the same
patterns.

The split is the point, so every test here asserts on BOTH predicates: a test
that only ever called one of them would pass just as well if the two were
aliases of each other.
"""

from __future__ import annotations

import pytest

from nora.agent.egress import scan_identifiers, scan_reply

# What she runs on. Only `scan_reply` may refuse these.
MODEL_WORDS = (
    "搵首 haiku 嚟聽下",
    "gemini 星座 準唔準",
    "opus 同 sonnet 邊個平啲",
    "個 app 用緊 claude 同 openai",
    "deepseek 出咗新 model",
)

# Somebody's identifier. BOTH predicates must refuse these.
IDENTIFIERS = (
    ("phone", "佢個號碼係 +85290000101 呀"),
    ("uuid", "uuid: a1000000-0000-4000-8000-000000000000"),
    ("keylike", "here you go: sk-abcdefghijklmnop1234567"),
)

# Ordinary chat. Neither predicate may refuse these — `egress.py` documents
# free-standing digit runs as deliberately unmatched.
ORDINARY = (
    "價錢係 12345678 蚊，貴到痴線 😏",
    "聽日 8:30 樓下等",
    "K73 幾時到",
)


@pytest.mark.parametrize("text", MODEL_WORDS)
def test_a_model_name_passes_both_predicates_after_the_veto_was_disabled(text):
    """The model-name veto is OFF (operator, 2026-08-31 — see `egress.py`).
    These words were once refused by `scan_reply` and withheld whole replies;
    now both predicates pass them. The identifier floor is unchanged and is
    covered by the IDENTIFIERS tests below."""
    assert scan_identifiers(text) is None, text
    assert scan_reply(text) is None, text


@pytest.mark.parametrize("kind,text", IDENTIFIERS)
def test_somebody_elses_identifier_is_refused_by_both_predicates(kind, text):
    """The floor. Splitting must not have moved any of these out of the
    identifier set — that is exactly how a split loosens a guard."""
    assert scan_identifiers(text) == kind, text
    assert scan_reply(text) == kind, text


@pytest.mark.parametrize("text", ORDINARY)
def test_ordinary_chat_passes_both_predicates(text):
    """Fails closed, but not at random: a guard that refused these would make
    her mute on prices, times and bus routes."""
    assert scan_identifiers(text) is None, text
    assert scan_reply(text) is None, text


def test_the_empty_string_passes_both():
    assert scan_identifiers("") is None
    assert scan_reply("") is None


def test_the_reply_scan_still_reports_the_identifier_when_both_are_present():
    """A reply carrying a number AND the word `claude` is blocked either way,
    but the counter should name the exposure that matters. `phone` tells the
    operator somebody's number nearly went out; `model` would not."""
    assert scan_reply("+85290000101 是 claude 講嘅") == "phone"
