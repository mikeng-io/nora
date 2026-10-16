"""`rollup_read`'s interval and its row's place words: the reading of what a
model writes, with no store.

An interval that cannot be read is an error, never «everything»: an answer to
a question nobody asked looks like an answer. Dates are Hong Kong days.
"""

from __future__ import annotations

import pytest

from nora.agent.rollup_read import iso, parse_interval, render_row
from nora.domain.rollup_read import RollupReadRow

HKT_MIDNIGHT = 1_790_000_000_000 - (1_790_000_000_000 % 86_400_000) - 8 * 3_600_000
DAY = 86_400_000


def test_a_date_is_that_hong_kong_day():
    start, end = parse_interval("2026-09-21")
    assert iso(start) == "2026-09-21T00:00:00+08:00"
    assert end - start == DAY


def test_a_range_of_dates_includes_the_whole_end_day():
    start, end = parse_interval("2026-09-21/2026-09-22")
    assert (iso(start), iso(end)) == ("2026-09-21T00:00:00+08:00", "2026-09-23T00:00:00+08:00")


def test_a_range_of_times_is_exactly_that_range_and_an_offset_is_honoured():
    start, end = parse_interval("2026-09-21T18:00:00+08:00/2026-09-21T20:30:00+08:00")
    assert end - start == 150 * 60_000
    utc_start, _ = parse_interval("2026-09-21T10:00:00+00:00/2026-09-21T11:00:00Z")
    assert iso(utc_start) == "2026-09-21T18:00:00+08:00"
    naive, _ = parse_interval("2026-09-21T18:00/2026-09-21T19:00")     # no offset: Hong Kong
    assert iso(naive) == "2026-09-21T18:00:00+08:00"


def test_a_lone_moment_is_that_instant():
    start, end = parse_interval("2026-09-21T18:00:00+08:00")
    assert end - start == 1


@pytest.mark.parametrize("text", [
    "", "  ", "/", "2026-09-21/", "/2026-09-21", "last week", "2026-13-40",
    "2026-09-22/2026-09-21", "2026-09-21T18:00/2026-09-21T18:00",
    "2026-09-21/2026-09-22/2026-09-23", "99999-01-01",
])
def test_anything_else_is_refused(text):
    with pytest.raises(ValueError):
        parse_interval(text)


def _stay(**fields):
    return RollupReadRow(
        kind="stay", subject="a", valid_from_ms=HKT_MIDNIGHT, valid_to_ms=HKT_MIDNIGHT + 3_600_000,
        fields={"place_kind": None, "place_label": None, "place_saved": False} | fields,
    )


@pytest.mark.parametrize("fields, words", [
    ({"place_kind": "home", "place_label": "Elm Road"}, "home near Elm Road"),
    ({"place_kind": "work", "place_label": "Elm Tower", "place_saved": True}, "Elm Tower"),
    ({"place_kind": "unlabeled", "place_label": "Elm Road"}, "an unlabelled place near Elm Road"),
    ({"place_kind": None, "place_label": None}, "an unlabelled place"),
    ({"place_kind": "home", "place_label": None}, "home"),
])
def test_a_place_is_words_and_an_unlabelled_one_says_so(fields, words):
    row = render_row(_stay(**fields), name_of=lambda s: "Ada", room_label=lambda k: k)
    assert row["place"] == words
    assert row["who"] == "Ada" and row["mins"] == 60
    assert not {"lat", "lng"} & set(row)


def test_a_row_carries_the_key_a_note_cites_it_by():
    row = render_row(_stay(), name_of=lambda s: "Ada", room_label=lambda k: k)
    assert (row["kind"], row["subject"]) == ("stay", "a")
    assert row["from"] == iso(HKT_MIDNIGHT) and row["to"] == iso(HKT_MIDNIGHT + 3_600_000)


# ---- two rooms under one label keep both counts -----------------------------------

def test_two_rooms_with_one_label_are_both_counted_never_one_overwriting_the_other():
    from nora.agent.rollup_read import _rooms_by_label

    labels = {"signal:a": "Family", "signal:b": "Family", "signal:c": "another room",
              "signal:d": "another room", "signal:e": "Work"}
    out = _rooms_by_label(
        {"signal:a": 3, "signal:b": 5, "signal:c": 1, "signal:d": 2, "signal:e": 7},
        labels.__getitem__,
    )
    assert out == {"Family": 3, "Family (2)": 5, "another room": 1,
                   "another room (2)": 2, "Work": 7}
    assert sum(out.values()) == 18
