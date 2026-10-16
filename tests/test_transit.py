"""`transit` — live bus arrivals from two operators (DEC-40, R-27).

Every scenario here is offline, against a local `aiohttp` server that behaves like
the two real endpoints — including the shapes they return for the WRONG call.
That is not decoration. This is a TWO-OPERATOR tool, and a fake answering both
the same way proves nothing: KMB answers `200 {"data": []}` for a route MTR
runs, which is byte-identical to "no buses". So the fake below serves KMB only
over `GET /v1/transport/kmb/route-eta/...` and MTR only over `POST
.../mtr/bus/getSchedule`, and answers the other verb the way the real hosts do
— `405`/`404` — so a client that asked the wrong operator would fail rather
than quietly agree.

The payload fixtures are trimmed copies of live responses captured
(K73 at 23:15 HKT, 68X at 23:16 HKT), field names and value TYPES verbatim from
the wire: `service_type` is a string in the KMB route list and an int in a KMB
ETA row; every MTR number is a string; `status` is `"0"` on both a real route
and a nonexistent one.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.infra.transit import TransitClient

# ---- what the two operators really return -----------------------------------

# The KMB route list is the operator resolver: "68X" present and "K73" absent.
# `service_type` is a STRING here and an int in an ETA row — that divergence is
# on the wire, not a transcription slip.
KMB_ROUTES = [
    {"route": "68X", "bound": "O", "service_type": "1",
     "orig_en": "HUNG SHUI KIU (HUNG FUK ESTATE)", "dest_en": "MONG KOK (PARK AVENUE)"},
    {"route": "68X", "bound": "O", "service_type": "3",
     "orig_en": "YUEN LONG (WEST)", "dest_en": "MONG KOK (PARK AVENUE)"},
    {"route": "68X", "bound": "I", "service_type": "1",
     "orig_en": "MONG KOK (PARK AVENUE)", "dest_en": "HUNG SHUI KIU (HUNG FUK ESTATE)"},
    # A real KMB route whose ETA response below is EMPTY — the "route exists,
    # nothing running" case that must not read as "no such route".
    {"route": "N48", "bound": "I", "service_type": "1",
     "orig_en": "TSUEN WAN", "dest_en": "YU CHUI COURT"},
    # The route the co-location case rides on: calls at two poles of the
    # 天恆邨總站 terminus, one per bound (see KMB_ROUTE_STOPS).
    {"route": "265M", "bound": "O", "service_type": "1",
     "orig_en": "TIN HENG ESTATE", "dest_en": "KWAI CHUNG (LAI YIU ESTATE)"},
]

KMB_68X_ETA = {
    "type": "RouteETA", "version": "1.0",
    "generated_timestamp": "2026-08-06T23:16:39+08:00",
    "data": [
        {"co": "KMB", "route": "68X", "dir": "O", "service_type": 1, "seq": 1,
         "dest_tc": "旺角(柏景灣)", "dest_en": "MONG KOK (PARK AVENUE)", "eta_seq": 1,
         "eta": "2026-08-06T23:25:00+08:00", "rmk_en": "Scheduled Bus",
         "data_timestamp": "2026-08-06T23:16:16+08:00"},
        {"co": "KMB", "route": "68X", "dir": "O", "service_type": 1, "seq": 1,
         "dest_tc": "旺角(柏景灣)", "dest_en": "MONG KOK (PARK AVENUE)", "eta_seq": 2,
         "eta": "2026-08-06T23:55:00+08:00", "rmk_en": "Scheduled Bus",
         "data_timestamp": "2026-08-06T23:16:16+08:00"},
        {"co": "KMB", "route": "68X", "dir": "I", "service_type": 1, "seq": 1,
         "dest_tc": "洪水橋(洪福邨)", "dest_en": "HUNG SHUI KIU (HUNG FUK ESTATE)",
         "eta_seq": 1, "eta": "2026-08-06T23:40:00+08:00", "rmk_en": "",
         "data_timestamp": "2026-08-06T23:16:16+08:00"},
        # A stop further down the route. It must not be mistaken for an origin.
        {"co": "KMB", "route": "68X", "dir": "O", "service_type": 1, "seq": 7,
         "dest_tc": "旺角(柏景灣)", "dest_en": "MONG KOK (PARK AVENUE)", "eta_seq": 1,
         "eta": "2026-08-06T23:31:00+08:00", "rmk_en": "",
         "data_timestamp": "2026-08-06T23:16:16+08:00"},
    ],
}

# `eta: null` with the reason in a free-text remark — the shape the KMB spec's
# own sample uses for a route that is not running today.
KMB_N48_ETA = {
    "type": "RouteETA", "version": "1.0",
    "generated_timestamp": "2026-08-06T23:16:39+08:00",
    "data": [
        {"co": "KMB", "route": "N48", "dir": "I", "service_type": 1, "seq": 1,
         "dest_en": "YU CHUI COURT", "eta_seq": 1, "eta": None,
         "rmk_en": "This route only operates on Sundays and Public Holidays",
         "data_timestamp": "2026-08-06T23:16:16+08:00"},
    ],
}

MTR_ROUTES_CSV = (
    "﻿ROUTE_ID,ROUTE_NAME_CHI,ROUTE_NAME_ENG,IS_CIRCULAR,LINE_UP,LINE_DOWN,REFERENCE_ID\n"
    "K73,天恆至元朗西,Tin Heng to Yuen Long West,0,K73_YLW,K73_TH,K73\n"
    "K74,天水圍市中心至凹頭 (循環線),Tin Shui Chi Town Centre to Au Tau (Circular),1,"
    "K74_POH_CIR,,K74\n"
)

# ---- the stop datasets the place resolver reads (DEC-41) ---------------------

# Trimmed copies of the live KMB stop list (probed live). Names, ids and
# coordinates verbatim from the wire, including the two shapes that make
# matching interesting: every KMB name carries a "(TN970)"-style stop
# code — so 「天恆」 can never EXACT-match a KMB stop — and the three 天恆邨總站
# poles are the same terminus, metres apart, while the three 大學-suffixed
# campuses are in three different districts.
KMB_STOPS = [
    {"stop": "105E0AF98899CAEE", "name_en": "TIN HENG ESTATE BUS TERMINUS (TN970)",
     "name_tc": "天恆邨總站 (TN970)", "name_sc": "天恒邨总站 (TN970)",
     "lat": "22.469374", "long": "113.999642"},
    {"stop": "A22733425D08E68A", "name_en": "TIN HENG ESTATE BUS TERMINUS (TN971)",
     "name_tc": "天恆邨總站 (TN971)", "name_sc": "天恒邨总站 (TN971)",
     "lat": "22.469439", "long": "113.999680"},
    {"stop": "223DAE7E925E3BB9", "name_en": "TIN HENG ESTATE BUS TERMINUS (TN972)",
     "name_tc": "天恆邨總站 (TN972)", "name_sc": "天恒邨总站 (TN972)",
     "lat": "22.469471", "long": "113.999708"},
    {"stop": "6D66CBA494DECB08", "name_en": "HUNG SHUI KIU (HUNG FUK ESTATE) BUS TERMINUS",
     "name_tc": "洪水橋(洪福邨)總站 (YL900)", "name_sc": "洪水桥(洪福邨)总站 (YL900)",
     "lat": "22.436706", "long": "113.998677"},
    {"stop": "147D513A708AA741", "name_en": "FUI SHA WAI (YL353)",
     "name_tc": "灰沙圍 (YL353)", "name_sc": "灰沙围 (YL353)",
     "lat": "22.438957", "long": "114.005297"},
    {"stop": "EC19750840FFEDDC", "name_en": "MONG KOK (PARK AVENUE) BUS TERMINUS (MK912)",
     "name_tc": "旺角(柏景灣)總站 (MK912)", "name_sc": "旺角(柏景湾)总站 (MK912)",
     "lat": "22.315830", "long": "114.163112"},
    {"stop": "548DB838B4673D93", "name_en": "UNIVERSITY STATION (ST903)",
     "name_tc": "大學站 (ST903)", "name_sc": "大学站 (ST903)",
     "lat": "22.412578", "long": "114.210303"},
    {"stop": "AA5DA45828958EAE", "name_en": "LINGNAN UNIVERSITY (TM290)",
     "name_tc": "嶺南大學 (TM290)", "name_sc": "岭南大学 (TM290)",
     "lat": "22.409975", "long": "113.982404"},
    {"stop": "558599642E78BB9B", "name_en": "H.K. POLYTECHNIC UNIVERSITY (YT620)",
     "name_tc": "香港理工大學 (YT620)", "name_sc": "香港理工大学 (YT620)",
     "lat": "22.410600", "long": "114.124500"},
]

# Route-stop rows: `seq` is a STRING on the live wire ("1") where the spec's own
# sample shows an int — the same intra-spec divergence the route
# list has for `service_type`, kept verbatim so the parser is tested against
# what the host actually sends.
KMB_ROUTE_STOPS = {
    ("68X", "outbound"): [
        {"route": "68X", "bound": "O", "service_type": "1", "seq": "1",
         "stop": "6D66CBA494DECB08"},
        {"route": "68X", "bound": "O", "service_type": "1", "seq": "7",
         "stop": "147D513A708AA741"},
    ],
    ("68X", "inbound"): [
        {"route": "68X", "bound": "I", "service_type": "1", "seq": "1",
         "stop": "EC19750840FFEDDC"},
    ],
    # 265M calls at TWO poles of the same terminus — TN972 outbound, TN970
    # inbound — which is the co-location case: one place, two stop ids.
    ("265M", "outbound"): [
        {"route": "265M", "bound": "O", "service_type": "1", "seq": "1",
         "stop": "223DAE7E925E3BB9"},
    ],
    ("265M", "inbound"): [
        {"route": "265M", "bound": "I", "service_type": "1", "seq": "20",
         "stop": "105E0AF98899CAEE"},
    ],
}

# Per-stop ETA (`GET /v1/transport/kmb/eta/{stop_id}/{route}/{service_type}`,
# verified live 23:58 HKT). Same row shape as
# route-eta; every row is already at the asked-for stop.
KMB_STOP_ETA = {
    ("147D513A708AA741", "68X"): [
        # The very row the origin logic must EXCLUDE (seq 7, 23:31) is the
        # answer when somebody says they are at 灰沙圍.
        {"co": "KMB", "route": "68X", "dir": "O", "service_type": 1, "seq": 7,
         "dest_tc": "旺角(柏景灣)", "dest_en": "MONG KOK (PARK AVENUE)", "eta_seq": 1,
         "eta": "2026-08-06T23:31:00+08:00", "rmk_en": "",
         "data_timestamp": "2026-08-06T23:16:16+08:00"},
    ],
    ("223DAE7E925E3BB9", "265M"): [
        {"co": "KMB", "route": "265M", "dir": "O", "service_type": 1, "seq": 1,
         "dest_tc": "葵涌(麗瑤邨)", "dest_en": "KWAI CHUNG (LAI YIU ESTATE)", "eta_seq": 1,
         "eta": "2026-08-06T23:30:00+08:00", "rmk_en": "Scheduled Bus",
         "data_timestamp": "2026-08-06T23:16:16+08:00"},
    ],
    ("105E0AF98899CAEE", "265M"): [
        {"co": "KMB", "route": "265M", "dir": "I", "service_type": 1, "seq": 20,
         "dest_tc": "天恆邨", "dest_en": "TIN HENG ESTATE", "eta_seq": 1,
         "eta": "2026-08-06T23:26:00+08:00", "rmk_en": "",
         "data_timestamp": "2026-08-06T23:16:16+08:00"},
    ],
}

# Trimmed copy of opendata.mtr.com.hk/data/mtr_bus_stops.csv (probed
# live): BOM, quoted names carrying commas, and STATION_ID in exactly the
# format `getSchedule` uses for `busStopId` — that equality is the join key the
# resolver stands on. 輕鐵 appears in two stops ~2.2 km apart, BOTH on K73: the
# honest on-route ambiguity case.
MTR_STOPS_CSV = (
    "﻿ROUTE_ID,DIRECTION,STATION_SEQNO,STATION_ID,STATION_LATITUDE,"
    "STATION_LONGITUDE,STATION_NAME_CHI,STATION_NAME_ENG,REFERENCE_ID\n"
    "K73,O,1,K73-U010,22.469006,113.999661,天恆,Tin Heng,K73\n"
    'K73,O,2,K73-U020,22.468098,113.999163,天逸邨逸潭樓,"Yat Tam House, Tin Yat Estate",K73\n'
    "K73,I,1,K73-D010,22.445181,114.024919,元朗西 (輕鐵豐年路站),"
    "Yuen Long West (LR Ftpf Nqi Road Stop),K73\n"
    "K73,I,9,K73-D090,22.453358,114.00589,輕鐵天慈站,LR Tin Tsz Stop,K73\n"
    "K73,I,16,K73-D160,22.469006,113.999661,天恆,Tin Heng,K73\n"
    "K74,O,1,K74-U010,22.4619,114.0049,天水圍市中心,Tin Shui Chi Town Centre,K74\n"
)


def _mtr_bus(*, arrival: str, departure: str, scheduled: str, text: str, line: str) -> dict:
    return {
        "arrivalTimeInSecond": arrival, "arrivalTimeText": text, "busId": "804",
        "busLocation": {"latitude": 0, "longitude": 0}, "busRemark": None,
        "departureTimeInSecond": departure, "departureTimeText": text,
        "isDelayed": "0", "isScheduled": scheduled, "lineRef": line,
    }


MTR_K73 = {
    "appRefreshTimeInSecond": "60",
    "busStop": [
        # The ORIGIN of the K73_TH direction: `arrivalTimeInSecond` is the
        # documented 108000 sentinel ("no arrival time text for first stops of
        # non-circular route"), so only the DEPARTURE means anything here.
        {"bus": [_mtr_bus(arrival="108000", departure="545", scheduled="1", text="",
                          line="K73_TH"),
                 _mtr_bus(arrival="108000", departure="1145", scheduled="1", text="",
                          line="K73_TH")],
         "busIcon": None, "busStopId": "K73-D010", "busStopRemark": None,
         "busStopStatus": None, "busStopStatusRemarkContent": None,
         "busStopStatusRemarkTitle": None, "busStopStatusTime": None, "isSuspended": "0"},
        {"bus": [_mtr_bus(arrival="24", departure="67", scheduled="0",
                          text="Arriving / Departed", line="K73_TH")],
         "busIcon": None, "busStopId": "K73-D020", "busStopRemark": None,
         "busStopStatus": None, "busStopStatusRemarkContent": None,
         "busStopStatusRemarkTitle": None, "busStopStatusTime": None, "isSuspended": "0"},
        {"bus": [_mtr_bus(arrival="108000", departure="245", scheduled="1", text="",
                          line="K73_YLW"),
                 # Already gone: the real API returns negative departures.
                 _mtr_bus(arrival="108000", departure="-103", scheduled="0", text="",
                          line="K73_YLW")],
         "busIcon": None, "busStopId": "K73-U010", "busStopRemark": None,
         "busStopStatus": None, "busStopStatusRemarkContent": None,
         "busStopStatusRemarkTitle": None, "busStopStatusTime": None, "isSuspended": "0"},
        # 天恆 as the inbound TERMINUS (K73-D160): here `arrivalTimeInSecond`
        # is real — the live probe at 23:58 showed a bus at the stop (arrival
        # "0", departure already negative) and a tracked one 527 s out — so at
        # a named stop the ARRIVAL is the answer, not the departure.
        {"bus": [_mtr_bus(arrival="0", departure="-59", scheduled="1", text="",
                          line="K73_TH"),
                 _mtr_bus(arrival="527", departure="527", scheduled="0",
                          text="9 minutes", line="K73_TH")],
         "busIcon": None, "busStopId": "K73-D160", "busStopRemark": None,
         "busStopStatus": None, "busStopStatusRemarkContent": None,
         "busStopStatusRemarkTitle": None, "busStopStatusTime": None, "isSuspended": "0"},
    ],
    "caseNumber": 0, "caseNumberDetail": "",
    "footerRemarks": "System testing in progress.  Estimated arrival time may vary from "
                     "actual arrival time.",
    "routeColour": "", "routeName": "K73", "routeStatus": "0", "routeStatusColour": "black",
    "routeStatusRemarkContent": None, "routeStatusRemarkFooterRemark": "",
    "routeStatusRemarkTitle": None,
    # NOT RFC 3339, and not even the format MTR's own data dictionary claims
    # ("yyyy-MM-dd HH:mm"). Slashes, minute precision, no offset.
    "routeStatusTime": "2026/08/06 23:15", "status": "0",
}

# THE TRAP, captured live. This is what MTR returns for `ZZZ999` — and, at
# 23:15, for K12, K14, K51A, K52A, K52P, K53S, K58, K65A, K74,
# K75S and K76S, every one of them a REAL published route that had simply
# stopped running for the night. The envelope is identical. Nothing inside it
# distinguishes "no such route" from "no service", so nothing inside it may be
# used to.
MTR_NO_SERVICE = {
    "appRefreshTimeInSecond": "60", "busStop": [], "caseNumber": 1, "caseNumberDetail": "",
    "footerRemarks": None, "routeColour": "grey", "routeName": "ZZZ999", "routeStatus": "1",
    "routeStatusColour": "black", "routeStatusRemarkContent": "",
    "routeStatusRemarkFooterRemark": "",
    "routeStatusRemarkTitle": "Arrival time temporarily not available",
    "routeStatusTime": "2026/08/06 23:15", "status": "0",
}


# ---- a server that behaves like the two real ones ---------------------------

class _Operators:
    """Both endpoints, each answering only its own verb and path.

    `calls` records what was actually asked, so a test can prove which operator
    a route reached rather than inferring it from the answer.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.kmb_status = 200
        self.mtr_status = 200
        self.csv_status = 200
        self.stops_status = 200      # the two STOP datasets fail together, like csv_status
        self.stop_eta_empty = False  # a resolved stop with nothing showing at it
        self.delay = 0.0

    async def kmb_route_list(self, request: web.Request) -> web.Response:
        self.calls.append("kmb:routes")
        if self.csv_status != 200:      # the two catalogues fail together in one test
            return web.Response(status=self.csv_status)
        await asyncio.sleep(self.delay)
        return web.json_response({
            "type": "RouteList", "version": "1.0",
            "generated_timestamp": "2026-08-06T23:16:16+08:00", "data": KMB_ROUTES,
        })

    async def kmb_route_eta(self, request: web.Request) -> web.Response:
        route = request.match_info["route"]
        self.calls.append(f"kmb:eta:{route}/{request.match_info['service_type']}")
        if self.kmb_status != 200:
            return web.Response(status=self.kmb_status)
        body = {"type": "RouteETA", "version": "1.0",
                "generated_timestamp": "2026-08-06T23:16:39+08:00", "data": []}
        if route == "68X":
            body = KMB_68X_ETA
        elif route == "N48":
            body = KMB_N48_ETA
        return web.json_response(body)

    async def mtr_routes_csv(self, request: web.Request) -> web.Response:
        self.calls.append("mtr:routes")
        if self.csv_status != 200:
            return web.Response(status=self.csv_status)
        await asyncio.sleep(self.delay)
        return web.Response(text=MTR_ROUTES_CSV, content_type="text/csv")

    async def kmb_stop_list(self, request: web.Request) -> web.Response:
        self.calls.append("kmb:stops")
        if self.stops_status != 200:
            return web.Response(status=self.stops_status)
        return web.json_response({
            "type": "StopList", "version": "1.0",
            "generated_timestamp": "2026-08-06T23:16:16+08:00", "data": KMB_STOPS,
        })

    async def kmb_route_stop(self, request: web.Request) -> web.Response:
        route = request.match_info["route"]
        bound = request.match_info["bound"]
        self.calls.append(f"kmb:route-stop:{route}/{bound}")
        # The live host answers 200 with an empty list for a route it does not
        # know, exactly like route-eta — never a 404.
        return web.json_response({
            "type": "RouteStop", "version": "1.0",
            "generated_timestamp": "2026-08-06T23:16:16+08:00",
            "data": KMB_ROUTE_STOPS.get((route, bound), []),
        })

    async def kmb_stop_specific_eta(self, request: web.Request) -> web.Response:
        stop = request.match_info["stop_id"]
        route = request.match_info["route"]
        self.calls.append(f"kmb:stopeta:{stop}/{route}")
        if self.kmb_status != 200:
            return web.Response(status=self.kmb_status)
        rows = [] if self.stop_eta_empty else KMB_STOP_ETA.get((stop, route), [])
        return web.json_response({
            "type": "ETA", "version": "1.0",
            "generated_timestamp": "2026-08-06T23:16:39+08:00", "data": rows,
        })

    async def mtr_stops_csv(self, request: web.Request) -> web.Response:
        self.calls.append("mtr:stops")
        if self.stops_status != 200:
            return web.Response(status=self.stops_status)
        return web.Response(text=MTR_STOPS_CSV, content_type="text/csv")

    async def mtr_schedule(self, request: web.Request) -> web.Response:
        # The real endpoint is POST-only and reads a JSON body. A GET here is a
        # 405, which is what makes "asked the wrong operator" a visible failure.
        if request.method != "POST":
            return web.Response(status=405)
        payload = await request.json()
        route = payload.get("routeName", "")
        self.calls.append(f"mtr:eta:{route}")
        if self.mtr_status != 200:
            return web.Response(status=self.mtr_status)
        if route == "K73":
            return web.json_response(MTR_K73)
        return web.json_response({**MTR_NO_SERVICE, "routeName": route})


