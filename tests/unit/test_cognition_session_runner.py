"""`nora.app.cognition_session.CognitionSessionRunner` — the session loop
that turns a would-wake into ONE Cognition session in shadow.

`FakeCognitionAgentRunner` is the test seam
(`nora.app.cognition_session.CognitionAgentRunner`): it never touches
Strands or a model, and its `.run()` calls the closures it is HANDED by
name — exactly what a real model would do through Strands' tool-calling
loop, with none of the SDK machinery.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest

from nora.app.cognition_session import (
    AdaptorBudget,
    CognitionRunResult,
    CognitionSessionRunner,
    MissingHomeRoomError,
)
from nora.domain.cognition_session import Room
from nora.domain.stimuli import Stimulus, UnitView
from nora.testing.cognition_grounding import EmptyGrounder
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.stimuli import InMemoryStimulusStore

NOW = 1_800_000_000_000


def iso(ms: int) -> str:
    """A session verb's time: ISO 8601 with an offset (M5)."""
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()

HOME = Room("signal", "home-room")
BUDGET = AdaptorBudget(turns=4, total_tokens=20_000, output_tokens=2_000, timeout_seconds=30.0)


@dataclass
class FakeCognitionAgentRunner:
    """Scripted: on `.run()`, calls `script` (a list of `(tool_name, kwargs)`
    pairs) against the tools it was handed, in order, then returns
    `stop_reason`."""

    script: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    calls: int = 0
    results: list[object] = field(default_factory=list)
    last_tool_names: list[str] = field(default_factory=list)

    async def run(
        self, *, system_prompt: str, prompt: str, tools: list[Any],
        budget: AdaptorBudget, trace_attributes: Mapping[str, str],
    ) -> CognitionRunResult:
        self.calls += 1
        by_name = {getattr(t, "__name__", None): t for t in tools}
        self.last_tool_names = sorted(n for n in by_name if n)
        for name, kwargs in self.script:
            self.results.append(await by_name[name](**kwargs))
        return CognitionRunResult(stop_reason="end_turn")


def _pending(unit: str, n: int = 1) -> tuple[Stimulus, ...]:
    return tuple(
        Stimulus(
            id=i, unit=unit, kind="message", item="message", ref=f"messages:{i}",
            at_ms=NOW, recorded_at_ms=NOW, platform="signal", conversation_id="home-room",
        )
        for i in range(1, n + 1)
    )


def _seeded(*wakes: tuple[str, int]) -> InMemoryStimulusStore:
    """A stimulus store holding the wake these tests hand the hook (an outcome
    names a wake that exists); by default wake 1 of `unit-1`."""
    stimuli = InMemoryStimulusStore()
    for unit, wake_id in wakes or (("unit-1", 1),):
        stimuli.seed_wake(unit, wake_id)
    return stimuli


def _runner(
    *, agent_runner: FakeCognitionAgentRunner | None = None,
    store: InMemoryCognitionStore | None = None,
    stimuli: InMemoryStimulusStore | None = None,
    memory: Any = None, grounder: Any = None,
    read_tools_for: Any = None, read_only_tools: frozenset[str] = frozenset(),
) -> tuple[CognitionSessionRunner, InMemoryCognitionStore, FakeCognitionAgentRunner]:
    store = store or InMemoryCognitionStore()
    fake = agent_runner or FakeCognitionAgentRunner()
    runner = CognitionSessionRunner(
        store=store, stimulus_store=stimuli or _seeded(), memory=memory,
        agent_runner=fake, units=("unit-1",), session_timeout_seconds=900.0,
        home_rooms={"unit-1": HOME},
        read_tools_for=read_tools_for or (lambda unit, rooms, **kw: []),
        read_only_tools=read_only_tools,
        skill_text_for=lambda: "", grounder=grounder or EmptyGrounder(),
        doctrine_text="DOCTRINE",
        doctrine_sha256="sha", open_note_titles=_async_const(()),
        open_events=_async_const2(()), prior_standing=_async_const2(None),
        budget=BUDGET, cooldown_seconds=300.0, max_items=12,
        mint_ref=_counting_ref(), clock=lambda: NOW / 1000,
    )
    return runner, store, fake


def _async_const(value):
    async def f(*args, **kwargs):
        return value
    return f


def _async_const2(value):
    async def f(*args, **kwargs):
        return value
    return f


def _counting_ref():
    n = 0
    def f():
        nonlocal n
        n += 1
        return f"ref{n}"
    return f


# --------------------------------------------------------------------------
# off / empty agenda: zero model calls
# --------------------------------------------------------------------------



async def test_an_empty_agenda_makes_no_model_call():
    stimuli = _seeded()
    runner, store, fake = _runner(stimuli=stimuli)
    view = UnitView(unit="unit-1", pending=(), last_activity_ms=None, last_wake=None)
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    assert outcome is None
    assert fake.calls == 0
    assert store.sessions == []
    assert (stimuli.outcomes[1].outcome, stimuli.outcomes[1].detail) == ("closed", "empty agenda")


async def test_a_unit_with_no_home_room_makes_no_model_call_and_is_recorded_failed():
    stimuli = _seeded(("ghost-unit", 2))
    runner, _store, fake = _runner(stimuli=stimuli)
    view = UnitView(
        unit="ghost-unit", pending=_pending("ghost-unit"), last_activity_ms=None, last_wake=None,
    )
    with pytest.raises(MissingHomeRoomError):
        await runner.on_wake(unit="ghost-unit", view=view, now_ms=NOW, wake_id=2)
    assert fake.calls == 0
    assert stimuli.outcomes[2].outcome == "failed"


# --------------------------------------------------------------------------
# a real session: records itself, runs the model exactly once, ends normally
# --------------------------------------------------------------------------

async def test_a_session_with_a_nonempty_agenda_records_itself_and_calls_the_model_once():
    fake = FakeCognitionAgentRunner(script=[])
    runner, store, fake = _runner(agent_runner=fake, stimuli=_seeded(("unit-1", 42)))
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1", n=3), last_activity_ms=None, last_wake=None,
    )
    outcome = await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=42)
    assert fake.calls == 1
    assert outcome is not None
    assert len(store.sessions) == 1
    session_id, session, _ = store.sessions[0]
    assert session.wake_id == 42
    assert session.mode == "live"
    assert session_id == outcome.session_id






async def test_the_tool_list_offers_every_write_pace_and_remind_verb():
    fake = FakeCognitionAgentRunner(script=[])
    runner, _store, fake = _runner(agent_runner=fake)
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    assert set(fake.last_tool_names) >= {
        "note_create", "note_revise", "event_propose", "event_revise",
        "event_set_status", "standing_revise", "wake_at", "wake_move", "wake_cancel",
        "remind", "remind_move", "remind_cancel",
    }




