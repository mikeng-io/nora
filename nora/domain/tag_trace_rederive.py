"""`rebuild_state` — the re-derivation path (spec
docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md
§6.9/§16.4): rebuild a `Standing`'s `state` from the actual `TagTrace` turns,
rather than folding fold-onto-fold indefinitely.

`Standing.state` is lossy by design (§6.9 — "each debrief rewrites `state`
wholesale") precisely BECAUSE raw `TagTrace`s are retained
(`nora.infra.sql.tag_traces.SqlTagTraceStore`, Task 5/6): one bad fold cannot
destroy history undetectably, since §16.4's periodic full re-derivation can
always rebuild the position from the traces themselves. This module is that
rebuild's entry point.

**Task 6's job is retention, not the fold.** This is a STUB — Phase 1 proves
`SqlTagTraceStore.recent()` returns `TagTrace`s in the right order
(newest-first) for `rebuild_state` to eventually consume; the actual
folding/summarising logic is Phase 2 (`Standing`), out of scope here.

Framework-free, per `nora/domain/`'s rule: stdlib and dataclasses only.
"""

from __future__ import annotations

from collections.abc import Sequence

from .tag_trace import TagTrace


def rebuild_state(traces: Sequence[TagTrace]) -> str:
    """Rebuild a `Standing`'s `state` from a conversation's retained
    `TagTrace`s, oldest-to-newest fold order.

    **Not implemented in Phase 1.** `SqlTagTraceStore.recent()` returns
    newest-first (matching its index's own column order); a real
    implementation folds oldest-to-newest, so a caller passing `recent()`'s
    result straight through must reverse it first — that ordering contract is
    Phase 2's to define and test, not this stub's.
    """
    raise NotImplementedError(
        "rebuild_state is a Phase 1 stub (Task 6) — the fold/summarise logic "
        "is Phase 2 (Standing), not yet implemented. See "
        "docs/superpowers/specs/2026-08-20-reflection-reasoning-continuity-design.md "
        "§6.9/§16.4."
    )
