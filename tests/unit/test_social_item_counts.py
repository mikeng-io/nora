"""`nora.domain.social_stream.ItemCounts` and `counts_changed`: what one
read's counts say, and whether they are a new `social_item_count` row
(design/cognition-loop/22-social-stream.md §5). Pure."""

from __future__ import annotations

from datetime import UTC, datetime

from nora.domain.social_stream import ItemCounts, RecordedCounts, counts_changed

AT = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)


def latest(likes=31, comments=4, reposts=None, hidden=False) -> RecordedCounts:
    return RecordedCounts(likes, comments, reposts, hidden, AT)


def test_hidden_likes_are_none_whatever_was_sent():
    assert ItemCounts(likes=31, comments=4, likes_hidden=True).likes is None
    assert ItemCounts(likes=31, likes_hidden=False).likes == 31
    assert ItemCounts(likes=31).likes == 31, "an unknown switch hides nothing"


def test_a_read_is_known_by_a_count_or_hidden_likes_only():
    assert not ItemCounts().known
    assert not ItemCounts(likes_hidden=False).known
    assert ItemCounts(likes_hidden=True).known
    assert ItemCounts(comments=0).known, "0 is a count, not unknown"


def test_the_first_known_read_of_an_item_is_a_row_and_nothing_known_never_is():
    assert counts_changed(ItemCounts(likes=0), None)
    assert not counts_changed(ItemCounts(), None)
    assert not counts_changed(ItemCounts(), latest())


def test_only_a_value_the_read_carries_is_compared():
    assert not counts_changed(ItemCounts(31, 4), latest())
    assert not counts_changed(ItemCounts(31, None, None), latest()), (
        "a count fallen back to unknown is no change"
    )
    assert counts_changed(ItemCounts(32, None), latest())
    assert counts_changed(ItemCounts(31, 4, 1), latest()), "a repost count now known"


def test_the_switch_is_compared_when_the_read_carries_it():
    assert counts_changed(ItemCounts(31, 4, likes_hidden=True), latest())
    assert counts_changed(ItemCounts(31, 4, likes_hidden=False), latest(None, 4, hidden=True))
    assert not counts_changed(ItemCounts(None, 4, likes_hidden=None), latest(None, 4, hidden=True))
