"""Schedule and recheck deliveries with real PostgreSQL admission and act rows."""

# ruff: noqa: F811 - pytest resolves imported fixtures by name.

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from nora.agent.ports import adapt
from nora.agent.toolkit.spec import _spec
from nora.domain.models import PersonRef
from nora.infra.sql import create_sql_engine
from nora.infra.sql.cognitive_graph_schema import EDGE, ENTITY, INTENTION
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.item_registration import RegistrationScope, SqlItemRegistration
from nora.memory import ActAuthority, act_pass
from nora.memory.adapters.act_store import SqlActStore
from nora.memory.adapters.admission_rows import SqlAdmissionRows
from nora.memory.adapters.admission_store import SqlAdmissionStore
from nora.memory.adapters.tools.intentions import _make_intention_commit
from nora.memory.app.admission import AdmissionService
from nora.memory.app.admission_identity import AdmissionIdentity
from nora.memory.domain.acts import Act, ActIdentity, Outcome
from nora.memory.domain.admission import Refusal
from nora.memory.domain.admission_ports import Stored
from nora.memory.domain.admission_proposals import CreateIntention, MergeEntities, Scope
from nora.memory.domain.intention_operations import parse_intention_operation
from nora.memory.wire import admission, delivery_arbitration
from nora.obs.metrics import Metrics
from nora.persons import Persons
from nora.policy import Conversation, Policy
from tests.infra.test_act_envelope import _Revisions
from tests.infra.test_cognitive_graph_admission import (
    ACCOUNT,
    PASS,
    PERSON,
    _about,
    _authority,
    _rows,
    engine,  # noqa: F401
)
from tests.infra.test_intention_commit import _open
from tests.test_room_tools import _call

_RECIPIENT = PersonRef("second-recipient", "Generated Recipient", "generated-recipient")
_A = "signal:room-1"
_B = "signal:room-b"
_CONTEXT = replace(PASS, occasion="called")
_TRIGGER = {"kind": "time", "at": "2026-01-01T12:00:00Z", "zone": "UTC"}


def _policy(*, enabled=True, second_unit=PASS.unit):
    _, base, _ = _authority()
    rooms = (
        *(replace(room, proactive=enabled) for room in base.conversations),
        Conversation(
            "signal", frozenset({"room-b"}), None, primary_id="room-b",
            unit=second_unit, proactive=enabled,
        ),
    )
    return Policy(rooms, _by_id={(room.platform, room.primary_id): room for room in rooms})


def _people():
    return Persons(
        {person.person_id: person for person in (PERSON, _RECIPIENT)},
        {("signal", ACCOUNT): PERSON.person_id},
    )


def _tool(engine, graph=None):
    _, _, guard = _authority()
    policy, people = _policy(), _people()
    if graph is None:
        service = admission(engine, people, policy, guard, content_checks=False)
    else:
        identity = AdmissionIdentity(people, policy)
        service = AdmissionService(
            SqlAdmissionStore(SqlAdmissionRows(engine, identity), graph, engine),
            identity, guard, content_checks=False,
        )
    return _make_intention_commit(SimpleNamespace(intention_admission=service, metrics=Metrics()))


def _scheduled(*parents, **fields):
    return _open(
        **{"delivery": "choose_moment", "trigger": _TRIGGER, "serves": list(parents), **fields},
    )


async def _commit(tool, *operations, room=_A, shown=()):
    context = replace(_CONTEXT, room=room)
    with act_pass(
        ActAuthority(PASS.unit, pass_context=context), shown=frozenset(("item-1", *shown)),
    ):
        result = await tool(list(operations))
    return result["operations"]


async def _opened(tool, operation=None, **kwargs):
    entry, = await _commit(tool, operation or _open(), **kwargs)
    assert entry["status"] == "admitted", entry
    return entry["id"]


async def _drop(tool, graph_id):
    entry, = await _commit(tool, {"op": "drop", "base": graph_id}, shown=(graph_id,))
    assert entry["status"] == "admitted", entry


def _duplicate(entry, expected):
    assert (entry["status"], entry.get("reason"), entry.get("field")) == (
        "refused", "duplicate_delivery", expected,
    )


