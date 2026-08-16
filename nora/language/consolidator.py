"""Task 8: `LanguageConsolidator` — the thin sequencing shim over Task 6's
`LexemeObserver` and Task 7's `SqlLanguageStore`. Satisfies
`nora.domain.ports.RoomConsolidator` (see that Protocol's own docstring for
why `consolidate` is FIXED to mean OBSERVATIONS APPENDED, not messages seen or
lexemes discovered — this class must not reinterpret that).

This class owns ONLY the order — extract, append, rescore — and nothing else.
The one model call lives in `LexemeObserver`; durability and idempotency live
in `SqlLanguageStore`; scoring lives in `nora.domain.language_scoring` (reached
through `SqlLanguageStore.rescore`, never re-derived here). CLAUDE.md's
comprehend/measure/serialize spine names why the split is drawn exactly here:
`LexemeObserver` COMPREHENDS a batch into candidates and `SqlLanguageStore`
MEASURES/SERIALIZES them into durable, scored state; this module is neither —
it is the fixed sequence a `RoomConsolidator` caller can rely on, and adding
any decision of its own (re-ranking, filtering, retrying) would duplicate a
policy one of its two collaborators already owns.

`rescore` runs UNCONDITIONALLY — even for a genuinely empty batch. Decay is
time-driven: `dream_loop`'s 24h firing condition exists specifically to fade a
DORMANT room's lexicon, and it reaches `consolidate` with `lag == 0`, i.e. an
empty batch. Skipping `rescore` there (an early `return 0` before either
collaborator ran) would make that whole path a no-op and leave a stale lexicon
rendering as `live` forever, describing a slang era that ended months ago —
exactly the failure `SqlLanguageStore.rescore`'s own module docstring argues
against. Only the OBSERVER call and the APPEND are skipped for an empty batch
(no model call, no store round trip is worth making over zero messages) — the
rescore is not.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from nora.domain.models import TranscriptRow
from nora.infra.sql.language import SqlLanguageStore
from nora.language.observer import LexemeObserver


def _now_ms() -> int:
    return int(time.time() * 1000)


class LanguageConsolidator:
    """Satisfies `nora.domain.ports.RoomConsolidator`.

    Two collaborators through `__init__`, no hooks: the observer owns the one
    model call, the store owns durability. This class owns only the order —
    extract, append, rescore — and returns what was appended.
    """

    name = "language"

    def __init__(self, *, observer: LexemeObserver, store: SqlLanguageStore) -> None:
        self._observer = observer
        self._store = store

    async def consolidate(
        self, *, scope_id: str, batch: Sequence[TranscriptRow]
    ) -> int:
        appended = 0
        if batch:
            candidates = await self._observer.observe(batch=batch)
            appended = await self._store.append_observations(
                scope_id=scope_id, observations=candidates
            )
        # Rescore UNCONDITIONALLY. Decay is time-driven, and dream_loop's 24h
        # firing exists to fade a dormant room's lexicon — it arrives here
        # with an EMPTY batch, so an early return would make that whole path
        # a no-op and leave a stale lexicon rendering as `live` forever.
        await self._store.rescore(scope_id=scope_id, now_ms=_now_ms())
        return appended
