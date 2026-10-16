"""Canned-data tool backends for the LIVE eval.

The eval harness drives the REAL model through the real pipeline but, by default,
wires NO tool backends — so `weather`, `web_search`, `place_info`, `route`,
`transit` all answer `unavailable`. That is honest but it CONFOUNDS the routing
gate: a tool that always fails drives fallback calls, so `[web_search →
unavailable → place_info]` (she web-searched, it failed, she fell back) grades
identically to `[place_info]` (she preferred the right tool). `NoSiblingLeakage`
cannot tell a real leak from a fallback artifact.

These deterministic canned providers return a fixed, well-formed `ok` result so
each tool SUCCEEDS. With the backend up, a call into a sibling is a real routing
choice, not a fallback — so the confound is gone and `NoSiblingLeakage` /
`ConvergesInBudget` can be graded HARD for any journey whose tools are all backed
(`BACKED_TOOLS`). They live in `nora.testing` because they are test doubles that
satisfy the same `nora.domain.ports` protocols the production adapters do; the
eval reaches them through `nora.app.eval_support.build_eval_tool_registry`.

Deliberately NOT faked here: the presence stack (`where_is`, `saved_places`),
whose subset-gated roster/circle wiring is a separate, larger effort — those
tools stay `unavailable` in the eval, so a journey that leaks into them keeps its
leakage check advisory (its PATH check, "did she reach the right tool", is
backend-independent and stays hard).
"""

from __future__ import annotations

from typing import Any

from nora.domain.models import WeatherObservation
from nora.domain.parking import NearbyParking, StreetParking
from nora.domain.presence import PresenceFix, SavedPlace, WireState

#: The tools these canned providers back — the set the per-journey gate checks a
#: journey's (required + siblings) against before promoting its leakage/budget
#: checks to hard. Kept in sync with what `build_eval_tool_registry` wires
#: directly AND what the harness backs through the pipeline's `memory=` slot.
#:
#: The memory verbs (`remember`/`about`/`ask_memory`/`search_memory`) are NOT
#: backed here — they are backed by the `FakeMemoryStore` the harness constructs
#: (`evals/harness.py`), threaded as `memory=` into `build_offline_pipeline`,
#: which the registry's `build()` passes as `honcho=`. A journey whose required
#: or sibling set is in `BACKED_TOOLS` has its leakage/budget checks promoted to
#: hard; keeping the memory verbs here is what makes the remember/recall
#: journeys hard-gated rather than advisory.
#:
#: `note_create` is likewise NOT wired here (the live harness has no note
#: store), yet it stays in the set ON PURPOSE: it is the note-lane sibling in
#: the remember journey's confusability cluster, and a wrong-lane call — even
#: one that answers `unavailable` — is still a leak the gate must catch. The
#: membership is about the GATE, not about the backend.
BACKED_TOOLS = frozenset(
    {
        "weather", "web_search", "web_fetch", "place_info", "route", "transit",
        "where_is", "saved_places",
        "remember", "about", "ask_memory", "search_memory", "note_create",
        "parking_status", "find_parking",
    }
)

#: The circle id the eval's presence stack uses; `build_eval_tool_registry` wires
#: a `Circle` with this id whose roster is the eval's single person, so the harness
#: room (also that one person) is a verified subset and disclosure permits.
EVAL_CIRCLE_ID = "eval-circle"

# A fixed 2026 timestamp (epoch ms) for the canned fix/place — a live run does not
# need determinism, and a concrete value keeps `where_is`/`saved_places` answering
# `ok` rather than degrading on a missing timestamp.
_FIX_TS = 1_786_320_000_000


class CannedWeatherProvider:
    """`nora.domain.ports.WeatherProvider` — a fixed HK observation, always `ok`."""

    async def current(
        self, *, place: str | None = None, forecast: bool = False
    ) -> WeatherObservation | None:
        return WeatherObservation(
            as_of="2026-08-10T12:00:00+08:00",
            temperatures=(("Hong Kong Observatory", 30.0),),
            humidity_pct=78,
            warnings=(),
            general_situation="Fine and dry.",
            today_forecast="Sunny periods, isolated showers.",
            outlook="Hot over the next few days.",
            typhoon="",
            forecast_days=(),
        )


