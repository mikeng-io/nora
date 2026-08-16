"""Tests for deterministic lexeme scoring."""

from nora.language import scoring

NOW = 0
DAY_MS = 86_400_000


def test_a_word_many_people_use_outscores_a_word_one_person_repeats():
    """Speaker SPREAD, not raw frequency, is what makes a word the room's.
    One person saying `firm` forty times is a personal tic; four people saying
    it eight times each is a lexeme."""
    tic = scoring.score(occurrences=40, distinct_speakers=1,
                        last_seen_ms=NOW, now_ms=NOW)
    shared = scoring.score(occurrences=32, distinct_speakers=4,
                           last_seen_ms=NOW, now_ms=NOW)
    assert shared > tic


def test_recency_decays_a_word_nobody_says_any_more():
    fresh = scoring.score(occurrences=10, distinct_speakers=3,
                          last_seen_ms=NOW, now_ms=NOW)
    old = scoring.score(occurrences=10, distinct_speakers=3,
                        last_seen_ms=NOW - 90 * DAY_MS, now_ms=NOW)
    assert old < fresh


def test_state_falls_through_live_then_fading_then_stale():
    assert scoring.state_for(score=0.9, last_seen_ms=NOW, now_ms=NOW) == "live"
    assert scoring.state_for(score=0.9, last_seen_ms=NOW - 30 * DAY_MS,
                             now_ms=NOW) == "fading"
    assert scoring.state_for(score=0.9, last_seen_ms=NOW - 200 * DAY_MS,
                             now_ms=NOW) == "stale"