async def test_a_wake_at_call_is_recorded_with_a_minted_ref():
    fake = FakeCognitionAgentRunner(script=[(
        "wake_at", {"subject_rows": ["event:1"], "at": iso(NOW + 3_600_000), "why": "check it"},
    )])
    runner, store, fake = _runner(agent_runner=fake)
    view = UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None,
    )
    await runner.on_wake(unit="unit-1", view=view, now_ms=NOW, wake_id=1)
    assert len(store.paces) >= 1
    _, pace, _ = store.paces[0]
    assert pace.outcome == "recorded"
    assert pace.wake_ref == "ref1"





def wake_memory():
    from nora.app.cognition_memory import WakeMemory
    from nora.obs.metrics import Metrics
    from nora.testing.comprehend import InMemoryComprehendRecords
    from nora.testing.memory_graph import FakeMemoryGraph
    return WakeMemory(graph=FakeMemoryGraph(), records=InMemoryComprehendRecords(),
                      metrics=Metrics())


def proposal(items=None):
    """A statements-only proposal payload."""
    return {"statements": []}


def relation_statement(dst, *, rel="likes", src="@person:p_ada",
                       cite=({"source": 101},)):
    """One relation statement over shown row 101, endpoints as handles."""
    return {"type": "relation", "rel": rel, "subject": src,
            "object": dst if isinstance(dst, dict) else f"@{dst[0]}:{dst[1]}",
            "cite": list(cite)}


async def test_act_is_callable_before_any_memory_proposal():
    fake = FakeCognitionAgentRunner(script=[
        ("wake_at", {"subject_rows": ["event:1"], "at": iso(NOW + 3600000), "why": "check"}),
        ("memory_propose", proposal()),
        ("wake_at", {"subject_rows": ["event:1"], "at": iso(NOW + 3600000), "why": "check"}),
    ])
    runner, store, _ = _runner(agent_runner=fake, memory=wake_memory())
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert fake.results[0]["status"] == "ok"
    assert fake.results[1]["status"] == "ok"
    assert len(store.paces) >= 1


async def test_an_unprojected_message_still_runs_its_wake_on_its_own_text():
    from nora.domain.cognition_ground import Grounding, RecordItem
    from nora.testing.cognition_grounding import StaticGrounder

    stimulus = _pending("unit-1")[0]
    record = RecordItem(ref=stimulus.ref, kind="message", message_id=1, room=HOME,
                        author="Miles", at_ms=NOW, text="downstairs now")
    fake = FakeCognitionAgentRunner(script=[("memory_propose", proposal())])
    runner, _store, _ = _runner(agent_runner=fake, memory=wake_memory(),
                                grounder=StaticGrounder(Grounding(records={stimulus.ref: record})))
    await runner.replay(view=UnitView(unit="unit-1", pending=(stimulus,),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert fake.results[0]["status"] == "ok"


async def test_an_out_of_vocabulary_relation_is_refused_by_the_schema():
    payload = proposal()

    payload["statements"] = [relation_statement(
        ["topic", "generated-topic"], rel="invented",
        src={"new": {"kind": "work", "name": "Generated thing"}})]
    fake = FakeCognitionAgentRunner(script=[("memory_propose", payload)])
    runner, store, _ = _runner(agent_runner=fake, memory=wake_memory())
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert fake.results[0]["status"] == "refused"
    statement = fake.results[0]["statements"][0]
    assert statement["reason"] == "schema"
    assert any("rel" in error for error in statement["errors"])
    assert not store.paces


async def test_cognition_09_backfill_withholds_act_tools():
    runner, store, fake = _runner(memory=wake_memory())
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW, historical=True)
    assert {"memory_propose"} <= set(fake.last_tool_names)
    assert not {"profile_read", "profile_revise"} & set(fake.last_tool_names)
    forbidden = ("note_", "memo_", "event_", "lesson_", "topic_", "decision_",
                 "commitment_", "standing_", "wake_", "remind", "send_")
    assert not any(n.startswith(forbidden) for n in fake.last_tool_names)
    assert not store.paces


class _StubRead:
    """A read-tool-shaped stand-in (`tool_name`/`tool_spec`/`stream`), the
    shape `as_of_reads` recognises and wraps."""

    tool_name = "memory_search"
    tool_type = "python"
    tool_spec: ClassVar = {"name": "memory_search", "description": "generated search",
                 "inputSchema": {"json": {"type": "object", "properties": {}}}}

    async def stream(self, tool_use: Any, invocation_state: Any, **kw: Any) -> Any:
        yield tool_use


async def test_cognition_backfill_reads_the_same_surface_as_of_the_evidence_instant():
    """`historical` no longer blanks the read surface: the session asks the
    SAME `read_tools_for` a live wake does — with a `clock` bound to the
    evidence instant, and every returned tool wrapped in `HistoricalRead` —
    so a shared read reaches the model through one code path."""
    from nora.agent.comprehend_tools import HistoricalRead

    seen: dict[str, Any] = {}

    def reads(unit: str, rooms: Any, **kw: Any) -> list[Any]:
        seen.update(kw)
        return [_StubRead()]

    class _Agent:
        built: ClassVar[list[Any]] = []

        async def run(self, *, tools: list[Any], **kw: Any) -> CognitionRunResult:
            _Agent.built = list(tools)
            return CognitionRunResult(stop_reason="end_turn")

    runner, _, _ = _runner(agent_runner=_Agent(), read_tools_for=reads,
                           read_only_tools=frozenset({"memory_search"}))
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
                    last_activity_ms=None, last_wake=None)
    await runner.replay(view=view, now_ms=NOW, historical=True)
    (record,) = (t for t in _Agent.built if isinstance(t, HistoricalRead))
    assert record.tool_name == "memory_search"
    assert seen["clock"]() == datetime.fromtimestamp(NOW / 1000, UTC)

    # A live wake asks the same source, clockless, with nothing wrapped.
    seen.clear()
    await runner.replay(view=view, now_ms=NOW)
    assert "clock" not in seen
    assert not [t for t in _Agent.built if isinstance(t, HistoricalRead)]


async def test_rows_a_read_shows_become_the_evidence_a_proposal_may_cite():
    """The `on_rows` custody path, end to end at the session boundary: the
    rows a transcript read actually handed out become `("message", id)`
    Sources in `graph_fetched` — so `memory_propose` admits evidence that
    cites a shown row and refuses `unseen_source` for one the read never
    exposed (the withheld body the tool kept out of its result)."""
    from nora.domain.models import DeliveryStatus, TranscriptRow

    memory = wake_memory()
    _seed_event(memory, "u:event:generated-gala", unit="unit-1")
    shown = TranscriptRow(
        id=101, sender_id="+85200000001", sender_name="Ada", ts=NOW,
        body="dinner at eight", is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None, person_id="p_ada")
    withheld = TranscriptRow(
        id=102, sender_id="+85200000002", sender_name="Bob", ts=NOW,
        body=None, is_nora=False,
        delivery_status=DeliveryStatus.SENT, raw_json=None, person_id="p_bob")

    def reads(unit: str, rooms: Any, **kw: Any) -> list[Any]:
        on_rows = kw.get("on_rows")
        assert callable(on_rows)

        async def load_transcript() -> dict[str, Any]:
            on_rows([shown, withheld])
            return {"messages": [{"sender": "Ada", "body": "dinner at eight"}]}
        return [load_transcript]

    def proposing(cited: str) -> dict[str, Any]:
        payload = proposal()

        payload["statements"] = [relation_statement(
            ["event", "u:event:generated-gala"], cite=({"source": int(cited)},))]
        return payload

    fake = FakeCognitionAgentRunner(script=[
        ("load_transcript", {}),
        ("memory_propose", proposing("101")),
        ("memory_propose", proposing("102")),
    ])
    runner, _, _ = _runner(
        agent_runner=fake, memory=memory, read_tools_for=reads,
        read_only_tools=frozenset({"load_transcript"}))
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    shown_refused = {r["reason"] for r in fake.results[1]["refused"]}
    withheld_refused = {r["reason"] for r in fake.results[2]["refused"]}
    assert "unseen_source" not in shown_refused
    assert "unseen_source" in withheld_refused


