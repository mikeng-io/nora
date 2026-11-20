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
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace

from aiohttp import ClientSession
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.access import IdentityAccess
from nora.analytics.identity_inputs import ManagedAnalyticsInputs
from nora.analytics.rollup import RollupConfig
from nora.analytics.runner import RollupRunner, Subject
from nora.analytics.units import PersonScope, UnitConfig, UnitRollups
from nora.config import Config
from nora.config_reload import FileReload, LivePolicy, ReloadFiles, RestartRequiredError
from nora.domain.live import LiveMapping, LiveSequence
from nora.domain.managed import Verdict
from nora.domain.management import Scope, ScopeKind
from nora.infra.sql import create_sql_engine, run_migrations
from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.rollups import SqlRollupStore, UnitRooms
from nora.infra.sql.social_stream import readable_social_store
from nora.infra.sql.stimuli import SqlStimulusStore
from nora.obs.metrics import Metrics
from nora.persons import Persons, PersonsError
from nora.policy import Policy, PolicyError, load_policy

log = logging.getLogger("nora.analytics.composition")


class AnalyticsStartupError(Exception):
    """A fatal analytics-rollup startup failure — refuse to start with a
    readable message, the same shape `PresenceStartupError` uses."""


@dataclass(frozen=True)
class AnalyticsDependencies:
    runner: RollupRunner | None
    persons: Persons | None
    subjects: Sequence[Subject]
    metrics: Metrics
    metrics_port: int
    tick_seconds: float
    #: How far behind the wall clock a run's watermark sits, so a row whose
    #: transaction committed a moment late is still inside it.
    grace_seconds: float = 60.0
    units: Sequence[UnitRooms] = ()
    reload_files: ReloadFiles | FileReload[Policy] | None = None
    refresh_identity: Callable[[], Awaitable[None]] | None = None
    engine: AsyncEngine | None = None


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
            "subject; add circle_id or remove the block (DEC-54)"
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
    # ponytail: this process runs no Discord client — the policy loads with
    # `_NoChannels`, so `rooms()` could only ever return the static rooms and
    # a guild entry's unit and `persons` would vanish; `conversations` is the
    # source until a shared channel source exists.
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


def _file_identity(cfg: Config, metrics: Metrics) -> tuple[Persons, Policy, ReloadFiles]:
    def validate_persons(persons: Persons) -> None:
        if not _subjects_from_identity(persons):
            raise AnalyticsStartupError(
                "analytics enabled but identity has no Life360 subjects")

    try:
        reload_files = ReloadFiles(
            policy_path=cfg.policy_file, identity_path=cfg.identity_file,
            ai_peer=cfg.honcho.ai_peer, default_workspace=cfg.honcho.workspace_id,
            metrics=metrics, validate_persons=validate_persons,
        )
    except (PersonsError, PolicyError, ValueError) as exc:
        raise AnalyticsStartupError(f"identity/policy error: {exc}") from exc
    persons, policy = reload_files.persons, reload_files.policy
    return persons, policy, reload_files


