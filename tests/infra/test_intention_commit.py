"""Commit and close generated intentions through the real admission store."""

# ruff: noqa: F811 - pytest resolves imported fixtures by name.

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from nora.agent.ports import adapt
from nora.agent.toolkit.spec import _spec
from nora.infra.sql.cognitive_graph_schema import EDGE, ENTITY, INTENTION, REGISTRY
from nora.infra.sql.cognitive_graph_store import SqlCognitiveGraph
from nora.infra.sql.schema import MESSAGES
from nora.memory import ActAuthority, act_pass
from nora.memory.adapters.tools.intentions import _make_intention_commit
from nora.memory.domain.graph import IntentionForm
from nora.obs.metrics import Metrics
from tests.infra.test_cognitive_graph_admission import (
    ACCOUNT,
    PASS,
    PERSON,
    _rows,
    _service,
    engine,  # noqa: F401
)
from tests.infra.test_condition_matcher import _health
from tests.test_room_tools import _call

_CONTEXT = replace(PASS, occasion="called", writer="engagement")
_WITNESS = "item:person-event"
_TEXT = "The generated work is finished."


def _open(*, form: IntentionForm = "achieve", **changes) -> dict:
    return {
        "op": "open", "form": form, "delivery": "when_it_comes_up",
        "owner": "nora", "owed_to": PERSON.person_id, "citations": ["item-1"],
        "condition": {
            "form": "person_event",
            "value": {
                "person_id": PERSON.person_id, "platform": "signal",
                "room": "room-1", "text": _TEXT,
            },
        },
        **changes,
    }


@contextmanager
def _pass(*shown):
    with act_pass(ActAuthority(PASS.unit, pass_context=_CONTEXT), shown=frozenset(shown)):
        yield


def _tool(engine):
    return _make_intention_commit(SimpleNamespace(
        intention_admission=_service(engine), metrics=Metrics(),
    ))


async def _opened(tool, **fields):
    with _pass("item-1"):
        result = await tool([_open(**fields)])
    assert result["status"] == "ok", result
    return result["operations"][0]["id"]


async def _witness(engine):
    async with engine.begin() as conn:
        await conn.execute(sa.insert(REGISTRY).values(id=_WITNESS, unit=PASS.unit, kind="item"))
        await conn.execute(sa.insert(MESSAGES).values(
            graph_id=_WITNESS, unit=PASS.unit, platform="signal", conversation_id="room-1",
            origin_room="room-1", conversation_type="group", sender_id=ACCOUNT, ts=2000,
            person_id=PERSON.person_id, persons=[PERSON.person_id], body=_TEXT,
        ))
    await _health(engine, "signal", "up", unit=PASS.unit)


async def test_open_stamps_pass_provenance_and_computes_people_links(engine):
    await SqlCognitiveGraph(engine).create(ENTITY, {
        "id": "entity:person", "unit": PASS.unit, "kind": "entity", "basis": "computed",
        "state": "admitted", "writer": "fixture", "exposure": "internal",
        "name": PERSON.name, "pointer_kind": "person", "pointer_ref": PERSON.person_id,
    })
    async with engine.begin() as conn:
        await conn.execute(sa.update(MESSAGES).where(MESSAGES.c.graph_id == "item-1").values(
            exposure="internal", excluded_persons=["generated-exclusion"],
        ))
    graph_id = await _opened(_tool(engine))
    row = (await _rows(engine, INTENTION))[graph_id]
    assert row["exposure"] == "internal"
    assert row["excluded_persons"] == ["generated-exclusion"]
    assert row["state"] == "open"
    assert {name: row[name] for name in _CONTEXT.provenance()} == _CONTEXT.provenance()
    assert {(r["type"], r["dst_id"], r["basis"]) for r in (await _rows(engine, EDGE)).values()} == {
        ("cites", "item-1", "committed"), ("about", "entity:person", "computed"),
    }


async def test_a_citation_outside_the_pass_shown_set_is_refused(engine):
    with _pass():
        result = await _tool(engine)([_open()])
    assert result["status"] == "refused"
    assert result["operations"][0]["reason"] == "citation_not_shown"
    assert await _rows(engine, INTENTION) == {}


async def test_a_malformed_operation_leaves_its_valid_sibling_admitted(engine):
    tool = adapt(_spec("intention_commit", _tool(engine)))
    with _pass("item-1"):
        result = await _call(tool, operations=[_open(about=["entity:topic"]), _open()])
    assert result["status"] == "refused"
    refused, admitted = result["operations"]
    assert (refused["reason"], refused["field"]) == ("invalid_field", "about")
    assert admitted["status"] == "admitted"
    assert set(await _rows(engine, INTENTION)) == {admitted["id"]}


