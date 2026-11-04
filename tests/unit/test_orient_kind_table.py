"""Node 33: the live wake's input and admission, without a model call."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nora.agent.fetch_watch import GraphFetched
from nora.app.comprehend import GraphWrite
from nora.domain.cognition_ground import Grounding, RecordItem
from nora.domain.cognition_session import AgendaItem
from nora.domain.comprehend import ComprehendItem
from nora.domain.memory_graph import MEMORY_KINDS, Entity, Relation, Source, WorkItem
from nora.domain.stimuli import UnitView
from nora.language.comprehend_render import SESSION_INSTRUCTION, render_kind_table
from nora.testing.cognition_grounding import StaticGrounder
from tests.objectives import shipped_objectives
from tests.unit.test_cognition_session_runner import (
    HOME,
    NOW,
    FakeCognitionAgentRunner,
    _pending,
    _runner,
    proposal,
    wake_memory,
)

AT = datetime.fromtimestamp(NOW / 1000, UTC)
TEXT = "I like Lantern Game. I have a spare controller."


def _state(memory, *, text=TEXT, now=AT):
    from nora.domain.memory_graph import pointer_entity

    person = pointer_entity("person", "unit-1", "generated-person", "Generated")
    memory.graph.entities[person.id] = person
    source = Source(("message", "1"), "record", "unit-1", ("generated-person",), text)
    item = ComprehendItem(
        WorkItem("generated-wake", source.key, "unit-1", text, now),
        (source,), "signal", "home-room",
        snapshot={"messages": [{"id": 1, "body": text, "ts": NOW,
                                 "person_id": "generated-person", "mentions": []}],
                  "context": []})
    fetched = GraphFetched()
    fetched.show(source)
    return SimpleNamespace(memory_items={0: item}, graph_fetched=fetched, session_id=1,
                           unit="unit-1", historical=False, shown_keys=set(),
                           admitted_indices=set(), admitted_relations={}, admitted_evidence=[])


def _payload():
    result = proposal()
    result.pop("items")
    result["entities"] = [{"id": "generated-game", "kind": "work", "name": "Lantern Game"}]
    result["relations"] = [{"id": "like", "src": ["person", "generated-person"],
                            "rel": "likes", "dst": ["work", "generated-game"],
                            "persons": ["generated-person"]}]
    result["evidence"] = [{"source": ["message", "1"], "relation_id": "like",
                           "span": "I like Lantern Game"}]
    return result


def _reading(quote):
    return {"about": [{"person_id": "generated-person", "reference": "I"}],
            "what": "has a spare controller", "where": None, "how": "stated",
            "happened_at": {"source_id": 1, "expression": None}, "source_ids": [1],
            "evidence": [{"source_id": 1, "quote": quote}]}


def test_orient_renders_the_kind_constant():
    assert [row.kind for row in MEMORY_KINDS] == [
        "event", "place", "work", "organisation", "topic", "person relation", "reading",
        "profile line", "note", "lesson", "decision"]
    assert render_kind_table() in SESSION_INSTRUCTION
    assert "memory_stage" not in SESSION_INSTRUCTION
    assert "an item fitting no row is silence" in SESSION_INSTRUCTION
    assert MEMORY_KINDS[8].memory_type == "prospective"
    assert "corner is still open" in MEMORY_KINDS[8].create_when


@pytest.mark.parametrize("removed,part", [("work", "entity"), ("likes", "relation")])
async def test_admission_uses_the_kind_constant(monkeypatch, removed, part):
    """Remove a supported row/edge: old vocabulary alone must no longer admit it."""
    import nora.app.comprehend as admission

    rows = tuple(row for row in MEMORY_KINDS if row.kind != removed)
    if part == "relation":
        rows = tuple(replace(row, relates_by=tuple(r for r in row.relates_by if r != removed))
                     for row in rows)
    monkeypatch.setattr(admission, "MEMORY_KINDS", rows)
    memory = wake_memory()
    state = _state(memory)
    result, local = await memory.admission._admit(
        state.memory_items[0].work, GraphWrite.model_validate(_payload()),
        state.graph_fetched, state.session_id)
    assert {"kind": part, "reason": "vocabulary"} in local
    assert not result.entities if part == "entity" else not result.relations


@pytest.mark.parametrize("kind", ["note", "lesson", "reading", "profile line"])
async def test_a_notebook_row_is_refused_as_an_entity_kind(kind):
    """A kind-table row that is not a graph entity kind refuses; it never reaches the store."""
    memory = wake_memory()
    state = _state(memory)
    payload = _payload()
    payload["entities"] = [{"id": "generated-entry", "kind": kind, "name": "Generated"}]
    payload["relations"], payload["evidence"] = [], []
    result, local = await memory.admission._admit(
        state.memory_items[0].work, GraphWrite.model_validate(payload),
        state.graph_fetched, state.session_id)
    assert {"kind": "entity", "reason": "vocabulary"} in local
    assert not result.entities


async def test_bad_reading_keeps_valid_reading_and_graph():
    memory = wake_memory()
    state = _state(memory)
    payload = _payload()
    payload["readings"] = [_reading("I have a spare controller"), _reading("not in the source")]
    result = await memory.propose(state, items=[0], payload=payload)
    assert result["status"] == "ok", result
    assert result["admitted"] == [0]
    assert len(result["committed"]) == 1
    assert memory.graph.entities["generated-game"].kind == "work"
    assert [c["what"] for claims in memory.records.claims.values() for c in claims] == [
        "has a spare controller"]
    assert len(result["refused"]) == 1
    assert memory.records.refusals == [(1, "reading", "evidence_absent")]


async def test_later_contradiction_supersedes_current_relation():
    memory = wake_memory()
    first = await memory.propose(_state(memory), items=[0], payload=_payload())
    old_id = first["committed"][0]
    state = _state(memory, text="I dislike Lantern Game now.", now=AT + timedelta(days=1))
    payload = _payload()
    payload["entities"] = []
    payload["relations"][0].update(id="dislike", rel="dislikes")
    payload["evidence"][0].update(relation_id="dislike", span="I dislike Lantern Game now")
    payload["corrections"] = [{"relation_id": old_id, "kind": "supersede",
                                "at": state.memory_items[0].work.as_of.isoformat(),
                                "by": "dislike", "evidence": [["message", "1"]]}]
    later = await memory.propose(state, items=[0], payload=payload)
    assert later["status"] == "ok", later
    assert memory.graph.relations[old_id].status == "superseded"
    assert memory.graph.relations[old_id].superseded_by == later["committed"][0]
    assert memory.graph.relations[later["committed"][0]].rel == "dislikes"


async def test_live_preview_carries_alias_candidates_with_shown_provenance(monkeypatch):
    import nora.app.cognition_memory as live
    from nora.language.comprehend_render import preview_text

    memory = wake_memory()
    game = Entity("generated-game", "work", "unit-1", "Lantern Game", ("Lantern",))
    memory.graph.entities[game.id] = game
    memory.graph.relations["generated-like"] = Relation(
        "generated-like", ("person", "generated-person"), "likes", game.key,
        "notebook", "unit-1", asserted_at=AT)
    stimulus = _pending("unit-1")[0]
    row = RecordItem(ref=stimulus.ref, kind="message", message_id=1, room=HOME,
                     author="Generated", at_ms=NOW, text="Lantern again")
    state = SimpleNamespace(
        now_ms=NOW, historical=False, unit="unit-1", memory_items={},
        graph_fetched=GraphFetched(), shown_keys=set(),
        items_by_index={0: AgendaItem(kind="message", room=HOME, summary="generated",
                                     stimulus_ids=(1,), cues=(), surprise=0.0)})
    previews = []

    def capture(preview, **kwargs):
        previews.append(preview)
        return preview_text(preview, **kwargs)

    monkeypatch.setattr(live, "preview_text", capture)
    await memory.prepare(state, {1: stimulus}, Grounding(records={stimulus.ref: row}))
    assert any(node.key == game.key for graph in previews[0].candidates for node in graph.nodes)
    assert game.key in state.shown_keys
    withheld = set()
    assert "more=available" in preview_text(previews[0], tokens=100, shown_keys=withheld)
    assert not withheld


async def test_assembled_session_puts_orient_before_act():
    class Capture(FakeCognitionAgentRunner):
        async def run(self, **kwargs):
            self.system_prompt = kwargs["system_prompt"]
            return await super().run(**kwargs)

    store = shipped_objectives()
    agent = Capture(script=[("memory_propose", proposal())])
    stimulus = _pending("unit-1")[0]
    row = RecordItem(ref=stimulus.ref, kind="message", message_id=1, room=HOME,
                     author="Generated", at_ms=NOW, text="nothing further")
    runner, _, _ = _runner(agent_runner=agent, memory=wake_memory(),
                           grounder=StaticGrounder(Grounding(records={stimulus.ref: row})))
    runner._doctrine_text = store.for_objective("cognition").text
    runner._skill_text_for = lambda kinds: store.skill("cognition-register").instructions
    await runner.replay(view=UnitView(unit="unit-1", pending=(stimulus,),
                                     last_activity_ms=None, last_wake=None), now_ms=NOW)
    text = agent.system_prompt
    assert text.index("# What you are handed (Observe)") < text.index("# Orient")
    assert text.index("# Orient") < text.index("# Decide") < text.index("# Act")
    assert text.index("# Act") < text.index("## What belongs in a note")
    assert text.index("## What belongs in a note") < text.index("# Two ways to be wrong")
    assert render_kind_table() in text


async def test_only_invalid_reading_leaves_the_item_pending():
    memory = wake_memory()
    state = _state(memory)
    payload = proposal()
    payload.pop("items")
    payload["readings"] = [_reading("not in the source")]
    result = await memory.propose(state, items=[0], payload=payload)
    assert result["status"] == "refused"
    assert result["refused"] == [{"kind": "reading", "reason": "evidence_absent"}]
    assert not state.admitted_indices
    assert not memory.records.claims


async def test_a_withheld_social_stimulus_is_skipped_not_a_failed_wake():
    """Node 33 audit: one hidden social item no longer fails the whole wake."""
    from nora.domain.stimuli import Stimulus, UnitView

    social = Stimulus(id=2, unit="unit-1", kind="social", item="post",
                      ref="social:instagram:generated-hidden", at_ms=NOW, recorded_at_ms=NOW,
                      platform="signal", conversation_id="home-room")
    memory = wake_memory()
    memory.records.withheld.add(("social", "instagram:generated-hidden"))
    fake = FakeCognitionAgentRunner(script=[("memory_propose", proposal())])
    runner, _, _ = _runner(agent_runner=fake, memory=memory)
    await runner.replay(view=UnitView(unit="unit-1", pending=(*_pending("unit-1"), social),
                                      last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert fake.results and fake.results[0]["status"] == "ok"


@pytest.mark.parametrize("wrote,closes", [(True, "ok"), (False, "refused")])
async def test_an_admitted_memory_write_backs_a_written_close(monkeypatch, wrote, closes):
    """Option B: memory is a write, so an item whose admitted proposal wrote
    something closes `written`; an empty proposal still cannot claim it."""
    memory = wake_memory()

    async def admitted(state, *, items, payload):
        state.admitted_indices.update(items)  # as WakeMemory.propose records it
        return {"status": "ok", "admitted": items, "refused": [], "committed": [],
                "connected": {"status": "ok", "nodes": [], "relations": []}}
    monkeypatch.setattr(memory, "propose", admitted)
    payload = proposal()
    if wrote:
        payload["readings"] = [_reading("I have a spare controller")]
    fake = FakeCognitionAgentRunner(script=[
        ("memory_propose", payload), ("close", {"item_index": 0, "reason": "written"})])
    runner, _, _ = _runner(agent_runner=fake, memory=memory)
    await runner.replay(view=UnitView(unit="unit-1", pending=_pending("unit-1"),
                                      last_activity_ms=None, last_wake=None), now_ms=NOW)
    assert fake.results[1]["status"] == closes, fake.results[1]


@pytest.mark.parametrize("author,admitted", [("generated-person", True), ("someone-else", False)])
async def test_a_write_as_the_model_writes_it_is_admitted(author, admitted):
    """The smoke's refused shape: integer source id, a date, no projected person yet.

    The person end is staged only for the cited message's author; anyone else
    still needs a pointer the projection made.
    """
    memory = wake_memory()
    state = _state(memory)
    memory.graph.entities.clear()
    payload = _payload()
    payload["relations"][0].update(src=["person", author], persons=[author, "generated-person"],
                                   valid_from="2026-10-17")
    payload["evidence"][0]["source"] = ["message", 1]
    result, local = await memory.admission._admit(
        state.memory_items[0].work, GraphWrite.model_validate(payload),
        state.graph_fetched, state.session_id)
    assert not local
    if admitted:
        assert [r.rel for r in result.relations] == ["likes"]
        assert result.relations[0].valid_from == datetime(2026, 10, 17, tzinfo=AT.tzinfo)
        pointer = memory.graph.entities["unit-1:person:generated-person"]
        # Scoped reads match a pointer by its ref; losing it hides the relation.
        assert pointer.attributes == {"ref": "generated-person"}
        assert not any(r.reason == "missing_evidence" for r in result.refused)
    else:
        assert not result.relations
        assert {r.reason for r in result.refused} >= {"wrong_ends"}
        assert "unit-1:person:someone-else" not in memory.graph.entities


async def test_a_refused_relation_never_vouches_for_another_persons_end():
    """A stages the author's pointer then fails; B cites someone else's message."""
    memory = wake_memory()
    state = _state(memory)
    memory.graph.entities.clear()
    other = Source(("message", "2"), "record", "unit-1", ("someone-else",), "Lantern Game rocks")
    state.graph_fetched.show(other)
    payload = _payload()
    a = {"id": "a", "src": ["person", "generated-person"], "rel": "likes",
         "dst": ["work", "never-proposed"], "persons": ["generated-person"]}
    b = {**payload["relations"][0],
         "persons": ["generated-person", "someone-else"]}
    payload["relations"] = [a, b]
    payload["evidence"] = [
        {"source": ["message", 1], "relation_id": "a", "span": "I like Lantern Game"},
        {"source": ["message", 2], "relation_id": "like", "span": "Lantern Game rocks"}]
    result, _ = await memory.admission._admit(
        state.memory_items[0].work, GraphWrite.model_validate(payload),
        state.graph_fetched, state.session_id)
    assert not result.relations
    assert "unit-1:person:generated-person" not in memory.graph.entities
