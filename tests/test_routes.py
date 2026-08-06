"""`route` — Google routing + place resolution (design/14 §3, §5.2; DEC-46, R-28).

Every scenario here is offline, against a local `aiohttp` server that behaves
like the two real Google endpoints — INCLUDING the shapes they return for the
wrong call. That is not decoration (CLAUDE.md's «never a fake that answers every
request the same way»): a route is TWO endpoints — Places Text Search resolves
each name to a coordinate, then Routes computes the journey — and a fake that
served both the same way would prove nothing. So the fake below:

* serves Places Text Search only over `POST /v1/places:searchText` and Routes
  only over `POST /directions/v2:computeRoutes`, answering a GET on either with
  405 the way the real hosts do;
* returns a 200-with-zero-results for a name nothing matches (the shape that
  must read as `not_found`, distinct from a 500 that must read as
  `unavailable`);
* returns `{}` — no `routes` — for a pair Google cannot connect (the shape that
  must read as `no_route`, distinct again from `unavailable`);
* records every request it received, so a test can assert that a query blocked
  by R-28 reached NEITHER endpoint.

Payload shapes are trimmed copies of the live v2 responses (天水圍站 → Yoho,
TRANSIT, 358 s, captured 2026-08-06): `duration` is `"358s"`, `distanceMeters`
an int, transit legs carry `transitLine.nameShort` / `.vehicle.type` /
`.agencies[].name` and `stopDetails` with RFC 3339 times.
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.routes import RouteClient
from nora.infra.routes.places import PlaceResolver

# ---- what the two Google endpoints really return ----------------------------

# One place per query, keyed on the textQuery the resolver sends. Coordinates,
# names and dining fields in the shapes Text Search (New) actually uses:
# `location.{latitude,longitude}`, `displayName.text`, `priceLevel` an ENUM
# string, `currentOpeningHours.openNow` a bool.
PLACES = {
    "天水圍站": [
        {"id": "tsw", "displayName": {"text": "Tin Shui Chi Station"},
         "formattedAddress": "Tin Shui Chi",
         "location": {"latitude": 22.4667, "longitude": 114.0033}},
    ],
    "Yoho Mall": [
        {"id": "yoho", "displayName": {"text": "YOHO Mall"},
         "formattedAddress": "Yuen Long", "location": {"latitude": 22.4445, "longitude": 114.0225},
         "rating": 4.2, "priceLevel": "PRICE_LEVEL_MODERATE",
         "currentOpeningHours": {"openNow": True}},
        # A second point ~40 m away with the SAME name — one mall's two
        # entrances, NOT an ambiguity.
        {"id": "yoho2", "displayName": {"text": "YOHO Mall"},
         "formattedAddress": "Yuen Long", "location": {"latitude": 22.4448, "longitude": 114.0226}},
    ],
    # A genuinely ambiguous name — a CATEGORY, not a place: several DISTINCT
    # hotpot restaurants scattered across districts. Three distinct far results
    # (the top plus two others) is what `MIN_AMBIGUOUS_OTHERS` calls a fork,
    # after the live data showed a single runner-up is usually the same place.
    "打邊爐": [
        {"id": "hp1", "displayName": {"text": "旺角打邊爐"},
         "formattedAddress": "Mong Kok", "location": {"latitude": 22.410, "longitude": 114.124}},
        {"id": "hp2", "displayName": {"text": "元朗打邊爐"},
         "formattedAddress": "Yuen Long", "location": {"latitude": 22.444, "longitude": 114.022}},
        {"id": "hp3", "displayName": {"text": "銅鑼灣打邊爐"},
         "formattedAddress": "CWB",
         "location": {"latitude": 22.410, "longitude": 114.185}},
    ],
    # A named landmark that returns a bilingual DUPLICATE of the same place plus
    # itself — one far runner-up only, so it must resolve to the top hit, not to
    # a spurious «which one». This is the 香港迪士尼樂園 shape, measured live.
    "迪士尼": [
        {"id": "disney_zh", "displayName": {"text": "香港迪士尼樂園"},
         "formattedAddress": "Lantau", "location": {"latitude": 22.3130, "longitude": 114.0413}},
        {"id": "disney_en", "displayName": {"text": "Hong Kong Disneyland Resort"},
         "formattedAddress": "Lantau", "location": {"latitude": 22.3160, "longitude": 114.0450}},
    ],
    # A place that exists but which Routes cannot connect to the origin.
    "孤島": [
        {"id": "island", "displayName": {"text": "Lonely Island"},
         "formattedAddress": "Nowhere", "location": {"latitude": 22.15, "longitude": 113.90}},
    ],
}

# The transit journey 天水圍站 → Yoho, trimmed from the live response. One walk
# step (no transitDetails) and one bus step (K73).
ROUTES_TRANSIT = {
    "routes": [{
        "duration": "358s", "distanceMeters": 2100,
        "legs": [{
            "steps": [
                {"travelMode": "WALK"},
                {"transitDetails": {
                    "stopDetails": {
                        "departureStop": {"name": "Tin Shui Chi Station"},
                        "departureTime": "2026-08-06T12:00:00Z",
                        "arrivalStop": {"name": "YOHO Mall"},
                        "arrivalTime": "2026-08-06T12:05:58Z",
                    },
                    "headsign": "Yuen Long West",
                    "transitLine": {
                        "name": "Tin Heng — Yuen Long West", "nameShort": "K73",
                        "vehicle": {"type": "BUS"},
                        "agencies": [{"name": "MTR Bus"}],
                    },
                    "stopCount": 6,
                }},
            ],
        }],
    }],
}


class _Google:
    """The two Google endpoints, each behaving like the real host — including
    for the wrong verb and the wrong query.

    Flags let one test make a specific failure happen without disturbing the
    others: `places_status` / `routes_status` for a transport failure,
    `no_route` for the empty-`routes` shape.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.last_routes_body: dict = {}
        self.last_field_mask: str = ""
        self.places_status = 200
        self.routes_status = 200
        self.no_route = False

    async def places(self, request: web.Request) -> web.Response:
        if request.method != "POST":                # the real endpoint is POST-only
            return web.Response(status=405)
        body = await request.json()
        query = body.get("textQuery", "")
        self.calls.append(f"places:{query}")
        if self.places_status != 200:
            return web.Response(status=self.places_status)
        return web.json_response({"places": PLACES.get(query, [])})

    async def routes(self, request: web.Request) -> web.Response:
        if request.method != "POST":
            return web.Response(status=405)
        self.last_routes_body = await request.json()
        self.last_field_mask = request.headers.get("X-Goog-FieldMask", "")
        self.calls.append("routes")
        if self.routes_status != 200:
            return web.Response(status=self.routes_status)
        if self.no_route:
            return web.json_response({})            # Google's no-connection shape
        return web.json_response(ROUTES_TRANSIT)


