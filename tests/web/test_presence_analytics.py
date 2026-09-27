from pathlib import Path

from aiohttp import web

from nora.agent.whereis import Circle
from nora.app.gatherings import GatheringService
from nora.domain.models import PersonRef
from nora.domain.reminders import ReminderCaps
from nora.persons import Persons
from nora.testing import FakeMemoryStore, FakeRecordStore
from nora.testing.gatherings import FakeGatheringStore
from nora.testing.reminders import FakeReminderStore
from nora.web.api import register_web_routes
from tests.conftest import build_test_doctrine_store

MILES = PersonRef(person_id="miles", name="Miles", honcho_peer_id="miles")
CIRCLE = Circle(circle_id="c1", person_ids=frozenset({"miles"}), people=(MILES,))


def _events() -> GatheringService:
    return GatheringService(
        store=FakeGatheringStore(),
        reminders=FakeReminderStore(),
        reminder_caps=ReminderCaps(),
        route_source=None,
        honcho=None,
        ai_peer="nora",
    )


class FakeAnalyticsStore:
    def __init__(self, *, empty: bool = False) -> None:
        self.empty = empty
        self.rhythm_windows: list[int] = []
        self.route_windows: list[int] = []

    async def places_with_aggregates(self, *, subject_id: str) -> list[dict]:
        if self.empty:
            return []
        return [
            {
                "kind": "home",
                "label": "Tin Sgak Path",
                "saved": False,
                "lat": 22.410,
                "lng": 114.1245,
                "radius_m": 120.0,
                "confidence": 0.45,
                "first_seen_ts": 1_787_133_855_000,
                "last_seen_ts": 1_788_015_839_599,
                "stay_count": 42,
                "total_dwell_ms": 1_782_000_000,
            },
        ]

    async def rhythm_bins(self, *, subject_id: str, window_ms: int) -> list[dict]:
        self.rhythm_windows.append(window_ms)
        if self.empty:
            return []
        return [{"hour_of_week": 0, "stay_count": 3, "total_dwell_ms": 7_200_000}]

    async def trips_with_corridors(
        self, *, subject_id: str, window_ms: int,
    ) -> dict[str, list[dict]]:
        self.route_windows.append(window_ms)
        if self.empty:
            return {"trips": [], "corridors": []}
        return {
            "trips": [
                {
                    "id": 293,
                    "started_ts": 1_788_012_780_000,
                    "ended_ts": 1_788_013_740_000,
                    "distance_m": 23_300.0,
                    "avg_speed_kmh": 87.2,
                    "fix_count": 41,
                    "corridor_id": 2,
                    "corridor_sample_count": 5,
                },
            ],
            "corridors": [
                {
                    "id": 2,
                    "sample_count": 5,
                    "first_seen_ts": 1_787_900_000_000,
                    "last_seen_ts": 1_788_013_740_000,
                    "sequence": ["Nathan Road", "Argyle Street", "Harbour City"],
                },
            ],
        }


def _app(*, presence: object | None, mobility: FakeAnalyticsStore) -> web.Application:
    app = web.Application()
    register_web_routes(
        app,
        store=FakeRecordStore(),
        honcho=FakeMemoryStore(),
        reminders=FakeReminderStore(),
        persons=Persons(by_person={"miles": MILES}, index={}),
        ai_peer="nora",
        started_at=0.0,
        games_client=None,
        presence=presence,
        presence_circles=(CIRCLE,),
        skills_dir=Path("/nonexistent-skills"),
        events=_events(),
        doctrine_store=build_test_doctrine_store(),
        mobility=mobility,
    )
    return app


