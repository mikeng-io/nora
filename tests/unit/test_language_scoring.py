"""Tests for deterministic lexeme scoring.

`nora.language.scoring` (Task 5) moved to `nora.domain.language_scoring` in
Task 7 — `nora.infra.sql.language.SqlLanguageStore.rescore` is the one place
that actually applies this arithmetic, and `nora.infra` cannot import
`nora.language` (the layers contract). This import must point at the module
`score()`'s code actually reads its globals from, not a re-export: this test
patches module-level constants with `mock.patch.object(scoring, ...)`, which
only affects computation for the DEFINING module."""

from unittest import mock

from nora.domain import language_scoring as scoring

NOW = 0
DAY_MS = 86_400_000


def test_a_word_many_people_use_outscores_a_word_one_person_repeats():
    """Speaker SPREAD, not raw frequency, is what makes a word the room's.
    One person saying `firm` forty times is a personal tic; four people saying
    it eight times each is a lexeme.

    The named weights W_FREQUENCY and W_SPREAD are the ONLY controls of relative
    influence. Exchanging them inverts the ordering: this test proves that
    SPREAD truly dominates with the shipped weights, not by accident of the
    formula structure."""
    # With shipped weights: spread dominates
    tic = scoring.score(occurrences=40, distinct_speakers=1,
                        last_seen_ms=NOW, now_ms=NOW)
    shared = scoring.score(occurrences=32, distinct_speakers=4,
                           last_seen_ms=NOW, now_ms=NOW)
    assert shared > tic

    # When weights are swapped: frequency dominates, and tic should win
    with mock.patch.object(scoring, 'W_FREQUENCY', 2.0), \
         mock.patch.object(scoring, 'W_SPREAD', 0.4):
        tic_inverted = scoring.score(occurrences=40, distinct_speakers=1,
                                     last_seen_ms=NOW, now_ms=NOW)
        shared_inverted = scoring.score(occurrences=32, distinct_speakers=4,
                                        last_seen_ms=NOW, now_ms=NOW)
        assert tic_inverted > shared_inverted, \
            "When W_FREQUENCY and W_SPREAD are inverted, frequency should dominate"


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