@pytest.fixture
async def operators():
    ops = _Operators()
    app = web.Application()
    app.router.add_get("/v1/transport/kmb/route/", ops.kmb_route_list)
    app.router.add_get(
        "/v1/transport/kmb/route-eta/{route}/{service_type}", ops.kmb_route_eta,
    )
    app.router.add_get("/data/mtr_bus_routes.csv", ops.mtr_routes_csv)
    app.router.add_route("*", "/v1/transport/mtr/bus/getSchedule", ops.mtr_schedule)
    app.router.add_get("/v1/transport/kmb/stop", ops.kmb_stop_list)
    app.router.add_get(
        "/v1/transport/kmb/route-stop/{route}/{bound}/{service_type}", ops.kmb_route_stop,
    )
    app.router.add_get(
        "/v1/transport/kmb/eta/{stop_id}/{route}/{service_type}", ops.kmb_stop_specific_eta,
    )
    app.router.add_get("/data/mtr_bus_stops.csv", ops.mtr_stops_csv)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("/")).rstrip("/")
    ops.client = TransitClient(                                     # type: ignore[attr-defined]
        kmb_base_url=base,
        mtr_schedule_url=f"{base}/v1/transport/mtr/bus/getSchedule",
        mtr_routes_url=f"{base}/data/mtr_bus_routes.csv",
        mtr_stops_url=f"{base}/data/mtr_bus_stops.csv",
    )
    yield ops
    await server.close()