@pytest.fixture
async def google():
    ops = _Google()
    app = web.Application()
    app.router.add_route("*", "/v1/places:searchText", ops.places)
    app.router.add_route("*", "/directions/v2:computeRoutes", ops.routes)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("/")).rstrip("/")
    resolver = PlaceResolver(
        api_key="test-key", search_url=f"{base}/v1/places:searchText",
        timeout_seconds=8.0,
    )
    ops.client = RouteClient(                                       # type: ignore[attr-defined]
        api_key="test-key", routes_url=f"{base}/directions/v2:computeRoutes",
        resolver=resolver,
    )
    yield ops
    await server.close()


# ---- the three states, and they are three different answers ------------------

async def test_a_real_route_is_ok_with_typed_legs(google):
    out = await google.client.route("天水圍站", "Yoho Mall")
    assert out["status"] == "ok"
    assert out["mode"] == "TRANSIT"
    assert out["duration_seconds"] == 358
    assert out["distance_meters"] == 2100
    leg = out["legs"][0]
    assert leg["line"] == "K73"
    assert leg["vehicle"] == "BUS"
    assert leg["agency"] == "MTR Bus"
    assert leg["departure_stop"] == "Tin Shui Chi Station"
    assert leg["headsign"] == "Yuen Long West"
    assert leg["stop_count"] == 6


