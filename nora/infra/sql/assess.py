"""`SqlAssessReportStore` — the Assess adaptor's one write, and the operator's
one read (migration `0077_assess_report`).

Two methods only: `record` (append one run's report) and `recent` (the
operator's own read, newest first). Nothing here is on a turn's path — no
prompt block, no read tool reaches this store (reports and metrics only;
`tests/unit/test_assess_reports_are_operator_only.py` pins it).

`__init__(self, engine: AsyncEngine)`, as `SqlCultureStore`: no config, no
clock, no metrics threaded through the constructor.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.domain.assess import AssessReport, DqCheckResult
from nora.infra.sql.schema import ASSESS_REPORT

__all__ = ["SqlAssessReportStore"]


def _row_to_report(row: sa.Row[Any]) -> AssessReport:
    return AssessReport(
        kind=row.kind,
        run_at_ms=row.run_at_ms,
        results=tuple(
            DqCheckResult(
                check=r["check"], status=r["status"], failing=r.get("failing", 0),
                sample=tuple(r.get("sample", ())), detail=r.get("detail", ""),
            )
            for r in row.results
        ),
    )


class SqlAssessReportStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def record(self, report: AssessReport) -> None:
        results = [
            {
                "check": r.check, "status": r.status, "failing": r.failing,
                "sample": list(r.sample), "detail": r.detail,
            }
            for r in report.results
        ]
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(ASSESS_REPORT).values(
                    kind=report.kind, run_at_ms=report.run_at_ms,
                    results=json.loads(json.dumps(results)),
                )
            )

    async def recent(
        self, *, kind: str = "nightly_dq", limit: int = 20,
    ) -> list[AssessReport]:
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                sa.select(ASSESS_REPORT)
                .where(ASSESS_REPORT.c.kind == kind)
                .order_by(ASSESS_REPORT.c.run_at_ms.desc())
                .limit(limit)
            )
            return [_row_to_report(r) for r in rows]


# A plain report -> dict view, for the CLI's printer.
def report_as_dict(report: AssessReport) -> dict[str, Any]:
    return asdict(report)
