"""`memory_propose` admission and the backfill surface — the session-runner
tests for `{new:…}` endpoint resolution, refusal custody and the resident
read catalogue, split from `test_cognition_session_runner.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest

from nora.app.cognition_session import CognitionRunResult
from nora.domain.stimuli import UnitView
from tests.unit.test_cognition_session_runner import (
    HOME,
    NOW,
    FakeCognitionAgentRunner,
    _pending,
    _runner,
    _StubRead,
    wake_memory,
)


class _StubReadMemoryRead:
    """A `memory_read`-shaped stand-in, the sibling of `_StubRead`."""

    tool_name = "memory_read"
    tool_type = "python"
    tool_spec: ClassVar = {"name": "memory_read", "description": "generated read",
                 "inputSchema": {"json": {"type": "object", "properties": {}}}}

    async def stream(self, tool_use: Any, invocation_state: Any, **kw: Any) -> Any:
        yield tool_use


async def test_cognition_backfill_surface_is_fully_resident():
    """A historical session holds the whole read catalogue resident — the
    shelf and `load_tools`' per-call name cap are gone — while act tools
    stay withheld (27 §7 rule 2)."""
    class _Agent:
        names: ClassVar[list[str]] = []

        async def run(self, *, tools: list[Any], **kw: Any) -> CognitionRunResult:
            _Agent.names = [
                getattr(t, "tool_name", None) or getattr(t, "__name__", None)
                for t in tools]
            return CognitionRunResult(stop_reason="end_turn")

    runner, _, _ = _runner(
        agent_runner=_Agent(), memory=wake_memory(),
        read_tools_for=lambda unit, rooms, **kw: [_StubRead(), _StubReadMemoryRead()],
        read_only_tools=frozenset({"memory_search", "memory_read"}))
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW, historical=True)
    names = set(_Agent.names)
    assert {"memory_search", "memory_read", "memory_propose"} <= names
    assert "load_tools" not in names
    forbidden = ("note_", "memo_", "event_", "standing_", "wake_", "remind")
    assert not any(n.startswith(forbidden) for n in names)


async def test_new_endpoint_matching_a_candidate_is_refused_possible_existing():
    """`memory_propose` admission runs resolution candidates for a `{new:…}`
    endpoint: a candidate refuses the statement `possible_existing` with the
    candidate handles; `new.distinct` confirms and admits; an exact
    name/alias match stays a hard `duplicate_entity` refusal."""
    from nora.agent.fetch_watch import GraphFetched
    from nora.app.comprehend import (
        Citation,
        MemoryProposalAdmission,
        NewEndpoint,
        NewEntity,
        Proposal,
        RelationStatement,
        Scope,
    )
    from nora.domain.memory_graph import Entity, Relation, Source, WorkItem
    from nora.obs.metrics import Metrics
    from nora.testing.comprehend import InMemoryComprehendRecords
    from nora.testing.memory_graph import FakeMemoryGraph

    graph = FakeMemoryGraph()
    graph.entities["e1"] = Entity("e1", "event", "u", "generated gala ball")
    link = Relation("preview-e1", ("unit", "u:unit:u"), "hosts",
                    ("event", "e1"), "notebook", "u", (),
                    asserted_at=datetime.fromtimestamp(NOW / 1000, UTC))
    graph.relations[link.id] = link
    admission = MemoryProposalAdmission(
        graph=graph, records=InMemoryComprehendRecords(), metrics=Metrics())
    work = WorkItem("w1", ("stimulus", "1"), "u", "h",
                    datetime.fromtimestamp(NOW / 1000, UTC))
    fetched = GraphFetched()
    fetched.show(Source(("message", "1"), "record", "u", (), "going to the gala"))
    scope = Scope(catalogue={1: ("message", "1")})

    def stmt(new_fields: dict, place: dict | None = None) -> RelationStatement:
        return RelationStatement(
            type="relation", rel="at",
            subject=NewEndpoint(new=NewEntity(kind="event", **new_fields)),
            object=NewEndpoint(new=NewEntity(kind="place", **(place or {"name": "Nowhere"}))),
            cite=[Citation(source=1)])

    result, _, aux = await admission._admit(
        work, Proposal(statements=[stmt({"name": "generated gala"})]),
        fetched, 7, scope)
    entry = aux["report"][0]
    assert entry["status"] == "refused" and entry["reason"] == "possible_existing"
    assert entry["candidates"][0]["handle"] == "@event:e1"
    assert ("event", "e1") in aux["candidate_keys"]
    assert aux["possible_existing"] == {("event", "generated gala")}

    # `distinct` is honoured only to confirm a refusal the session already
    # returned for the same name/kind — a first proposal still refuses.
    result, _, aux = await admission._admit(
        work, Proposal(statements=[stmt({
            "name": "generated gala",
            "distinct": "a different gala entirely"})]),
        fetched, 7, scope)
    assert aux["report"][0]["reason"] == "possible_existing"

    result, _, aux = await admission._admit(
        work, Proposal(statements=[stmt({
            "name": "generated gala",
            "distinct": "a different gala entirely"})]),
        fetched, 7, scope,
        confirmed={("event", "generated gala")})
    assert aux["report"][0]["status"] == "saved"
    assert not result.refused

    graph.entities["e2"] = Entity("e2", "event", "u", "exact ball")
    for distinct in ("still mine", None):
        result, _, aux = await admission._admit(
            work, Proposal(statements=[stmt({
                "name": "exact ball", "distinct": distinct},
                place={"name": "somewhere else"})]),
            fetched, 7, scope)
        assert aux["report"][0]["status"] == "refused"
        # The hard refusal path is the store's own duplicate check —
        # `possible_existing` is only ever for approximate matches.
        assert "duplicate_entity" in {
            r.reason for r in aux["refused_by_stmt"].get(0, ())}


@pytest.mark.parametrize("stop", ["end_turn", "limit_turns", "limit_total_tokens",
                                  "limit_output_tokens", "tool_use", None, "error"])
async def test_historical_completion_is_the_session_outcome(stop):
    from nora.domain.stimuli import SessionLimitError

    class Terminal(FakeCognitionAgentRunner):
        async def run(self, **kwargs):
            await super().run(**kwargs)
            if stop == "error":
                raise RuntimeError("generated failure")
            return CognitionRunResult(stop_reason=stop)

    from nora.domain.cognition_ground import Grounding, RecordItem
    from nora.testing.cognition_grounding import StaticGrounder

    memory = wake_memory()
    grounding = Grounding(records={"messages:1": RecordItem(
        ref="messages:1", kind="message", message_id=1, room=HOME,
        author="Generated", at_ms=NOW, text="A generated observation",
    )})
    runner, _, _ = _runner(agent_runner=Terminal(), memory=memory,
                           grounder=StaticGrounder(grounding))
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
                    last_activity_ms=None, last_wake=None)
    if stop == "end_turn":
        await runner.replay(view=view, now_ms=NOW, historical=True)
        receipts = [r for r in memory.records.receipts if r["part"] == "session_complete"]
        assert len(receipts) == 1 and set(receipts[0]["payload"]["inputs"]) == {"1"}
    else:
        with pytest.raises(RuntimeError if stop == "error" else SessionLimitError):
            await runner.replay(view=view, now_ms=NOW, historical=True)
        assert memory.records.receipts == []


def _admission_setup(graph=None):
    """Shared `{new:…}` admission fixture: graph, admission, source, stmt builder."""
    from nora.agent.fetch_watch import GraphFetched
    from nora.app.comprehend import (
        Citation,
        MemoryProposalAdmission,
        NewEndpoint,
        NewEntity,
        Proposal,
        RelationStatement,
        Scope,
    )
    from nora.domain.memory_graph import Source, WorkItem
    from nora.obs.metrics import Metrics
    from nora.testing.comprehend import InMemoryComprehendRecords
    from nora.testing.memory_graph import FakeMemoryGraph

    graph = graph or FakeMemoryGraph()
    admission = MemoryProposalAdmission(
        graph=graph, records=InMemoryComprehendRecords(), metrics=Metrics())
    work = WorkItem("w1", ("stimulus", "1"), "u", "h",
                    datetime.fromtimestamp(NOW / 1000, UTC))
    fetched = GraphFetched()
    fetched.show(Source(("message", "1"), "record", "u", (), "going to the gala"))
    scope = Scope(catalogue={1: ("message", "1")})

    def stmt(new_fields: dict, place: dict | None = None) -> RelationStatement:
        return RelationStatement(
            type="relation", rel="at",
            subject=NewEndpoint(new=NewEntity(kind="event", **new_fields)),
            object=NewEndpoint(new=NewEntity(kind="place", **(place or {"name": "Nowhere"}))),
            cite=[Citation(source=1)])

    def mk(*statements: RelationStatement) -> Proposal:
        return Proposal(statements=list(statements))
    return graph, admission, work, fetched, scope, stmt, mk


async def test_a_failed_candidate_lookup_is_unavailable_never_admission():
    """`new.distinct` never skips the lookup, and a lookup that raises is
    `unavailable`, never an admission — with or without `distinct`."""
    from nora.testing.memory_graph import FakeMemoryGraph

    class _RaisingGraph(FakeMemoryGraph):
        async def candidates(self, **kw: Any) -> tuple:
            raise RuntimeError("index down")

    _graph, admission, work, fetched, scope, stmt, mk = _admission_setup(
        _RaisingGraph())
    for distinct in (None, "a different gala entirely"):
        result, _, aux = await admission._admit(
            work, mk(stmt({
                "name": "generated gala", "distinct": distinct})),
            fetched, 7, scope,
            confirmed={("event", "generated gala")})
        assert aux["report"][0]["status"] == "refused"
        assert aux["report"][0]["reason"] == "unavailable"
        assert not result.relations


async def test_candidate_custody_is_exactly_what_the_cards_emit():
    """A walk can touch more than the three strongest relations the card
    emits: shown custody is only the returned handle and the endpoints of
    the retained relations — never the trimmed-out ones."""
    from dataclasses import replace as _replace

    from nora.domain.memory_graph import Entity, Relation
    from nora.testing.memory_graph import FakeMemoryGraph

    class _WideWalk(FakeMemoryGraph):
        """A store whose walk returns every incident edge, past fan_out."""
        async def walk(self, seeds: Any, query: Any) -> Any:
            return await self._walk(
                seeds, _replace(query, fan_out=99, nodes=99))

    graph, admission, work, fetched, scope, stmt, mk = _admission_setup(
        _WideWalk())
    at = datetime.fromtimestamp(NOW / 1000, UTC)
    graph.entities["e1"] = Entity("e1", "event", "u", "generated gala ball")
    for i in range(4):
        graph.relations[f"r{i}"] = Relation(
            f"r{i}", ("event", "e1"), "at", ("place", f"p{i}"),
            "notebook", "u", (), asserted_at=at)
        at = at.replace(hour=at.hour - 1)

    _result, _, aux = await admission._admit(
        work, mk(stmt({"name": "generated gala"})),
        fetched, 7, scope)
    entry = aux["report"][0]
    assert entry["reason"] == "possible_existing"
    emitted = {
        ("event", "e1"),
        *[k for r in entry["candidates"][0]["relations"]
          for k in (tuple(r["src"][1:].rsplit(":", 1)), tuple(r["dst"][1:].rsplit(":", 1)))]}
    # The card emits three relations; the fourth's endpoint never showed.
    assert aux["candidate_keys"] == emitted
    assert ("place", "p3") not in aux["candidate_keys"]
    assert aux["candidate_relations"] == {
        r["id"] for r in entry["candidates"][0]["relations"]}
