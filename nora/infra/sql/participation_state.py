"""`SqlParticipationStateStore` — the participation trigger's per-room
watermarks, in `participation_window_state` instead of in a dict a
force-recreate empties (`nora.app.participation`).

**The read is once and the writes are rare, so they are separate verbs.**
`nora/infra/sql/dream_schedule.py` states the rule: a combined `upsert_and_read`
would write on every read, and a table that changes only when a window closes
must not be written by a tick that only wants to know where the room was. The
trigger holds the live cursors in memory (`note_message` moves one per message,
synchronously, and must not await), so the read happens once per process, at
hydration, and writes land only on ticks that closed a window or sent.

**`read_cursors` reads `messages` too.** A restored cursor joins two facts:
where this room was last judged (this table) and where the record has got to
since (`messages`). The trigger's quiet arm compares them, so one statement
answers both and the halves are read at the same instant.

The message half is restricted to `is_nora = 0`. Nora's own replies are rows in
that table but are not participation candidates: the dispatcher never notes
them, and a cursor that counted them would place the newest message after the
window end she spoke in, so the room would read as having unjudged traffic
because she answered it and every turn would re-arm the next one.

**Absence is an answer, never zero.** A room with no row is not in the result
at all; a row whose `last_send_at` is NULL comes back as `None`; a room whose
messages have been pruned comes back with `last_message_at=None`. See
`nora.domain.participation`'s module docstring for why each of those must not
be 0.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.participation import ParticipationCursor

from .schema import MESSAGES, PARTICIPATION_WINDOW_STATE


class SqlParticipationStateStore:
    """`__init__(self, engine)` and nothing else, matching `SqlStandingStore`
    and `SqlDreamScheduleClock`: no config, clock or metrics, so a fixture can
    build it from a bare `AsyncEngine`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def read_cursors(self) -> list[ParticipationCursor]:
        """Every room this trigger has ever judged, with the record's newest
        inbound message beside it. One row per room; rooms with no row are
        absent from the result rather than present with zeroes.

        The message half is a LATERAL rather than one query per room, so the
        query count does not grow with the allowlist; `idx_messages_conv_ts`
        serves the correlated lookup.

        A LEFT JOIN, not an inner one: a room whose messages have been pruned
        still has a watermark worth restoring, and dropping the row would make
        the next window it closes look like its first.
        """
        newest = (
            sa.select(MESSAGES.c.ts, MESSAGES.c.conversation_type)
            .where(
                MESSAGES.c.platform == PARTICIPATION_WINDOW_STATE.c.platform,
                MESSAGES.c.conversation_id
                == PARTICIPATION_WINDOW_STATE.c.conversation_id,
                # Her own replies are not candidates — see the module docstring.
                MESSAGES.c.is_nora == 0,
                MESSAGES.c.hidden_at.is_(None),
            )
            .order_by(MESSAGES.c.ts.desc(), MESSAGES.c.id.desc())
            .limit(1)
            .lateral("newest")
        )
        stmt = sa.select(
            PARTICIPATION_WINDOW_STATE.c.platform,
            PARTICIPATION_WINDOW_STATE.c.conversation_id,
            PARTICIPATION_WINDOW_STATE.c.last_window_end_at,
            PARTICIPATION_WINDOW_STATE.c.last_send_at,
            newest.c.ts,
            newest.c.conversation_type,
        ).select_from(
            PARTICIPATION_WINDOW_STATE.outerjoin(newest, sa.true())
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            ParticipationCursor(
                platform=r.platform,
                conversation_id=r.conversation_id,
                last_window_end_at=int(r.last_window_end_at),
                last_send_at=None if r.last_send_at is None else int(r.last_send_at),
                last_message_at=None if r.ts is None else int(r.ts),
                conversation_type=r.conversation_type,
            )
            for r in rows
        ]

    async def record_window_end(
        self, *, platform: str, conversation_id: str, at_ms: int
    ) -> None:
        """Stamp the window this room has now judged up to. An UPSERT: the
        second window is the ordinary case and a plain INSERT would raise on
        it.

        `last_send_at` is deliberately NOT in the `set_`: a close that ran no
        turn, or ran one that stayed silent, must leave the cooldown where it
        was. This verb is not handed the send at all.
        """
        stmt = pg_insert(PARTICIPATION_WINDOW_STATE).values(
            platform=platform, conversation_id=conversation_id,
            last_window_end_at=at_ms,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt.on_conflict_do_update(
                index_elements=[
                    PARTICIPATION_WINDOW_STATE.c.platform,
                    PARTICIPATION_WINDOW_STATE.c.conversation_id,
                ],
                set_={
                    "last_window_end_at": stmt.excluded.last_window_end_at,
                    "updated_at": sa.func.now(),
                },
            ))

    async def record_send(
        self, *, platform: str, conversation_id: str, at_ms: int
    ) -> None:
        """Stamp a DELIVERED participation message — the cooldown.

        An UPSERT that also supplies `last_window_end_at`, rather than an
        UPDATE that assumes the row is there. The close normally wrote the row
        in the same iteration with this same `at_ms`, but a zero-row UPDATE
        would lose the cooldown without raising. The insert branch therefore
        carries the truthful value, and the conflict branch touches only the
        send.
        """
        stmt = pg_insert(PARTICIPATION_WINDOW_STATE).values(
            platform=platform, conversation_id=conversation_id,
            last_window_end_at=at_ms, last_send_at=at_ms,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt.on_conflict_do_update(
                index_elements=[
                    PARTICIPATION_WINDOW_STATE.c.platform,
                    PARTICIPATION_WINDOW_STATE.c.conversation_id,
                ],
                set_={
                    "last_send_at": stmt.excluded.last_send_at,
                    "updated_at": sa.func.now(),
                },
            ))

    async def last_send_at(
        self, *, platform: str, conversation_id: str
    ) -> int | None:
        """The durable send cursor for one room, read by the claimed
        `participation_fire` work — it must not trust the live tick's
        in-memory mirror across a restart. Absence is None, never 0."""
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                sa.select(PARTICIPATION_WINDOW_STATE.c.last_send_at).where(
                    PARTICIPATION_WINDOW_STATE.c.platform == platform,
                    PARTICIPATION_WINDOW_STATE.c.conversation_id
                    == conversation_id,
                )
            )).first()
        return None if row is None or row.last_send_at is None else int(row.last_send_at)

    async def unfolded_candidates(self) -> list[dict[str, Any]]:
        """The durable candidate set: `bridge_work` participation rows whose
        note committed (`result.candidate.noted_ms`) but which no window has
        folded yet (`folded_ms` absent) — membership is the mark, whatever
        the bookkeeping row's state. A cross-table read is deliberate —
        the WorkRunner's work ledger IS the membership record, and reading
        `messages` would invent membership from traffic that was never a
        candidate."""
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT id, platform, conversation_id,"
                " result->'candidate'->>'noted_ms' AS noted_ms"
                " FROM bridge_work"
                " WHERE kind = 'participation'"
                " AND COALESCE(jsonb_exists("
                "   result->'candidate', 'noted_ms'), false)"
                " AND NOT COALESCE(jsonb_exists("
                "   result->'candidate', 'folded_ms'), false)"
            ))).mappings().all()
        return [dict(r) for r in rows]

    async def noted_rooms(self) -> list[tuple[str, str]]:
        """Every `(platform, conversation_id)` that has EVER carried a
        committed candidate mark — folded or not. A room whose membership was
        already judged keeps its ledger discipline: ambient transcript rows
        must never re-time or re-open its windows, so hydration has to know
        the room is ledger-owned even when no candidate is outstanding."""
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT DISTINCT platform, conversation_id FROM bridge_work"
                " WHERE kind = 'participation'"
                " AND COALESCE(jsonb_exists("
                "   result->'candidate', 'noted_ms'), false)"
            ))).mappings().all()
        return [(r["platform"], r["conversation_id"]) for r in rows]
