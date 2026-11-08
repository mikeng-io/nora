"""Generated wake: citations bind to exactly the rows the agenda emitted."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from nora.agent.fetch_watch import GraphFetched
from nora.app.cognition_memory import WakeMemory
from nora.domain.cognition_ground import Grounding, RecordItem
from nora.domain.cognition_session import Room, build_situation
from nora.domain.comprehend import ComprehendItem
from nora.domain.extraction import partition_claims
from nora.domain.memory_graph import Source, WorkItem
from nora.domain.stimuli import Stimulus
from nora.language.cognition_render import RenderBudget, render_agenda
from nora.testing.comprehend import InMemoryComprehendRecords
from nora.testing.memory_graph import FakeMemoryGraph

NOW = 1800000000000
ROOM = Room("signal", "generated-room")
NOTICE = (
    "Extracted content: "
    + "Generated notice details. " * 8
    + "Pending: collect tomorrow; not collected."
)


async def wake(budget=None, seed=None, historical=False, reaction=False, reaction_only=False):
    stimuli = {
        i: Stimulus(
            id=i,
            unit="generated-unit",
            kind="message",
            item="message",
            ref=f"messages:{mid}",
            at_ms=NOW - 1000 * (3 - i),
            recorded_at_ms=NOW,
            platform=ROOM.platform,
            conversation_id=ROOM.conversation_id,
        )
        for i, mid in ((1, 101), (2, 102))
    }
    records = {
        s.ref: RecordItem(s.ref, "message", mid, ROOM, name, s.at_ms, text, person_id=person)
        for (mid, name, person, text), s in zip(
            (
                (101, "Generated A", "generated-a", "Image attached."),
                (102, "Generated B", "generated-b", "Context from B."),
            ),
            stimuli.values(),
            strict=True,
        )
    }
    if reaction:
        ref = "message_reaction:201"
        stimuli[3] = Stimulus(
            id=3, unit="generated-unit", kind="message", item="reaction", ref=ref,
            at_ms=NOW - 500, recorded_at_ms=NOW, platform=ROOM.platform,
            conversation_id=ROOM.conversation_id, person_id="generated-reactor",
        )
        records[ref] = RecordItem(
            ref, "reaction", 101, ROOM, "Generated A", NOW - 500, "Image attached.",
            emoji="👍", person_id="generated-reactor",
        )
        if reaction_only:
            del stimuli[1]
    sources = (
        Source(("message", "101"), "record", "generated-unit", ("generated-a",), "Image attached."),
        Source(
            ("media", "101:generated-image"), "record", "generated-unit", ("generated-a",), NOTICE
        ),
        Source(("message", "102"), "record", "generated-unit", ("generated-b",), "Context from B."),
        Source(("message", "103"), "record", "generated-unit", (), "Loaded but never shown."),
    )
    at = datetime.fromtimestamp(NOW / 1000, UTC)
    # The first loader's media slot collides with the next real message.
    loaded = ComprehendItem(
        WorkItem("generated", sources[0].key, "generated-unit", "", at),
        sources,
        ROOM.platform,
        ROOM.conversation_id,
        author="generated-a",
        snapshot={
            "messages": [
                {"id": 101, "person_id": "generated-a", "ts": NOW - 2000, "body": sources[0].text},
                {"id": 102, "person_id": "generated-a", "ts": NOW, "body": NOTICE},
            ],
            "context": [
                {"id": 102, "person_id": "generated-b", "ts": NOW - 1000, "body": sources[2].text}
            ],
            "source_keys": {"101": ["message", "101"], "102": list(sources[1].key)},
        },
    )
    memory = WakeMemory(
        graph=FakeMemoryGraph(),
        records=InMemoryComprehendRecords({sources[0].key: loaded}),
        metrics=None,
        content_checks=False,
    )
    if seed is not None:
        seed(memory)
    items = build_situation(
        unit="generated-unit",
        pending=tuple(stimuli.values()),
        home_room=ROOM,
        now_ms=NOW,
        last_session_ms=None,
    ).agenda.items
    state = SimpleNamespace(
        memory=memory,
        session_id=7,
        admitted_indices=set(),
        admitted_relations={},
        admitted_evidence=[],
        unit="generated-unit",
        now_ms=NOW,
        historical=historical,
        items_by_index=dict(enumerate(items)),
        memory_items={},
        shown_keys=set(),
        shown_persons=set(),
        graph_fetched=GraphFetched(),
    )
    grounding = Grounding(records=records)
    await memory.prepare(state, stimuli, grounding)
    prompt = render_agenda(
        items,
        stimuli=stimuli,
        grounding=grounding,
        now_ms=NOW,
        graph=state.graph_text,
        budget=budget,
        memory_items=state.memory_items,
    )
    memory.register_shown(state)
    return state, prompt


@pytest.mark.parametrize("historical", [False, True])
async def test_reaction_cannot_replace_message_author_or_grant_reactor_authority(historical):
    from nora.language.cognition_render import parse_rendered_agenda, rendered_new_sources
    from scripts.memory_graph_spike import evidence_exchanges

    state, prompt = await wake(historical=historical, reaction=True)
    item = state.memory_items[0]
    message = next(r for r in item.snapshot["messages"] if r["id"] == 101)
    assert message["person_id"] == "generated-a"
    assert message["ts"] == NOW - 2000
    assert sum(r["id"] == 101 for r in item.snapshot["messages"]) == 1
    reaction = next(row for group in parse_rendered_agenda(prompt)
                    for row in group.rows if row.attributes.get("reaction"))
    assert "person" not in reaction.attributes
    assert rendered_new_sources(prompt)["101"]["text"] == "Image attached."
    result = await state.memory.propose(
        state, items=[0], statements=[{
            "type": "reading", "about": [{"person_id": person, "reference": person}],
            "what": "Generated attachment", "how": "reported",
            "happened_at": {"source": 101, "expression": None},
            "cite": [{"source": 101, "quote": "Image attached."}],
        } for person in ("generated-a", "generated-reactor")])
    assert result["statements"][0]["status"] == "saved", result
    assert result["statements"][1]["reason"] == "unknown_subject", result
    assert state.memory.records.claims
    events = [
        {"kind": "event", "name": "nora.model.request", "attributes": {
            "nora.model.request.body": json.dumps({"messages": [
                {"role": "user", "content": prompt}]})}},
        {"kind": "event", "name": "nora.proposal.sources", "span_id": "generated",
         "attributes": {"body": json.dumps({"101": {"text": "Image attached."}})}},
        {"kind": "tool_finished", "span_id": "generated",
         "tool": {"name": "memory_propose", "input": {}},
         "result": {"content": [{"json": result}]}},
    ]
    cited = evidence_exchanges(events)[0]["source_rows"]["101"]["rendered"]
    assert 'person="generated-a"' in cited and "reaction=" not in cited


async def test_reaction_alone_neither_makes_target_citable_nor_shows_reactor():
    from nora.language.cognition_render import rendered_new_sources

    state, prompt = await wake(historical=True, reaction=True, reaction_only=True)
    assert "101" not in state.memory_items[0].snapshot["source_keys"]
    assert "101" not in rendered_new_sources(prompt)
    assert 'reaction="👍"' in prompt and 'person="generated-reactor"' not in prompt
    result = await state.memory.propose(state, items=[0], statements=[{
        "type": "reading", "about": [], "what": "Generated attachment", "how": "reported",
        "happened_at": {"source": 101, "expression": None},
        "cite": [{"source": 101, "quote": "Image attached."}],
    }])
    assert result["statements"][0]["reason"] == "citation_outside_batch", result


def claim(rid, quote):
    return {
        "about": [],
        "what": "Generated pending collection",
        "happened_at": {"source_id": rid, "expression": None},
        "where": None,
        "how": "reported",
        "source_ids": [rid],
        "evidence": [{"source_id": rid, "quote": quote}],
    }


@pytest.mark.asyncio
async def test_colliding_loader_slot_keeps_image_author_and_clock():
    state, prompt = await wake()
    item = state.memory_items[0]
    rid = next(int(i) for i, key in item.snapshot["source_keys"].items() if key[0] == "media")
    assert f'<m n="{rid}"' in prompt and rid < 0
    claims, drops = partition_claims(
        {"claims": [claim(rid, "Pending: collect tomorrow")]}, item.snapshot
    )
    assert not drops
    result = await state.memory.propose(
        state, items=[0], nothing_here=[],
        statements=[{
            "type": "reading", "about": [], "what": "Generated pending collection",
            "happened_at": {"source": rid, "expression": None},
            "where": None, "how": "reported",
            "cite": [{"source": rid, "quote": "Pending"}],
        }])
    assert result["status"] == "ok", result
    assert state.memory.records.claims
    assert claims[0]["assertions"] == [
        {"source_id": rid, "person_id": "generated-a", "asserted_at_ms": NOW - 2000}
    ]
    assert item.snapshot["source_keys"]["102"] == ["message", "102"]
    assert (
        next(r for r in item.snapshot["messages"] if r["id"] == 102)["person_id"] == "generated-b"
    )


@pytest.mark.asyncio
async def test_labelled_pending_content_is_in_the_agenda_row():
    state, prompt = await wake()
    assert "Extracted content:" in prompt and "Pending: collect tomorrow; not collected." in prompt
    media = next(r for r in state.memory_items[0].snapshot["messages"] if r["id"] < 0)
    assert "Pending: collect tomorrow; not collected." in media["body"]


@pytest.mark.asyncio
async def test_cited_sources_are_shown_once_without_parallel_json():
    state, prompt = await wake()
    assert "reading_sources=" not in prompt
    for text in ("Image attached.", "Context from B.", "Pending: collect tomorrow; not collected."):
        assert prompt.count(text) == 1
    assert len(state.memory_items[0].snapshot["source_keys"]) == 3


@pytest.mark.asyncio
async def test_budget_omitted_source_cannot_be_cited():
    state, prompt = await wake(RenderBudget(record_chars=160, min_item_chars=160))
    item = state.memory_items[0]
    assert '<m n="102"' in prompt
    assert "-1" not in item.snapshot["source_keys"]
    claims, drops = partition_claims({"claims": [claim(-1, "Pending")]}, item.snapshot)
    assert not claims and drops == ["citation_outside_batch"]


@pytest.mark.asyncio
async def test_a_loaded_but_unemitted_row_cannot_be_cited():
    """A source the loader handed prepare but the agenda never rendered is not
    citable: custody is the emitted catalogue, not the loaded set."""
    state, prompt = await wake()
    assert "Loaded but never shown." not in prompt
    result = await state.memory.propose(
        state, items=[0], nothing_here=[],
        statements=[{
            "type": "relation", "rel": "at",
            "subject": {"new": {"kind": "event", "name": "Generated outing"}},
            "object": {"new": {"kind": "place", "name": "Generated venue"}},
            "cite": [{"source": 103, "quote": "Loaded but never shown."}],
        }])
    assert result["status"] == "refused"
    assert result["statements"][0]["reason"] == "unseen_source"


@pytest.mark.asyncio
@pytest.mark.parametrize("now_ms, index", [(None, 0), (NOW, 99)])
async def test_unrendered_item_sources_cannot_be_cited(now_ms, index):
    state, shown = await wake()
    item = state.memory_items[0]
    assert '<m n="-1"' in shown and "-1" in item.snapshot["source_keys"]
    prompt = render_agenda(
        list(state.items_by_index.values()),
        now_ms=now_ms,
        memory_items={index: item},
    )
    assert '<item index="0"' in prompt
    assert '<m ' not in prompt
    assert item.snapshot["messages"] == []
    assert item.snapshot["source_keys"] == {}
    claims, drops = partition_claims({"claims": [claim(-1, "Pending")]}, item.snapshot)
    assert not claims and drops == ["citation_outside_batch"]


@pytest.mark.asyncio
async def test_a_clipped_row_tail_is_not_citable():
    """A tail the budget clipped was never shown — graph custody is the
    emitted body, so quoting the clipped-off tail must refuse."""
    state, _prompt = await wake(RenderBudget(message_chars=100))
    item = state.memory_items[0]
    n, _key = next((int(n), k) for n, k in item.snapshot["source_keys"].items()
                   if k[0] == "media")
    row = next(r for r in item.snapshot["messages"] if r["id"] == n)
    tail = "Pending: collect tomorrow; not collected."
    assert tail not in row["body"]
    result = await state.memory.propose(
        state, items=[0], nothing_here=[],
        statements=[{
            "type": "relation", "rel": "at",
            "subject": {"new": {"kind": "event", "name": "Generated outing"}},
            "object": {"new": {"kind": "place", "name": "Generated venue"}},
            "cite": [{"source": n, "quote": tail}],
        }])
    assert result["statements"][0]["status"] == "refused"
    assert result["statements"][0]["reason"] == "span_not_in_source"


@pytest.mark.asyncio
async def test_a_relation_clipped_from_the_preview_cannot_be_corrected():
    """The correction catalogue is the emitted rows' ids, not the loaded
    walk's: a relation the preview budget cut was never shown, so retiring
    it must refuse unshown."""
    from nora.domain.memory_graph import Entity, Relation

    def seed(memory):
        unit_key = ("unit", "generated-unit:unit:generated-unit")
        for i in range(12):
            work = Entity(f"work-{i}", "work", "generated-unit", "w" * 900)
            memory.graph.entities[work.id] = work
            edge = Relation(
                f"edge-{i}", unit_key, "hosts", work.key, "notebook",
                "generated-unit", (),
                asserted_at=datetime.fromtimestamp(NOW / 1000, UTC))
            memory.graph.relations[edge.id] = edge

    state, prompt = await wake(seed=seed)
    # The target is chosen from the rendered prompt itself, never from the
    # catalogue under test: a stored edge row the budget cut was never shown.
    clipped = next(rid for rid in state.memory.graph.relations
                   if rid.startswith("edge-") and f"{rid}:" not in prompt)
    correction = {"type": "correction", "relation": clipped, "change": "retire",
                  "cite": [{"source": 101, "quote": "Image attached."}]}
    result = await state.memory.propose(
        state, items=[0], nothing_here=[], statements=[correction])
    assert result["statements"][0]["status"] == "refused"
    assert result["statements"][0]["reason"] == "unshown"
    assert state.memory.graph.relations[clipped].status == "current"


def _seed_key_shaped_label(memory, *, blocked):
    """One person→work `likes` edge; `blocked` gives the work an
    identifier-shaped label the always-on screen withholds the whole block
    for."""
    from nora.domain.memory_graph import Entity, Relation, pointer_entity

    memory.metrics = SimpleNamespace(inc=lambda *a, **k: None)
    person = pointer_entity("person", "generated-unit", "generated-a", "Generated A")
    memory.graph.entities[person.id] = person
    work = Entity("generated-work", "work", "generated-unit",
                  "sk-" + "GENERATED" * 4 if blocked else "Generated work")
    memory.graph.entities[work.id] = work
    memory.graph.relations["generated-edge"] = Relation(
        "generated-edge", person.key, "likes", work.key, "notebook",
        "generated-unit", ("generated-a",),
        asserted_at=datetime.fromtimestamp(NOW / 1000, UTC))


def _correction(rid="generated-edge"):
    return {"type": "correction", "relation": rid, "change": "retire",
            "cite": [{"source": 101, "quote": "Image attached."}]}


@pytest.mark.asyncio
async def test_a_withheld_graph_keeps_no_correction_authority():
    """When the identifier screen withholds the whole graph block, its
    catalogue is withheld with it: a relation in a graph the session was
    never shown refuses `unshown` and stays `current`."""
    state, prompt = await wake(seed=lambda m: _seed_key_shaped_label(m, blocked=True))
    assert state.graph_text == "" and "generated-edge:" not in prompt
    result = await state.memory.propose(
        state, items=[0], nothing_here=[], statements=[_correction()])
    assert result["statements"][0]["status"] == "refused"
    assert result["statements"][0]["reason"] == "unshown"
    assert state.memory.graph.relations["generated-edge"].status == "current"


@pytest.mark.asyncio
async def test_an_emitted_preview_relation_can_be_corrected():
    """Positive control for the withheld case: an edge row the preview did
    emit keeps correction authority — retiring it saves and retires."""
    state, prompt = await wake(seed=lambda m: _seed_key_shaped_label(m, blocked=False))
    assert "generated-edge:" in prompt
    result = await state.memory.propose(
        state, items=[0], nothing_here=[], statements=[_correction()])
    assert result["statements"][0]["status"] == "saved"
    assert state.memory.graph.relations["generated-edge"].status == "retired"


@pytest.mark.asyncio
@pytest.mark.parametrize("historical", [False, True])
async def test_an_emitted_rows_author_is_handle_citable_without_source_scope(historical):
    """A unit-wide room: the emitted row renders `person=generated-a` while
    its citable Source is scoped to no one (`persons=()`). The author the
    session was shown is handle-citable — `persons` is a source's scope, not
    its authorship — and the relation keeps the cited sources' empty persons
    union, so nothing widens. A live and a historical session answer alike."""
    from dataclasses import replace as _replace

    from nora.domain.memory_graph import pointer_entity

    def seed(memory):
        person = pointer_entity(
            "person", "generated-unit", "generated-a", "Generated A")
        memory.graph.entities[person.id] = person

    state, prompt = await wake(historical=historical, seed=seed)
    assert 'person="generated-a"' in prompt
    for source in list(state.graph_fetched.sources.values()):
        state.graph_fetched.show(_replace(source, persons=()))
    result = await state.memory.propose(
        state, items=[0], nothing_here=[],
        statements=[{
            "type": "relation", "rel": "likes",
            "subject": "@person:generated-a",
            "object": {"new": {"kind": "work", "name": "Generated work"}},
            "cite": [{"source": 101, "quote": "Image attached."}],
        }])
    assert result["statements"][0]["status"] == "saved", result
    relation = state.memory.graph.relations[result["committed"][0]]
    assert relation.persons == ()


@pytest.mark.asyncio
async def test_a_shown_author_grounds_a_pointer_the_projection_has_not_written():
    """No pointer exists for the emitted row's author — the projection has
    not reached her, and the group room's Sources are scoped to no one.
    Admission stages the unit's pointer from shown authorship alone: the
    statement saves, the pointer is committed, and the relation's persons
    stay the cited sources' empty union."""
    from dataclasses import replace as _replace

    state, prompt = await wake()
    assert 'person="generated-a"' in prompt
    assert "generated-unit:person:generated-a" not in state.memory.graph.entities
    for source in list(state.graph_fetched.sources.values()):
        state.graph_fetched.show(_replace(source, persons=()))
    result = await state.memory.propose(
        state, items=[0], nothing_here=[],
        statements=[{
            "type": "relation", "rel": "likes",
            "subject": "@person:generated-a",
            "object": {"new": {"kind": "work", "name": "Generated work"}},
            "cite": [{"source": 101, "quote": "Image attached."}],
        }])
    assert result["statements"][0]["status"] == "saved", result
    pointer = state.memory.graph.entities["generated-unit:person:generated-a"]
    assert pointer.attributes == {"ref": "generated-a"}
    relation = state.memory.graph.relations[result["committed"][0]]
    assert relation.persons == ()


