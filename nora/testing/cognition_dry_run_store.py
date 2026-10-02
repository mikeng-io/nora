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
from nora.domain.cognition_remind import MirrorRow, PendingMirroredRemind, fold_pending_reminds
from nora.domain.ports import (
    NewCognitionPace,
    NewCognitionRemindMirror,
    NewCognitionSession,
    NewCognitionSessionClose,
    NewCognitionWouldWrite,
)
from nora.testing.stimuli import live_stimulus


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

    async def record_would_write(
        self, *, write: NewCognitionWouldWrite, recorded_at_ms: int,
    ) -> int:
        write_id = next(self._write_ids)
        self.would_writes.append((write_id, write, recorded_at_ms))
        return write_id

    async def record_pace(
        self, *, pace: NewCognitionPace, recorded_at_ms: int,
    ) -> int:
        self._check("record_pace")
        pace_id = next(self._pace_ids)
        self.paces.append((pace_id, pace, recorded_at_ms))
        return pace_id

    async def record_remind_mirror(
        self, *, mirror: NewCognitionRemindMirror, recorded_at_ms: int,
    ) -> int:
        mirror_id = next(self._remind_ids)
        self.remind_mirrors.append((mirror_id, mirror, recorded_at_ms))
        return mirror_id

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

    async def count_reminds_created(
        self, *, unit: str, platform: str, conversation_id: str, since_ms: int,
    ) -> int:
        return sum(
            1 for _, m, at in self.remind_mirrors
            if m.unit == unit and m.verb == "remind" and m.outcome == "recorded"
            and m.platform == platform and m.conversation_id == conversation_id
            and at >= since_ms
        )

    async def pending_mirrored_reminds(
        self, *, unit: str, now_ms: int,
    ) -> Sequence[PendingMirroredRemind]:
        mine = [m for _, m, _ in self.remind_mirrors if m.unit == unit]
        wanted = {
            m.reminder_ref for m in mine
            if m.outcome == "recorded" and m.verb in ("remind", "remind_move")
            and m.at_ms is not None and m.at_ms >= now_ms and m.reminder_ref is not None
        }
        return fold_pending_reminds([
            MirrorRow(
                reminder_ref=m.reminder_ref, verb=m.verb, outcome=m.outcome,
                platform=m.platform, conversation_id=m.conversation_id,
                subject_rows=m.subject_rows, at_ms=m.at_ms, session_id=m.session_id,
            )
            for m in mine if m.outcome == "recorded" and m.reminder_ref in wanted
        ], now_ms=now_ms)

    async def aclose(self) -> None:
        return None
