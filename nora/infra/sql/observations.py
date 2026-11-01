"""`SqlObservationStore` — the typed platform-observation Record.

`platform_observation` is the append-mostly history (one row per admitted
source transition, replayed source digests deduped by the unique
constraint); `platform_object_state` is the fold's compare-and-swap head.
The incremental update and `as_of` run ONE fold —
`nora.domain.observations.fold_observation`/`rebuild_head` — so the current
head, a full replay and the fake agree by construction.

`append` writes the row and the folded head in one `engine.begin()`
transaction — the "Record append + cheap current head update committed
together" of design 02 — and `tx_hook` lets a caller join that transaction
the way `RecordStore.append`'s does. Concurrent appends to one object
serialize on the head row's `FOR UPDATE` read; the FIRST writer claims the
row with an `ON CONFLICT DO NOTHING` insert and a loser re-reads under the
same lock, so two simultaneous first appends cannot race the insert.

`tx_hook` may be a plain callable or return an awaitable: a downstream
obligation that itself awaits (an enqueue, a state write) is awaited INSIDE
the transaction — a hook detached as a task would be an obligation the
commit never joined.
"""

from __future__ import annotations

import inspect
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.models import EditTarget
from nora.domain.observations import (
    OBSERVATION_CONTRACT,
    Coverage,
    Observation,
    ObservationHead,
    SourceScope,
    StoredObservation,
    fold_observation,
    payload_from_dict,
    rebuild_head,
    target_from_key,
    target_key,
)

from .schema import (
    MESSAGE_EDIT,
    MESSAGES,
    PLATFORM_OBJECT_STATE,
    PLATFORM_OBSERVATION,
)

OBS = PLATFORM_OBSERVATION
ST = PLATFORM_OBJECT_STATE

#: The head-shape version the fold writes — bumped when a stored `state`
#: shape changes so an old head can be rebuilt rather than misread.
PROJECTION_REV = 1


def _scope_columns(scope: SourceScope) -> dict[str, Any]:
    return {
        "platform": scope.platform,
        "account_key": scope.account_key,
        "conversation_id": scope.conversation_id,
        "thread": scope.thread,
    }


def _room_where(t: Any, scope: SourceScope) -> Any:
    """`platform` + `conversation_id` + `thread` — the room's boundary, no
    account. A read with an EMPTY `account_key` holds the room's boundary
    (the unit the grounder reads for): `platform` and `conversation_id`
    still name the room, so a scope cannot widen past it."""
    return sa.and_(
        t.c.platform == scope.platform,
        t.c.conversation_id == scope.conversation_id,
        t.c.thread == scope.thread,
    )


def _scope_where(scope: SourceScope) -> Any:
    if not scope.account_key:
        return _room_where(OBS, scope)
    t = OBS
    return sa.and_(
        _room_where(t, scope), t.c.account_key == scope.account_key)


def _st_scope_where(scope: SourceScope) -> Any:
    if not scope.account_key:
        return _room_where(ST, scope)
    t = ST
    return sa.and_(
        _room_where(t, scope), t.c.account_key == scope.account_key)


def _row_to_stored(row: sa.RowMapping) -> StoredObservation:
    contract = int(row["contract"])
    if contract != OBSERVATION_CONTRACT:
        raise ValueError(f"unsupported stored observation contract {contract}")
    obs = Observation(
        object_kind=row["kind"],
        scope=SourceScope(
            platform=row["platform"], account_key=row["account_key"],
            conversation_id=row["conversation_id"],
            thread=row["thread"] or ""),
        object_ref=row["object_ref"],
        target=target_from_key(row["target_key"]),
        target_author_id=row["target_author_id"] or "",
        operation=row["operation"],
        payload=payload_from_dict(row["kind"], dict(row["payload"])),
        subject_ref=row["subject_ref"] or "",
        actor_id=row["actor_id"] or "",
        actor_status=row["actor_status"],
        occurred_ms=row["occurred_ms"],
        received_ms=int(row["received_ms"]),
        time_quality=row["time_quality"],
        source_revision=row["source_revision"],
        source_position=row["source_position"],
        revision_scope=row["revision_scope"] or "",
        coverage=Coverage.from_dict(dict(row["coverage"])),
    )
    return StoredObservation(
        id=int(row["id"]), event_id=row["event_id"],
        source_digest=row["source_digest"], observation=obs,
        target_message_id=row["target_message_id"])


def _head_to_row(head: ObservationHead) -> dict[str, Any]:
    return {
        "state": head.state, "applied": head.applied,
        "head_observation_id": head.head_id or None,
        "source_position": head.position,
        "revision_scope": head.scope or None,
        "observed_ms": head.observed_ms, "ordering": head.ordering,
        "coverage": dict(head.coverage), "projection_rev": PROJECTION_REV,
    }