# ---- R-27 --------------------------------------------------------------------

async def test_an_unknown_route_is_never_reported_as_no_service(operators):
    """R-27. The trap, and the reason route validation is
    a correctness requirement rather than a nicety.

    `ZZZ999` and `K74` produce the SAME MTR envelope — 200, `status: "0"`,
    `busStop: []` — because at 23:15 K74 had stopped for the night. Both are
    also absent from the KMB list. The only thing that separates them is
    whether an operator CATALOGUE lists the number, so that is what decides,
    and the two answers must not be one sentence."""
    unknown = await operators.client.arrivals("ZZZ999")
    no_service = await operators.client.arrivals("K74")

    assert unknown["status"] == "nothing_found"
    assert no_service["status"] == "nothing_found"
    # Same status, different sentence. «There is no such route» and «that route
    # has stopped for the night» are different claims about the world and she
    # must not be able to say the first when the second is true.
    assert unknown["note"] != no_service["note"]
    assert unknown["known_route"] is False
    assert no_service["known_route"] is True


async def test_a_catalogue_we_could_not_read_is_unavailable_not_nothing_found(operators):
    """The other half of R-27. With no catalogue there is no way to tell an
    unknown route from a dead one, so there is nothing honest to say about the
    route at all — and «I could not check» is the only answer that is not a
    claim."""
    operators.csv_status = 503
    out = await operators.client.arrivals("ZZZ999")
    assert out["status"] == "unavailable"
    assert "no such" not in out["note"].lower()