async def test_drop_writes_the_dropped_ending(engine):
    tool = _tool(engine)
    base = await _opened(tool)
    with _pass(base, "item-1"):
        result = await tool([{"op": "drop", "base": base, "citations": ["item-1"]}])
    assert result["status"] == "ok", result
    row = (await _rows(engine, INTENTION))[result["operations"][0]["id"]]
    assert (row["state"], row["ending"], row["replaces"]) == ("ended", "dropped", base)


@pytest.mark.parametrize("ending", ["achieved", "ceased", "leaked", "unknown", "deadline"])
async def test_drop_refuses_any_other_model_supplied_ending(engine, ending):
    tool = _tool(engine)
    base = await _opened(tool)
    with _pass(base):
        result = await tool([{"op": "drop", "base": base, "ending": ending}])
    assert result["status"] == "refused"
    refusal = result["operations"][0]
    assert (refusal["reason"], refusal["field"]) == ("invalid_field", "ending")
    assert (await _rows(engine, INTENTION))[base]["state"] == "open"


async def test_no_active_pass_cannot_commit(engine):
    result = await _tool(engine)([_open()])
    assert result["status"] == "unavailable"
    assert await _rows(engine, INTENTION) == {}


async def test_commit_uncertainty_is_reported_as_unavailable(engine):
    tool = _tool(engine)

    def fail_commit(_conn):
        raise sa.exc.OperationalError(None, None, ConnectionError("generated commit failure"))

    sa.event.listen(engine.sync_engine, "commit", fail_commit)
    try:
        with _pass("item-1"):
            result = await tool([_open()])
    finally:
        sa.event.remove(engine.sync_engine, "commit", fail_commit)
    assert result["status"] == "unavailable"
    assert result["operations"] == [{"status": "unavailable", "commit_uncertain": True}]


async def test_close_uses_the_named_message_and_writes_one_closed_by_edge(engine):
    tool = _tool(engine)
    base = await _opened(tool)
    await _witness(engine)
    with _pass(base, _WITNESS):
        result = await tool([{"op": "close", "base": base, "witness": _WITNESS}])
    assert result["status"] == "ok", result
    assert (await _rows(engine, INTENTION))[base]["ending"] == "achieved"
    with _pass(base, _WITNESS):
        closed = await tool([{"op": "close", "base": base, "witness": _WITNESS}])
    assert closed["operations"][0]["reason"] == "not_open"
    edges = (await _rows(engine, EDGE)).values()
    assert [(r["src_id"], r["dst_id"]) for r in edges if r["type"] == "closed_by"] == [
        (base, _WITNESS),
    ]


async def test_close_refuses_the_wrong_or_unshown_witness(engine):
    tool = _tool(engine)
    base = await _opened(tool)
    await _witness(engine)
    with _pass(base, "item-1"):
        wrong = await tool([{"op": "close", "base": base, "witness": "item-1"}])
        missing = await tool([{"op": "close", "base": base, "witness": _WITNESS}])
    assert wrong["operations"][0]["reason"] == "witness_not_met"
    assert missing["operations"][0]["reason"] == "citation_not_shown"
    assert (await _rows(engine, INTENTION))[base]["state"] == "open"


async def test_close_refuses_an_untyped_condition(engine):
    tool = _tool(engine)
    base = await _opened(
        tool, condition={"form": "untyped", "value": "When the fixture is ready."},
        deadline="2029-01-01T12:00:00Z",
    )
    with _pass(base, "item-1"):
        result = await tool([{"op": "close", "base": base, "witness": "item-1"}])
    assert result["operations"][0]["reason"] == "untyped_condition"
    assert (await _rows(engine, INTENTION))[base]["state"] == "open"


async def test_revise_stores_the_replacement_and_close_refuses_the_stale_base(engine):
    tool = _tool(engine)
    base = await _opened(tool)
    with _pass(base, "item-1"):
        revised = await tool([_open(op="revise", base=base)])
        closed = await tool([{"op": "close", "base": base, "witness": "item-1"}])
    assert revised["status"] == "ok", revised
    current = revised["operations"][0]["id"]
    rows = await _rows(engine, INTENTION)
    assert (rows[current]["replaces"], rows[base]["state"]) == (base, "superseded")
    assert closed["operations"][0]["reason"] == "stale_base"
