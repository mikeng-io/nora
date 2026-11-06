"""The agenda-format experiment's four candidates (A/B/C/D): each renders
through the registered composers, every shown citation number resolves
through the emitted custody, and a clipped or unshown row never does."""

from __future__ import annotations

import re
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from nora.agent.fetch_watch import GraphFetched
from nora.app.cognition_memory import WakeMemory
from nora.app.comprehend import Citation, MemoryProposalAdmission, proposal_scope
from nora.domain.cognition_ground import Grounding, RecordItem
from nora.domain.cognition_session import AgendaItem, Room
from nora.domain.memory_graph import Source
from nora.domain.stimuli import Stimulus
from nora.language.cognition_render import RenderBudget, render_agenda

HK = ZoneInfo("Asia/Hong_Kong")
NOW = 1_800_000_000_000
ROOM = Room("signal", "room-a")
LABELS = {"signal:room-a": "Room A"}


def _stim(sid: int) -> Stimulus:
    return Stimulus(
        id=sid, unit="u", kind="message", item="message", ref=f"messages:{sid}",
        at_ms=NOW - sid * 60_000, recorded_at_ms=NOW,
        platform=ROOM.platform, conversation_id=ROOM.conversation_id, cues=(),
    )


def _rec(sid: int, mid: int, text: str, author: str, person_id: str | None) -> RecordItem:
    return RecordItem(
        ref=f"messages:{sid}", kind="message", message_id=mid, room=ROOM,
        author=author, at_ms=NOW - sid * 60_000, text=text, person_id=person_id,
    )


def _row(mid: int, body: str, person_id: str | None, author: str, ts: int) -> dict:
    return {
        "id": mid, "body": body, "ts": ts, "person_id": person_id,
        "author": author, "mentions": [], "source_key": ["message", str(mid)],
    }


def _session(fmt: str, *, budget: RenderBudget | None = None):
    """Render one item's two messages under `fmt` and register the shown rows."""
    stims = {1: _stim(1), 2: _stim(2), 3: _stim(3)}
    records = {
        "messages:1": _rec(1, 4471, "first words", "Ming", "ming"),
        "messages:2": _rec(2, 4472, "second words", "Ada", "ada"),
        "messages:3": _rec(3, 4473, "third words", "Ming", "ming"),
    }
    item = AgendaItem(
        kind="message", room=ROOM, summary="3 new messages", cues=(),
        surprise=1.0, stimulus_ids=(1, 2, 3),
    )
    memory = SimpleNamespace(
        sources=(
            Source(("message", "4471"), "record", "u", ("ming",), "first words"),
            Source(("message", "4472"), "record", "u", ("ada",), "second words"),
            Source(("message", "4473"), "record", "u", ("ming",), "third words"),
        ),
        snapshot={
            "record_rows": [
                _row(4471, "first words", "ming", "Ming", NOW - 60_000),
                _row(4472, "second words", "ada", "Ada", NOW - 120_000),
                _row(4473, "third words", "ming", "Ming", NOW - 180_000),
            ],
            "messages": [], "context": [], "source_keys": {},
        },
    )
    text = render_agenda(
        (item,), stimuli=stims,
        grounding=Grounding(records=records, room_labels=LABELS),
        now_ms=NOW, tz=HK, budget=budget or RenderBudget(),
        memory_items={0: memory}, agenda_format=fmt,
    )
    state = SimpleNamespace(memory_items={0: memory}, graph_fetched=GraphFetched())
    WakeMemory(graph=None, records=None, metrics=None).register_shown(state)
    scope = proposal_scope([memory], state.graph_fetched)
    return text, memory, state, scope


def _ns(text: str) -> list[int]:
    return [int(n) for n in re.findall(r'<m n="(\d+)"', text)]


@pytest.mark.parametrize("fmt", ["A", "B", "C", "D"])
def test_every_shown_citation_resolves(fmt: str) -> None:
    text, _mem, _state, scope = _session(fmt)
    ns = _ns(text)
    assert len(ns) == 3, f"{fmt}: expected three shown rows, got {ns}"
    for n in ns:
        assert n in scope.catalogue, f"{fmt}: shown n={n} absent from the catalogue"
        assert scope.catalogue[n] in scope_key_set(scope, _state)


def scope_key_set(scope, state):
    return set(state.graph_fetched.sources)


@pytest.mark.parametrize("fmt", ["A", "B", "C", "D"])
def test_clipped_and_unshown_rows_refuse(fmt: str) -> None:
    # A starved budget drops the oldest rows; they must not resolve.
    text, memory, state, scope = _session(
        fmt, budget=RenderBudget(record_chars=60, min_item_chars=50),
    )
    assert "earlier_not_shown" in text
    admission = MemoryProposalAdmission(graph=None, records=None, metrics=None)
    for n in _ns(text):
        sources, reason = admission._cited(
            [Citation(source=n)], scope, state.graph_fetched)
        assert reason is None and sources, f"{fmt}: shown n={n} did not resolve"
    cited, reason = admission._cited(
        [Citation(source=max(_ns(text)) + 1)], scope, state.graph_fetched)
    assert reason == "unseen_source" and not cited
    # A real message the render cut is not citable either.
    dropped = {"first words", "second words", "third words"} - set(
        r["body"] for r in memory.snapshot["messages"])
    assert dropped, f"{fmt}: no row was actually clipped"


def test_a_keeps_the_production_spelling() -> None:
    text, *_ = _session("A")
    assert 'n="4472"' in text or 'n="4473"' in text
    assert 'person="ming"' in text
    assert 'from="Ming"' in text


def test_b_spells_each_identity_once() -> None:
    text, *_ = _session("B")
    assert 'person="' not in text and 'source="' not in text
    assert 'from="Ming @person:ming"' in text
    assert "@person:ming" in text


def test_c_renders_the_closed_set_and_legend() -> None:
    text, memory, _state, scope = _session("C")
    ns = sorted(_ns(text))
    assert ns == sorted(set(ns)) and ns[0] == 1
    assert "people:" in text and "@person:ming" in text and "@person:ada" in text
    assert 'person="' not in text and 'source="' not in text
    assert "age=" in text
    # The catalogue keys ARE the ordinals shown.
    assert set(scope.catalogue) == set(ns)


def test_d_uses_c_rows_and_statement_preview() -> None:
    text, *_ = _session("D")
    assert "people:" in text and 'person="' not in text
    from nora.domain.comprehend import Preview
    from nora.domain.memory_graph import GraphEdge, GraphNode, Relation, Subgraph
    from nora.language.comprehend_render import preview_text
    from datetime import datetime, UTC
    edge = GraphEdge(
        relation=Relation(
            "r88", ("person", "u:person:ming"), "works_at", ("org", "hsbc"),
            "notebook", "u", ("ming",), None, None,
            asserted_at=datetime.now(UTC), written_by="test"),
        evidence_count=1, evidence=(), sources=())
    graph = Subgraph("ok", (GraphNode(("person", "u:person:ming")),), (edge,))
    keys, rels = set(), set()
    preview = preview_text(
        Preview((), graph, (), (datetime.now(UTC), datetime.now(UTC))),
        shown_keys=keys, shown_relations=rels, statements=True)
    assert "@person:u:person:ming works_at @org:hsbc [#r88]" in preview
    assert "r88" in rels
