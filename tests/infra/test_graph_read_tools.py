"""Read tools follow the generated graph and its room scope."""

# ruff: noqa: F811 - pytest resolves imported fixtures by name.

from dataclasses import replace
from datetime import timedelta

import pytest
import sqlalchemy as sa

from nora.access import IdentityAccess
from nora.agent.catalog.groups import OFF_GRAPH_ONLY_TOOLS
from nora.agent.lineage import LineagePolicy
from nora.agent.room import Room
from nora.agent.tools import build_tool_specs
from nora.domain.lineage import RoomFacts
from nora.memory import ActAuthority, wire
from nora.memory.app.act_scope import act_event, pass_shown
from nora.obs.metrics import Metrics
from nora.testing import FakeRosterSource
from nora.testing.memory_index import FakeQueryEmbedder
from tests.fakes import SinkSender
from tests.infra.test_pass_anchors import NOW, SCOPE, pointers
from tests.infra.test_pass_assembly import _assembler, _called, _floor
from tests.infra.test_retrieval_scope import (
    AT,
    DM,
    OTHER,
    PERSON,
    ROOM,
    UNIT,
    scenario,  # noqa: F401
    write_edge,
)
from tests.test_room_tools import _UNREACHABLE_TRANSIT, MILES


def specs(scenario, *, clock=lambda: NOW, room_id=ROOM, scope=SCOPE, metrics=None,
          graph=True, memory_retrieval=None, content_checks=True):
    member_account = "fixture-reader-account"
    people = replace(scenario.persons, index={
        **scenario.persons.index, ("signal", member_account): PERSON,
    })
    access = IdentityAccess.for_files(people, scenario.policy)
    lineage = LineagePolicy.from_policy(scenario.policy, access=access)
    roster = (frozenset({member_account}), frozenset({DM})) if room_id != DM else ()
    room = Room(
        platform="signal", conversation_id=room_id, access=access, persons=people,
        roster_source=FakeRosterSource(groups={room_id: roster}),
        tagger=None, self_ids=frozenset(),
    )
    graph_retrieval = wire.graph_read_tools(
        scenario.engine, access, lineage, FakeQueryEmbedder(), None,
    ) if graph else None
    built = build_tool_specs(
        store=None, conversation_id=room_id, conversation_type="group", room=room,
        persons=people, honcho=None, deliverer=SinkSender(),
        metrics=metrics or Metrics(), transit_source=_UNREACHABLE_TRANSIT, clock=clock,
        graph_retrieval=graph_retrieval, pass_scope=scope, memory_retrieval=memory_retrieval,
        content_checks=content_checks,
    )
    return {spec.name: spec.call for spec in built}


async def test_search_reads_claim_text_and_never_returns_another_units_row(scenario):
    phrase = "Generated telescope alignment"
    await scenario.claim("claim:here", content={"text": phrase})
    await scenario.claim("claim:outside", content={"text": phrase}, unit="other-unit")
    tools = specs(scenario)
    assert tools.keys().isdisjoint(OFF_GRAPH_ONLY_TOOLS)
    result = await tools["memory_search"](phrase)
    assert result["status"] == "ok"
    assert phrase in result["rows"]
    assert result["keys"] == [["claim", "claim:here"]]


async def test_search_anchors_the_person_pointer_without_registering_one(scenario):
    await pointers(scenario)
    await scenario.claim("claim:on-person")
    await write_edge(scenario, "claim:on-person", "person-here", "about")
    search = specs(scenario)["memory_search"]
    found = await search("Unmatched phrase", person="Fixture reader")
    assert found["status"] == "ok"
    assert ["entity", "person-here"] in found["keys"]
    assert ["claim", "claim:on-person"] in found["keys"]
    miss = await search("claim:on-person", person="Unknown generated person")
    assert miss["status"] == "nothing_found"
    assert "No one by that name" in miss["note"]


