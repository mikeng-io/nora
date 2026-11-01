"""Generated inputs through the two graph Cognition stages, no live tier writes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import tiktoken

from nora.agent.comprehend_tools import egress_reason
from nora.domain.comprehend import ComprehendItem, Preview, card_facts
from nora.domain.memory_graph import (
    Entity,
    Evidence,
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
EMPTY = {"entities": [], "relations": [], "evidence": [], "corrections": [], "readings": []}
WRITE = {
    **EMPTY,
    "relations": [{"id": "like", "src": list(PERSON.key), "rel": "likes", "dst": list(PLACE.key)}],
    "evidence": [{"source": list(SOURCE.key), "relation_id": "like", "span": "like Hall"}],
}




@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("where was Generated Person yesterday", "private_person"),
        ("@generated_handle posts", "private_person"),
        ("call +" + "852" + "9000" + "0101", "identifier"),
        (str(20 + 2.312) + "," + str(110 + 4.168), "coordinate"),
        ("who played Generated Hall on 2026-01-10", None),
    ],
)
def test_egress_screen(text, reason):
    assert egress_reason(text, ("Generated Person", "@generated_handle")) == reason


def test_nested_percent_encoding_cannot_bypass_private_person_screen():
    assert (
        egress_reason("where was Generated%2525252520Person", ("Generated Person",))
        == "private_person"
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



















def test_config_is_default_off_and_budgets_come_from_template(example_config):
    graph = example_config.memory.graph
    config = graph.comprehend
    assert not graph.enabled and not config.enabled
    assert (config.turns, config.total_tokens, config.output_tokens, config.timeout_seconds) == (
        8,
        120000,
        12000,
        240,
    )
    assert (config.live_concurrency, config.historical_concurrency) == (1, 1)
    assert (config.web_searches, config.page_fetches) == (3, 3)






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






def test_the_instruction_lists_the_vocabulary_admission_checks():
    from nora.domain.memory_graph import (
        ENTITY_KINDS,
        PROPOSED_KINDS,
        RELATION_RULES,
        validate_relation,
    )
    from nora.language.comprehend_render import COMPREHEND

    kinds = next(line for line in COMPREHEND.splitlines() if line.startswith("Entity kinds"))
    assert all(kind in kinds for kind in PROPOSED_KINDS) and "person" not in kinds
    for rel, rule in RELATION_RULES.items():
        if rule.layer == "notebook" and set(rule.sources) <= set(ENTITY_KINDS):
            assert f"\n- {rel}: " in COMPREHEND
    # A fetched page step one read may say what it is about; an entry's links are
    # written by the entry's own writer and are never offered here.
    assert "\n- about: reading|web_page|session|play -> any kind" in COMPREHEND
    assert all(f"\n- {rel}: " not in COMPREHEND for rel in ("records", "concerns", "settles"))
    topic = Entity("generated-topic", "topic", UNIT, "Generated Sport")
    evidence = (Evidence(SOURCE.key, relation_id="generated-interest", span="like Hall"),)
    interest = Relation(
        "generated-interest", PERSON.key, "interested_in", topic.key, "notebook", UNIT,
        asserted_at=AT,
    )
    assert validate_relation(interest, evidence, {SOURCE.key: SOURCE}) is None
    liked = replace(interest, rel="likes")
    assert validate_relation(liked, evidence, {SOURCE.key: SOURCE}) == "wrong_ends"








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
