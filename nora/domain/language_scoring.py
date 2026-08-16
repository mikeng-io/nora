"""Deterministic scoring for the room lexicon. NO model call lives here.

The split is the whole design: an LLM decides what a candidate MEANS, arithmetic
decides whether the room has adopted it. Asking a model «should Nora know this
word yet» would hand a policy decision to a stochastic process and make the
lexicon unreproducible from its own evidence.

Speaker SPREAD is weighted above raw frequency deliberately. One person saying a
word forty times is a personal tic; four people saying it eight times each is the
room's vocabulary, and the second is the thing worth telling her about.

**Lives in `nora.domain`, not `nora.language` where Task 5 first wrote it.**
Task 7's `nora.infra.sql.language.SqlLanguageStore.rescore` is the thing that
actually APPLIES this arithmetic — it is the one place the aggregated
`occurrence_count`/`distinct_speakers`/`last_seen_at` a rescore computes meets
the decision of what to store — and `nora.infra` sits BELOW `nora.language` in
the layers contract (`pyproject.toml`'s `layered architecture` contract; verified
empirically with `uv run lint-imports` while wiring this module in, which
reports `nora.infra is not allowed to import nora.language`). A lower layer may
never import a higher one, which is the exact reason `LexemeCandidate` and
`RoomLexeme`/`RoomLanguage`/`LexemeExample` already live here rather than in
`nora.language` or `nora.infra` (see `nora.domain.models`'s own docstring on
`LexemeCandidate`) — this module is the same relocation for the same reason,
one task later. `nora/language/scoring.py` now re-exports these two names for
any caller that still reaches them through their original import path; the
module docstring there explains the split.
"""

from __future__ import annotations

import math

DAY_MS = 86_400_000
HALF_LIFE_DAYS = 45.0
FADING_AFTER_DAYS = 21.0
STALE_AFTER_DAYS = 120.0

W_FREQUENCY = 0.4
W_SPREAD = 2.0
W_RECENCY = 0.6


def score(*, occurrences: int, distinct_speakers: int,
          last_seen_ms: int, now_ms: int) -> float:
    """Frequency is logged so a single loud thread cannot dominate; spread is
    linear-ish and weighted hardest; recency is an exponential decay."""
    frequency = math.log1p(max(occurrences, 0))
    spread = math.log1p(max(distinct_speakers, 0))
    age_days = max(now_ms - last_seen_ms, 0) / DAY_MS
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
    return float(W_FREQUENCY * frequency + W_SPREAD * spread + W_RECENCY * recency)


def state_for(*, score: float, last_seen_ms: int, now_ms: int) -> str:
    """Rendering state, NOT a permission to use the word — there is no
    per-word generation gate in V1 because there is no per-word generation
    decision. `stale` entries stop rendering entirely."""
    age_days = max(now_ms - last_seen_ms, 0) / DAY_MS
    if age_days >= STALE_AFTER_DAYS:
        return "stale"
    if age_days >= FADING_AFTER_DAYS:
        return "fading"
    return "live"