async def test_probe_prepares_memory_and_uses_the_wakes_instruction():
    from nora.language.comprehend_render import MEMORY_CRITERIA

    class Capture(FakeCognitionAgentRunner):
        async def run(self, **kwargs):
            self.system_prompt = kwargs["system_prompt"]
            return await super().run(**kwargs)

    agent = Capture(script=[("memory_propose", proposal())])
    memory = wake_memory()
    runner, _, _ = _runner(agent_runner=agent, memory=memory)
    await runner.probe(unit="unit-1", summary="a generated review", now_ms=NOW)
    assert MEMORY_CRITERIA in agent.system_prompt
    assert agent.results[0]["status"] == "ok"






async def test_five_failures_park_with_operator_requeue():
    memory = wake_memory()
    pending = _pending("unit-1")
    for _ in range(4):
        await memory.failed("unit-1", [1], None)
    assert await memory.parked("unit-1", pending) == set()
    await memory.failed("unit-1", [1], None)
    assert await memory.parked("unit-1", pending) == {1}
    await memory.records.failures("unit-1", 1, reset=True)
    assert await memory.parked("unit-1", pending) == set()


async def test_input_budget_defers_unseen_stimuli_without_dropping():
    from dataclasses import replace
    memory = wake_memory()
    stimuli = _seeded()
    runner, _, _ = _runner(stimuli=stimuli, memory=memory)
    runner._max_items = 1
    pending = _pending("unit-1", 2)
    pending = (pending[0], replace(pending[1], conversation_id="other-generated-room"))
    await runner.on_wake(unit="unit-1", view=UnitView(unit="unit-1", pending=pending,
        carry={1: 3, 2: 3}, last_activity_ms=None, last_wake=None), now_ms=NOW, wake_id=1)
    assert {c.stimulus_id for _, _, c in stimuli.carries if c.state == "carried"} == {2}




async def test_failed_session_keeps_stimuli_pending_and_counts_failure():
    memory = wake_memory()
    stimuli = _seeded()
    fake = FakeCognitionAgentRunner(script=[("nonexistent_generated_tool", {})])
    runner, _, _ = _runner(agent_runner=fake, stimuli=stimuli, memory=memory)
    with pytest.raises(KeyError):
        await runner.on_wake(unit="unit-1", view=UnitView(unit="unit-1",
            pending=_pending("unit-1"), last_activity_ms=None, last_wake=None),
            now_ms=NOW, wake_id=1)
    assert stimuli.outcomes[1].outcome == "failed"
    assert not stimuli.carries
    assert await memory.records.failures("unit-1", 1) == 1


async def test_schema_refusal_does_not_lock_acts():
    payload = proposal()

    payload["statements"] = [dict(
        relation_statement(["place", "generated-place"], rel="likes",
                           src={"new": {"kind": "work", "name": "Generated thing"}}),
        valid_from="bad-time")]
    fake = FakeCognitionAgentRunner(script=[("memory_propose", payload),
        ("wake_at", {"subject_rows": ["event:1"], "at": iso(NOW + 3600000), "why": "check"})])
    runner, store, _ = _runner(agent_runner=fake, memory=wake_memory())
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert any(r["reason"] == "unparseable_time" for r in fake.results[0]["refused"])
    assert fake.results[1]["status"] == "ok"
    assert store.paces


async def test_retried_link_write_relinks_existing_register_row_by_id(monkeypatch):
    from tests.unit.cognition_rig import GOOD_BODY, NOTE, Stores, session
    memory = wake_memory()
    stores = Stores()
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Source, WorkItem
    from tests.unit.cognition_rig import RECORDS
    for row in RECORDS.values():
        source = Source(("message", str(row.message_id)), "record", "u", (), row.text)
        work = WorkItem("generated-record", source.key, "u", "hash", datetime.now(UTC))
        memory.records.items[source.key] = ComprehendItem(work, (source,), "signal", "room-a",
            snapshot={"messages": [], "context": []})
    links = []

    async def link(state, result, *, targets=()):
        links.append(result["note_id"])
        if len(links) == 1:
            raise RuntimeError("generated link outage after Register commit")
        assert result["note_id"] in stores.notes.notes

    monkeypatch.setattr(memory, "link_result", link)
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**proposal())
        answers.append(await by["note_create"](body=GOOD_BODY, **NOTE))
        answers.append(await by["note_create"](body=GOOD_BODY, **NOTE))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert [a["status"] for a in answers] == ["unavailable", "ok"]
    assert links[0] == links[1] == answers[1]["note_id"]
    assert len(stores.notes.notes) == 1


from tests.unit.cognition_rig import GOOD_BODY, NOTE, Stores, session  # noqa: E402


def _register_load(stores):
    """A `records.load` that serves a register row the way the SQL store
    does — the row's own text as its notebook source plus the record source
    it cites — and refuses anything else, like an unprojected row."""
    from sqlalchemy.exc import NoResultFound

    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Source

    async def load(work):
        kind, ref = work.source
        body = None
        if kind == "note" and ref in stores.notes.notes:
            body = stores.notes.revisions[stores.notes.notes[ref].head_revision_id].body
        if kind == "event_entry" and any(
                str(e.id) == ref for e in stores.gatherings._events.values()):
            body = next(e.title for e in stores.gatherings._events.values()
                        if str(e.id) == ref)
        if body is None:
            raise NoResultFound("generated unprojected register row")
        return ComprehendItem(work, (
            Source(work.source, "notebook", "u", (), body),
            Source(("message", "101"), "record", "u", ("p_ada",), "generated cite"),
        ), "signal", "room-a", snapshot={"messages": [], "context": []})

    return load


def _about_proposal(entity_id: str, relation_id: str = "r-about"):
    payload = proposal()

    payload["statements"] = [relation_statement(
        ["event", entity_id], cite=({"source": 101},))]
    return payload


def _seed_event(memory, entity_id: str, name: str = "generated gala", *,
                shown: bool = True, unit: str = "u"):
    from nora.domain.memory_graph import Entity
    entity = Entity(entity_id, "event", unit, name)
    memory.graph.entities[entity.id] = entity
    if shown:
        _show_in_preview(memory, entity, unit)
    return entity


