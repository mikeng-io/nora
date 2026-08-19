"""Tests for `nora.domain.exchange_scoring` — mirrors
`tests/unit/test_language_scoring.py`, minus the spread test (this module has
no spread term; see its own module docstring)."""

from nora.domain import exchange_scoring as scoring

NOW = 0
DAY_MS = 86_400_000


def test_more_occurrences_scores_higher():
    few = scoring.score(occurrences=2, last_seen_ms=NOW, now_ms=NOW)
    many = scoring.score(occurrences=40, last_seen_ms=NOW, now_ms=NOW)
    assert many > few


def test_recency_decays_a_pattern_nobody_repeats():
    fresh = scoring.score(occurrences=10, last_seen_ms=NOW, now_ms=NOW)
    old = scoring.score(occurrences=10, last_seen_ms=NOW - 90 * DAY_MS, now_ms=NOW)
    assert old < fresh


def test_state_falls_through_live_then_fading_then_stale():
    assert scoring.state_for(score=0.9, last_seen_ms=NOW, now_ms=NOW) == "live"
    assert scoring.state_for(
        score=0.9, last_seen_ms=NOW - 30 * DAY_MS, now_ms=NOW) == "fading"
    assert scoring.state_for(
        score=0.9, last_seen_ms=NOW - 200 * DAY_MS, now_ms=NOW) == "stale"
