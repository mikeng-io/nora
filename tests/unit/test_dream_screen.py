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

Each per-adaptor case carries the item shape its own tier actually returns,
so a tier whose envelope grows a new text field gets a test that already walks
it.
"""

from __future__ import annotations

import pytest

from nora.agent.dream_screen import Rejection, screen
from nora.agent.egress import scan_identifiers, scan_reply
from nora.agent.security import looks_like_coordinate
from nora.obs.metrics import Metrics

# A benign item per adaptor. Rides along in every rejection batch: if a drop
# ever became per-batch, these are what would disappear with it. `screen`
# itself is generic (it walks the item; `adaptor` is only a metric label); the
# shapes below are read off the live `accept` parsers:
# `note_candidates._parse_candidate`, `note_curator`'s verdict parse.
BENIGN = {
    "note-candidates": {
        "title": "揀邊隻 co-op game",
        "description": "四個人以上、有主線",
        "body": "傾咗幾日,未揀到",
        "closing_condition": "揀咗邊隻就閂",
    },
    "note-curation": {
        "note_id": "n1", "verdict": "open", "why": "冇人再提過",
    },
}

# The same shapes, each with a phone number written into the tier's own
# free-text field — where a `web_fetch`ed page's contents would land.
CARRIES_A_PHONE = {
    "note-candidates": {
        "title": "搵人裝冷氣",
        "description": "阿邊個個號碼係 +85290000101",
        "body": "打去問價",
        "closing_condition": "裝好就閂",
    },
    "note-curation": {
        "note_id": "n1", "verdict": "closed",
        "why": "佢留低咗 +85290000101 所以搞掂",
    },
}

# And with a raw lat/lng pair — the shape `scan_identifiers` alone does not see,
# which is why `looks_like_coordinate` is the screen's second leg.
CARRIES_A_COORDINATE = {
    "note-candidates": {
        "title": "去邊度行山",
        "description": "集合喺 22.4106, 114.1245",
        "body": "早上九點",
        "closing_condition": "行完就閂",
    },
    "note-curation": {
        "note_id": "n1", "verdict": "closed",
        "why": "最後去咗 22.4106, 114.1245 嗰度",
    },
}

#: The Dreamer adaptors whose envelope carries FREE TEXT, and therefore the
#: ones a leak can ride out on — which is every shipped adaptor.
ADAPTORS = ("note-candidates", "note-curation")




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
    kept, rejected = screen(adaptor="note-curation", items=items, metrics=metrics)
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
def test_a_model_word_is_still_kept_by_the_screen(item):
    """The screen keeps model-name words: it uses `scan_identifiers` and keeps
    every word that carries no identifier. The reply predicate passes them too
    (the model-name veto is disabled, see `egress.py`), so this asserts the
    screen's own floor.
    """
    text = " ".join(str(v) for v in item.values())
    assert scan_identifiers(text) is None, "premise: it carries no identifier"
    assert scan_reply(text) is None, (
        "the model-name veto is disabled; the reply predicate passes it"
    )

    metrics = Metrics()
    kept, rejected = screen(adaptor="note-curation", items=[item], metrics=metrics)
    assert kept == (item,)
    assert rejected == ()
    assert metrics.get("nora_dream_screened_total") == 0.0


def test_the_drop_is_counted_under_its_adaptor_and_reason():
    """A drop nobody counts looks exactly like a model that produced nothing.
    The adaptor label is what makes «which tier is being poisoned» answerable
    at all — one shared counter would only ever say «something was»."""
    metrics = Metrics()
    screen(
        adaptor="note-candidates",
        items=[
            CARRIES_A_PHONE["note-candidates"],
            CARRIES_A_COORDINATE["note-candidates"],
            BENIGN["note-candidates"],
        ],
        metrics=metrics,
    )
    assert metrics.get("nora_dream_screened_total", adaptor="note-candidates") == 2.0
    assert (
        metrics.get(
            "nora_dream_screened_total",
            adaptor="note-candidates", reason="identifier",
        ) == 1.0
    )
    assert (
        metrics.get(
            "nora_dream_screened_total",
            adaptor="note-candidates", reason="coordinate",
        ) == 1.0
    )
    assert metrics.get("nora_dream_screened_total", adaptor="note-curation") == 0.0


def test_a_string_nested_in_a_list_value_is_still_screened():
    """A screen that reads only top-level `str` values is one an adaptor
    bypasses the first time it accepts a list field — silently, because the
    kept set looks identical either way."""
    metrics = Metrics()
    nested = {"lexeme": "阿邊個", "aliases": ["佢", "+85290000101"]}
    kept, rejected = screen(
        adaptor="note-curation",
        items=[nested, BENIGN["note-curation"]],
        metrics=metrics,
    )
    assert kept == (BENIGN["note-curation"],)
    assert rejected == (Rejection(index=0, reason="identifier", path="aliases[1]"),)
    assert metrics.get("nora_dream_screened_total", adaptor="note-curation") == 1.0


def test_a_string_nested_two_levels_down_is_still_screened():
    """The envelope is model-authored, so nothing bounds its depth. One level
    of recursion is not recursion."""
    metrics = Metrics()
    deep = {"lexeme": "個位", "evidence": {"quotes": ["幾時到", "22.4106, 114.1245"]}}
    kept, rejected = screen(adaptor="note-curation", items=[deep], metrics=metrics)
    assert kept == ()
    assert rejected == (
        Rejection(index=0, reason="coordinate", path="evidence.quotes[1]"),
    )


def test_an_empty_batch_returns_two_empty_tuples():
    """`nothing_found` is an ordinary R-27 outcome, not an error: an adaptor
    that looked and found nothing must not have to special-case the screen."""
    metrics = Metrics()
    kept, rejected = screen(adaptor="note-curation", items=[], metrics=metrics)
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
    kept, rejected = screen(adaptor="note-candidates", items=items, metrics=metrics)
    assert kept == ()
    assert [r.reason for r in rejected] == ["identifier", "identifier"]


def test_a_rejection_never_carries_the_offending_text():
    """The log line has to be useful without copying somebody's number out of
    a store that refused to hold it and into a log file that holds it forever."""
    metrics = Metrics()
    _, rejected = screen(
        adaptor="note-candidates",
        items=[CARRIES_A_PHONE["note-candidates"]],
        metrics=metrics,
    )
    assert len(rejected) == 1
    rendered = repr(rejected[0])
    assert "+85290000101" not in rendered
    # The FIELD is named so the drop is diagnosable, while the value is not.
    # `description` is where the fixture hides the number — the free-text field
    # a `web_fetch`ed page's contents would land in.
    assert rejected[0].path == "description"


# --- what the sink screen refuses beyond the reply floor -----------------------
#
# These four map onto the two existing reason labels, so the metric's
# vocabulary does not move. `scan_identifiers` (the reply's R-18 floor) and
# `looks_like_coordinate` pass every one of them, which the premise assertion
# pins — a test whose drop the old predicates already made proves nothing
# about the new patterns.


@pytest.mark.parametrize(("text", "reason"), [
    ("佢話有事就 email miles.novak@example.com", "identifier"),
    ("Discord user 123456789012345678 joined", "identifier"),
    ("where_is: lat 22.4106 lng 114.1245", "coordinate"),
    ("where_is returned {'lat': 22.4106, 'lng': 114.1245}", "coordinate"),
], ids=["email", "18-digit-id", "labelled-lat-lng", "where_is-dict"])
def test_the_sink_screen_refuses_what_the_reply_floor_does_not(text, reason):
    assert scan_identifiers(text) is None and not looks_like_coordinate(text), (
        "premise: the reply floor alone passes this"
    )
    metrics = Metrics()
    item = {"title": "搵佢", "body": text}
    kept, rejected = screen(
        adaptor="note-candidates", items=[item, BENIGN["note-candidates"]],
        metrics=metrics,
    )
    assert kept == (BENIGN["note-candidates"],)
    assert [(r.index, r.reason, r.path) for r in rejected] == [(0, reason, "body")]
    assert metrics.get(
        "nora_dream_screened_total", adaptor="note-candidates", reason=reason) == 1.0


@pytest.mark.parametrize("text", [
    "佢哋而家喺尖沙咀等緊，我答應咗聽日十點前覆 Miles 條路線，仲未覆",
    "約咗 2026-09-22 10:00 喺中環見",
    "嗰部機賣 1234567 蚊",
    "升咗去 version 3.14159 之後就冇事",
], ids=["cantonese-position", "date", "price", "version"])
def test_ordinary_position_prose_numbers_and_dates_are_kept(text):
    """The positive controls for the test above. A screen that refused every
    long number or every decimal would pass it and delete real positions: a
    date and time, a seven-digit price, a dotted version. `_LONG_ID` starts
    at 15 digits and refuses a digit run touching a `.`, and the coordinate
    pattern needs its `lat`/`lng` labels, for exactly these."""
    metrics = Metrics()
    item = {"standing": text}
    kept, rejected = screen(adaptor="debrief", items=[item], metrics=metrics)
    assert kept == (item,)
    assert rejected == ()
    assert metrics.get("nora_dream_screened_total") == 0.0