def _show_in_preview(memory, entity, unit: str = "u") -> None:
    """A unit→node edge puts the node in the wake preview, which is how a
    session honestly sees it: handles and associations name shown keys."""
    from nora.domain.memory_graph import Relation
    link = Relation(
        f"preview-{entity.id}", ("unit", f"{unit}:unit:{unit}"), "hosts",
        entity.key, "notebook", entity.unit, (),
        asserted_at=datetime.fromtimestamp(NOW / 1000, UTC))
    memory.graph.relations[link.id] = link


async def test_alias_free_admitted_association_survives_create_and_later_read():
    from nora.domain.memory_graph import Walk
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    event = _seed_event(memory, "u:event:generated-gala")
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**_about_proposal(event.id))
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", event.id]], **NOTE))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert answers[0]["status"] == "ok"
    note_id = answers[0]["note_id"]
    assert not any("generated gala" in r.body.casefold()
                   for r in stores.notes.revisions.values())
    edge = next(r for r in memory.graph.relations.values()
                if r.src == ("note", note_id))
    assert (edge.rel, edge.dst, edge.unit) == ("concerns", event.key, "u")
    # A later read through the same scoped walk still reaches the association.
    seen = await memory.graph.walk(
        (("note", note_id),),
        Walk(unit="u", persons=frozenset({"p_ada"}), depth=1,
             relations=("concerns",)))
    assert seen.status == "ok"
    assert [e.relation.dst for e in seen.relations] == [event.key]


async def test_explicit_association_excludes_unshown_body_alias():
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    chosen = _seed_event(memory, "u:event:chosen", "chosen gathering")
    unshown = _seed_event(memory, "u:event:unshown", "which place", shown=False)
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**_about_proposal(chosen.id))
        answers.append(await by["note_create"](
            body=GOOD_BODY,
            associations=[["event", chosen.id]], **NOTE))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert answers[0]["status"] == "ok"
    links = [r for r in memory.graph.relations.values()
             if r.src == ("note", answers[0]["note_id"])]
    assert [r.dst for r in links] == [chosen.key]
    assert not any(r.dst == unshown.key for r in links)


async def test_note_revise_replaces_the_semantic_association():
    from nora.domain.memory_graph import Walk
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    first = _seed_event(memory, "u:event:generated-gala")
    second = _seed_event(memory, "u:event:generated-regatta")
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**_about_proposal(first.id, "r1"))
        await by["memory_propose"](**_about_proposal(second.id, "r2"))
        created = await by["note_create"](
            body=GOOD_BODY, associations=[["event", first.id]], **NOTE)
        answers.append(created)
        got = await stores.notes.read(unit="u", note_id=created["note_id"])
        answers.append(await by["note_revise"](
            note_id=created["note_id"], parent_revision_id=got[0].head_revision_id,
            body=GOOD_BODY, associations=[["event", second.id]]))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert [a["status"] for a in answers] == ["ok", "ok"]
    note_id = answers[0]["note_id"]
    later = datetime.fromtimestamp(NOW / 1000 + 3600, UTC)
    seen = await memory.graph.walk(
        (("note", note_id),),
        Walk(unit="u", persons=frozenset({"p_ada"}), depth=1,
             relations=("concerns",), valid_at=later))
    assert [e.relation.dst for e in seen.relations] == [second.key]
    stale = next(r for r in memory.graph.relations.values()
                 if r.src == ("note", note_id) and r.dst == first.key)
    assert stale.valid_to is not None


async def test_event_revise_replaces_association_without_an_orphan_retry():
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    first = _seed_event(memory, "u:event:first", "first gathering")
    second = _seed_event(memory, "u:event:second", "second gathering")
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**_about_proposal(first.id, "r-first"))
        await by["memory_propose"](**_about_proposal(second.id, "r-second"))
        created = await by["event_propose"](
            title="Generated gathering", reasoning="told", confidence="high",
            cited_ids=["101"], associations=[["event", first.id]])
        assert created["status"] == "ok", created
        event_id = str(created["id"])
        event = next(e for e in stores.gatherings._events.values() if str(e.id) == event_id)
        arguments = {
            "event_id": event_id, "base_revision_id": str(event.head_revision_id),
            "venue_text": "Generated venue", "reasoning": "told", "confidence": "high",
            "cited_ids": ["101"], "associations": [["event", second.id]],
        }
        answers.append(await by["event_revise"](**arguments))
        revisions = len(stores.gatherings._revisions)
        answers.append(await by["event_revise"](**arguments))
        assert len(stores.gatherings._revisions) == revisions
        rows = [r for r in memory.graph.relations.values()
                if r.src == ("event_entry", event_id) and r.valid_to is None]
        assert {(r.rel, r.dst) for r in rows} == {
            ("records", ("event", f"u:event:{event_id}")), ("concerns", second.key)}
        assert any(r.dst == first.key and r.valid_to is not None
                   for r in memory.graph.relations.values())

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert [a["status"] for a in answers] == ["ok", "ok"], answers
    assert not any(r[-1] == "orphan_entity" for r in memory.records.refusals)


async def test_unshown_or_out_of_unit_association_cannot_persist():
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**proposal())
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", "u:event:never-shown"]], **NOTE))
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", "other-unit:event:x"]], **NOTE))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert [a["reason"] for a in answers] == ["unseen_association", "unseen_association"]
    assert not stores.notes.notes
    assert not memory.graph.relations


async def test_projection_outage_keeps_created_row_and_retry_relinks_same_association():
    memory = wake_memory()
    # The retry session's preview carries the committed note's UUID id; the
    # content screen is off in production, so this rig runs the same way.
    memory.content_checks = False
    stores = Stores()
    memory.records.load = _register_load(stores)
    event = _seed_event(memory, "u:event:generated-gala")
    answers = []

    async def behave(by, tools):
        await by["memory_propose"](**_about_proposal(event.id))
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", event.id]], **NOTE))

    from nora.app import register_graph
    real_project = register_graph.RegisterGraphLinks.project

    async def outage(self, item, session_id, *, selected=()):
        raise RuntimeError("generated projection outage")

    register_graph.RegisterGraphLinks.project = outage
    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    register_graph.RegisterGraphLinks.project = real_project
    assert answers[-1]["status"] == "unavailable"
    assert len(stores.notes.notes) == 1
    assert not [r for r in memory.graph.relations.values() if r.src[0] == "note"]
    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert answers[-1]["status"] == "ok"
    assert answers[-1]["note_id"] == next(iter(stores.notes.notes))
    assert len(stores.notes.notes) == 1
    assert any(r.src == ("note", answers[-1]["note_id"]) and r.dst == event.key
               for r in memory.graph.relations.values())


