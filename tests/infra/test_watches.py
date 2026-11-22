"""Watch admission, provider egress and Observe's durable outcomes."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
import sqlalchemy as sa
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.agent.egress import LeakGuard
from nora.app.graph_observe import graph_observe
from nora.doctrine.loader import Doctrine
from nora.domain.fetched_page import FetchedPage
from nora.domain.graph_registration import SourceKind
from nora.infra.search import TavilySearchProvider
from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import EDGE, ENTITY, INTENTION, REGISTRY, WATCH_RESULT
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.fetched_pages import SqlFetchedPageStore
from nora.infra.sql.graph_stimuli import SqlGraphStimuli
from nora.infra.sql.item_registration import (
    RegistrationScope,
    RuntimeRevisions,
    SqlItemRegistration,
)
from nora.infra.sql.schema import FETCHED_PAGES, SOURCE_HEALTH
from nora.memory.adapters.watch_results import SqlWatchResults
from nora.memory.app.admission import AdmissionService
from nora.memory.app.watches import WATCH_INTERVAL, WATCH_LIMIT, WatchPoller
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_ports import Stored
from nora.memory.domain.admission_proposals import (
    AddEdge,
    CreateEntity,
    Link,
    PassContext,
    Scope,
    SourceReference,
)
from nora.memory.wire import admission
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Conversation, Policy
from nora.testing.room_slots import FakeRoomSlots
from tests.fakes import FakeClock

_URL = "https://public.example/notice"
_AT = datetime(2026, 1, 1, tzinfo=UTC)
_PASS = PassContext(
    "unit-a",
    "room-a",
    False,
    "wake",
    "fixture",
    "pass",
    "trace",
    "model",
    "code",
    "rule",
    "skill",
    "config",
    "identity",
    "policy",
)


@dataclass
class _World:
    engine: AsyncEngine
    registration: SqlItemRegistration
    admission: AdmissionService

    async def fetch_item(self, url: str = _URL, *, room: str = "room-a") -> str:
        store = SqlFetchedPageStore(self.engine, registration=self.registration)
        await store.put(page=FetchedPage(room, url, "fixture", _AT, _AT + timedelta(days=1), {}))
        async with self.engine.connect() as conn:
            return (
                await conn.scalars(
                    sa.select(FETCHED_PAGES.c.graph_id).where(
                        FETCHED_PAGES.c.conversation_id == room,
                        FETCHED_PAGES.c.url == url,
                    )
                )
            ).one()

    async def rows(self, table: sa.Table, *where) -> list[sa.RowMapping]:
        async with self.engine.connect() as conn:
            return list((await conn.execute(sa.select(table).where(*where))).mappings())

    async def propose(self, url: str, shown: str | None, *, id: str = "target"):
        entity = CreateEntity(
            id=id,
            basis="inferred",
            scope=Scope("unit-a"),
            name="Fixture notice",
            reasoning="deduced",
            confidence="high",
            source_ref=SourceReference("page", url),
            citations=(shown,) if shown else (),
            links=(Link(type="watches", source="maintain", target=id, basis="committed"),),
        )
        (outcome,) = (
            await self.admission.admit(
                (entity,),
                _PASS,
                ("maintain", shown) if shown else ("maintain",),
            )
        ).outcomes
        return outcome

    async def watch(self, url: str = _URL, *, id: str = "target") -> str:
        fetched = await self.fetch_item(url)
        assert await self.propose(url, fetched, id=id) == Stored(id)
        return (await self.rows(EDGE, EDGE.c.type == "watches", EDGE.c.dst_id == id))[0]["id"]

    async def stimuli(self):
        return await SqlGraphStimuli(self.engine).read("unit-a")


@pytest.fixture
async def world(clean_pg_url: str) -> AsyncIterator[_World]:
    engine = create_sql_engine(clean_pg_url)
    policy = Policy(
        tuple(
            Conversation(
                "signal",
                frozenset({f"room-{key}"}),
                None,
                primary_id=f"room-{key}",
                unit=f"unit-{key}",
            )
            for key in ("a", "b")
        )
    )
    persons = Persons({}, {})
    guard = LeakGuard(Doctrine((), "", "", "rule", "", 0), "fixture-canary")
    registration = SqlItemRegistration(
        RegistrationScope(policy, persons), RuntimeRevisions({}, policy, persons)
    )
    engine = engine.execution_options(item_registration=registration)
    graph = SqlCognitiveGraph(engine)
    await graph.create(
        INTENTION,
        {
            "id": "maintain",
            "unit": "unit-a",
            "kind": "intention",
            "basis": "committed",
            "writer": "fixture",
            "exposure": "external",
            "state": "open",
            "owner": "nora",
            "owed_to": "nora",
            "form": "maintain",
            "holds_while_form": "predicate",
            "holds_while": {
                "graph_id": "fixture-status",
                "path": ["source_state", "state"],
                "equals": "open",
            },
            "delivery_mode": "when_it_comes_up",
        },
    )
    try:
        yield _World(
            engine, registration, admission(
                engine, persons, policy, guard, content_checks=False, room_slots=FakeRoomSlots({}),
            )
        )
    finally:
        await engine.dispose()


async def test_watch_admission_copies_a_same_unit_fetched_page(world):
    edge = await world.watch()
    (watch,) = await world.rows(EDGE, EDGE.c.id == edge)
    assert (watch["src_id"], watch["dst_id"], watch["state"]) == ("maintain", "target", "live")
    (entity,) = await world.rows(ENTITY)
    assert (entity["source_ref_kind"], entity["source_ref_value"]) == ("page", _URL)


@pytest.mark.parametrize("kind", [None, "account", "place"])
async def test_watch_admission_refuses_unpollable_targets(world, kind: SourceKind | None):
    await world.watch(id="pollable")
    await SqlCognitiveGraph(world.engine).create(
        ENTITY,
        {
            "id": "other",
            "unit": "unit-a",
            "kind": "entity",
            "basis": "computed",
            "writer": "fixture",
            "exposure": "external",
            "state": "admitted",
            "name": "Fixture source",
            "source_ref_kind": kind,
            "source_ref_value": "fixture-value" if kind else None,
        },
    )
    proposal = AddEdge(
        id="refused",
        basis="committed",
        scope=Scope("unit-a"),
        type="watches",
        source="maintain",
        target="other",
    )
    result = await world.admission.admit((proposal,), _PASS, ("maintain", "other"))
    assert result.outcomes == (Refusal("unpollable_watch_target", "target"),)
    assert await world.rows(EDGE, EDGE.c.id == "refused") == []


@pytest.mark.parametrize("other_unit", [False, True])
async def test_watch_admission_requires_the_exact_url_in_its_unit(world, other_unit):
    if other_unit:
        await world.fetch_item(room="room-b")
    decoy = await world.fetch_item(_URL + "/different")
    assert await world.propose(_URL, decoy) == Refusal("unbacked_page_reference", "source_ref")
    assert await world.rows(ENTITY) == []
    assert await world.rows(EDGE) == []
    await world.watch()


@pytest.mark.parametrize("path", ["%2B12025550101", "22.314%2C114.197"])
async def test_watch_admission_refuses_scanner_hits_even_with_a_fetched_page(world, path):
    await world.watch(id="pollable")
    url = f"https://public.example/{path}"
    fetched = await world.fetch_item(url)
    assert await world.propose(url, fetched) == Refusal("unsafe_page_reference", "source_ref")
    assert await world.rows(ENTITY, ENTITY.c.id == "target") == []
    assert await world.rows(EDGE, EDGE.c.dst_id == "target") == []


class _Page:
    def __init__(self, responses: list[str | None]) -> None:
        self._responses = iter(responses)
        self.calls: list[str] = []

    async def __call__(self, url: str) -> str | None:
        assert url.startswith("https://public.example/")
        self.calls.append(url)
        return next(self._responses)


def _poller(world: _World, page: _Page, clock: FakeClock) -> WatchPoller:
    return WatchPoller(
        SqlWatchResults(world.engine, world.registration, {"page"}),
        {"page": page},
        lambda: datetime.fromtimestamp(clock(), UTC),
    )


async def test_watch_polls_at_its_cadence_and_records_only_changes(world):
    edge = await world.watch()
    before = set(await world.stimuli())
    clock, page = FakeClock(_AT.timestamp()), _Page(["first", "first", "second"])
    poller = _poller(world, page, clock)
    await poller.run_once()
    await poller.run_once()
    assert page.calls == [_URL]
    (first,) = await world.rows(WATCH_RESULT)
    assert first["watch_edge"] == edge
    assert first["content_hash"] == sha256(b"first").hexdigest()
    (registry,) = await world.rows(REGISTRY, REGISTRY.c.id == first["graph_id"])
    assert (registry["kind"], registry["unit"], first["basis"]) == ("item", "unit-a", "observed")
    (stimulus,) = set(await world.stimuli()) - before
    assert (stimulus.kind, stimulus.graph_id) == ("watched_source_changed", first["graph_id"])

    clock.advance(WATCH_INTERVAL.total_seconds())
    await poller.run_once()
    assert page.calls == [_URL, _URL]
    assert await world.rows(WATCH_RESULT) == [first]
    assert set(await world.stimuli()) == before | {stimulus}

    clock.advance(WATCH_INTERVAL.total_seconds())
    await poller.run_once()
    assert len(await world.rows(WATCH_RESULT)) == 2
    assert len(set(await world.stimuli()) - before) == 2


async def test_watch_failure_records_unhealthy_observation_without_stimulating(world):
    await world.watch()
    before = await world.stimuli()
    clock, page = FakeClock(_AT.timestamp()), _Page([None])
    await _poller(world, page, clock).run_once()
    assert page.calls == [_URL]
    (health,) = await world.rows(SOURCE_HEALTH)
    assert (health["source"], health["scope"], health["outcome"], health["unit"]) == (
        "watch.page",
        "target",
        "error",
        "unit-a",
    )
    assert health["graph_id"]
    assert await world.rows(WATCH_RESULT) == []
    assert await world.stimuli() == before


async def test_watch_stops_when_maintain_is_ceased(world):
    edge = await world.watch()
    clock, page = FakeClock(_AT.timestamp()), _Page(["first", "unexpected"])
    poller = _poller(world, page, clock)
    await poller.run_once()
    assert page.calls == [_URL]
    before = await world.rows(WATCH_RESULT)
    (intention,) = await world.rows(INTENTION)
    (item,) = await world.rows(FETCHED_PAGES)
    async with world.engine.begin() as conn:
        await SqlCognitiveGraph(world.engine).end_intention_in(
            conn,
            intention,
            "ceased",
            witness_id=item["graph_id"],
            witness_at=_AT,
            witness_scope=item,
        )
    assert (await world.rows(EDGE, EDGE.c.id == edge))[0]["state"] == "retired"
    assert (await world.rows(INTENTION))[0]["ending"] == "ceased"
    clock.advance(WATCH_INTERVAL.total_seconds())
    await poller.run_once()
    assert page.calls == [_URL]
    assert await world.rows(WATCH_RESULT) == before


async def test_watch_cycle_limits_reads_and_eventually_visits_every_watch(world):
    urls = [f"{_URL}/{i}" for i in range(WATCH_LIMIT + 1)]
    for i, url in enumerate(urls):
        await world.watch(url, id=f"target-{i}")
    clock, page = FakeClock(_AT.timestamp()), _Page(["current"] * (WATCH_LIMIT * 2))
    poller = _poller(world, page, clock)
    await poller.run_once()
    assert len(page.calls) == len(set(page.calls)) == WATCH_LIMIT
    clock.advance(WATCH_INTERVAL.total_seconds())
    await poller.run_once()
    assert len(page.calls) == WATCH_LIMIT * 2
    assert set(page.calls) == set(urls)


async def test_observe_extracts_only_the_admitted_url_via_the_local_provider(world, aiohttp_server):
    sent = []

    async def extract(request):
        sent.append(await request.json())
        return web.json_response(
            {"results": [{"url": _URL, "raw_content": "current notice"}], "failed_results": []}
        )

    app = web.Application()
    app.router.add_post("/extract", extract)
    server = await aiohttp_server(app)
    provider = TavilySearchProvider(
        api_key="local-fixture", extract_url=str(server.make_url("/extract"))
    )
    edge = await world.watch()
    observe = graph_observe(
        world.engine, lambda: _AT, Metrics(), provider, room_slots=FakeRoomSlots({}),
    )
    await observe.tick()
    assert sent == [
        {"urls": [_URL], "extract_depth": "basic", "format": "markdown", "include_images": True}
    ]
    (item,) = await world.rows(WATCH_RESULT)
    assert item["watch_edge"] == edge
    assert item["content_hash"] == sha256(b"current notice").hexdigest()
