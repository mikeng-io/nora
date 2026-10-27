"""`RecordStore` over SQLAlchemy Core. Role: audit record + the tagging path's
context source. No semantics live here.

`append` is a bare INSERT. Pruning is a periodic sweep (`prune_all`), not part
of the write: a correlated-subquery prune per message would cost
O(rows-per-conversation) per write, and the ingest path's documented failure
behaviour under write contention is to drop the message.

The store is Postgres. MVCC lets writers proceed concurrently, so there is no
single-writer contention to count. The `nora_store_busy_total` metric NAME is
retained (frozen at 0) by the append-only metric-name policy
(`PRESERVED_METRIC_NAMES`); nothing increments it. asyncpg has no `lastrowid`,
so `append` gets the new id back via `RETURNING`.
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.bounds import cut, over_fetch
from nora.domain.external_files import external_files_json
from nora.domain.media import media_json
from nora.domain.models import (
    DeliveryStatus,
    EditedMessage,
    EditTarget,
    NormalizedMessage,
    RowEdit,
    TranscriptRow,
    UnattributedRow,
    mentions_json,
    parse_mentions_json,
)
from nora.domain.ports import RoomActivity
from nora.infra.sql import memory_index
from nora.infra.sql.migrations_runner import head_revision
from nora.infra.sql.schema import (
    MESSAGE_DELETE,
    MESSAGE_EDIT,
    MESSAGE_REACTION,
    MESSAGES,
    TAG_TRACE,
)

DEFAULT_MAX_PER_CONVERSATION = 500


def _latest_edit(*, edits_before: int | None = None) -> Any:
    """The newest `message_edit` of the `messages` row in the enclosing
    query, as a LATERAL subquery — one backwards seek on
    `idx_message_edit_message_ts` per row read.
    `edits_before` is the as-of bound `nora.infra.sql.snapshot` reads at: an
    edit made after the instant being reconstructed had not happened yet."""
    stmt = sa.select(
        MESSAGE_EDIT.c.ts, MESSAGE_EDIT.c.body, MESSAGE_EDIT.c.raw_json,
        MESSAGE_EDIT.c.mentions_json,
    ).where(MESSAGE_EDIT.c.message_id == MESSAGES.c.id)
    if edits_before is not None:
        stmt = stmt.where(MESSAGE_EDIT.c.ts <= edits_before)
    # NEWEST BY ARRIVAL, not by the edit's own clock. Ingest refuses an edit
    # older than the one on record (`edit_stale`) except where the platform
    # itself says it is newer — a Signal edit naming the latest revision, from
    # a device whose clock is behind — so the last row written IS the current
    # version, and ordering on `ts` would put a skewed-clock edit behind the
    # one it replaced.
    return (
        stmt.order_by(MESSAGE_EDIT.c.id.desc())
        .limit(1)
        .lateral("latest_edit")
    )


def turn_id_for_message(messages: Any = MESSAGES) -> Any:
    """Resolve a sent row through the indexed ids its trace actually recorded."""
    return (
        sa.select(TAG_TRACE.c.turn_id)
        .where(
            messages.c.is_nora == 1,
            TAG_TRACE.c.sent_message_ids.op("@>")(sa.func.jsonb_build_array(messages.c.id)),
            TAG_TRACE.c.conversation_id == messages.c.conversation_id,
            TAG_TRACE.c.platform == messages.c.platform,
        )
        .order_by(TAG_TRACE.c.turn_id.desc()).limit(1).correlate(messages).scalar_subquery()
    )


def transcript_select(*, edits_before: int | None = None) -> Any:
    """The columns every transcript read returns, with the newest edit
    folded in. ONE select for `recent`, `since`, `since_including_nora`,
    `record_query` and the snapshot's as-of read, so the fold cannot reach
    one and miss another; `transcript_row` is its mapping.

    **`hidden_at IS NULL`** (migration `0075_withdrawal`, step 2c): a message
    `nora person forget` (removed) hid is excluded from every transcript this select
    powers — a prompt block, a tool result, `record_query`, and the
    snapshot's as-of read, which copies the historical row image (`hidden_at`
    as it stood at T) into the sandbox and then reads it through this same
    select, so a snapshot before a forget correctly shows the row unhidden."""
    latest = _latest_edit(edits_before=edits_before)
    turn_id = turn_id_for_message()
    return sa.select(
        MESSAGES.c.id, MESSAGES.c.sender_id, MESSAGES.c.sender_name,
        MESSAGES.c.ts, MESSAGES.c.body, MESSAGES.c.is_nora,
        MESSAGES.c.delivery_status, MESSAGES.c.raw_json,
        MESSAGES.c.quoted_ts, MESSAGES.c.media_json, MESSAGES.c.person_id,
        MESSAGES.c.external_files_json,
        turn_id.label("turn_id"),
        latest.c.ts.label("edit_ts"), latest.c.body.label("edit_body"),
        latest.c.raw_json.label("edit_raw_json"),
    ).select_from(MESSAGES.outerjoin(latest, sa.true())).where(
        MESSAGES.c.hidden_at.is_(None),
    )


def _edit_mentions_json(message: NormalizedMessage) -> str | None:
    """NULL — «unknown» — when the platform's edit could not carry mentions
    and none were carried forward (`EditTarget.mentions_known`)."""
    if message.edit_of is not None and not message.edit_of.mentions_known:
        return None
    return mentions_json(message.mentions)


def transcript_row(r: Any) -> TranscriptRow:
    """The column-row -> `TranscriptRow` mapping for every `transcript_select`
    read — they select the same columns, so the mapping exists once.

    An edited row carries its LATEST body and keeps its own `raw_json`;
    what it first said and the latest envelope ride on `edit`."""
    edited = r.edit_ts is not None
    return TranscriptRow(
        id=r.id,
        sender_id=r.sender_id,
        sender_name=r.sender_name,
        ts=r.ts,
        body=r.edit_body if edited else r.body,
        is_nora=bool(r.is_nora),
        delivery_status=DeliveryStatus(r.delivery_status),
        raw_json=r.raw_json,
        quoted_ts=r.quoted_ts,
        media_json=r.media_json,
        external_files_json=r.external_files_json,
        person_id=r.person_id,
        turn_id=r.turn_id,
        edit=(
            RowEdit(ts=r.edit_ts, was=r.body, raw_json=r.edit_raw_json)
            if edited else None
        ),
    )


class SqlRecordStore:
    """Satisfies `nora.domain.ports.RecordStore`."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        max_per_conversation: int = DEFAULT_MAX_PER_CONVERSATION,
        metrics: Any = None,
        mention_persons: Any = None,
        extraction_unit: Any = None,
    ) -> None:
        """`metrics` is INJECTED, never imported.

        `nora.infra` sits below `nora.obs` in import-linter's layers contract,
        so importing `nora.obs.metrics` here — at module level or lazily inside
        this function, which import-linter detects either way — breaks contract
        2. Injection is also what the project's test-seam policy requires:
        collaborators come through `__init__`, never a module-level mutable
        singleton. The composition root passes `METRICS`; `None` means this
        store simply does not count, which is correct for unit tests.

        `mention_persons` resolves one @-mentioned platform account id to its
        person id (`Persons.resolve`'s answer, without the ref), so the row
        stores `mention_persons_json` beside `mentions_json` and the
        memory-index projector — pure SQL, `identity.toml` not its to read —
        can draw `mentions` edges (25 §2). `None` stores NULL («unknown») and
        no edges are drawn.
        """
        self._engine = engine
        self._max_per_conversation = max_per_conversation
        self._metrics = metrics
        self._mention_persons = mention_persons
        self._extraction_unit = extraction_unit

    def _resolved_mentions_json(self, message: NormalizedMessage) -> str | None:
        """`mention_persons_json` for one message or edit: the sorted person
        ids its @-mentions resolve to, or None when that is unknown (no
        resolver wired, or an edit the platform gave no mentions for)."""
        if self._mention_persons is None:
            return None
        if message.edit_of is not None and not message.edit_of.mentions_known:
            return None
        resolved = {
            pid for raw in message.mentions
            if (pid := self._mention_persons(message.platform, raw)) is not None
        }
        return json.dumps(sorted(resolved))

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
            # Same INSERT, no extra parameter: the parser already extracted it
            # onto the message (as for `person_id`, DEC-15).
            quoted_ts=message.quote.quoted_ts if message.quote else None,
            # Same INSERT (DEC-43). Custody is resolved BEFORE this write —
            # `nora.ingest.stages.media` runs ahead of the audit stage — so the
            # row that claims a handle and the blob that backs it are committed
            # together. An UPDATE afterwards would open a window in which the
            # record says `[image a3f2c1]` while nothing can produce `a3f2c1`.
            media_json=media_json(message.media_blobs),
            # Same INSERT: the file links ingest recognised.
            external_files_json=external_files_json(message.external_files),
            # Same INSERT, both: the handle an edit names its target by, and
            # the mentions the gate compares an edit against. "" is the parsers'
            # «no handle» and stores as NULL.
            platform_message_id=message.message_id or None,
            mentions_json=mentions_json(message.mentions),
            mention_persons_json=self._resolved_mentions_json(message),
        ).returning(MESSAGES.c.id)
        # `RETURNING id` + `scalar_one`, not `lastrowid`: asyncpg has no
        # `lastrowid` (it is always `None`), so the id the row was assigned is
        # read back from the statement itself. `scalar_one` also asserts exactly
        # one row came back, which a bare INSERT always produces.
        async with self._engine.begin() as conn:
            row_id = int((await conn.execute(stmt)).scalar_one())
            if self._extraction_unit is not None:
                from nora.infra.sql.extraction import enqueue

                unit = self._extraction_unit(message.platform, message.conversation_id)
                if unit is None:
                    raise ValueError("extraction owner unavailable")
                await enqueue(conn, message_id=row_id, message=message, unit=unit,
                              is_source=not is_nora)
            # Its node, attachments and edges in the same transaction; a failure
            # queues the projection and never costs the message.
            await memory_index.project(conn, "message", row_id, metrics=self._metrics)
        return row_id

    async def recent(self, *, conversation_id: str, limit: int) -> list[TranscriptRow]:
        """Newest `limit` rows for one conversation, returned oldest-first.

        A non-positive `limit` short-circuits to `[]` rather than reaching
        SQL: SQLAlchemy renders `.limit(-1)` as `LIMIT -1`, which SQLite
        treats as unbounded, not zero. `FakeRecordStore.recent` already
        returns `[]` for `limit <= 0`; this keeps both implementations
        agreeing (see `tests/contract/test_audit_store_contract.py`).
        """
        return await self._newest(conversation_id=conversation_id, limit=limit)

    async def media_rows(
        self, *, conversation_id: str, since_ts: int, limit: int,
    ) -> list[TranscriptRow]:
        """See `nora.domain.ports.RecordStore.media_rows`. One indexed seek on
        `idx_messages_conv_ts` bounded by `since_ts`; `media_json IS NOT NULL`
        is the column's own «Nora holds something for this row» (NULL, never
        `[]`). Raises on a database error, unlike the swallowing reads: the
        caller is a tool that must say `unavailable`, not «no photos»."""
        if limit <= 0:
            return []
        stmt = (
            transcript_select()
            .where(
                MESSAGES.c.conversation_id == conversation_id,
                MESSAGES.c.ts >= since_ts,
                MESSAGES.c.is_nora == 0,
                MESSAGES.c.media_json.is_not(None),
            )
            .order_by(MESSAGES.c.ts.desc(), MESSAGES.c.id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [self._row(r) for r in rows]

    async def external_file_rows(
        self, *, conversation_id: str, since_ts: int, limit: int,
    ) -> list[TranscriptRow]:
        """See `nora.domain.ports.RecordStore.external_file_rows`. The
        `media_rows` query with the other column; raises on a database error."""
        if limit <= 0:
            return []
        stmt = (
            transcript_select()
            .where(
                MESSAGES.c.conversation_id == conversation_id,
                MESSAGES.c.ts >= since_ts,
                MESSAGES.c.is_nora == 0,
                MESSAGES.c.external_files_json.is_not(None),
            )
            .order_by(MESSAGES.c.ts.desc(), MESSAGES.c.id.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [self._row(r) for r in rows]

    async def recent_before(
        self, *, conversation_id: str, before_ts: int, limit: int,
    ) -> list[TranscriptRow]:
        """See `nora.domain.ports.RecordStore.recent_before`."""
        return await self._newest(
            conversation_id=conversation_id, limit=limit, before_ts=before_ts,
        )

    async def _newest(
        self, *, conversation_id: str, limit: int, before_ts: int | None = None,
    ) -> list[TranscriptRow]:
        """`recent` and `recent_before`'s one query: the newest `limit` rows,
        optionally ending before `before_ts`, oldest-first. One body, so the
        two reads cannot drift in their over-fetch, tie-break or edit fold."""
        if limit <= 0:
            return []
        where = [MESSAGES.c.conversation_id == conversation_id]
        if before_ts is not None:
            where.append(MESSAGES.c.ts < before_ts)
        stmt = (
            transcript_select()
            .where(*where)
            .order_by(MESSAGES.c.ts.desc(), MESSAGES.c.id.desc())
            # OVER-FETCH BY ONE (`nora.domain.bounds`). This window is what
            # every per-turn derived block is built from, so a silent cut here
            # makes an empty block ambiguous three ways — nothing to say, its
            # subject fell outside the window, or the window itself was
            # clipped — and only this makes the third visible. The ambiguity
            # is a property of the window, not of its readers.
            .limit(over_fetch(limit))
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        # Cut BEFORE reversing: the query is newest-first, so the rows to keep
        # are the leading `limit`. Cutting after the reverse would drop the
        # newest rows and keep the oldest — the opposite of what this reads.
        kept = cut(
            rows, limit=limit, site="audit.recent", metrics=self._metrics,
        )
        return [self._row(r) for r in reversed(kept)]

    async def depth_to(self, *, conversation_id: str, ts: int) -> int | None:
        """See `nora.domain.ports.RecordStore.depth_to`.

        One indexed query, `idx_messages_conv_ts (conversation_id, ts DESC)` —
        the same index `recent` already seeks. `count(*) FILTER (WHERE ts >=
        :ts)` is `recent`'s own `ts DESC, id DESC` depth to the OLDEST row
        carrying this timestamp (a duplicated `ts` sorts its lowest-`id` row
        last within the tie, which is also `nora.agent.prefill._by_ts`'s
        tie-break — the two must agree, or this would size a window that
        still excludes the row `_parent_of` actually resolves to).
        `count(*) FILTER (WHERE ts = :ts)` is the existence check: zero means
        no row here carries `ts` at all, so `depth` above answers nothing and
        must not be handed back as though it does.
        """
        stmt = sa.select(
            sa.func.count().filter(MESSAGES.c.ts >= ts).label("depth"),
            sa.func.count().filter(MESSAGES.c.ts == ts).label("matches"),
        ).where(MESSAGES.c.conversation_id == conversation_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one()
        return int(row.depth) if row.matches else None

    async def since(
        self, *, conversation_id: str, after_id: int, limit: int
    ) -> list[TranscriptRow]:
        """Human rows with `id > after_id`, ASCENDING, at most `limit`.

        The consolidation cursor, not a window read: unlike `recent`, this is
        unbounded by recency and never reverses — it already selects ascending.
        `is_nora` rows are excluded IN THE QUERY (`MESSAGES.c.is_nora == 0`,
        the INTEGER-column predicate `audit.py`'s `unattributed` already uses)
        so a caller can never forget to filter Nora's own words out of the
        evidence the Dreamer derives from. Same `limit <= 0` short-circuit as
        `recent`, and for the same reason.
        """
        if limit <= 0:
            return []
        stmt = (
            transcript_select()
            .where(
                MESSAGES.c.conversation_id == conversation_id,
                MESSAGES.c.id > after_id,
                MESSAGES.c.is_nora == 0,
            )
            .order_by(MESSAGES.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [self._row(r) for r in rows]

    async def since_including_nora(
        self, *, conversation_id: str, after_id: int, limit: int
    ) -> list[TranscriptRow]:
        """All rows with `id > after_id`, ASCENDING, at most `limit`.

        `since`'s cursor shape WITHOUT the `is_nora` exclusion:
        the standing fold needs Nora's own
        rows to re-judge her position, and the Dreamer's `since` must keep
        excluding them — so the fold gets its own read rather than a flag on
        the existing one. Same `limit <= 0` short-circuit as `since`/`recent`,
        and for the same reason.
        """
        if limit <= 0:
            return []
        stmt = (
            transcript_select()
            .where(
                MESSAGES.c.conversation_id == conversation_id,
                MESSAGES.c.id > after_id,
            )
            .order_by(MESSAGES.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(r: Any) -> TranscriptRow:
        return transcript_row(r)

    async def find_edited(
        self, *, conversation_id: str, target: EditTarget, sender_id: str,
    ) -> EditedMessage | None:
        """See `nora.domain.ports.RecordStore.find_edited`.

        A HIDDEN row is found too, and says so (`EditedMessage.hidden`): a
        second delete of it is `already_hidden`, not unknown, and an edit of it
        must be refused rather than orphaned silently. This is the one read of
        `messages` that is not a transcript read.

        The handle is an index read on `idx_messages_conv_platform_message_id`.
        Signal's timestamp is matched against the message AND its edits, which
        is a scan of one conversation — it runs once per Signal edit, never per message. A
        target naming neither answers None without a query. Should two rows
        match — a platform re-delivering a message, a duplicated millisecond —
        the EARLIEST wins, `prefill._by_ts`'s rule:
        an edit changes something that already existed."""
        prefer: Any = sa.literal(0)
        if target.message_id:
            match = MESSAGES.c.platform_message_id == target.message_id
        elif target.sent_ts is not None and sender_id:
            # The ORIGINAL's sent-timestamp, OR one of its edits'. A second
            # edit of a Signal message can name the first edit's timestamp
            # rather than the original's — it is the revision the sender's
            # client was looking at — so matching `messages.ts` alone would
            # orphan every edit after the first.
            edited_at_that_ts = sa.exists().where(
                MESSAGE_EDIT.c.message_id == MESSAGES.c.id,
                MESSAGE_EDIT.c.ts == target.sent_ts,
            )
            # The author PREFERRED, not required: signal-cli can name one
            # sender by phone on the message and by ACI on its edit, and an
            # exact-id filter would orphan the edit. The caller decides
            # authorship (`edit_target._same_author`), which resolves both
            # forms to one person; this lookup only has to find the message.
            match = sa.or_(MESSAGES.c.ts == target.sent_ts, edited_at_that_ts)
            prefer = sa.case((MESSAGES.c.sender_id == sender_id, 0), else_=1)
        else:
            return None
        latest = _latest_edit()
        quoted = MESSAGES.alias("quoted")
        replies_to_nora = sa.exists().where(
            quoted.c.conversation_id == MESSAGES.c.conversation_id,
            quoted.c.ts == MESSAGES.c.quoted_ts,
            quoted.c.is_nora == 1,
        )
        stmt = (
            sa.select(
                MESSAGES.c.id, MESSAGES.c.ts, MESSAGES.c.sender_id,
                MESSAGES.c.person_id, MESSAGES.c.is_nora, MESSAGES.c.body,
                MESSAGES.c.mentions_json, MESSAGES.c.platform_message_id,
                MESSAGES.c.media_json,
                MESSAGES.c.hidden_at.is_not(None).label("hidden"),
                latest.c.ts.label("edit_ts"), latest.c.body.label("edit_body"),
                latest.c.mentions_json.label("edit_mentions_json"),
                replies_to_nora.label("replies_to_nora"),
                sa.select(sa.func.array_agg(MESSAGE_EDIT.c.ts))
                .where(MESSAGE_EDIT.c.message_id == MESSAGES.c.id)
                .scalar_subquery().label("edit_timestamps"),
            )
            .select_from(MESSAGES.outerjoin(latest, sa.true()))
            .where(MESSAGES.c.conversation_id == conversation_id, match)
            .order_by(prefer, MESSAGES.c.id.asc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            r = (await conn.execute(stmt)).first()
        if r is None:
            return None
        edited = r.edit_ts is not None
        return EditedMessage(
            id=r.id, ts=r.ts, sender_id=r.sender_id, person_id=r.person_id,
            is_nora=bool(r.is_nora),
            body=r.edit_body if edited else r.body,
            mentions=parse_mentions_json(
                r.edit_mentions_json if edited else r.mentions_json
            ),
            first_body=r.body,
            platform_message_id=r.platform_message_id or "",
            replies_to_nora=bool(r.replies_to_nora),
            edited_ts=r.edit_ts,
            media_json=r.media_json,
            edit_timestamps=frozenset(int(t) for t in (r.edit_timestamps or ())),
            hidden=bool(r.hidden),
        )

    async def quoted_ts_of(self, *, conversation_id: str, ts: int) -> int:
        """See `nora.domain.ports.RecordStore.quoted_ts_of`."""
        exact = sa.select(MESSAGES.c.ts).where(
            MESSAGES.c.conversation_id == conversation_id, MESSAGES.c.ts == ts,
        ).limit(1)
        via_edit = (
            sa.select(MESSAGES.c.ts)
            .select_from(MESSAGES.join(MESSAGE_EDIT, MESSAGE_EDIT.c.message_id == MESSAGES.c.id))
            .where(MESSAGES.c.conversation_id == conversation_id, MESSAGE_EDIT.c.ts == ts)
            .order_by(MESSAGES.c.id.asc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            if (await conn.execute(exact)).first() is not None:
                return ts
            row = (await conn.execute(via_edit)).first()
        return int(row.ts) if row is not None else ts

    async def append_edit(self, *, message_id: int, message: NormalizedMessage) -> None:
        """See `nora.domain.ports.RecordStore.append_edit`. One INSERT; the
        edited row is never touched."""
        stmt = sa.insert(MESSAGE_EDIT).values(
            message_id=message_id,
            ts=message.timestamp,
            body=message.body,
            raw_json=json.dumps(message.raw),
            mentions_json=_edit_mentions_json(message),
            mention_persons_json=self._resolved_mentions_json(message),
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
            # The node's text is the newest edit's.
            await memory_index.project(conn, "message", message_id, metrics=self._metrics)

    async def append_reaction(
        self, *, message_id: int, message: NormalizedMessage, person_id: str | None,
    ) -> int:
        """See `nora.domain.ports.RecordStore.append_reaction`. One INSERT."""
        touch = message.touch
        stmt = sa.insert(MESSAGE_REACTION).values(
            message_id=message_id,
            sender_id=message.sender_id,
            person_id=person_id,
            emoji=touch.emoji if touch is not None else "",
            removed=int(touch.removed) if touch is not None else 0,
            ts=message.timestamp,
            raw_json=json.dumps(message.raw),
        ).returning(MESSAGE_REACTION.c.id)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.scalar_one())

    async def append_delete(
        self, *, message_id: int, message: NormalizedMessage, person_id: str | None,
    ) -> int:
        """See `nora.domain.ports.RecordStore.append_delete`. One INSERT; the
        deleted row is never touched."""
        stmt = sa.insert(MESSAGE_DELETE).values(
            message_id=message_id,
            sender_id=message.sender_id,
            person_id=person_id,
            ts=message.timestamp,
            raw_json=json.dumps(message.raw),
        ).returning(MESSAGE_DELETE.c.id)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return int(result.scalar_one())

    async def hide_message(self, *, message_id: int, now_ms: int) -> bool:
        """See `nora.domain.ports.RecordStore.hide_message`: one conditional UPDATE, so a
        second delete of the same message keeps the first hide's timestamp."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.update(MESSAGES)
                .where(MESSAGES.c.id == message_id, MESSAGES.c.hidden_at.is_(None))
                .values(hidden_at=now_ms)
            )
            if result.rowcount:
                # A hidden message is never a hit: its node, its attachments'
                # nodes and their edges go in the hide's own write.
                await memory_index.project(
                    conn, "message", message_id, metrics=self._metrics,
                )
        return bool(result.rowcount)

    async def aclose(self) -> None:
        await self._engine.dispose()

    async def set_delivery_outcome(
        self, *, row_id: int, status: DeliveryStatus, ts: int | None = None
    ) -> None:
        """Close out one recorded send: its status, and the PLATFORM's own
        timestamp for it.

        `ts` is here — rather than on a write of its own — because there is no
        second write to be had. `Deliverer` records the row before it sends
        (A9), stamping `int(time.time() * 1000)` from the local clock; Signal
        then assigns its OWN sent-timestamp, and that is what a later reply's
        `dataMessage.quote.id` points at. They differ by a few ms:
        close enough to look right, never equal, so a join by `ts` silently
        resolved every sender in the room except Nora herself — and a reply to
        Nora is the most common reply in a group where Nora is tagged. Riding
        the post-send update that already existed keeps the reply path a single
        statement — one write for the outcome and the corrected timestamp
        together, not two.

        `ts=None` leaves the timestamp alone. A platform that answered without
        one is a thinner transcript; a row stamped 0 or NULL is a corrupt one,
        and `recent` orders on that column.

        An unknown `row_id` is a deliberate silent no-op, matching the legacy
        `nora/store/db.py::set_delivery_status`: `Deliverer` calls this after a
        send it cannot un-send, and raising here would convert a harmless race
        into a failure under R-5.
        """
        values: dict[str, Any] = {"delivery_status": status.value}
        if ts is not None:
            values["ts"] = ts
        stmt = sa.update(MESSAGES).where(MESSAGES.c.id == row_id).values(**values)
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
            # Only the platform's timestamp is indexed (the node's `at_ms`);
            # the delivery status is not, so a status-only close projects
            # nothing.
            if ts is not None:
                await memory_index.project(conn, "message", row_id, metrics=self._metrics)

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
                MESSAGES.c.ts, MESSAGES.c.body, MESSAGES.c.conversation_id,
                MESSAGES.c.raw_json,
                # The custody index, so lazy adoption can join what a photo
                # showed before it feeds the row to Honcho. Without it a
                # back-filled row reaches memory as `[image a3f2c1]` and nothing
                # else.
                MESSAGES.c.media_json,
            )
            .where(MESSAGES.c.person_id.is_(None), MESSAGES.c.is_nora == 0)
            # A withdrawn message is never replayed into memory: adoption FEEDS
            # these rows to Honcho (`nora.app.adoption`), so a message its author
            # deleted would otherwise reach memory the day its sender is first
            # mapped.
            .where(MESSAGES.c.hidden_at.is_(None))
            .order_by(MESSAGES.c.ts.asc(), MESSAGES.c.id.asc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [
            UnattributedRow(
                id=r.id, platform=r.platform, sender_id=r.sender_id, ts=r.ts,
                body=r.body, conversation_id=r.conversation_id, raw_json=r.raw_json,
                media_json=r.media_json,
            )
            for r in rows
        ]

    async def set_person_id(self, *, row_id: int, person_id: str) -> None:
        """Attribute one already-audited row. One row per statement, on purpose.

        This is the only write lazy adoption performs, and it is off the ingest
        path entirely. A single batched UPDATE across the whole backlog would
        hold a transaction open for the length of the batch and lock every row
        it touched; a row at a time, interleaved with the caller's awaited Honcho
        feed, never does.

        An unknown `row_id` is a silent no-op, matching `set_delivery_outcome` —
        the row may have been pruned out from under the backfill between the
        scan and this call, which is harmless, not a failure.
        """
        stmt = (
            sa.update(MESSAGES)
            .where(MESSAGES.c.id == row_id)
            .values(person_id=person_id)
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
            # The attribution is the `authored_by` and `shared_by` edges.
            await memory_index.project(conn, "message", row_id, metrics=self._metrics)

    async def room_activity(self, *, conversation_id: str, since_ts: int) -> RoomActivity:
        """See `nora.domain.ports.RecordStore.room_activity`.

        One aggregate over `idx_messages_conv_ts (conversation_id, ts DESC)`,
        the index `recent` seeks: `count(*) FILTER (WHERE ts >= :since)` and
        `max(ts)`, both over human rows only. `max` over no rows is NULL,
        which is `last_ts=None`, never 0.
        """
        stmt = sa.select(
            sa.func.count().filter(MESSAGES.c.ts >= since_ts).label("recent"),
            # People, not accounts: `person_id` when resolved, the raw sender
            # id only for a row nobody resolved (DEC-14).
            sa.func.count(sa.distinct(
                sa.func.coalesce(MESSAGES.c.person_id, MESSAGES.c.sender_id)
            )).filter(MESSAGES.c.ts >= since_ts).label("speakers"),
            sa.func.max(MESSAGES.c.ts).label("last_ts"),
        ).where(
            MESSAGES.c.conversation_id == conversation_id,
            MESSAGES.c.is_nora == 0,
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one()
        return RoomActivity(
            count=int(row.recent),
            last_ts=None if row.last_ts is None else int(row.last_ts),
            speakers=int(row.speakers),
        )

    async def stats(self) -> dict[str, Any]:
        """Store counters plus `schema_revision`.

        `schema_revision` is `head_revision()` — the code's own migration head, from the
        `versions/` directory this process ships with — not
        `current_revision()`, a read of the `alembic_version` row in the
        connected database. That means it can never disagree with the code
        it is running inside of, which also means it cannot detect the one
        failure mode a schema-shape check exists for: this process pointed
        at a database `run_migrations` has not (yet, or ever) brought to
        that head. Reporting the code's belief rather than the database's
        actual state is deliberate: `current_revision()` opens a new
        synchronous connection per call (it is not async-native, unlike
        everything else `SqlRecordStore` does), and `stats()` is on
        `/health`'s hot path. `GET /health` only reads `stats()["messages"]`,
        so a caller of `schema_revision` should read it as "what this process
        expects," not "what this database's `alembic_version` row says."
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

    async def drain_index_queue(self, *, limit: int = 500) -> int:
        """Re-project what the memory index's projections queued, from the
        nora process (`nora.app.runtime.memory_drain_loop`); the analytics
        process drains too (`SqlRollupStore.drain_index_queue`), and the two
        take disjoint rows. Returns the rows tried."""
        async with self._engine.begin() as conn:
            return await memory_index.drain_queue(conn, limit=limit, metrics=self._metrics)

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
        stmt = (
            sa.delete(MESSAGES).where(MESSAGES.c.id.in_(doomed)).returning(MESSAGES.c.id)
        )
        async with self._engine.begin() as conn:
            pruned = list((await conn.execute(stmt)).scalars())
            # A pruned message's node goes with it.
            await memory_index.drop(conn, "message", pruned, metrics=self._metrics)
        return len(pruned)