# ---- two operators, two endpoints -------------------------------------------

async def test_each_route_reaches_the_operator_that_runs_it(operators):
    """68X is KMB and K73 is MTR, and the route number does not say which."""
    await operators.client.arrivals("68X")
    assert any(c.startswith("kmb:eta:68X") for c in operators.calls)
    assert not any(c.startswith("mtr:eta:") for c in operators.calls)

    operators.calls.clear()
    await operators.client.arrivals("K73")
    assert any(c.startswith("mtr:eta:K73") for c in operators.calls)
    assert not any(c.startswith("kmb:eta:") for c in operators.calls)


async def test_the_two_catalogues_are_fetched_concurrently(operators):
    """A single tool hitting two hosts one after another is dead time we own."""
    operators.delay = 0.25
    started = asyncio.get_running_loop().time()
    await operators.client.arrivals("K73")
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 0.45, f"catalogues look sequential: {elapsed:.3f}s for 2 x 0.25s"


async def test_a_dead_eta_endpoint_degrades_to_unavailable(operators):
    operators.mtr_status = 500
    out = await operators.client.arrivals("K73")
    assert out["status"] == "unavailable"


async def test_the_client_never_raises_on_a_hostile_response(operators):
    """A tool that raises costs the whole reply (R-5), so nothing here may."""
    operators.mtr_status = 418
    operators.kmb_status = 418
    for route in ("K73", "68X", "", "../../etc/passwd", "68" * 200):
        out = await operators.client.arrivals(route)
        assert out["status"] in {"ok", "nothing_found", "unavailable"}