async def test_analytics_endpoints_return_the_spec_shapes(aiohttp_client):
    mobility = FakeAnalyticsStore()
    client = await aiohttp_client(_app(presence=object(), mobility=mobility))

    places = await (await client.get("/app/v1/presence/analytics/places")).json()
    assert places == {
        "subjects": [
            {
                "subjectId": "miles",
                "places": [
                    {
                        "kind": "home",
                        "label": "Tin Sgak Path",
                        "saved": False,
                        "lat": 22.410,
                        "lng": 114.1245,
                        "radiusM": 120.0,
                        "confidence": 0.45,
                        "firstSeenTs": 1_787_133_855_000,
                        "lastSeenTs": 1_788_015_839_599,
                        "stayCount": 42,
                        "totalDwellMs": 1_782_000_000,
                    },
                ],
            },
        ],
    }

    rhythm = await (await client.get(
        "/app/v1/presence/analytics/rhythm?subjectId=miles"
    )).json()
    assert rhythm == {
        "subjectId": "miles",
        "bins": [{"hourOfWeek": 0, "stayCount": 3, "totalDwellMs": 7_200_000}],
        "windowDays": 30,
    }

    routes = await (await client.get(
        "/app/v1/presence/analytics/routes?subjectId=miles"
    )).json()
    assert routes == {
        "subjectId": "miles",
        "trips": [
            {
                "id": 293,
                "startedTs": 1_788_012_780_000,
                "endedTs": 1_788_013_740_000,
                "distanceM": 23_300.0,
                "avgSpeedKmh": 87.2,
                "fixCount": 41,
                "corridorId": 2,
                "corridorSampleCount": 5,
            },
        ],
        "corridors": [
            {
                "id": 2,
                "sampleCount": 5,
                "firstSeenTs": 1_787_900_000_000,
                "lastSeenTs": 1_788_013_740_000,
                "sequence": ["Nathan Road", "Argyle Street", "Harbour City"],
            },
        ],
    }


async def test_analytics_endpoints_return_empty_shapes_when_presence_is_disabled(
    aiohttp_client,
):
    mobility = FakeAnalyticsStore()
    client = await aiohttp_client(_app(presence=None, mobility=mobility))

    assert await (await client.get("/app/v1/presence/analytics/places")).json() == {
        "subjects": [],
    }
    assert await (await client.get(
        "/app/v1/presence/analytics/rhythm?subjectId=miles"
    )).json() == {"subjectId": "miles", "bins": [], "windowDays": 30}
    assert await (await client.get(
        "/app/v1/presence/analytics/routes?subjectId=miles"
    )).json() == {"subjectId": "miles", "trips": [], "corridors": []}
    assert mobility.rhythm_windows == []
    assert mobility.route_windows == []


async def test_analytics_endpoints_keep_empty_subjects_in_the_r27_shape(aiohttp_client):
    mobility = FakeAnalyticsStore(empty=True)
    client = await aiohttp_client(_app(presence=object(), mobility=mobility))

    assert await (await client.get("/app/v1/presence/analytics/places")).json() == {
        "subjects": [{"subjectId": "miles", "places": []}],
    }
    assert await (await client.get(
        "/app/v1/presence/analytics/rhythm?subjectId=miles"
    )).json() == {"subjectId": "miles", "bins": [], "windowDays": 30}
    assert await (await client.get(
        "/app/v1/presence/analytics/routes?subjectId=miles"
    )).json() == {"subjectId": "miles", "trips": [], "corridors": []}


async def test_analytics_windows_are_capped_at_90_days(aiohttp_client):
    mobility = FakeAnalyticsStore()
    client = await aiohttp_client(_app(presence=object(), mobility=mobility))

    assert (await client.get(
        "/app/v1/presence/analytics/rhythm?subjectId=miles&days=999"
    )).status == 200
    body = await (await client.get(
        "/app/v1/presence/analytics/routes?subjectId=miles&days=999"
    )).json()
    assert body["subjectId"] == "miles"
    assert mobility.rhythm_windows[-1] == 90 * 24 * 60 * 60 * 1000
    assert mobility.route_windows[-1] == 90 * 24 * 60 * 60 * 1000


async def test_analytics_unknown_subjects_are_empty_and_not_read(aiohttp_client):
    mobility = FakeAnalyticsStore()
    client = await aiohttp_client(_app(presence=object(), mobility=mobility))

    rhythm = await (await client.get(
        "/app/v1/presence/analytics/rhythm?subjectId=stranger"
    )).json()
    routes = await (await client.get(
        "/app/v1/presence/analytics/routes?subjectId=stranger"
    )).json()
    assert rhythm == {"subjectId": "stranger", "bins": [], "windowDays": 30}
    assert routes == {"subjectId": "stranger", "trips": [], "corridors": []}
    assert mobility.rhythm_windows == []
    assert mobility.route_windows == []
