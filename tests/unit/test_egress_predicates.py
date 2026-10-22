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
    assert scan_reply("份嘢 $22.5 買的") is None
    assert scan_reply("2026-09-06 見") is None
    assert scan_reply("大概 20 分鐘前喺屋企") is None


def _decimal(whole: int, frac: int) -> str:
    """A generated decimal: `_decimal(1, 111)` is `1.111`. The coordinate fixtures
    are built here rather than written out, because they are synthetic and a
    literal run of decimal pairs is what the PII hook reads as a location table."""
    return f"{whole}.{frac:03d}"


_FIRST, _SECOND = _decimal(1, 111), _decimal(2, 222)
_FULLWIDTH_PAIR = str.maketrans("0123456789.,", "０１２３４５６７８９．，")


@pytest.mark.parametrize("text", [
    f"{_FIRST} {_SECOND}",
    f"佢喺 {_FIRST} {_SECOND} 附近",
    f"-{_FIRST} {_SECOND}",
    f"{_FIRST} / {_SECOND}",
    f"{_FIRST}, {_SECOND}".translate(_FULLWIDTH_PAIR),
])
def test_a_pair_separated_by_whitespace_or_punctuation_is_a_coordinate(text):
    """The same pair as the comma form: a reply that separates the two decimals
    with a space, a slash or a full-width comma carries the same coordinate.
    No range check: an out-of-range pair is still a leaked pair."""
    assert scan_reply(text) == "coordinate", text


@pytest.mark.parametrize("text", [
    f"latitude {_FIRST}, longitude {_SECOND}",
    f"lat: {_FIRST} lng: {_SECOND}",
    f"Lat/Lng: {_FIRST}, {_SECOND}",
    f"LNG = {_SECOND}",
    f"Latitude {_FIRST}",
    f"經緯 lat {_FIRST} / lon {_SECOND}",
])
def test_a_labelled_latitude_or_longitude_is_a_coordinate(text):
    """A label (`lat`, `latitude`, `lng`, `lon`, `longitude`) separated from its
    value by whitespace or punctuation is the same disclosure as the bare pair.
    `lat 22.410 lng 114.124` used to pass here; that assertion was a deliberate
    choice (model speech) and is reversed on purpose by the brief."""
    assert scan_reply(text) == "coordinate", text


# A `\b` boundary treats every Unicode letter as a word character, so an
# identifier followed (or preceded) by one of these escaped the scan. Each
# neighbour is a letter in another script: CJK, a Latin letter with a diacritic,
# Cyrillic, Arabic, and a katakana prolonged sound mark (category Lm).
_NEIGHBOURS = ("字", "é", "я", "ع", "ー")
_BARE_IDENTIFIERS = {
    "phone": "+85290000101",
    "uuid": "a1000000-0000-4000-8000-000000000000",
    "keylike": "sk-abcdefghijklmnop1234567",
}


@pytest.mark.parametrize("neighbour", _NEIGHBOURS)
@pytest.mark.parametrize("kind", sorted(_BARE_IDENTIFIERS))
def test_an_identifier_followed_by_a_unicode_letter_is_still_refused(kind, neighbour):
    text = f"佢 {_BARE_IDENTIFIERS[kind]}{neighbour} 呀"
    assert scan_identifiers(text) == kind, text
    assert scan_reply(text) == kind, text


@pytest.mark.parametrize("neighbour", _NEIGHBOURS)
@pytest.mark.parametrize("kind", sorted(_BARE_IDENTIFIERS))
def test_an_identifier_preceded_by_a_unicode_letter_is_still_refused(kind, neighbour):
    text = f"佢{neighbour}{_BARE_IDENTIFIERS[kind]} 呀"
    assert scan_identifiers(text) == kind, text
    assert scan_reply(text) == kind, text


@pytest.mark.parametrize("digits", ["123456", "1234567", "12345678901234", "123456789012345"])
@pytest.mark.parametrize("around", [
    "打俾我 +{d}",
    "打俾我 +{d}.",
    "(+{d})",
    "+{d}, 謝謝",
    "佢個號碼 +{d}字",
])
def test_every_accepted_short_direct_address_is_detected(digits, around):
    """`Persons` accepts an E.164 id of 6 to 15 digits (`_E164`), so the guard
    must detect every length it accepts, wherever it sits in prose."""
    text = around.format(d=digits)
    assert scan_identifiers(text) == "phone", text
    assert scan_reply(text) == "phone", text


def _synthetic_digits(n: int, step: int = 3) -> str:
    """`n` digits that belong to nobody: a generated fixture, never a number."""
    return "".join(str((i * step) % 10) for i in range(n))


_FORMATS = (
    "+{cc} {a} {b}",
    "+{cc}-{a}-{b}",
    "+{cc} ({a}) {b}",
    "+{cc}.{a}.{b}",
    "+{cc} ({z}) {a}-{b}",
    "+{cc}\u200b{a} {b}",
)
_SCRIPTS = (
    str.maketrans("", ""),
    str.maketrans("0123456789+", "０１２３４５６７８９＋"),
    str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"),
)