@pytest.mark.asyncio
async def test_a_clipped_rows_author_is_not_a_shown_person():
    """The budget clips row 101 and the media row: their author was never
    emitted as `person=`, so `@person:generated-a` refuses `unshown` even
    though a fetched Source names her in scope. The emitted row's author
    still resolves, in the same call."""
    state, _prompt = await wake(RenderBudget(record_chars=160, min_item_chars=160))
    emitted = [r.get("person_id") for r in state.memory_items[0].snapshot["messages"]]
    assert emitted == ["generated-b"], emitted
    statements = [
        {"type": "relation", "rel": "likes", "subject": f"@person:{person}",
         "object": {"new": {"kind": "work", "name": "Generated work"}},
         "cite": [{"source": 102, "quote": "Context from B."}]}
        for person in ("generated-a", "generated-b")
    ]
    result = await state.memory.propose(
        state, items=[0], nothing_here=[], statements=statements)
    assert result["statements"][0]["status"] == "refused"
    assert result["statements"][0]["reason"] == "unshown"
    assert result["statements"][1]["status"] == "saved"


@pytest.mark.asyncio
async def test_a_person_never_shown_is_still_unshown():
    """Custody is authorship the session emitted, and nothing else: a person
    appearing nowhere as an emitted row's `person=` refuses `unshown`."""
    state, _prompt = await wake()
    result = await state.memory.propose(
        state, items=[0], nothing_here=[],
        statements=[{
            "type": "relation", "rel": "likes",
            "subject": "@person:generated-stranger",
            "object": {"new": {"kind": "work", "name": "Generated work"}},
            "cite": [{"source": 101, "quote": "Image attached."}],
        }])
    assert result["statements"][0]["status"] == "refused"
    assert result["statements"][0]["reason"] == "unshown"
