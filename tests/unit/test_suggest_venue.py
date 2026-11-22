"""`suggest_venue` reads each roster person's likes from the graph and keeps
the three answers three: an unread person is unknown, never no preference."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from nora.app.gatherings import GatheringService
from nora.domain.gatherings import GatheringNotFoundError
from nora.memory import PersonLikes
from nora.memory.domain.presentations import Like
from nora.memory.domain.retrieval import RetrievedRow
from nora.testing.gatherings import FakeGatheringStore

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


class _NoHoncho:
    """The service is still handed a Honcho store; any use of it fails."""

    def __getattr__(self, name):
        raise AssertionError(f"Honcho.{name} was used")


class _FakePlaces:
    """`place_info` by the name asked: a named place resolves, a category is
    `ambiguous`, anything else `not_found`."""

    def __init__(self, places: dict[str, str], categories: tuple[str, ...] = ()) -> None:
        self._places, self._categories = places, categories

    async def place_info(self, name: str) -> dict:
        if name in self._places:
            return {"status": "ok", "name": self._places[name], "place_id": f"id-{name}",
                    "address": f"{name} Rd", "rating": 4.0}
        if name in self._categories:
            return {"status": "ambiguous", "could_be": ["A", "B", "C"]}
        return {"status": "not_found"}


def _row(**changes) -> RetrievedRow:
    base = RetrievedRow(
        graph_id="row", kind="entity", source=None, basis="inferred", reasoning=None,
        confidence=None, occurred_at=None, asserted_at=NOW, valid_from=None, valid_to=None,
        state="live", origin_room=None, exposure="internal", persons=(), content={},
        edges=(), evidence=(), close_when={}, distance=1, already_shown=False,
    )
    return replace(base, **changes)


def _like(topic: str) -> Like:
    return Like(
        _row(graph_id=f"claim-{topic}", kind="claim", part="trait"),
        _row(graph_id=f"topic-{topic}", content={"name": topic}), (), False,
    )


def _read(person: str, status="ok", *topics: str) -> PersonLikes:
    return PersonLikes(person, status, tuple(_like(topic) for topic in topics))


class _FakeLikes:
    """The graph read: each person's own `PersonLikes`, and what it was asked."""

    def __init__(self, *reads: PersonLikes) -> None:
        self._reads = {read.person: read for read in reads}
        self.asked: list[tuple] = []

    async def likes_of(self, reading, persons, now, *, unit=""):
        self.asked.append((reading, tuple(persons), unit))
        return tuple(self._reads[person] for person in persons)


def _service(*, route_source=None, likes=None) -> GatheringService:
    return GatheringService(
        store=FakeGatheringStore(), route_source=route_source, honcho=_NoHoncho(),
        likes=likes, ai_peer="nora", now=lambda: NOW,
    )


async def _venue_event(svc):
    return await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles",
        title="Dinner", platform="signal",
    )


async def _suggest(svc, event, roster=("miles", "daisy")):
    return await svc.suggest_venue(
        event_id=event.id, conversation_id="signal:group-1", platform="signal",
        roster_person_ids=list(roster), unit_id="",
    )


_PLACES = _FakePlaces(
    {"Soi 29": "Soi 29 Thai Kitchen", "Cafe X": "Cafe X Central"},
    categories=("ramen",),
)


async def test_suggest_venue_ranks_places_by_holders_and_drops_what_is_not_a_place():
    likes = _FakeLikes(
        _read("miles", "ok", "Soi 29", "ramen", "Cafe X"),
        _read("daisy", "ok", "soi 29", "hiking"),
    )
    svc = _service(route_source=_PLACES, likes=likes)
    out = await _suggest(svc, await _venue_event(svc))
    assert (out.status, out.complete) == ("ok", True)
    assert [(c.venue_text, c.name, c.source) for c in out.candidates] == [
        ("Soi 29", "Soi 29 Thai Kitchen", "mention_frequency"),
        ("Cafe X", "Cafe X Central", "explicit_callback"),
    ]
    assert out.route_hints is None  # reserved, always None


async def test_suggest_venue_ok_when_every_read_is_ok():
    svc = _service(route_source=_PLACES, likes=_FakeLikes(
        _read("miles", "ok", "Soi 29"), _read("daisy", "nothing_found"),
    ))
    out = await _suggest(svc, await _venue_event(svc))
    assert (out.status, out.complete) == ("ok", True)


