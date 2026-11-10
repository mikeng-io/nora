"""Migration `0151_stimulus_claims` on a database that already holds wakes.

The backfill replays the pre-`0150` consumption rule EXACTLY: a wake at
`at_ms` consumed `id <= through` whose `pending_since` — a dated row's own
`due_ms`, an undated row's `recorded_at` — was at most `at_ms`, minus its
`unclaimed` names. A mid-tick deadline is the case that distinguishes it:
a row due at 90 000 ms (due_tick 2) was consumed by a wake at 95 000 ms
(tick floor 1) — `due_ms <= at_ms`, not `due_tick <= floor(at_ms / 60 000)`.

It runs once over each `(unit, mode)`'s newest VALID mark — a failed or
expired wake is not one — and then seeds `stimulus_pending`, where a claim
consumes only when it cannot release: an untracked wake's, or a `closed`
outcome's. `ck_cognition_wake_mode` admits only `live` at `0149`, so every
wake here is `live`; mode isolation of the same predicate is the store
contract's job (`test_a_closed_claim_in_another_mode_never_consumes`).
"""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command

from nora.infra.sql import create_sql_engine
from nora.infra.sql.migrations_runner import alembic_config, run_migrations


def _at(ms: int) -> Any:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


async def _rows(engine: Any, sql: str) -> list[tuple[Any, ...]]:
    async with engine.connect() as conn:
        return [tuple(r) for r in (await conn.execute(sa.text(sql))).all()]


@pytest.mark.fresh_database
async def test_the_0149_upgrade_replays_the_exact_old_consumption_rule(
    clean_pg_url: str,
) -> None:
    engine = create_sql_engine(clean_pg_url)
    try:
        command.downgrade(alembic_config(clean_pg_url),
                          "0149_computer_profile_merge")
        async with engine.begin() as conn:
            # Stimuli, appended in order (ids 1..5), unit "nora":
            #   1  dated due at 90000 — mid-tick: consumed by the 95000 mark
            #      under the exact `due_ms <= at_ms` rule (its tick is 2,
            #      ABOVE the mark's floor tick 1 — the wrong rule resurrects)
            #   2  dated due at 80000, named `unclaimed` by the mark — left
            #   3  dated due at 200000 — past the mark's time — left
            #   4  undated recorded at 85000 — consumed
            #   5  undated recorded at 99000 — after the mark — left
            # and "ops": id 6, dated due at 90000 — consumed by a TRACKED
            # mark whose outcome closed.
            await conn.execute(sa.text(
                "INSERT INTO stimulus (unit, kind, item, ref, at_ms, recorded_at,"
                " platform, conversation_id, person_id, cues, waits, due_ms)"
                " VALUES"
                " ('nora','self','self','s:mid',80000,:r0,'signal','g','p','{}',0,90000),"
                " ('nora','self','self','s:uncl',70000,:r1,'signal','g','p','{}',0,80000),"
                " ('nora','self','self','s:late',70000,:r1,'signal','g','p','{}',0,200000),"
                " ('nora','message','message','m:old',85000,:r2,'signal','g','p','{}',0,NULL),"
                " ('nora','message','message','m:new',99000,:r3,'signal','g','p','{}',0,NULL),"
                " ('ops','self','self','o:mid',80000,:r0,'signal','g','p','{}',0,90000)"
            ), {
                "r0": _at(80_000), "r1": _at(70_000),
                "r2": _at(85_000), "r3": _at(99_000),
            })
            # Wakes — nora: w1 the UNTRACKED mark at 95000 (unclaimed names
            # id 2); w2 a newer TRACKED wake with no outcome, stale (EXPIRED);
            # w3 a newer tracked wake FAILED. ops: w4 a TRACKED mark at 95000
            # whose outcome closed. The marks stay w1 and w4.
            await conn.execute(sa.text(
                "INSERT INTO cognition_wake (unit, mode, reason,"
                " from_stimulus_id, through_stimulus_id, stimulus_count,"
                " eligible_count, oldest_pending_ms, cut_cooldown, detail,"
                " at_ms, recorded_at, tracked) VALUES"
                " ('nora','live','settled',0,5,4,4,70000,0,"
                "  CAST(:u1 AS jsonb),95000,:r4,0),"
                " ('nora','live','settled',0,5,0,0,NULL,0,'{}',97000,:r5,1),"
                " ('nora','live','settled',0,5,0,0,NULL,0,'{}',98000,:r6,1),"
                " ('ops','live','settled',0,6,1,1,80000,0,'{}',95000,:r4,1)"
            ), {
                "u1": '{"unclaimed": [2]}',
                "r4": _at(95_000), "r5": _at(97_000), "r6": _at(98_000),
            })
            await conn.execute(sa.text(
                "INSERT INTO cognition_wake_outcome (wake_id, unit, session_id,"
                " outcome, detail, at_ms, cause, recorded_at)"
                " VALUES (3,'nora',NULL,'failed','',98000,'infra',:r6),"
                "        (4,'ops',NULL,'closed','',96000,'infra',:r4)"
            ), {"r6": _at(98_000), "r4": _at(96_000)})
        run_migrations(clean_pg_url)

        claims = {sid for (wid, sid) in await _rows(
            engine, "SELECT wake_id, stimulus_id FROM stimulus_claim",
        ) if wid == 1}
        # The old rule, exactly: mid(1) and the old undated row(4) consumed;
        # uncl(2) named unclaimed; late(3) and new(5) past the mark.
        assert claims == {1, 4}
        ops_claims = {sid for (wid, sid) in await _rows(
            engine, "SELECT wake_id, stimulus_id FROM stimulus_claim",
        ) if wid == 4}
        assert ops_claims == {6}
        # Nothing under the expired wake or the failed one — neither is a
        # valid mark.
        assert await _rows(
            engine,
            "SELECT wake_id FROM stimulus_claim WHERE wake_id IN (2, 3)",
        ) == []

        pending = {(u, s) for (u, s) in await _rows(
            engine, "SELECT unit, stimulus_id FROM stimulus_pending"
            " WHERE stimulus_id > 0",
        )}
        # nora: the unclaimed name and the two past-the-mark rows pending —
        # the mid-tick due row is NOT resurrected. ops: its one stimulus was
        # consumed by a closed outcome's claim — the pending set is empty.
        assert pending == {
            ("nora", 2), ("nora", 3), ("nora", 5),
        }
        sentinels = set(await _rows(
            engine,
            "SELECT unit, mode, stimulus_id FROM stimulus_pending"
            " WHERE stimulus_id = 0",
        ))
        assert ("nora", "live", 0) in sentinels
        assert ("ops", "live", 0) in sentinels
    finally:
        await engine.dispose()
