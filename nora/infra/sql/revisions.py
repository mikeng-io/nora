"""What instructions, config and code were in force, recorded at load.

The doctrine and `nora/dreamer/` hot-reload on mtime from the deploy host's
own checkout, `config.toml`/`policy.toml`/`identity.toml` live only on that
host, and the code is whatever image is running. None of that is answerable
as of T from git alone, so each load writes a row here, and
`nora.infra.sql.snapshot_at.in_force` reads the newest at or before T.

Content is stored once per distinct body (`revision_blob`, keyed by sha256)
and every revision row names it, so a restart that loads the same tree adds
a row, not a copy. Append-only: this store inserts and never updates.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.infra.sql.schema import (
    CONFIG_REVISION,
    DEPLOY_REVISION,
    DOCTRINE_REVISION,
    REVISION_BLOB,
)

__all__ = ["SqlRevisionStore", "content_sha256", "tree_sha256"]


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tree_sha256(files: Mapping[str, str]) -> str:
    """One digest for a set of files: over the sorted `path -> sha256` map,
    so the same tree hashes the same whatever order it was read in."""
    return content_sha256(json.dumps(dict(sorted(files.items())), separators=(",", ":")))


class SqlRevisionStore:
    """Writes `revision_blob`, `doctrine_revision`, `config_revision` and
    `deploy_revision`. Written by `nora.app.revisions.RevisionRecorder`;
    read by `nora.infra.sql.snapshot_at`."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record_instructions(
        self, *, corpus: str, files: Mapping[str, str], loaded_at: datetime,
        assembled_sha256: str | None, hostname: str,
    ) -> str:
        """One load of `corpus` (`doctrine` or `dreamer`): `files` maps each
        path relative to the corpus directory to its text. Returns the
        tree's digest."""
        digests = {path: content_sha256(text) for path, text in files.items()}
        tree = tree_sha256(digests)
        async with self._engine.begin() as conn:
            await self._blobs(conn, {digests[p]: t for p, t in files.items()})
            await conn.execute(sa.insert(DOCTRINE_REVISION).values(
                corpus=corpus, content_sha256=tree, assembled_sha256=assembled_sha256,
                files=digests, loaded_at=loaded_at, hostname=hostname,
            ))
        return tree

    async def record_config(
        self, *, file: str, path: str, content: str, loaded_at: datetime, hostname: str,
    ) -> str:
        """One config file as a process loaded it. Returns its digest."""
        digest = content_sha256(content)
        async with self._engine.begin() as conn:
            await self._blobs(conn, {digest: content})
            await conn.execute(sa.insert(CONFIG_REVISION).values(
                file=file, path=path, content_sha256=digest, loaded_at=loaded_at,
                hostname=hostname,
            ))
        return digest

    async def record_deploy(
        self, *, started_at: datetime, manifest_status: str, version: str | None,
        git_sha: str | None, built_at: str | None, image_id: str | None, hostname: str,
    ) -> None:
        """One process start and the code it ran."""
        async with self._engine.begin() as conn:
            await conn.execute(sa.insert(DEPLOY_REVISION).values(
                started_at=started_at, manifest_status=manifest_status, version=version,
                git_sha=git_sha, built_at=built_at, image_id=image_id, hostname=hostname,
            ))

    async def history_sizes(self) -> dict[str, tuple[int, int]]:
        """`table -> (versions, bytes)` in `row_history`: how much history each
        table has cost, for the gauges DEC-232's reversal condition reads."""
        async with self._engine.connect() as conn:
            rows = (await conn.execute(sa.text(
                "SELECT table_name, count(*), coalesce(sum(pg_column_size(h.*)), 0) "
                "FROM row_history h GROUP BY table_name"
            ))).all()
        return {str(r[0]): (int(r[1]), int(r[2])) for r in rows}

    @staticmethod
    async def _blobs(conn: AsyncConnection, blobs: Mapping[str, str]) -> None:
        if not blobs:
            return
        await conn.execute(
            pg_insert(REVISION_BLOB)
            .values([{"sha256": sha, "content": text} for sha, text in blobs.items()])
            .on_conflict_do_nothing(index_elements=[REVISION_BLOB.c.sha256])
        )
