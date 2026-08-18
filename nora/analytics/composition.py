"""The analytics rollup's composition root — mirrors
`nora.presence.composition` exactly: a SECOND top-level composition root
because `nora.analytics` is a second process, and the ONE place it reads
`Config` (this module, plus `nora.analytics.__main__`, which passes `cfg`
straight through).

`build_rollup` returns `None` when disabled (DEC-115's own inert-by-default
posture) — a process an operator started before turning the feature on
should stop cleanly, exactly `nora.presence.composition`'s own docstring.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from nora.analytics.rollup import MobilityRollup, RollupConfig
from nora.config import Config
from nora.infra.sql import create_sql_engine, run_migrations
from nora.infra.sql.mobility import SqlMobilityStore
from nora.obs.metrics import Metrics
from nora.persons import Persons, PersonsError, load_persons

log = logging.getLogger("nora.analytics.composition")


class AnalyticsStartupError(Exception):
    """A fatal analytics-rollup startup failure — refuse to start with a
    readable message, the same shape `PresenceStartupError` uses."""


@dataclass(frozen=True)
class AnalyticsDependencies:
    rollup: MobilityRollup | None
    persons: Persons | None
    subjects: tuple[tuple[str, list[str], str], ...]  # (subject_id, person_ids, circle_id)
    metrics: Metrics
    metrics_port: int
    tick_seconds: float


def _subjects_from_identity(persons: Persons) -> tuple[tuple[str, list[str], str], ...]:
    """Every (subject_id, [observed person_ids], circle_id) this deployment
    should derive for — built from identity.toml's life360 memberships
    (`CircleSpec`'s own source), resolved through `subject_id_for` (Task 5)
    so a renamed person's fixes under BOTH their former and current
    person_id are read as one subject."""
    by_current: dict[str, list[str]] = {}
    circle_for: dict[str, str] = {}
    for membership in persons.life360:
        subject = persons.subject_id_for(membership.person_id)
        by_current.setdefault(subject, []).append(membership.person_id)
        if membership.circle_id is not None:
            circle_for[subject] = membership.circle_id
    return tuple(
        (subject, person_ids, circle_for.get(subject, ""))
        for subject, person_ids in by_current.items()
    )


def build_rollup(cfg: Config) -> AnalyticsDependencies:
    metrics = Metrics()
    analytics = cfg.analytics
    if not analytics.enabled:
        log.info("analytics is disabled (analytics.enabled = false) — rollup inert")
        return AnalyticsDependencies(
            rollup=None, persons=None, subjects=(), metrics=metrics,
            metrics_port=analytics.metrics_port, tick_seconds=analytics.tick_seconds,
        )

    try:
        persons = load_persons(cfg.identity_file)
    except PersonsError as exc:
        raise AnalyticsStartupError(f"identity.toml error: {exc}") from exc
    subjects = _subjects_from_identity(persons)
    if not subjects:
        raise AnalyticsStartupError(
            "analytics.enabled is true but no [[persons.<id>.life360]] "
            "block exists in identity.toml — there is nothing to derive; "
            "refusing to start"
        )

    database_url = os.environ.get(cfg.store.database_url_env, "")
    if not database_url:
        raise AnalyticsStartupError(
            f"environment variable {cfg.store.database_url_env} is not set — "
            "refusing to start"
        )
    try:
        # Idempotent, like the collector's own call — this process may start
        # before or after Nora/presence.
        run_migrations(database_url)
    except Exception as exc:
        raise AnalyticsStartupError(f"migration error: {exc}") from exc

    store = SqlMobilityStore(create_sql_engine(database_url), metrics=metrics)
    rollup_config = RollupConfig(
        tick_seconds=analytics.tick_seconds, safety_lag=analytics.safety_lag,
        settled_seconds=analytics.settled_seconds,
        max_age_seconds=analytics.max_age_seconds,
        batch_limit=analytics.batch_limit,
        backfill_batch_limit=analytics.backfill_batch_limit,
        backfill_threshold=analytics.backfill_threshold,
        grace_seconds=analytics.grace_seconds,
        stay_radius_m=analytics.stay_radius_m,
        stay_min_dwell_seconds=analytics.stay_min_dwell_seconds,
        cluster_eps_m=analytics.cluster_eps_m,
        cluster_min_samples=analytics.cluster_min_samples,
        min_nights=analytics.min_nights, min_weekdays=analytics.min_weekdays,
        label_freshness_days=analytics.label_freshness_days,
        tz_name=analytics.tz_name,
    )
    rollup = MobilityRollup(store=store, config=rollup_config, metrics=metrics)
    log.info(
        "analytics rollup wired: %d subject(s), tick %.0fs",
        len(subjects), analytics.tick_seconds,
    )
    return AnalyticsDependencies(
        rollup=rollup, persons=persons, subjects=subjects, metrics=metrics,
        metrics_port=analytics.metrics_port, tick_seconds=analytics.tick_seconds,
    )
