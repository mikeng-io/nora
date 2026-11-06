"""Generated wake: citations bind to exactly the rows the agenda emitted."""

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


async def wake(budget=None):
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
        historical=False,
        items_by_index=dict(enumerate(items)),
        memory_items={},
        shown_keys=set(),
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
