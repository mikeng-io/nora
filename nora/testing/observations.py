"""`FakeObservationStore` — the in-memory `ObservationStore` contract double.

Same rules as the SQL store, same fold (`fold_observation`/
`rebuild_head`): append dedupes on (platform, account_key, source_digest),
the head updates transactionally with the row, targets re-link idempotently.
The `tx_hook` contract is honest here too: it is called with
`(conn=None, observation_id)` — a fake has no SQL connection, but the hook
still runs INSIDE the append so a hook failure fails the append, matching
the transaction's all-or-nothing shape.
"""

from __future__ import annotations

import inspect
import itertools
from dataclasses import replace
from typing import Any

from nora.domain.models import ReactionRow
from nora.domain.observations import (
    Observation,
    ObservationHead,
    SourceScope,
    StoredObservation,
    _record_us,
    fold_observation,
    fold_target_reactions,
    object_handle,
    parse_handle,
    rebuild_head,
    target_key,
    union_head,
)


class FakeObservationStore:
    def __init__(self, records: Any = None) -> None:
        self._rows: list[StoredObservation] = []
        self._next_id = 1
        self._digests: set[tuple[str, str, str]] = set()
        self._heads: dict[tuple[str, str, str, str, str, str], ObservationHead] = {}
        # `platform_object_state.updated_at`'s mirror: the row id of the
        # newest append that folded into each head — `heads` orders on it
        # exactly as the SQL store orders on `updated_at`.
        self._touched: dict[tuple[str, str, str, str, str, str], int] = {}
        self._records = records
        # The durable commit clock `recorded_at` stamps: when a
        # `FakeRecordStore` is wired BOTH tables stamp from ONE counter —
        # the fake's mirror of both stamps being the same database's clock
        # under the ingest lock, so a union fold sees genuine commit order
        # rather than a fabricated one. A lone store stamps its own.
        self._clock = (records._clock if records is not None
                       else itertools.count(1))

    @staticmethod
    def _object_key(scope: SourceScope, kind: str, object_ref: str):
        return (*scope.key(), kind, object_ref)

    @staticmethod
    def _in_scope(row_scope: SourceScope, scope: SourceScope) -> bool:
        """The store's read boundary, same rule as `_scope_where`'s: an
        EMPTY `account_key` reads room-wide (`platform` +
        `conversation_id` + `thread`), a named one must match exactly."""
        if scope.account_key:
            return row_scope.key() == scope.key()
        return (row_scope.platform == scope.platform
                and row_scope.conversation_id == scope.conversation_id
                and row_scope.thread == scope.thread)

    def _scoped_rows(self, scope: SourceScope) -> list[StoredObservation]:
        return [r for r in self._rows
                if self._in_scope(r.observation.scope, scope)]

    def _edit_arrived(self, *, platform: str, conversation_id: str, sent_ts: int) -> None:
        """Mirror the edit transaction's unique, room-bound target re-link."""
        if self._records is None:
            return
        rows = []
        for row in self._rows:
            o = row.observation
            if (row.target_message_id is not None or o.target is None
                    or o.target.message_id or o.target.sent_ts != sent_ts
                    or o.scope.platform != platform
                    or o.scope.conversation_id != conversation_id or o.scope.thread):
                rows.append(row)
                continue
            matches = [r["id"] for r in self._records.rows
                       if r["platform"] == platform
                       and r["conversation_id"] == conversation_id
                       and (not o.target_author_id or r["sender_id"] == o.target_author_id)
                       and (r["ts"] == sent_ts or any(
                           e["message_id"] == r["id"] and e["ts"] == sent_ts
                           for e in self._records.edits))]
            rows.append(replace(row, target_message_id=matches[0]) if len(matches) == 1 else row)
        self._rows = rows

    def _reaction_arrived(self, message_id: int, recorded_at: int) -> None:
        for row in self._rows:
            obs = row.observation
            if obs.object_kind == "reaction" and row.target_message_id == message_id:
                key = self._object_key(obs.scope, obs.object_kind, obs.object_ref)
                self._touched[key] = recorded_at

    async def append(
        self, *, observation: Observation, event_id: str, source_digest: str,
        target_message_id: int | None = None,
        delivered_message_id: int | None = None, tx_hook: Any = None,
    ) -> int | None:
        obs = observation
        dedupe = (obs.scope.platform, obs.scope.account_key, source_digest)
        if dedupe in self._digests:
            return None  # replay of an admitted source event: no effect
        row = StoredObservation(
            id=self._next_id, event_id=event_id, source_digest=source_digest,
            observation=obs, target_message_id=target_message_id,
            delivered_message_id=delivered_message_id,
            recorded_at=next(self._clock))
        self._next_id += 1
        if tx_hook is not None:
            awaited = tx_hook(None, row.id)
            if inspect.isawaitable(awaited):
                await awaited
        self._digests.add(dedupe)
        self._rows.append(row)
        key = self._object_key(obs.scope, obs.object_kind, obs.object_ref)
        self._heads[key] = fold_observation(self._heads.get(key), row)
        self._touched[key] = int(row.recorded_at)
        return row.id

    def _union_rows(
        self, scope: SourceScope, h: ObservationHead,
    ) -> list[StoredObservation]:
        """The stored reaction rows of THIS object's own group — the head's
        `(account_key, thread)` keeps a room-wide read's same-named
        objects apart, matching the SQL union's per-object partition."""
        return [
            r for r in self._rows
            if r.observation.object_kind == "reaction"
            and r.observation.object_ref == h.object_ref
            and r.observation.scope.account_key == h.account_key
            and r.observation.scope.thread == h.thread
            and self._in_scope(r.observation.scope, scope)
        ]

    def _legacy(self, mids: set[int]) -> list[ReactionRow]:
        """The wired record store's `message_reaction` rows for these
        resolved targets — the legacy half the union fold needs."""
        if self._records is None:
            return []
        return [
            ReactionRow(
                id=int(d["id"]), message_id=int(d["message_id"]),
                sender_id=str(d["sender_id"]), emoji=str(d["emoji"]),
                removed=bool(d["removed"]), ts=int(d["ts"]),
                created_at=d.get("created_at"))
            for d in self._records.reactions
            if int(d["message_id"]) in mids
        ]

    def _union_head(self, scope: SourceScope, h: ObservationHead) -> ObservationHead:
        """A `reaction` head re-folded across BOTH stores — the fake's
        mirror of `SqlObservationStore._union_reactions`, so `current`/
        `heads`/`head_for_handle` serve the same state the fake's
        transcript annotation does."""
        if h.kind != "reaction":
            return h
        rows = self._union_rows(scope, h)
        mids = {r.target_message_id for r in rows if r.target_message_id}
        tr = fold_target_reactions(self._legacy(mids), rows)
        if tr is None:
            return h
        return union_head(tr, object_ref=h.object_ref,
                          account_key=h.account_key, thread=h.thread)

    async def current(
        self, *, scope: SourceScope, kind: str, object_ref: str,
    ) -> ObservationHead | None:
        head: ObservationHead | None = None
        if scope.account_key:
            head = self._heads.get(self._object_key(scope, kind, object_ref))
        else:
            for (p, _a, c, t, k, r), h in self._heads.items():
                if (p, c, t, k, r) == (scope.platform, scope.conversation_id,
                                       scope.thread, kind, object_ref):
                    head = h
                    break
        return None if head is None else self._union_head(scope, head)

    async def heads(
        self, *, scope: SourceScope, kind: str | None = None,
        limit: int = 100,
    ) -> list[ObservationHead]:
        out = [(key, h) for key, h in self._heads.items()
               for (p, a, c, t, k, _r) in (key,)
               if self._in_scope(
                   SourceScope(platform=p, account_key=a, conversation_id=c,
                               thread=t), scope)
               and (kind is None or k == kind)]
        # `updated_at DESC` — most-recently-touched first, dict order the
        # tie-break, matching the SQL store's page.
        out.sort(key=lambda kh: self._touched.get(kh[0], 0), reverse=True)
        return [self._union_head(scope, h) for _key, h in out][: max(limit, 0)]

    async def head_for_handle(
        self, *, scope: SourceScope, handle: str,
    ) -> ObservationHead | None:
        kind, _digest = parse_handle(handle)
        if not kind:
            return None
        for (p, a, c, t, k, _r), h in self._heads.items():
            if (self._in_scope(
                    SourceScope(platform=p, account_key=a, conversation_id=c,
                                thread=t), scope)
                    and k == kind
                    and object_handle(k, h.object_ref, account_key=a,
                                      thread=t) == handle):
                return self._union_head(scope, h)
        return None

    async def history(
        self, *, scope: SourceScope, kind: str, object_ref: str,
        after_id: int = 0, limit: int = 1000,
    ) -> list[StoredObservation]:
        out = [r for r in self._scoped_rows(scope)
               if r.observation.object_kind == kind
               and r.observation.object_ref == object_ref
               and r.id > after_id]
        return out[: max(limit, 0)]

    async def as_of(
        self, *, scope: SourceScope, kind: str, object_ref: str,
        through_id: int, limit: int = 1000,
    ) -> tuple[ObservationHead | None, bool]:
        if kind == "reaction":
            rows = [r for r in self._rows
                    if r.observation.object_kind == "reaction"
                    and r.observation.object_ref == object_ref
                    and self._in_scope(r.observation.scope, scope)
                    and r.id <= through_id]
            bound_row = next(
                (r for r in self._rows if r.id == through_id), None)
            bound_at = (None if bound_row is None
                        else _record_us(bound_row.recorded_at))
            more = len(rows) > max(limit, 0)
            kept = rows[: max(limit, 0)]
            mids = {r.target_message_id for r in kept
                    if r.target_message_id}
            legacy_all = self._legacy(mids)
            if bound_at is None:
                legacy = []
                gap = bool(mids)
            else:
                legacy = [
                    r for r in legacy_all
                    if (at := _record_us(r.created_at)) is not None
                    and at <= bound_at]
                gap = len(legacy) < len(legacy_all)
            tr = fold_target_reactions(
                legacy, kept, partial=more, gap=gap)
            if tr is None:
                return None, more
            account_key = thread = ""
            if kept:
                last = kept[-1].observation.scope
                account_key, thread = last.account_key, last.thread
            return union_head(
                tr, object_ref=object_ref, account_key=account_key,
                thread=thread), more
        rows = [r for r in self._scoped_rows(scope)
                if r.observation.object_kind == kind
                and r.observation.object_ref == object_ref
                and r.id <= through_id]
        more = len(rows) > max(limit, 0)
        head = rebuild_head(rows[: max(limit, 0)],
                            kind=kind, object_ref=object_ref)
        return head, more

    async def recent(
        self, *, scope: SourceScope, since_ms: int = 0, limit: int = 50,
    ) -> list[StoredObservation]:
        def at(r: StoredObservation) -> int:
            o = r.observation
            return o.occurred_ms if o.occurred_ms is not None else o.received_ms
        out = sorted((r for r in self._scoped_rows(scope) if at(r) >= since_ms),
                     key=lambda r: (at(r), r.id), reverse=True)
        return out[: max(limit, 0)]

    async def by_ids(
        self, *, scope: SourceScope, ids: tuple[int, ...], limit: int = 200,
    ) -> list[StoredObservation]:
        wanted = set(ids)
        out = [r for r in self._scoped_rows(scope) if r.id in wanted]
        return out[: max(limit, 0)]

    async def resolve_targets(
        self, *, scope: SourceScope, target_key: str, message_id: int,
    ) -> int:
        n = 0
        rows: list[StoredObservation] = []
        for r in self._rows:
            o = r.observation
            if (o.scope.key() == scope.key()
                    and target_key == self._row_target_key(r)
                    and r.target_message_id is None):
                r = StoredObservation(
                    id=r.id, event_id=r.event_id,
                    source_digest=r.source_digest, observation=o,
                    target_message_id=message_id,
                    recorded_at=r.recorded_at)
                n += 1
            rows.append(r)
        self._rows = rows
        return n

    @staticmethod
    def _row_target_key(r: StoredObservation) -> str | None:
        return target_key(r.observation.target,
                          author_id=r.observation.target_author_id)

    async def unresolved_targets(
        self, *, scope: SourceScope, limit: int = 200,
    ) -> list[StoredObservation]:
        out = [r for r in self._rows
               if r.observation.scope.key() == scope.key()
               and r.observation.target is not None
               and r.target_message_id is None]
        return out[: max(limit, 0)]

    async def aclose(self) -> None:
        pass


#: Alias matching the port docstring's naming (`InMemory`-style testing
#: doubles in `nora/testing/` use either convention; the port names this).
InMemoryObservationStore = FakeObservationStore
