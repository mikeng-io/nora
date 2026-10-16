"""The two egress predicates.

`scan_identifiers` is the floor: an E.164 number, an ACI/PNI, or a key-shaped
string is unsafe *wherever* it goes — into the group, into a tool result the
model then reasons over, or out in a tool ARGUMENT to a third party.

`scan_reply` is the same identifier floor, scoped to text addressed to the
GROUP (R-6/R-18). It does not refuse the name of the model she runs on
(`_MODELNAME`): that veto is disabled because `social_fetch` (DEC-151) relays
third-party posts that legitimately name models and a whole-reply veto costs
the answer (see `egress.py`). The two functions remain distinct names because
the reply path is a different audience with a different fail-closed cost, even
though today they check the same patterns.

The split is the point, so every test here asserts on BOTH predicates: a test
that only ever called one of them would pass just as well if the two were
aliases of each other.
"""

from __future__ import annotations

import pytest

from nora.agent.egress import scan_identifiers, scan_reply

# What she runs on. Neither predicate refuses these (the model-name veto is off).
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
    """The model-name veto is OFF (see `egress.py`): both predicates pass these
    words, so a reply naming a model is not withheld. The identifier floor is
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


def test_a_coordinate_pair_is_blocked_by_the_reply_scan_after_dec156():
    """where_been/where_is results carry lat/lng (DEC-156) — the model holds
    the material a reply could echo, so the reply screen blocks the
    pair (the same shape `looks_like_coordinate` uses for tool ARGUMENTS;
    redefined here because security.py imports from this module). Ordinary
    numbers — prices, dates, counts — still pass."""
    assert scan_reply("佢喺 22.410, 114.124 附近") == "coordinate"
    assert scan_reply("-33.87, 151.20") == "coordinate"
    assert scan_reply("22.410,114.124") == "coordinate"
    # no comma pair — model speech, docstring-handled
    assert scan_reply("lat 22.410 lng 114.124") is None
    assert scan_reply("份嘢 $22.5 買的") is None
    assert scan_reply("2026-09-06 見") is None
    assert scan_reply("大概 20 分鐘前喺屋企") is None
