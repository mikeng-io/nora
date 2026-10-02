"""`nora cognition health`'s report and its verdict (the Cognition cut-over's
safety net, design/cognition-loop/70-migration.md §7).

The question: is the cognition loop healthy RIGHT NOW, answered from durable rows
and not from a Prometheus series that every restart resets (nora restarts 2 to 24
times a day). Pure: no store, no clock. `nora.app.cognition_health` reads the
rows and hands them in; the CLI prints the report and exits with `exit_code`.

**Counts, ages and rule names only.** Nothing here carries a row's text, a person,
a room id or a stimulus ref: a session's `detail` is reduced to a flag before it
arrives, and a refusal is its rule NAME (a code constant). A unit is the operator's
own policy.toml key, the same vocabulary `nora cognition health --unit` takes.

**Three verdicts, never two** (AGENTS.md: an ERROR is never a PASS):

- `PASS`   (exit 0): every threshold held and there was something to judge.
- `FAIL`   (exit 1): a threshold was breached. The finding says which.
- `ERROR`  (exit 2): the loop could not be judged: a read failed, or no session ran
  and nothing waits, so zero sessions is not «healthy», it is «unknown». A FAIL
  outranks an ERROR: the actionable fact is reported first.

The thresholds are module constants, each with the number it came from.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

#: `StimulusBacklog` (deploy/prometheus/rules/nora.yml) fires on the same number:
#: a stimulus that could wake a unit and has waited four hours is a loop that is not
#: consuming. The wake table's own `max_age_seconds` is 3,600 and the failure
#: backoff's cap 21,600, so four hours is past anything the brakes explain on a
#: first failure and short of a unit legitimately backing off its sixth.
STALLED_PENDING_SECONDS = 14_400.0

#: CUTOVER-PLAN §5.1's stop sign, "sessions failed or timeout more than 10%", read
#: over sessions that ENDED (closed, failed, timeout). A cancelled one is a restart,
#: not a verdict on the session. With fewer than `FAILURE_RATIO_MIN_ENDED` ended a
#: ratio says nothing (one failure in four is 25%), so it is reported and not judged.
FAILURE_RATIO = 0.10
FAILURE_RATIO_MIN_ENDED = 5

Level = Literal["PASS", "FAIL", "ERROR"]
EXIT_CODES: Mapping[str, int] = {"PASS": 0, "FAIL": 1, "ERROR": 2}


@dataclass(frozen=True)
class SessionCount:
    """Sessions that started on `day` (the cognition zone's calendar day), by how
    they ended. `outcome` is `closed`, `failed`, `timeout`, `cancelled`, or `open`
    (no outcome row yet). `cause` is `infra` / `agenda` for a failure, `restart` or
    `other` for a cancel (`restart` is a cancelled outcome whose detail says so),
    `none` otherwise."""

    day: str
    unit: str
    outcome: str
    cause: str
    count: int


@dataclass(frozen=True)
class UnitState:
    """One unit's inbox and its last wake, as the evaluator's own read sees them
    (`SqlStimulusStore.view`, so a watermark, a carry and a retraction are applied
    by the one rule). `eligible` are the pending stimuli that could wake the unit
    (`waits` False); `waiting` ride the next wake and never start one. Ages are in
    seconds at `HealthReport.now_ms`."""

    unit: str
    eligible: int
    waiting: int
    oldest_eligible_age_seconds: float | None
    by_kind: Mapping[str, int] = field(default_factory=dict)
    #: The read hit its bound, so `eligible + waiting` is a floor, not a count.
    truncated: bool = False
    last_wake_age_seconds: float | None = None
    #: Consecutive failed wakes the backoff is counting (`UnitView.failures`).
    failure_streak: int = 0


@dataclass(frozen=True)
class TokenStats:
    """What the sessions in the window spent, from the outcome rows
    (`cognition_wake_outcome`), mean and max PER SESSION. A session whose runner
    could not read its usage (a wake that ended before a model call, a restart,
    a provider that reported none) is `without_usage`, never a zero in the mean."""

    with_usage: int
    without_usage: int
    input_mean: float | None = None
    input_max: int | None = None
    output_mean: float | None = None
    output_max: int | None = None
    cache_read_mean: float | None = None
    cache_read_max: int | None = None
    #: Sum of cache-read over sum of input across the sessions that reported both: the
    #: share of what was read that the provider served from its prompt cache. The
    #: provider's own counting decides whether cache-read is part of input.
    cache_share: float | None = None


@dataclass(frozen=True)
class HealthReport:
    since_ms: int
    until_ms: int
    now_ms: int
    unit: str | None
    sessions: tuple[SessionCount, ...] = ()
    units: tuple[UnitState, ...] = ()
    #: Tracked wakes with no outcome row (a session in flight, or one that was killed),
    #: split at `[cognition] session_timeout_seconds`. An EXPIRED open wake has no
    #: session behind it: its process died and nothing recorded it (a boot reclaim writes
    #: those, so one that persists is a defect).
    open_wakes: int = 0
    expired_open_wakes: int = 0
    tokens: TokenStats | None = None
    process_starts: int = 0
    distinct_builds: int = 0
    #: `(section, exception class name)` for every read that failed. Never a message:
    #: a database error can quote a row.
    errors: tuple[tuple[str, str], ...] = ()

    @property
    def total_sessions(self) -> int:
        return sum(c.count for c in self.sessions)

    def sessions_with(self, *outcomes: str) -> int:
        return sum(c.count for c in self.sessions if c.outcome in outcomes)

    @property
    def eligible_pending(self) -> int:
        return sum(u.eligible for u in self.units)

    @property
    def oldest_eligible_age_seconds(self) -> float | None:
        ages = [u.oldest_eligible_age_seconds for u in self.units
                if u.oldest_eligible_age_seconds is not None]
        return max(ages) if ages else None


@dataclass(frozen=True)
class Finding:
    level: Literal["FAIL", "ERROR"]
    code: str
    text: str


def judge(report: HealthReport) -> tuple[Level, tuple[Finding, ...]]:
    """The verdict and every finding behind it. Order is stable."""
    findings: list[Finding] = []
    for section, error in report.errors:
        findings.append(Finding(
            "ERROR", "unreadable",
            f"{section} could not be read ({error}); not judged, not zero",
        ))
    # An absent read is not a quiet one: with the unit state unread there is no
    # «nothing pending» to compare a session count against.
    units_read = not any(section == "pending" for section, _ in report.errors)
    sessions_read = not any(section == "sessions" for section, _ in report.errors)
    if sessions_read and units_read and report.total_sessions == 0:
        if report.eligible_pending > 0:
            findings.append(Finding(
                "FAIL", "no_sessions_with_pending",
                f"no session ran in the window while {report.eligible_pending} "
                "stimulus(es) wait to wake a unit",
            ))
        else:
            findings.append(Finding(
                "ERROR", "no_sessions",
                "no session ran in the window and nothing is pending: there is "
                "nothing to judge, and zero sessions is not a clean loop",
            ))
    oldest = report.oldest_eligible_age_seconds
    if oldest is not None and oldest > STALLED_PENDING_SECONDS:
        stalled = [u.unit for u in report.units
                   if (u.oldest_eligible_age_seconds or 0.0) > STALLED_PENDING_SECONDS]
        findings.append(Finding(
            "FAIL", "stalled",
            f"the oldest waiting stimulus is {oldest / 3600:.1f} h old (limit "
            f"{STALLED_PENDING_SECONDS / 3600:.0f} h), unit(s): {', '.join(sorted(stalled))}",
        ))
    ended = report.sessions_with("closed", "failed", "timeout")
    broke = report.sessions_with("failed", "timeout")
    if ended >= FAILURE_RATIO_MIN_ENDED and broke / ended > FAILURE_RATIO:
        findings.append(Finding(
            "FAIL", "failure_ratio",
            f"{broke} of {ended} ended sessions failed or timed out "
            f"({broke / ended:.0%}, limit {FAILURE_RATIO:.0%})",
        ))
    if report.expired_open_wakes > 0:
        findings.append(Finding(
            "FAIL", "expired_open_wake",
            f"{report.expired_open_wakes} wake(s) have no outcome and are past the session "
            "timeout: a session that was killed and recorded nothing, still holding its unit",
        ))
    if any(f.level == "FAIL" for f in findings):
        return "FAIL", tuple(findings)
    if findings:
        return "ERROR", tuple(findings)
    return "PASS", ()


def exit_code(level: Level) -> int:
    return EXIT_CODES[level]


def _age(seconds: float | None) -> str:
    if seconds is None:
        return "none"
    if seconds < 120:
        return f"{seconds:.0f}s"
    if seconds < 7200:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"


def _num(value: float | int | None) -> str:
    return "unread" if value is None else f"{value:,.0f}"


def render_text(report: HealthReport, *, iso: Mapping[str, str]) -> list[str]:
    """The report as lines. `iso` carries the pre-formatted window (`since`,
    `until`) so this module holds no clock or zone."""
    level, findings = judge(report)
    lines = [
        f"cognition health {iso['since']} .. {iso['until']}"
        + (f"  unit={report.unit}" if report.unit else ""),
        f"  verdict: {level}",
    ]
    for f in findings:
        lines.append(f"    {f.level} {f.code}: {f.text}")
    lines.append("  sessions by day and outcome (cause):")
    if not report.sessions:
        lines.append("    none in the window")
    for c in sorted(report.sessions, key=lambda c: (c.day, c.unit, c.outcome, c.cause)):
        why = "" if c.cause == "none" else f" ({c.cause})"
        lines.append(f"    {c.day}  {c.unit}  {c.outcome}{why}: {c.count}")
    lines.append(
        f"  wakes with no outcome: {report.open_wakes} open, "
        f"{report.expired_open_wakes} past the session timeout",
    )
    lines.append("  pending stimuli per unit (could wake / waiting; oldest could-wake):")
    if not report.units:
        lines.append("    no unit has a stimulus")
    for u in sorted(report.units, key=lambda u: u.unit):
        kinds = ", ".join(f"{k}={n}" for k, n in sorted(u.by_kind.items())) or "-"
        floor = "+" if u.truncated else ""
        lines.append(
            f"    {u.unit}: {u.eligible}{floor} / {u.waiting}"
            f"  oldest {_age(u.oldest_eligible_age_seconds)}"
            f"  kinds {kinds}  last wake {_age(u.last_wake_age_seconds)} ago"
            f"  failure streak {u.failure_streak}",
        )
    lines.append(
        "  last wake TICK is a gauge, not a row: nora_cognition_wake_last_tick_seconds "
        "(CognitionWakeStalled); the line above is the last wake RECORDED",
    )
    t = report.tokens
    if t is None:
        lines.append("  tokens per session: not read")
    else:
        lines.append(
            f"  tokens per session ({t.with_usage} with usage, {t.without_usage} unread): "
            f"input mean {_num(t.input_mean)} max {_num(t.input_max)}; "
            f"output mean {_num(t.output_mean)} max {_num(t.output_max)}; "
            f"cache-read mean {_num(t.cache_read_mean)} max {_num(t.cache_read_max)}"
            + ("" if t.cache_share is None else f"; cache share {t.cache_share:.0%}"),
        )
    lines.append(
        f"  process starts in the window: {report.process_starts} "
        f"({report.distinct_builds} distinct build(s))",
    )
    return lines


def report_json(report: HealthReport, *, iso: Mapping[str, str]) -> dict[str, object]:
    level, findings = judge(report)
    return {
        "schema": 1,
        "verdict": level,
        "exit_code": exit_code(level),
        "findings": [{"level": f.level, "code": f.code, "text": f.text} for f in findings],
        "window": {"since": iso["since"], "until": iso["until"],
                   "since_ms": report.since_ms, "until_ms": report.until_ms},
        "unit": report.unit,
        "sessions": [
            {"day": c.day, "unit": c.unit, "outcome": c.outcome, "cause": c.cause,
             "count": c.count} for c in report.sessions
        ],
        "open_wakes": report.open_wakes,
        "expired_open_wakes": report.expired_open_wakes,
        "units": [
            {"unit": u.unit, "eligible": u.eligible, "waiting": u.waiting,
             "oldest_eligible_age_seconds": u.oldest_eligible_age_seconds,
             "by_kind": dict(u.by_kind), "truncated": u.truncated,
             "last_wake_age_seconds": u.last_wake_age_seconds,
             "failure_streak": u.failure_streak} for u in report.units
        ],
        "tokens": None if report.tokens is None else {
            "with_usage": report.tokens.with_usage,
            "without_usage": report.tokens.without_usage,
            "input_mean": report.tokens.input_mean, "input_max": report.tokens.input_max,
            "output_mean": report.tokens.output_mean, "output_max": report.tokens.output_max,
            "cache_read_mean": report.tokens.cache_read_mean,
            "cache_read_max": report.tokens.cache_read_max,
            "cache_share": report.tokens.cache_share,
        },
        "process_starts": report.process_starts,
        "distinct_builds": report.distinct_builds,
        "errors": [{"section": s, "error": e} for s, e in report.errors],
    }
