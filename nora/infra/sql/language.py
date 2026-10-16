"""`SqlDreamCursor` — the Dreamer's consolidation cursor over SQLAlchemy Core.

**This module owns one table: `consolidation_watermark`, the DREAMER's
cursor** — `(consolidator_name, scope_id) -> how far this adaptor has read
`messages`` — and every scheduled adaptor in `nora.app.runtime.dream_loop`
reads and advances it, including the note tier's, which has nothing to do with
language. The class is what `nora.app.runtime._ConsolidationCursor` names
structurally; its six methods ARE that Protocol. The `language` name is
inherited and no longer describes the contents.

The six verbs, and what each is for:

- `watermark_for` / `advance_watermark` — read and UPSERT the cursor. An
  unknown `(name, scope)` pair reads `0`, which is what lets
  `nora.app.runtime.seed_watermarks` tell «never seen» from «read nothing».
- `lag` / `seconds_since_last_message` / `watermark_age_seconds` — the three
  fire conditions `_dream_adaptor_once` evaluates.
- `watermark_is_behind_the_record` — the fail-closed tell: a watermark below
  the oldest surviving row means messages were deleted before they were ever
  read, which is R-27's `unavailable` and never `nothing_found`.

Every read carries `is_nora = 0` in its predicate, because the watermark only
ever advances over HUMAN rows (`RecordStore.since` excludes `is_nora`) — and
that predicate is what lets these queries use the partial
`idx_messages_dream_lag` instead of scanning a conversation's whole history
every tick.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.schema import CONSOLIDATION_WATERMARK, MESSAGES

log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


class SqlLanguageStore:
    """The Dreamer's consolidation cursor — `nora.app.runtime
    ._ConsolidationCursor`'s six verbs, and nothing else. Every scheduled
    adaptor reads and advances it, so this store is TIER-NEUTRAL despite the
    module's inherited name (see the module docstring)."""

    def __init__(self, engine: AsyncEngine, *, metrics: Any = None) -> None:
        """`metrics` is INJECTED, never imported — the same discipline
        `SqlRecordStore.__init__` documents: `nora.infra` sits BELOW
        `nora.obs` in import-linter's layers contract, so importing
        `nora.obs.metrics` here (even lazily, inside a method) breaks that
        contract. `Any` rather than `Metrics` for the same reason; `None`
        (the default) means this store simply does not count, which is
        correct for every unit test that does not care."""
        self._engine = engine
        self._metrics = metrics

    async def watermark_for(self, *, consolidator_name: str, scope_id: str) -> int:
        stmt = sa.select(CONSOLIDATION_WATERMARK.c.last_message_id).where(
            CONSOLIDATION_WATERMARK.c.consolidator_name == consolidator_name,
            CONSOLIDATION_WATERMARK.c.scope_id == scope_id,
        )
        async with self._engine.connect() as conn:
            val = (await conn.execute(stmt)).scalar_one_or_none()
        return int(val) if val is not None else 0

    async def advance_watermark(
        self, *, consolidator_name: str, scope_id: str, last_message_id: int
    ) -> None:
        stmt = (
            pg.insert(CONSOLIDATION_WATERMARK)
            .values(
                consolidator_name=consolidator_name,
                scope_id=scope_id,
                last_message_id=last_message_id,
                updated_at=sa.func.now(),
            )
            .on_conflict_do_update(
                index_elements=[
                    CONSOLIDATION_WATERMARK.c.consolidator_name,
                    CONSOLIDATION_WATERMARK.c.scope_id,
                ],
                set_={"last_message_id": last_message_id, "updated_at": sa.func.now()},
            )
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def watermark_age_seconds(
        self, *, consolidator_name: str, scope_id: str
    ) -> float:
        """`inf` when this `(consolidator_name, scope_id)` has never been
        watermarked — the honest reading of "how long since this last ran" for
        a pair that has never run is "arbitrarily long ago", and `_dream_once`
        relies on exactly that: `age >= max_age_seconds` is one of its three
        fire conditions, deliberately firing at lag == 0, so a scope's very first
        tick must fire even with nothing unread yet.
        """
        stmt = sa.select(CONSOLIDATION_WATERMARK.c.updated_at).where(
            CONSOLIDATION_WATERMARK.c.consolidator_name == consolidator_name,
            CONSOLIDATION_WATERMARK.c.scope_id == scope_id,
        )
        async with self._engine.connect() as conn:
            updated_at = (await conn.execute(stmt)).scalar_one_or_none()
        if updated_at is None:
            return float("inf")
        return float(max((datetime.now(UTC) - updated_at).total_seconds(), 0.0))

    async def lag(self, *, scope_id: str, after_id: int) -> int:
        """`idx_messages_dream_lag` exists for exactly this query."""
        stmt = sa.select(sa.func.count()).select_from(MESSAGES).where(
            MESSAGES.c.conversation_id == scope_id,
            MESSAGES.c.id > after_id,
            MESSAGES.c.is_nora == 0,
        )
        async with self._engine.connect() as conn:
            return int((await conn.execute(stmt)).scalar_one())

    async def head_message_id(self, *, scope_id: str) -> int:
        """The NEWEST message id in this scope, or 0 if it holds none.

        For `seed_watermarks`' head fallback: an adaptor whose scope the
        inherit-source never covered would otherwise start at 0 and walk the
        room's whole history `batch_limit` rows at a time, a model call per
        batch. A cursor seeded at 0 on a busy room would
        leave a long unread backlog, so accounts get written from slices of
        that room's PAST while the head claims to say what the room is now.

        `is_nora` is NOT filtered here, unlike `lag`. The value is a CURSOR
        POSITION, not a count of work: the watermark is compared against
        `messages.id` whatever wrote each row, so excluding her own sends would
        seed behind messages that are already read and re-read them.
        """
        stmt = sa.select(sa.func.max(MESSAGES.c.id)).where(
            MESSAGES.c.conversation_id == scope_id
        )
        async with self._engine.connect() as conn:
            head = (await conn.execute(stmt)).scalar_one_or_none()
        return int(head or 0)

    async def seconds_since_last_message(self, *, scope_id: str) -> float:
        """`inf` when the scope has no messages at all — mirrors
        `watermark_age_seconds`'s empty case. Harmless either way at the one
        call site: `_dream_once` only consults this under `lag > 0`, and a
        scope with zero messages ever also has `lag == 0`.
        """
        stmt = sa.select(sa.func.max(MESSAGES.c.ts)).where(
            MESSAGES.c.conversation_id == scope_id
        )
        async with self._engine.connect() as conn:
            latest_ts = (await conn.execute(stmt)).scalar_one_or_none()
        if latest_ts is None:
            return float("inf")
        return float(max(_now_ms() - latest_ts, 0) / 1000.0)

    async def watermark_is_behind_the_record(
        self, *, scope_id: str, watermark: int
    ) -> bool:
        """True when the oldest SURVIVING message's id is more than one past
        `watermark` — i.e. something between what was already read and what
        still exists was deleted before this consolidator ever read it. A
        scope pruned to `id == 7` while the watermark sits at `3` IS behind
        (`7 > 3 + 1`) — rows 4-6 are gone and were never consolidated. No
        surviving rows at all is not "behind" either — there is nothing to
        have lost evidence from.

        `watermark <= 0` returns `False` UNCONDITIONALLY, before the query
        runs at all — this is not an optimisation, it is required for
        correctness. `watermark_for` returns `0` for any scope this
        consolidator has never watermarked, but `messages.id` is ONE GLOBAL
        sequence shared by every conversation and every sender: for any
        conversation other than the very first row ever recorded in the whole
        deployment, its oldest human row already has `id >> 1`. Without this
        early return, `min_id > 0 + 1` is `True` on the first tick of
        essentially every room, with nothing pruned and nothing lost — not a
        corner case, the guaranteed path for every room in a multi-group
        deployment. `nora_dream_evidence_lost_total` is a monotonic counter;
        firing it once per room on an ordinary first tick makes it
        permanently non-zero, and a tell that always fires is not
        fail-closed, it is noise — the one signal that should mean "evidence
        was pruned unread" could never again be read as evidence of anything.
        A scope only has evidence worth losing once IT has actually
        consumed some (`watermark > 0`); before that, "not behind" is simply
        true.

        `is_nora == 0` is part of the (post-guard) predicate for the same
        reason `lag` carries it: the watermark only ever advances over HUMAN
        rows (`RecordStore.since` excludes `is_nora`), so a surviving Nora
        row is not evidence this consolidator could have lost — and the
        predicate is what lets this query use the partial
        `idx_messages_dream_lag` (`(conversation_id, id) WHERE is_nora = 0`)
        instead of scanning the conversation's whole history every tick.
        """
        if watermark <= 0:
            return False
        stmt = sa.select(sa.func.min(MESSAGES.c.id)).where(
            MESSAGES.c.conversation_id == scope_id,
            MESSAGES.c.is_nora == 0,
        )
        async with self._engine.connect() as conn:
            min_id = (await conn.execute(stmt)).scalar_one_or_none()
        if min_id is None:
            return False
        return bool(min_id > watermark + 1)
