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
from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore
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
    (`CircleSpec`'s own source).

    `former_ids_for` (Task 5's reverse lookup) folds in every RETIRED
    person_id that once resolved to this subject, so a renamed person's
    fixes stored under BOTH their former and current person_id are read as
    one subject. `subject_id_for` alone cannot do this: by `load_persons`'
    own fail-closed construction (a `former_person_ids` entry colliding
    with a live `[persons.<id>]` block raises `PersonsError`), a
    `Life360Membership.person_id` can only ever be a LIVE id — so calling
    `subject_id_for` on every membership here is always a no-op (it never
    sees a former id to resolve), and `former_ids_for` is the only way to
    recover the retired ids at all.

    Fail-closed (R-5), matching `nora.presence.composition
    ._circles_from_identity`'s own precedent over the identical source
    data: a life360 membership with no `circle_id` cannot be placed, and
    silently writing `""` into a NOT NULL `circle_id` column (`presence_stays`/
    `presence_derived_places` carry no CHECK constraint to catch it) would
    corrupt every row derived for that subject — refuse to start instead of
    guessing."""
    by_current: dict[str, list[str]] = {}
    circle_for: dict[str, str] = {}
    unplaced: list[str] = []
    for membership in persons.life360:
        subject = persons.subject_id_for(membership.person_id)
        by_current.setdefault(subject, []).append(membership.person_id)
        if membership.circle_id is None:
            unplaced.append(f"{membership.member_id} (person_id={membership.person_id!r})")
            continue
        circle_for[subject] = membership.circle_id
    if unplaced:
        raise AnalyticsStartupError(
            f"Life360 member id(s) {unplaced} have a [[persons.<id>.life360]] "
            "block with no circle_id in identity.toml — analytics cannot "
            "derive a circle_id to write stays/places under for their "
            "subject; add circle_id or remove the block (design/19 §3)"
        )
    return tuple(
        (
            subject,
            list(dict.fromkeys([*person_ids, *persons.former_ids_for(subject)])),
            circle_for[subject],
        )
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

    engine = create_sql_engine(database_url)
    store = SqlMobilityStore(engine, metrics=metrics)
    # Google-cache-only labelling (2026-08-19 follow-up, design/27 §3.5):
    # this process READS `geocode_cache` only — it never calls a geocoder
    # itself. REUSES `engine` for the same reason `nora.app.composition`
    # reuses its own (a second `create_sql_engine` forks the pool).
    geocode_cache = SqlGeocodeCacheStore(engine)
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
        anomaly_min_samples=analytics.anomaly_min_samples,
        anomaly_z_threshold=analytics.anomaly_z_threshold,
        corridor_max_edit_distance=analytics.corridor_max_edit_distance,
        corridor_min_samples=analytics.corridor_min_samples,
        min_trip_fixes=analytics.min_trip_fixes,
    )
    rollup = MobilityRollup(
        store=store, config=rollup_config, metrics=metrics, geocode_cache=geocode_cache,
    )
    log.info(
        "analytics rollup wired: %d subject(s), tick %.0fs",
        len(subjects), analytics.tick_seconds,
    )
    return AnalyticsDependencies(
        rollup=rollup, persons=persons, subjects=subjects, metrics=metrics,
        metrics_port=analytics.metrics_port, tick_seconds=analytics.tick_seconds,
    )
