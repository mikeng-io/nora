"""`HkoWeatherProvider` — HK weather from the Hong Kong Observatory Open Data API.

The `WeatherProvider` port's only implementation, and the reason weather needs
no key at all: HKO's Open Data API is fully public — no key, no registration,
no email gate. That
is why it is the DEFAULT and why `[weather]` ships ENABLED, unlike `route` (paid,
keyed) or `search` (keyed): there is nothing to turn on and nothing to leak. A
coordinate never enters here — the request carries no location; HKO returns the
whole territory and the model picks the station nearest the asked place.

One `weather` call reads several HKO `dataType`s CONCURRENTLY and combines them,
so a comprehensive answer costs one tool round, not three:
* `rhrread` — current report: per-station `temperature`, one-station `humidity`,
  and `warningMessage` (Very Hot Weather / Rainstorm / Typhoon lines, verbatim).
* `flw` — local forecast: `generalSituation`, today's `forecastDesc`, `outlook`,
  and `tcInfo` (the tropical-cyclone track note when one is active).
* `fnd` — the 9-day forecast, fetched ONLY when `forecast=True` (keeps the default
  payload lean; most 「今日天氣」 asks don't need nine days).

`lang=tc` because the group is Cantonese — names come back as 「元朗公園」「屯門」
and warnings/forecasts in Chinese, which is what Nora relays.

Like every outbound helper here (`geocode`, `routes`, `search`) it NEVER raises: a
non-200, non-JSON body, or transport error degrades to a missing reading, never an
exception into the agent loop (R-5). The current reading is load-bearing — no
`rhrread` means the whole call is `None` («I couldn't get the weather»); the
forecast pieces are best-effort — a `flw`/`fnd` failure just leaves those fields
empty rather than sinking an otherwise-good current answer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from nora.domain.models import DailyForecast, WeatherObservation

log = logging.getLogger("nora.weather")

HKO_OPENDATA_URL = "https://data.weather.gov.hk/weatherAPI/opendata/weather.php"


class HkoWeatherProvider:
    """HK Observatory Open Data → `WeatherObservation`, or None. NEVER raises.

    Constructed once per process and held by the tool registry. Keyless, so it is
    built whenever `[weather] enabled` — there is no key to gate on."""

    def __init__(
        self,
        *,
        url: str = HKO_OPENDATA_URL,
        lang: str = "tc",
        timeout_seconds: float = 8.0,
    ) -> None:
        self._url = url
        self._lang = lang
        self._timeout_seconds = timeout_seconds

    async def current(
        self, *, place: str | None = None, forecast: bool = False,
    ) -> WeatherObservation | None:
        """The current HK weather picture, plus the 9-day forecast when
        `forecast=True`. `place` is accepted for the port's sake but HKO reports
        the whole territory, so it is not sent upstream — the model picks the
        station nearest what it asked about. None on a current-reading failure."""
        try:
            return await self._current(forecast=forecast)
        except Exception:  # the never-raises backstop (R-5)
            log.exception("weather lookup failed")
            return None

    async def _current(self, *, forecast: bool) -> WeatherObservation | None:
        data_types = ["rhrread", "flw"] + (["fnd"] if forecast else [])
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            bodies = await asyncio.gather(
                *(self._get(session, dt) for dt in data_types),
                return_exceptions=True,
            )
        got: dict[str, dict[str, Any]] = {}
        for dt, body in zip(data_types, bodies, strict=False):
            if isinstance(body, dict):
                got[dt] = body
            elif isinstance(body, BaseException):
                log.warning("weather %s errored: %s", dt, body)

        rhrread = got.get("rhrread")
        if rhrread is None:
            # The current reading is the load-bearing half — without it there is
            # no answer, only «I couldn't get the weather».
            return None
        temps, humidity, warnings, as_of = _parse_current(rhrread)
        if not temps:
            return None

        flw = got.get("flw") or {}
        forecast_days = _parse_forecast(got.get("fnd")) if forecast else ()

        return WeatherObservation(
            as_of=as_of,
            temperatures=temps,
            humidity_pct=humidity,
            warnings=warnings,
            general_situation=_text(flw.get("generalSituation")),
            today_forecast=_text(flw.get("forecastDesc")),
            outlook=_text(flw.get("outlook")),
            typhoon=_text(flw.get("tcInfo")),
            forecast_days=forecast_days,
        )

    async def _get(self, session: aiohttp.ClientSession, data_type: str) -> dict[str, Any] | None:
        """One HKO dataType as a dict, or None on any non-200 / non-JSON body."""
        params = {"dataType": data_type, "lang": self._lang}
        async with session.get(self._url, params=params) as response:
            if response.status != 200:
                log.warning("weather %s answered %s", data_type, response.status)
                return None
            try:
                payload = await response.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError):
                log.warning("weather %s did not return JSON", data_type)
                return None
        return payload if isinstance(payload, dict) else None


def _parse_current(
    payload: dict[str, Any],
) -> tuple[tuple[tuple[str, float], ...], int | None, tuple[str, ...], str]:
    """`rhrread` → (temperatures, humidity, warnings, as_of). Tolerant of the two
    live quirks: `warningMessage` is a LIST when warnings are up but a bare `""`
    when none are, and a temperature `value` may be int or float."""
    temps: list[tuple[str, float]] = []
    for item in _data_list(payload.get("temperature")):
        place = str(item.get("place") or "").strip()
        value = item.get("value")
        if place and isinstance(value, (int, float)):
            temps.append((place, float(value)))

    humidity: int | None = None
    for item in _data_list(payload.get("humidity")):
        value = item.get("value")
        if isinstance(value, (int, float)):
            humidity = int(value)
            break

    raw_warn = payload.get("warningMessage")
    warnings: tuple[str, ...]
    if isinstance(raw_warn, str):
        warnings = (raw_warn,) if raw_warn.strip() else ()
    elif isinstance(raw_warn, list):
        warnings = tuple(str(w).strip() for w in raw_warn if str(w).strip())
    else:
        warnings = ()

    temp = payload.get("temperature")
    as_of = str(temp.get("recordTime") or "") if isinstance(temp, dict) else ""
    if not as_of:
        as_of = str(payload.get("updateTime") or "")

    return tuple(temps), humidity, warnings, as_of


def _parse_forecast(payload: dict[str, Any] | None) -> tuple[DailyForecast, ...]:
    """`fnd.weatherForecast` → a tuple of `DailyForecast`, or () if absent."""
    if not isinstance(payload, dict):
        return ()
    days = payload.get("weatherForecast")
    if not isinstance(days, list):
        return ()
    out: list[DailyForecast] = []
    for day in days:
        if not isinstance(day, dict):
            continue
        out.append(DailyForecast(
            date=str(day.get("forecastDate") or ""),
            weekday=_text(day.get("week")),
            weather=_text(day.get("forecastWeather")),
            wind=_text(day.get("forecastWind")),
            max_c=_temp_value(day.get("forecastMaxtemp")),
            min_c=_temp_value(day.get("forecastMintemp")),
            rain_probability=_text(day.get("PSR")),
        ))
    return tuple(out)


def _data_list(section: Any) -> list[dict[str, Any]]:
    """The `data` list inside a `{recordTime, data:[...]}` section, or []."""
    if not isinstance(section, dict):
        return []
    data = section.get("data")
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _temp_value(section: Any) -> float | None:
    """The `value` inside a `{value, unit}` temperature object, or None."""
    if isinstance(section, dict) and isinstance(section.get("value"), (int, float)):
        return float(section["value"])
    return None


def _text(value: Any) -> str:
    """A trimmed string, or "" — HKO omits a field by sending "" already."""
    return str(value).strip() if isinstance(value, str) else ""
