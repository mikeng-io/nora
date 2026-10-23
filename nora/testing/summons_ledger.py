"""`InMemorySummonsLedger` — the double for
`nora.infra.sql.summons.SqlSummonsLedger`, bound to it by
`tests/contract/test_summons_ledger_contract.py`.

Its `claim` is atomic the way the SQL one is: there is no `await` between the
membership check and the insert, so two concurrent claims on one card (one
message id) in one event loop cannot both win. `bind_comment` is the same
shape over the comment id, which stays unique across rows. A fake that could
let both through would pass a test the real store fails, and the property
under test is exactly that.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta
from uuid import UUID

from nora.domain.summons import OPEN_STATES, SUMMONS_MODES, SUMMONS_STATES, SummonsRow


class InMemorySummonsLedger:
    def __init__(self) -> None:
        self._rows: dict[UUID, SummonsRow] = {}
        self._by_message: dict[str, UUID] = {}
        self._by_comment: dict[str, UUID] = {}

    async def claim(
        self, *, message_id: str, media_id: str, shortcode: str,
        author_igid: str, mention_at: datetime | None, mode: str, now: datetime,
    ) -> UUID | None:
        if mode not in SUMMONS_MODES:
            raise ValueError(f"unknown summons mode {mode!r}")
        if message_id in self._by_message:
            return None
        row_id = uuid.uuid4()
        self._by_message[message_id] = row_id
        self._rows[row_id] = SummonsRow(
            id=row_id, message_id=message_id, comment_id=None, media_id=media_id,
            shortcode=shortcode,
            author_igid=author_igid, person_id=None, mention_at=mention_at,
            seen_at=now, state="claimed", mode=mode, updated_at=now,
        )
        return row_id

    async def finish(
        self, summons_id: UUID, state: str, *, now: datetime,
        person_id: str | None = None, reply_text: str | None = None,
        reply_comment_id: str | None = None, bridge_code: str | None = None,
        trace_id: str | None = None, clear_reply: bool = False,
    ) -> bool:
        if state not in SUMMONS_STATES:
            raise ValueError(f"unknown summons state {state!r}")
        row = self._rows.get(summons_id)
        if row is None or row.state not in OPEN_STATES:
            return False
        changes: dict[str, object] = {"state": state, "updated_at": now}
        for key, value in (
            ("person_id", person_id), ("reply_text", reply_text),
            ("reply_comment_id", reply_comment_id), ("bridge_code", bridge_code),
            ("trace_id", trace_id),
        ):
            if value is not None:
                changes[key] = value
        if clear_reply:
            changes["reply_text"] = None
        self._rows[summons_id] = dataclasses.replace(row, **changes)  # type: ignore[arg-type]
        return True

    async def take_for_flush(self, summons_id: UUID, *, now: datetime) -> bool:
        row = self._rows.get(summons_id)
        if row is None or row.state != "staged":
            return False
        self._rows[summons_id] = dataclasses.replace(row, state="flushing", updated_at=now)
        return True

    async def bind_comment(
        self, summons_id: UUID, comment_id: str, *, now: datetime,
    ) -> bool:
        row = self._rows.get(summons_id)
        if (
            row is None or row.state != "claimed" or row.comment_id is not None
            or comment_id in self._by_comment
        ):
            return False
        self._by_comment[comment_id] = summons_id
        self._rows[summons_id] = dataclasses.replace(
            row, comment_id=comment_id, updated_at=now,
        )
        return True

    async def awaiting_flush(self) -> SummonsRow | None:
        waiting = sorted(
            (r for r in self._rows.values() if r.state == "staged" and r.mode == "live"),
            key=lambda r: (r.seen_at, str(r.id)),
        )
        return waiting[0] if waiting else None

    async def get(self, summons_id: UUID) -> SummonsRow | None:
        return self._rows.get(summons_id)

    async def sweep_stale_claims(
        self, *, now: datetime, older_than: timedelta,
    ) -> dict[str, int]:
        cutoff = now - older_than
        swept = {"failed": 0, "ambiguous": 0}
        for row_id, row in list(self._rows.items()):
            to = {"claimed": "failed", "flushing": "ambiguous"}.get(row.state)
            if to is None or row.updated_at is None or not row.updated_at < cutoff:
                continue
            self._rows[row_id] = dataclasses.replace(
                row, state=to, bridge_code="swept_at_boot", updated_at=now,
            )
            swept[to] += 1
        return swept
