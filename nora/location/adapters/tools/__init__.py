from __future__ import annotations

from nora.agent.toolkit.spec import _spec


def build(deps):
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
    from nora.location.adapters.tools.presence import _make_where_been

    where_been = _make_where_been(deps)
    from nora.location.adapters.tools.routine import _make_routine_read

    routine_read = _make_routine_read(deps)
    from nora.location.adapters.tools.geo import _make_weather

    weather = _make_weather(deps)
    from nora.location.adapters.tools.geo import _make_parking_status

    parking_status = _make_parking_status(deps)
    from nora.location.adapters.tools.geo import _make_find_parking

    find_parking = _make_find_parking(deps)
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
