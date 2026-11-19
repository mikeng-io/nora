"""Write, revise and retire across every store migrated here, snapshot at two
moments, and get EXACTLY what was held at each.

WHAT IS HELD is read from the live tables at each moment (`to_jsonb` of
every row) — the thing the restore claims to reproduce, observed directly,
never through `row_history`. The restore is then built after the source has
moved on, into a sandbox database of its own (`sandbox_as_of`, the harness's
per-case database), and read back the same way. Equality is of whole row
sets, append-only tables included, so a row too many, a row too few or one
column carrying a later value all fail.

Per in-place table, six rows keyed 0..5 walk every transition:

- 0 written before t1 and never touched (the parent append-only rows cite);
- 1 written before t1, revised between t1 and t2, retired after t2;
- 2 written before t1, retired between t1 and t2;
- 3 written, revised and retired all before t1;
- 4 written between t1 and t2, revised after t2;
- 5 written after t2.

Append-only tables get one row in each of the three stretches.

And one case no store asks for: an edit of message 2, which is pruned
between t1 and t2. `message_edit.message_id` is ON DELETE CASCADE, so the
prune deletes the edit with no statement naming it; the edit must be held at
t1, gone at t2, and kept in history as hidden.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pytest
import sqlalchemy as sa

from nora.infra.sql import create_sql_engine
from nora.infra.sql.history import APPEND_ONLY, HISTORY_TABLES
from nora.infra.sql.snapshot_at import copied_tables
from nora.subject import sandbox_as_of
from tests.integration.snapshot_rows import APPEND, DERIVED, MS0, REVISE, ROWS

#: The edit of message 2 that the prune cascades away.
PRUNED_EDIT = 3100


def test_the_fixture_covers_every_restorable_table() -> None:
    assert set(ROWS) == set(REVISE) == set(HISTORY_TABLES)
    assert set(APPEND) == set(APPEND_ONLY)


def _table(name: str) -> sa.Table:
    from nora.infra.sql.schema import METADATA
    return METADATA.tables[name]


def _key(table: str, row: dict[str, Any]) -> list[Any]:
    t = _table(table)
    return [t.c[c] == row[c] for c in HISTORY_TABLES[table]]


#: The kind tables `_write` also seeds registry parents for (and an edge's
#: endpoints) — the same registry+row write the store makes.
_GRAPH_TABLES = ("entity", "claim", "intention", "edge")


async def _write(conn: Any, table: str, i: int) -> None:
    row = ROWS[table](i)
    if table in _GRAPH_TABLES:
        parents = [{"id": row["id"], "unit": row["unit"], "kind": row["kind"]}]
        parents += [{"id": row[e], "unit": row["unit"], "kind": "entity"}
                    for e in ("src_id", "dst_id") if table == "edge"]
        await conn.execute(sa.insert(_table("registry")).values(parents))
    if table.startswith("rollup_") and "unit" in row:
        graph_id = f"generated:{table}:{i}"
        await conn.execute(sa.insert(_table("registry")).values(
            id=graph_id, unit=row["unit"], kind="aggregate",
        ))
        row["graph_id"] = graph_id
    if table == "claim_support":
        claim = {**ROWS["claim"](i), "id": row["claim_id"]}
        await conn.execute(sa.insert(_table("registry")).values(
            id=claim["id"], unit=claim["unit"], kind="claim",
        ))
        await conn.execute(sa.insert(_table("claim")).values(claim))
    if table == "graph_stimulus_claim":
        await conn.execute(sa.insert(_table("registry")).values(
            id=row["graph_id"], unit=row["unit"], kind="intention",
        ))
        await conn.execute(sa.insert(_table("graph_stimulus")).values(
            **{key: row[key] for key in ("unit", "kind", "graph_id", "occurrence")},
            source="clock", eligible_at=row["claimed"],
        ))
    if table == "watch_result":
        await _seed_watch(conn, row["watch_edge"], i)
    if table in {"forget_request", "dispute", "claim_support", "watch_result"}:
        await conn.execute(sa.insert(_table("registry")).values(
            id=row["graph_id"], unit=row["unit"],
            kind="aggregate" if table == "claim_support" else "item",
        ))
    await conn.execute(sa.insert(_table(table)).values(row))


async def _revise(conn: Any, table: str, i: int) -> None:
    column, value = REVISE[table]
    await conn.execute(
        sa.update(_table(table)).where(*_key(table, ROWS[table](i))).values({column: value(i)})
    )


async def _retire(conn: Any, table: str, i: int) -> None:
    await conn.execute(sa.delete(_table(table)).where(*_key(table, ROWS[table](i))))


async def _append(conn: Any, table: str, n: int) -> None:
    row = APPEND[table](n)
    if graph_id := row.get("graph_id"):
        await conn.execute(sa.insert(_table("registry")).values(
            id=graph_id, unit=row["unit"], kind="item",
        ))
    await conn.execute(sa.insert(_table(table)).values(row))


async def _seed_watch(conn: Any, edge_id: str, n: int) -> None:
    target_id = f"{edge_id}-target"
    intention_id = f"{edge_id}-intention"
    parents = {
        "entity": {**ROWS["entity"](n), "id": target_id},
        "intention": {
            **ROWS["intention"](n), "id": intention_id, "form": "maintain",
            "done_when_form": None, "done_when": None, "holds_while_form": "predicate",
            "holds_while": {"graph_id": target_id, "path": ["state"], "equals": "admitted"},
        },
        "edge": {
            **ROWS["edge"](n), "id": edge_id, "type": "watches", "basis": "committed",
            "src_id": intention_id, "dst_id": target_id,
        },
    }
    await conn.execute(sa.insert(_table("registry")).values([
        {key: row[key] for key in ("id", "unit", "kind")} for row in parents.values()
    ]))
    for table, row in parents.items():
        await conn.execute(sa.insert(_table(table)).values(row))


async def _held(engine: Any) -> dict[str, list[str]]:
    """Every restorable table's rows, as canonical JSON, sorted."""
    out: dict[str, list[str]] = {}
    async with engine.connect() as conn:
        for table in copied_tables():
            images = (await conn.execute(
                sa.text(f'SELECT to_jsonb(t)::text FROM "{table}" t')  # noqa: S608 — module constant
            )).scalars().all()
            out[table] = sorted(json.dumps(json.loads(i), sort_keys=True) for i in images)
    return out


