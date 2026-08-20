"""Dreamer Plan 2's thin sequencing shim over `ExchangePatternObserver` and
`SqlExchangeStore` — the exchange sibling of
`nora.language.consolidator.LanguageConsolidator`, same shape, same reasons:
extract, append, rescore, and nothing else. Satisfies
`nora.domain.ports.RoomConsolidator`.

`ExchangeConsolidator` never touches watermark state — see
`nora.infra.sql.exchange`'s module docstring: `nora.app.runtime._dream_once`
already owns the ONE shared watermark per scope and hands every
`RoomConsolidator` in its list the same batch.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from nora.domain.models import TranscriptRow
from nora.infra.sql.exchange import SqlExchangeStore
from nora.language.exchange_observer import ExchangePatternObserver


def _now_ms() -> int:
    return int(time.time() * 1000)


class ExtractionUnparseableError(Exception):
    """Mirrors `nora.language.consolidator.ExtractionUnparseableError`
    exactly: raised for a NON-EMPTY batch whose model response did not
    parse — `_dream_once` already treats a raising consolidator as a failed
    cycle (logged, counted, backed off), never advancing the watermark."""


class ExchangeConsolidator:
    """Satisfies `nora.domain.ports.RoomConsolidator`. Two collaborators
    through `__init__`, no hooks."""

    name = "exchange"

    def __init__(
        self, *, observer: ExchangePatternObserver, store: SqlExchangeStore
    ) -> None:
        self._observer = observer
        self._store = store

    async def consolidate(
        self, *, scope_id: str, batch: Sequence[TranscriptRow]
    ) -> int:
        appended = 0
        if batch:
            result = await self._observer.observe(batch=batch)
            if not result.parsed:
                raise ExtractionUnparseableError(
                    f"scope {scope_id!r}: exchange-pattern model response for "
                    f"a {len(batch)}-message batch did not parse; refusing to "
                    "treat it as consolidated"
                )
            appended = await self._store.append_observations(
                scope_id=scope_id, observations=result.candidates, batch=batch,
            )
        # Rescore UNCONDITIONALLY — decay is time-driven, same argument
        # `LanguageConsolidator.consolidate` makes for its own call.
        await self._store.rescore(scope_id=scope_id, now_ms=_now_ms())
        return appended
