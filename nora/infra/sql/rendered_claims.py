"""`SqlRenderedClaimStore` — what Nora rendered, so recognition can read it
rather than assume it.

`31-recognition.md` §3. Two verbs and a prune, and each one is a decision the
node argues:

- `record` UPSERTS one row per rendered claim per conversation. It is AWAITED on
  the tag path, not spawned: a fast follow-up correction against a ledger that
  has not been written yet reports «nothing matched» when the truth is «I could
  not check», which is R-27's exact inversion, and `tag_trace`'s fire-and-forget
  write is the shape to avoid here rather than to copy.
- `in_window` is the read recognition targets against.
- `prune` bounds it, because this is a WORKING SET and not an audit trail.

**R-27 on the read**, and it is the reason the store returns a status rather
than a bare tuple: an empty ledger and an unreadable one are different facts
about a turn, and collapsing them would make recognition report "nobody is
talking about anything we hold" whenever the database hiccuped.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Literal, get_args

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.facts import RenderedClaim
from nora.infra.sql.schema import RENDERED_CLAIM

log = logging.getLogger(__name__)

#: R-27's three states on the ledger read. The metric's label vocabulary is
#: `get_args` of THIS, never a hand-copied tuple.
LedgerStatus = Literal["ok", "nothing_found", "unavailable"]
LEDGER_STATUSES: frozenset[str] = frozenset(get_args(LedgerStatus))

@dataclass(frozen=True)
class LedgerRead:
    """The rows, and whether the read could be made at all.

    Two fields rather than one, because `()` with `status="ok"` («she rendered
    nothing about this») and `()` with `status="unavailable"` («I could not
    check what she rendered») licence completely different behaviour in the
    caller: the first means no dispute is possible, the second means no dispute
    may be RULED OUT.
    """

    claims: tuple[RenderedClaim, ...]
    status: LedgerStatus


class SqlRenderedClaimStore:
    """`SqlRenderedClaimStore(engine)` — a bare `AsyncEngine`, `metrics`
    optional and injected, the shape its siblings use."""

    def __init__(
        self, engine: AsyncEngine, *,
        window_hours: int = 24,
        metrics: Any = None,
    ) -> None:
        """`window_hours` is `[facts] recognition_window_hours` — how far back
        a message may reach when deciding what Nora "just rendered".

        The ledger is pruned at TWICE it, so a row stays readable for one
        window after it stops being eligible. That is what lets the prune be a
        background sweep on its own clock rather than a precise deletion racing
        the read.
        """
        self._engine = engine
        self._window_ms = window_hours * 60 * 60 * 1000
        self._metrics = metrics

    @property
    def window_ms(self) -> int:
        return self._window_ms

    async def record(
        self, *, claims: tuple[RenderedClaim, ...],
    ) -> LedgerStatus:
        """Write what this turn rendered. `nothing_found` for an empty turn —
        which is a real outcome and not a skipped call.

        **Never raises.** It runs on the tag path, and a ledger write failing
        must not cost a reply. It IS counted, because the next turn's targeting
        then has to report `unavailable` rather than an empty set, and a silent
        failure here would make that impossible to explain.
        """
        if not claims:
            self._count("record", "nothing_found")
            return "nothing_found"
        rows = [
            {
                "conversation_id": c.conversation_id, "kind": c.kind,
                "row_key": c.row_key, "surface": c.surface,
                "workspace": c.workspace, "last_tag_id": c.last_tag_id,
                "last_rendered_at": c.last_rendered_at,
            }
            for c in claims
        ]
        try:
            async with self._engine.begin() as conn:
                for row in rows:
                    stmt = pg.insert(RENDERED_CLAIM).values(row)
                    await conn.execute(stmt.on_conflict_do_update(
                        index_elements=["conversation_id", "kind", "row_key"],
                        set_={
                            "surface": stmt.excluded.surface,
                            "workspace": stmt.excluded.workspace,
                            "last_tag_id": stmt.excluded.last_tag_id,
                            "last_rendered_at": stmt.excluded.last_rendered_at,
                        },
                    ))
        except Exception:
            log.exception(
                "rendered-claim write failed for conversation %s",
                claims[0].conversation_id,
            )
            self._count("record", "unavailable")
            return "unavailable"
        self._count("record", "ok")
        return "ok"

    async def in_window(
        self, *, conversation_id: str, since_ms: int,
    ) -> LedgerRead:
        """What this conversation rendered at or after `since_ms`, newest
        first. The read recognition targets against.

        **Returns a status, not a bare tuple.** `()` with `ok` is «she rendered
        nothing about this»; `()` with `unavailable` is «I could not check».
        The first licenses ruling a dispute out and the second does not.
        """
        try:
            async with self._engine.connect() as conn:
                rows = (await conn.execute(
                    sa.select(RENDERED_CLAIM)
                    .where(
                        RENDERED_CLAIM.c.conversation_id == conversation_id,
                        RENDERED_CLAIM.c.last_rendered_at >= since_ms,
                    )
                    .order_by(RENDERED_CLAIM.c.last_rendered_at.desc())
                )).all()
        except Exception:
            log.exception(
                "rendered-claim read unavailable for conversation %s",
                conversation_id,
            )
            self._count("read", "unavailable")
            return LedgerRead(claims=(), status="unavailable")
        claims = tuple(
            RenderedClaim(
                conversation_id=r.conversation_id, kind=r.kind,
                row_key=r.row_key, surface=r.surface, workspace=r.workspace,
                last_tag_id=r.last_tag_id,
                last_rendered_at=r.last_rendered_at,
            )
            for r in rows
        )
        self._count("read", "ok" if claims else "nothing_found")
        return LedgerRead(claims=claims, status="ok" if claims else "nothing_found")

    async def prune_all(self) -> int:
        """`_PrunableStore`'s shape, so this joins the retention sweep that
        already exists instead of growing a loop of its own.

        31 §3.1 owes a trigger and a cadence — "a pass with no trigger
        definition is a polling bug in embryo" is the external review's own
        words about a different pass in this bundle — and the honest answer is
        that this one needs no new trigger at all: it is a working set on a
        horizon, which is exactly what that loop already does for `messages`.
        """
        return await self.prune(
            before_ms=int(time.time() * 1000) - 2 * self._window_ms,
        )

    async def prune(self, *, before_ms: int) -> int:
        """Drop everything last rendered before `before_ms`. Returns rows
        removed.

        A working set has to be bounded by something, and 31 §3.1 says which
        pass owns it and on what cadence rather than leaving it as "pruned
        somehow" — a pass with no trigger definition is a polling bug in
        embryo, which is the review's own words about a different pass in the
        same bundle.
        """
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.delete(RENDERED_CLAIM).where(
                    RENDERED_CLAIM.c.last_rendered_at < before_ms,
                )
            )
        removed = int(result.rowcount)
        if removed and self._metrics is not None:
            self._metrics.inc(
                "nora_rendered_claim_pruned_total", float(removed),
            )
        return removed

    def _count(self, verb: str, status: LedgerStatus) -> None:
        # RUNTIME check as well as mypy's: the vocabulary constant is what the
        # metric's help text is written from, and a value drifting from it is
        # the label-vocabulary failure CLAUDE.md names.
        if status not in LEDGER_STATUSES:
            raise ValueError(
                f"ledger status must be one of {sorted(LEDGER_STATUSES)}, "
                f"got {status!r}"
            )
        if self._metrics is not None:
            self._metrics.inc(
                "nora_rendered_claim_total", verb=verb, status=status,
            )