def build_rollup(
    cfg: Config, *, access: IdentityAccess | None = None,
    policy: Policy | None = None, engine: AsyncEngine | None = None,
) -> AnalyticsDependencies:
    metrics = Metrics()
    analytics = cfg.analytics
    if not analytics.enabled:
        log.info("analytics is disabled (analytics.enabled = false) — rollup inert")
        return AnalyticsDependencies(
            runner=None, persons=None, subjects=(), metrics=metrics,
            metrics_port=analytics.metrics_port, tick_seconds=analytics.tick_seconds,
            grace_seconds=analytics.grace_seconds,
        )

    inputs = None
    persons: Persons | None = None
    reload_files: ReloadFiles | FileReload[Policy] | None = None
    subjects: Sequence[Subject] = ()
    units: Sequence[UnitRooms] = ()
    if cfg.identity.backend == "managed":
        if access is None or access.is_file_backend or policy is None or engine is None:
            raise AnalyticsStartupError("managed analytics authority is unavailable")
        inputs = ManagedAnalyticsInputs(access, policy, PresenceStore(engine))
        subjects = LiveSequence(lambda: inputs.current.subjects)
        units = LiveSequence(lambda: inputs.current.units)
    else:
        persons, policy, reload_files = _file_identity(cfg, metrics)
        subjects = LiveSequence(lambda: _subjects_from_identity(persons))
        units = LiveSequence(lambda: _units_from_policy(policy, persons))

    database_url = cfg.credentials.get(cfg.store.database_url_env, "")
    if not database_url:
        raise AnalyticsStartupError(
            f"environment variable {cfg.store.database_url_env} is not set — "
            "refusing to start"
        )
    if engine is None:
        try:
            # Idempotent, like the collector's own call — this process may start
            # before or after Nora/presence.
            run_migrations(database_url)
        except Exception as exc:
            raise AnalyticsStartupError(f"migration error: {exc}") from exc

    engine = engine or create_sql_engine(database_url)
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
    people = (LiveMapping(lambda: inputs.current.people) if inputs is not None else
        LiveMapping(lambda: {
        s.subject_id: PersonScope(
            subject=s.subject_id, circles=s.circle_ids,
            former_ids=tuple(p for p in s.person_ids if p != s.subject_id),
        )
        for s in subjects
    }))
    social = readable_social_store(engine, inputs if inputs is not None else persons)
    unit_rollups = UnitRollups(
        store=rollups, config=unit_config, units=units, people=people,
        stimuli=SqlStimulusStore(engine), metrics=metrics,
        social=social, access=access,
        areas=geocode_cache,
    )
    runner = RollupRunner(
        mobility=mobility, rollups=rollups, config=rollup_config,
        unit_rollups=unit_rollups, subjects=subjects, unit_config=unit_config,
        metrics=metrics, geocode_cache=geocode_cache,
    )
    log.info("analytics rollup wired: %s identity, tick %.0fs",
             cfg.identity.backend, analytics.tick_seconds)
    return AnalyticsDependencies(
        runner=runner, persons=persons, subjects=subjects, metrics=metrics,
        metrics_port=analytics.metrics_port, tick_seconds=analytics.tick_seconds,
        grace_seconds=analytics.grace_seconds, units=units, reload_files=reload_files,
        refresh_identity=inputs.refresh if inputs is not None else None,
        engine=engine,
    )


@asynccontextmanager
async def open_rollup(cfg: Config) -> AsyncIterator[AnalyticsDependencies]:
    """Own managed clients/engine for the daemon and rebuild; file mode keeps its factory."""
    if not cfg.analytics.enabled or cfg.identity.backend == "file":
        deps = build_rollup(cfg)
        try:
            yield deps
        finally:
            if deps.engine is not None:
                await deps.engine.dispose()
        return
    from nora.people import managed_services

    database_url = cfg.credentials.get(cfg.store.database_url_env, "")
    if not database_url:
        raise AnalyticsStartupError("analytics database credential is unavailable")
    try:
        policy = LivePolicy(load_policy(cfg.policy_file))
        run_migrations(database_url)
    except Exception as exc:  # noqa: BLE001 — CLI startup must not echo credentials or SQL parameters
        raise AnalyticsStartupError(
            f"managed analytics setup failed ({type(exc).__name__})") from None
    boot = policy.current

    def validate(candidate: Policy) -> None:
        if (candidate.units_declared, candidate._unit_workspace) != (
                boot.units_declared, boot._unit_workspace):
            raise RestartRequiredError("analytics policy unit workspace")

    reload = FileReload(cfg.policy_file, policy, load_policy, validate, Metrics())
    engine = create_sql_engine(database_url)
    try:
        async with ClientSession() as session:
            try:
                services = managed_services(managed=cfg.identity.managed, session=session,
                    policy=policy, engine=engine, credentials=cfg.credentials)
                state = await services.registry.identity_state(external_id="boot-probe")
                relations = await services.backend.relations_on(
                    scope=Scope(ScopeKind.WORKSPACE, "boot-probe"))
                if state.verdict is Verdict.UNAVAILABLE or not isinstance(relations, frozenset):
                    raise AnalyticsStartupError("managed services are unavailable")
                deps = build_rollup(cfg, access=services.access, policy=policy, engine=engine)
                if deps.refresh_identity is not None:
                    await deps.refresh_identity()
            except Exception as exc:  # noqa: BLE001 — bounded managed initialization failure
                raise AnalyticsStartupError(
                    f"managed analytics authority failed ({type(exc).__name__})") from None
            yield replace(deps, reload_files=reload)
    finally:
        await engine.dispose()
