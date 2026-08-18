"""Canned-data tool backends for the LIVE eval (F4, external review 2026-08-10).

The eval harness drives the REAL model through the real pipeline but, by default,
wires NO tool backends — so `weather`, `web_search`, `place_info`, `route`,
`transit` all answer `unavailable`. That is honest but it CONFOUNDS the routing
gate: a tool that always fails drives fallback calls, so `[web_search →
unavailable → place_info]` (she web-searched, it failed, she fell back) grades
identically to `[place_info]` (she preferred the right tool). `NoSiblingLeakage`
cannot tell a real leak from a fallback artifact, which is why it shipped
advisory.

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
from nora.domain.presence import PresenceFix, SavedPlace, WireState

#: The tools these canned providers back — the set the per-journey gate checks a
#: journey's (required + siblings) against before promoting its leakage/budget
#: checks to hard. Kept in sync with what `build_eval_tool_registry` wires.
BACKED_TOOLS = frozenset(
    {
        "weather", "web_search", "web_fetch", "place_info", "route", "transit",
        "where_is", "saved_places",
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


class CannedSearchProvider:
    """`nora.domain.ports.SearchProvider` — one fixed ranked result, and one
    fixed fetched page, always `ok`.

    The `content` is deliberately written as a COMPLETE, authoritative-sounding
    answer covering the common ask shapes (hours, rating, address, a direct
    statement), NOT a vague snippet. A vague snippet makes the model re-search
    up to its budget looking for the specific fact, which false-fails
    `ConvergesInBudget` — a canned-data artifact, not a routing failure. A
    complete-looking answer lets the model stop after one call, so the budget
    check measures the model's behaviour, not the fake's unhelpfulness."""

    async def search(self, *, query: str) -> dict[str, Any]:
        # A single, DIRECT, affirmative answer that covers the common ask shapes
        # (hours, rating, AND parking/availability) so the model accepts it and
        # STOPS after one call instead of re-searching to its budget — the flail
        # is a canned-data artifact (a vague result makes a real model keep
        # looking), not a routing signal. Kept assertive and self-terminating.
        return {
            "status": "ok",
            "results": [
                {
                    "title": "Complete answer",
                    "url": "https://example.com/a",
                    "content": (
                        f"COMPLETE, CURRENT ANSWER to «{query}»: Yes. It is open "
                        "daily 10:00-22:00, rated 4.2/5; parking is available now "
                        "with spaces free; address 1 Example Road, Hong Kong. This "
                        "is the full, authoritative answer — do not search again."
                    ),
                }
            ],
        }

    async def fetch(self, *, url: str) -> dict[str, Any]:
        # Same self-terminating shape as `search`: one complete-looking page so
        # `web_fetch` backs `ConvergesInBudget` for a journey that opens a link.
        return {
            "status": "ok",
            "url": url,
            "content": (
                f"COMPLETE, CURRENT PAGE CONTENT for {url}: this is the whole of "
                "what the page says — do not fetch it again."
            ),
        }


class CannedRouteSource:
    """The `route`/`place_info` collaborator (`RouteClient` shape) — canned dicts,
    written COMPLETE (see `CannedSearchProvider`) so the model does not re-call
    looking for a missing field and blow `ConvergesInBudget`."""

    async def place_info(self, name: str) -> dict[str, Any]:
        # `status` is REQUIRED: the place_info tool does `_record(..., out["status"])`
        # (a direct key access, tools.py) — omit it and place_info raises KeyError,
        # errors silently, records nothing, and vanishes from the trajectory (this
        # bit once: the router forced place_info, she called it, and the eval showed
        # web_search because the call never recorded).
        return {
            "status": "ok",
            "name": name, "address": "1 Example Road, Hong Kong",
            "rating": 4.2, "price_level": "$$", "open_now": True,
            "opening_hours": "Mon-Sun 10:00-22:00",
        }

    async def route(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok", "summary": "About 20 minutes by MTR, one interchange.",
            "minutes": 20, "steps": ["Walk to the MTR", "Ride 6 stops", "Exit B"],
        }


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
        )

    async def places_for(self, *, circle_id: str) -> list[SavedPlace]:
        return [
            SavedPlace(
                circle_id=circle_id, place_id="saved:work", name="返工",
                lat=22.2830, lng=114.1588, updated_ms=_FIX_TS,
            )
        ]

    async def window(
        self, *, circle_id: str, person_id: str, since_ts: int, until_ts: int
    ) -> list[PresenceFix]:
        return []

    async def refresh(self, *, person_id: str) -> bool:
        return False