# ---- what comes back ---------------------------------------------------------

async def test_kmb_departures_come_from_the_first_stop_and_are_rfc3339(operators):
    out = await operators.client.arrivals("68X")
    assert out["status"] == "ok"
    assert out["operator"] == "KMB"
    towards = {d["towards"]: d for d in out["directions"]}
    mong_kok = next(d for k, d in towards.items() if "MONG KOK" in k)
    # seq 7's 23:31 is a later stop on the same direction and must not appear
    # among the origin departures.
    assert [dep["at"] for dep in mong_kok["departures"]] == [
        "2026-08-06T23:25:00+08:00", "2026-08-06T23:55:00+08:00",
    ]
    assert mong_kok["departures"][0]["in_minutes"] == 8
    assert mong_kok["departures"][0]["scheduled"] is True


async def test_an_mtr_relative_second_becomes_an_rfc3339_instant(operators):
    """MTR gives seconds and a `"2026/08/06 23:15"` clock reading that is not
    RFC 3339 in any dialect. Both are normalised against the operator's OWN
    stamp, so the answer is internally consistent even if its clock drifts."""
    out = await operators.client.arrivals("K73")
    assert out["status"] == "ok"
    assert out["as_of"] == "2026-08-06T23:15:00+08:00"
    tin_heng = next(d for d in out["directions"] if "Tin Heng" in d["towards"])
    # 545 s after 23:15 is 23:24:05.
    assert tin_heng["departures"][0]["at"] == "2026-08-06T23:24:05+08:00"
    assert tin_heng["departures"][0]["in_minutes"] == 9


