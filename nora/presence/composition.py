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
from nora.infra.life360 import Life360Client
from nora.infra.sql import create_sql_engine, run_migrations
from nora.infra.sql.presence import PresenceStore
from nora.obs.metrics import Metrics
from nora.presence.collector import CircleSpec, PresenceCollector

log = logging.getLogger("nora.presence.composition")


class PresenceStartupError(Exception):
    """A fatal presence-collector startup failure — refuse to start with a
    readable message, the same shape `nora.app.composition.StartupError` uses."""


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

    if not presence.circles:
        raise PresenceStartupError(
            "presence.enabled is true but no [[presence.circles]] are configured — "
            "there is nothing to collect; refusing to start"
        )

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
    circles = [
        CircleSpec(circle_id=c.circle_id, members=dict(c.members))
        for c in presence.circles
    ]
    collector = PresenceCollector(
        client=client,
        store=store,
        circles=circles,
        cadence_seconds=presence.cadence_seconds,
        metrics=metrics,
    )
    log.info(
        "presence collector wired: %d circle(s), cadence %.0fs, retention %dd",
        len(circles),
        presence.cadence_seconds,
        presence.retention_days,
    )
    return PresenceDependencies(
        collector=collector,
        store=store,
        metrics=metrics,
        metrics_port=presence.metrics_port,
    )
