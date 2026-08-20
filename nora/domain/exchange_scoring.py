"""Deterministic scoring for exchange patterns — the Plan 2 sibling of
`nora.domain.language_scoring`. Same split: an LLM classifies WHICH act a
batch of messages performs, arithmetic decides whether the room has kept
doing it. No "spread" term — a pattern's `participants` are fixed by its own
natural key (already >= 2, never aggregated), so unlike a lexeme's speaker
count there is nothing here for spread to weight.
"""

from __future__ import annotations

import math

DAY_MS = 86_400_000
HALF_LIFE_DAYS = 45.0
FADING_AFTER_DAYS = 21.0
STALE_AFTER_DAYS = 120.0

W_FREQUENCY = 0.4
W_RECENCY = 0.6


def score(*, occurrences: int, last_seen_ms: int, now_ms: int) -> float:
    """Frequency is logged so one busy exchange cannot dominate; recency is
    an exponential decay — same shape as `language_scoring.score` minus the
    spread term."""
    frequency = math.log1p(max(occurrences, 0))
    age_days = max(now_ms - last_seen_ms, 0) / DAY_MS
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
    return float(W_FREQUENCY * frequency + W_RECENCY * recency)


def state_for(*, score: float, last_seen_ms: int, now_ms: int) -> str:
    """Rendering state, not a permission to use the pattern — mirrors
    `language_scoring.state_for` exactly (same day thresholds)."""
    age_days = max(now_ms - last_seen_ms, 0) / DAY_MS
    if age_days >= STALE_AFTER_DAYS:
        return "stale"
    if age_days >= FADING_AFTER_DAYS:
        return "fading"
    return "live"