def _row_to_head(row: sa.RowMapping) -> ObservationHead:
    return ObservationHead(
        kind=row["kind"], object_ref=row["object_ref"],
        state=dict(row["state"]), applied=int(row["applied"]),
        head_id=int(row["head_observation_id"] or 0),
        position=row["source_position"],
        scope=row["revision_scope"] or "",
        observed_ms=row["observed_ms"], ordering=row["ordering"],
        coverage=dict(row["coverage"]))


class SqlObservationStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def append(
        self, *, observation: Observation, event_id: str, source_digest: str,
        target_message_id: int | None = None, tx_hook: Any = None,
    ) -> int | None:
        obs = observation
        scope = obs.scope
        async with self._engine.begin() as conn:
            ins = await conn.execute(
                pg_insert(OBS).values(
                    contract=OBSERVATION_CONTRACT, kind=obs.object_kind,
                    event_id=event_id, source_digest=source_digest,
                    **_scope_columns(scope),
                    object_ref=obs.object_ref, operation=obs.operation,
                    target_key=target_key(obs.target, author_id=obs.target_author_id),
                    target_author_id=obs.target_author_id or None,
                    target_message_id=target_message_id,
                    actor_id=obs.actor_id or None,
                    actor_status=obs.actor_status,
                    subject_ref=obs.subject_ref or None,
                    occurred_ms=obs.occurred_ms,
                    received_ms=obs.received_ms,
                    time_quality=obs.time_quality,
                    source_revision=obs.source_revision,
                    source_position=obs.source_position,
                    revision_scope=obs.revision_scope or None,
                    payload=obs.payload.to_dict(),
                    coverage=obs.coverage.to_dict(),
                ).on_conflict_do_nothing(
                    constraint="uq_platform_observation_source",
                ).returning(OBS.c.id))
            row_id = ins.scalar_one_or_none()
            if row_id is None:
                return None  # replay of an admitted source event: no effect
            stored = StoredObservation(
                id=int(row_id), event_id=event_id,
                source_digest=source_digest, observation=obs,
                target_message_id=target_message_id)
            await self._apply_head(conn, stored)
            if tx_hook is not None:
                awaited = tx_hook(conn, int(row_id))
                if inspect.isawaitable(awaited):
                    await awaited
            return int(row_id)

    async def _apply_head(
        self, conn: AsyncConnection, stored: StoredObservation,
    ) -> None:
        obs = stored.observation
        sel = (
            sa.select(ST)
            .where(_st_scope_where(obs.scope), ST.c.kind == obs.object_kind,
                   ST.c.object_ref == obs.object_ref)
            .with_for_update())
        row = (await conn.execute(sel)).mappings().one_or_none()
        if row is None:
            # FIRST WRITER: two appends to one new object can both read an
            # absent head (an absent row holds no lock). Claim the row with a
            # conflict-safe insert of our fold; the loser re-reads the
            # winner's row under FOR UPDATE and folds from THAT, so one
            # object's appends serialize rather than violating the unique key
            # or last-write-wins-ing a head.
            first = fold_observation(None, stored)
            ins = await conn.execute(
                pg_insert(ST).values(
                    **_scope_columns(obs.scope), kind=obs.object_kind,
                    object_ref=obs.object_ref, **_head_to_row(first))
                .on_conflict_do_nothing(
                    constraint="uq_platform_object_state_object")
                .returning(ST.c.id))
            if ins.scalar_one_or_none() is not None:
                return
            row = (await conn.execute(sel)).mappings().one()
        head = _row_to_head(row)
        new_head = fold_observation(head, stored)
        await conn.execute(
            sa.update(ST).where(ST.c.id == row["id"]).values(
                **_head_to_row(new_head), updated_at=sa.func.now()))

    async def current(
        self, *, scope: SourceScope, kind: str, object_ref: str,
    ) -> ObservationHead | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                sa.select(ST).where(_st_scope_where(scope), ST.c.kind == kind,
                                    ST.c.object_ref == object_ref))
            ).mappings().one_or_none()
        return None if row is None else _row_to_head(row)

    async def heads(
        self, *, scope: SourceScope, kind: str | None = None,
        limit: int = 100,
    ) -> list[ObservationHead]:
        where = _st_scope_where(scope)
        if kind is not None:
            where = sa.and_(where, ST.c.kind == kind)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(ST).where(where).order_by(ST.c.id)
                .limit(max(limit, 0)))).mappings().all()
        return [_row_to_head(r) for r in rows]

    async def history(
        self, *, scope: SourceScope, kind: str, object_ref: str,
        after_id: int = 0, limit: int = 1000,
    ) -> list[StoredObservation]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(OBS).where(
                    _scope_where(scope), OBS.c.kind == kind,
                    OBS.c.object_ref == object_ref, OBS.c.id > after_id)
                .order_by(OBS.c.id).limit(max(limit, 0)))).mappings().all()
        return [_row_to_stored(r) for r in rows]

    async def as_of(
        self, *, scope: SourceScope, kind: str, object_ref: str,
        through_id: int, limit: int = 1000,
    ) -> tuple[ObservationHead | None, bool]:
        """Bounded as-of: at most `limit` retained rows fold. `more` says the
        history continues past the bound — the head then folds an EARLIER
        point than `through_id` and the caller must treat it as partial,
        never as the requested state."""
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(OBS).where(
                    _scope_where(scope), OBS.c.kind == kind,
                    OBS.c.object_ref == object_ref, OBS.c.id <= through_id)
                .order_by(OBS.c.id).limit(max(limit, 0) + 1))).mappings().all()
        more = len(rows) > max(limit, 0)
        head = rebuild_head(
            [_row_to_stored(r) for r in rows[: max(limit, 0)]],
            kind=kind, object_ref=object_ref)
        return head, more

    async def recent(
        self, *, scope: SourceScope, since_ms: int = 0, limit: int = 50,
    ) -> list[StoredObservation]:
        at = sa.func.coalesce(OBS.c.occurred_ms, OBS.c.received_ms)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(OBS).where(_scope_where(scope), at >= since_ms)
                .order_by(at.desc(), OBS.c.id.desc())
                .limit(max(limit, 0)))).mappings().all()
        return [_row_to_stored(r) for r in rows]

    async def by_ids(
        self, *, scope: SourceScope, ids: tuple[int, ...], limit: int = 200,
    ) -> list[StoredObservation]:
        """Specific observation rows of one scope, bounded — the grounder's
        batch read of the rows agenda stimuli name. A row of ANOTHER scope
        never resolves here: the scope is the read boundary."""
        if not ids:
            return []
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(OBS).where(
                    _scope_where(scope), OBS.c.id.in_(tuple(ids)))
                .order_by(OBS.c.id).limit(max(limit, 0)))).mappings().all()
        return [_row_to_stored(r) for r in rows]

    async def resolve_targets(
        self, *, scope: SourceScope, target_key: str, message_id: int,
    ) -> int:
        async with self._engine.begin() as conn:
            res = await conn.execute(
                sa.update(OBS).where(
                    _scope_where(scope), OBS.c.target_key == target_key,
                    OBS.c.target_message_id.is_(None))
                .values(target_message_id=message_id))
            return int(res.rowcount or 0)

    async def unresolved_targets(
        self, *, scope: SourceScope, limit: int = 200,
    ) -> list[StoredObservation]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                sa.select(OBS).where(
                    _scope_where(scope), OBS.c.target_key.isnot(None),
                    OBS.c.target_message_id.is_(None))
                .order_by(OBS.c.id).limit(max(limit, 0)))).mappings().all()
        return [_row_to_stored(r) for r in rows]

    async def aclose(self) -> None:
        await self._engine.dispose()


