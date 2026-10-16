"""`SqlRollupRead`: the Rollup read behind `rollup_read`.

Reads the CURRENT build only (`rollup_build.status = 'current'`, the one
pointer every reader resolves): a superseded build is history for eval, never
an answer. **It reads and never builds**: nothing here starts, advances or
switches a build, so a session that reads the Rollup cannot cause one.

**Withdrawal.** A hidden message never contributes to a row: the unit
rollups' inputs read `messages.hidden_at IS NULL` (`SqlRollupStore.
message_facts`) and every row is recomputed by the next analytics pass, which
diffs the row in place. So a message hidden after the last pass still counts
until the next one; `RollupRead.through_ms` and the tool's build stamp are
how a reader sees how fresh the rows are.

**Lineage is not applied here.** A row's lineage is returned with it and the
tool applies `nora.agent.lineage` for the calling room (the store never sees a
room). The unit boundary IS applied here: unit rollups are read for `unit`
only, and the person-scoped kinds (`stay`, `trip`) for the subjects given.

**No coordinate leaves this module** (R-28): a stay carries its place's words
(kind, label, whether the label is a saved name) and nothing else about where.

`__init__(self, engine)` and nothing else, the house shape for a store.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.lineage import Lineage
from nora.domain.rollup_read import RollupRead, RollupReadQuery, RollupReadRow
from nora.infra.sql.mobility import current_build_id
from nora.infra.sql.schema import (
    PRESENCE_DERIVED_PLACES,
    PRESENCE_STAYS,
    PRESENCE_TRIPS,
    ROLLUP_BUILD,
    ROLLUP_COLOCATION,
    ROLLUP_DAYBOOK,
    ROLLUP_OUTING,
    ROLLUP_TIE,
)

__all__ = ["SqlRollupRead"]


class SqlRollupRead:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def read(self, q: RollupReadQuery) -> RollupRead:
        if not q.subjects:
            # NEVER «all»: a unit row filter that is skipped when its subject
            # set is empty turns «nobody» into «everybody in the unit». The
            # caller names whom it means; an empty set is a bug there.
            raise ValueError("rollup_read needs at least one subject")
        async with self._engine.connect() as conn:
            build = (await conn.execute(
                sa.select(ROLLUP_BUILD.c.id, ROLLUP_BUILD.c.through_ms)
                .where(ROLLUP_BUILD.c.status == "current")
            )).first()
            if build is None:
                return RollupRead(rows=(), more=False, build_id=None, through_ms=None)
            rows = (await conn.execute(self._select(q).limit(q.limit + 1))).all()
        more = len(rows) > q.limit
        return RollupRead(
            rows=tuple(_row(q.kind, r) for r in rows[: q.limit]), more=more,
            build_id=int(build.id), through_ms=int(build.through_ms),
        )

    def _select(self, q: RollupReadQuery) -> sa.Select[Any]:
        if q.kind == "stay":
            return self._person_select(
                q, PRESENCE_STAYS, PRESENCE_STAYS.c.started_ts, PRESENCE_STAYS.c.ended_ts,
                extra=(
                    PRESENCE_DERIVED_PLACES.c.kind.label("place_kind"),
                    PRESENCE_DERIVED_PLACES.c.label.label("place_label"),
                    PRESENCE_DERIVED_PLACES.c.saved_place_id.label("place_saved_id"),
                ),
                join=PRESENCE_STAYS.outerjoin(
                    PRESENCE_DERIVED_PLACES,
                    PRESENCE_STAYS.c.place_id == PRESENCE_DERIVED_PLACES.c.id,
                ),
            )
        if q.kind == "trip":
            return self._person_select(
                q, PRESENCE_TRIPS, PRESENCE_TRIPS.c.started_ts, PRESENCE_TRIPS.c.ended_ts,
                extra=(PRESENCE_TRIPS.c.distance_m, PRESENCE_TRIPS.c.avg_speed_kmh),
            )
        table = {
            "colocation": ROLLUP_COLOCATION, "outing": ROLLUP_OUTING,
            "daybook": ROLLUP_DAYBOOK, "tie": ROLLUP_TIE,
        }.get(q.kind)
        if table is None:
            raise ValueError(f"not a Rollup kind rollup_read reads: {q.kind!r}")
        where = [
            table.c.build_id == current_build_id(), table.c.unit == q.unit,
            table.c.valid_from_ms < q.end_ms, table.c.valid_to_ms >= q.start_ms,
        ]
        where.append(_involves(q, table))
        return sa.select(table).where(*where).order_by(table.c.valid_from_ms, table.c.id)

    @staticmethod
    def _person_select(
        q: RollupReadQuery, table: sa.Table, start: Any, end: Any, *,
        extra: tuple[Any, ...] = (), join: Any = None,
    ) -> sa.Select[Any]:
        stmt = sa.select(
            table.c.subject_id, start.label("valid_from_ms"), end.label("valid_to_ms"),
            table.c.lineage, *extra,
        )
        if join is not None:
            stmt = stmt.select_from(join)
        return stmt.where(
            table.c.build_id == current_build_id(),
            table.c.subject_id.in_(q.subjects),
            start < q.end_ms, end >= q.start_ms,
        ).order_by(start, table.c.id)


def _involves(q: RollupReadQuery, table: sa.Table) -> Any:
    """The subjects a unit row must involve: any of them, or every one."""
    subjects = list(q.subjects)
    if q.kind == "daybook":
        return table.c.person_id.in_(subjects)
    if q.kind == "tie":
        def one(s: str) -> Any:
            return sa.or_(table.c.person_a == s, table.c.person_b == s)

        return sa.and_(*map(one, subjects)) if q.match == "all" else sa.or_(*map(one, subjects))
    persons = table.c.persons
    return persons.contains(subjects) if q.match == "all" else persons.overlap(subjects)


def _row(kind: str, r: Any) -> RollupReadRow:
    def make(subject: str, fields: dict[str, Any]) -> RollupReadRow:
        return RollupReadRow(
            kind=kind, subject=subject, valid_from_ms=int(r.valid_from_ms),
            valid_to_ms=int(r.valid_to_ms), lineage=Lineage.from_json(r.lineage),
            fields=fields,
        )

    if kind == "stay":
        return make(r.subject_id, {
            "place_kind": r.place_kind, "place_label": r.place_label,
            "place_saved": r.place_saved_id is not None,
        })
    if kind == "trip":
        return make(r.subject_id, {
            "distance_m": float(r.distance_m), "avg_speed_kmh": float(r.avg_speed_kmh),
        })
    if kind in ("colocation", "outing"):
        fields: dict[str, Any] = {
            "persons": list(r.persons), "place_label": r.place_label,
            "place_saved": r.saved_place_id is not None, "open": bool(r.open),
        }
        if kind == "colocation":
            fields |= {"at_home_of": list(r.at_home_of), "homes_unknown": list(r.homes_unknown)}
        return make(f"{r.unit}|{','.join(r.persons)}", fields)
    if kind == "daybook":
        return make(f"{r.unit}|{r.person_id}", {
            "person": r.person_id, "local_date": r.local_date, "stay_ms": int(r.stay_ms),
            "home_ms": int(r.home_ms), "colocation_ms": int(r.colocation_ms),
            "with_persons": list(r.with_persons), "outings": int(r.outings),
            "rooms": dict(r.rooms), "messages": int(r.messages), "reactions": int(r.reactions),
        })
    return make(f"{r.unit}|{r.person_a},{r.person_b}", {
        "iso_week": r.iso_week, "persons": [r.person_a, r.person_b],
        "replies": [int(r.replies_ab), int(r.replies_ba)],
        "mentions": [int(r.mentions_ab), int(r.mentions_ba)],
        "reactions": [int(r.reactions_ab), int(r.reactions_ba)],
        "colocation_ms": int(r.colocation_ms),
    })
