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

from typing import Any

from nora.domain.observations import (
    Observation,
    ObservationHead,
    SourceScope,
    StoredObservation,
    fold_observation,
    rebuild_head,
    target_key,
)


class FakeObservationStore:
    def __init__(self) -> None:
        self._rows: list[StoredObservation] = []
        self._next_id = 1
        self._digests: set[tuple[str, str, str]] = set()
        self._heads: dict[tuple[str, str, str, str, str, str], ObservationHead] = {}

    @staticmethod
    def _object_key(scope: SourceScope, kind: str, object_ref: str):
        return (*scope.key(), kind, object_ref)

    async def append(
        self, *, observation: Observation, event_id: str, source_digest: str,
        target_message_id: int | None = None, tx_hook: Any = None,
    ) -> int | None:
        obs = observation
        dedupe = (obs.scope.platform, obs.scope.account_key, source_digest)
        if dedupe in self._digests:
            return None  # replay of an admitted source event: no effect
        row = StoredObservation(
            id=self._next_id, event_id=event_id, source_digest=source_digest,
            observation=obs, target_message_id=target_message_id)
        self._next_id += 1
        if tx_hook is not None:
            tx_hook(None, row.id)
        self._digests.add(dedupe)
        self._rows.append(row)
        key = self._object_key(obs.scope, obs.object_kind, obs.object_ref)
        self._heads[key] = fold_observation(self._heads.get(key), row)
        return row.id

    async def current(
        self, *, scope: SourceScope, kind: str, object_ref: str,
    ) -> ObservationHead | None:
        return self._heads.get(self._object_key(scope, kind, object_ref))

    async def heads(
        self, *, scope: SourceScope, kind: str | None = None,
        limit: int = 100,
    ) -> list[ObservationHead]:
        out = [h for (p, a, c, t, k, _r), h in self._heads.items()
               if (p, a, c, t) == scope.key() and (kind is None or k == kind)]
        return out[: max(limit, 0)]

    async def history(
        self, *, scope: SourceScope, kind: str, object_ref: str,
        after_id: int = 0, limit: int = 1000,
    ) -> list[StoredObservation]:
        out = [r for r in self._rows
               if r.observation.scope.key() == scope.key()
               and r.observation.object_kind == kind
               and r.observation.object_ref == object_ref
               and r.id > after_id]
        return out[: max(limit, 0)]

    async def as_of(
        self, *, scope: SourceScope, kind: str, object_ref: str,
        through_id: int,
    ) -> ObservationHead | None:
        rows = [r for r in self._rows
                if r.observation.scope.key() == scope.key()
                and r.observation.object_kind == kind
                and r.observation.object_ref == object_ref
                and r.id <= through_id]
        return rebuild_head(rows, kind=kind, object_ref=object_ref)

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
                    target_message_id=message_id)
                n += 1
            rows.append(r)
        self._rows = rows
        return n

    @staticmethod
    def _row_target_key(r: StoredObservation) -> str | None:
        return target_key(r.observation.target)

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