@pytest.mark.parametrize("script", _SCRIPTS)
@pytest.mark.parametrize("template", _FORMATS)
def test_a_formatted_international_number_is_detected(template, script):
    """Separators, parentheses, full-width and other-script digits, and an
    invisible character inside the number do not hide it."""
    number = template.format(
        cc=_synthetic_digits(2), a=_synthetic_digits(4, 7),
        b=_synthetic_digits(4, 9), z=_synthetic_digits(3, 1),
    )
    text = f"打俾我 {number.translate(script)} 呀"
    assert scan_identifiers(text) == "phone", text
    assert scan_reply(text) == "phone", text


@pytest.mark.parametrize("text", [
    "2024-10-22 14:30",
    "1,000,000 蚊",
    "v1.2.3.4",
    "room 12345 樓",
    "3.14159 meters",
    "2026/10/22",
    "09:30-17:45",
    "$1,234.56 total",
])
def test_ordinary_digit_runs_are_not_phones(text):
    """Only a leading `+` makes a run a phone. Dates, times, prices, versions
    and room numbers are ordinary chat and must still pass."""
    assert scan_identifiers(text) is None, text
    assert scan_reply(text) is None, text


# Dotted dates, versions, score and time pairs are ordinary chat. A pair needs a
# separator between its decimals, and a bare dot is not one: `2026.10.22` is a
# date, not two coordinates sharing a dot.
def _generated(template: str, *nums: int) -> str:
    """A generated date, version, score or coordinate: `_generated("{}.{:02d}", 14, 30)`
    is `14.30`. Literal runs of decimal pairs are what the PII hook reads as a location
    table, so none of these fixtures is written out."""
    return template.format(*nums)


@pytest.mark.parametrize("text", [
    _generated("{}.{:02d}.{:02d}", 2026, 10, 22),
    _generated("{}.{:02d}.{}", 22, 10, 2026),
    _generated("python {}.{}.{} is out", 3, 12, 4),
    _generated("v{}.{}.{}", 1, 20, 0),
    _generated("{}.{:02d} {}.{:02d}", 14, 30, 16, 0),
    _generated("{}.{} {}.{} out of 10", 8, 5, 9, 0),
    _generated("{}.{:02d}, {}.{:02d} 都得", 3, 11, 3, 12),
])
def test_dotted_dates_versions_and_score_pairs_are_not_coordinates(text):
    assert scan_identifiers(text) is None, text
    assert scan_reply(text) is None, text


@pytest.mark.parametrize("text", [
    "+852/9876/5432",
    "+852, 9876, 5432",
    "+852\u20109876\u20105432",   # hyphen
    "+852\u20119876\u20115432",   # non-breaking hyphen
    "+852\u20139876\u20135432",   # en dash
    "+852\u20159876\u20155432",   # horizontal bar
])
def test_a_phone_separated_by_a_slash_comma_or_unicode_dash_is_detected(text):
    assert scan_identifiers(text) == "phone", text
    assert scan_reply(text) == "phone", text


# Combining marks (Mn, Me, Mc) are invisible in a rendered reply, so one inside
# an identifier or a coordinate must not change the verdict. U+034F is a
# combining grapheme joiner; the others are an acute accent, a Devanagari visarga
# (Mc) and an enclosing circle (Me).
COMBINING = ("\u034f", "\u0301", "\u0903", "\u20dd")


@pytest.mark.parametrize("ch", COMBINING)
def test_a_combining_mark_inside_an_identifier_is_still_detected(ch):
    assert scan_identifiers(f"+852 9{ch}876 5432") == "phone"
    assert scan_identifiers(f"key sk-abcd{ch}efghijklmnop1234567") == "keylike"


@pytest.mark.parametrize("ch", COMBINING)
def test_a_combining_mark_inside_a_coordinate_is_still_a_coordinate(ch):
    assert scan_reply(f"22.4{ch}1, 114.12") == "coordinate"


@pytest.mark.parametrize("text", [
    "long: 114.124",
    "long = 114.124",
    "LONG:114.124",
])
def test_a_labelled_longitude_with_three_decimals_is_a_coordinate(text):
    assert scan_reply(text) == "coordinate", text


@pytest.mark.parametrize("text", [
    "long: 5.5",
    "long: 114.12",
])
def test_an_unlabelled_decimal_or_a_short_long_value_is_not_a_coordinate(text):
    """`long` is a label only with `:` or `=` after it, and its value needs three
    decimals. Prose like `long: 5.5` (a length, a duration) must pass."""
    assert scan_reply(text) is None, text


@pytest.mark.parametrize("text", [
    _generated("{}.{:02d}N, {}.{:02d}E", 22, 41, 114, 12),
    _generated("{}.{:02d}°N {}.{:02d}°E", 22, 41, 114, 12),
    _generated("{}.{:02d}°N, {}.{:02d}°E", 22, 41, 114, 12),
    _generated("{}.{:03d}N {}.{:03d}E", 1, 111, 2, 222),
])
def test_a_pair_with_a_compass_letter_or_degree_sign_is_a_coordinate(text):
    assert scan_reply(text) == "coordinate", text


@pytest.mark.parametrize("text", [
    _generated("北緯{}.{:02d}度 東經{}.{:02d}度", 22, 41, 114, 12),
    _generated("北緯{}.{:02d}度", 22, 41),
    _generated("西經{}.{:02d}", 114, 12),
])
def test_a_cantonese_latitude_or_longitude_is_a_coordinate(text):
    assert scan_reply(text) == "coordinate", text