async def test_no_route_is_not_unavailable(google):
    """Google was reached and returned no way to connect the two points — a
    DIFFERENT answer from «I could not check» (design/14 §3)."""
    google.no_route = True
    out = await google.client.route("天水圍站", "孤島")
    assert out["status"] == "no_route"
    assert "routes" in google.calls          # it DID reach Google


async def test_a_failed_call_is_unavailable_not_no_route(google):
    google.routes_status = 500
    out = await google.client.route("天水圍站", "Yoho Mall")
    assert out["status"] == "unavailable"


async def test_a_place_that_resolves_to_nothing_is_no_route_without_calling_routes(google):
    """A name Places returns zero results for is `no_route` with the miss named
    — and Routes is never asked, because there is no coordinate to ask about."""
    out = await google.client.route("天水圍站", "nowhere-at-all")
    assert out["status"] == "no_route"
    assert "routes" not in google.calls
    assert "天水圍站" in out["note"] or "nowhere" in out["note"]


async def test_unreadable_place_data_is_unavailable(google):
    google.places_status = 503
    out = await google.client.route("天水圍站", "Yoho Mall")
    assert out["status"] == "unavailable"


# ---- the dining bonus is typed, not wrapped ----------------------------------

async def test_a_dining_destination_returns_typed_fields_unwrapped(google):
    out = await google.client.route("天水圍站", "Yoho Mall")
    info = out["destination_info"]
    assert info["rating"] == 4.2
    assert info["price_level"] == "PRICE_LEVEL_MODERATE"
    assert info["open_now"] is True
    # Typed — nothing here is wrapped `untrusted="true"`, and no free-text
    # review field is present (design/14 §2.1; the field mask omits reviews).
    assert "<untrusted" not in json.dumps(out, ensure_ascii=False)
    assert "review" not in json.dumps(out, ensure_ascii=False).lower()


# ---- the field mask is a bill: only what is rendered -------------------------

async def test_the_field_mask_names_only_rendered_fields(google):
    await google.client.route("天水圍站", "Yoho Mall")
    mask = google.last_field_mask
    assert mask                              # a call with no mask is billed for everything
    assert "*" not in mask
    # The two free-text / large surfaces this API can return, both unrendered,
    # both dearer — never requested.
    assert "navigationInstruction" not in mask
    assert "polyline" not in mask
    assert "routes.duration" in mask
    assert "transitDetails" in mask


# ---- arrive_by: the first time_shiftable tool --------------------------------

async def test_arrive_by_is_sent_as_arrival_time_for_transit(google):
    await google.client.route(
        "天水圍站", "Yoho Mall", mode="TRANSIT", arrive_by="2026-08-07T20:00:00+08:00",
    )
    assert google.last_routes_body["arrivalTime"] == "2026-08-07T20:00:00+08:00"
    assert google.last_routes_body["travelMode"] == "TRANSIT"


async def test_arrive_by_is_ignored_for_driving(google):
    """`arrivalTime` is a transit-only field on this API; sending it on a DRIVE
    request would be rejected, so it is set only where it means something."""
    await google.client.route(
        "天水圍站", "Yoho Mall", mode="DRIVE", arrive_by="2026-08-07T20:00:00+08:00",
    )
    assert "arrivalTime" not in google.last_routes_body
    assert google.last_routes_body["travelMode"] == "DRIVE"


# ---- transit sub-preferences: the intent a person names (audit 2026-08-07) ---

