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
from collections.abc import Sequence

from nora.domain.cognition_pace import PaceRow, PendingSelfWake, fold_self_wakes
from nora.domain.ports import (
    NewCognitionPace,
    NewCognitionRemindMirror,
    NewCognitionSession,
    NewCognitionSessionClose,
    NewCognitionSessionEnd,
    NewCognitionWouldWrite,
)


class InMemoryCognitionDryRunStore:
    def __init__(self) -> None:
        self._session_ids = itertools.count(1)
        self._close_ids = itertools.count(1)
        self._write_ids = itertools.count(1)
        self._pace_ids = itertools.count(1)
        self._remind_ids = itertools.count(1)
        self.sessions: list[tuple[int, NewCognitionSession, int]] = []
        self.closes: list[tuple[int, NewCognitionSessionClose, int]] = []
        self.would_writes: list[tuple[int, NewCognitionWouldWrite, int]] = []
        self.paces: list[tuple[int, NewCognitionPace, int]] = []
        self.remind_mirrors: list[tuple[int, NewCognitionRemindMirror, int]] = []
        self._end_ids = itertools.count(1)
        self.ends: list[tuple[int, NewCognitionSessionEnd, int]] = []
        #: Set to make `record_session_end` raise, as a database outage does.
        self.fail_session_end = False

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

    async def record_pace(
        self, *, pace: NewCognitionPace, recorded_at_ms: int,
    ) -> int:
        pace_id = next(self._pace_ids)
        self.paces.append((pace_id, pace, recorded_at_ms))
        return pace_id

    async def record_remind_mirror(
        self, *, mirror: NewCognitionRemindMirror, recorded_at_ms: int,
    ) -> int:
        mirror_id = next(self._remind_ids)
        self.remind_mirrors.append((mirror_id, mirror, recorded_at_ms))
        return mirror_id

    async def record_session_end(
        self, *, end: NewCognitionSessionEnd, recorded_at_ms: int,
    ) -> int:
        if self.fail_session_end:
            raise RuntimeError("ledger unavailable")
        for end_id, existing, _ in self.ends:
            if existing.session_id == end.session_id:
                return end_id
        end_id = next(self._end_ids)
        self.ends.append((end_id, end, recorded_at_ms))
        return end_id

    async def pending_self_wakes(self, *, unit: str) -> Sequence[PendingSelfWake]:
        return fold_self_wakes([
            PaceRow(
                wake_ref=p.wake_ref, transition=p.transition, outcome=p.outcome,
                subject_rows=p.subject_rows, session_id=p.session_id,
                stimulus_id=p.stimulus_id, at_ms=p.at_ms, supersedes=p.supersedes,
            )
            for _, p, _ in self.paces if p.unit == unit
        ])

    async def count_self_wakes_created(self, *, unit: str, since_ms: int) -> int:
        return sum(
            1 for _, p, at in self.paces
            if p.unit == unit and p.verb == "wake_at" and p.outcome == "recorded"
            and at >= since_ms
        )

    async def aclose(self) -> None:
        return None