async def _act(engine, graph_id, verdict=None, *, destination=_A):
    registration = SqlItemRegistration(RegistrationScope(_policy(), _people()), _Revisions())
    acts = SqlActStore(engine, registration)
    act = Act(
        ActIdentity("arbitration-fixture", graph_id, 0), "delivery", destination,
        ActAuthority(
            PASS.unit, PERSON.person_id, graph_id, destination, replace(PASS, pass_id=graph_id),
        ),
        "unknown",
    )
    assert await acts.reserve(act)
    if verdict is not None:
        await acts.observe(act, Outcome(verdict))


async def _recheck(engine, graph_id, destination=_A, *, policy=None):
    arbiter = delivery_arbitration(engine, policy or _policy())
    return await arbiter.recheck(graph_id, destination, datetime.now(UTC))


async def test_second_open_conflicts_and_its_sibling_is_admitted(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    first = await _opened(tool, _scheduled(parent), shown=(parent,))
    duplicate, sibling = await _commit(
        tool, _scheduled(parent, citations=[]), _open(), shown=(parent,),
    )
    _duplicate(duplicate, first)
    assert set(await _rows(engine, INTENTION)) == {parent, first, sibling["id"]}


class _ConcurrentGraph(SqlCognitiveGraph):
    def __init__(self, engine, barrier, locked):
        super().__init__(engine)
        self._barrier = barrier
        self._locked = locked

    async def write_admission(self, *args, after_write=None, **kwargs):
        async def coordinated(conn):
            await self._barrier.wait()
            await after_write(conn)
            self._locked.append(await conn.scalar(sa.text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks "
                "WHERE pid = pg_backend_pid() AND locktype = 'advisory' AND granted)"
            )))

        await super().write_admission(*args, after_write=coordinated, **kwargs)


async def test_two_connections_admit_one_delivery(engine, clean_pg_url):
    parent = await _opened(_tool(engine))
    other_engine = create_sql_engine(clean_pg_url)
    barrier, locked = asyncio.Barrier(2), []
    try:
        first, second = await asyncio.gather(*(
            _commit(
                _tool(db, _ConcurrentGraph(db, barrier, locked)),
                _scheduled(parent), room=room, shown=(parent,),
            )
            for db, room in ((engine, _A), (other_engine, _B))
        ))
    finally:
        await other_engine.dispose()
    winner, loser = sorted([*first, *second], key=lambda entry: entry["status"])
    assert winner["status"] == "admitted"
    _duplicate(loser, winner["id"])
    assert locked == [True]
    assert set(await _rows(engine, INTENTION)) == {parent, winner["id"]}