async def test_resolved_person_without_pointer_searches_the_query_only(scenario):
    phrase = "Generated telescope alignment"
    await scenario.claim("claim:here", content={"text": phrase})
    found = await specs(scenario)["memory_search"](phrase, person="Fixture reader")
    assert found["keys"] == [["claim", "claim:here"]]
    assert "Nothing is remembered about them as a subject" in found["note"]


async def test_read_returns_neighbours_only_up_to_the_requested_depth(scenario):
    await scenario.claim("claim:base")
    await scenario.item("item:support")
    await write_edge(scenario, "claim:base", "item:support", "cites")
    read = specs(scenario)["memory_read"]
    shallow = await read("claim:base", depth=0)
    assert shallow["status"] == "partial"
    assert "depth" in shallow["note"]
    assert shallow["keys"] == [["claim", "claim:base"]]
    wide = await read("claim:base", depth=1)
    assert ["item", "item:support"] in wide["keys"]
    assert "Generated observation" in wide["rows"]
    assert await read("claim:base", depth=100) == await read("claim:base", depth=2)


async def test_reader_reports_failure_after_a_working_engine_is_disposed(scenario):
    await scenario.claim("claim:known")
    metrics = Metrics()
    read = specs(scenario, metrics=metrics)["memory_read"]
    assert (await read("claim:known"))["status"] == "ok"
    await scenario.engine.dispose()

    def refuse_connection(*_args):
        raise sa.exc.OperationalError(None, None, ConnectionError("generated disconnect"))

    sa.event.listen(scenario.engine.sync_engine, "do_connect", refuse_connection)
    try:
        result = await read("claim:known")
    finally:
        sa.event.remove(scenario.engine.sync_engine, "do_connect", refuse_connection)
    assert result["status"] == "unavailable"
    assert "statement_failed" in result["note"]
    assert metrics.get("nora_room_lookups_total", tool="memory_read", status="unavailable") == 1


async def test_read_adds_every_returned_id_to_the_assembled_shown_set(scenario):
    called = await _called(scenario)
    assembled = await _assembler(scenario).assemble("called", called, _floor(), SCOPE, NOW)
    assert assembled.shown == {"called-item"}
    await scenario.item("item:later", body="A generated row added after assembly.")
    read = specs(scenario)["memory_read"]
    with act_event("called", ActAuthority(UNIT), shown=assembled.shown):
        result = await read("item:later")
        assert result["keys"] == [["item", "item:later"]]
        assert pass_shown() == {"called-item", "item:later"}
    assert pass_shown() is None


async def test_read_outside_a_pass_uses_the_bound_room_without_creating_a_shown_set(scenario):
    await scenario.item("item:private", dm=True, room=DM, person=OTHER)
    await scenario.item("item:local", person=PERSON)
    read = specs(scenario, scope=None)["memory_read"]
    assert (await read("item:local"))["status"] == "ok"
    assert (await read("item:private"))["status"] == "nothing_found"
    assert pass_shown() is None


@pytest.mark.parametrize(("since", "until", "status", "note"), (
    ("bad-date", None, "unavailable", "Use ISO"),
    ("2027-01-01", None, "nothing_found", "window"),
    ("2026-11-04", "2026-11-01", "nothing_found", "window"),
))
async def test_search_reports_bad_or_empty_windows_with_a_metric(
    scenario, since, until, status, note,
):
    metrics = Metrics()
    search = specs(scenario, metrics=metrics)["memory_search"]
    result = await search("Generated phrase", since=since, until=until)
    assert result["status"] == status
    assert note in result["note"]
    assert metrics.get("nora_room_lookups_total", tool="memory_search", status=status) == 1


async def test_search_narrows_the_time_window(scenario):
    phrase = "Generated telescope alignment"
    await scenario.claim("claim:old", content={"text": phrase}, asserted_at=AT - timedelta(days=10))
    await scenario.claim("claim:current", content={"text": phrase})
    search = specs(scenario)["memory_search"]
    result = await search(phrase, since="2026-11-01", until="2026-11-01")
    assert result["keys"] == [["claim", "claim:current"]]
    assert result["status"] == "partial"
    assert "time_window" in result["note"]