async def test_a_departure_that_has_already_gone_is_not_offered(operators):
    out = await operators.client.arrivals("K73")
    yuen_long = next(d for d in out["directions"] if "Yuen Long West" in d["towards"])
    assert [dep["in_minutes"] for dep in yuen_long["departures"]] == [4]


async def test_a_scheduled_time_is_not_presented_as_a_live_one(operators):
    """MTR's `isScheduled` is `"1"` for a TIMETABLE entry and `"0"` for a bus
    the system can actually see. Reporting the first as the second is a
    confident wrong answer of exactly the shape R-5 is about."""
    out = await operators.client.arrivals("K73")
    flags = {d["towards"]: [dep["scheduled"] for dep in d["departures"]]
             for d in out["directions"]}
    assert all(any(f) for f in flags.values())


async def test_a_free_text_operator_remark_is_wrapped_and_says_what_it_is(operators):
    """A typed envelope is not wrapped, but a free-text field
    inside one is. KMB's `rmk_en` is an operator-authored sentence."""
    out = await operators.client.arrivals("N48")
    assert out["status"] == "nothing_found"
    # NAMED for what it is, not `<untrusted>`: a tag shared with a fetched web
    # page and a social post would carry one description for all three, and
    # its NAME would be a word defined in a doctrine file most audiences never
    # load.
    assert out["operator_note"].startswith('<transit_remark source="KMB" ')
    assert "service remark from the operator" in out["operator_note"]
    assert "Sundays and Public Holidays" in out["operator_note"]


async def test_the_answer_is_bounded_and_says_so_when_it_truncates(operators):
    """Every tool result is re-sent on every subsequent model call, so the cost
    of one is `tokens x calls remaining`."""
    out = await operators.client.arrivals("68X")
    assert len(json.dumps(out, ensure_ascii=False)) < 1200
    assert out["truncated"] is False


# ---- the place resolver: `near` (DEC-41, R-27b) ------------------------------

async def test_near_times_come_from_the_named_stop_not_the_origin(operators):
    """「K73 幾時到天恆」. The resolver joins the MTR stops CSV to the schedule
    on STATION_ID == busStopId, so the departures are AT 天恆 — U010's 245 s
    outbound, and the inbound terminus D160 read from its ARRIVAL seconds —
    not the 545 s the origin (元朗西, D010) shows."""
    out = await operators.client.arrivals("K73", near="「天恆」")
    assert out["status"] == "ok"
    assert out["near"]["status"] == "ok"
    assert out["near"]["stop"] == "天恆"
    minutes = {d["towards"]: [dep["in_minutes"] for dep in d["departures"]]
               for d in out["directions"]}
    assert minutes["Yuen Long West"] == [4]      # U010, departure 245 s
    assert minutes["Tin Heng"] == [0, 9]         # D160, arrivals 0 s and 527 s
    assert "9" not in str(minutes["Yuen Long West"])  # D010's 545 s never shown


async def test_kmb_near_asks_the_per_stop_eta_endpoint(operators):
    """The KMB half: stop list -> route-stop intersection -> `eta/{stop}/{route}/
    {service_type}`, all verified live. 「灰沙圍」 exact-matches only
    after the "(YL353)" code is set aside, and the 23:31 row the origin logic
    must exclude (seq 7) is exactly the answer here."""
    out = await operators.client.arrivals("68X", near="灰沙圍")
    assert out["status"] == "ok"
    assert out["near"]["status"] == "ok"
    assert any(c == "kmb:stopeta:147D513A708AA741/68X" for c in operators.calls)
    (direction,) = out["directions"]
    assert direction["departures"][0]["at"] == "2026-08-06T23:31:00+08:00"


async def test_a_place_the_route_does_not_serve_is_never_no_such_place(operators):
    """R-27b — R-27's rule on the place axis. 大學站 exists (KMB, Sha Tin) but
    K73 does not go there; 銀河站 exists nowhere in either dataset. Collapsing
    the two repeats the exact trap R-27 exists for: 「K73 唔經大學站」 is a
    useful, checkable sentence, and «no such stop» said about a real place is
    a confident lie."""
    not_on_route = await operators.client.arrivals("K73", near="大學站")
    nowhere = await operators.client.arrivals("K73", near="銀河站")

    assert not_on_route["near"]["status"] == "not_on_route"
    assert nowhere["near"]["status"] == "not_found"
    assert not_on_route["note"] != nowhere["note"]
    # Neither failure costs the route answer: both fall back to the origin
    # departures the tool already knew how to give.
    assert not_on_route["status"] == "ok"
    assert nowhere["status"] == "ok"


