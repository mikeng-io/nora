"""The presence collector's composition root — the ONE place it reads Config.

Mirrors `nora.app.composition`: everything downstream receives dependencies, and
only this module (plus `nora.presence.__main__`, which passes `cfg` straight
through) knows `Config`'s shape. It is a SECOND top-level composition root
because the collector is a second process.

`build_collector` returns a wired `PresenceCollector`, or `None` when presence is
disabled — the inert default (DEC-54/55). A disabled collector is not an error:
the daemon logs it and exits cleanly, exactly as an operator would expect from a
process they started before turning the feature on.

Enabled-but-misconfigured IS an error, and it refuses to start (R-5: a collector
that cannot authenticate is silently broken, indistinguishable from a quiet
circle). The credentials are read from the environment by NAME (DEC-8); a
missing one is a `PresenceStartupError`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from aiohttp import ClientSession
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.access import IdentityAccess
from nora.config import Config
from nora.config_reload import FileReload, LivePersons
from nora.domain.live import LiveSequence
from nora.domain.managed import Verdict
from nora.infra.life360 import Life360Client
from nora.infra.sql import create_sql_engine, run_migrations
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.item_registration import (
    RegistrationScope,
    RuntimeRevisions,
    SqlItemRegistration,
)
from nora.infra.sql.presence import PresenceStore
from nora.infra.sql.source_health import SqlSourceHealthLog
from nora.obs.metrics import Metrics
from nora.people import ManagedPresenceAccounts
from nora.persons import Persons, PersonsError, load_persons
from nora.policy import Policy, PolicyError, load_policy
from nora.presence.collector import CircleSpec, PresenceCollector

log = logging.getLogger("nora.presence.composition")


class PresenceStartupError(Exception):
    """A fatal presence-collector startup failure — refuse to start with a
    readable message, the same shape `nora.app.composition.StartupError` uses."""


def _circles_from_identity(persons: Persons) -> list[CircleSpec]:
    """The file backend's operator-authored circle and account mappings."""
    by_circle: dict[str, dict[str, str]] = {}
    unplaced: list[str] = []
    for membership in persons.life360:
        if membership.circle_id is None:
            unplaced.append(membership.member_id)
            continue
        by_circle.setdefault(membership.circle_id, {})[membership.member_id] = (
            membership.person_id
        )
    if unplaced:
        raise PresenceStartupError(
            f"Life360 member id(s) {unplaced} have a [[persons.<id>.life360]] block "
            "with no circle_id in identity.toml — they cannot be placed in a circle "
            "to collect; add circle_id or remove the block (design/self-awareness/presence.md)"
        )
    if not by_circle:
        raise PresenceStartupError(
            "presence.enabled is true but no [[persons.<id>.life360]] block with a "
            "circle_id exists in identity.toml — there is nothing to collect; "
            "refusing to start"
        )
    return [
        CircleSpec(circle_id=circle_id, members=members)
        for circle_id, members in by_circle.items()
    ]


@dataclass(frozen=True)
class PresenceDependencies:
    """What the collector process holds. `collector` is None when disabled."""

    collector: PresenceCollector | None
    store: PresenceStore | None
    metrics: Metrics
    metrics_port: int
    identity_reload: FileReload[Persons] | None = None


def _file_circles(
    cfg: Config, metrics: Metrics,
) -> tuple[Sequence[CircleSpec], FileReload[Persons], Policy | None, LivePersons]:
    try:
        persons = LivePersons(load_persons(cfg.identity_file))
        policy = load_policy(cfg.policy_file) if cfg.policy_file.exists() else None
    except (PersonsError, PolicyError) as exc:
        raise PresenceStartupError(f"identity/policy error: {exc}") from exc

    def validate_persons(candidate: Persons) -> None:
        if cfg.agent.name in candidate.by_person:
            raise PresenceStartupError("identity.toml uses the reserved aiPeer person id")
        _circles_from_identity(candidate)

    validate_persons(persons)
    identity_reload = FileReload(
        cfg.identity_file, persons, load_persons, validate_persons, metrics,
    )
    circles = LiveSequence(lambda: _circles_from_identity(persons))

    return circles, identity_reload, policy, persons


