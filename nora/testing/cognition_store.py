"""`InMemoryCognitionStore` -- the domain-only double for
`nora.infra.sql.cognition_store.SqlCognitionStore`, bound to it by
`tests/contract/test_cognition_store_contract.py`.

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
    NewCognitionSession,
    NewCognitionSessionClose,
)
from nora.testing.stimuli import live_stimulus


class InMemoryCognitionStore:
    def __init__(self) -> None:
        self._session_ids = itertools.count(1)
        self._close_ids = itertools.count(1)
        self._pace_ids = itertools.count(1)
        self.sessions: list[tuple[int, NewCognitionSession, int]] = []
        self.closes: list[tuple[int, NewCognitionSessionClose, int]] = []
        self.paces: list[tuple[int, NewCognitionPace, int]] = []
        #: The retracted stimulus ids of the database this ledger shares with
        #: an `InMemoryStimulusStore(ledger=self)`: ONE set, as the SQL twins
        #: read one `stimulus_retraction` table.
        self.retracted: set[int] = set()
        #: Method names that raise, as a database outage does
        #: (`"record_session"`, `"record_pace"`, ...).
        self.fail_on: set[str] = set()

    def _check(self, method: str) -> None:
        if method in self.fail_on:
            raise RuntimeError(f"ledger unavailable ({method})")

    async def record_session(
        self, *, session: NewCognitionSession, recorded_at_ms: int,
    ) -> int:
        self._check("record_session")
        session_id = next(self._session_ids)
        self.sessions.append((session_id, session, recorded_at_ms))
        return session_id

    async def record_close(
        self, *, close: NewCognitionSessionClose, recorded_at_ms: int,
    ) -> int:
        close_id = next(self._close_ids)
        self.closes.append((close_id, close, recorded_at_ms))
        return close_id

    async def record_pace(
        self, *, pace: NewCognitionPace, recorded_at_ms: int,
    ) -> int:
        self._check("record_pace")
        pace_id = next(self._pace_ids)
        self.paces.append((pace_id, pace, recorded_at_ms))
        return pace_id

    async def pending_self_wakes(
        self, *, unit: str, since_ms: int,
    ) -> Sequence[PendingSelfWake]:
        mine = [(p, at) for _, p, at in self.paces if p.unit == unit]
        recent = {p.wake_ref for p, at in mine if at >= since_ms and p.wake_ref is not None}
        return fold_self_wakes([
            PaceRow(
                wake_ref=p.wake_ref, transition=p.transition, outcome=p.outcome,
                subject_rows=p.subject_rows, session_id=p.session_id,
                stimulus_id=p.stimulus_id, at_ms=p.at_ms, supersedes=p.supersedes,
                retracted=not live_stimulus(self.retracted, p.stimulus_id),
            )
            for p, _ in mine if p.wake_ref in recent
        ])

    async def count_self_wakes_created(self, *, unit: str, since_ms: int) -> int:
        return sum(
            1 for _, p, at in self.paces
            if p.unit == unit and p.verb == "wake_at" and p.outcome == "recorded"
            and at >= since_ms
        )

    async def aclose(self) -> None:
        return None