async def test_a_named_transit_sub_mode_becomes_allowed_travel_modes(google):
    """「by MTR」 restricts to heavy rail — SUBWAY+TRAIN, NOT `RAIL` (which the
    docs define as SUBWAY+TRAIN+LIGHT_RAIL, and would pull 輕鐵 into an MTR
    request). Revert the `transitPreferences` block in `_compute` and this goes
    red."""
    await google.client.route("天水圍站", "Yoho Mall", by="mtr")
    assert google.last_routes_body["transitPreferences"]["allowedTravelModes"] == [
        "SUBWAY", "TRAIN",
    ]


async def test_tram_and_light_rail_both_map_to_light_rail(google):
    """Google's own grouping: `LIGHT_RAIL` is documented "light rail or tram",
    and there is no TRAM enum. 電車/叮叮 and 輕鐵 both land here; HK keeps them
    geographically disjoint so the restriction resolves per area."""
    await google.client.route("天水圍站", "Yoho Mall", by="tram")
    assert google.last_routes_body["transitPreferences"]["allowedTravelModes"] == ["LIGHT_RAIL"]
    await google.client.route("天水圍站", "Yoho Mall", by="light_rail")
    assert google.last_routes_body["transitPreferences"]["allowedTravelModes"] == ["LIGHT_RAIL"]


async def test_an_unknown_sub_mode_is_dropped_not_sent(google):
    """The `_MODES` fallback shape: an out-of-set keyword maps to nothing and the
    request stays all-modes, never an error and never a bad enum reaching Google."""
    await google.client.route("天水圍站", "Yoho Mall", by="hovercraft")
    assert "transitPreferences" not in google.last_routes_body


async def test_a_routing_preference_maps(google):
    await google.client.route("天水圍站", "Yoho Mall", prefer="less_walking")
    assert google.last_routes_body["transitPreferences"]["routingPreference"] == "LESS_WALKING"
    await google.client.route("天水圍站", "Yoho Mall", prefer="fewer_transfers")
    assert google.last_routes_body["transitPreferences"]["routingPreference"] == "FEWER_TRANSFERS"


async def test_transit_preferences_are_omitted_for_driving(google):
    """`transitPreferences` is transit-only; Google rejects it on DRIVE. Even with
    both sub-fields named, a DRIVE request carries none."""
    await google.client.route(
        "天水圍站", "Yoho Mall", mode="DRIVE", by="mtr", prefer="less_walking",
    )
    assert "transitPreferences" not in google.last_routes_body


async def test_depart_after_is_sent_as_departure_time(google):
    """「八點出發」 is a different question from 「八點到」 — it sets `departureTime`,
    not `arrivalTime`."""
    await google.client.route(
        "天水圍站", "Yoho Mall", depart_after="2026-08-07T18:00:00+08:00",
    )
    assert google.last_routes_body["departureTime"] == "2026-08-07T18:00:00+08:00"
    assert "arrivalTime" not in google.last_routes_body


async def test_arrive_by_wins_when_both_times_are_given(google):
    """They are mutually exclusive on this API; arrive-by is the more specific
    intent, so depart-after is dropped rather than sent alongside it."""
    await google.client.route(
        "天水圍站", "Yoho Mall",
        arrive_by="2026-08-07T20:00:00+08:00", depart_after="2026-08-07T18:00:00+08:00",
    )
    assert google.last_routes_body["arrivalTime"] == "2026-08-07T20:00:00+08:00"
    assert "departureTime" not in google.last_routes_body


# ---- the resolver: ambiguity is an answer, not a guess -----------------------

async def test_resolver_returns_one_place_for_a_specific_name(google):
    res = await google.client._resolver.resolve("天水圍站")
    assert res.status == "ok"
    assert res.place is not None


async def test_colocated_points_are_one_place_not_an_ambiguity(google):
    """YOHO Mall's two entrances are ~40 m apart with the same name — one place,
    the same discipline transit's `same_place` applies to a terminus's poles."""
    res = await google.client._resolver.resolve("Yoho Mall")
    assert res.status == "ok"


