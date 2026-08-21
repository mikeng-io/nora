"""The Dreamer's sink screen — the second path into the prompt.

Adaptor output is read back into every later turn, so an identifier or a raw
coordinate that survives a `propose` is as exposed as one Nora said out loud.
These tests hold four properties that are each easy to lose:

* the drop is **per item**, not per batch — one poisoned item never costs its
  siblings, so every rejection test carries a benign sibling and asserts it
  survived;
* the predicate is `scan_identifiers`, **not** `scan_reply` — a lexeme about
  the word 「haiku」 is exactly what this room coins, and the reply predicate
  would delete it while looking like a security win;
* the walk is **recursive** — a string one level down inside a list is still
  screened, or an adaptor bypasses the screen by nesting a field;
* the drop is **counted**, with the adaptor label, because a drop nobody counts
  is indistinguishable from a model that never produced one.

The per-adaptor cases below are the keystone's «a named test per adaptor», with
the fact adaptor non-optional per the reflection spec's §14.10. Each carries the
item shape its own tier actually returns, so a tier whose envelope grows a new
text field gets a test that already walks it.
"""

from __future__ import annotations

import pytest

from nora.agent.dream_screen import Rejection, screen
from nora.agent.egress import scan_identifiers, scan_reply
from nora.obs.metrics import Metrics

# A benign item per adaptor. Rides along in every rejection batch: if a drop
# ever became per-batch, these are what would disappear with it.
BENIGN = {
    "room_language": {"lexeme": "落閘", "sense": "收工唔再傾嘢", "confidence": 0.8},
    "room_exchange": {"prompt": "食咩好", "reply": "隨便", "weight": 2},
    "room_portrait": {"facet": "rhythm", "observation": "夜晚先郁"},
    "facts": {"subject": "miles", "predicate": "drinks", "object": "齋啡"},
}

# The same four shapes, each with a phone number written into the tier's own
# free-text field — where a `web_fetch`ed page's contents would land.
CARRIES_A_PHONE = {
    "room_language": {"lexeme": "阿邊個", "sense": "個號碼係 +85290000101 嗰位"},
    "room_exchange": {"prompt": "點搵佢", "reply": "打 +85290000101 啦"},
    "room_portrait": {"facet": "contact", "observation": "reachable at +85290000101"},
    "facts": {"subject": "miles", "predicate": "phone", "object": "+85290000101"},
}

# And with a raw lat/lng pair — the shape `scan_identifiers` alone does not see,
# which is why `looks_like_coordinate` is the screen's second leg.
CARRIES_A_COORDINATE = {
    "room_language": {"lexeme": "個位", "sense": "即係 22.4106, 114.1245 嗰度"},
    "room_exchange": {"prompt": "喺邊", "reply": "22.4106,114.1245"},
    "room_portrait": {"facet": "haunt", "observation": "usually at 22.4106, 114.1245"},
    "facts": {"subject": "miles", "predicate": "home", "object": "22.4106, 114.1245"},
}

ADAPTORS = ("room_language", "room_exchange", "room_portrait", "facts")


@pytest.mark.parametrize("adaptor", ADAPTORS)
def test_an_item_carrying_a_phone_number_never_reaches_the_kept_set(adaptor):
    """R-18 on the sink. The benign sibling in the same batch is the half that
    proves the drop is per item rather than per run."""
    metrics = Metrics()
    kept, rejected = screen(
        adaptor=adaptor,
        items=[CARRIES_A_PHONE[adaptor], BENIGN[adaptor]],
        metrics=metrics,
    )
    assert kept == (BENIGN[adaptor],)
    assert [(r.index, r.reason) for r in rejected] == [(0, "identifier")]


@pytest.mark.parametrize("adaptor", ADAPTORS)
def test_an_item_carrying_a_coordinate_never_reaches_the_kept_set(adaptor):
    """R-28 on the sink. `scan_identifiers` passes a lat/lng pair — it matches
    no `+`, no UUID, no key prefix — so a screen built on it alone would write
    a machine position into a store that renders into every later turn."""
    metrics = Metrics()
    kept, rejected = screen(
        adaptor=adaptor,
        items=[BENIGN[adaptor], CARRIES_A_COORDINATE[adaptor]],
        metrics=metrics,
    )
    assert kept == (BENIGN[adaptor],)
    assert [(r.index, r.reason) for r in rejected] == [(1, "coordinate")]


def test_a_benign_batch_is_kept_whole_and_counts_nothing():
    metrics = Metrics()
    items = [BENIGN[a] for a in ADAPTORS]
    kept, rejected = screen(adaptor="room_language", items=items, metrics=metrics)
    assert kept == tuple(items)
    assert rejected == ()
    assert metrics.get("nora_dream_screened_total") == 0.0


