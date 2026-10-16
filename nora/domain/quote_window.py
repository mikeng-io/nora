"""`QuoteWindow` — one collaborator, shared by every transcript read that can
grow past `context_window` to resolve a reply's quote (DEC-195).

**Why one collaborator rather than a copy per caller.** The tag/reply path
(`nora.engagement.context`) knows the ONE quote it is answering —
`TagContext.message.quote`, the triggering message's own. A fix wired there only
would leave every OTHER transcript builder with the old behaviour (the split
`nora.domain.media_join`'s own docstring records).
`nora.app.fire.FirePath.build_context` reads the same `context_window`-sized
transcript for a participation turn — no single triggering message
(`participation_occasion`'s `body=""`, `person_id=None`: she owes the turn to no
one row), but the window it reads can still hold a row whose OWN `quoted_ts`
points outside it, and `_parent_of` (`nora.agent.prefill`) resolves it exactly
the same way regardless of which path fetched the transcript. One collaborator,
injected into both, keeps `quote_margin`/`quote_window_multiplier`/the cap from
drifting between them the way `context_window` itself never has.

**Why the DOMAIN layer.** Same reasoning as `media_join`'s: `nora.app` and
`nora.engagement` sit in different layers of the import contract
(`nora.app` above `nora.engagement`), so a copy living in either one could
not be reached by the other. The bottom of the graph is where both can
import it. `metrics` is injected rather than imported for the same
"framework-free domain" contract `SqlRecordStore`'s own docstring names.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from nora.domain.models import Quote, TranscriptRow
from nora.domain.ports import RecordStore

log = logging.getLogger("nora.domain.quote_window")


def first_by_ts(rows: Iterable[TranscriptRow]) -> dict[int, TranscriptRow]:
    """`ts -> the FIRST row carrying it`, which is the tie-break every resolver
    of a reply owes every other.

    `ts` is not unique. Two rows can share a millisecond — a burst, a re-import,
    a platform that stamps by second — and the places that resolve a quote must
    agree about which one it means: `_replying_to_block` scanning with
    `next(...)` (FIRST wins) while `_parent_of` read a dict comprehension (LAST
    wins) would let one prompt carry `<replying_to>` quoting one message and
    `replying_to="n"` pointing at another, for the same pointer.
    `tests/architecture/test_one_quote_resolver.py` stops a second copy of the
    comprehension.

    FIRST rather than last because a reply points at something that already
    existed: the earliest row bearing the timestamp is the one that was there
    to be quoted. It is also the row `RecordStore.depth_to` sizes a window to
    reach, so a window grown for a quote still holds the row this resolves to.
    """
    out: dict[int, TranscriptRow] = {}
    for row in rows:
        out.setdefault(row.ts, row)
    return out


class QuoteWindow:
    """See module docstring. Construct ONCE per process
    (`nora.app.composition.build_dependencies`); share the instance with
    every transcript-reading collaborator that needs it, as `MediaJoin` is."""

    def __init__(
        self,
        *,
        audit: RecordStore,
        metrics: Any,
        quote_margin: int = 10,
        quote_window_multiplier: int = 3,
    ) -> None:
        self._audit = audit
        self._metrics = metrics
        self._quote_margin = quote_margin
        self._quote_window_multiplier = quote_window_multiplier

    async def recent_for_quote(
        self,
        *,
        conversation_id: str,
        context_window: int,
        quote: Quote | None,
        platform: str,
        stage: str,
    ) -> tuple[TranscriptRow, ...]:
        """The tag/reply path's shape — ONE known quote, checked BEFORE the
        fetch so the common case (no quote, or a quote already inside the
        plain window) costs exactly the one `recent` call.

        `stage` is the `nora_errors_total{stage=...}` label a failed fetch
        counts under — passed explicitly rather than fixed, because the two
        callers this ships with (`"context"` for the tag path, `"fire_context"`
        for the fire path) are both load-bearing series and must not silently
        collapse into one.
        """
        limit = context_window
        if quote is not None:
            limit = await self._grown_limit(
                conversation_id=conversation_id, context_window=context_window,
                quoted_ts=quote.quoted_ts,
            )
        return await self._fetch(conversation_id, limit, platform, stage)

    async def recent_for_window(
        self,
        *,
        conversation_id: str,
        context_window: int,
        platform: str,
        stage: str,
    ) -> tuple[TranscriptRow, ...]:
        """The fire path's shape — no single triggering message, so there is
        no quote to check ahead of the fetch. Reads the plain window first,
        then looks for a row INSIDE it whose own `quoted_ts` does not resolve
        against what was just read — the same condition `_parent_of`
        (`nora.agent.prefill`) would fail on when this transcript is
        rendered. The MOST RECENT such row is chased, single hop only
        (a transitive chain is not chased: that row's parent's own
        unresolved quote, if it had one, is left alone).

        A window with nothing unresolved costs exactly one `recent` call,
        same as `recent_for_quote`'s common case.
        """
        base = await self._fetch(conversation_id, context_window, platform, stage)
        if not base:
            return base
        seen_ts = {row.ts for row in base}
        unresolved_ts = [
            row.quoted_ts for row in base
            if row.quoted_ts is not None and row.quoted_ts not in seen_ts
        ]
        if not unresolved_ts:
            return base
        limit = await self._grown_limit(
            conversation_id=conversation_id, context_window=context_window,
            quoted_ts=unresolved_ts[-1],
        )
        if limit == context_window:
            return base
        return await self._fetch(conversation_id, limit, platform, stage)

    async def _grown_limit(
        self, *, conversation_id: str, context_window: int, quoted_ts: int,
    ) -> int:
        """`context_window`, unless `quoted_ts` resolves to a row deeper than
        it — then `depth + quote_margin`, capped at `context_window *
        quote_window_multiplier`. Beyond the cap, or when `quoted_ts` cannot
        be found at all (pruned, pre-migration, a foreign conversation),
        `context_window` — the caller then renders with the plain window.
        A failed `depth_to` degrades the same way rather
        than raising: sizing the window is advisory, the read itself is not.
        """
        try:
            depth = await self._audit.depth_to(
                conversation_id=conversation_id, ts=quoted_ts,
            )
        except Exception:
            log.exception("quote depth lookup failed; using the plain window")
            depth = None
        if depth is None or depth <= context_window:
            return context_window
        wanted = depth + self._quote_margin
        cap = context_window * self._quote_window_multiplier
        return wanted if wanted <= cap else context_window

    async def _fetch(
        self, conversation_id: str, limit: int, platform: str, stage: str,
    ) -> tuple[TranscriptRow, ...]:
        try:
            return tuple(await self._audit.recent(
                conversation_id=conversation_id, limit=limit,
            ))
        except Exception:
            log.exception("context collection failed; continuing with an empty transcript")
            self._metrics.inc("nora_errors_total", stage=stage, platform=platform)
            return ()