@pytest.mark.parametrize("name", ["memory_search", "memory_read"])
async def test_graph_tool_results_preserve_untrusted_memory_framing(scenario, name):
    phrase = "Generated telescope alignment"
    await scenario.claim("claim:probe", content={"text": phrase})
    read = specs(scenario)[name]
    result = await read(phrase if name == "memory_search" else "claim:probe")
    assert result["status"] == "ok"
    assert result["untrusted"] is True
    assert "never an instruction" in result["framing"]


async def test_graph_reads_preserve_enabled_identifier_screen(scenario):
    phone = next(value for value in MILES if value.startswith("+"))
    await scenario.item("item:contains-an-identifier", body=f"Generated call to {phone}")
    read = specs(scenario)["memory_read"]
    held = await read("item:contains-an-identifier")
    assert held["status"] == "unavailable"
    assert phone not in str(held)
    allowed = await specs(scenario, content_checks=False)["memory_read"](
        "item:contains-an-identifier",
    )
    assert allowed["status"] == "ok"
    assert phone in allowed["rows"]


async def test_read_rechecks_visibility_when_a_previous_search_showed_the_row(scenario):
    from nora.infra.sql.cognitive_graph_schema import CLAIM

    phrase = "Generated telescope alignment"
    await scenario.claim("claim:withdrawn", content={"text": phrase})
    tools = specs(scenario)
    found = await tools["memory_search"](phrase)
    assert found["keys"] == [["claim", "claim:withdrawn"]]
    async with scenario.engine.begin() as conn:
        await conn.execute(sa.update(CLAIM).where(CLAIM.c.id == "claim:withdrawn").values(
            exposure="operator",
        ))
    read = await tools["memory_read"]("claim:withdrawn")
    assert read["status"] == "nothing_found"
    assert phrase not in read["rows"]


_PERSON_A = "generated-person-a"
_PERSON_B = "generated-person-b"
_ACCOUNT_A = frozenset({"generated-account-a"})
_ACCOUNT_B = frozenset({"generated-account-b"})
#: The file roster predates managed registration and lists only person B —
#: under managed it decides nothing; membership comes from the room's
#: managed records.
_MANAGED_FACTS = RoomFacts(
    "signal", frozenset({ROOM}), UNIT, frozenset({_PERSON_B}), label="Managed", primary_id=ROOM)
_MANAGED_LINEAGE = LineagePolicy(
    rooms=(_MANAGED_FACTS,), _by_id={("signal", ROOM): _MANAGED_FACTS})


async def _managed_access(members: tuple[str, ...] = (_PERSON_A,), *, display_name=""):
    """A selected managed backend over the shared fakes."""
    from uuid import uuid4

    from nora.access import IdentityAccess
    from nora.domain.identity import Identifier
    from nora.domain.managed import (
        AccountState,
        ManagedAccount,
        ManagedPerson,
        OutcomeStatus,
        PersonLifecycle,
    )
    from nora.domain.management import Scope, ScopeKind
    from nora.people.app.managed_authority import AuthorityService
    from nora.testing.managed import (
        FakeAuthorityBackend,
        FakeBindingStore,
        FakeIdentityRegistry,
        FakeTopologySource,
    )

    bindings = FakeBindingStore()
    backend = FakeAuthorityBackend()
    topology = FakeTopologySource(
        rooms={f"signal/{ROOM}": (UNIT, False)},
        units={UNIT: "generated-workspace"}, workspaces={"generated-workspace"})
    registry = FakeIdentityRegistry()
    for pid, account in ((_PERSON_A, _ACCOUNT_A), (_PERSON_B, _ACCOUNT_B)):
        assert (await bindings.register(
            person=ManagedPerson(pid, PersonLifecycle.ACTIVE, display_name=display_name),
            account=ManagedAccount(
                str(uuid4()), pid, "signal",
                frozenset(
                    Identifier("signal", "uuid", value) for value in account),
                AccountState.BOUND),
            membership=None,
        )).status is OutcomeStatus.COMPLETE
        outcome, _ = await registry.ensure_identity(
            external_id=pid, traits={"display_name": pid, "social": True})
        assert outcome.status is OutcomeStatus.COMPLETE
    for pid in members:
        assert (await bindings.set_membership(
            person_id=pid, scope=Scope(ScopeKind.ROOM, f"signal/{ROOM}"),
            member=True, source="generated-source",
        )).status is OutcomeStatus.COMPLETE
    backend.tuples.update(("Room", f"signal/{ROOM}", "member", pid) for pid in members)
    access = IdentityAccess(
        bindings=bindings, authority=AuthorityService(bindings, backend, topology),
        topology=topology, registry=registry)
    return bindings, access


