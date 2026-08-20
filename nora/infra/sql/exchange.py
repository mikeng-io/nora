"""`RoomExchangeReader` plus the Dreamer Plan 2 write side, over SQLAlchemy
Core — the exchange-pattern sibling of `nora.infra.sql.language`.

Two tables: `exchange_observation` is append-only EVIDENCE, one row per
extracted pattern, natural-keyed `(scope_id, act, message_ids)` so a retried
or overlapping cycle cannot double-count; `room_exchange` is the CONSOLIDATED
table `room_exchange`'s own reader actually serves, keyed
`(scope_id, act, participants)`.

**Deliberate deviation from `SqlLanguageStore.append_observations`'s own
precedent**: that store re-derives `conversation_id`/`observed_at` with a
fresh `SELECT ... FROM messages WHERE id = :message_id`, rather than trusting
anything the candidate itself carries, because its candidate (`LexemeCandidate
.surface_form`) is partly MODEL-AUTHORED and the whole point is never to trust
a model's own claim about what it saw. `ExchangeCandidate` carries no
model-authored text at all — only a closed-set `act` and evidence pointers
already validated against the SAME batch this cycle's `RecordStore.since`
read from the database — so `append_observations` here takes that `batch`
directly and reads `conversation_id`/`ts` off its own `TranscriptRow`s,
skipping a second round trip to re-fetch data this process already holds,
from the very read that produced it. Nothing is trusted that was not already
DB-sourced in this cycle.

Watermark/lag/quiet/age all stay on `SqlLanguageStore` — `nora.app.runtime
._dream_once` reads ONE shared watermark per scope (hardcoded
`consolidator_name="language"`) and hands the SAME batch to every
`RoomConsolidator` in its list, Plan 2 included; `ExchangeConsolidator`
therefore never touches watermark state, exactly as `LanguageConsolidator`
does not.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.exchange_scoring import score, state_for
from nora.domain.models import (
    ExchangeCandidate,
    ExchangeExample,
    RoomExchange,
    RoomExchangePattern,
    TranscriptRow,
)
from nora.infra.sql.schema import EXCHANGE_OBSERVATION, MESSAGES, ROOM_EXCHANGE

log = logging.getLogger(__name__)

_RENDERED_STATES = ("live", "fading")
_EXAMPLES_PER_PATTERN_CAP = 3


def _now_ms() -> int:
    return int(time.time() * 1000)


class SqlExchangeStore:
    """Satisfies `nora.domain.ports.RoomExchangeReader`; also Plan 2's write
    side, consumed by `nora.language.exchange_consolidator.ExchangeConsolidator`."""

    def __init__(self, engine: AsyncEngine, *, metrics: Any = None) -> None:
        self._engine = engine
        self._metrics = metrics

    async def append_observations(
        self, *, scope_id: str, observations: Sequence[ExchangeCandidate],
        batch: Sequence[TranscriptRow],
    ) -> int:
        """ONE `INSERT ... SELECT ... ON CONFLICT DO NOTHING`, natural-keyed
        on `(scope_id, act, message_ids)`. `conversation_id`/`observed_at`
        come from `batch` — see module docstring for why that is safe here
        even though `SqlLanguageStore`'s own precedent re-queries — a
        candidate whose message_ids are not ALL present in `batch` (should be
        unreachable: `ExchangePatternObserver._extract` already drops any
        candidate with evidence outside the batch) contributes nothing rather
        than inserting a row keyed on a guess."""
        if not observations:
            return 0
        by_id = {row.id: row for row in batch}
        rows = []
        skipped = 0
        for obs in observations:
            rows_for = [by_id.get(mid) for mid in obs.message_ids]
            if any(r is None for r in rows_for):
                skipped += 1
                continue
            # `TranscriptRow` carries no `conversation_id` field of its own —
            # it is scoped to one conversation by the caller of `since` — so
            # `conversation_id` is the caller-supplied `scope_id`, which IS
            # the conversation id for every current `RoomConsolidator` caller
            # (`nora.app.runtime.dream_loop`'s `scopes()`), exactly the same
            # identification `SqlLanguageStore.lag`/`seconds_since_last_message`
            # already rely on.
            observed_at = max(r.ts for r in rows_for if r is not None)
            rows.append({
                "scope_id": scope_id,
                "conversation_id": scope_id,
                "act": obs.act,
                "message_ids": list(obs.message_ids),
                "participants": list(obs.participants),
                "observed_at": observed_at,
                "extractor_confidence": obs.confidence,
            })
        if skipped and self._metrics is not None:
            self._metrics.inc(
                "nora_exchange_candidates_dropped_total", float(skipped), reason="not_found",
            )
        if not rows:
            return 0
        stmt = (
            pg.insert(EXCHANGE_OBSERVATION)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=[
                    EXCHANGE_OBSERVATION.c.scope_id,
                    EXCHANGE_OBSERVATION.c.act,
                    EXCHANGE_OBSERVATION.c.message_ids,
                ]
            )
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.rowcount or 0)

    async def rescore(self, *, scope_id: str, now_ms: int) -> None:
        """Full recompute over every surviving observation for `scope_id`,
        grouped by `(act, participants)` — mirrors
        `SqlLanguageStore.rescore` exactly, minus the `distinct_speakers`
        aggregate (`participants` is already fixed by the natural key, never
        aggregated across rows)."""
        EO = EXCHANGE_OBSERVATION  # noqa: N806
        ranked = (
            sa.select(
                EO.c.act, EO.c.participants, EO.c.message_ids, EO.c.observed_at,
                sa.func.row_number()
                .over(
                    partition_by=(EO.c.act, EO.c.participants),
                    order_by=EO.c.observed_at.desc(),
                )
                .label("rn"),
            )
            .where(EO.c.scope_id == scope_id)
            .cte("ranked")
        )
        aggregated = (
            sa.select(
                EO.c.act, EO.c.participants,
                sa.func.count().label("occurrence_count"),
                sa.func.min(EO.c.observed_at).label("first_seen_at"),
                sa.func.max(EO.c.observed_at).label("last_seen_at"),
            )
            .where(EO.c.scope_id == scope_id)
            .group_by(EO.c.act, EO.c.participants)
            .cte("aggregated")
        )
        # No per-example `person_id`/`text` on `exchange_observation` (unlike
        # `language_observation`, there is no verbatim quote column here —
        # see module docstring: nothing on this evidence row is model-authored
        # TEXT to quote). The rendered example instead names the message ids
        # the pattern draws on, for `nora.language.exchange_render` to resolve
        # a real quote from `messages` at render time — kept OUT of this
        # store's own JSONB so a pruned message cannot leave a stale quote
        # sitting in `room_exchange.examples` forever.
        example_obj = sa.func.jsonb_build_object("message_ids", ranked.c.message_ids)
        examples_cte = (
            sa.select(
                ranked.c.act, ranked.c.participants,
                sa.func.jsonb_agg(
                    aggregate_order_by(example_obj, ranked.c.observed_at.desc())
                ).label("examples"),
            )
            .where(ranked.c.rn <= _EXAMPLES_PER_PATTERN_CAP)
            .group_by(ranked.c.act, ranked.c.participants)
            .cte("examples")
        )
        select_stmt = sa.select(
            aggregated.c.act, aggregated.c.participants, aggregated.c.occurrence_count,
            aggregated.c.first_seen_at, aggregated.c.last_seen_at, examples_cte.c.examples,
        ).select_from(
            aggregated.join(
                examples_cte,
                sa.and_(
                    aggregated.c.act == examples_cte.c.act,
                    aggregated.c.participants == examples_cte.c.participants,
                ),
            )
        )
        async with self._engine.begin() as conn:
            rows = (await conn.execute(select_stmt)).all()
            if not rows:
                return
            upsert_rows = []
            for r in rows:
                recognition_score = score(
                    occurrences=r.occurrence_count, last_seen_ms=r.last_seen_at, now_ms=now_ms,
                )
                state = state_for(
                    score=recognition_score, last_seen_ms=r.last_seen_at, now_ms=now_ms,
                )
                upsert_rows.append({
                    "scope_id": scope_id,
                    "act": r.act,
                    "participants": r.participants,
                    "first_seen_at": r.first_seen_at,
                    "last_seen_at": r.last_seen_at,
                    "occurrence_count": r.occurrence_count,
                    "recognition_score": recognition_score,
                    "state": state,
                    "examples": r.examples,
                })
            upsert_stmt = pg.insert(ROOM_EXCHANGE).values(upsert_rows)
            upsert_stmt = upsert_stmt.on_conflict_do_update(
                index_elements=[
                    ROOM_EXCHANGE.c.scope_id, ROOM_EXCHANGE.c.act, ROOM_EXCHANGE.c.participants,
                ],
                set_={
                    "first_seen_at": upsert_stmt.excluded.first_seen_at,
                    "last_seen_at": upsert_stmt.excluded.last_seen_at,
                    "occurrence_count": upsert_stmt.excluded.occurrence_count,
                    "recognition_score": upsert_stmt.excluded.recognition_score,
                    "state": upsert_stmt.excluded.state,
                    "examples": upsert_stmt.excluded.examples,
                },
            )
            await conn.execute(upsert_stmt)

    async def room_exchange(self, *, scope_id: str, limit: int) -> RoomExchange:
        """Satisfies `RoomExchangeReader.room_exchange`: NEVER raises — same
        broad `except Exception` as `SqlLanguageStore.room_language`, and for
        the identical reason (this read runs beside four siblings in
        `TranscriptInjection`'s `asyncio.gather`).

        Resolves each example's quote from `messages` at READ time (see
        `rescore`'s docstring for why the quote is not stored) — a message
        pruned since the pattern was last rescored simply yields no quote for
        that example, handled by `nora.language.exchange_render`'s own
        "no quotable example, do not render" rule, never an exception here.
        """
        try:
            stmt = (
                sa.select(ROOM_EXCHANGE)
                .where(
                    ROOM_EXCHANGE.c.scope_id == scope_id,
                    ROOM_EXCHANGE.c.state.in_(_RENDERED_STATES),
                )
                .order_by(ROOM_EXCHANGE.c.recognition_score.desc())
                .limit(limit)
            )
            async with self._engine.connect() as conn:
                rows = (await conn.execute(stmt)).all()
                all_message_ids = sorted({
                    mid
                    for r in rows
                    for ex in r.examples
                    for mid in ex["message_ids"]
                })
                bodies: dict[int, tuple[str, str | None]] = {}
                if all_message_ids:
                    msg_stmt = sa.select(
                        MESSAGES.c.id, MESSAGES.c.body, MESSAGES.c.person_id,
                    ).where(MESSAGES.c.id.in_(all_message_ids))
                    for m in (await conn.execute(msg_stmt)).all():
                        bodies[m.id] = (m.body or "", m.person_id)
            patterns = tuple(
                RoomExchangePattern(
                    act=r.act,
                    participants=tuple(r.participants),
                    occurrence_count=r.occurrence_count,
                    first_seen_ms=r.first_seen_at,
                    last_seen_ms=r.last_seen_at,
                    state=r.state,
                    examples=tuple(
                        ExchangeExample(
                            message_id=mid, person_id=bodies[mid][1], text=bodies[mid][0],
                        )
                        for ex in r.examples
                        for mid in ex["message_ids"]
                        if mid in bodies and bodies[mid][0].strip()
                    ),
                )
                for r in rows
            )
            return RoomExchange(scope_id=scope_id, patterns=patterns, generated_ms=_now_ms())
        except Exception:
            log.exception("room_exchange unavailable for scope %s", scope_id)
            return RoomExchange(scope_id=scope_id)
