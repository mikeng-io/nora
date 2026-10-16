"""`dream_schedule_state` — where a watermark-free scheduled adaptor's last
fire is remembered, so a restart does not hand it a fresh clock.

**The two verbs are deliberately not one `upsert_and_read`.** `dream_loop`
reads the clock for every (adaptor, key) pair on every tick and writes it only
on the ticks that actually fire — a combined verb would write on every read,
turning a 5-minute liveness check into a 5-minute write against a table whose
whole purpose is to change once a day.

**A missing row means «never fired», never «fired at 0».** The distinction is
the one this module exists to keep: epoch 0 is a real instant in 1970 that any
slot arithmetic will happily compute against, and a store that returned it for
an absent pair would make a brand-new adaptor look like one that last ran
decades ago. `None` is the honest reading and the caller decides what a first sighting means.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from .schema import DREAM_SCHEDULE_STATE


class SqlDreamScheduleClock:
    """The Postgres implementation. Shares `InMemoryDreamScheduleClock`'s
    contract suite (`tests/contract/test_dream_schedule_clock_contract.py`)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def last_fired(self, *, adaptor_name: str, key: str) -> int | None:
        """Epoch-ms of this pair's last recorded fire, or `None` if it has
        never fired. Never raises on an absent row — absence is an answer."""
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                sa.select(DREAM_SCHEDULE_STATE.c.last_fired_at).where(
                    DREAM_SCHEDULE_STATE.c.adaptor_name == adaptor_name,
                    DREAM_SCHEDULE_STATE.c.scope_id == key,
                )
            )).first()
        return None if row is None else int(row.last_fired_at)

    async def record_fire(
        self, *, adaptor_name: str, key: str, at_ms: int
    ) -> None:
        """Stamp this pair's fire. An UPSERT, because the second day is the
        ordinary case and a plain INSERT would raise on it.

        `updated_at` is refreshed alongside so the row's own audit column does
        not freeze at the first fire — it is the operator's read of «is this
        clock alive», and a clock whose `updated_at` never moves reads as a
        dead subsystem while `last_fired_at` advances underneath it.
        """
        stmt = pg_insert(DREAM_SCHEDULE_STATE).values(
            adaptor_name=adaptor_name, scope_id=key, last_fired_at=at_ms,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt.on_conflict_do_update(
                index_elements=[
                    DREAM_SCHEDULE_STATE.c.adaptor_name,
                    DREAM_SCHEDULE_STATE.c.scope_id,
                ],
                set_={
                    "last_fired_at": stmt.excluded.last_fired_at,
                    "updated_at": sa.func.now(),
                },
            ))
