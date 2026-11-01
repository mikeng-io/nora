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

import dataclasses
import inspect
import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from nora.domain.models import EditTarget, ReactionRow
from nora.domain.observations import (
    OBSERVATION_CONTRACT,
    Coverage,
    Observation,
    ObservationHead,
    SourceScope,
    StoredObservation,
    fold_observation,
    fold_target_reactions,
    parse_handle,
    payload_from_dict,
    rebuild_head,
    target_from_key,
    target_key,
    union_head,
)

from .audit import REACTION_STATES_MAX
from .schema import (
    MESSAGE_EDIT,
    MESSAGE_REACTION,
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


def _row_to_stored(row: Any) -> StoredObservation:
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
        target_message_id=row["target_message_id"],
        recorded_at=row.get("recorded_at"))


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
        coverage=dict(row["coverage"]),
        account_key=row["account_key"] or "",
        thread=row["thread"] or "")


#: `message_reaction.created_at` as epoch microseconds — the durable commit
#: clock `fold_target_reactions` orders legacy deltas by, computed where the
#: rows are so a text column never reaches the fold as a string.
_MR_AT = sa.cast(
    sa.extract(
        "epoch",
        sa.cast(MESSAGE_REACTION.c.created_at, sa.DateTime(timezone=True)))
    * 1_000_000, sa.BigInteger)
_OBS_AT = sa.cast(
    sa.extract("epoch", OBS.c.recorded_at) * 1_000_000, sa.BigInteger)