@pytest.mark.parametrize(
    "item",
    [
        {"lexeme": "haiku", "sense": "個 group 叫首三行詩做 haiku"},
        {"lexeme": "opus", "sense": "大工程叫 opus"},
        {"prompt": "sonnet 定 haiku", "reply": "梗係 haiku"},
        {"subject": "miles", "predicate": "listens_to", "object": "gemini 星座 podcast"},
    ],
)
def test_a_word_scan_reply_would_refuse_is_still_kept(item):
    """THE test that pins `scan_identifiers` over `scan_reply`, and the reason
    it cannot be flipped back by accident.

    Each item here is refused by `scan_reply` — asserted, so this test fails
    loudly if `_MODELNAME` is ever narrowed and the pinning becomes vacuous —
    and carries no identifier at all. A screen built on the reply predicate
    would silently delete legitimate room language for a word that was never a
    disclosure; the rule there is «she does not SAY it», not «the string never
    exists in a store».
    """
    text = " ".join(str(v) for v in item.values())
    assert scan_reply(text) == "model", "premise: the reply predicate refuses this"
    assert scan_identifiers(text) is None, "premise: it carries no identifier"

    metrics = Metrics()
    kept, rejected = screen(adaptor="room_language", items=[item], metrics=metrics)
    assert kept == (item,)
    assert rejected == ()
    assert metrics.get("nora_dream_screened_total") == 0.0


def test_the_drop_is_counted_under_its_adaptor_and_reason():
    """A drop nobody counts looks exactly like a model that produced nothing.
    The adaptor label is what makes «which tier is being poisoned» answerable
    at all — one shared counter would only ever say «something was»."""
    metrics = Metrics()
    screen(
        adaptor="facts",
        items=[CARRIES_A_PHONE["facts"], CARRIES_A_COORDINATE["facts"], BENIGN["facts"]],
        metrics=metrics,
    )
    assert metrics.get("nora_dream_screened_total", adaptor="facts") == 2.0
    assert (
        metrics.get("nora_dream_screened_total", adaptor="facts", reason="identifier") == 1.0
    )
    assert (
        metrics.get("nora_dream_screened_total", adaptor="facts", reason="coordinate") == 1.0
    )
    assert metrics.get("nora_dream_screened_total", adaptor="room_language") == 0.0


def test_a_string_nested_in_a_list_value_is_still_screened():
    """A screen that reads only top-level `str` values is one an adaptor
    bypasses the first time it accepts a list field — silently, because the
    kept set looks identical either way."""
    metrics = Metrics()
    nested = {"lexeme": "阿邊個", "aliases": ["佢", "+85290000101"]}
    kept, rejected = screen(
        adaptor="room_language", items=[nested, BENIGN["room_language"]], metrics=metrics
    )
    assert kept == (BENIGN["room_language"],)
    assert rejected == (Rejection(index=0, reason="identifier", path="aliases[1]"),)
    assert metrics.get("nora_dream_screened_total", adaptor="room_language") == 1.0


def test_a_string_nested_two_levels_down_is_still_screened():
    """The envelope is model-authored, so nothing bounds its depth. One level
    of recursion is not recursion."""
    metrics = Metrics()
    deep = {"lexeme": "個位", "evidence": {"quotes": ["幾時到", "22.4106, 114.1245"]}}
    kept, rejected = screen(adaptor="room_language", items=[deep], metrics=metrics)
    assert kept == ()
    assert rejected == (
        Rejection(index=0, reason="coordinate", path="evidence.quotes[1]"),
    )


def test_an_empty_batch_returns_two_empty_tuples():
    """`nothing_found` is an ordinary R-27 outcome, not an error: an adaptor
    that looked and found nothing must not have to special-case the screen."""
    metrics = Metrics()
    kept, rejected = screen(adaptor="room_language", items=[], metrics=metrics)
    assert kept == ()
    assert rejected == ()
    assert metrics.get("nora_dream_screened_total") == 0.0


def test_a_uuid_and_a_key_shaped_string_are_identifiers_too():
    """`scan_identifiers` is three patterns, not one. An ACI is an identifier
    only Nora ever sees — which is exactly why persisting one would be hers to
    have leaked — and a key-shaped string in a store that renders into the
    prompt is a credential the next turn reads back."""
    metrics = Metrics()
    items = [
        {"lexeme": "x", "sense": "a1000000-0000-4000-8000-000000000000"},
        {"lexeme": "y", "sense": "sk-abcdefghijklmnop1234567"},
    ]
    kept, rejected = screen(adaptor="facts", items=items, metrics=metrics)
    assert kept == ()
    assert [r.reason for r in rejected] == ["identifier", "identifier"]


def test_a_rejection_never_carries_the_offending_text():
    """The log line has to be useful without copying somebody's number out of
    a store that refused to hold it and into a log file that holds it forever."""
    metrics = Metrics()
    _, rejected = screen(
        adaptor="facts", items=[CARRIES_A_PHONE["facts"]], metrics=metrics
    )
    assert len(rejected) == 1
    rendered = repr(rejected[0])
    assert "+85290000101" not in rendered
    assert rejected[0].path == "object"
