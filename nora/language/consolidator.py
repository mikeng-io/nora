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
rescore is not. An unparseable observer response skips it too, but for the
OPPOSITE reason — see `ExtractionUnparseableError` below.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from nora.domain.models import TranscriptRow
from nora.infra.sql.language import SqlLanguageStore
from nora.language.observer import LexemeObserver


def _now_ms() -> int:
    return int(time.time() * 1000)


class ExtractionUnparseableError(Exception):
    """Raised by `consolidate` when `LexemeObserver.observe` reports
    `parsed=False` for a NON-EMPTY batch — the model's response for this
    cycle did not parse as the expected JSON shape at all, as distinct from a
    well-formed response that genuinely found nothing (`observer.py`'s own
    `ObservationResult` docstring). Raising, rather than returning `0`
    exactly as a genuine empty-extraction would, is deliberate: `0` is
    already `RoomConsolidator.consolidate`'s fixed meaning for "observations
    appended", and overloading it to also mean "could not tell" would make it
    lie for BOTH cases at once. `_dream_once` (`nora.app.runtime`) already
    treats a raising consolidator as a failed cycle — logged, counted
    (`nora_errors_total{stage="dream"}`), backed off, and load-bearingly NOT
    advancing the watermark (`tests/unit/test_runtime.py::
    test_the_watermark_advances_only_after_observations_are_committed`'s own
    crash-shaped half) — so raising here reuses that already-proven path
    rather than inventing a second way to say "do not trust this batch".
    Skipping `rescore` for this one cycle (this exception fires before the
    unconditional `rescore` call below is ever reached) is an accepted side
    effect, not a goal: decay resumes the next time this scope's cycle
    actually completes, and a model broken badly enough to be unparseable is
    not a state worth rescoring through anyway.
    """


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
            result = await self._observer.observe(batch=batch)
            if not result.parsed:
                # See `ExtractionUnparseableError`'s own docstring: this batch was
                # never actually read, so it must not be treated as "read and
                # found nothing" by anything downstream, including the
                # watermark.
                raise ExtractionUnparseableError(
                    f"scope {scope_id!r}: model response for a "
                    f"{len(batch)}-message batch did not parse; refusing to "
                    "treat it as consolidated"
                )
            appended = await self._store.append_observations(
                scope_id=scope_id, observations=result.candidates
            )
        # Rescore UNCONDITIONALLY. Decay is time-driven, and dream_loop's 24h
        # firing exists to fade a dormant room's lexicon — it arrives here
        # with an EMPTY batch, so an early return would make that whole path
        # a no-op and leave a stale lexicon rendering as `live` forever.
        await self._store.rescore(scope_id=scope_id, now_ms=_now_ms())
        return appended
