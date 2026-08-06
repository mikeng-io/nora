"""The two egress predicates, and why one function could not be both.

`scan_identifiers` is the floor: an E.164 number, an ACI/PNI, or a key-shaped
string is unsafe *wherever* it goes — into the group, into a tool result the
model then reasons over, or out in a tool ARGUMENT to a third party.

`scan_reply` adds the name of the model she runs on. That is a REPLY-only
concern (R-6/R-18): the group must never learn what she runs on, but
「搵首 haiku」 and 「gemini 星座」 are ordinary things for a group of software
engineers to say, and `Deliverer.deliver` fails closed on the WHOLE reply — so
applying the reply predicate anywhere else costs a whole message, or a whole
memory, for a word that was never a disclosure in the first place.

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
def test_a_model_name_passes_the_identifier_scan_and_is_refused_by_the_reply_scan(text):
    """THE reason for the split. `_MODELNAME` matches nine words an engineer
    says in ordinary conversation. Under the old single function these became
    refused tool arguments and withheld memories; under `Deliverer` they still
    must not be said."""
    assert scan_identifiers(text) is None, text
    assert scan_reply(text) == "model", text


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