async def test_revise_without_associations_keeps_the_semantic_link():
    from nora.domain.memory_graph import Walk
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    event = _seed_event(memory, "u:event:generated-gala")
    answers = []
    note_id = None

    async def behave(by, tools):
        nonlocal note_id
        await by["memory_propose"](**_about_proposal(event.id))
        created = await by["note_create"](
            body=GOOD_BODY, associations=[["event", event.id]], **NOTE)
        note_id = created["note_id"]
        got = await stores.notes.read(unit="u", note_id=note_id)
        answers.append(await by["note_revise"](
            note_id=note_id, parent_revision_id=got[0].head_revision_id,
            body=GOOD_BODY))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert answers[0]["status"] == "ok"
    seen = await memory.graph.walk(
        (("note", note_id),),
        Walk(unit="u", persons=frozenset({"p_ada"}), depth=1,
             relations=("concerns",), valid_at=datetime.fromtimestamp(
                 NOW / 1000 + 3600, UTC)))
    assert [e.relation.dst for e in seen.relations] == [event.key]


async def test_reselecting_a_closed_association_relinks_with_a_new_relation():
    from nora.domain.memory_graph import Walk
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    first = _seed_event(memory, "u:event:generated-gala")
    second = _seed_event(memory, "u:event:generated-regatta")
    answers = []
    note_id = None

    async def behave(by, tools):
        nonlocal note_id
        await by["memory_propose"](**_about_proposal(first.id, "r1"))
        await by["memory_propose"](**_about_proposal(second.id, "r2"))
        created = await by["note_create"](
            body=GOOD_BODY, associations=[["event", first.id]], **NOTE)
        note_id = created["note_id"]
        for assoc in (second, first):
            got = await stores.notes.read(unit="u", note_id=note_id)
            answers.append(await by["note_revise"](
                note_id=note_id, parent_revision_id=got[0].head_revision_id,
                body=GOOD_BODY, associations=[["event", assoc.id]]))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert [a["status"] for a in answers] == ["ok", "ok"]
    seen = await memory.graph.walk(
        (("note", note_id),),
        Walk(unit="u", persons=frozenset({"p_ada"}), depth=1,
             relations=("concerns",), valid_at=datetime.fromtimestamp(
                 NOW / 1000 + 3600, UTC)))
    assert [e.relation.dst for e in seen.relations] == [first.key]
    ids = {r.id for r in memory.graph.relations.values()
           if r.src == ("note", note_id) and r.dst == first.key}
    assert len(ids) == 2  # the re-added link is a new generation, not a duplicate


async def test_revise_with_empty_associations_clears_the_link():
    from nora.domain.memory_graph import Walk
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    event = _seed_event(memory, "u:event:generated-gala", "going ahead")
    answers = []
    note_id = None

    async def behave(by, tools):
        nonlocal note_id
        await by["memory_propose"](**_about_proposal(event.id))
        created = await by["note_create"](
            body=GOOD_BODY, associations=[["event", event.id]], **NOTE)
        note_id = created["note_id"]
        got = await stores.notes.read(unit="u", note_id=note_id)
        answers.append(await by["note_revise"](
            note_id=note_id, parent_revision_id=got[0].head_revision_id,
            body=GOOD_BODY, associations=[]))

    await session(behave, stores=stores, runner_kwargs={"memory": memory})
    assert answers[0]["status"] == "ok"
    seen = await memory.graph.walk(
        (("note", note_id),),
        Walk(unit="u", persons=frozenset({"p_ada"}), depth=1,
             relations=("concerns",), valid_at=datetime.fromtimestamp(
                 NOW / 1000 + 3600, UTC)))
    assert seen.relations == ()


async def test_revise_commit_then_projection_crash_retries_and_relinks():
    from nora.app import register_graph
    memory = wake_memory()
    # As above: the later sessions' preview carries the committed note's
    # UUID id; the content screen is off in production.
    memory.content_checks = False
    stores = Stores()
    memory.records.load = _register_load(stores)
    first = _seed_event(memory, "u:event:generated-gala")
    second = _seed_event(memory, "u:event:generated-regatta")
    answers = []
    note_id = None
    head = None

    async def create_behave(by, tools):
        nonlocal note_id, head
        await by["memory_propose"](**_about_proposal(first.id, "r1"))
        await by["memory_propose"](**_about_proposal(second.id, "r2"))
        created = await by["note_create"](
            body=GOOD_BODY, associations=[["event", first.id]], **NOTE)
        note_id = created["note_id"]
        head = (await stores.notes.read(unit="u", note_id=note_id))[0].head_revision_id

    async def revise_behave(by, tools):
        await by["memory_propose"](**_about_proposal(first.id, "r1"))
        await by["memory_propose"](**_about_proposal(second.id, "r2"))
        answers.append(await by["note_revise"](
            note_id=note_id, parent_revision_id=head, body=GOOD_BODY,
            associations=[["event", second.id]]))

    real_project = register_graph.RegisterGraphLinks.project

    async def outage(self, item, session_id, *, selected=()):
        raise RuntimeError("generated projection outage")

    await session(create_behave, stores=stores, runner_kwargs={"memory": memory})
    register_graph.RegisterGraphLinks.project = outage
    await session(revise_behave, stores=stores, runner_kwargs={"memory": memory})
    register_graph.RegisterGraphLinks.project = real_project
    assert answers[-1]["status"] == "unavailable"
    # The receipt committed with the revision: the identical retry replays it
    # rather than meeting a stale_base on the row it already wrote.
    await session(revise_behave, stores=stores, runner_kwargs={"memory": memory})
    assert answers[-1]["status"] == "ok"
    assert [r.dst for r in memory.graph.relations.values()
            if r.src == ("note", note_id) and r.valid_to is None] == [second.key]


async def test_association_shown_only_by_a_memory_search_is_admissible():
    from strands.types._events import ToolResultEvent
    memory = wake_memory()
    stores = Stores()
    memory.records.load = _register_load(stores)
    event = _seed_event(memory, "u:event:search-gala")
    answers = []

    class SearchTool:
        tool_name = "memory_search"
        tool_type = "python"
        tool_spec: ClassVar = {"name": "memory_search", "description": "generated search",
                     "inputSchema": {"json": {"type": "object", "properties": {}}}}

        async def stream(self, tool_use, invocation_state, **kwargs):
            yield ToolResultEvent({"toolUseId": tool_use["toolUseId"],
                "content": [{"json": {"status": "ok",
                    # A returned node's body may MENTION a key it never
                    # returned: prose is not shown-ness; `keys` is.
                    "preview": "event:u:event:search-gala · prose says event:u:event:forged",
                    "keys": [["event", "u:event:search-gala"]]}}]})

    async def behave(by, tools):
        async for _ in by["memory_search"].stream({"toolUseId": "t1", "input": {}}, {}):
            pass
        await by["memory_propose"](**proposal())
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", "u:event:forged"]], **NOTE))
        answers.append(await by["note_create"](
            body=GOOD_BODY, associations=[["event", event.id]], **NOTE))

    await session(behave, stores=stores, runner_kwargs={
        "memory": memory,
        "read_tools_for": lambda unit, rooms: [SearchTool()],
        "read_only_tools": frozenset({"memory_search"})})
    assert answers[0]["status"] == "refused" and answers[0]["reason"] == "unseen_association"
    assert answers[1]["status"] == "ok"
    assert any(r.src[0] == "note" and r.dst == event.key
               for r in memory.graph.relations.values())


