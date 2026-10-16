"""The analytics rollup's composition root — mirrors
`nora.presence.composition` exactly: a SECOND top-level composition root
because `nora.analytics` is a second process, and the ONE place it reads
`Config` (this module, plus `nora.analytics.__main__`, which passes `cfg`
straight through).

`build_rollup` returns `None` when disabled (DEC-115's inert-by-default
posture) — a process an operator started before turning the feature on
should stop cleanly, as in `nora.presence.composition`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from nora.analytics.rollup import RollupConfig
from nora.analytics.runner import RollupRunner, Subject
from nora.analytics.units import PersonScope, UnitConfig, UnitRollups
from nora.config import Config
from nora.infra.sql import create_sql_engine, run_migrations
from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.rollups import SqlRollupStore, UnitRooms
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.obs.metrics import Metrics
from nora.persons import Persons, PersonsError, load_persons
from nora.policy import Policy, PolicyError, load_policy

log = logging.getLogger("nora.analytics.composition")


class AnalyticsStartupError(Exception):
    """A fatal analytics-rollup startup failure — refuse to start with a
    readable message, the same shape `PresenceStartupError` uses."""


@dataclass(frozen=True)
class AnalyticsDependencies:
    runner: RollupRunner | None
    persons: Persons | None
    subjects: tuple[Subject, ...]
    metrics: Metrics
    metrics_port: int
    tick_seconds: float
    #: How far behind the wall clock a run's watermark sits, so a row whose
    #: transaction committed a moment late is still inside it.
    grace_seconds: float = 60.0
    units: tuple[UnitRooms, ...] = ()


def _subjects_from_identity(persons: Persons) -> tuple[Subject, ...]:
    """Every subject this deployment should derive for, with every person id
    its fixes were recorded under and every circle it is polled in — built
    from identity.toml's life360 memberships (`CircleSpec`'s own source).

    `former_ids_for` folds in every RETIRED
    person_id that once resolved to this subject, so a renamed person's
    fixes stored under BOTH their former and current person_id are read as
    one subject. `subject_id_for` alone cannot do this: by `load_persons`'
    own fail-closed construction (a `former_person_ids` entry colliding
    with a live `[persons.<id>]` block raises `PersonsError`), a
    `Life360Membership.person_id` can only ever be a LIVE id — so calling
    `subject_id_for` on every membership here is always a no-op (it never
    sees a former id to resolve), and `former_ids_for` is the only way to
    recover the retired ids at all.

    A person in two circles is polled in both; the fold reads the polls of
    every one of them as its clock (`circle_ids`), and writes its rows under
    the first.

    Fail-closed (R-5), matching `nora.presence.composition
    ._circles_from_identity`'s own precedent over the identical source
    data: a life360 membership with no `circle_id` cannot be placed, and
    silently writing `""` into a NOT NULL `circle_id` column would corrupt
    every row derived for that subject — refuse to start instead of
    guessing."""
    by_current: dict[str, list[str]] = {}
    circles_for: dict[str, list[str]] = {}
    unplaced: list[str] = []
    for membership in persons.life360:
        subject = persons.subject_id_for(membership.person_id)
        by_current.setdefault(subject, []).append(membership.person_id)
        if membership.circle_id is None:
            unplaced.append(f"{membership.member_id} (person_id={membership.person_id!r})")
            continue
        circles_for.setdefault(subject, [])
        if membership.circle_id not in circles_for[subject]:
            circles_for[subject].append(membership.circle_id)
    if unplaced:
        raise AnalyticsStartupError(
            f"Life360 member id(s) {unplaced} have a [[persons.<id>.life360]] "
            "block with no circle_id in identity.toml — analytics cannot "
            "derive a circle_id to write stays/places under for their "
            "subject; add circle_id or remove the block (design/19 §3)"
        )
    return tuple(
        Subject(
            subject_id=subject,
            person_ids=tuple(dict.fromkeys([*person_ids, *persons.former_ids_for(subject)])),
            circle_ids=tuple(circles_for[subject]),
        )
        for subject, person_ids in by_current.items()
    )


def _units_from_policy(policy: Policy, persons: Persons) -> tuple[UnitRooms, ...]:
    """Each unit's rooms (every id form a room is matched under) and members
    (the persons its rooms LIST, by subject; a room open to everyone lists
    nobody, so it adds no member — a unit's membership is never guessed),
    and how a platform account resolves to a member for mentions."""
    rooms: dict[str, list[tuple[str, str]]] = {}
    members: dict[str, set[str]] = {}
    for convo in policy.conversations:
        rooms.setdefault(convo.unit, []).extend(
            (convo.platform, cid) for cid in sorted(convo.ids)
        )
        members.setdefault(convo.unit, set()).update(
            persons.subject_id_for(p) for p in (convo.persons or ())
        )
    out: list[UnitRooms] = []
    for unit in sorted(rooms):
        platforms = sorted({p for p, _ in rooms[unit]})
        accounts = {
            (platform, account): subject
            for subject in sorted(members[unit])
            for platform in platforms
            for account in sorted(persons.platform_ids(platform, subject))
        }
        out.append(UnitRooms(
            unit=unit, rooms=tuple(rooms[unit]), members=tuple(sorted(members[unit])),
            accounts=accounts,
        ))
    return tuple(out)


def build_rollup(cfg: Config) -> AnalyticsDependencies:
    metrics = Metrics()
    analytics = cfg.analytics
    if not analytics.enabled:
        log.info("analytics is disabled (analytics.enabled = false) — rollup inert")
        return AnalyticsDependencies(
            runner=None, persons=None, subjects=(), metrics=metrics,
            metrics_port=analytics.metrics_port, tick_seconds=analytics.tick_seconds,
            grace_seconds=analytics.grace_seconds,
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
    # The unit rollups need the rooms and their units (policy.toml). Without
    # it the presence Rollup still runs and the unit rollups compute nothing:
    # a unit's membership is never guessed.
    try:
        units = _units_from_policy(load_policy(cfg.policy_file), persons)
    except PolicyError as exc:
        log.warning("policy.toml unreadable (%s) — the unit rollups compute nothing", exc)
        units = ()

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
    mobility = SqlMobilityStore(engine, metrics=metrics)
    # Google-cache-only labelling: this process READS `geocode_cache` only —
    # it never calls a geocoder itself. REUSES `engine` for the same reason
    # `nora.app.composition` reuses its own (a second `create_sql_engine`
    # forks the pool).
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
        place_match_radius_m=analytics.place_match_radius_m,
        stay_wander_radius_m=analytics.stay_wander_radius_m,
        tz_name=analytics.tz_name,
        anomaly_min_samples=analytics.anomaly_min_samples,
        anomaly_z_threshold=analytics.anomaly_z_threshold,
        corridor_max_edit_distance=analytics.corridor_max_edit_distance,
        corridor_min_samples=analytics.corridor_min_samples,
        corridor_cluster_batch_limit=analytics.corridor_cluster_batch_limit,
        min_trip_fixes=analytics.min_trip_fixes,
        max_trip_duration_seconds=analytics.max_trip_duration_seconds,
    )
    unit_config = UnitConfig(
        colocation_radius_m=analytics.colocation_radius_m,
        colocation_min_overlap_seconds=analytics.colocation_min_overlap_seconds,
        response_window_seconds=analytics.response_window_seconds,
        follow_window_seconds=analytics.follow_window_seconds,
        follow_names=tuple(analytics.follow_names),
        stimulus_horizon_seconds=analytics.stimulus_horizon_seconds,
        deviation_silence_days=analytics.deviation_silence_days,
        deviation_stimuli_per_person_per_day=analytics.deviation_stimuli_per_person_per_day,
        health_max_poll_gap_seconds=analytics.health_max_poll_gap_seconds,
        tz_name=analytics.tz_name,
    )
    rollups = SqlRollupStore(engine, metrics=metrics)
    people = {
        s.subject_id: PersonScope(
            subject=s.subject_id, circles=s.circle_ids,
            former_ids=tuple(p for p in s.person_ids if p != s.subject_id),
        )
        for s in subjects
    }
    unit_rollups = UnitRollups(
        store=rollups, config=unit_config, units=units, people=people,
        stimuli=SqlStimulusStore(engine), metrics=metrics,
    )
    runner = RollupRunner(
        mobility=mobility, rollups=rollups, config=rollup_config,
        unit_rollups=unit_rollups, subjects=subjects, unit_config=unit_config,
        metrics=metrics, geocode_cache=geocode_cache,
    )
    log.info(
        "analytics rollup wired: %d subject(s), %d unit(s), tick %.0fs",
        len(subjects), len(units), analytics.tick_seconds,
    )
    return AnalyticsDependencies(
        runner=runner, persons=persons, subjects=subjects, metrics=metrics,
        metrics_port=analytics.metrics_port, tick_seconds=analytics.tick_seconds,
        grace_seconds=analytics.grace_seconds, units=units,
    )