async def _clock(engine: Any) -> datetime:
    async with engine.connect() as conn:
        return (await conn.execute(sa.text("SELECT clock_timestamp()"))).scalar_one()


@pytest.mark.asyncio
async def test_a_snapshot_at_each_moment_holds_exactly_what_was_held(
    clean_pg_url: str, pg_template: str,
) -> None:
    engine = create_sql_engine(clean_pg_url)
    tables = [t for t in copied_tables() if t in HISTORY_TABLES]
    try:
        async with engine.begin() as conn:  # before t1
            # `culture.head_revision_id` is a foreign key: its revisions first.
            for i in range(6):
                await _append(conn, "culture_revision", 100 + i)
            for table in tables:
                for i in (0, 1, 2, 3):
                    await _write(conn, table, i)
            for table, row in DERIVED.items():
                await conn.execute(sa.insert(_table(table)).values(row(0)))
            await conn.execute(sa.insert(_table("message_edit")).values(
                id=PRUNED_EDIT, message_id=ROWS["messages"](2)["id"], ts=MS0, body="edited",
            ))
        async with engine.begin() as conn:
            for table in tables:
                await _revise(conn, table, 3)
                await _retire(conn, table, 3)
            for table in APPEND:
                await _append(conn, table, 1)
        t1 = await _clock(engine)
        held_t1 = await _held(engine)

        async with engine.begin() as conn:  # between t1 and t2
            for table in tables:
                await _revise(conn, table, 1)
                await _retire(conn, table, 2)
                await _write(conn, table, 4)
            for table in APPEND:
                await _append(conn, table, 2)
        t2 = await _clock(engine)
        held_t2 = await _held(engine)

        async with engine.begin() as conn:  # after t2
            for table in tables:
                await _revise(conn, table, 4)
                await _retire(conn, table, 1)
                await _write(conn, table, 5)
            for table in APPEND:
                await _append(conn, table, 3)
        now = await _held(engine)
    finally:
        await engine.dispose()

    # Positive control: every table moved at each step, so a restore that
    # returned today's rows, or t1's at t2, cannot pass.
    for table in (*tables, *APPEND):
        assert held_t1[table] != held_t2[table] != now[table], table

    def edits(held: dict[str, list[str]]) -> set[int]:
        return {json.loads(r)["id"] for r in held["message_edit"]}

    assert PRUNED_EDIT in edits(held_t1) and PRUNED_EDIT not in edits(held_t2)
    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.connect() as conn:
            hidden = (await conn.execute(sa.text(
                "SELECT op FROM row_history WHERE table_name = 'message_edit' "
                "AND row_key = jsonb_build_object('id', CAST(:i AS integer)) "
                "AND superseded_at IS NULL"
            ), {"i": PRUNED_EDIT})).scalar_one()
    finally:
        await engine.dispose()
    assert hidden == "delete"  # the cascade was recorded: hidden, and kept

    for at, held in ((t1, held_t1), (t2, held_t2)):
        async with sandbox_as_of(clean_pg_url, at, template=pg_template) as (url, report):
            restored_engine = create_sql_engine(url)
            try:
                restored = await _held(restored_engine)
                async with restored_engine.connect() as conn:
                    for table in DERIVED:
                        assert await conn.scalar(
                            sa.select(sa.func.count()).select_from(_table(table)),
                        ) == 0
            finally:
                await restored_engine.dispose()
        assert report.orphaned == {}
        for table in copied_tables():
            assert restored[table] == held[table], f"{table} at {at.isoformat()}"