def _rx_union_stmt(
    scope: SourceScope, refs: frozenset[str], *,
    through_id: int | None = None, per_object: int = REACTION_STATES_MAX,
) -> Any:
    """One tagged row set for a union reaction read — the head surfaces'
    batched counterpart of `audit.reaction_states`.

    `t` rows: the scope's typed reaction rows for `refs`, ranked per
    `(account_key, thread, object_ref)` and bounded, each carrying its
    commit clock; `n` rows: the same groups' REAL totals (before the
    bound), so a cut is detectable. `l` rows: bounded latest-per-key
    `message_reaction` tuples for the targets the typed rows resolved to;
    `m` rows: their real totals. `b` rows (as-of only): whether the bound
    resolved — a bound that names no row cannot order legacy events, which
    the fold must flag rather than guess.
    """
    null_int = sa.cast(sa.null(), sa.BigInteger)
    null_text = sa.cast(sa.null(), sa.Text)
    null_json = sa.cast(sa.null(), pg.JSONB)
    obs_where = sa.and_(
        _scope_where(scope), OBS.c.kind == "reaction",
        OBS.c.object_ref.in_(tuple(refs)))
    if through_id is not None:
        obs_where = sa.and_(obs_where, OBS.c.id <= through_id)
    t_ranked = sa.select(
        OBS, _OBS_AT.label("at"),
        sa.func.row_number().over(
            partition_by=(OBS.c.account_key, OBS.c.thread,
                          OBS.c.object_ref),
            order_by=OBS.c.id).label("rn"),
    ).where(obs_where).cte("rx_t_ranked")
    t_bounded = sa.select(t_ranked).where(
        t_ranked.c.rn <= per_object).cte("rx_t_bounded")
    t_meta = sa.select(
        t_ranked.c.account_key, t_ranked.c.thread, t_ranked.c.object_ref,
        sa.func.count().label("total"),
        sa.func.max(sa.func.coalesce(
            t_ranked.c.occurred_ms, t_ranked.c.received_ms)).label("obs"),
    ).group_by(
        t_ranked.c.account_key, t_ranked.c.thread, t_ranked.c.object_ref,
    ).cte("rx_t_meta")
    mids = sa.select(t_bounded.c.target_message_id).where(
        t_bounded.c.target_message_id.isnot(None)).distinct().cte("rx_mids")
    # The legacy side counts only where its durable commit clock proves it
    # preceded the as-of bound — the same genuine ordering the unbounded
    # read gets for free.
    mr_where = MESSAGE_REACTION.c.message_id.in_(
        sa.select(mids.c.target_message_id))
    bound = None
    if through_id is not None:
        bound = sa.select(OBS.c.recorded_at).where(
            _scope_where(scope), OBS.c.id == through_id).cte("rx_bound")
        mr_where = sa.and_(
            mr_where,
            sa.cast(MESSAGE_REACTION.c.created_at,
                    sa.DateTime(timezone=True))
            <= sa.select(bound.c.recorded_at).scalar_subquery())  # type: ignore[assignment]
    l_scope = sa.select(
        MESSAGE_REACTION.c.message_id, MESSAGE_REACTION.c.id,
        MESSAGE_REACTION.c.sender_id, MESSAGE_REACTION.c.emoji,
        MESSAGE_REACTION.c.removed, MESSAGE_REACTION.c.ts,
        _MR_AT.label("at"),
    ).where(mr_where).cte("rx_l_scope")
    l_meta = sa.select(
        l_scope.c.message_id, sa.func.count().label("recorded"),
        sa.func.max(l_scope.c.ts).label("observed_at"),
    ).group_by(l_scope.c.message_id).cte("rx_l_meta")
    l_latest = sa.select(l_scope).distinct(
        l_scope.c.message_id, l_scope.c.sender_id, l_scope.c.emoji
    ).order_by(
        l_scope.c.message_id, l_scope.c.sender_id, l_scope.c.emoji,
        l_scope.c.id.desc()).cte("rx_l_latest")
    l_expected = sa.select(
        l_latest.c.message_id, sa.func.count().label("n")
    ).group_by(l_latest.c.message_id).cte("rx_l_expected")
    l_bounded = sa.select(l_latest).limit(
        REACTION_STATES_MAX).cte("rx_l_bounded")
    selects = [
        sa.select(
            sa.literal("t").label("tag"), t_bounded.c.account_key.label("k1"),
            t_bounded.c.thread.label("k2"), t_bounded.c.object_ref.label("k3"),
            null_int.label("mid"), t_bounded.c.id.label("a"),
            t_bounded.c.at.label("b"), null_int.label("c"), null_int.label("d"),
            null_text.label("e"), null_text.label("f"),
            sa.type_coerce(
                sa.func.to_jsonb(t_bounded.table_valued()), pg.JSONB).label("j")),
        sa.select(
            sa.literal("n"), t_meta.c.account_key, t_meta.c.thread,
            t_meta.c.object_ref, null_int, t_meta.c.total, t_meta.c.obs,
            null_int, null_int, null_text, null_text, null_json),
        sa.select(
            sa.literal("l"), null_text, null_text, null_text, l_bounded.c.message_id,
            l_bounded.c.id, l_bounded.c.ts, l_bounded.c.at,
            l_bounded.c.removed, l_bounded.c.sender_id,
            l_bounded.c.emoji, null_json),
        sa.select(
            sa.literal("m"), null_text, null_text, null_text, l_meta.c.message_id,
            l_meta.c.recorded, l_meta.c.observed_at,
            sa.func.coalesce(l_expected.c.n, 0), null_int, null_text, null_text, null_json,
        ).select_from(
            l_meta.outerjoin(
                l_expected, l_meta.c.message_id == l_expected.c.message_id)),
    ]
    if bound is not None:
        selects.append(sa.select(
            sa.literal("b"), null_text, null_text, null_text, null_int,
            sa.func.count(bound.c.recorded_at),
            null_int, null_int, null_int, null_text, null_text, null_json))
    return sa.union_all(*selects)


def _split_rx_rows(
    rows: Any,
) -> tuple[
    dict[tuple[str, str, str], list[StoredObservation]],
    dict[tuple[str, str, str], tuple[int, int]],
    dict[int, list[ReactionRow]],
    dict[int, tuple[int, int, int]],
    int,
]:
    """The tagged union row set back into per-object typed rows, per-object
    totals, per-target legacy tuples and per-target totals — and the as-of
    bound's existence (`b`, -1 when no bound was read). A typed row that
    fails to parse is counted by `tmeta`'s real total anyway, so the head's
    partial flag covers it rather than folding without it."""
    typed: dict[tuple[str, str, str], list[StoredObservation]] = {}
    tmeta: dict[tuple[str, str, str], tuple[int, int]] = {}
    legacy: dict[int, list[ReactionRow]] = {}
    lmeta: dict[int, tuple[int, int, int]] = {}
    bound = -1
    for r in rows:
        if r.tag == "t":
            try:
                j = r.j if isinstance(r.j, dict) else json.loads(r.j)
                stored = dataclasses.replace(
                    _row_to_stored(j), recorded_at=int(r.b))
            except (ValueError, TypeError, KeyError):
                continue
            typed.setdefault(
                (str(r.k1), str(r.k2), str(r.k3)), []).append(stored)
        elif r.tag == "n":
            tmeta[(str(r.k1), str(r.k2), str(r.k3))] = (
                int(r.a), int(r.b or 0))
        elif r.tag == "l":
            legacy.setdefault(int(r.mid), []).append(ReactionRow(
                id=int(r.a), message_id=int(r.mid), sender_id=str(r.e),
                emoji=str(r.f), removed=bool(r.d), ts=int(r.b),
                created_at=int(r.c)))
        elif r.tag == "m":
            lmeta[int(r.mid)] = (int(r.a), int(r.b or 0), int(r.c or 0))
        elif r.tag == "b":
            bound = int(r.a)
    return typed, tmeta, legacy, lmeta, bound


