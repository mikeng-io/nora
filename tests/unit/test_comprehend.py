"""Generated inputs through the two graph Cognition stages, no live tier writes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import tiktoken

from nora.domain.comprehend import ComprehendItem, Preview, card_facts
from nora.domain.memory_graph import (
    Entity,
    GraphEdge,
    GraphNode,
    Relation,
    Source,
    Subgraph,
    WorkItem,
    pointer_entity,
)
from nora.language.comprehend_render import preview_text

AT = datetime(2026, 1, 5, 9, tzinfo=UTC)  # Monday
UNIT = "generated-unit"
PERSON = pointer_entity("person", UNIT, "generated-person", "Generated Person")
PLACE = Entity("generated-place", "place", UNIT, "Generated Hall", aliases=("Hall",))
WORK = WorkItem("generated-work", ("message", "1"), UNIT, "generated-hash", AT)
SOURCE = Source(WORK.source, "record", UNIT, (), "I like Hall on Saturday")
ITEM = ComprehendItem(
    WORK,
    (SOURCE,),
    "signal",
    "generated-room",
    "generated-person",
    snapshot={
        "messages": [
            {
                "id": 1,
                "body": SOURCE.text,
                "ts": int(AT.timestamp() * 1000),
                "person_id": "generated-person",
                "mentions": [],
            }
        ],
        "context": [],
    },
)









def test_preview_caps_render_whole_rows_and_more():
    graph = Subgraph(
        "ok",
        nodes=tuple(
            GraphNode(
                ("place", str(i)), replace(PLACE, id=str(i), name="Long generated label " * 10)
            )
            for i in range(100)
        ),
    )
    preview = Preview((), graph, (), (AT, AT))
    text = preview_text(preview, tokens=80)
    assert "more=available" in text
    assert len(tiktoken.get_encoding("cl100k_base").encode(text)) <= 80











def test_card_facts_are_current_ranked_and_bounded():
    old = Relation("old", PERSON.key, "likes", PLACE.key, "notebook", UNIT, asserted_at=AT)
    new = replace(old, id="new", rel="dislikes", asserted_at=AT + timedelta(days=1))
    retired = replace(new, id="retired", rel="lives_in", status="retired")
    graph = Subgraph(
        "ok",
        (GraphNode(PERSON.key, PERSON), GraphNode(PLACE.key, PLACE)),
        (GraphEdge(old, 8), GraphEdge(new, 2), GraphEdge(retired, 9)),
    )
    text = card_facts(graph, PERSON.key)
    assert text.index("dislikes") < text.index("likes Generated")
    assert "lives_in" not in text
    assert len(card_facts(graph, PERSON.key, max_chars=90)) <= 90
























def test_preview_never_labels_a_notebook_item_as_record_evidence():
    from nora.domain.memory_graph import GraphNode

    graph = Subgraph("ok", (GraphNode(("note", "generated-note")),))
    text = preview_text(Preview((), graph, (), (AT, AT)))
    assert "note:generated-note" in text
    assert "time=unavailable" in text and "state=unavailable" in text
    assert "Record/Rollup" not in text










def test_web_page_preview_uses_found_layer_word():
    graph = Subgraph("ok", (GraphNode(("web_page", "generated-page")),))
    assert "found" in preview_text(Preview((), graph, (), (AT, AT)))














def test_preview_text_reports_only_the_keys_of_the_rows_it_emitted():
    """`shown_keys` is the emitted row's own provenance: node rows give their
    node, edge rows both endpoints — a kind:id string inside a label is prose
    and is never collected."""
    shown: set = set()
    edge = Relation("r", PERSON.key, "likes", ("event", "u:event:edge-endpoint"),
                    "notebook", UNIT, asserted_at=AT)
    graph = Subgraph(
        "partial",
        (GraphNode(PERSON.key, PERSON),
         GraphNode(("place", "u:forged-name"),
                   Entity("u:forged-name", "place", UNIT,
                          "a name saying event:u:event:forged"))),
        (GraphEdge(edge, 3),),
    )
    preview_text(Preview((), graph, (), (AT, AT)), shown_keys=shown)
    assert shown == {
        PERSON.key,
        ("place", "u:forged-name"),
        ("event", "u:event:edge-endpoint"),
    }


def test_preview_text_never_reports_a_row_the_budget_omitted():
    shown: set = set()
    graph = Subgraph(
        "ok",
        nodes=tuple(
            GraphNode(("place", str(i)),
                      replace(PLACE, id=str(i), name="Long generated label " * 10))
            for i in range(100)
        ),
    )
    preview_text(Preview((), graph, (), (AT, AT)), tokens=250, shown_keys=shown)
    assert ("place", "0") in shown
    assert ("place", "99") not in shown
    assert len(shown) < 100