async def test_different_owed_to_is_admitted(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    first = await _opened(tool, _scheduled(parent), shown=(parent,))
    second = await _opened(
        tool, _scheduled(parent, owed_to=_RECIPIENT.person_id), shown=(parent,),
    )
    rows = await _rows(engine, INTENTION)
    assert (rows[first]["owed_to"], rows[second]["owed_to"]) == (
        PERSON.person_id, _RECIPIENT.person_id,
    )


async def test_unresolved_open_is_admitted_without_scanning_prior_acts(engine):
    tool = _tool(engine)
    await SqlCognitiveGraph(engine).create(ENTITY, {
        "id": "entity:delivery-person", "unit": PASS.unit, "kind": "entity",
        "basis": "computed", "state": "admitted", "writer": "fixture", "exposure": "internal",
        "name": PERSON.name, "pointer_kind": "person", "pointer_ref": PERSON.person_id,
    })
    first = await _opened(tool, _scheduled())

    def block_history_read(_conn, _cursor, statement, *_args):
        if "nora_act_outcome" in statement:
            raise sa.exc.OperationalError(None, None, ConnectionError("generated read failure"))

    sa.event.listen(engine.sync_engine, "before_cursor_execute", block_history_read)
    try:
        second = await _opened(tool, _scheduled(), room=_B)
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", block_history_read)
    assert set(await _rows(engine, INTENTION)) == {first, second}
    assert {row["src_id"] for row in (await _rows(engine, EDGE)).values()
            if row["type"] == "about"} == {first, second}


async def test_done_blocks_its_room_until_the_parent_is_revised(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    original = await _opened(tool, _scheduled(parent), shown=(parent,))
    await _act(engine, original, "done")
    await _drop(tool, original)
    same, = await _commit(tool, _scheduled(parent), shown=(parent,))
    _duplicate(same, original)
    elsewhere = await _opened(tool, _scheduled(parent), room=_B, shown=(parent,))
    await _drop(tool, elsewhere)
    current = await _opened(tool, _open(op="revise", base=parent), shown=(parent,))
    latest = await _opened(tool, _scheduled(parent), shown=(parent,))
    rows = await _rows(engine, INTENTION)
    assert rows[current]["replaces"] == parent
    assert rows[latest]["origin_room"] == _A


async def test_revision_cannot_move_onto_another_deliveries_parent(engine):
    tool = _tool(engine)
    parent, other_parent = await _opened(tool), await _opened(tool)
    first = await _opened(tool, _scheduled(parent), shown=(parent,))
    base = await _opened(tool, _scheduled(other_parent), shown=(other_parent,))
    outcome, = await _commit(
        tool, _scheduled(parent, op="revise", base=base), shown=(parent, base),
    )
    _duplicate(outcome, first)
    assert (await _rows(engine, INTENTION))[base]["state"] == "open"


async def test_revision_into_choose_moment_is_arbitrated(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    first = await _opened(tool, _scheduled(parent), shown=(parent,))
    base = await _opened(tool, _open(serves=[parent]), shown=(parent,))
    outcome, = await _commit(
        tool, _scheduled(parent, op="revise", base=base), shown=(parent, base),
    )
    _duplicate(outcome, first)
    assert (await _rows(engine, INTENTION))[base]["delivery_mode"] == "when_it_comes_up"


async def test_firing_act_without_an_outcome_conflicts_across_destinations(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    first = await _opened(tool, _scheduled(parent), shown=(parent,))
    await _act(engine, first)
    await _drop(tool, first)
    outcome, = await _commit(tool, _scheduled(parent), room=_B, shown=(parent,))
    _duplicate(outcome, first)


async def test_revision_can_move_its_trigger_while_its_own_chain_is_firing(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    first = await _opened(tool, _scheduled(parent), shown=(parent,))
    await _act(engine, first)
    base = await _opened(tool, _scheduled(parent, op="revise", base=first), shown=(parent, first))
    replacement = await _opened(
        tool,
        _scheduled(parent, op="revise", base=base, trigger={**_TRIGGER, "at": "2026-02-01T00:00Z"}),
        shown=(parent, base),
    )
    assert (await _rows(engine, INTENTION))[replacement]["replaces"] == base


async def test_two_rooms_commit_together_or_roll_back_when_second_room_conflicts(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    already = await _opened(tool, _scheduled(parent), room=_B, shown=(parent,))
    await _act(engine, already, "done", destination=_B)
    await _drop(tool, already)
    existing = set(await _rows(engine, INTENTION))
    refused, = await _commit(tool, _scheduled(parent, rooms=[_A, _B]), shown=(parent,))
    _duplicate(refused, already)
    assert set(await _rows(engine, INTENTION)) == existing
    clean_parent = await _opened(tool)
    admitted, = await _commit(
        tool, _scheduled(clean_parent, rooms=[_A, _B]), shown=(clean_parent,),
    )
    assert admitted["status"] == "admitted", admitted
    rows = await _rows(engine, INTENTION)
    assert {rows[key]["origin_room"] for key in admitted["ids"]} == {_A, _B}


async def test_only_an_explicit_rooms_list_admits_a_shared_identity_for_two_rooms(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    first = await _opened(tool, _scheduled(parent), shown=(parent,))
    refused, = await _commit(tool, _scheduled(parent), room=_B, shown=(parent,))
    _duplicate(refused, first)
    await _drop(tool, first)
    wrapped = adapt(_spec("intention_commit", tool))
    with act_pass(
        ActAuthority(PASS.unit, pass_context=_CONTEXT), shown=frozenset((parent, "item-1")),
    ):
        result = await _call(wrapped, operations=[_scheduled(parent, rooms=[_A, _B])])
    entry, = result["operations"]
    assert entry["status"] == "admitted", entry
    rows = await _rows(engine, INTENTION)
    assert {rows[key]["origin_room"] for key in entry.get("ids", [])} == {_A, _B}


async def test_done_in_first_room_does_not_withhold_second_rooms_delivery(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    opened, = await _commit(tool, _scheduled(parent, rooms=[_A, _B]), shown=(parent,))
    assert opened["status"] == "admitted", opened
    first, second = opened["ids"]
    await _act(engine, first, "done")
    assert (await _recheck(engine, second, _B)).status == "deliver"


async def test_fresh_due_intention_can_deliver(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    scheduled = await _opened(tool, _scheduled(parent), shown=(parent,))
    assert (await _recheck(engine, scheduled)).status == "deliver"


async def test_superseded_revision_is_withheld(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    base = await _opened(tool, _scheduled(parent), shown=(parent,))
    current = await _opened(
        tool, _scheduled(parent, op="revise", base=base), shown=(base, parent),
    )
    assert (await _recheck(engine, current)).status == "deliver"
    assert (await _recheck(engine, base)).reason == "not_current"


@pytest.mark.parametrize(("verdict", "reason"), [
    ("done", "already_delivered"), ("uncertain", "uncertain_prior"),
])
async def test_recorded_prior_outcome_of_the_same_identity_withholds(engine, verdict, reason):
    tool = _tool(engine)
    parent = await _opened(tool)
    previous = await _opened(tool, _scheduled(parent), shown=(parent,))
    await _drop(tool, previous)
    current = await _opened(tool, _scheduled(parent), shown=(parent,))
    assert (await _recheck(engine, current)).status == "deliver"
    await _act(engine, previous, verdict)
    result = await _recheck(engine, current)
    assert (result.status, result.reason) == ("withhold", reason)


async def test_recheck_withholds_when_room_loses_proactive_permission(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    scheduled = await _opened(tool, _scheduled(parent), shown=(parent,))
    result = await _recheck(engine, scheduled, policy=_policy(enabled=False))
    assert (result.status, result.reason) == ("withhold", "room_not_allowed")


async def test_recheck_withholds_when_the_destination_moves_to_another_unit(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    scheduled = await _opened(tool, _scheduled(parent), room=_B, shown=(parent,))
    assert (await _recheck(engine, scheduled, _B)).status == "deliver"
    moved = await _recheck(engine, scheduled, _B, policy=_policy(second_unit="affiliate"))
    assert (moved.status, moved.reason) == ("withhold", "not_current")


async def test_recheck_reports_a_failed_read_as_unavailable(engine):
    tool = _tool(engine)
    parent = await _opened(tool)
    scheduled = await _opened(tool, _scheduled(parent), shown=(parent,))

    def fail_read(_conn, _cursor, statement, *_args):
        if "nora_act_outcome" in statement:
            raise sa.exc.OperationalError(None, None, ConnectionError("generated read failure"))

    sa.event.listen(engine.sync_engine, "before_cursor_execute", fail_read)
    try:
        result = await _recheck(engine, scheduled)
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", fail_read)
    assert (result.status, result.reason) == ("unavailable", "read_failed")


async def test_merged_entities_and_revised_parents_keep_one_live_subject(engine):
    _, policy, guard = _authority()
    service = admission(engine, _people(), policy, guard, content_checks=False)
    tool = _tool(engine)
    parent = await _opened(tool)
    new_parent = await _opened(tool, _open(op="revise", base=parent), shown=(parent,))
    entity, survivor = "entity:delivery-original", "entity:delivery-survivor"
    for key in (entity, survivor):
        await SqlCognitiveGraph(engine).create(ENTITY, {
            "id": key, "unit": PASS.unit, "kind": "entity", "basis": "inferred",
            "state": "admitted", "writer": "fixture", "exposure": "external", "name": key,
            "reasoning": "deduced", "confidence": "high",
        })
    old = parse_intention_operation(_scheduled(parent), PASS)
    new = parse_intention_operation(_scheduled(new_parent), PASS)
    assert isinstance(old, CreateIntention) and isinstance(new, CreateIntention)
    merge = MergeEntities(
        id="edge:delivery-merge", source=entity, survivor=survivor,
        scope=Scope(PASS.unit), basis="inferred", reasoning="deduced",
        confidence="high", citations=("item-1",),
    )
    first = replace(old, links=(*old.links, _about(old.id, entity)))
    second = replace(new, links=(*new.links, _about(new.id, survivor)))
    shown = ("item-1", parent, new_parent, entity, survivor)
    outcomes = (await service.admit((first, merge, second), PASS, shown)).outcomes
    assert outcomes == (
        Stored(first.id), Stored(merge.id, entity), Refusal("duplicate_delivery", first.id),
    )