async def test_an_ambiguous_place_is_an_answer_not_a_guess(operators):
    """輕鐵 matches two K73 stops 2.2 km apart (元朗西 輕鐵豐年路站 and 輕鐵天慈站).
    Picking one would be wrong-confident-stop — this feature's version of
    wrong-confident-answer — so the candidates come back and she asks."""
    out = await operators.client.arrivals("K73", near="輕鐵")
    assert out["near"]["status"] == "ambiguous"
    assert len(out["near"]["could_be"]) == 2
    assert set(out["near"]["could_be"]) == {"元朗西 (輕鐵豐年路站)", "輕鐵天慈站"}
    # And no stop-scoped times were invented for either candidate: the
    # directions are the origin ones the route answer always had.
    assert out["status"] == "ok"


async def test_colocated_terminus_poles_are_one_place_not_an_ambiguity(operators):
    """The three 天恆邨總站 poles are metres apart — one terminus, not three
    places. 265M calls at two of them, one per bound; both are queried, the
    third (TN971, off this route) is not."""
    out = await operators.client.arrivals("265M", near="天恆")
    assert out["near"]["status"] == "ok"
    assert any(c == "kmb:stopeta:223DAE7E925E3BB9/265M" for c in operators.calls)
    assert any(c == "kmb:stopeta:105E0AF98899CAEE/265M" for c in operators.calls)
    assert not any("A22733425D08E68A" in c for c in operators.calls)
    towards = {d["towards"] for d in out["directions"]}
    assert towards == {"KWAI CHUNG (LAI YIU ESTATE)", "TIN HENG ESTATE"}


async def test_unreadable_stop_data_is_unavailable_not_place_not_found(operators):
    """R-27's hinge, on the place axis: a directory that never loaded is not an
    empty directory, so «no stop by that name» is not a claim anyone is
    entitled to make."""
    operators.stops_status = 503
    out = await operators.client.arrivals("K73", near="天恆")
    assert out["near"]["status"] == "unavailable"
    assert out["status"] == "ok"        # the route answer itself still stands
    assert "no stop" not in out["note"].lower()


async def test_a_resolved_stop_with_nothing_showing_is_its_own_sentence(operators):
    """«That stop is real and on this route, and nothing is showing there» is a
    third sentence — not «no such stop» and not «no such route»."""
    operators.stop_eta_empty = True
    out = await operators.client.arrivals("68X", near="灰沙圍")
    assert out["status"] == "nothing_found"
    assert out["known_route"] is True
    assert out["near"]["status"] == "ok"
    assert out["note"] != (await operators.client.arrivals("ZZZ999"))["note"]


async def test_a_near_answer_is_still_bounded(operators):
    out = await operators.client.arrivals("K73", near="天恆")
    assert len(json.dumps(out, ensure_ascii=False)) < 1400


# ---- the tool, as the model sees it -----------------------------------------

def _tool_wiring(client: TransitClient) -> dict:
    """`build_tools` with everything except transit stubbed out to nothing.

    The other collaborators are the ones `tests/test_tools.py` already covers;
    what is exercised here is the one tool that has a collaborator outside the
    host, against a server that behaves like the two real operators.
    """
    from nora.agent.room import Room
    from nora.agent.tools import build_tools
    from nora.obs.metrics import Metrics
    from nora.persons import Persons

    metrics = Metrics()
    tools = build_tools(
        store=None, conversation_id="g1", conversation_type="group",
        persons=Persons(by_person={}, index={}), honcho=None, deliverer=None,
        metrics=metrics,
        room=Room(platform="signal", conversation_id="g1", roster_source=None,
                  persons=Persons(by_person={}, index={})),
        transit_source=client, question="K73 幾時到", context_window=40,
    )
    return {"transit": {t.tool_name: t for t in tools}["transit"], "metrics": metrics}


async def _call(tool, **kwargs):
    """Invoke the underlying function of a @tool-decorated closure."""
    return await tool._tool_func(**kwargs)


async def test_the_tool_returns_the_three_states_and_counts_each_apart(operators):
    wiring = _tool_wiring(operators.client)
    assert (await _call(wiring["transit"], route="K73"))["status"] == "ok"
    assert (await _call(wiring["transit"], route="ZZZ999"))["status"] == "nothing_found"
    operators.mtr_status = 503
    assert (await _call(wiring["transit"], route="K73"))["status"] == "unavailable"

    # `{tool, status}`. A single `nora_tool_calls_total{tool="transit"}` would
    # make "the API is down" and "no such bus" one number.
    for status in ("ok", "nothing_found", "unavailable"):
        assert wiring["metrics"].get(
            "nora_tool_calls_total", tool="transit", status=status,
        ) == 1, status


async def test_the_tool_never_raises_into_the_agent_loop(operators):
    """A raising tool costs the whole reply, so a broken client degrades."""
    class _Exploding:
        async def arrivals(self, route: str) -> dict:
            raise RuntimeError("transit is on fire")

    wiring = _tool_wiring(_Exploding())          # type: ignore[arg-type]
    out = await _call(wiring["transit"], route="K73")
    assert out["status"] == "unavailable"
    assert wiring["metrics"].get(
        "nora_tool_calls_total", tool="transit", status="unavailable",
    ) == 1


