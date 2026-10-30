"""Generated durable effects of every act change the snapshot invariant."""

import sqlalchemy as sa

from nora.infra.sql.engine import create_sql_engine
from nora.infra.sql.schema import METADATA
from scripts import memory_graph_spike as spike
from tests.integration.test_snapshot_holds_what_was_held import APPEND, REVISE, ROWS


async def test_every_generated_act_footprint_moves_invariant(pg_url):
    engine = create_sql_engine(pg_url)
    try:
        async with engine.begin() as conn:
            for table in ("note", "events", "standing", "reminders"):
                await conn.execute(sa.insert(METADATA.tables[table]).values(ROWS[table](0)))
            await conn.execute(sa.insert(METADATA.tables["memo"]).values(APPEND["memo"](0)))
        # Independent of ACT_TABLES: one observed durable effect for each live verb.
        effects = {
            "note_create": ("note", "insert"),
            "note_revise": ("note_revision", "insert"),
            "note_set_status": ("note", "update"),
            "note_merge": ("note_merge_decision", "insert"),
            "memo_create": ("memo", "insert"),
            "memo_revise": ("memo_revision", "insert"),
            "memo_set_status": ("memo_revision", "insert"),
            "event_propose": ("events", "insert"),
            "event_revise": ("event_revision", "insert"),
            "event_set_status": ("events", "update"),
            "standing_revise": ("standing_revision", "insert"),
            "wake_at": ("cognition_self_wake", "insert"),
            "wake_move": ("cognition_self_wake", "insert"),
            "wake_cancel": ("cognition_self_wake", "insert"),
            "remind": ("reminders", "insert"),
            "remind_move": ("reminders", "update"),
            "remind_cancel": ("reminders", "update"),
        }
        for number, (verb, (name, operation)) in enumerate(effects.items(), start=1):
            before = await spike.acts(engine)
            async with engine.connect() as conn, conn.begin():
                table = METADATA.tables[name]
                if operation == "insert":
                    row = (ROWS if name in ROWS else APPEND)[name](number)
                    if name == "memo_revision":
                        row["memo_id"] = APPEND["memo"](0)["id"]
                        row["seq"] = number
                        if verb == "memo_set_status":
                            row["state"] = "withdrawn"
                    await conn.execute(sa.insert(table).values(row))
                else:
                    column, value = REVISE[name]
                    if verb == "note_set_status":
                        column, value = "retired_at", lambda n: n
                    if verb == "remind_move":
                        column, value = "text", lambda n: f"Generated moved reminder {n}"
                    await conn.execute(sa.update(table).values({column: value(number)}))
                # Read committed state through the actual invariant, including in-place effects.
                await conn.commit()
                changed = await spike.acts(engine)
                assert spike.check_acts(before, changed) > 0, verb

    finally:
        await engine.dispose()