async def test_wake_preview_admits_only_the_keys_the_renderer_emitted():
    """`prepare` must not admit a key the budget cut: `shown_keys` is the
    renderer's emitted rows, not the whole walked subgraph. Two chosen keys,
    checked against the actual emitted preview, are the independent control."""
    from dataclasses import replace
    from types import SimpleNamespace

    from nora.agent.fetch_watch import GraphFetched
    from nora.domain.cognition_ground import Grounding, RecordItem
    from nora.domain.memory_graph import Entity, Relation
    memory = wake_memory()
    at = datetime.fromtimestamp(NOW / 1000, UTC)
    # Two hops of twenty leaves under five hubs: the depth-2 walk reaches ~100
    # nodes but the preview budget renders far fewer, so the last walked leaf
    # is a key the walk had and the model was never shown.
    for h in range(5):
        hub = Entity(f"u:place:h{h}", "place", "u", "Long generated label " * 10)
        memory.graph.entities[hub.id] = hub
        memory.graph.relations[f"hub{h}"] = Relation(
            f"hub{h}", ("message", "1"), "concerns", hub.key, "record", "u",
            asserted_at=at)
        for i in range(30):
            leaf = Entity(f"u:place:l{h}_{i}", "place", "u", "Long generated label " * 10)
            memory.graph.entities[leaf.id] = leaf
            memory.graph.relations[f"l{h}_{i}"] = Relation(
                f"l{h}_{i}", hub.key, "concerns", leaf.key, "notebook", "u",
                asserted_at=at)
    state = SimpleNamespace(now_ms=NOW, unit="u", session_id=1, historical=False,
        items_by_index={0: SimpleNamespace(stimulus_ids=(1,), room=HOME)},
        memory_items={}, graph_fetched=GraphFetched(),
        admitted_relations={}, admitted_evidence=[], shown_keys=set())
    await memory.prepare(state, {1: replace(_pending("u")[0], kind="message", ref="messages:1")},
        Grounding(records={"messages:1": RecordItem(
            ref="messages:1", kind="message", message_id=1, room=HOME,
            author="Generated author", person_id="generated-author",
            at_ms=NOW, text="Generated message")}))
    # The emitted preview is the shown surface: hub h0 was rendered, a deep
    # walked leaf was cut. Both keys are chosen independently of the state.
    assert "place:u:place:h0" in state.graph_text
    assert "place:u:place:l3_19" not in state.graph_text
    assert ("place", "u:place:h0") in state.shown_keys
    assert ("place", "u:place:l3_19") not in state.shown_keys


async def test_stays_and_plays_join_the_same_proposal_coverage():
    from dataclasses import replace
    from types import SimpleNamespace

    from nora.agent.fetch_watch import GraphFetched
    from nora.domain.cognition_ground import Grounding
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Source, WorkItem
    memory = wake_memory()
    play = replace(_pending("unit-1")[0], kind="play", ref="play:generated-play")
    stay = replace(play, id=2, kind="rollup", ref="arrival|generated-place|generated-person|1000")
    source = Source(("play", "generated-play"), "record", "unit-1", (), "Generated track")
    work = WorkItem("generated-play", source.key, "unit-1", "hash", datetime.now(UTC))
    memory.records.items[source.key] = ComprehendItem(work, (source,), "signal", "home-room",
        snapshot={"messages": [], "context": []})
    state = SimpleNamespace(now_ms=NOW, unit="unit-1", session_id=1, historical=False,
        items_by_index={0: SimpleNamespace(stimulus_ids=(1, 2), room=HOME)}, memory_items={},
        graph_fetched=GraphFetched(), admitted_relations={},
        admitted_evidence=[], shown_keys=set())
    await memory.prepare(state, {1: play, 2: stay}, Grounding())
    assert {s.key[0] for s in state.memory_items[0].sources} == {"play", "rollup"}
    assert "Generated track" in next(s.text for s in state.memory_items[0].sources
                                     if s.key[0] == "play")
    answer = await memory.propose(state,  statements=[])
    assert answer["status"] == "ok"


async def test_inspection_renders_real_wake_clock_room_and_open_items_without_model():
    from nora.domain.cognition_ground import EventLine, Grounding, NoteLine, RecordItem
    from nora.testing.cognition_grounding import StaticGrounder

    grounding = Grounding(
        records={"messages:1": RecordItem(ref="messages:1", kind="message", message_id=1,
            room=HOME, author="Generated speaker", at_ms=NOW, text="I arrived")},
        notes=(NoteLine(id="generated-note", title="Hand over generated gifts",
                        description="Generated open handover"),),
        events=(EventLine(id="generated-event", room=HOME, title="Generated dinner",
                         when_ms=NOW, venue="Generated venue", status="proposed"),),
        room_labels={"signal:home-room": "Generated room"})
    runner, _, agent = _runner(grounder=StaticGrounder(grounding))
    inspection = await runner.inspect(view=UnitView(
        unit="unit-1", pending=_pending("unit-1"), last_activity_ms=None, last_wake=None),
        now_ms=NOW)
    assert set(inspection) == {"system", "agenda", "work"}
    assert inspection["work"] == []
    assert "DOCTRINE" in inspection["system"]
    for value in ("Generated room", "I arrived", "Hand over generated gifts", "Generated dinner"):
        assert value in inspection["agenda"]
    assert "clock" in inspection["agenda"] and "2027" in inspection["agenda"]
    assert agent.calls == 0


async def test_an_inspection_records_no_session_outcome_or_carry():
    """An inspection is a read of what a session would be run on: it must
    leave the shared wake sandbox exactly as it found it — no session row,
    no wake outcome, no carry, and the stimuli still pending."""
    stimuli = _seeded()
    runner, store, agent = _runner(stimuli=stimuli)
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
                    last_activity_ms=None, last_wake=None)
    inspection = await runner.inspect(view=view, now_ms=NOW)
    assert inspection is not None and set(inspection) == {"system", "agenda", "work"}
    assert inspection["work"] == []
    assert store.sessions == []
    assert stimuli.outcomes == {}
    assert stimuli.carries == []
    assert agent.calls == 0


async def test_inspection_of_an_empty_agenda_is_none_and_calls_no_model():
    runner, _, agent = _runner()
    assert await runner.inspect(view=UnitView(
        unit="unit-1", pending=(), last_activity_ms=None, last_wake=None),
        now_ms=NOW) is None
    assert agent.calls == 0


