"""`AuditStore` over SQLAlchemy Core. Role: audit record + the tagging path's
context source. No semantics live here.

`append` is a bare INSERT. It used to run the INSERT and a correlated-subquery
prune in one transaction on every message — O(rows-per-conversation) per write.
WAL permits exactly one writer, so under an N-listener burst that produced
SQLITE_BUSY, and the ingest path's documented failure behaviour is to drop the
message. Pruning is now a periodic sweep (`prune_all`), so a retention chore
can never cost an audit row.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.models import (
    DeliveryStatus,
    NormalizedMessage,
    TranscriptRow,
    UnattributedRow,
)
from nora.infra.sql.migrations_runner import head_revision
from nora.infra.sql.schema import MESSAGES

DEFAULT_MAX_PER_CONVERSATION = 500


class SqlAuditStore:
    """Satisfies `nora.domain.ports.AuditStore`."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        max_per_conversation: int = DEFAULT_MAX_PER_CONVERSATION,
        metrics: Any = None,
    ) -> None:
        """`metrics` is INJECTED, never imported.

        `nora.infra` sits below `nora.obs` in import-linter's layers contract,
        so importing `nora.obs.metrics` here — at module level or lazily inside
        this function, which import-linter detects either way — breaks contract
        2. Injection is also what the project's test-seam policy requires:
        collaborators come through `__init__`, never a module-level mutable
        singleton. The composition root passes `METRICS`; `None` means this
        store simply does not count, which is correct for unit tests.
        """
        self._engine = engine
        self._max_per_conversation = max_per_conversation
        self._metrics = metrics

    async def append(
        self,
        *,
        message: NormalizedMessage,
        is_nora: bool = False,
        delivery_status: DeliveryStatus = DeliveryStatus.PENDING,
        person_id: str | None = None,
    ) -> int:
        stmt = sa.insert(MESSAGES).values(
            platform=message.platform,
            conversation_id=message.conversation_id,
            conversation_type=message.conversation_type,
            sender_id=message.sender_id,
            sender_name=message.sender_name,
            person_id=person_id,
            ts=message.timestamp,
            body=message.body,
            is_nora=int(is_nora),
            delivery_status=delivery_status.value,
            raw_json=json.dumps(message.raw),
        )
        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(stmt)
        except OperationalError as exc:
            self._note_busy(exc)
            raise
        # `lastrowid`, not `inserted_primary_key[0]`: the latter is typed
        # `Any | None` and fails strict mypy with "not indexable". `lastrowid`
        # is a plain `int` and is what the legacy `db.append` used too.
        return int(result.lastrowid)

    async def recent(self, *, conversation_id: str, limit: int) -> list[TranscriptRow]:
        """Newest `limit` rows for one conversation, returned oldest-first.

        A non-positive `limit` short-circuits to `[]` rather than reaching
        SQL: SQLAlchemy renders `.limit(-1)` as `LIMIT -1`, which SQLite
        treats as unbounded, not zero. `FakeAuditStore.recent` already
        returns `[]` for `limit <= 0`; this keeps both implementations
        agreeing (see `tests/contract/test_audit_store_contract.py`).
        """
        if limit <= 0:
            return []
        stmt = (
            sa.select(
                MESSAGES.c.id, MESSAGES.c.sender_id, MESSAGES.c.sender_name,
                MESSAGES.c.ts, MESSAGES.c.body, MESSAGES.c.is_nora,
                MESSAGES.c.delivery_status, MESSAGES.c.raw_json,
            )
            .where(MESSAGES.c.conversation_id == conversation_id)
            .order_by(MESSAGES.c.ts.desc(), MESSAGES.c.id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            TranscriptRow(
                id=r.id,
                sender_id=r.sender_id,
                sender_name=r.sender_name,
                ts=r.ts,
                body=r.body,
                is_nora=bool(r.is_nora),
                delivery_status=DeliveryStatus(r.delivery_status),
                raw_json=r.raw_json,
            )
            for r in reversed(rows)
        ]

    async def aclose(self) -> None:
        await self._engine.dispose()

    async def set_delivery_status(self, *, row_id: int, status: DeliveryStatus) -> None:
        """An unknown `row_id` is a deliberate silent no-op, matching the
        legacy `nora/store/db.py::set_delivery_status`: `Deliverer` calls this
        after a send it cannot un-send, and raising here would convert a
        harmless race into a failure under R-5."""
        stmt = (
            sa.update(MESSAGES)
            .where(MESSAGES.c.id == row_id)
            .values(delivery_status=status.value)
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def unattributed(self, *, limit: int) -> list[UnattributedRow]:
        """Audited rows still attributed to nobody, OLDEST FIRST.

        The ordering is load-bearing, not incidental: the caller
        (`nora.app.adoption`) replays these into a person's Honcho peer, and a
        peer's memory has to read chronologically. `ts`, not `id` — a row's
        insert order is the order Nora *received* it, which under signal-cli's
        catch-up delivery is not the order it was *sent*. `id` only breaks ties.

        `is_nora = 0` is part of the predicate deliberately. Nora's own replies
        are audited with `person_id` NULL too, and her account is never written
        to identity.toml (DEC-9), so they can never resolve to a person — every
        one of them would otherwise be re-scanned, forever, on every restart.

        A non-positive `limit` short-circuits for the same reason `recent` does:
        SQLAlchemy renders `.limit(-1)` as SQLite's unbounded `LIMIT -1`.
        """
        if limit <= 0:
            return []
        stmt = (
            sa.select(
                MESSAGES.c.id, MESSAGES.c.platform, MESSAGES.c.sender_id,
                MESSAGES.c.ts, MESSAGES.c.body, MESSAGES.c.raw_json,
            )
            .where(MESSAGES.c.person_id.is_(None), MESSAGES.c.is_nora == 0)
            .order_by(MESSAGES.c.ts.asc(), MESSAGES.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            UnattributedRow(
                id=r.id, platform=r.platform, sender_id=r.sender_id, ts=r.ts,
                body=r.body, raw_json=r.raw_json,
            )
            for r in rows
        ]

    async def set_person_id(self, *, row_id: int, person_id: str) -> None:
        """Attribute one already-audited row. One row per statement, on purpose.

        This is the only write lazy adoption performs, and it is off the ingest
        path entirely (see this module's docstring: WAL permits exactly one
        writer, and the ingest path's documented failure behaviour is to DROP
        the message). A single batched UPDATE across the whole backlog would
        hold that one writer for the length of the batch; a row at a time,
        interleaved with the caller's awaited Honcho feed, never does.

        An unknown `row_id` is a silent no-op, matching `set_delivery_status` —
        the row may have been pruned out from under the backfill between the
        scan and this call, which is harmless, not a failure.
        """
        stmt = (
            sa.update(MESSAGES)
            .where(MESSAGES.c.id == row_id)
            .values(person_id=person_id)
        )
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        except OperationalError as exc:
            self._note_busy(exc)
            raise

    async def stats(self) -> dict[str, Any]:
        """`schema_revision` replaces the retired `schema_version`.

        It is `head_revision()` — the code's own migration head, from the
        `versions/` directory this process ships with — not
        `current_revision()`, a read of the `alembic_version` row in the
        connected database. That means it can never disagree with the code
        it is running inside of, which also means it cannot detect the one
        failure mode a schema-shape check exists for: this process pointed
        at a database `run_migrations` has not (yet, or ever) brought to
        that head. Reporting the code's belief rather than the database's
        actual state was a deliberate choice, not an oversight — the
        alternative, `current_revision()`, opens a new synchronous
        connection per call (it is not async-native, unlike everything
        else `SqlAuditStore` does), and `stats()` is on `/health`'s hot
        path. `GET /health` today only reads `stats()["messages"]`, not
        this field, so the distinction has no live consequence yet — but a
        future caller of `schema_revision` should read it as "what this
        process expects," not "what this database's `alembic_version`
        row actually says."
        """
        stmt = sa.select(
            sa.func.count(),
            sa.func.count(sa.distinct(MESSAGES.c.conversation_id)),
            sa.func.coalesce(sa.func.sum(MESSAGES.c.is_nora), 0),
            sa.func.coalesce(sa.func.max(MESSAGES.c.ts), 0),
        ).select_from(MESSAGES)
        async with self._engine.connect() as conn:
            total, conversations, nora, latest = (await conn.execute(stmt)).one()
        return {
            "messages": int(total),
            "conversations": int(conversations),
            "nora_messages": int(nora),
            "latest_ts": int(latest),
            "schema_revision": head_revision(),
        }

    async def prune_all(self) -> int:
        """Trim every conversation to the retention window. Called periodically
        by the runtime, never on the write path — see this module's docstring."""
        keep = self._max_per_conversation
        ranked = sa.select(
            MESSAGES.c.id,
            sa.func.row_number()
            .over(
                partition_by=MESSAGES.c.conversation_id,
                order_by=[MESSAGES.c.ts.desc(), MESSAGES.c.id.desc()],
            )
            .label("rank"),
        ).subquery()
        doomed = sa.select(ranked.c.id).where(ranked.c.rank > keep)
        stmt = sa.delete(MESSAGES).where(MESSAGES.c.id.in_(doomed))
        try:
            async with self._engine.begin() as conn:
                result = await conn.execute(stmt)
        except OperationalError as exc:
            self._note_busy(exc)
            raise
        return int(result.rowcount or 0)

    def _note_busy(self, exc: OperationalError) -> None:
        if self._metrics is not None and "database is locked" in str(exc).lower():
            self._metrics.inc("nora_store_busy_total")