@pytest.mark.asyncio
async def test_a_history_version_missing_a_later_column_restores_it_as_null(
    clean_pg_url: str, pg_template: str,
) -> None:
    """0075_withdrawal added `hidden_at` to `messages` with no row_history
    catch-up: a version recorded before that migration has no `hidden_at` key in
    its `image` at all, never a `null` value. A restore to an instant covered by
    such a version must still produce a row — with `hidden_at` NULL, the column's
    own default for a row nothing ever hid — not an error and not a
    spuriously-hidden one."""
    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.begin() as conn:
            await _write(conn, "messages", 0)
        t1 = await _clock(engine)
        async with engine.begin() as conn:
            # Simulate a pre-0075 version: strip `hidden_at` from the image
            # `row_history` already holds, rather than leaving it `null` —
            # a key ABSENT is what every version recorded before the
            # migration actually looks like.
            await conn.execute(sa.text(
                "UPDATE row_history SET image = image - 'hidden_at' "
                "WHERE table_name = 'messages' "
                "AND row_key = jsonb_build_object('id', CAST(:i AS bigint))"
            ), {"i": ROWS["messages"](0)["id"]})
    finally:
        await engine.dispose()

    async with sandbox_as_of(clean_pg_url, t1, template=pg_template) as (url, report):
        restored_engine = create_sql_engine(url)
        try:
            async with restored_engine.connect() as conn:
                hidden_at = (await conn.execute(sa.text(
                    'SELECT hidden_at FROM "messages" WHERE id = :i'
                ), {"i": ROWS["messages"](0)["id"]})).scalar_one_or_none()
        finally:
            await restored_engine.dispose()
    assert report.orphaned == {}
    assert hidden_at is None


async def test_snapshot_restores_scoped_rollup_written_before_graph_planes(
    clean_pg_url: str, pg_template: str,
) -> None:
    engine = create_sql_engine(clean_pg_url)
    try:
        async with engine.begin() as conn:
            await _write(conn, "rollup_build", 0)
            await _write(conn, "rollup_tie", 0)
            await conn.execute(sa.text(
                "DELETE FROM row_history WHERE table_name = 'registry'"
            ))
            await conn.execute(sa.text(
                "UPDATE row_history SET image = image - 'graph_id' "
                "WHERE table_name = 'rollup_tie'"
            ))
        at = await _clock(engine)
        async with sandbox_as_of(clean_pg_url, at, template=pg_template) as (url, report):
            target = create_sql_engine(url)
            try:
                async with target.connect() as conn:
                    tie, registry = _table("rollup_tie"), _table("registry")
                    pair = await conn.execute(sa.select(
                        tie.c.unit, tie.c.person_a, tie.c.graph_id, registry.c.kind,
                    ).select_from(tie.join(registry, (
                        tie.c.graph_id == registry.c.id
                    ) & (tie.c.unit == registry.c.unit))))
                    assert pair.one()[:2] == ("u", "a")
                assert report.orphaned == {}
            finally:
                await target.dispose()
    finally:
        await engine.dispose()
