"""Nora as of T: every store's as-of-T view, copied into a sandbox database.

`nora snapshot --at T`.
Where `nora.infra.sql.snapshot` reconstructs ONE room's four tiers and has
the eval layer rewrite them through the stores, this copies what EVERY
restorable store held at T, row for row with its own ids, into a migrated
database that holds nothing yet — the per-case sandbox
`nora.subject.sandbox.sandbox_database` clones from the blank template. A
subject composed against it with its clock at T reads what Nora read then.

WHAT IS COPIED, per `nora.infra.sql.history`:

- every `HISTORY_TABLES` table: the version of each row open at T in
  `row_history` (a row hidden by then is not in it);
- every `APPEND_ONLY` table: the rows recorded at or before T;
- `row_history` itself up to T, with any supersession after T cleared, so the
  sandbox's own history as of any T' <= T answers as the source's does.

WHAT IS NOT, and the snapshot says so rather than leaving it empty
silently (`EXCEPTED`, printed by the CLI):

- **memory** (Honcho): live and mutable by design, with no chain to read
  backwards. A case runs on its own empty Honcho workspace
  (`nora/subject/memory_workspace.py`) and seeds memory or declares itself
  memory-free.
- **the Rollup before migration 0071**: from 0071 it is versioned
  builds with history, restored like every history table — the build
  `rollup_build` says was current at T, with its rows as they were at T —
  but its history starts at that migration, so a T before it holds none.
  (A Rollup AT an arbitrary watermark T can also be rebuilt from the Record
  restored here: it is a pure function of it.)
- **events and RSVPs before migration 0074**: updated in place
  until `event_revision` and RSVP transition rows started there, so a T
  before it holds none; restored like every other history/append-only table
  from 0074 on.
- **an event's `unit_id` before an operator's cross-room backfill**
  (see `0074_event_revision`'s module docstring): every event
  migrated by 0074 holds `unit_id = ''` until that backfill runs, so a
  restore at a T before it ran cannot resolve those rows to a unit either.

WHAT WAS IN FORCE: `in_force` reads the doctrine, the Dreamer's doctrine and
objectives, the three config files and the deploy recorded at or before T
(`nora.app.revisions` writes them at load), and `materialise` writes them out
as files a subject can be composed from. A corpus with no load recorded by T
is reported unavailable, never guessed from today's files.

SEEDING WRITES NO HISTORY OF ITS OWN. The trigger is switched off for the
seeding transaction (`SET LOCAL nora.row_history = 'off'`,
`history.CAPTURE_SETTING`): recording the copy as new writes would date
every restored row at the moment of the copy.

The source is only read (`plan`, `in_force` and the read half of `seed` take
a read-only transaction at REPEATABLE READ, so every table is read at one
instant of the source).

VERSIONS ARE DATED WHEN THE TRIGGER RAN, NOT WHEN THEIR TRANSACTION
COMMITTED. `recorded_at` is `clock_timestamp()` at the write, so a restore at
T includes a version whose transaction was still open at T and committed
after it (or later rolled back never reached the table at all). The window
is a transaction's length, milliseconds on every write path here; a T that
falls inside one is the only case where the restore differs from what a
reader at T could see.

THE SOURCE AND THE TARGET MUST BE AT THE SAME MIGRATION. `seed` refuses a
mismatch rather than copying rows into columns the source never had.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.infra.sql.history import (
    APPEND_ONLY,
    CAPTURE_SETTING,
    DEFERRED,
    HISTORY_TABLES,
    append_only_as_of,
    as_of,
)
from nora.infra.sql.schema import (
    CONFIG_REVISION,
    DEPLOY_REVISION,
    DOCTRINE_REVISION,
    METADATA,
    REVISION_BLOB,
    ROW_HISTORY,
)

__all__ = [
    "BACKFILLED",
    "CONFIG_FILES",
    "EXCEPTED",
    "InForce",
    "InstructionLoad",
    "SeedReport",
    "SnapshotPlan",
    "SnapshotRefusedError",
    "copied_tables",
    "in_force",
    "materialise",
    "plan",
    "render_plan",
    "seed",
]

#: What a restore cannot hold, in the words the CLI prints.
EXCEPTED: tuple[str, ...] = (
    "memory (Honcho): live and mutable, no chain to read backwards; a case "
    "runs on its own empty Honcho workspace and seeds memory or declares "
    "itself memory-free",
    "the Rollup before migration 0071 (step 2): its history starts there, so a "
    "T before it holds no Rollup rows",
    "events and RSVPs before migration 0074 (step 2b): their history starts "
    "there, so a T before it holds none",
)

CONFIG_FILES: tuple[str, ...] = ("config.toml", "policy.toml", "identity.toml")


class SnapshotRefusedError(RuntimeError):
    """A snapshot that would answer wrongly rather than not at all: a source
    that keeps no history, a target that is the source or already holds
    rows."""


def copied_tables() -> tuple[str, ...]:
    """Every table a restore fills, in foreign-key order (parents first)."""
    wanted = set(HISTORY_TABLES) | set(APPEND_ONLY)
    return tuple(t.name for t in METADATA.sorted_tables if t.name in wanted)


@dataclass(frozen=True, slots=True)
class InstructionLoad:
    """One recorded load of an instruction corpus or a config file."""

    name: str
    loaded_at: datetime
    content_sha256: str
    #: path relative to the corpus directory -> content sha256. A config
    #: file is one entry named for itself.
    files: dict[str, str]
    assembled_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class InForce:
    """What was loaded at T. `None` is «no load recorded by T», which is
    unavailable, never «nothing was in force»."""

    doctrine: InstructionLoad | None
    dreamer: InstructionLoad | None
    config: dict[str, InstructionLoad | None]
    #: the newest `deploy_revision` row at or before T, as a mapping.
    deploy: dict[str, Any] | None

    def missing(self) -> tuple[str, ...]:
        """Each thing whose state at T cannot be named. A deploy row that
        carries no version, commit or image id (an image built with no
        `NORA_GIT_SHA`) names no code, so it is missing too."""
        out = [n for n, v in (("doctrine", self.doctrine), ("dreamer", self.dreamer))
               if v is None]
        deploy = self.deploy
        if deploy is None or not any(deploy.get(k) for k in ("version", "git_sha", "image_id")):
            out.append("deploy")
        out += [f for f, v in self.config.items() if v is None]
        return tuple(out)


@dataclass(frozen=True, slots=True)
class SnapshotPlan:
    """What a snapshot at `at` would copy. Written by nothing."""

    at: datetime
    source_revision: str | None
    rows: dict[str, int]
    history_rows: int
    in_force: InForce
    deferred: dict[str, str] = field(default_factory=lambda: dict(DEFERRED))
    excepted: tuple[str, ...] = EXCEPTED


@dataclass(frozen=True, slots=True)
class SeedReport:
    at: datetime
    copied: dict[str, int]
    #: rows an append-only child held whose parent was not held at T (an
    #: edit of a message pruned before T): not copied, counted.
    orphaned: dict[str, int]
    history_rows: int


def _require_aware(at: datetime) -> None:
    if at.tzinfo is None:
        raise ValueError("`at` must be timezone-aware: system time is an instant")


async def _source_revision(conn: AsyncConnection) -> str | None:
    try:
        return (await conn.execute(sa.text(
            "SELECT version_num FROM alembic_version"
        ))).scalar_one_or_none()
    except sa.exc.DBAPIError:
        return None


async def _has_table(conn: AsyncConnection, table: str) -> bool:
    return bool((await conn.execute(
        sa.text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f'public."{table}"'},
    )).scalar_one())


async def _read_only(conn: AsyncConnection) -> None:
    await conn.execute(sa.text(
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    ))


async def _require_history(conn: AsyncConnection) -> None:
    if not await _has_table(conn, "row_history"):
        raise SnapshotRefusedError(
            "the source keeps no row_history: it predates migration "
            "0068_system_time_history, so no store can be read as of T there"
        )


async def plan(source: AsyncEngine, at: datetime) -> SnapshotPlan:
    """Count what a snapshot at `at` would copy, and read what was in force.
    Writes nothing anywhere."""
    _require_aware(at)
    async with source.connect() as conn, conn.begin():
        await _read_only(conn)
        await _require_history(conn)
        rows: dict[str, int] = {}
        for table in copied_tables():
            if table in HISTORY_TABLES:
                rows[table] = int((await conn.execute(
                    sa.select(sa.func.count()).select_from(ROW_HISTORY).where(
                        ROW_HISTORY.c.table_name == table,
                        ROW_HISTORY.c.recorded_at <= at,
                        sa.or_(ROW_HISTORY.c.superseded_at.is_(None),
                               ROW_HISTORY.c.superseded_at > at),
                        ROW_HISTORY.c.op != "delete",
                    )
                )).scalar_one())
            elif await _has_table(conn, table):
                column = APPEND_ONLY[table]
                rows[table] = int((await conn.execute(sa.text(
                    f'SELECT count(*) FROM "{table}" WHERE "{column}" <= :at'  # noqa: S608 — table and column names are module constants, never input
                ), {"at": at})).scalar_one())
        history_rows = int((await conn.execute(
            sa.select(sa.func.count()).select_from(ROW_HISTORY)
            .where(ROW_HISTORY.c.recorded_at <= at)
        )).scalar_one())
        return SnapshotPlan(
            at=at,
            source_revision=await _source_revision(conn),
            rows=rows,
            history_rows=history_rows,
            in_force=await in_force(conn, at),
        )


async def in_force(conn: AsyncConnection, at: datetime) -> InForce:
    """The doctrine, the Dreamer's corpus, each config file and the deploy
    whose load is the newest at or before `at`."""
    _require_aware(at)

    async def corpus(name: str) -> InstructionLoad | None:
        row = (await conn.execute(
            sa.select(DOCTRINE_REVISION)
            .where(DOCTRINE_REVISION.c.corpus == name,
                   DOCTRINE_REVISION.c.loaded_at <= at)
            .order_by(DOCTRINE_REVISION.c.loaded_at.desc(), DOCTRINE_REVISION.c.id.desc())
            .limit(1)
        )).one_or_none()
        if row is None:
            return None
        return InstructionLoad(
            name=name, loaded_at=row.loaded_at, content_sha256=row.content_sha256,
            files=dict(row.files), assembled_sha256=row.assembled_sha256,
        )

    async def config(name: str) -> InstructionLoad | None:
        row = (await conn.execute(
            sa.select(CONFIG_REVISION)
            .where(CONFIG_REVISION.c.file == name, CONFIG_REVISION.c.loaded_at <= at)
            .order_by(CONFIG_REVISION.c.loaded_at.desc(), CONFIG_REVISION.c.id.desc())
            .limit(1)
        )).one_or_none()
        if row is None:
            return None
        return InstructionLoad(
            name=name, loaded_at=row.loaded_at, content_sha256=row.content_sha256,
            files={name: row.content_sha256},
        )

    if not await _has_table(conn, "doctrine_revision"):
        return InForce(doctrine=None, dreamer=None,
                       config=dict.fromkeys(CONFIG_FILES), deploy=None)
    deploy = (await conn.execute(
        sa.select(DEPLOY_REVISION)
        .where(DEPLOY_REVISION.c.started_at <= at)
        .order_by(DEPLOY_REVISION.c.started_at.desc(), DEPLOY_REVISION.c.id.desc())
        .limit(1)
    )).one_or_none()
    return InForce(
        doctrine=await corpus("doctrine"),
        dreamer=await corpus("dreamer"),
        config={name: await config(name) for name in CONFIG_FILES},
        deploy=None if deploy is None else dict(deploy._mapping),
    )


async def materialise(
    source: AsyncEngine, at: datetime, out_dir: Path,
) -> tuple[InForce, tuple[Path, ...]]:
    """Write what was in force at `at` under `out_dir`: `doctrine/`,
    `dreamer/` and the three config files, from their recorded content, plus
    `in_force.json` naming each load. A subject is composed from them with
    `overrides={"doctrine.dir": out_dir/"doctrine", "dreamer.dir":
    out_dir/"dreamer"}` and the recorded `policy.toml`/`identity.toml` as
    `SubjectSpec.policy`/`identity`. Nothing is written for a corpus with no
    recorded load; `in_force.json` lists it under `missing`.
    """
    written: list[Path] = []
    async with source.connect() as conn, conn.begin():
        await _read_only(conn)
        forced = await in_force(conn, at)

        async def body(sha: str) -> str:
            content = (await conn.execute(
                sa.select(REVISION_BLOB.c.content).where(REVISION_BLOB.c.sha256 == sha)
            )).scalar_one_or_none()
            if content is None:
                raise SnapshotRefusedError(f"revision_blob {sha} is named but not held")
            return str(content)

        for load in (forced.doctrine, forced.dreamer):
            if load is None:
                continue
            root = (out_dir / load.name).resolve()
            for rel, sha in sorted(load.files.items()):
                path = (root / rel).resolve()
                if root not in path.parents:
                    raise SnapshotRefusedError(f"recorded path {rel!r} leaves {root}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(await body(sha), encoding="utf-8")
                written.append(path)
        for name, load in forced.config.items():
            if load is None:
                continue
            path = out_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(await body(load.content_sha256), encoding="utf-8")
            written.append(path)
    manifest = out_dir / "in_force.json"
    manifest.write_text(json.dumps({
        "at": at.isoformat(),
        "missing": list(forced.missing()),
        "doctrine": _describe(forced.doctrine),
        "dreamer": _describe(forced.dreamer),
        "config": {n: _describe(v) for n, v in forced.config.items()},
        "deploy": None if forced.deploy is None else {
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in forced.deploy.items()
        },
        "excepted": list(EXCEPTED),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    written.append(manifest)
    return forced, tuple(written)


def _describe(load: InstructionLoad | None) -> dict[str, Any] | None:
    if load is None:
        return None
    return {
        "loaded_at": load.loaded_at.isoformat(),
        "content_sha256": load.content_sha256,
        "assembled_sha256": load.assembled_sha256,
        "files": load.files,
    }


def render_plan(shot: SnapshotPlan, *, source: str, dry_run: bool) -> list[str]:
    """The CLI's report: every restorable table's count at T, what was in
    force, and what a restore cannot hold. `source` is already redacted."""
    lines = [
        f"snapshot of {source} as of {shot.at.isoformat()}"
        + (" (dry run: nothing written)" if dry_run else ""),
        f"source migration: {shot.source_revision or 'unknown'}",
        "",
        "held at T:",
    ]
    width = max((len(t) for t in shot.rows), default=0)
    for table, count in shot.rows.items():
        lines.append(f"  {table.ljust(width)}  {count}")
    lines.append(f"  {'row_history'.ljust(width)}  {shot.history_rows} versions up to T")
    lines += ["", "in force at T:"]
    forced = shot.in_force

    def load(label: str, value: InstructionLoad | None) -> str:
        if value is None:
            return f"  {label}: UNAVAILABLE (no load recorded at or before T)"
        extra = f", served as {value.assembled_sha256[:12]}" if value.assembled_sha256 else ""
        return (
            f"  {label}: loaded {value.loaded_at.isoformat()}, "
            f"{len(value.files)} file(s), {value.content_sha256[:12]}{extra}"
        )

    lines.append(load("doctrine", forced.doctrine))
    lines.append(load("dreamer", forced.dreamer))
    for name, value in forced.config.items():
        lines.append(load(name, value))
    if forced.deploy is None:
        lines.append("  deploy: UNAVAILABLE (no start recorded at or before T)")
    else:
        d = forced.deploy
        lines.append(
            f"  deploy: started {d['started_at'].isoformat()}, manifest {d['manifest_status']}, "
            f"version {d['version'] or '-'}, commit {d['git_sha'] or '-'}, "
            f"image {d['image_id'] or '-'}"
        )
    lines += ["", "not restorable:"]
    lines += [f"  {e}" for e in shot.excepted]
    deferred = ", ".join(f"{t} (step {step})" for t, step in shot.deferred.items())
    lines.append(f"  left empty: {deferred}")
    return lines


def _same_database(a: str, b: str) -> bool:
    ua, ub = make_url(a), make_url(b)
    return (ua.host, ua.port, ua.database) == (ub.host, ub.port, ub.database)


async def _target_columns(conn: AsyncConnection, table: str) -> list[tuple[str, str | None, bool]]:
    """(name, default expression, not-null) of each column, in table order."""
    rows = (await conn.execute(sa.text(
        "SELECT column_name, column_default, is_nullable = 'NO' "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"
    ), {"t": table})).all()
    return [(r[0], r[1], bool(r[2])) for r in rows]


#: NOT NULL columns a migration added AFTER history began (0068) with a
#: backfill default it then DROPPED, keyed `(table, column)` to the value that
#: migration gave every existing row. The target column has no default to read
#: back, so without this an image recorded before the migration restores the
#: column as NULL and the whole table's insert is refused. One entry per
#: `alter_column(..., server_default=None)` on a copied table;
#: `tests/architecture/test_restore_knows_every_dropped_backfill.py` holds the
#: list to the migrations.
BACKFILLED: dict[tuple[str, str], str] = {
    # 0094_compute_jobs_generic_outbox: "every existing row is `legacy`".
    ("compute_jobs", "kind"): "'legacy'::text",
    ("compute_jobs", "payload"): "'{}'::jsonb",
}


def _insert_sql(table: str, columns: list[tuple[str, str | None, bool]]) -> str:
    """`INSERT ... SELECT` from a JSON array of row images.

    A NOT NULL column with a default reads its default where the image has no
    value: an image recorded before a later migration added the column holds
    no key for it, and the migration's own default is what such a row got.
    A NOT NULL column whose migration dropped its backfill default reads the
    backfill instead (`BACKFILLED`), for the same reason.
    A foreign key to ANOTHER table keeps only the rows whose parent is
    already there (parents are copied first); `seed` counts what it drops.
    """
    names = ", ".join(f'"{c}"' for c, _, _ in columns)
    exprs = []
    for name, default, not_null in columns:
        if not_null and default is not None and not default.startswith("nextval("):
            exprs.append(f'coalesce(r."{name}", {default})')
        elif not_null and (table, name) in BACKFILLED:
            exprs.append(f'coalesce(r."{name}", {BACKFILLED[(table, name)]})')
        else:
            exprs.append(f'r."{name}"')
    guards = []
    for fk in METADATA.tables[table].foreign_keys:
        parent = fk.column.table.name
        if parent == table:
            continue
        guards.append(
            f'(r."{fk.parent.name}" IS NULL OR EXISTS (SELECT 1 FROM "{parent}" p '  # noqa: S608 — table and column names are module constants, never input
            f'WHERE p."{fk.column.name}" = r."{fk.parent.name}"))'
        )
    where = f" WHERE {' AND '.join(guards)}" if guards else ""
    return (
        f'INSERT INTO "{table}" ({names}) SELECT {", ".join(exprs)} '  # noqa: S608 — table and column names are module constants, never input
        f'FROM jsonb_populate_recordset(NULL::"{table}", CAST(:rows AS jsonb)) AS r{where}'
    )


def _self_referencing(table: str) -> bool:
    return any(fk.column.table.name == table for fk in METADATA.tables[table].foreign_keys)


async def _reset_sequences(conn: AsyncConnection, table: str) -> None:
    serials = (await conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = :t "
        "AND (is_identity = 'YES' OR column_default LIKE 'nextval(%')"
    ), {"t": table})).scalars().all()
    relation = sa.table(table, schema="public")
    for column in serials:
        # Catalog names are identifiers, not SQL: let the dialect escape them.
        maximum = sa.func.max(sa.column(column))
        await conn.execute(
            sa.select(sa.func.setval(
                sa.func.pg_get_serial_sequence(sa.bindparam("t"), sa.bindparam("c")),
                maximum,
            )).select_from(relation).having(maximum.is_not(None)),
            {"t": conn.dialect.identifier_preparer.format_table(relation), "c": column},
        )


async def _refuse_a_target_that_holds_rows(conn: AsyncConnection) -> None:
    held = []
    for table in (*copied_tables(), "row_history"):
        if (await conn.execute(sa.text(f'SELECT EXISTS (SELECT 1 FROM "{table}")'))).scalar_one():  # noqa: S608 — table and column names are module constants, never input
            held.append(table)
    if held:
        raise SnapshotRefusedError(
            "the target already holds rows in " + ", ".join(held)
            + "; a snapshot is copied into an empty migrated database "
            "(a fresh sandbox), never merged into one"
        )


async def seed(source: AsyncEngine, target: AsyncEngine, at: datetime) -> SeedReport:
    """Copy every restorable store as of `at` from `source` into `target`.

    `target` must be a different, migrated database whose restorable tables
    are empty. One transaction on each side: the source's read-only and
    REPEATABLE READ, the target's with history capture off; a failure leaves
    the target as it was.
    """
    _require_aware(at)
    if _same_database(
        source.url.render_as_string(hide_password=False),
        target.url.render_as_string(hide_password=False),
    ):
        raise SnapshotRefusedError("the target is the source; a snapshot never writes the source")
    copied: dict[str, int] = {}
    orphaned: dict[str, int] = {}
    async with source.connect() as src, src.begin(), target.connect() as dst, dst.begin():
        await _read_only(src)
        await _require_history(src)
        source_revision = await _source_revision(src)
        target_revision = await _source_revision(dst)
        if source_revision != target_revision:
            raise SnapshotRefusedError(
                f"the source is at migration {source_revision!r} and the target at "
                f"{target_revision!r}; a snapshot copies rows between one schema only"
            )
        await dst.execute(sa.text(f"SET LOCAL {CAPTURE_SETTING} = 'off'"))
        await _refuse_a_target_that_holds_rows(dst)
        for table in copied_tables():
            if table in APPEND_ONLY and not await _has_table(src, table):
                continue
            columns = await _target_columns(dst, table)
            sql = sa.text(_insert_sql(table, columns))
            batch = 1_000_000 if _self_referencing(table) else 1000
            reader = (
                as_of(src, table, at, batch=batch) if table in HISTORY_TABLES
                else append_only_as_of(src, table, at, batch=batch)
            )
            offered = inserted = 0
            async for rows in reader:
                offered += len(rows)
                result = await dst.execute(sql, {"rows": json.dumps(rows, default=str)})
                inserted += int(result.rowcount or 0)
            copied[table] = inserted
            if offered != inserted:
                orphaned[table] = offered - inserted
            await _reset_sequences(dst, table)
        history_rows = await _copy_history(src, dst, at)
    return SeedReport(at=at, copied=copied, orphaned=orphaned, history_rows=history_rows)


async def _copy_history(src: AsyncConnection, dst: AsyncConnection, at: datetime) -> int:
    """`row_history` up to `at`, supersessions after `at` cleared: the
    sandbox's chain is the source's chain as it stood at `at`."""
    cleared = sa.case((ROW_HISTORY.c.superseded_at > at, None),
                      else_=ROW_HISTORY.c.superseded_at)
    cleared_by = sa.case((ROW_HISTORY.c.superseded_at > at, None),
                         else_=ROW_HISTORY.c.superseded_by)
    stmt = (
        sa.select(
            ROW_HISTORY.c.id, ROW_HISTORY.c.table_name, ROW_HISTORY.c.row_key,
            ROW_HISTORY.c.op, ROW_HISTORY.c.image, ROW_HISTORY.c.recorded_at,
            cleared.label("superseded_at"), cleared_by.label("superseded_by"),
        )
        .where(ROW_HISTORY.c.recorded_at <= at)
        .order_by(ROW_HISTORY.c.id)
    )
    total = 0
    result = await src.stream(stmt)
    async for part in result.partitions(2000):
        rows: list[Mapping[str, Any]] = [dict(r._mapping) for r in part]
        await dst.execute(sa.insert(ROW_HISTORY), rows)
        total += len(rows)
    await _reset_sequences(dst, "row_history")
    return total
