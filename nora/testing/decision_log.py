"""`InMemoryDecisionLogStore` — the domain-only double for
`nora.infra.sql.decision_log.SqlDecisionLogStore`, bound to it by
`tests/contract/test_decision_log_store_contract.py`.

**It refuses what Postgres refuses, and that is not automatic.**
`InMemoryStandingStore` is the precedent: a fake that stores whatever it
is handed is MORE PERMISSIVE THAN THE REAL STORE, and a contract suite cannot
see it unless a test reaches the store by going *around* the domain type. So
this mirrors all three CHECKs and — the one that matters most — the
duplicate-key refusal that IS the append-only property.

**And it orders the way Postgres orders.** `ts DESC, id DESC` to take the newest
rows, then reversed to oldest-first. Both halves are copied deliberately: a
double that took the FIRST `limit` rows would answer a busy room's oldest
decisions, and one with no `id` tie-break would order two rows in one
millisecond by whatever Python hashed first — a different block from the same
input, which is the fake-diverges failure this file exists to avoid.
"""

from __future__ import annotations

from collections.abc import Sequence

from nora.domain.decision_log import (
    REASON_MAX_CHARS,
    RECENT_LIMIT,
    VERDICTS,
    WRITERS,
    DecisionRow,
)


class DuplicateDecisionError(Exception):
    """A second row with an id that already exists.

    Named rather than a bare `ValueError` because it is the append-only
    property announcing itself: the SQL store raises `IntegrityError` from the
    primary key, and a caller distinguishing "an id was reused" from "this row
    is malformed" needs the two to be different types.
    """


class InMemoryDecisionLogStore:
    def __init__(self) -> None:
        self._rows: dict[str, DecisionRow] = {}

    async def append(self, *, row: DecisionRow) -> None:
        if row.id in self._rows:
            raise DuplicateDecisionError(
                f"decision {row.id} is already logged; the decision log is "
                "append-only and never rewrites a row"
            )
        # `decision_log_writer_is_known_check`,
        # `decision_log_verdict_is_known_check` and
        # `decision_log_reason_fits_the_block_check`. Unreachable through
        # `DecisionRow.__post_init__`, which is exactly why they are here: the
        # CHECKs exist for a writer that got a value some other way, and a
        # double without them cannot stand in for the real store on that input.
        if row.writer not in WRITERS:
            raise ValueError("decision_log_writer_is_known_check")
        if row.verdict not in VERDICTS:
            raise ValueError("decision_log_verdict_is_known_check")
        if len(row.reason) > REASON_MAX_CHARS:
            raise ValueError("decision_log_reason_fits_the_block_check")
        self._rows[row.id] = row

    async def recent(
        self, *, conversation_id: str, since_ms: int, limit: int = RECENT_LIMIT,
    ) -> tuple[DecisionRow, ...]:
        return self._window(
            [r for r in self._rows.values() if r.conversation_id == conversation_id],
            since_ms=since_ms, limit=limit,
        )

    async def recent_for_unit(
        self, *, unit: str, since_ms: int,
        writers: Sequence[str] = (), limit: int = RECENT_LIMIT,
    ) -> tuple[DecisionRow, ...]:
        # The `writers` narrowing happens BEFORE `_window`, matching the SQL
        # store's WHERE. A double that filtered after the limit would answer
        # fewer rows than Postgres for the same input — and would hide the
        # exact starvation the argument exists to prevent.
        return self._window(
            [
                r for r in self._rows.values()
                if r.unit == unit
                and (not writers or r.writer in writers)
            ],
            since_ms=since_ms, limit=limit,
        )

    @staticmethod
    def _window(
        rows: list[DecisionRow], *, since_ms: int, limit: int,
    ) -> tuple[DecisionRow, ...]:
        inside = sorted(
            (r for r in rows if r.ts >= since_ms), key=lambda r: (r.ts, r.id),
        )
        # `[-limit:]` and NOT `[:limit]`. The whole point of the bound is to
        # answer the most recent decisions; taking the head answers the oldest
        # ones under a note that says "the last 24 hours".
        #
        # `limit <= 0` answers NOTHING, spelled out because Python disagrees
        # with SQL here and silently: `inside[-0:]` is `inside[0:]` — every
        # row — while `LIMIT 0` is no rows. Unreachable from production, which
        # is exactly the shape this file exists to refuse: a double that is
        # more permissive than the store on an input no test happens to try.
        return tuple(inside[-limit:]) if limit > 0 else ()