async def test_a_scattered_category_is_candidates_not_a_guess(google):
    """打邊爐 is a CATEGORY — hotpot places across several districts — so it
    returns candidates for the model to ask about, never a silent pick
    (design/14 §5.2)."""
    res = await google.client._resolver.resolve("打邊爐")
    assert res.status == "ambiguous"
    assert set(res.could_be) == {"旺角打邊爐", "元朗打邊爐", "銅鑼灣打邊爐"}


async def test_a_named_landmark_with_a_bilingual_duplicate_resolves_to_the_top(google):
    """香港迪士尼樂園 returns its Chinese and English names as two points ~400 m
    apart — the SAME resort, not a fork. A single far runner-up resolves to the
    top hit rather than a spurious «which one» (the live 2026-08-07 shape that
    `MIN_AMBIGUOUS_OTHERS` exists for)."""
    res = await google.client._resolver.resolve("迪士尼")
    assert res.status == "ok"
    assert res.place is not None and res.place.name == "香港迪士尼樂園"


async def test_a_name_with_no_match_is_not_found_not_unavailable(google):
    res = await google.client._resolver.resolve("no-such-place-anywhere")
    assert res.status == "not_found"


async def test_a_resolver_transport_failure_is_unavailable(google):
    google.places_status = 500
    res = await google.client._resolver.resolve("Yoho Mall")
    assert res.status == "unavailable"


async def test_an_ambiguous_endpoint_makes_the_route_no_route_with_candidates(google):
    out = await google.client.route("天水圍站", "打邊爐")
    assert out["status"] == "no_route"
    assert set(out["destination_could_be"]) == {"旺角打邊爐", "元朗打邊爐", "銅鑼灣打邊爐"}
    assert "routes" not in google.calls      # never guessed a point to send


async def test_the_client_never_raises_into_the_agent_loop(google):
    """A raising collaborator costs the whole reply, so `RouteClient.route`
    degrades to `unavailable` rather than propagating — the contract its
    docstring states (R-5). The real `PlaceResolver` never raises; this proves
    the backstop for the case where some future collaborator does."""
    class _Exploding:
        async def resolve(self, query: str):
            raise RuntimeError("places is on fire")

    client = RouteClient(api_key="k", resolver=_Exploding())        # type: ignore[arg-type]
    out = await client.route("a", "b")
    assert out["status"] == "unavailable"


# ---- the tool, as the model sees it, and R-28 --------------------------------

def _tool_wiring(route_source):
    """`build_tools` with everything except route stubbed out.

    `persons` carries one known person — Tyler, alias 陳生 — because R-28's
    person block screens origin/destination against the whole address book, and
    a fake with nobody in it could not exercise the block."""
    from nora.agent.room import Room
    from nora.agent.tools import build_tools
    from nora.domain.models import PersonRef
    from nora.obs.metrics import Metrics
    from nora.persons import Persons

    tyler = PersonRef(
        person_id="p_tyler", name="Tyler", honcho_peer_id="p_tyler",
        first_name="Tyler", aliases=("陳生", "阿豹"),
    )
    persons = Persons(by_person={"p_tyler": tyler}, index={})
    metrics = Metrics()
    tools = build_tools(
        store=None, conversation_id="g1", conversation_type="group",
        persons=persons, honcho=None, deliverer=None, metrics=metrics,
        room=Room(platform="signal", conversation_id="g1", roster_source=None,
                  persons=persons),
        transit_source=None, route_source=route_source,
        question="點去 Yoho", context_window=40,
    )
    return {"route": {t.tool_name: t for t in tools}["route"], "metrics": metrics}


async def _call(tool, **kwargs):
    return await tool._tool_func(**kwargs)