async def test_suggest_venue_partial_read_with_places_is_ok_but_not_complete():
    svc = _service(route_source=_PLACES, likes=_FakeLikes(
        _read("miles", "partial", "Soi 29"), _read("daisy", "ok"),
    ))
    out = await _suggest(svc, await _venue_event(svc))
    assert (out.status, out.complete) == ("ok", False)
    assert [c.venue_text for c in out.candidates] == ["Soi 29"]


async def test_suggest_venue_partial_read_with_no_place_is_unavailable_not_nothing_found():
    svc = _service(route_source=_PLACES, likes=_FakeLikes(
        _read("miles", "partial", "ramen"), _read("daisy", "ok"),
    ))
    out = await _suggest(svc, await _venue_event(svc))
    assert (out.status, out.candidates) == ("unavailable", ())


async def test_suggest_venue_unavailable_read_is_never_nothing_found():
    for reads in (
        (_read("miles", "unavailable"), _read("daisy", "unavailable")),
        (_read("miles", "unavailable"), _read("daisy", "nothing_found")),
    ):
        svc = _service(route_source=_PLACES, likes=_FakeLikes(*reads))
        out = await _suggest(svc, await _venue_event(svc))
        assert (out.status, out.candidates) == ("unavailable", ()), reads


async def test_suggest_venue_one_unavailable_person_does_not_hide_another_s_place():
    svc = _service(route_source=_PLACES, likes=_FakeLikes(
        _read("miles", "unavailable"), _read("daisy", "ok", "Cafe X"),
    ))
    out = await _suggest(svc, await _venue_event(svc))
    assert (out.status, out.complete) == ("ok", False)


async def test_suggest_venue_nothing_found_when_every_read_completed_and_none_is_a_place():
    for reads in (
        (_read("miles", "nothing_found"), _read("daisy", "nothing_found")),
        (_read("miles", "ok", "ramen"), _read("daisy", "ok", "hiking")),
    ):
        svc = _service(route_source=_PLACES, likes=_FakeLikes(*reads))
        out = await _suggest(svc, await _venue_event(svc))
        assert (out.status, out.candidates) == ("nothing_found", ()), reads


async def test_suggest_venue_a_topic_that_cannot_be_looked_up_is_unknown_not_absent():
    down = type("Down", (), {"place_info": staticmethod(_down)})()
    svc = _service(route_source=down, likes=_FakeLikes(_read("miles", "ok", "Soi 29")))
    out = await _suggest(svc, await _venue_event(svc), roster=("miles",))
    assert (out.status, out.candidates) == ("unavailable", ())


async def _down(name):
    return {"status": "unavailable"}


async def test_suggest_venue_without_a_likes_read_or_a_roster_is_unavailable():
    event_svc = _service(route_source=_PLACES)
    out = await _suggest(event_svc, await _venue_event(event_svc))
    assert out.status == "unavailable"
    empty = _service(route_source=_PLACES, likes=_FakeLikes())
    assert (await _suggest(empty, await _venue_event(empty), roster=())).status == "unavailable"


async def test_suggest_venue_reads_only_the_roster_for_the_rooms_own_audience():
    likes = _FakeLikes(_read("miles", "ok", "Soi 29"), _read("daisy", "ok"))
    svc = _service(route_source=_PLACES, likes=likes)
    event = await svc.create_event(
        conversation_id="signal:group-1", requester_person_id="miles", title="Dinner",
        platform="signal", unit_id="work",
    )
    await svc.suggest_venue(
        event_id=event.id, conversation_id="signal:group-1", platform="signal",
        roster_person_ids=["miles", "daisy"], unit_id="work",
    )
    ((reading, persons, unit),) = likes.asked
    assert persons == ("miles", "daisy") and unit == "work"
    assert (reading.platform, reading.conversation_id) == ("signal", "signal:group-1")
    assert reading.live.person_ids == frozenset({"miles", "daisy"})


async def test_suggest_venue_refuses_a_mismatched_conversation_id_before_any_read():
    likes = _FakeLikes(_read("miles", "ok", "Soi 29"))
    svc = _service(route_source=_PLACES, likes=likes)
    event = await _venue_event(svc)
    with pytest.raises(GatheringNotFoundError):
        await svc.suggest_venue(
            event_id=event.id, conversation_id="signal:some-other-group", platform="signal",
            roster_person_ids=["miles"],
        )
    assert likes.asked == []
