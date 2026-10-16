"""`nora.domain.social_stream.decide` — what a probe makes due.

The marker is the probe's own `latest_reel_media`, never a frame's `taken_at`,
and an absent count is never read as "unchanged".
"""

from __future__ import annotations

from nora.domain.social_stream import Counts, Due, Probe, decide

COUNTS = Counts(media_count=10, total_clips_count=3)


def probe(
    marker: int | None = 100, media: int | None = 10, clips: int | None = 3,
    *, available: bool = True,
) -> Probe:
    return Probe(
        latest_reel_media=marker, media_count=media, total_clips_count=clips,
        counts_available=available,
    )


def test_a_new_marker_makes_the_story_read_due() -> None:
    due = decide(probe(marker=200), marker=100, last_counts=COUNTS)
    assert due == Due(story_due=True, items_due=False, partial=False, count_fell=False)
    assert due.outcome == "story_due"


def test_the_same_marker_is_not_due_even_after_a_read_that_found_nothing() -> None:
    # A completed read that returned `found: false` advances the marker like
    # any other, so the stored marker equals the probe and nothing is due.
    due = decide(probe(marker=100), marker=100, last_counts=COUNTS)
    assert not due.story_due
    assert due.outcome == "unchanged"


def test_no_marker_yet_makes_a_present_story_due() -> None:
    assert decide(probe(marker=100), marker=None, last_counts=COUNTS).story_due


def test_a_zero_marker_is_no_story_and_nothing_to_read() -> None:
    assert not decide(probe(marker=0), marker=None, last_counts=COUNTS).story_due
    assert not decide(probe(marker=0), marker=100, last_counts=COUNTS).story_due


def test_a_rise_in_either_count_makes_the_list_read_due() -> None:
    assert decide(probe(media=11), marker=100, last_counts=COUNTS).outcome == "items_due"
    assert decide(probe(clips=4), marker=100, last_counts=COUNTS).items_due


def test_a_story_and_a_rise_are_both_due() -> None:
    due = decide(probe(marker=200, media=11), marker=100, last_counts=COUNTS)
    assert due.story_due and due.items_due


def test_counts_never_recorded_make_the_list_read_due() -> None:
    assert decide(probe(), marker=100, last_counts=None).items_due


def test_unavailable_counts_are_partial_and_the_story_comparison_stands() -> None:
    due = decide(probe(marker=200, media=None, clips=None, available=False),
                 marker=100, last_counts=COUNTS)
    assert due.partial and due.story_due and not due.items_due
    quiet = decide(probe(marker=100, media=None, clips=None, available=False),
                   marker=100, last_counts=COUNTS)
    assert quiet.partial and not quiet.story_due and not quiet.items_due
    assert quiet.outcome == "partial"


def test_an_absent_count_is_never_unchanged_even_when_the_flag_says_available() -> None:
    due = decide(probe(media=None), marker=100, last_counts=COUNTS)
    assert due.partial and not due.items_due


def test_a_fallen_count_is_recorded_and_triggers_nothing() -> None:
    due = decide(probe(media=9), marker=100, last_counts=COUNTS)
    assert due == Due(story_due=False, items_due=False, partial=False, count_fell=True)
    assert due.outcome == "count_fell"


def test_one_count_falling_while_the_other_rises_is_both() -> None:
    due = decide(probe(media=9, clips=4), marker=100, last_counts=COUNTS)
    assert due.items_due and due.count_fell


def test_equal_counts_are_unchanged() -> None:
    assert decide(probe(), marker=100, last_counts=COUNTS).outcome == "unchanged"