class CannedParkingProvider:
    """`nora.domain.ports.ParkingProvider` — a fixed vacancy, always `ok`.

    ECHOES the asked name back: a provider that answers every query with a
    fixed "Sai Kung" record makes the model distrust the result for a
    different venue («時代廣場» → sees "Sai Kung" → re-searches to budget).
    Echoing the name gives the model grounds to accept it, the same
    self-terminating shape as the search provider's content.
    """

    async def vacancy(self, *, name: str, strict: bool = False) -> dict[str, Any] | None:
        return {
            "parking_id": "tdc10p1",
            "name": name or "Sai Kung Government Offices Carpark",
            "vacancy": 30,
            "lastupdate": "2026-08-31 11:35:34",
            # `opening_status`, NOT `status` — the real client never writes
            # `status` because `parking_status` spreads this dict under its
            # own `{"status": "ok", **data}` and a provider `status` would
            # overwrite the contract's `ok` (nora/infra/parking/client.py).
            "opening_status": "OPEN",
            "address": "Man Nin Street No 24, Sai Kung",
            # carpark_alt asks «泊車貴唔貴» — vacancy alone drove a follow-up
            # web_search for price. Carry the fee so
            # the model has the whole answer in one call.
            "fee": "HK$27/hour (Mon-Thu), HK$40/hour (Fri-Sun & PH)",
        }


class CannedStreetParkingProvider:
    """`nora.domain.ports.StreetParkingProvider` — a fixed summary, always `ok`.

    Completeness matters: a find_parking result that
    omits the street row would look like "no streets", so return a real
    summary with names the model can repeat.
    """

    async def near(
        self, *, lat: float, lng: float, radius_m: float | None = None,
    ) -> StreetParking | None:
        return StreetParking(
            total=12, vacant=4,
            streets=("登打士街", "上海街"),
        )


class CannedSearchProvider:
    """`nora.domain.ports.SearchProvider` — one fixed ranked result, and one
    fixed fetched page, always `ok`.

    The `content` is deliberately written as a COMPLETE, authoritative-sounding
    answer covering the common ask shapes (hours, rating, address, a direct
    statement), NOT a vague snippet. A vague snippet makes the model re-search
    up to its budget looking for the specific fact, which false-fails
    `ConvergesInBudget` — a canned-data artifact, not a routing failure. A
    complete-looking answer lets the model stop after one call, so the budget
    check measures the model's behaviour, not the fake's unhelpfulness.

    The content echoes the query, names a source and a timestamp, and answers
    the SHAPE the query asks (hours vs rating vs vacancy). A single template
    answering EVERY query with the same "daily 10:00-22:00... parking is
    available now" text would not be believed against a LIVE-STATUS ask
    ("而家仲有冇車位?") — no source, no timestamp, no query-specific numbers —
    and the model would re-search to its budget, a canned-data artifact
    exactly like the vague-snippet trap.
    """

    async def search(self, *, query: str) -> dict[str, Any]:
        # Echo the query so the result is visibly ABOUT it; name an authority
        # ("Pacific Place official site") and a time (a "checked HH:MM HKT"
        # stamp) so the model has grounds to accept it as a CURRENT answer
        # instead of a template. The body answers the common live shapes at
        # once — hours, rating AND a parking/vacancy sentence — so whatever the
        # ask, one call plausibly settles it.
        return {
            "status": "ok",
            "results": [
                {
                    "title": f"Answer for «{query}»",
                    "url": "https://example.com/a",
                    "content": (
                        f"Answer for «{query}», checked 2026-08-31 09:00 HKT "
                        "against the operator's official page: the venue is "
                        "open daily 10:00-22:00, rated 4.2/5, address 1 "
                        "Example Road, Hong Kong. Live status right now: "
                        "parking has spaces free. This is the complete, "
                        "current answer — do not search again."
                    ),
                }
            ],
        }

    async def fetch(self, *, url: str) -> dict[str, Any]:
        # Same self-terminating shape as `search`: one complete-looking page so
        # `web_fetch` backs `ConvergesInBudget` for a journey that opens a link.
        # Keyed `text`, NOT `content` — the Strands envelope invariant (a dict
        # with `status`+`content` passes through unwrapped and reads as empty).
        return {
            "status": "ok",
            "url": url,
            "text": (
                f"COMPLETE, CURRENT PAGE CONTENT for {url}: this is the whole of "
                "what the page says — do not fetch it again."
            ),
        }