class _RecordingClient:
    """A route client that records whether it was reached — so a test can prove
    a blocked query touched NOTHING."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def route(self, origin, destination, mode="TRANSIT", arrive_by="", **_kw):
        self.calls.append((origin, destination))
        return {"status": "ok", "origin": origin, "destination": destination,
                "mode": mode, "duration_seconds": 100, "distance_meters": 500,
                "legs": [], "note": "ok"}


async def test_a_route_naming_a_person_is_refused_before_egress():
    """R-28. A route query with a person's name or nickname in origin/
    destination is refused BEFORE any HTTP call — 「由陳生屋企去我公司」 must
    never resolve a person to a home address and send it to Google (design/14
    §2.2). Revert the person block in `nora.agent.tools.route` and this reaches
    the client instead: the assertion that `client.calls` is empty is exactly
    the probe.
    """
    client = _RecordingClient()
    wiring = _tool_wiring(client)

    # 陳生 is Tyler's alias; the origin is phrased AROUND it, as a home address is.
    out = await _call(wiring["route"], origin="陳生屋企", destination="Yoho Mall")
    assert out["status"] == "refused"
    assert client.calls == []                       # it reached NOTHING
    # The refusal names neither the person nor which argument it was.
    assert "陳生" not in json.dumps(out, ensure_ascii=False)

    # And a route between two PLACES does reach the client — the block is not a
    # blanket refusal of routing.
    ok = await _call(wiring["route"], origin="天水圍站", destination="Yoho Mall")
    assert ok["status"] == "ok"
    assert client.calls == [("天水圍站", "Yoho Mall")]


async def test_an_identifier_in_a_route_argument_is_refused_before_egress():
    """The shared floor: a phone number in origin/destination never leaves."""
    client = _RecordingClient()
    wiring = _tool_wiring(client)
    out = await _call(wiring["route"], origin="+85290000101", destination="Yoho Mall")
    assert out["status"] == "refused"
    assert client.calls == []


async def test_the_tool_counts_the_states_apart(google):
    wiring = _tool_wiring(google.client)

    async def _status(**kw):
        return (await _call(wiring["route"], **kw))["status"]

    assert await _status(origin="天水圍站", destination="Yoho Mall") == "ok"
    google.no_route = True
    assert await _status(origin="天水圍站", destination="孤島") == "no_route"
    google.routes_status = 500
    google.no_route = False
    assert await _status(origin="天水圍站", destination="Yoho Mall") == "unavailable"
    for status in ("ok", "no_route", "unavailable"):
        assert wiring["metrics"].get(
            "nora_tool_calls_total", tool="route", status=status,
        ) == 1, status


async def test_the_tool_is_unavailable_when_no_client_is_wired():
    """Every offline/eval composition and any deployment with no Google key
    leaves `route_source=None`; the tool answers `unavailable`, never a silent
    empty (R-5) — and never leaks, since the R-28 screen runs first."""
    wiring = _tool_wiring(None)
    out = await _call(wiring["route"], origin="天水圍站", destination="Yoho Mall")
    assert out["status"] == "unavailable"
    # A person is still refused even with no client — the guard is first.
    refused = await _call(wiring["route"], origin="阿豹", destination="Yoho Mall")
    assert refused["status"] == "refused"


async def test_the_tool_never_raises_into_the_agent_loop():
    class _Exploding:
        async def route(self, origin, destination, mode="TRANSIT", arrive_by="", **_kw):
            raise RuntimeError("route is on fire")

    wiring = _tool_wiring(_Exploding())
    out = await _call(wiring["route"], origin="天水圍站", destination="Yoho Mall")
    assert out["status"] == "unavailable"


async def test_the_tool_screens_an_identifier_out_of_a_candidate_list():
    """Candidates are Google-authored place names rendered on their way to a
    reply; an identifier among them is dropped, the same screen `transit`
    applies to `near.could_be`."""
    class _Candidates:
        async def route(self, origin, destination, mode="TRANSIT", arrive_by="", **_kw):
            return {"status": "no_route", "origin": origin, "destination": destination,
                    "destination_could_be": ["元朗打邊爐", "+85290000101"], "note": "n"}

    wiring = _tool_wiring(_Candidates())
    out = await _call(wiring["route"], origin="天水圍站", destination="打邊爐")
    assert out["destination_could_be"] == ["元朗打邊爐"]