def _rx_fold(
    head: ObservationHead,
    typed: dict[tuple[str, str, str], list[StoredObservation]],
    tmeta: dict[tuple[str, str, str], tuple[int, int]],
    legacy: dict[int, list[ReactionRow]],
    lmeta: dict[int, tuple[int, int, int]],
    *, gap: bool = False, partial: bool = False,
) -> ObservationHead:
    """`head` re-folded across BOTH stores — the ST row's existence plus
    the union fold's state, so `current`/`heads`/`head_for_handle`/`as_of`
    for `kind="reaction"` read the same answer the target annotation
    carries. `partial`/`gap` are the caller's own bound flags; a typed
    bound cut or a legacy standing cut is detected here from the real
    totals. The ST row itself stays the typed-only incremental head — a
    UNION head is always rebuilt on read, never stored."""
    key = (head.account_key, head.thread, head.object_ref)
    rows = typed.get(key, [])
    ttotal, tobs = tmeta.get(key, (0, 0))
    mids = {r.target_message_id for r in rows if r.target_message_id}
    l_rows = [r for m in mids for r in legacy.get(m, ())]
    recorded = ttotal + sum(lmeta.get(m, (0, 0, 0))[0] for m in mids)
    observed = max([tobs] + [lmeta.get(m, (0, 0, 0))[1] for m in mids])
    partial = partial or len(rows) < ttotal or any(
        len(legacy.get(m, ())) < lmeta.get(m, (0, 0, 0))[2] for m in mids)
    tr = fold_target_reactions(
        l_rows, rows, recorded=recorded,
        observed_at=observed or None, partial=partial, gap=gap)
    if tr is None:
        return head
    return union_head(tr, object_ref=head.object_ref,
                      account_key=head.account_key, thread=head.thread)


