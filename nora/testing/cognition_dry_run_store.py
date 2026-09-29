"""`InMemoryCognitionDryRunStore` -- the domain-only double for
`nora.infra.sql.cognition_dry_run.SqlCognitionDryRunStore`, bound to it by
`tests/contract/test_cognition_dry_run_store_contract.py`
(design/cognition-loop/70-migration.md step 3a).

All three rows are append-only, so the double is three lists rather than
three dicts: no key is ever a compare-and-swap target here, unlike
`InMemoryStandingStore` next door.
"""

from __future__ import annotations

import itertools

from nora.domain.ports import (
    NewCognitionSession,
    NewCognitionSessionClose,
    NewCognitionWouldWrite,
)


class InMemoryCognitionDryRunStore:
    def __init__(self) -> None:
        self._session_ids = itertools.count(1)
        self._close_ids = itertools.count(1)
        self._write_ids = itertools.count(1)
        self.sessions: list[tuple[int, NewCognitionSession, int]] = []
        self.closes: list[tuple[int, NewCognitionSessionClose, int]] = []
        self.would_writes: list[tuple[int, NewCognitionWouldWrite, int]] = []

    async def record_session(
        self, *, session: NewCognitionSession, recorded_at_ms: int,
    ) -> int:
        session_id = next(self._session_ids)
        self.sessions.append((session_id, session, recorded_at_ms))
        return session_id

    async def record_close(
        self, *, close: NewCognitionSessionClose, recorded_at_ms: int,
    ) -> int:
        close_id = next(self._close_ids)
        self.closes.append((close_id, close, recorded_at_ms))
        return close_id

    async def record_would_write(
        self, *, write: NewCognitionWouldWrite, recorded_at_ms: int,
    ) -> int:
        write_id = next(self._write_ids)
        self.would_writes.append((write_id, write, recorded_at_ms))
        return write_id

    async def aclose(self) -> None:
        return None
