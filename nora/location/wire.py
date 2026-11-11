import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from nora.agent.whereis import Circle, circles_from_persons
from nora.app.composition_readers import reverse_geocoder
from nora.domain.live import LiveSequence
from nora.infra.sql.mobility import SqlMobilityStore
from nora.infra.sql.presence import PresenceStore
from nora.location.adapters.tools import build as _build

"""Compose this area's tool adapters from shared dependencies."""


def tool_specs(deps):
    return _build(deps)


def been_iso_impl(deps):
    from nora.location.adapters.tools.presence import _make__been_iso

    return _make__been_iso(deps)


log = logging.getLogger("nora.app.composition")


@dataclass(frozen=True)
class StoresWiring:
    mobility: Any


def stores(engine) -> StoresWiring:
    # The dashboard consumer reads the derived analytics tables through the
    # same pool as every other Nora store. A second engine would fork the pool
    # and violate the one-pool, one-lifecycle composition rule.
    mobility = SqlMobilityStore(engine)
    return StoresWiring(mobility=mobility)


@dataclass(frozen=True)
class RouteWiring:
    route_client: Any


def route(cfg) -> RouteWiring:
    # The window scanned for a picture's marker code is the one `media_fetch` scans: the
    # prefill plus `load_transcript`'s reach, so any picture she can still see resolves and
    # an older one is honestly not found. `route` (Google, DEC-46) is wired ONLY when…
    route_client = None
    if cfg.route.enabled:
        route_key = cfg.credentials.get(cfg.route.api_key_env, "")
        if route_key:
            from nora.infra.routes.client import RouteClient

            route_client = RouteClient(api_key=route_key)
        else:
            log.warning(
                "route enabled but %s is unset — routing stays unavailable",
                cfg.route.api_key_env,
            )
    return RouteWiring(route_client=route_client)


@dataclass(frozen=True)
class PlaceAreasWiring:
    place_areas: Any


def place_areas(cfg, route_client) -> PlaceAreasWiring:

    # A venue's district and city come from the Geocoding API by the place id `place_info`
    # resolved it to, with the route key (a Places id is that key's project's, and the key
    # is restricted to Routes + Places + Geocoding). No route client, no venue area.
    place_areas = None
    if route_client is not None:
        from nora.infra.geocode import GoogleGeocoder

        place_areas = GoogleGeocoder(api_key=cfg.credentials.get(cfg.route.api_key_env, ""))
    return PlaceAreasWiring(place_areas=place_areas)


@dataclass(frozen=True)
class WeatherWiring:
    weather_client: Any


def weather(cfg) -> WeatherWiring:
    # `weather` (DEC-70) is wired on `[weather] enabled` alone — the HK Observatory provider
    # is KEYLESS, so unlike `route`/`search` there is no key to resolve; off leaves the
    # registry without a provider and `weather` answers `unavailable` (R-5). `provider`…
    weather_client = None
    if cfg.weather.enabled:
        if cfg.weather.provider == "hko":
            from nora.infra.weather import HkoWeatherProvider

            weather_client = HkoWeatherProvider(
                url=cfg.weather.url,
                lang=cfg.weather.lang,
                timeout_seconds=cfg.weather.timeout_seconds,
            )
        else:
            log.warning(
                "weather enabled but provider %r is unknown — weather stays unavailable",
                cfg.weather.provider,
            )
    return WeatherWiring(weather_client=weather_client)


@dataclass(frozen=True)
class PresenceWiring:
    presence_circles: Any
    presence_store: Any


def presence(cfg, engine, metrics, persons) -> PresenceWiring:
    # `whereis` (DEC-59) is wired ONLY when `[presence] enabled` AND identity.toml yields at
    # least one circle — the enabled-AND-configured gate `route` and media custody use. Off,
    # or circle-less, leaves the registry without a reader and `whereis` answers…
    presence_store: PresenceStore | None = None
    presence_circles: Sequence[Circle] = LiveSequence(lambda: circles_from_persons(persons))
    if cfg.presence.enabled:
        presence_store = PresenceStore(
            engine,
            retention_days=cfg.presence.retention_days,
            metrics=metrics,
        )
    return PresenceWiring(presence_circles=presence_circles, presence_store=presence_store)


@dataclass(frozen=True)
class RefreshWiring:
    refresher: Any


def refresh(cfg, presence_store) -> RefreshWiring:
    # The on-demand LIVE-refresh client (DEC-67): when `[presence].refresh_url` is set AND
    # presence is wired, `where_is` asks the collector to force a fresh reading before it
    # reads the store. Unset → `where_is` stays read-only (offline/eval, or an operator who…
    refresher = None
    if cfg.presence.refresh_url and presence_store is not None:
        from nora.infra.presence_refresh import HttpLocationRefresher

        refresher = HttpLocationRefresher(
            base_url=cfg.presence.refresh_url,
            # A margin over the collector's poll deadline, not a guarantee: the collector's TOTAL
            # time is request_location + read_circle (each up to the Life360 client's own timeout) +
            # the poll, so a slow network can still exceed this and Nora cuts it off. Harmless —…
            timeout_seconds=cfg.presence.refresh_timeout_seconds + 2.0,
        )
    return RefreshWiring(refresher=refresher)


@dataclass(frozen=True)
class GeocoderWiring:
    geocoder: Any


def geocoder(cfg, engine) -> GeocoderWiring:
    # The on-demand reverse-geocoder: `Whereis.locate` (nora/agent/whereis.py) calls it ONLY
    # when a fix has no address and someone actually asked `where_is` about it. The
    # collector talks to Life360 alone, never Google: geocoding every fix for every tracked…
    from nora.infra.sql.geocode_cache import SqlGeocodeCacheStore

    geocode_cache_store = SqlGeocodeCacheStore(engine)
    geocoder = reverse_geocoder(cfg, cfg.credentials, cache_store=geocode_cache_store)
    return GeocoderWiring(geocoder=geocoder)