async def resolve_target_message_id(
    engine: AsyncEngine, *, platform: str, conversation_id: str,
    target: EditTarget | None, target_author_id: str = "",
) -> int | None:
    """Resolve an observation's `target` to a `messages.id`, or None when the
    Record cannot point at it — an unresolved target stays unresolved (the
    observation is retained for `resolve_targets`), NEVER guessed at a
    same-timestamp row.

    `message_id` targets match `platform_message_id`. Timestamp targets match
    `messages.ts` OR one of its edits (`SqlRecordStore`'s edit-target rule:
    a second edit can name the first edit's timestamp) and prefer the row
    authored by `target_author_id` when several match — the author's identity
    is preserved separately from the actor's (commit `38b4a157f`)."""
    if target is None or (not target.message_id and target.sent_ts is None):
        return None
    if target.message_id:
        match = MESSAGES.c.platform_message_id == target.message_id
        prefer = sa.literal(0)
    elif target.sent_ts is not None:
        edited_at = sa.exists().where(
            MESSAGE_EDIT.c.message_id == MESSAGES.c.id,
            MESSAGE_EDIT.c.ts == target.sent_ts,
        )
        match = sa.or_(MESSAGES.c.ts == target.sent_ts, edited_at)
        prefer = (
            sa.case((MESSAGES.c.sender_id == target_author_id, 0), else_=1)
            if target_author_id else sa.literal(0))
    else:
        return None
    stmt = (
        sa.select(MESSAGES.c.id)
        .where(MESSAGES.c.platform == platform,
               MESSAGES.c.conversation_id == conversation_id, match)
        .order_by(prefer, MESSAGES.c.id.asc()).limit(1))
    async with engine.connect() as conn:
        row = (await conn.execute(stmt)).first()
    return int(row[0]) if row is not None else None
