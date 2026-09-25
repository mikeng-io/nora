"""`InMemorySummonsLedger` — the double for
`nora.infra.sql.summons.SqlSummonsLedger`, bound to it by
`tests/contract/test_summons_ledger_contract.py`.

Its `claim` is atomic the way the SQL one is: there is no `await` between the
membership check and the insert, so two concurrent claims on one comment in
one event loop cannot both win. A fake that could let both through would pass
a test the real store fails, and the property under test is exactly that.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime
from uuid import UUID

from nora.domain.summons import SUMMONS_MODES, SUMMONS_STATES, SummonsRow


class InMemorySummonsLedger:
    def __init__(self) -> None:
        self._rows: dict[UUID, SummonsRow] = {}
        self._by_comment: dict[str, UUID] = {}

    async def claim(
        self, *, comment_id: str, media_id: str, shortcode: str,
        author_igid: str, mention_at: datetime | None, mode: str, now: datetime,
    ) -> UUID | None:
        if mode not in SUMMONS_MODES:
            raise ValueError(f"unknown summons mode {mode!r}")
        if comment_id in self._by_comment:
            return None
        row_id = uuid.uuid4()
        self._by_comment[comment_id] = row_id
        self._rows[row_id] = SummonsRow(
            id=row_id, comment_id=comment_id, media_id=media_id, shortcode=shortcode,
            author_igid=author_igid, person_id=None, mention_at=mention_at,
            seen_at=now, state="claimed", mode=mode, updated_at=now,
        )
        return row_id

    async def finish(
        self, summons_id: UUID, state: str, *, now: datetime,
        person_id: str | None = None, reply_text: str | None = None,
        reply_comment_id: str | None = None, bridge_code: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        if state not in SUMMONS_STATES:
            raise ValueError(f"unknown summons state {state!r}")
        row = self._rows.get(summons_id)
        if row is None:
            return
        changes: dict[str, object] = {"state": state, "updated_at": now}
        for key, value in (
            ("person_id", person_id), ("reply_text", reply_text),
            ("reply_comment_id", reply_comment_id), ("bridge_code", bridge_code),
            ("trace_id", trace_id),
        ):
            if value is not None:
                changes[key] = value
        self._rows[summons_id] = dataclasses.replace(row, **changes)  # type: ignore[arg-type]

    async def seen(self, comment_ids: tuple[str, ...]) -> frozenset[str]:
        return frozenset(c for c in comment_ids if c in self._by_comment)

    async def awaiting_flush(self) -> SummonsRow | None:
        waiting = sorted(
            (r for r in self._rows.values() if r.state == "staged" and r.mode == "live"),
            key=lambda r: (r.seen_at, str(r.id)),
        )
        return waiting[0] if waiting else None

    async def get(self, summons_id: UUID) -> SummonsRow | None:
        return self._rows.get(summons_id)

    async def sweep_stale_claims(self, *, now: datetime) -> int:
        swept = 0
        for row_id, row in list(self._rows.items()):
            if row.state == "claimed":
                self._rows[row_id] = dataclasses.replace(
                    row, state="failed", bridge_code="swept_at_boot", updated_at=now,
                )
                swept += 1
        return swept