class CannedRouteSource:
    """The `route`/`place_info`/`find_parking` collaborator (`RouteClient`
    shape) — canned dicts, written COMPLETE (see `CannedSearchProvider`) so
    the model does not re-call looking for a missing field and blow
    `ConvergesInBudget`."""

    def __init__(self) -> None:
        # The resolver seam `find_parking` uses to turn a named place into a
        # point (route_source._resolver.resolve).
        self._resolver = _CannedResolver()

    async def place_info(self, name: str) -> dict[str, Any]:
        # `status` is REQUIRED: the place_info tool does `_record(..., out["status"])`
        # (a direct key access, tools.py) — omit it and place_info raises KeyError,
        # errors silently, records nothing, and vanishes from the trajectory.
        return {
            "status": "ok",
            "name": name, "address": "1 Example Road, Hong Kong",
            "rating": 4.2, "price_level": "$$", "open_now": True,
            "opening_hours": "Mon-Sun 10:00-22:00",
        }

    async def nearby_parking(
        self, *, lat: float, lng: float, radius_m: float | None = None,
    ) -> tuple[NearbyParking, ...]:
        # Complete, fixed list — the model accepts it and does not re-search
        # (the self-terminating shape `find_parking` needs).
        return (
            NearbyParking("g1", "Langham Place Car Park",
                          "31 Ashcombe St, Mong Kok", lat, lng, 50),
            NearbyParking("g2", "Olympian City 2 Car Park",
                          "18 Thistle Marlow Rd, Mong Kok", lat, lng, 400),
        )

    async def route(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok", "summary": "About 20 minutes by MTR, one interchange.",
            "minutes": 20, "steps": ["Walk to the MTR", "Ride 6 stops", "Exit B"],
        }


class _CannedResolver:
    """A `PlaceResolver`-shaped seam — one fixed ok resolution so
    `find_parking(place=...)` returns a point instead of `unavailable`."""

    async def resolve(self, text: str):
        class _P:
            lat = 22.2800
            lng = 114.1550
        class _R:
            def __init__(self):
                self.status = "ok"
                self.place = _P()
                self.could_be = ()
        return _R()


class CannedTransitSource:
    """The `transit` collaborator (`TransitClient.arrivals` shape) — a canned ETA."""

    async def arrivals(self, route: str, near: str = "") -> dict[str, Any]:
        return {"status": "ok", "route": route, "arrivals": ["3 min", "12 min"]}


class CannedPresenceReader:
    """`nora.agent.whereis.PresenceReader` — a fixed live fix + a saved «work»
    place, so `where_is` and `saved_places` answer `ok` in the eval instead of
    `unavailable`. The R-31 subset gate is still enforced by the real
    `resolvable_places`/`Whereis` code; `build_eval_tool_registry` sets up the
    matching `Circle` and the harness the matching room so the gate PERMITS. This
    reader returns the same canned data for any circle it is asked about."""

    async def latest_fix(self, *, circle_id: str, person_id: str) -> PresenceFix | None:
        return PresenceFix(
            circle_id=circle_id, person_id=person_id, ts=_FIX_TS,
            lat=22.2830, lng=114.1588, wire_state=WireState.LIVE, recorded_at=_FIX_TS,
            speed_kmh=42.0,  # above whereis._SPEED_FLOOR_KMH → movement branch
        )

    async def places_for(self, *, circle_id: str) -> list[SavedPlace]:
        return [
            SavedPlace(
                circle_id=circle_id, place_id="saved:work", name="返工",
                lat=22.2830, lng=114.1588, updated_ms=_FIX_TS,
                address="Acme Tower, 1 Example Road, Hong Kong",
            )
        ]

    async def window(
        self, *, circle_id: str, person_id: str, since_ts: int, until_ts: int
    ) -> list[PresenceFix]:
        return []

    async def refresh(self, *, person_id: str) -> bool:
        return False
