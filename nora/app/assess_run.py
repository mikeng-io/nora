"""`nora assess run-nightly` (design/cognition-loop/30-cognition.md §6,
70-migration.md §2 row 3, DEC-240).

WHAT IT DOES. Runs every deterministic check `nora.app.assess_dq` names
(`nora.domain.assess.DQ_CHECKS`, in order), against a read-only connection —
no check here writes a tier, matching `15-map.md` §6's "Assess … reports and
metrics only". `--record` (default) writes one `assess_report` row
(`nora.infra.sql.assess.SqlAssessReportStore.record`) and increments
`nora_dq_failures_total{check}` once per FAILING check (never per row: the
metric answers "is this check red tonight", the report's `failing` count
answers "how red"). `--no-record` prints without writing, the same dry-run
shape `nora.app.events_backfill` and `nora.app.person_forget` use for a
command that touches state.

REFUSED: the whole run, when `[assess] mode` is `"off"` — the flag this
module's docstring promises (`nora.config.AssessConfig`). Not scoped
per-check: a partial run under a flag meant to gate the whole adaptor would
be the same silent widening `20-observe.md`'s brakes warn against.

THE ROLLUP REBUILD DIFF IS OPT-IN (`--rollup-diff`), off by default. Building
a second, unswitched Rollup build (`nora.analytics.runner.RollupRunner.
rebuild(switch=False)`) is real work — `25-rollup.md` §2's own caveat is that
its cost on the deployment's real history must be measured before it runs on
every tick, and a nightly cron is exactly the kind of "off the deploy path"
the design names as the fallback. Without it, `rollup_rebuild_divergence`
reports `not_applicable` for the run, which is the honest state, not a
failure."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from nora.app.assess_dq import run_deterministic_checks
from nora.domain.assess import AssessReport, DqCheckResult
from nora.infra.sql.assess import SqlAssessReportStore

log = logging.getLogger("nora.app.assess_run")


class AssessDisabledError(Exception):
    """`[assess] mode` is `"off"`. Refused, not a silent no-op — an operator
    invoking the command wants to know their config did not enable it."""


@dataclass
class NightlyReport:
    report: AssessReport
    recorded: bool


async def run_nightly(
    engine: AsyncEngine, *, mode: str, record: bool = True,
    rollup_builds: tuple[int, int] | None = None,
    rollup_window_ms: tuple[int, int] = (0, 0),
    metrics: object | None = None,
) -> NightlyReport:
    if mode != "on":
        raise AssessDisabledError('[assess] mode is "off"; set it to "on" first')
    run_at_ms = int(time.time() * 1000)
    async with engine.connect() as conn:
        results = await run_deterministic_checks(
            conn, rollup_builds=rollup_builds, rollup_window_ms=rollup_window_ms,
        )
    report = AssessReport(kind="nightly_dq", run_at_ms=run_at_ms, results=results)
    if metrics is not None:
        for r in results:
            if r.status == "fail":
                metrics.inc("nora_dq_failures_total", check=r.check)  # type: ignore[attr-defined]
    if record:
        await SqlAssessReportStore(engine).record(report)
    return NightlyReport(report=report, recorded=record)


def render(report: AssessReport) -> list[str]:
    lines = [f"assess nightly_dq @ {report.run_at_ms}"]
    for r in report.results:
        marker = {"ok": "OK", "fail": "FAIL", "not_applicable": "N/A"}[r.status]
        line = f"  [{marker}] {r.check}"
        if r.status == "fail":
            line += f" ({r.failing}): {', '.join(r.sample)}"
        if r.detail:
            line += f" -- {r.detail}"
        lines.append(line)
    return lines


def _result_by_check(report: AssessReport, check: str) -> DqCheckResult | None:
    return next((r for r in report.results if r.check == check), None)
