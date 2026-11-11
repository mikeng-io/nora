"""Advance the sandbox's world between two wakes.

Before wake `n`, copy only the Record the world produced between wake
`n-1` and `n`: the Record tables, their `row_history` chain, and the
wakeup feed (`stimulus`) — never anything her sessions derive (memory,
profiles, notebook, topics, lessons, Standing, `cognition_*`,
`stimulus_claim`/`retraction`, `kind='self'` marks). The next wake then
orients against the candidate's own earlier writes.

Record tables that take in-place writes (a message hidden, an
observation re-linked) are advanced by replaying their `row_history`
versions in the window — the same chain `as_of` reads — and append-only
tables by inserting the rows the window added, both with the source's
own ids. `stimulus_pending` is rebuilt exactly as the restore does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.infra.sql import snapshot_at
from nora.infra.sql.history import APPEND_ONLY
from nora.infra.sql.schema import METADATA, ROW_HISTORY

#: The Record's history tables: messages and their edit/reaction/delete
#: rows, plus platform observations. Everything else in HISTORY_TABLES is
#: what her sessions or pipelines own.
RECORD_HISTORY_TABLES = (
    "messages", "message_edit", "message_reaction", "message_delete",
    "platform_observation", "platform_object_state",
)

#: The Record's append-only tables: the social stream's rows and blob
#: prune marks. Session-owned append-only tables (`cognition_*`,
#: `stimulus_claim`, `decision_log`, `ledger_*`…) are never copied.
RECORD_APPEND_TABLES = tuple(
    t for t in APPEND_ONLY
    if t in (
        "social_item", "social_item_state", "social_item_count",
        "social_perception", "social_poll", "blob_tombstone",
    )
)

#: The wakeup feed is ingest's, so it advances with the Record — except
#: `self`, which only her own pace verbs write.
SESSION_STIMULUS_KINDS = ("self",)


@dataclass(frozen=True, slots=True)
class AdvanceReport:
    """What one advance wrote, per table — the evidence it copied no
    derived store: every name is a Record table or `stimulus`."""

    at_ms: int
    written: dict[str, int] = field(default_factory=dict)
    #: The `table_name` of every `row_history` row copied — the receipt
    #: checks each one, never the aggregate "row_history" key alone.
    history_tables: frozenset[str] = frozenset()

    def wrote_only_record(self) -> bool:
        record = {*RECORD_HISTORY_TABLES, *RECORD_APPEND_TABLES,
                  "stimulus", "row_history"}
        return (set(self.written) <= record
                and self.history_tables <= set(RECORD_HISTORY_TABLES))


async def bump_sequences(source: AsyncEngine, target: AsyncEngine) -> None:
    """Push every Record table's sequence — and `row_history`'s — past the
    source's current max id, so a row a session writes can never collide
    with a source row a later advance copies in."""
    async with source.connect() as src, target.connect() as dst, dst.begin():
        for table in (*RECORD_HISTORY_TABLES, *RECORD_APPEND_TABLES,
                      "stimulus", "row_history"):
            source_max = (await src.execute(sa.text(
                f'SELECT COALESCE(MAX(id), 0) FROM "{table}"'  # noqa: S608 — constants
            ))).scalar_one()
            seq = (await dst.execute(sa.text(
                "SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table}
            )).scalar_one_or_none()
            if seq is None:
                continue
            local_max = (await dst.execute(sa.text(
                f'SELECT COALESCE(MAX(id), 0) FROM "{table}"'  # noqa: S608 — constants
            ))).scalar_one()
            await dst.execute(
                sa.text("SELECT setval(:s, :v)"), {"s": seq, "v": max(local_max, source_max) + 1}
            )


async def advance_record(
    source: AsyncEngine, target: AsyncEngine,
    *, after_ms: int, at_ms: int,
) -> AdvanceReport:
    """Copy the world's Record rows dated `(after_ms, at_ms]` into the
    sandbox. The source is read-only; the target keeps everything its own
    sessions wrote."""
    prev, at = _dt(after_ms), _dt(at_ms)
    written: dict[str, int] = {}
    async with source.connect() as src, src.begin(), target.connect() as dst, dst.begin():
        await src.execute(sa.text(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        ))
        for table in RECORD_HISTORY_TABLES:
            written[table] = await _replay_history(src, dst, table, prev, at)
        copied, tables = await _copy_history(src, dst, prev, at)
        written["row_history"] = copied
        for table in RECORD_APPEND_TABLES:
            written[table] = await _copy_appended(src, dst, table, at)
        written["stimulus"] = await _copy_stimuli(src, dst, at)
        await snapshot_at._rebuild_derived(dst)
    return AdvanceReport(
        at_ms=at_ms, history_tables=tables,
        written={k: v for k, v in written.items() if v})


def _dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)


async def _replay_history(
    src: AsyncConnection, dst: AsyncConnection, table: str,
    prev: datetime, at: datetime,
) -> int:
    versions = (await src.execute(sa.text(
        'SELECT row_key, op, image FROM row_history WHERE table_name = :t '
        'AND recorded_at > :prev AND recorded_at <= :at ORDER BY id'
    ), {"t": table, "prev": prev, "at": at})).mappings().all()
    if not versions:
        return 0
    columns = await snapshot_at._target_columns(dst, table)
    updates = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c, _, _ in columns if c != "id")
    upsert = sa.text(
        snapshot_at._insert_sql(table, columns)
        + f' ON CONFLICT (id) DO UPDATE SET {updates}'
    )
    delete = sa.text(f'DELETE FROM "{table}" WHERE id = :id')  # noqa: S608 — constants
    touched = 0
    upserts: list[dict[str, Any]] = []
    for version in versions:
        image = version["image"]
        if version["op"] == "delete":
            if upserts:
                await dst.execute(upsert, {"rows": json.dumps(upserts, default=str)})
                upserts = []
            await dst.execute(delete, {"id": image.get("id")})
        else:
            upserts.append(dict(image))
        touched += 1
    if upserts:
        await dst.execute(upsert, {"rows": json.dumps(upserts, default=str)})
    return touched


async def _history_window(
    src: AsyncConnection, prev: datetime, at: datetime,
) -> list[Any]:
    """Record-table history rows in the window, never a derived chain."""
    return list((await src.execute(
        sa.select(ROW_HISTORY).where(
            ROW_HISTORY.c.recorded_at > prev,
            ROW_HISTORY.c.recorded_at <= at,
            ROW_HISTORY.c.table_name.in_(RECORD_HISTORY_TABLES),
        ).order_by(ROW_HISTORY.c.id)
    )).mappings().all())


async def _copy_history(
    src: AsyncConnection, dst: AsyncConnection, prev: datetime, at: datetime,
) -> tuple[int, frozenset[str]]:
    rows = await _history_window(src, prev, at)
    stmt = pg_insert(ROW_HISTORY).on_conflict_do_nothing(index_elements=["id"])
    for chunk in (rows[i:i + 2000] for i in range(0, len(rows), 2000)):
        await dst.execute(stmt, [dict(r) for r in chunk])
    return len(rows), frozenset(r["table_name"] for r in rows)


async def _copy_appended(
    src: AsyncConnection, dst: AsyncConnection, table: str, at: datetime,
) -> int:
    column = APPEND_ONLY[table]
    rows = (await src.execute(sa.text(
        f'SELECT * FROM "{table}" WHERE "{column}" <= :at ORDER BY id'  # noqa: S608
    ), {"at": at})).mappings().all()
    if not rows:
        return 0
    stmt = pg_insert(METADATA.tables[table]).on_conflict_do_nothing(
        index_elements=["id"]
    )
    inserted = 0
    for chunk in (rows[i:i + 1000] for i in range(0, len(rows), 1000)):
        result = await dst.execute(stmt, [dict(r) for r in chunk])
        inserted += int(result.rowcount or 0)
    return inserted


async def _copy_stimuli(
    src: AsyncConnection, dst: AsyncConnection, at: datetime,
) -> int:
    rows = (await src.execute(sa.text(
        "SELECT * FROM stimulus WHERE recorded_at <= :at AND kind != 'self' "
        "ORDER BY id"
    ), {"at": at})).mappings().all()
    if not rows:
        return 0
    stmt = pg_insert(METADATA.tables["stimulus"]).on_conflict_do_nothing(
        index_elements=["id"]
    )
    inserted = 0
    for chunk in (rows[i:i + 1000] for i in range(0, len(rows), 1000)):
        result = await dst.execute(stmt, [dict(r) for r in chunk])
        inserted += int(result.rowcount or 0)
    return inserted
