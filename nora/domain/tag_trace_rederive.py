"""`rebuild_state` — the re-derivation entry point: rebuild a `Standing`'s
`state` from the actual `TagTrace` turns, rather than folding fold-onto-fold
indefinitely.

`Standing.state` is lossy by design (each fold rewrites it wholesale), but raw
`TagTrace`s are retained (`nora.infra.sql.tag_traces.SqlTagTraceStore`), so one
bad fold cannot destroy history undetectably: a full re-derivation can always
rebuild the position from the traces themselves.

This is a STUB: `SqlTagTraceStore.recent()` returns `TagTrace`s newest-first
for `rebuild_state` to consume, but the folding/summarising logic is not
implemented.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

from collections.abc import Sequence

from .tag_trace import TagTrace


def rebuild_state(traces: Sequence[TagTrace]) -> str:
    """Rebuild a `Standing`'s `state` from a conversation's retained
    `TagTrace`s, oldest-to-newest fold order.

    **Not implemented.** `SqlTagTraceStore.recent()` returns newest-first
    (matching its index's own column order); a real implementation folds
    oldest-to-newest, so a caller passing `recent()`'s result straight through
    must reverse it first. That ordering contract is for the implementation to
    define and test, not this stub.
    """
    raise NotImplementedError(
        "rebuild_state is a Phase 1 stub (Task 6) — the fold/summarise logic "
        "is Phase 2 (Standing), not yet implemented. See "
        "docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md "
        "§6.9/§16.4."
    )
