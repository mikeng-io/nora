"""Profile's owning-store revision reads; independent of the optional index.

Progress is per account/domain/source at consolidation_watermark. A content
revision changes when the computable input changes, not when a Rollup is rebuilt.
Failed reads raise and leave that domain pending. Presence uses the existing
coordinate-free Rollup read shape and named routine metadata.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by, insert
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.culture import Episode, RoomMessage
from nora.domain.notes import anchor_bounds_ms
from nora.domain.profile import Said
from nora.domain.profile_inputs import PersonSourceRevision
from nora.domain.rollup import Citation
from nora.domain.rollup_read import RollupReadQuery
from nora.domain.social_stream import PersonSocialItem, Reading, RecordedCounts, to_us
from nora.infra.sql.audit import (
    _latest_edit,
    transcript_row,
    transcript_select,
    turn_id_for_message,
)
from nora.infra.sql.character import _in_rooms
from nora.infra.sql.mobility import SqlMobilityStore, current_build_id
from nora.infra.sql.rollup_read import SqlRollupRead, _row
from nora.infra.sql.rollups import SqlRollupStore
from nora.infra.sql.schema import (
    CONSOLIDATION_WATERMARK as W,
)
from nora.infra.sql.schema import (
    EVENT_REVISION,
    EVENTS,
    MEDIA_OBJECTS,
    MEMO,
    MEMO_REVISION,
    MESSAGE_DELETE,
    MESSAGE_EDIT,
    MESSAGES,
    NOTE,
    NOTE_REVISION,
    ROLLUP_DAYBOOK,
    ROLLUP_RESPONSE,
    ROLLUP_TIE,
    ROW_HISTORY,
    SOCIAL_ITEM,
    SOCIAL_ITEM_COUNT,
    SOCIAL_PERCEPTION,
)
from nora.infra.sql.social_stream import SqlSocialStreamStore

MEDIA_RETRY = 'profile-person:media-retry'


def _revision(value: Any) -> Any:
    return sa.func.encode(sa.func.sha256(sa.func.convert_to(
        sa.cast(value, sa.Text), 'UTF8')), 'hex')


def _scope(key: str, source: str) -> str:
    return key + '\x1f' + source


def _media_revision(ref: Any) -> Any:
    m = MEDIA_OBJECTS.c
    return sa.select(sa.func.jsonb_agg(aggregate_order_by(sa.func.jsonb_build_array(
        m.sha256, m.title, m.description, m.content, m.describe_model, m.provenance,
    ), m.sha256))).where(sa.func.strpos(sa.func.coalesce(ref, ''), m.sha256) > 0).scalar_subquery()


def _stamp(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else (
        "unknown" if value is None else str(value))


class SqlProfileInputs:
    """Four bounded source reads and their existing consolidation settlement."""

    def __init__(self, engine: AsyncEngine, *, social: SqlSocialStreamStore | None = None) -> None:
        self._engine = engine
        # Composition supplies readable_social_store, carrying current and
        # former opted-out ids. A missing reader is unavailable, never open.
        self._social = social

    async def recovery_requested(self, *, key: str) -> bool:
        """An explicit operator request, separate from evidence/cursor progress."""
        async with self._engine.connect() as conn:
            return bool((await conn.execute(sa.select(sa.exists().where(
                W.c.consolidator_name == MEDIA_RETRY, W.c.scope_id == key,
                W.c.source_revision.is_distinct_from(W.c.source_read_revision),
            )))).scalar_one())

    async def request_recovery(self, *, key: str) -> bool:
        """Queue at most one attempt. Duplicate pending requests are idempotent."""
        stmt = insert(W).values(consolidator_name=MEDIA_RETRY, scope_id=key,
            last_message_id=0, source_revision=uuid.uuid4().hex)
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt.on_conflict_do_update(
                index_elements=[W.c.consolidator_name, W.c.scope_id],
                set_={'source_revision': stmt.excluded.source_revision,
                      'updated_at': sa.func.now()},
                where=W.c.source_revision.is_not_distinct_from(W.c.source_read_revision),
            ).returning(W.c.scope_id))).scalar_one_or_none() is not None

    async def claim_recovery(self, *, key: str) -> None:
        """Consume before reading/model work, including a failed or interrupted attempt.

        This is an operator's one-shot wake, not proof of settlement. Keep the
        token/claim in the existing watermark history; normal source receipts
        remain pending until the normal successful settlement acknowledges them.
        """
        async with self._engine.begin() as conn:
            await conn.execute(sa.update(W).where(
                W.c.consolidator_name == MEDIA_RETRY, W.c.scope_id == key,
                W.c.source_revision.is_distinct_from(W.c.source_read_revision),
            ).values(source_read_revision=W.c.source_revision, updated_at=sa.func.now()))

    async def _pending(
        self, *, key: str, domain: str, stmt: Any, limit: int, citations: Sequence[str] = (),
        include_settled: bool = False,
    ) -> list[Any]:
        q = stmt.subquery()
        scope = sa.literal(key + '\x1f') + q.c.source
        query = sa.select(q).outerjoin(W, sa.and_(
            W.c.consolidator_name == f'profile-person:source:{domain}',
            W.c.scope_id == scope,
        )).where(sa.or_(W.c.source_revision.is_(None),
                       W.c.source_revision != q.c.revision)).order_by(
            sa.case((W.c.source_read_revision == q.c.revision, 1), else_=0),
            W.c.updated_at.asc().nulls_first(), q.c.source).limit(limit)
        if include_settled:
            query = sa.select(q).order_by(q.c.source).limit(limit)
        if citations:
            query = (sa.select(q).where(q.c.citation.in_(citations))
                     .order_by(q.c.source).limit(limit))
        async with self._engine.connect() as conn:
            return list((await conn.execute(query)).all())

    async def settle(self, *, key: str, revisions: Sequence[PersonSourceRevision],
                     attempted: Sequence[PersonSourceRevision] = ()) -> None:
        """Only the exact snapshots handed to a successful account commit.

        A crash before this transaction repeats them. A newer source revision
        is never acknowledged by this older session's snapshot.
        """
        acknowledged = {(r.domain, r.source): r for r in revisions}
        read = {(r.domain, r.source): r for r in (*attempted, *revisions)
                if r.revision and r.state != 'unavailable'}
        async with self._engine.begin() as conn:
            for identity, r in read.items():
                ack = identity in acknowledged
                stmt = insert(W).values(
                    consolidator_name=f'profile-person:source:{r.domain}',
                    scope_id=_scope(key, r.source), last_message_id=0,
                    source_revision=r.revision if ack else None, source_read_revision=r.revision,
                )
                await conn.execute(stmt.on_conflict_do_update(
                    index_elements=[W.c.consolidator_name, W.c.scope_id],
                    set_={'source_revision': r.revision if ack else W.c.source_revision,
                          'source_read_revision': r.revision, 'updated_at': sa.func.now()},
                ))

    def _messages(self, *, rooms: Sequence[tuple[str, str]], person: str,
                  message_ids: Sequence[int] | None = None) -> Any:
        m = MESSAGES.c
        scope = [_in_rooms(rooms), m.is_nora == 0]
        if message_ids is not None:
            scope.append(m.id.in_(message_ids))
        refs = sa.select(sa.func.coalesce(m.media_json, '').label('ref')).where(
            *scope).distinct().subquery()
        ready = sa.exists().where(
            sa.func.strpos(refs.c.ref, MEDIA_OBJECTS.c.sha256) > 0,
            sa.or_(*(sa.func.coalesce(MEDIA_OBJECTS.c[f], '') != ''
                     for f in ('title', 'description', 'content'))),
        )
        # ponytail: two media scans per distinct attachment ref; use a relational
        # message-to-media index if distinct refs make this read exceed its budget.
        # Reuse each attachment lookup across messages, revision comparisons,
        # ordering and recovery counts instead of flattening correlated scans.
        cache = sa.select(refs.c.ref, _media_revision(refs.c.ref).label('media'),
            ready.label('ready')).cte('profile_message_media').prefix_with('MATERIALIZED')
        media = cache.c.media
        edits = sa.select(sa.func.max(MESSAGE_EDIT.c.id)).where(
            MESSAGE_EDIT.c.message_id == m.id).scalar_subquery()
        deleted = sa.exists().where(MESSAGE_DELETE.c.message_id == m.id)
        edited_mentions = sa.select(MESSAGE_EDIT.c.mention_persons_json).where(
            MESSAGE_EDIT.c.message_id == m.id).order_by(MESSAGE_EDIT.c.id.desc()
            ).limit(1).scalar_subquery()
        eligible = sa.or_(m.person_id == person, sa.cast(sa.func.coalesce(
            edited_mentions, m.mention_persons_json), JSONB).contains([person]))
        snapshot = sa.func.jsonb_build_array(
            edits, deleted, m.body, m.media_json, m.hidden_at, m.person_id,
            m.mention_persons_json, media,
        )
        # transcript_select excludes hidden rows. Select its named columns
        # explicitly here so a withdrawal yields metadata without content.
        latest = _latest_edit()
        stmt = sa.select(
            m.id, m.sender_id, m.sender_name, m.ts, m.body, m.is_nora,
            m.delivery_status, m.raw_json, m.quoted_ts, m.media_json, m.person_id,
            turn_id_for_message().label("turn_id"),
            m.external_files_json, latest.c.ts.label('edit_ts'),
            latest.c.body.label('edit_body'), latest.c.raw_json.label('edit_raw_json'),

            m.conversation_id, (sa.literal('message:') + sa.cast(m.id, sa.Text)).label('source'),
            _revision(snapshot).label('revision'), m.hidden_at, deleted.label('deleted'),
            cache.c.ready.label('cache_ready'), media.label('cached_media'),
            sa.or_(edits.is_not(None), deleted, m.hidden_at.is_not(None)).label('changed'),
            sa.func.coalesce(eligible, False).label('eligible'),
            sa.or_(m.person_id == person, sa.cast(m.mention_persons_json, JSONB)
                   .contains([person])).label('originally_eligible'),
        ).select_from(MESSAGES.join(cache, cache.c.ref == sa.func.coalesce(m.media_json, ''))
                      .outerjoin(latest, sa.true())).where(*scope)
        # Pending predicates and ordering must reuse the revision projection.
        # Keep provenance outside this fence: it is needed only for delivered rows.
        q = stmt.cte('profile_messages').prefix_with('MATERIALIZED')
        message_at = sa.select(sa.func.max(ROW_HISTORY.c.recorded_at)).where(
            ROW_HISTORY.c.table_name == 'messages',
            ROW_HISTORY.c.row_key == sa.func.jsonb_build_object('id', q.c.id),
        ).scalar_subquery()
        reading_at = sa.select(sa.func.max(ROW_HISTORY.c.recorded_at)).where(
            ROW_HISTORY.c.table_name == 'media_objects',
            sa.func.strpos(sa.func.coalesce(q.c.media_json, ''),
                           ROW_HISTORY.c.row_key['sha256'].astext) > 0,
        ).scalar_subquery()
        edit_at = sa.select(sa.func.max(MESSAGE_EDIT.c.recorded_at)).where(
            MESSAGE_EDIT.c.message_id == q.c.id).scalar_subquery()
        return sa.select(q, sa.func.greatest(message_at, reading_at, edit_at)
                         .label('recorded_at'))

    async def messages(
        self, *, key: str, rooms: Sequence[tuple[str, str]], person: str, limit: int,
        after: int = 0,
    ) -> list[PersonSourceRevision]:
        q = self._messages(rooms=rooms, person=person).subquery()
        seen = sa.exists().where(
            W.c.consolidator_name == 'profile-person:source:message',
            W.c.scope_id == sa.literal(key + '\x1f') + q.c.source,
        )
        # Initial recovery rehands eligible EXISTING cache, without replaying
        # every consumed plain message or resetting either history cursor. Edits
        # and withdrawals are independent candidates, including pre-upgrade rows;
        # their source revision, never their edit id vs Record id, deduplicates them.
        stmt = sa.select(q).where(sa.or_(q.c.eligible, seen,
            sa.and_(q.c.changed, q.c.originally_eligible)),
            sa.or_(q.c.id > after, q.c.cache_ready, q.c.changed, seen))
        rows = await self._pending(key=key, domain='message', stmt=stmt, limit=limit)
        out = [PersonSourceRevision(
            domain='message', source=r.source, revision=r.revision,
            state='ineligible' if not r.eligible else 'hidden'
                  if r.hidden_at is not None or r.deleted else 'readable',
            recorded_at=_stamp(r.recorded_at),
            as_of=r.edit_ts if r.edit_ts is not None else r.ts,
            cache_ready=bool(r.cache_ready),
            media_state='cached' if r.cache_ready else 'not_yet_read',
            ready_handles=tuple(x[0][:6] for x in (r.cached_media or ()) if any(x[1:4])),

            roots=() if not r.eligible or r.hidden_at is not None or r.deleted else (
                RoomMessage(room=r.conversation_id, row=transcript_row(r)),),
        ) for r in rows]
        if len(out) < limit:
            h = ROW_HISTORY.c
            source = sa.literal('message:') + h.image['id'].astext
            prior = W.alias('prior')
            tombstones = sa.select(source.label('source'),
                _revision(sa.func.jsonb_build_array('absent', h.id)).label('revision'),
                h.recorded_at,
            ).where(h.table_name == 'messages', h.op == 'delete', h.superseded_at.is_(None),
                sa.or_(sa.false(), *(sa.and_(h.image['platform'].astext == p,
                    h.image['conversation_id'].astext == room) for p, room in rooms)),
                sa.or_(sa.exists().where(
                    prior.c.consolidator_name == 'profile-person:source:message',
                    prior.c.scope_id == sa.literal(key + '\x1f') + source),
                    sa.and_(h.image['is_nora'].astext == '0', sa.or_(
                        h.image['person_id'].astext == person,
                        sa.cast(h.image['mention_persons_json'].astext, JSONB)
                        .contains([person])))))
            gone = await self._pending(key=key, domain='message', stmt=tombstones,
                                       limit=limit - len(out))
            out.extend(PersonSourceRevision(domain='message', source=r.source,
                revision=r.revision, state='absent', recorded_at=_stamp(r.recorded_at))
                for r in gone)
        return out

    async def media_recovery(
        self, *, key: str, rooms: Sequence[tuple[str, str]], person: str,
        initialize: bool = False,
    ) -> dict[str, int | bool]:
        """Counts only, read-only unless the ordinary loop freezes its boundary.

        Completion means every eligible cached candidate at that frozen bound
        was rendered and settled. It says nothing about absent/unreadable media,
        extraction accuracy or historical message recovery's separate cursor.
        """
        name = 'profile-person:media-boundary'
        async with self._engine.begin() as conn:
            head = int((await conn.execute(sa.select(sa.func.max(MESSAGES.c.id))))
                       .scalar_one_or_none() or 0)
            if initialize:
                await conn.execute(insert(W).values(consolidator_name=name, scope_id=key,
                    last_message_id=head).on_conflict_do_nothing())
            boundary_row = (await conn.execute(sa.select(
                W.c.last_message_id, W.c.source_read_revision).where(
                W.c.consolidator_name == name, W.c.scope_id == key))).one_or_none()
            boundary = None if boundary_row is None else boundary_row.last_message_id
            swept = boundary_row is not None and boundary_row.source_read_revision == 'swept'
            q = self._messages(rooms=rooms, person=person).subquery()
            visible = sa.and_(q.c.eligible, q.c.hidden_at.is_(None), ~q.c.deleted)
            bounded = q.c.id <= (head if boundary is None else boundary)
            scope = sa.literal(key + '\x1f') + q.c.source
            joined = q.outerjoin(W, sa.and_(
                W.c.consolidator_name == 'profile-person:source:message', W.c.scope_id == scope))
            pending = sa.or_(W.c.source_revision.is_(None), W.c.source_revision != q.c.revision)
            row = (await conn.execute(sa.select(
                sa.func.count().filter(sa.and_(visible, q.c.cache_ready)),
                sa.func.count().filter(sa.and_(visible, q.c.cache_ready, pending)),
                sa.func.count().filter(sa.and_(visible, q.c.cache_ready, pending,
                    W.c.source_read_revision.is_distinct_from(q.c.revision))),
                sa.func.count().filter(sa.and_(visible, ~q.c.cache_ready,
                                               q.c.media_json.is_not(None))),
                sa.func.count().filter(sa.and_(q.c.eligible, ~visible)),
            ).select_from(joined).where(bounded))).one()
            if initialize and not swept and row[2] == 0:
                # Initial bounded recovery is a finite sweep, not a permanent
                # completion-triggered model-call path. Pending unreadable work
                # and future revisions keep ordinary per-account wake timing.
                await conn.execute(sa.update(W).where(
                    W.c.consolidator_name == name, W.c.scope_id == key,
                ).values(source_read_revision='swept'))
                swept = True
        return {"boundary": int(boundary if boundary is not None else head),
                "frozen": boundary is not None, "initial_sweep_complete": swept,
                "eligible_cached": int(row[0]),
                "pending_cached": int(row[1]), "unattempted_cached": int(row[2]),
                "missing_cache": int(row[3]), "hidden_or_deleted": int(row[4]),
                "complete": boundary is not None and row[1] == 0}

    async def social(
        self, *, key: str, persons: Sequence[str], limit: int, citations: Sequence[str] = (),
        since_ms: int | None = None, until_ms: int | None = None,
        include_settled: bool = False,
    ) -> list[PersonSourceRevision]:
        if self._social is None:
            raise RuntimeError("readable social store is unavailable")
        i, c = SOCIAL_ITEM.c, SOCIAL_ITEM_COUNT.c
        latest = sa.select(SOCIAL_ITEM_COUNT).where(
            c.network == i.network, c.item_id == i.item_id,
        ).order_by(c.recorded_at.desc(), c.id.desc()).limit(1).lateral('counts')
        p = SOCIAL_PERCEPTION.c
        perception = sa.select(p.outcome, p.recorded_at).where(
            p.network == i.network, p.item_id == i.item_id,
        ).order_by(p.recorded_at.desc(), p.id.desc()).limit(1).lateral('perception')
        # One owner for gone/hidden/opt-out eligibility, shared with every
        # social reader. This is its Record projection, never a network call.
        denied = ~sa.and_(*self._social._readable())
        media = _media_revision(i.media_ref)
        snapshot = sa.func.jsonb_build_array(
            denied, i.caption, media, perception.c.outcome, perception.c.recorded_at,
            latest.c.likes, latest.c.comments,
            latest.c.reposts, latest.c.likes_hidden, latest.c.recorded_at,
        )
        stmt = sa.select(
            i.network, i.item_id, i.kind, i.collab, i.taken_at, i.origin, i.recorded_at, i.person,
            sa.case((denied, None), else_=i.caption).label('caption'),
            sa.case((denied, None), else_=i.media_ref).label('media_ref'),
            denied.label('denied'), perception.c.outcome.label('perception_outcome'),
            latest.c.likes, latest.c.comments, latest.c.reposts,
            latest.c.likes_hidden, latest.c.recorded_at.label('count_at'),
            (i.network + ':' + i.item_id).label('source'),
            (sa.literal('social:') + i.network + ':' + i.item_id).label('citation'),
            _revision(snapshot).label('revision'),
        ).select_from(SOCIAL_ITEM.outerjoin(latest, sa.true()).outerjoin(
            perception, sa.true())).where(i.person.in_(persons))
        if since_ms is not None:
            stmt = stmt.where(i.taken_at >= datetime.fromtimestamp(since_ms / 1000).astimezone())
        if until_ms is not None:
            stmt = stmt.where(i.taken_at < datetime.fromtimestamp(until_ms / 1000).astimezone())
        rows = await self._pending(key=key, domain='social', stmt=stmt, limit=limit,
                                   citations=citations, include_settled=include_settled)
        # All these are permitted item attachments, never arbitrary body handles.
        shas = sorted({sha for r in rows if not r.denied for sha in (r.media_ref or '').split(',')
                       if sha})
        async with self._engine.connect() as conn:
            found = (await conn.execute(sa.select(MEDIA_OBJECTS).where(
                MEDIA_OBJECTS.c.sha256.in_(shas)))).all() if shas else []
        readings = {r.sha256: Reading(r.title, r.description, r.content) for r in found}
        return [PersonSourceRevision(
            domain='social', source=r.source, revision=r.revision, subject=r.person,
            state='withheld' if r.denied else 'readable', recorded_at=_stamp(r.recorded_at),
            social=None if r.denied else PersonSocialItem(
                network=r.network, item_id=r.item_id, kind=r.kind, collab=r.collab,
                taken_at=r.taken_at, origin=r.origin, caption=r.caption,
                readings=tuple(readings.get(sha, Reading()) for sha in
                               (r.media_ref or '').split(',') if sha),
                recorded_us=to_us(r.recorded_at),
            ),
            media_state=('withheld' if r.denied else 'cached'
                if any(readings.get(sha, Reading()).description
                       or readings.get(sha, Reading()).content
                       or readings.get(sha, Reading()).title
                       for sha in (r.media_ref or '').split(',') if sha) else 'explicit_empty'
                if r.perception_outcome == 'nothing_found' else 'unavailable'
                if r.perception_outcome in ('failed', 'unavailable', 'capped') else 'not_yet_read'),
            properties=(("perception_outcome", r.perception_outcome or "not_yet_read"),),
            counts=None if r.denied or r.count_at is None else RecordedCounts(
                likes=r.likes, comments=r.comments, reposts=r.reposts,
                likes_hidden=r.likes_hidden, read_at=r.count_at,
            ),
        ) for r in rows]

    async def _roots(
        self, *, refs: Sequence[str], rooms: Sequence[tuple[str, str]],
    ) -> tuple[RoomMessage, ...]:
        ids = [int(ref) for ref in refs if ref.isdigit()]
        if not ids:
            return ()
        async with self._engine.connect() as conn:
            rows = (await conn.execute(transcript_select().add_columns(MESSAGES.c.conversation_id)
                .where(_in_rooms(rooms), MESSAGES.c.id.in_(ids), MESSAGES.c.is_nora == 0,
                       MESSAGES.c.hidden_at.is_(None), ~sa.exists().where(
                           MESSAGE_DELETE.c.message_id == MESSAGES.c.id),
                       ).order_by(MESSAGES.c.id))).all()
        return tuple(RoomMessage(room=r.conversation_id, row=transcript_row(r)) for r in rows)

    async def register(
        self, *, key: str, unit: str, rooms: Sequence[tuple[str, str]], person: str, limit: int,
    ) -> list[PersonSourceRevision]:
        """Current Register state, with no untyped Notebook prose/location fields.

        Roots are resolved from owning-store citations, never graph neighbours
        or held Profile. Note anchors and event participants establish relevance;
        a memo must resolve to a message by/about this person. Cancelled/retired
        entries still explain their current state but do not imply occurrence.
        """
        n, nr, e, m, mr = NOTE.c, NOTE_REVISION.c, EVENTS.c, MEMO.c, MEMO_REVISION.c
        note = sa.select(
            (sa.literal('note:') + sa.cast(n.id, sa.Text)).label('source'),
            sa.cast(n.head_revision_id, sa.Text).label('head'),
            sa.case((n.retired_at.is_not(None), 'retired'), else_='open').label('state'),
            nr.written_at.label('written'), nr.citations.label('refs'),
            sa.cast(n.anchor_date, sa.BigInteger).label('start'),
            sa.cast(sa.null(), sa.BigInteger).label('end'),
        ).join(NOTE_REVISION, nr.id == n.head_revision_id).where(
            n.unit == unit, n.anchor_entity_ids.contains([person]))
        newer = MEMO_REVISION.alias('newer')
        memo = sa.select(
            (sa.literal('memo:') + sa.cast(m.id, sa.Text)).label('source'),
            sa.cast(mr.id, sa.Text).label('head'), mr.state, mr.written_at.label('written'),
            mr.citations.label('refs'), sa.cast(sa.null(), sa.BigInteger).label('start'),
            sa.cast(sa.null(), sa.BigInteger).label('end'),
        ).join(MEMO_REVISION, mr.memo_id == m.id).where(m.unit == unit, ~sa.exists().where(
            newer.c.memo_id == mr.memo_id, newer.c.seq > mr.seq))
        # Each event field's actual Record citations; prose is never selected.
        event = sa.select(
            (sa.literal('event:') + sa.cast(e.id, sa.Text)).label('source'),
            sa.cast(e.head_revision_id, sa.Text).label('head'), e.status.label('state'),
            sa.cast(sa.extract('epoch', EVENT_REVISION.c.recorded_at) * 1000,
                    sa.BigInteger).label('written'),
            sa.cast(sa.literal([]), sa.ARRAY(sa.Text)).label('refs'),
            sa.cast(sa.extract('epoch', e.when) * 1000, sa.BigInteger).label('start'),
            sa.cast(sa.extract('epoch', e.end) * 1000, sa.BigInteger).label('end'),
        ).select_from(EVENTS.outerjoin(EVENT_REVISION,
            EVENT_REVISION.c.id == e.head_revision_id)).where(
                e.unit_id == unit, e.people.contains([person]),
                e.conversation_id.in_([r for _, r in rooms]))
        out = []
        # Per-domain bounded heads. Exact root revisions participate in the token
        # below, so an edit/withdrawal of a linked root reconsiders this context.
        async with self._engine.connect() as conn:
            held = {str(r.scope_id): r.source_revision for r in (await conn.execute(
                sa.select(W.c.scope_id, W.c.source_revision).where(
                W.c.consolidator_name == 'profile-person:source:register',
                W.c.scope_id.startswith(key + '\x1f'),
            ))).all()}
            # ponytail: Register is small and its state is context, not a catch-up
            # log. Keyset filtering below keeps changed heads bounded per wake.
            heads = [r for stmt in (note, memo, event) for r in (await conn.execute(stmt)).all()]
        for r in sorted(heads, key=lambda r: r.source):
            refs = r.refs or ()
            if isinstance(refs, dict):
                refs = [str(c) for meta in refs.values() if isinstance(meta, dict)
                        for c in meta.get('citations', ())]
            roots = await self._roots(refs=refs, rooms=rooms)
            linked = []
            social_refs = [ref for ref in refs if ref.startswith('social:')]
            rollup_refs = [ref for ref in refs if ref.startswith(('rollup:daybook:',
                                                               'rollup:tie:', 'response:'))]
            if social_refs:
                linked.extend(await self.social(key=key, persons=[person], limit=6,
                                                citations=social_refs))
            if rollup_refs:
                linked.extend(await self.rollup(key=key, unit=unit, rooms=rooms,
                    person=person, limit=6, citations=rollup_refs))
            for ref in refs:
                # Register's real stored Rollup citation grammar. Resolve only
                # non-location kinds against their owning current-build store.
                if not ref.startswith('rollup(') or not ref.endswith(')'):
                    continue
                kind, _, rest = ref[7:-1].partition('|')
                subject, _, span = rest.rpartition('|')
                if kind not in ('daybook', 'tie'):
                    continue
                start, _, end = span.partition('/')
                try:
                    first, last = datetime.fromisoformat(start), datetime.fromisoformat(end)
                    if first.tzinfo is None or last.tzinfo is None:
                        continue
                except ValueError:
                    continue
                if not subject.startswith(unit + '|'):
                    continue
                resolved = await SqlRollupStore(self._engine).resolve(Citation(
                    kind=kind, subject=subject, start_ms=int(first.timestamp() * 1000),
                    end_ms=int(last.timestamp() * 1000)))
                if resolved.row_ids:
                    linked.extend(await self.rollup(key=key, unit=unit, rooms=rooms,
                        person=person, limit=6, row_ids={kind: resolved.row_ids}))
            if r.source.startswith('memo:') and not linked:
                q = self._messages(rooms=rooms, person=person,
                    message_ids=[x.row.id for x in roots]).subquery()
                async with self._engine.connect() as conn:
                    relevant = (await conn.execute(sa.select(q.c.id).where(q.c.eligible,
                        q.c.id.in_([x.row.id for x in roots])).limit(1))).first()
                if relevant is None:
                    continue
            # No Notebook prose, presence root, held Profile or own send can
            # become a citation or smuggle location into this input.
            token = hashlib.sha256(json.dumps([
                r.head, r.state, r.start, r.end, [(x.source, x.revision) for x in linked],
                [(x.row.id, x.row.body, x.row.media_json,
                  x.row.edit.ts if x.row.edit else None) for x in roots],
            ], sort_keys=True, default=str).encode()).hexdigest()
            if held.get(_scope(key, r.source)) == token:
                continue
            bounds = (anchor_bounds_ms(int(r.start)) if r.source.startswith('note:')
                      and r.start is not None else None)
            out.append(PersonSourceRevision(
                domain='register', source=r.source, revision=token, state=r.state,
                recorded_at=str(r.written), roots=roots,
                effective_from=bounds[0] if bounds is not None else r.start,
                effective_until=bounds[1] if bounds is not None else r.end, links=tuple(refs),
            ))
            out.extend(linked)
            if sum(x.domain == 'register' for x in out) >= limit:
                break
        return out

    async def rollup(
        self, *, key: str, unit: str, rooms: Sequence[tuple[str, str]], person: str, limit: int,
        citations: Sequence[str] = (), row_ids: dict[str, Sequence[int]] | None = None,
    ) -> list[PersonSourceRevision]:
        """Named presence occasions and message aggregates with their actual roots.

        Project message counts, reply/mention ties and quoted responses beside
        coordinate-free presence revisions. Reaction counts and Notebook prose
        are omitted. Validate message counts against readable roots before
        making an aggregate citable.
        """
        presence = (await self.presence_revisions(key=key, unit=unit, person=person, limit=limit)
                    if not citations and row_ids is None else [])
        out: list[PersonSourceRevision] = []
        m = MESSAGES.c
        parent = MESSAGES.alias('parent')
        edited_mentions = sa.select(MESSAGE_EDIT.c.mention_persons_json).where(
            MESSAGE_EDIT.c.message_id == m.id).order_by(MESSAGE_EDIT.c.id.desc()
            ).limit(1).scalar_subquery()
        mentions = sa.cast(sa.func.coalesce(edited_mentions, m.mention_persons_json), JSONB)
        for table, kind, subject, fields, predicate in (
            (ROLLUP_DAYBOOK, 'daybook', ROLLUP_DAYBOOK.c.local_date,
             ('messages',), ROLLUP_DAYBOOK.c.person_id == person),
            (ROLLUP_TIE, 'tie', ROLLUP_TIE.c.iso_week + ':' + ROLLUP_TIE.c.person_a
             + ',' + ROLLUP_TIE.c.person_b,
             ('replies_ab', 'replies_ba', 'mentions_ab', 'mentions_ba'),
             sa.or_(ROLLUP_TIE.c.person_a == person, ROLLUP_TIE.c.person_b == person)),
            (ROLLUP_RESPONSE, 'response', sa.cast(ROLLUP_RESPONSE.c.message_id, sa.Text),
             ('replies',), sa.and_(ROLLUP_RESPONSE.c.responders.contains([person]),
                                  ROLLUP_RESPONSE.c.outcome.not_in(('open', 'unknown')))),
        ):
            if row_ids is not None and kind not in row_ids:
                continue
            t = table.c
            if row_ids is not None:
                predicate = sa.and_(predicate, t.id.in_(row_ids[kind]))
            source = sa.literal(kind + ':') + subject
            citation = (sa.literal('response:') + sa.cast(t.message_id, sa.Text)
                if kind == 'response' else sa.literal(f'rollup:{kind}:{unit}|')
                + (t.person_a + ',' + t.person_b if kind == 'tie' else t.person_id))
            scope = [_in_rooms(rooms), m.is_nora == 0, m.hidden_at.is_(None),
                     m.ts >= t.valid_from_ms,
                     m.ts <= t.valid_to_ms if kind == 'response' else m.ts < t.valid_to_ms,
                     ~sa.exists().where(MESSAGE_DELETE.c.message_id == m.id)]
            flags: list[Any] = []
            if kind == 'daybook':
                scope.append(m.person_id == person)
                flags = [sa.true()]
            elif kind == 'tie':
                def quotes(who: Any) -> Any:
                    return sa.exists().where(
                        parent.c.platform == m.platform,
                        parent.c.conversation_id == m.conversation_id,
                        parent.c.ts == m.quoted_ts, parent.c.person_id == who,
                        parent.c.is_nora == 0, parent.c.hidden_at.is_(None),
                    )
                flags = [
                    sa.and_(m.person_id == t.person_a, quotes(t.person_b)),
                    sa.and_(m.person_id == t.person_b, quotes(t.person_a)),
                    sa.and_(m.person_id == t.person_a,
                            mentions.contains(sa.func.jsonb_build_array(t.person_b))),
                    sa.and_(m.person_id == t.person_b,
                            mentions.contains(sa.func.jsonb_build_array(t.person_a))),
                ]
                scope.append(sa.or_(*flags))
            else:
                scope.extend((m.platform == t.platform, m.conversation_id == t.conversation_id,
                              m.quoted_ts == t.sent_ts))
                flags = [sa.true()]
            root_ids = sa.select(sa.func.array_agg(m.id)).where(*scope).scalar_subquery()
            root_edits = sa.select(sa.func.max(MESSAGE_EDIT.c.id)).where(
                MESSAGE_EDIT.c.message_id == m.id).scalar_subquery()
            roots_revision = sa.select(sa.func.jsonb_agg(aggregate_order_by(
                sa.func.jsonb_build_array(m.id, m.body, root_edits, m.media_json), m.id)
            )).where(*scope).scalar_subquery()
            counts = [sa.select(sa.func.count()).select_from(MESSAGES).where(
                *scope, f).scalar_subquery().label('root_count_' + str(i))
                for i, f in enumerate(flags)]
            context_revision: Any = sa.null()
            if kind == 'response':
                send = MESSAGES.alias('send_context')
                send_edit = sa.select(sa.func.max(MESSAGE_EDIT.c.id)).where(
                    MESSAGE_EDIT.c.message_id == send.c.id).scalar_subquery()
                context_revision = sa.select(sa.func.jsonb_build_array(
                    send.c.body, send.c.hidden_at, send_edit,
                )).where(send.c.id == t.message_id).scalar_subquery()
            values = sa.func.jsonb_build_array(t.valid_from_ms, t.valid_to_ms,
                *(t[f] for f in fields), roots_revision, context_revision)
            stmt = sa.select(
                source.label('source'), citation.label('citation'),
                _revision(values).label('revision'),
                t.valid_from_ms, t.valid_to_ms, t.build_id,
                (t.outcome if kind == 'response' else sa.null()).label('outcome'),
                (t.reactions if kind == 'response' else sa.literal(0)).label('reactions'),
                (t.edits if kind == 'response' else sa.literal(0)).label('edits'),
                root_ids.label('root_ids'), *counts, *(t[f] for f in fields),
            ).where(t.unit == unit, t.build_id == current_build_id(), predicate)
            # Reject incomplete/oversized roots BEFORE the bounded page. They
            # remain unacknowledged and a later readable revision is rediscovered.
            # Flattening repeats the correlated roots in filters, pending-revision
            # checks and ordering; JIT compilation alone exceeded the source timeout.
            q = stmt.cte('rollup_roots').prefix_with('MATERIALIZED')
            stmt = sa.select(q).where(
                sa.func.cardinality(q.c.root_ids).between(1, 100),
                sa.or_(*(q.c[f] != 0 for f in fields)),
                *(q.c[f] == q.c['root_count_' + str(i)] for i, f in enumerate(fields)),
            )
            if kind == 'response':
                stmt = stmt.where(sa.exists().where(
                    MESSAGES.c.id == sa.cast(sa.func.split_part(q.c.source, ':', 2),
                                             sa.BigInteger),
                    _in_rooms(rooms), MESSAGES.c.is_nora == 1,
                    MESSAGES.c.hidden_at.is_(None),
                    ~sa.exists().where(MESSAGE_DELETE.c.message_id == MESSAGES.c.id),
                ))
            rows = await self._pending(key=key, domain='rollup', stmt=stmt, limit=limit,
                                       citations=citations, include_settled=row_ids is not None)
            for r in rows:
                ids = r.root_ids or []
                if not ids or len(ids) > 100 or not any(int(getattr(r, f)) for f in fields) or any(
                    int(getattr(r, f)) != int(getattr(r, 'root_count_' + str(i)))
                    for i, f in enumerate(fields)
                ):
                    continue  # unavailable/incomplete roots never settle
                origin = await self._roots(refs=[str(i) for i in ids], rooms=rooms)
                if {x.row.id for x in origin} != set(ids):
                    continue  # a withdrawal between reads cannot become support
                episode = None
                properties = tuple((f, str(getattr(r, f))) for f in fields)
                if kind == 'response':
                    send_id = int(r.source.split(':', 1)[1])
                    async with self._engine.connect() as conn:
                        sent = (await conn.execute(transcript_select().add_columns(
                            MESSAGES.c.conversation_id).where(_in_rooms(rooms),
                            MESSAGES.c.id == send_id, MESSAGES.c.is_nora == 1,
                            ~sa.exists().where(MESSAGE_DELETE.c.message_id == MESSAGES.c.id),
                        ))).first()
                    if sent is None:
                        continue
                    # The unit episode's total was checked above, but this
                    # account receives only its subject's actual replies.
                    origin = tuple(x for x in origin if x.row.person_id == person)
                    if not origin:
                        continue
                    episode = Episode(send_id=send_id, room=sent.conversation_id,
                        turn_id=sent.turn_id,
                        text=transcript_row(sent).body or '', outcome=r.outcome,
                        reactions=r.reactions, edits=r.edits,
                        replies=tuple(x.row for x in origin))
                    properties = (('quoted_replies_by_subject', str(len(origin))),)
                out.append(PersonSourceRevision(
                    domain='rollup', source=r.source, revision=r.revision, state='computed',
                    recorded_at=f'build:{r.build_id}', roots=origin,
                    effective_from=r.valid_from_ms, effective_until=r.valid_to_ms,
                    as_of=max(x.row.ts for x in origin),
                    properties=properties, episode=episode,
                    citable=r.citation,
                ))
                if len(out) >= limit:
                    return [*presence, *out]
        return [*presence, *out]

    async def presence_revisions(self, *, key: str, unit: str, person: str,
                       limit: int, since_ms: int = 0, until_ms: int = 2**62,
                       include_settled: bool = False) -> list[PersonSourceRevision]:
        """The existing coordinate-free Rollup shape plus named routine metadata.

        Source identity and revision exclude build time. Each bounded stay/trip
        is one occasion; place/rhythm/corridor summaries add no occasions.
        """
        reader = SqlRollupRead(self._engine)
        rows: list[tuple[Any, str]] = []
        # The same safe SELECT as rollup_read, with pending-revision filtering
        # before LIMIT so acknowledged early stays cannot starve later ones.
        for kind in ("stay", "trip"):
            query = RollupReadQuery(kind=kind, unit=unit, subjects=(person,),
                                   start_ms=since_ms, end_ms=until_ms, limit=limit)
            shape = reader._select(query).subquery()
            source = (sa.literal(f"presence:{kind}:{person}:")
                      + sa.cast(shape.c.valid_from_ms, sa.Text))
            values = sa.func.to_jsonb(shape.table_valued())
            stmt = sa.select(shape, source.label("source"), source.label("citation"),
                             _revision(values).label("revision"))
            pending = await self._pending(key=key, domain="rollup", stmt=stmt, limit=limit,
                                          include_settled=include_settled)
            rows.extend((_row(kind, r), r.revision) for r in pending)
        if await SqlRollupStore(self._engine).current_build() is None:
            return [PersonSourceRevision(domain="rollup", source="presence:unavailable",
                revision="", state="unavailable", recorded_at="unknown")]
        mobility = SqlMobilityStore(self._engine)
        until_ms = min(until_ms, int(time.time() * 1000))
        places = await mobility.places_with_aggregates(subject_id=person, until_ms=until_ms)
        corridors = await mobility.corridors_for_subject(subject_id=person, until_ms=until_ms)
        rhythm = await mobility.rhythm_bins(subject_id=person, window_ms=180 * 86400000,
                                            until_ms=until_ms)
        # Explicit projection: coordinates/raw fixes never cross this seam.
        routine = {
            "places": [{k: p[k] for k in ("kind", "label", "stay_count",
                        "first_seen_ts", "last_seen_ts")} for p in places],
            "corridors": [{"sequence": list(c.sequence), "samples": c.sample_count,
                           "first": c.first_seen_ts, "last": c.last_seen_ts} for c in corridors],
            "rhythm": rhythm,
        }
        # Context has no citation/evidence: it cannot mint an occasion. One
        # snapshot per call (one day per windowed history call), not per stay.
        routine_json = json.dumps(routine, ensure_ascii=False)
        routine_source = f"presence:routine:{person}"
        routine_revision = hashlib.sha256(routine_json.encode()).hexdigest()
        pending_routine = await self._pending(key=key, domain="rollup", limit=1,
            include_settled=include_settled, stmt=sa.select(
                sa.literal(routine_source).label("source"),
                sa.literal(routine_revision).label("revision")))
        out = [PersonSourceRevision(domain="rollup", source=routine_source,
            subject=person, revision=routine_revision,
            state="computed", recorded_at="unknown", as_of=until_ms,
            properties=(("routine", routine_json),))] if pending_routine and any(
                routine.values()) else []
        for row, revision in rows:
            cite = f"presence:{row.kind}:{person}:{row.valid_from_ms}"
            fields = {k: v for k, v in row.fields.items()
                      if k in ("place_kind", "place_label", "place_saved", "distance_m",
                               "avg_speed_kmh")}
            properties = (("observation", json.dumps(fields, ensure_ascii=False)),)
            out.append(PersonSourceRevision(domain="rollup", source=cite, revision=revision,
                subject=person,
                state="computed", recorded_at="unknown", citable=cite,
                effective_from=row.valid_from_ms, effective_until=row.valid_to_ms,
                as_of=row.valid_from_ms, properties=properties,
                evidence=((cite, Said(row.valid_from_ms, person, None, occasion=cite,
                    happened_at=row.valid_from_ms, medium="presence", location=True)),)))
        return out
