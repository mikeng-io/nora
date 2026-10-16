"""`HkoWeatherProvider` — HK Observatory current + forecast, and its fail-soft
discipline. Runs offline against a local aiohttp server that behaves like the
real HKO Open Data host, INCLUDING the shapes that make it hard: `warningMessage`
is a LIST when warnings are up but a bare `""` when none are, `dataType` selects
the body, and a temperature `value` may be int or float. Reverting the never-raises
or the current-required rule turns a test here red — the fake answers wrong the
way the real host does.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.weather.hko import HkoWeatherProvider

# Real-shaped bodies (trimmed) captured from the live endpoint.
_RHRREAD = {
    "temperature": {
        "recordTime": "2026-08-08T18:00:00+08:00",
        "data": [
            {"place": "Yuen Long Park", "value": 34, "unit": "C"},
            {"place": "Tuen Mun", "value": 33.0, "unit": "C"},
        ],
    },
    "humidity": {"data": [{"place": "Hong Kong Observatory", "value": 57, "unit": "percent"}]},
    "warningMessage": ["The Very Hot Weather Warning is now in force."],
    "updateTime": "2026-08-08T18:02:00+08:00",
}
_FLW = {
    "generalSituation": "強颱風白海豚的外圍下沉氣流正為廣東帶來極端酷熱的天氣。",
    "forecastDesc": "大致天晴，日間極端酷熱。",
    "outlook": "下週初持續酷熱。",
    "tcInfo": "白海豚集結在溫州之東南偏東約460公里。",
    "updateTime": "2026-08-08T17:45:00+08:00",
}
_FND = {
    "weatherForecast": [
        {
            "forecastDate": "20260809", "week": "星期日",
            "forecastWind": "西至西北風4級。", "forecastWeather": "大致天晴。日間極端酷熱。",
            "forecastMaxtemp": {"value": 36, "unit": "C"},
            "forecastMintemp": {"value": 29, "unit": "C"}, "PSR": "低",
        },
    ],
    "updateTime": "2026-08-08T17:45:00+08:00",
}


class _Hko:
    """The HKO endpoint, keyed on `dataType`, with a request log and per-type
    failure modes so the current-required and best-effort rules can be proven."""

    def __init__(self) -> None:
        self.rhrread: object = _RHRREAD
        self.flw: object = _FLW
        self.fnd: object = _FND
        self.seen: list[str] = []

    async def handle(self, request: web.Request) -> web.Response:
        dt = request.query.get("dataType", "")
        self.seen.append(dt)
        body = {"rhrread": self.rhrread, "flw": self.flw, "fnd": self.fnd}.get(dt)
        if body == "500":
            return web.Response(status=500)
        if body == "notjson":
            return web.Response(text="<html>not json</html>", content_type="text/html")
        return web.json_response(body)


@pytest.fixture
async def hko(monkeypatch):
    fake = _Hko()
    app = web.Application()
    app.router.add_get("/weather.php", fake.handle)
    server = TestServer(app)
    await server.start_server()
    url = str(server.make_url("/weather.php"))
    yield fake, HkoWeatherProvider(url=url, timeout_seconds=5.0)
    await server.close()


async def test_current_reads_temperatures_humidity_warnings_and_outlook(hko):
    fake, provider = hko
    obs = await provider.current(place="天水圍")
    assert obs is not None
    assert ("Yuen Long Park", 34.0) in obs.temperatures
    assert ("Tuen Mun", 33.0) in obs.temperatures
    assert obs.humidity_pct == 57
    assert obs.warnings == ("The Very Hot Weather Warning is now in force.",)
    assert "極端酷熱" in obs.today_forecast
    assert obs.typhoon  # tcInfo carried
    assert "fnd" not in fake.seen  # forecast NOT fetched unless asked


async def test_forecast_true_pulls_the_multi_day_forecast(hko):
    fake, provider = hko
    obs = await provider.current(forecast=True)
    assert obs is not None
    assert "fnd" in fake.seen
    assert len(obs.forecast_days) == 1
    day = obs.forecast_days[0]
    assert day.date == "20260809" and day.max_c == 36.0 and day.rain_probability == "低"


async def test_no_warning_is_an_empty_tuple_not_a_blank_string(hko):
    fake, provider = hko
    fake.rhrread = {**_RHRREAD, "warningMessage": ""}   # the HKO "none in force" shape
    obs = await provider.current()
    assert obs is not None
    assert obs.warnings == ()


async def test_no_current_reading_is_none_not_an_empty_observation(hko):
    fake, provider = hko
    fake.rhrread = "500"  # the load-bearing half failed
    assert await provider.current() is None


async def test_a_broken_forecast_still_yields_the_current_reading(hko):
    fake, provider = hko
    fake.flw = "500"  # best-effort half fails
    obs = await provider.current()
    assert obs is not None
    assert obs.temperatures  # current survived
    assert obs.today_forecast == ""  # outlook just empty


async def test_a_non_json_body_is_none_never_raises(hko):
    fake, provider = hko
    fake.rhrread = "notjson"
    assert await provider.current() is None


async def test_a_dead_host_is_none_never_raises():
    provider = HkoWeatherProvider(url="http://127.0.0.1:1/dead", timeout_seconds=1.0)
    assert await provider.current() is None


def test_render_always_carries_warnings_and_omits_empty_fields():
    """`_weather_result` keeps the input lean (the fetch-token lesson): warnings
    ride EVERY result (may be []), but an empty outlook / absent typhoon / absent
    forecast are dropped rather than sent as blanks re-billed each turn."""
    from nora.agent.tools import _weather_result
    from nora.domain.models import WeatherObservation

    obs = WeatherObservation(
        as_of="2026-08-08T18:00:00+08:00",
        temperatures=(("Yuen Long Park", 34.0),),
        humidity_pct=57,
        warnings=("酷熱天氣警告",),
        general_situation="",           # empty → omitted
        today_forecast="大致天晴",
        outlook="",                     # empty → omitted
        typhoon="",                     # empty → omitted
        forecast_days=(),               # empty → no "forecast" key
    )
    result = _weather_result(obs)
    assert result["status"] == "ok"
    assert result["warnings"] == ["酷熱天氣警告"]        # always present
    assert result["temperatures"] == [{"place": "Yuen Long Park", "c": 34.0}]
    assert result["today"] == "大致天晴"
    assert "general_situation" not in result             # empty dropped
    assert "outlook" not in result and "typhoon" not in result
    assert "forecast" not in result


def test_render_no_warnings_still_carries_an_empty_list():
    from nora.agent.tools import _weather_result
    from nora.domain.models import WeatherObservation

    obs = WeatherObservation(
        as_of="t", temperatures=(("HKO", 30.0),), humidity_pct=None,
        warnings=(), general_situation="", today_forecast="", outlook="",
        typhoon="", forecast_days=(),
    )
    result = _weather_result(obs)
    assert result["warnings"] == []                      # present, empty
    assert "humidity_pct" not in result                  # None dropped


# ---- ADR 0015 §3: how to report a status rides in the result ----------------


def test_an_ok_note_leads_with_a_warning_only_when_there_is_one():
    """«Lead with the warning» and «use the nearest station» left the
    description; each arrives only with the field it is about."""
    from nora.agent.tools import _weather_result
    from nora.domain.models import WeatherObservation

    def obs(warnings, temperatures):
        return WeatherObservation(
            as_of="t", temperatures=temperatures, humidity_pct=None,
            warnings=warnings, general_situation="", today_forecast="", outlook="",
            typhoon="", forecast_days=(),
        )

    warned = _weather_result(obs(("酷熱天氣警告",), (("HKO", 34.0),)))
    assert warned["note"].startswith("Lead with the warnings")
    assert "nearest" in warned["note"]

    calm = _weather_result(obs((), (("HKO", 30.0),)))
    assert "warning" not in calm["note"]
    assert "nearest" in calm["note"]

    bare = _weather_result(obs((), ()))
    assert "note" not in bare
    for out in (warned, calm):
        assert "—" not in out["note"] and "–" not in out["note"]


async def test_every_non_ok_weather_carries_a_dash_free_note():
    """Both `unavailable` paths (unwired, provider down) carry a note. The
    provider is a fault-only double: the tool folds a raise and a `None` into
    the same `_WEATHER_OFF`."""
    from nora.agent.tools import build_tool_specs
    from nora.obs.metrics import Metrics

    class _Down:
        async def current(self, *, place=None, forecast=False):
            raise RuntimeError("HKO is down")

    for source in (None, _Down()):
        specs = build_tool_specs(
            store=None, conversation_id="c", conversation_type="group",
            persons=None, honcho=None, deliverer=None, room=None,
            transit_source=None, weather_source=source, metrics=Metrics(),
        )
        out = await next(s for s in specs if s.name == "weather").call()
        assert out["status"] == "unavailable"
        assert out["note"].strip()
        assert "—" not in out["note"] and "–" not in out["note"]