#: `object_handle`'s digest in SQL: sha256 of
#: `account_key|thread|kind:object_ref`, first 12 hex chars —
#: PostgreSQL's builtin `sha256(bytea)` (v11+), the same bytes Python's
#: `hashlib.sha256` digests, so a handle resolves exactly, never by
#: scanning a bounded head page.
_HANDLE_DIGEST = sa.func.substr(
    sa.func.encode(
        sa.func.sha256(sa.func.convert_to(
            sa.func.concat(
                sa.func.coalesce(ST.c.account_key, ""), sa.literal("|"),
                sa.func.coalesce(ST.c.thread, ""), sa.literal("|"),
                ST.c.kind, sa.literal(":"), ST.c.object_ref),
            "UTF8")), "hex"), 1, 12)


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

    async def _union_reactions(
        self, scope: SourceScope, heads: list[ObservationHead],
    ) -> list[ObservationHead]:
        """The page's `reaction` heads re-folded across BOTH stores, one
        batched statement — the same union `reaction_states` serves the
        target annotations, so an object's current read and its message's
        reaction line cannot disagree. Unresolved-target rows fold into
        the object's head (they are its evidence) while the annotation
        folds only resolved ones — the two disagree only where the link
        itself is unproven, which `resolve_targets` closes later feeding
        the SAME projection."""
        refs = {h.object_ref for h in heads if h.kind == "reaction"}
        if not refs:
            return heads
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                _rx_union_stmt(scope, frozenset(refs)))).all()
        typed, tmeta, legacy, lmeta, _ = _split_rx_rows(rows)
        return [
            _rx_fold(h, typed, tmeta, legacy, lmeta)
            if h.kind == "reaction" else h for h in heads
        ]

    async def current(
        self, *, scope: SourceScope, kind: str, object_ref: str,
    ) -> ObservationHead | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                sa.select(ST).where(_st_scope_where(scope), ST.c.kind == kind,
                                    ST.c.object_ref == object_ref))
            ).mappings().one_or_none()
        head = None if row is None else _row_to_head(row)
        if head is not None and kind == "reaction":
            head = (await self._union_reactions(scope, [head]))[0]
        return head

    async def heads(
        self, *, scope: SourceScope, kind: str | None = None,
        limit: int = 100,
    ) -> list[ObservationHead]:
        where = _st_scope_where(scope)
        if kind is not None:
            where = sa.and_(where, ST.c.kind == kind)
        async with self._engine.connect() as conn:
            # MOST-RECENTLY-TOUCHED first (`updated_at` bumps on every
            # folded head write), so a bounded page is the scope's freshest
            # objects — relevance is recency, and a `limit + 1` caller can
            # honestly say how many older heads it did not read.
            rows = (await conn.execute(
                sa.select(ST).where(where)
                .order_by(ST.c.updated_at.desc(), ST.c.id)
                .limit(max(limit, 0)))).mappings().all()
        return await self._union_reactions(
            scope, [_row_to_head(r) for r in rows])

    async def head_for_handle(
        self, *, scope: SourceScope, handle: str,
    ) -> ObservationHead | None:
        kind, digest = parse_handle(handle)
        if not kind:
            return None
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                sa.select(ST).where(
                    _st_scope_where(scope), ST.c.kind == kind,
                    sa.literal(digest) == _HANDLE_DIGEST))
            ).mappings().one_or_none()
        head = None if row is None else _row_to_head(row)
        if head is not None and kind == "reaction":
            head = (await self._union_reactions(scope, [head]))[0]
        return head

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
        never as the requested state.

        For `kind="reaction"` the state is the same union `current` and the
        target annotation read — typed rows at or before `through_id` plus
        legacy deltas whose durable commit clock proves they preceded that
        row's own commit. A bound naming no row cannot order legacy events:
        they are excluded and the fold flags `unordered`, never guessed."""
        if kind == "reaction":
            async with self._engine.connect() as conn:
                rows = (await conn.execute(_rx_union_stmt(
                    scope, frozenset({object_ref}), through_id=through_id,
                    per_object=max(limit, 0) + 1))).all()
            typed, tmeta, legacy, lmeta, bound = _split_rx_rows(rows)
            all_rows = sorted(
                (r for v in typed.values() for r in v), key=lambda r: r.id)
            more = len(all_rows) > max(limit, 0)
            kept = all_rows[: max(limit, 0)]
            ttotal = sum(t[0] for t in tmeta.values())
            tobs = max([t[1] for t in tmeta.values()] or [0])
            mids = {r.target_message_id for r in kept
                    if r.target_message_id}
            l_rows = [r for m in mids for r in legacy.get(m, ())]
            recorded = ttotal + sum(
                lmeta.get(m, (0, 0, 0))[0] for m in mids)
            observed = max(
                [tobs] + [lmeta.get(m, (0, 0, 0))[1] for m in mids])
            partial = more or len(all_rows) < ttotal or any(
                len(legacy.get(m, ())) < lmeta.get(m, (0, 0, 0))[2]
                for m in mids)
            tr = fold_target_reactions(
                l_rows, kept, recorded=recorded,
                observed_at=observed or None, partial=partial,
                gap=bound == 0 and bool(mids))
            if tr is None:
                return None, more
            account_key = thread = ""
            if kept:
                last = kept[-1].observation.scope
                account_key, thread = last.account_key, last.thread
            return union_head(
                tr, object_ref=object_ref, account_key=account_key,
                thread=thread), more
        async with self._engine.connect() as conn:
            kept_rows = (await conn.execute(
                sa.select(OBS).where(
                    _scope_where(scope), OBS.c.kind == kind,
                    OBS.c.object_ref == object_ref, OBS.c.id <= through_id)
                .order_by(OBS.c.id).limit(
                    max(limit, 0) + 1))).mappings().all()
        more = len(kept_rows) > max(limit, 0)
        head = rebuild_head(
            [_row_to_stored(r) for r in kept_rows[: max(limit, 0)]],
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


async def _resolve_target_row(
    conn: AsyncConnection, *, platform: str, conversation_id: str,
    target: EditTarget, target_author_id: str = "",
) -> int | None:
    """The ONE target→row rule, callable inside an open transaction: the row
    the target PROVABLY names, or None. Zero matches is an unresolved target;
    several is an ambiguous one — an arbitrary same-timestamp pick would bind
    evidence to a row it may not name (design 02: ambiguity stays unresolved).
    The author's identity is preserved separately from the actor's (commit
    `38b4a157f`): an author-qualified target resolves only a row that author
    sent, and only when that author sent exactly one."""
    if not target.message_id and target.sent_ts is None:
        return None
    # `SqlRecordStore`'s edit-target rule: a second edit can name the first
    # edit's timestamp, so a timestamp target matches the message OR one of
    # its edits.
    edited_at = sa.exists().where(
        MESSAGE_EDIT.c.message_id == MESSAGES.c.id,
        MESSAGE_EDIT.c.ts == target.sent_ts)
    match = (MESSAGES.c.platform_message_id == target.message_id
             if target.message_id else
             sa.or_(MESSAGES.c.ts == target.sent_ts, edited_at))
    where = sa.and_(MESSAGES.c.platform == platform,
                    MESSAGES.c.conversation_id == conversation_id, match)
    if target_author_id:
        where = sa.and_(where, MESSAGES.c.sender_id == target_author_id)
    rows = (await conn.execute(
        sa.select(MESSAGES.c.id).where(where).limit(2))).all()
    if len(rows) != 1:
        return None
    return int(rows[0].id)


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
    a second edit can name the first edit's timestamp) and require a UNIQUE
    match under `target_author_id` when one is supplied — an ambiguous
    same-timestamp answer stays NULL rather than naming whichever row
    happened to sort first."""
    if target is None or (not target.message_id and target.sent_ts is None):
        return None
    async with engine.connect() as conn:
        return await _resolve_target_row(
            conn, platform=platform, conversation_id=conversation_id,
            target=target, target_author_id=target_author_id)