async def test_the_tool_does_not_echo_an_identifier_back_at_the_model(operators):
    """The `_asked_about`/`calculate` hole, in the third place a MODEL-SUPPLIED
    string is handed straight back. An identifier inside a tool result costs
    the whole reply at R-18, which fails closed on the message rather than on
    the field."""
    class _Parroting:
        async def arrivals(self, route: str, near: str = "") -> dict:
            return {"status": "nothing_found", "route": route, "known_route": False,
                    "note": "no such route"}

    wiring = _tool_wiring(_Parroting())          # type: ignore[arg-type]
    assert "route" not in await _call(wiring["transit"], route="+85290000101")
    assert (await _call(wiring["transit"], route="68X"))["route"] == "68X"


async def test_the_tool_passes_near_through_to_the_resolver(operators):
    """`near` is a real model decision — the user named a place — so it is in
    the schema (DEC-24) and reaches the resolver as said."""
    wiring = _tool_wiring(operators.client)
    out = await _call(wiring["transit"], route="K73", near="天恆")
    assert out["status"] == "ok"
    assert out["near"]["status"] == "ok"
    assert out["near"]["asked"] == "天恆"


async def test_the_tool_never_echoes_an_identifier_from_the_near_argument(operators):
    """`near` is the fourth MODEL-SUPPLIED string handed back inside a tool
    result (after `lookup_person`'s name, `calculate`'s expression and
    `route`). Same hole, same fix: an identifier is dropped from the echo, and
    the resolution outcome survives without it."""
    class _Parroting:
        async def arrivals(self, route: str, near: str = "") -> dict:
            return {"status": "ok", "route": route, "known_route": True,
                    "near": {"status": "not_found", "asked": near},
                    "directions": [], "note": "n"}

    wiring = _tool_wiring(_Parroting())          # type: ignore[arg-type]
    out = await _call(wiring["transit"], route="68X", near="+85290000101")
    assert "asked" not in out["near"]
    assert out["near"]["status"] == "not_found"


# ---- ADR 0015 §3: how to report a status rides in the result ----------------

_DASHES = ("—", "–")


async def test_every_non_ok_answer_carries_a_dash_free_note(operators):
    """ADR 0015 §3/§6: the description only NAMES the statuses and the `near`
    outcomes; what to say for each is the result's `note`. Every non-ok branch
    of `describe` (and each `near` suffix) is driven here, so a branch that
    loses its sentence goes red."""
    # First: the catalogue is cached once read, so its outage must come first.
    operators.csv_status = 503
    catalogue_down = await operators.client.arrivals("ZZZ999")
    operators.csv_status = 200
    no_such = await operators.client.arrivals("ZZZ999")
    no_service = await operators.client.arrivals("K74")
    operators.stop_eta_empty = True
    stop_quiet = await operators.client.arrivals("68X", near="灰沙圍")
    operators.stop_eta_empty = False
    not_on_route = await operators.client.arrivals("K73", near="大學站")
    nowhere = await operators.client.arrivals("K73", near="銀河站")
    ambiguous = await operators.client.arrivals("K73", near="輕鐵")
    operators.stops_status = 503
    stops_down = await operators.client.arrivals("K73", near="天恆")
    operators.stops_status = 200
    operators.mtr_status = 503
    eta_down = await operators.client.arrivals("K73")
    operators.mtr_status = 200

    class _Exploding:
        async def arrivals(self, route: str, near: str = "") -> dict:
            raise RuntimeError("transit is on fire")

    exploded = await _call(_tool_wiring(_Exploding())["transit"], route="K73")  # type: ignore[arg-type]

    cases = {
        "nothing_found/no_such_route": no_such,
        "nothing_found/no_service": no_service,
        "nothing_found/stop_quiet": stop_quiet,
        "ok/not_on_route": not_on_route,
        "ok/near_not_found": nowhere,
        "ok/near_ambiguous": ambiguous,
        "ok/near_unavailable": stops_down,
        "unavailable/eta": eta_down,
        "unavailable/catalogue": catalogue_down,
        "unavailable/raised": exploded,
    }
    for case, out in cases.items():
        assert out["status"] == case.split("/")[0], (case, out)
        assert isinstance(out.get("note"), str) and out["note"].strip(), case
        assert not any(d in out["note"] for d in _DASHES), case


def _scheduled(out: dict) -> bool:
    return any(dep["scheduled"] for d in out["directions"] for dep in d["departures"])


async def test_an_ok_note_carries_the_rules_its_answer_needs(operators):
    """The reporting rules that left the description arrive with the answer
    they are about: «from the start of the direction» only when the times are
    not at a named stop, and the timetable clause only when a departure in
    THIS answer came off a timetable."""
    origin = await operators.client.arrivals("K73")
    assert origin["status"] == "ok"
    assert "START" in origin["note"]
    assert ("timetable" in origin["note"]) == _scheduled(origin)

    at_stop = await operators.client.arrivals("68X", near="灰沙圍")
    assert at_stop["status"] == "ok" and at_stop["near"]["status"] == "ok"
    assert "AT the stop" in at_stop["note"]
    assert "START" not in at_stop["note"]
    assert ("timetable" in at_stop["note"]) == _scheduled(at_stop)
