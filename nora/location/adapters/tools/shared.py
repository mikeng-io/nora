from __future__ import annotations

from collections.abc import Mapping

from nora.domain.models import (
    WeatherObservation,
)

_MAPS_TRAVELMODE = {"TRANSIT": "transit", "DRIVE": "driving", "WALK": "walking"}

_PLACE_INFO_UNAVAILABLE = (
    "I can't look places up right now. Say you couldn't check, and don't guess a rating."
)

_WEATHER_OFF: Mapping[str, object] = {
    "status": "unavailable",
    "note": "I couldn't reach the weather service just now. Say you couldn't check, "
    "and don't invent a temperature or forecast.",
}


def _weather_result(obs: WeatherObservation) -> dict[str, object]:
    """Render a `WeatherObservation` into the model-facing tool result — only the"""
    result: dict[str, object] = {
        "status": "ok",
        "as_of": obs.as_of,
        "warnings": list(obs.warnings),  # always present (may be []); lead with it
        "temperatures": [{"place": p, "c": c} for p, c in obs.temperatures],
    }
    # ADR 0015 §3: how to report it, per field present. The warning clause
    # leads when there is one: a signal in force outranks the temperature.
    note = []
    if obs.warnings:
        note.append(
            "Lead with the warnings: a typhoon signal or rainstorm matters "
            "more than the temperature, so never call the weather fine "
            "while one is up."
        )
    if obs.temperatures:
        note.append(
            "temperatures are HKO stations across HK: use the one nearest "
            "the place asked about, and don't read them all out."
        )
    if note:
        result["note"] = " ".join(note)
    if obs.humidity_pct is not None:
        result["humidity_pct"] = obs.humidity_pct
    for key, value in (
        ("general_situation", obs.general_situation),
        ("today", obs.today_forecast),
        ("outlook", obs.outlook),
        ("typhoon", obs.typhoon),
    ):
        if value:
            result[key] = value
    if obs.forecast_days:
        result["forecast"] = [
            {
                "date": d.date,
                "weekday": d.weekday,
                "weather": d.weather,
                "wind": d.wind,
                "max_c": d.max_c,
                "min_c": d.min_c,
                "rain": d.rain_probability,
            }
            for d in obs.forecast_days
        ]
    return result