async def test_inspected_wake_injects_implicit_senders_plan():
    from nora.domain.cognition_ground import Grounding, RecordItem
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Entity, Relation, Source, WorkItem, pointer_entity
    from nora.testing.cognition_grounding import StaticGrounder

    memory = wake_memory()
    at = datetime.fromtimestamp(NOW / 1000, UTC)
    actor = pointer_entity("person", "unit-1", "generated-actor", "Generated actor")
    event = Entity("generated-event", "event", "unit-1", "Generated lunar outing")
    other = Entity("other-event", "event", "other-unit", "Forbidden outing")
    for e in (actor, event, other):
        await memory.graph.upsert_entity(e)
    memory.graph.relations["plan"] = Relation("plan", actor.key, "plans", event.key,
        "notebook", "unit-1", ("generated-actor",), asserted_at=at)
    memory.graph.relations["foreign"] = Relation("foreign", actor.key, "plans", other.key,
        "notebook", "other-unit", ("generated-actor",), asserted_at=at)
    # Structural Record edges must not turn an actor into all their messages.
    memory.graph.relations["hub"] = Relation("hub", ("message", "99"), "authored_by",
        actor.key, "record", "unit-1", ("generated-actor",), asserted_at=at)
    source = Source(("message", "1"), "record", "unit-1", ("generated-actor",), "Yes, count me in")
    memory.records.items[source.key] = ComprehendItem(
        WorkItem("generated", source.key, "unit-1", "", at), (source,),
        "signal", "home-room", author="generated-actor", reply_parent=("message", "2"),
        mentions=("generated-mention",), urls=("https://generated.example/event",),
        snapshot={"messages": [{"id": 1, "body": source.text, "ts": NOW,
                    "person_id": "generated-actor", "mentions": ["generated-mention"]}]})
    grounding = Grounding(records={"messages:1": RecordItem(ref="messages:1", kind="message",
        message_id=1, room=HOME, author="Generated actor", person_id="generated-actor",
        at_ms=NOW, text=source.text)})
    runner, _, agent = _runner(memory=memory, grounder=StaticGrounder(grounding))
    inspection = await runner.inspect(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert "Generated lunar outing" in inspection["agenda"]
    assert "plans" in inspection["agenda"]
    assert "Forbidden outing" not in inspection["agenda"]
    assert "message:99" not in inspection["agenda"]
    assert "generated-actor" in inspection["agenda"]
    assert agent.calls == 0


async def test_mixed_agenda_keeps_message_room_and_author():
    from dataclasses import replace
    from types import SimpleNamespace

    from nora.agent.fetch_watch import GraphFetched
    from nora.domain.cognition_ground import Grounding, RecordItem
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Entity, Relation, Source, WorkItem, pointer_entity

    memory = wake_memory()
    at = datetime.fromtimestamp(NOW / 1000, UTC)
    social = replace(_pending("unit-1")[0], kind="social", ref="social:generated-post")
    message = replace(social, id=2, kind="message", ref="messages:1")
    for key, platform, room, author in (
        (("social", "generated-post"), "instagram", "generated-feed", ""),
        (("message", "1"), HOME.platform, HOME.conversation_id, "generated-author"),
    ):
        source = Source(key, "record", "unit-1", (), "Generated stimulus")
        memory.records.items[key] = ComprehendItem(
            WorkItem("generated", key, "unit-1", "", at), (source,), platform, room,
            author=author, response_closed=True,
            snapshot={"messages": [], "context": []})
    room = pointer_entity("room", "unit-1", "signal:home-room", "Generated room")
    event = Entity("room-event", "event", "unit-1", "Generated room plan")
    for e in (room, event):
        await memory.graph.upsert_entity(e)
    memory.graph.relations["room-plan"] = Relation("room-plan", room.key, "plans", event.key,
        "notebook", "unit-1", asserted_at=at)
    state = SimpleNamespace(now_ms=NOW, unit="unit-1", historical=False,
        items_by_index={0: SimpleNamespace(stimulus_ids=(1, 2), room=HOME)},
        memory_items={}, graph_fetched=GraphFetched(), shown_keys=set())
    await memory.prepare(state, {1: social, 2: message}, Grounding(records={
        "messages:1": RecordItem(ref="messages:1", kind="message", message_id=1,
            room=HOME, author="Generated author", person_id="generated-author",
            at_ms=NOW, text="Generated message")}))
    item = state.memory_items[0]
    assert (item.platform, item.room, item.author) == ("signal", "home-room", "generated-author")
    assert not item.response_closed
    assert "Generated room plan" in state.graph_text


async def test_identifier_graph_node_is_dropped_from_wake_input():
    from nora.domain.cognition_ground import Grounding, RecordItem
    from nora.domain.memory_graph import Entity, Relation, pointer_entity
    from nora.testing.cognition_grounding import StaticGrounder

    memory = wake_memory()
    at = datetime.fromtimestamp(NOW / 1000, UTC)
    actor = pointer_entity("person", "unit-1", "generated-actor", "Generated actor")
    identifier = "+" + "852" + "55550123"
    unsafe = Entity("generated-contact", "event", "unit-1", "Contact " + identifier)
    for e in (actor, unsafe):
        await memory.graph.upsert_entity(e)
    memory.graph.relations["contact"] = Relation("contact", actor.key, "plans", unsafe.key,
        "notebook", "unit-1", ("generated-actor",), asserted_at=at)
    grounding = Grounding(records={"messages:1": RecordItem(ref="messages:1", kind="message",
        message_id=1, room=HOME, author="Generated actor", person_id="generated-actor",
        at_ms=NOW, text="Generated safe message")})
    runner, _, agent = _runner(memory=memory, grounder=StaticGrounder(grounding))
    view = UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None)
    inspection = await runner.inspect(view=view, now_ms=NOW)
    assert "Generated safe message" in inspection["agenda"]
    assert identifier not in inspection["agenda"]
    assert memory.metrics.get("nora_errors_total", stage="wake_prefetch") == 1
    assert agent.calls == 0
    memory.content_checks = False
    inspection = await runner.inspect(view=view, now_ms=NOW)
    assert identifier in inspection["agenda"]