async def relink_arriving_targets(
    conn: AsyncConnection, *, scope: SourceScope,
    platform_message_id: str | None, sent_ts: int | None,
    sender_id: str | None,
) -> int:
    """Re-link retained observations whose target can name the message that
    just committed — called INSIDE the message row's own transaction so the
    link lands with the row, never after it (the port's "resolve_targets
    re-links the rest when the target arrives").

    The arriving message generates every target key it could answer — `id:`
    its platform handle, `ts:` its timestamp plain and author-qualified —
    and each unresolved row carrying one is re-resolved through the SAME
    fail-closed rule `_resolve_target_row` uses: an ambiguous same-timestamp
    target stays unresolved rather than binding to whichever row happened to
    arrive. Returns how many rows gained a link this call."""
    keys: list[str] = []
    if platform_message_id:
        keys.append(f"id:{platform_message_id}")
    if sent_ts is not None:
        keys.append(f"ts:{sent_ts}")
        if sender_id:
            keys.append(f"ts:{sent_ts}:author:{sender_id}")
    if not keys:
        return 0
    pending = sa.and_(_scope_where(scope), OBS.c.target_key.in_(keys),
                      OBS.c.target_message_id.is_(None))
    if not (await conn.execute(sa.select(sa.exists().where(pending)))).scalar_one():
        return 0
    linked = 0
    # Resolve each address once, not once per retained vote/reaction row.
    # There are at most three addresses; author-qualified native ids share
    # an address with bare ids, so those need their own unique-match read.
    for key in keys:
        authors = ("", sender_id) if key.startswith("id:") and sender_id else ("",)
        for author in authors:
            qualified_author = (
                sender_id or "" if ":author:" in key else author)
            target = target_from_key(key)
            if target is None:
                continue
            resolved = await _resolve_target_row(
                conn, platform=scope.platform,
                conversation_id=scope.conversation_id,
                target=target, target_author_id=qualified_author)
            if resolved is None:
                continue
            author_match = (OBS.c.target_author_id == qualified_author
                            if qualified_author else
                            sa.or_(OBS.c.target_author_id.is_(None),
                                   OBS.c.target_author_id == ""))
            result = await conn.execute(
                sa.update(OBS).where(pending, OBS.c.target_key == key, author_match)
                .values(target_message_id=resolved))
            linked += result.rowcount
    return linked
