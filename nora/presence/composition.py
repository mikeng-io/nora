"""The presence collector's composition root — the ONE place it reads Config.

Mirrors `nora.app.composition`: everything downstream receives dependencies, and
only this module (plus `nora.presence.__main__`, which passes `cfg` straight
through) knows `Config`'s shape. It is a SECOND top-level composition root
because the collector is a second process (§4) — the same DDD/CA shape design/20
§3 lays the substrate for.

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
import os
from dataclasses import dataclass

from nora.config import Config
from nora.domain.ports import ReverseGeocoder
from nora.infra.geocode import GoogleGeocoder
from nora.infra.geocode_hkgov import HKGovGeocoder
from nora.infra.life360 import Life360Client
from nora.infra.sql import create_sql_engine, run_migrations
from nora.infra.sql.presence import PresenceStore
from nora.obs.metrics import Metrics
from nora.persons import Persons, PersonsError, load_persons
from nora.presence.collector import CircleSpec, PresenceCollector

log = logging.getLogger("nora.presence.composition")


class PresenceStartupError(Exception):
    """A fatal presence-collector startup failure — refuse to start with a
    readable message, the same shape `nora.app.composition.StartupError` uses."""


def _circles_from_identity(persons: Persons) -> list[CircleSpec]:
    """Build the circles to collect from identity.toml's `[[persons.<id>.life360]]`
    blocks (design/19 §3), grouping members by their `circle_id`. config.toml
    carries no ids — the mapping lives in one place, gitignored.

    Fail-closed (R-5): a life360 block with no `circle_id` cannot be placed in a
    circle, and presence-enabled-but-no-collectable-members is a silent no-op —
    both refuse to start. The resolved `person_id`s per circle ARE the R-21
    disclosure roster (§5)."""
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
            "to collect; add circle_id or remove the block (design/19 §3)"
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


def build_collector(cfg: Config) -> PresenceDependencies:
    """Build the collector object graph from `cfg`, or an inert one when
    presence is disabled."""
    metrics = Metrics()
    presence = cfg.presence
    if not presence.enabled:
        log.info("presence is disabled (presence.enabled = false) — collector inert")
        return PresenceDependencies(
            collector=None, store=None, metrics=metrics, metrics_port=presence.metrics_port
        )

    try:
        persons = load_persons(cfg.identity_file)
    except PersonsError as exc:
        raise PresenceStartupError(f"identity.toml error: {exc}") from exc
    circles = _circles_from_identity(persons)  # raises if none / any unplaced

    credential = os.environ.get(presence.client_credential_env, "")
    username = os.environ.get(presence.username_env, "")
    password = os.environ.get(presence.password_env, "")
    missing = [
        name
        for name, value in (
            (presence.client_credential_env, credential),
            (presence.username_env, username),
            (presence.password_env, password),
        )
        if not value
    ]
    if missing:
        raise PresenceStartupError(
            "presence.enabled is true but these credential env vars are unset "
            f"({', '.join(missing)}); refusing to start (DEC-8: creds via env)"
        )

    database_url = os.environ.get(cfg.store.database_url_env, "")
    if not database_url:
        raise PresenceStartupError(
            f"environment variable {cfg.store.database_url_env} is not set — the "
            "PostgreSQL URL must be injected by the host (DEC-8); refusing to start"
        )
    try:
        # Idempotent (`alembic upgrade head`): the collector may start before or
        # after Nora, so it brings the shared `nora` database to head itself
        # rather than assuming Nora already did.
        run_migrations(database_url)
    except Exception as exc:
        raise PresenceStartupError(f"migration error: {exc}") from exc

    store = PresenceStore(
        create_sql_engine(database_url),
        retention_days=presence.retention_days,
        metrics=metrics,
    )
    client = Life360Client(
        client_credential=credential,
        username=username,
        password=password,
        metrics=metrics,
    )
    # The reverse geocoder — a SWITCHABLE option behind the `ReverseGeocoder`
    # port (2026-08-08). "google" (default, proven) needs the Maps key; "hkgov"
    # needs a portal service URL + token. Either is OPTIONAL: no credential →
    # no geocoder, and a fix stores its coordinate/Life360 label, never a startup
    # failure (an address is a nice-to-have, DEC-54's inert-by-default spirit).
    geocoder: ReverseGeocoder | None = None
    if presence.geocode_provider == "hkgov":
        token = os.environ.get(presence.hkgov_token_env, "")
        if token and presence.hkgov_geocode_url:
            geocoder = HKGovGeocoder(
                service_url=presence.hkgov_geocode_url, token=token,
                lang_zh=presence.hkgov_lang_zh, lang_en=presence.hkgov_lang_en,
            )
        else:
            log.info(
                "presence geocoder disabled: geocode_provider=hkgov but "
                "hkgov_geocode_url or %s is unset", presence.hkgov_token_env,
            )
    else:
        maps_key = os.environ.get(presence.geocode_api_key_env, "")
        if maps_key:
            geocoder = GoogleGeocoder(api_key=maps_key)
        else:
            log.info(
                "presence geocoder disabled (%s unset) — a fix without a Life360 "
                "address stores its coordinate only",
                presence.geocode_api_key_env,
            )
    collector = PresenceCollector(
        client=client,
        store=store,
        circles=circles,
        cadence_seconds=presence.cadence_seconds,
        cadence_moving_seconds=presence.cadence_moving_seconds,
        cadence_still_seconds=presence.cadence_still_seconds,
        geocoder=geocoder,
        metrics=metrics,
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
        metrics_port=presence.metrics_port,
    )
