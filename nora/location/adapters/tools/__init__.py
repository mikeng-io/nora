from __future__ import annotations

from types import SimpleNamespace

from nora.agent.toolkit.spec import _spec
from nora.location.app.circles import ScopedCircles


def build(deps):
    reader = ScopedCircles(deps.room, deps.presence_circles)
    metrics = deps.metrics
    deps = SimpleNamespace(**{**vars(deps), "metrics": reader.metrics(metrics)},
                           circle_reader=reader)
    from nora.location.adapters.tools.routes import _make_transit

    transit = _make_transit(deps)
    from nora.location.adapters.tools.routes import _make_route

    route = _make_route(deps)
    from nora.location.adapters.tools.routes import _make_place_info

    place_info = _make_place_info(deps)
    from nora.location.adapters.tools.routes import _make_saved_places

    saved_places = _make_saved_places(deps)
    from nora.location.adapters.tools.presence import _make_where_is

    where_is = _make_where_is(deps)
    from nora.location.adapters.tools.geo import _make_reverse_geocode

    reverse_geocode = _make_reverse_geocode(deps)
    from nora.location.adapters.tools.been import _make_where_been

    where_been = _make_where_been(deps)
    from nora.location.adapters.tools.routine_read import _make_routine_read

    routine_read = _make_routine_read(deps)
    from nora.location.adapters.tools.geo import _make_weather

    weather = _make_weather(deps)
    from nora.location.adapters.tools.geo import _make_parking_status

    parking_status = _make_parking_status(deps)
    from nora.location.adapters.tools.geo import _make_find_parking

    find_parking = _make_find_parking(deps)
    transit = reader.guarded(transit, "transit", metrics)
    route = reader.guarded(route, "route", metrics)
    place_info = reader.guarded(place_info, "place_info", metrics)
    saved_places = reader.guarded(saved_places, "saved_places", metrics)
    where_is = reader.guarded(where_is, "where_is", metrics)
    reverse_geocode = reader.guarded(reverse_geocode, "reverse_geocode", metrics)
    where_been = reader.guarded(where_been, "where_been", metrics)
    routine_read = reader.guarded(routine_read, "routine_read", metrics)
    weather = reader.guarded(weather, "weather", metrics)
    parking_status = reader.guarded(parking_status, "parking_status", metrics)
    find_parking = reader.guarded(find_parking, "find_parking", metrics)
    return (
        _spec("transit", transit),
        _spec("route", route),
        _spec("place_info", place_info),
        _spec("saved_places", saved_places),
        _spec("where_is", where_is),
        _spec("reverse_geocode", reverse_geocode),
        _spec("where_been", where_been),
        _spec("routine_read", routine_read),
        _spec("weather", weather),
        _spec("parking_status", parking_status),
        _spec("find_parking", find_parking),
    )