def build_collector(
    cfg: Config, *, managed_accounts: ManagedPresenceAccounts | None = None,
    engine: AsyncEngine | None = None,
    access: IdentityAccess | None = None, policy: Policy | None = None,
) -> PresenceDependencies:
    """Build the collector object graph from `cfg`, or an inert one when
    presence is disabled."""
    metrics = Metrics()
    presence = cfg.presence
    if not presence.enabled:
        log.info("presence is disabled (presence.enabled = false) — collector inert")
        return PresenceDependencies(
            collector=None, store=None, metrics=metrics, metrics_port=presence.metrics_port
        )

    identity_reload = None
    persons = None
    if cfg.identity.backend == "managed":
        if managed_accounts is None or engine is None:
            raise PresenceStartupError("managed presence authority is unavailable")
        circles: Sequence[CircleSpec] = _managed_circles(cfg)
    else:
        circles, identity_reload, policy, persons = _file_circles(cfg, metrics)

    credentials = cfg.credentials
    credential = credentials.get(presence.client_credential_env, "")
    username = credentials.get(presence.username_env, "")
    password = credentials.get(presence.password_env, "")
    # Token mode: an operator-supplied bearer replaces the password grant, so
    # the three login vars are not what the collector needs to start.
    access_token = credentials.get(presence.auth_token_env, "")
    missing = [
        name
        for name, value in (
            (presence.client_credential_env, credential),
            (presence.username_env, username),
            (presence.password_env, password),
        )
        if not value and not access_token
    ]
    if missing:
        raise PresenceStartupError(
            "presence.enabled is true but these credential env vars are unset "
            f"({', '.join(missing)}); refusing to start (DEC-8: creds via env)"
        )

    database_url = cfg.credentials.get(cfg.store.database_url_env, "")
    if not database_url:
        raise PresenceStartupError(
            f"environment variable {cfg.store.database_url_env} is not set — the "
            "PostgreSQL URL must be injected by the host (DEC-8); refusing to start"
        )
    if engine is None:
        try:
            run_migrations(database_url)
        except Exception as exc:  # noqa: BLE001 — bounded startup error
            raise PresenceStartupError(f"migration failed ({type(exc).__name__})") from None
        engine = create_sql_engine(database_url)
    registration = SqlItemRegistration(
        RegistrationScope(policy, persons, access=access),
        RuntimeRevisions(cfg.model_dump(), policy, persons),
    ) if policy is not None else None
    store = PresenceStore(
        engine,
        retention_days=presence.retention_days,
        metrics=metrics,
        registration=registration,
        aggregate_graph=SqlCognitiveGraph(engine),
    )
    client = Life360Client(
        client_credential=credential,
        username=username,
        password=password,
        metrics=metrics,
        access_token=access_token or None,
    )
    if access_token:
        log.info("life360 auth: token mode (%s) — the password grant is never used",
                 presence.auth_token_env)
    collector = PresenceCollector(
        client=client,
        store=store,
        circles=circles,
        cadence_seconds=presence.cadence_seconds,
        cadence_moving_seconds=presence.cadence_moving_seconds,
        cadence_still_seconds=presence.cadence_still_seconds,
        metrics=metrics,
        # Every poll's outcome, as Record: what
        # lets a later step prove «the collector was healthy for the span».
        health_log=SqlSourceHealthLog(engine, registration=registration),
        managed_accounts=managed_accounts,
    )
    log.info(
        "presence collector wired: %d circle(s), cadence %.0f-%.0fs, retention %dd",
        len(circles),
        presence.cadence_moving_seconds,
        presence.cadence_still_seconds,
        presence.retention_days,
    )
    return PresenceDependencies(
        collector=collector,
        store=store,
        metrics=metrics,
        metrics_port=presence.metrics_port, identity_reload=identity_reload,
    )


def _managed_circles(cfg: Config) -> tuple[CircleSpec, ...]:
    ids = cfg.presence.managed_circle_ids
    if not ids:
        raise PresenceStartupError("managed presence requires explicit circle selection")
    return tuple(CircleSpec(circle_id=cid, members={}) for cid in ids)


@asynccontextmanager
async def open_collector(cfg: Config) -> AsyncIterator[PresenceDependencies]:
    """Own the selected managed clients and engine for the collector lifetime."""
    if not cfg.presence.enabled or cfg.identity.backend == "file":
        deps = build_collector(cfg)
        try:
            yield deps
        finally:
            if deps.store is not None:
                await deps.store.aclose()
        return
    from nora.people import managed_services

    _managed_circles(cfg)
    database_url = cfg.credentials.get(cfg.store.database_url_env, "")
    if not database_url:
        raise PresenceStartupError("presence database credential is unavailable")
    try:
        policy = load_policy(cfg.policy_file)
        run_migrations(database_url)
    except Exception as exc:  # noqa: BLE001 — startup errors must not echo private paths or SQL
        raise PresenceStartupError(
            f"managed presence setup failed ({type(exc).__name__})") from None
    engine = create_sql_engine(database_url)
    try:
        async with ClientSession() as session:
            try:
                services = managed_services(managed=cfg.identity.managed, session=session,
                    policy=policy, engine=engine, credentials=cfg.credentials)
                state = await services.registry.identity_state(external_id="boot-probe")
                if state.verdict is Verdict.UNAVAILABLE:
                    raise PresenceStartupError("managed identity source is unavailable")
                deps = build_collector(cfg, engine=engine, access=services.access, policy=policy,
                    managed_accounts=ManagedPresenceAccounts(services.bindings, services.registry))
            except Exception as exc:  # noqa: BLE001 — no credential-bearing errors escape startup
                raise PresenceStartupError(
                    f"managed presence authority failed ({type(exc).__name__})") from None
            yield deps
    finally:
        await engine.dispose()
