"""`nora assess run-nightly` (DEC-240).

Runs every deterministic check `nora.app.assess_dq` names
(`nora.domain.assess.DQ_CHECKS`, in order) against a read-only connection. No
check writes a tier: Assess reports and records metrics only. Each check runs
in isolation (`nora.app.assess_dq._isolated`): one check raising is recorded as
`status="error"` and never takes the rest of the run, or the report, down.

`--record` (default) writes one `assess_report` row
(`nora.infra.sql.assess.SqlAssessReportStore.record`, the full report,
including errored checks) and increments `nora_dq_failures_total{check}` once
per failing check and `nora_dq_errors_total{check}` once per errored check.
The counters are per check, never per row: the metric answers "is this check
red", the report's `failing` count answers "how red". Fail and error are
separate series because a check that never reached a verdict is not a check
that found a violation. `--no-record` prints without writing, the same dry-run
shape `nora memory rebuild --dry-run` uses.

Refused: the whole run, when `[assess] mode` is `"off"` (`nora.config.
AssessConfig`). The flag is not scoped per check; a partial run under a flag
that gates the whole adaptor would silently widen it.

The rollup rebuild diff is opt-in (`--rollup-diff`) and off by default. It
builds a second, unswitched Rollup build (`nora.analytics.runner.RollupRunner.
rebuild(switch=False)`), which costs real work on the deployment's history.
Without it, `rollup_rebuild_divergence` reports `not_applicable` for the run,
which is an honest state, not a failure."""

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
            elif r.status == "error":
                metrics.inc("nora_dq_errors_total", check=r.check)  # type: ignore[attr-defined]
    if record:
        await SqlAssessReportStore(engine).record(report)
    return NightlyReport(report=report, recorded=record)


def render(report: AssessReport) -> list[str]:
    lines = [f"assess nightly_dq @ {report.run_at_ms}"]
    for r in report.results:
        marker = {
            "ok": "OK", "fail": "FAIL", "not_applicable": "N/A", "error": "ERROR",
        }[r.status]
        line = f"  [{marker}] {r.check}"
        if r.status == "fail":
            line += f" ({r.failing}): {', '.join(r.sample)}"
        if r.detail:
            line += f" -- {r.detail}"
        lines.append(line)
    return lines


def _result_by_check(report: AssessReport, check: str) -> DqCheckResult | None:
    return next((r for r in report.results if r.check == check), None)