async def test_native_transcript_rendered_file_text_is_the_proposals_evidence():
    from nora.agent.room import Room as ToolRoom
    from nora.agent.tools import build_tool_specs
    from nora.domain.models import NormalizedMessage
    from nora.obs.metrics import Metrics
    from nora.persons import Persons
    from nora.testing import FakeMemoryStore, FakeRecordStore, FakeRosterSource
    from tests.fakes import SinkSender

    raw = "dinner plan https://docs.google.com/document/d/generatedfile123/edit"
    store = FakeRecordStore()
    message_id = await store.append(message=NormalizedMessage(
        "signal", "home-room", "group", "generated-sender", "Ada", NOW - 1, raw, {}),
        person_id="p_ada")
    persons = Persons(by_person={}, index={})
    room = ToolRoom(platform="signal", conversation_id="home-room", persons=persons,
                    roster_source=FakeRosterSource())
    memory = wake_memory()
    from nora.domain.memory_graph import Entity, pointer_entity

    person = pointer_entity("person", "unit-1", "p_ada", "Ada")
    event = Entity("unit-1:event:generated-gala", "event", "unit-1", "generated gala")
    memory.graph.entities.update({person.id: person, event.id: event})
    _show_in_preview(memory, event, "unit-1")
    responses = []

    def reads(unit, rooms, **kw):
        specs = build_tool_specs(
            store=store, conversation_id="home-room", conversation_type="group",
            persons=persons, honcho=FakeMemoryStore(), deliverer=SinkSender(),
            metrics=Metrics(), room=room, transit_source=None,
            clock=lambda: datetime.fromtimestamp(NOW / 1000, UTC),
            on_rows=kw["on_rows"])
        native = next(spec.call for spec in specs if spec.name == "load_transcript")

        async def load_transcript():
            answer = await native()
            responses.append(answer)
            return answer
        return [load_transcript]

    async def behave(by, tools):
        await by["load_transcript"]()
        shown = responses[-1]["messages"][0]["body"]
        assert shown != raw and "docs.google.com" not in shown
        for span in (shown, raw):
            payload = proposal()

            payload["statements"] = [relation_statement(
                list(event.key), rel="likes", src="@" + ":".join(person.key),
                cite=({"source": message_id, "quote": span},))]
            answer = await by["memory_propose"](**payload)
            responses.append(answer)

    from tests.unit.cognition_rig import Script

    runner, _, _ = _runner(
        agent_runner=Script(behave), memory=memory, read_tools_for=reads,
        read_only_tools=frozenset({"load_transcript"}))
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
        last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert responses[1]["status"] == "ok", responses[1]
    assert responses[1]["statements"][0]["status"] == "saved"
    assert any(r["reason"] == "span_not_in_source" for r in responses[2]["refused"])


async def test_wall_clock_timeout_names_the_terminal_bound(monkeypatch):
    import asyncio
    import json
    from dataclasses import replace

    from opentelemetry import context

    from nora.app.cognition_session import SessionTimeoutError
    from nora.obs.tracing import EVAL_EVIDENCE_KEY

    runner, _, fake = _runner()
    runner._budget = replace(BUDGET, timeout_seconds=0.01)
    cancelled = []

    async def blocked(**kwargs):
        fake.calls += 1
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(fake, "run", blocked)
    events = []

    def record(kind, **payload):
        events.append({"kind": kind, **payload})

    token = context.attach(context.set_value(EVAL_EVIDENCE_KEY, record))
    try:
        with pytest.raises(SessionTimeoutError):
            await runner.on_wake(unit="unit-1", view=UnitView(
                unit="unit-1", pending=_pending("unit-1"), last_activity_ms=NOW, last_wake=None),
                now_ms=NOW, wake_id=1)
    finally:
        context.detach(token)
    assert fake.calls == 1 and cancelled == [True]
    started = next(e for e in events if e.get("name") == "nora.session.started")
    assert json.loads(started["attributes"]["body"])["budgets"]["timeout_seconds"] == 0.01
    terminal = next(e for e in events if e.get("name") == "nora.session.terminal")
    body = json.loads(terminal["attributes"]["body"])
    assert body["error_type"] == "SessionTimeoutError"
    assert body["bound"] == "timeout_seconds"


async def test_inspection_social_read_scope_uses_expanded_rendered_sources():
    import json
    from dataclasses import replace

    from opentelemetry import context

    from nora.agent.punctuation import undash
    from nora.domain.cognition_ground import Grounding, SocialLine
    from nora.domain.comprehend import ComprehendItem
    from nora.domain.memory_graph import Source, WorkItem
    from nora.obs.tracing import EVAL_EVIDENCE_KEY
    from nora.testing.cognition_grounding import StaticGrounder
    from scripts.memory_graph_spike import recorded_fact_sessions

    memory = wake_memory()
    stimulus = replace(_pending("unit-1")[0], kind="social", ref="social_read:generated-read")
    sources = tuple(Source(("social", f"generated-network:{item}"), "record", "unit-1",
                           (), f"Generated caption {item}") for item in ("one", "two"))
    work = WorkItem("generated", ("social_read", "generated-read"), "unit-1", "",
                    datetime.fromtimestamp(NOW / 1000, UTC))
    memory.records.items[work.source] = ComprehendItem(
        work, sources, HOME.platform, HOME.conversation_id, author="generated-author",
        snapshot={"messages": [], "context": [], "metadata": {"taken_at": NOW}})
    grounding = Grounding(socials={stimulus.ref: SocialLine(
        stimulus.ref, "Generated author", "generated-author", "story", NOW)})
    runner, _, agent = _runner(memory=memory, grounder=StaticGrounder(grounding))
    token = context.attach(context.set_value(EVAL_EVIDENCE_KEY, lambda *a, **kw: None))
    try:
        inspection = await runner.inspect(view=UnitView(
            unit="unit-1", pending=(stimulus,), last_activity_ms=None, last_wake=None),
            now_ms=NOW, historical=True)
    finally:
        context.detach(token)
    assert agent.calls == 0
    assert inspection is not None
    assert all(source.text in inspection["agenda"] for source in sources)
    events = [{"kind": "event", "name": "nora.session.started", "span_id": "generated",
               "attributes": {"body": json.dumps({"session_id": 1,
                   "system": inspection["system"], "agenda": inspection["agenda"]})}},
              {"kind": "event", "name": "nora.model.request", "span_id": "generated",
               "attributes": {"nora.model.request.body": json.dumps({"messages": [
                   {"role": "system", "content": undash(inspection["system"])},
                   {"role": "user", "content": undash(inspection["agenda"])}]})}},
              {"kind": "event", "name": "nora.session.terminal", "span_id": "generated",
               "attributes": {"body": json.dumps({"session_id": 1})}}]
    recorded = recorded_fact_sessions(events)[0]["inspection"]["new_sources"]
    assert {tuple(row["key"]) for row in recorded.values()} == {s.key for s in sources}
    assert inspection["new_sources"] == recorded


async def test_session_manifest_keeps_pending_stimulus_refs():
    import json
    from dataclasses import replace

    from opentelemetry import context

    from nora.obs.tracing import EVAL_EVIDENCE_KEY

    runner, _, agent = _runner()
    pending = (_pending("unit-1")[0], replace(_pending("unit-1", 2)[1],
               kind="social", ref="social_read:generated-read"))
    events = []

    def record(kind, **payload):
        events.append({"kind": kind, **payload})

    token = context.attach(context.set_value(EVAL_EVIDENCE_KEY, record))
    try:
        await runner.on_wake(unit="unit-1", view=UnitView(
            unit="unit-1", pending=pending, last_activity_ms=NOW, last_wake=None),
            now_ms=NOW, wake_id=1)
    finally:
        context.detach(token)
    assert agent.calls == 1
    started = next(e for e in events if e.get("name") == "nora.session.started")
    assert json.loads(started["attributes"]["body"]).get("stimuli") == [
        {"id": 1, "kind": "message", "ref": "messages:1"},
        {"id": 2, "kind": "social", "ref": "social_read:generated-read"},
    ]


