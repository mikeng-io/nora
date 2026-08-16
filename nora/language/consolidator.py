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

`rescore` running even when nothing was appended is `SqlLanguageStore.rescore`'s
own documented shape (decay is time-driven — a scope with no new evidence must
still be able to let an old word fade), not a decision made here; this class
just calls it unconditionally after a non-empty batch, exactly as an empty
batch's early return means neither collaborator is touched for zero messages
(no model call, no store round trip is worth making over nothing).
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
        if not batch:
            return 0
        candidates = await self._observer.observe(batch=batch)
        appended = await self._store.append_observations(
            scope_id=scope_id, observations=candidates
        )
        # Rescore even when nothing was appended: decay is time-driven, so a
        # quiet scope must still be able to let a word fade. `dream_loop`'s
        # 24h firing depends on this being unconditional.
        await self._store.rescore(scope_id=scope_id, now_ms=_now_ms())
        return appended