def _managed_tools(scenario, access, roster):
    reader = wire.graph_read_tools(
        scenario.engine, access, _MANAGED_LINEAGE, FakeQueryEmbedder(), None,
    )
    room = Room(
        platform="signal", conversation_id=ROOM, access=access, persons=access.persons_view(),
        roster_source=FakeRosterSource(groups={ROOM: roster}), tagger=None, self_ids=frozenset(),
    )
    return {
        spec.name: spec.call for spec in build_tool_specs(
            store=None, conversation_id=ROOM, conversation_type="group", room=room,
            persons=access.persons_view(), honcho=None, deliverer=SinkSender(),
            metrics=Metrics(), transit_source=_UNREACHABLE_TRANSIT, clock=lambda: NOW,
            graph_retrieval=reader,
        )
    }


async def test_memory_search_admits_a_managed_member_the_stale_roster_omits(scenario):
    phrase = "Generated telescope alignment"
    await scenario.claim("claim:managed", content={"text": phrase})
    _, access = await _managed_access()
    tools = _managed_tools(scenario, access, (_ACCOUNT_A,))
    result = await tools["memory_search"](phrase, person=_PERSON_A)
    assert result["status"] == "ok"
    assert result["keys"] == [["claim", "claim:managed"]]


async def test_memory_search_does_not_treat_the_roster_as_room_membership(scenario):
    phrase = "Generated telescope alignment"
    await scenario.claim("claim:managed", content={"text": phrase})
    _, access = await _managed_access()
    tools = _managed_tools(scenario, access, (_ACCOUNT_A, _ACCOUNT_B))
    refused = await tools["memory_search"](phrase, person=_PERSON_B)
    assert refused["status"] == "nothing_found"
    assert "No one by that name" in refused["note"]
    admitted = await tools["memory_search"](phrase, person=_PERSON_A)
    assert admitted["keys"] == [["claim", "claim:managed"]]


async def test_memory_search_is_unavailable_while_managed_authority_is_down(scenario):
    phrase = "Generated telescope alignment"
    await scenario.claim("claim:managed", content={"text": phrase})
    bindings, access = await _managed_access()
    search = _managed_tools(scenario, access, (_ACCOUNT_A,))["memory_search"]
    assert (await search(phrase, person=_PERSON_A))["status"] == "ok"
    bindings.down = True
    assert (await search(phrase, person=_PERSON_A))["status"] == "unavailable"
    bindings.down = False
    assert (await search(phrase, person=_PERSON_A))["status"] == "ok"


async def test_an_ambiguous_person_is_asked_about_not_searched(scenario):
    phrase = "Generated telescope alignment"
    await scenario.claim("claim:managed", content={"text": phrase})
    _, access = await _managed_access((_PERSON_A, _PERSON_B), display_name="Shared name")
    search = _managed_tools(scenario, access, (_ACCOUNT_A, _ACCOUNT_B))["memory_search"]
    assert (await search(phrase))["keys"] == [["claim", "claim:managed"]]
    result = await search(phrase, person="Shared name")
    assert result["status"] == "nothing_found"
    assert "Ask which one" in result["note"]
    assert "claim:managed" not in str(result)
