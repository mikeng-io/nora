"""Generated inputs through the two graph Cognition stages, no live tier writes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import tiktoken

from nora.agent.comprehend_tools import egress_reason
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


@pytest.mark.parametrize("name,argument", [
    ("web_search", "query"), ("web_fetch", "url"),
    ("instagram_search", "query"), ("instagram_read", "handle"),
    ("instagram_read", "link"), ("instagram_read", "url"),
    ("x_search", "query"), ("x_read", "handle"),
    ("x_read", "link"), ("x_read", "url"),
    ("place_info", "name"), ("place_info", "address"),
    ("media_fetch", "url"), ("media_fetch", "question"),
    ("file_fetch", "link"), ("file_fetch", "url"),
])
async def test_outbound_arguments_are_screened_before_the_tool_runs(name, argument):
    from nora.agent.comprehend_tools import ComprehendRead, LookupState
    from nora.agent.fetch_watch import GraphFetched, _result_of
    from nora.agent.ports import ToolSpec, adapt
    from nora.obs.metrics import Metrics
    from nora.testing.comprehend import InMemoryComprehendRecords

    public = "https://example.invalid/volleyball" if argument in {"url", "link"} else "volleyball"
    calls = []

    async def outbound(query: str = "", url: str = "", handle: str = "", name: str = "",
                       address: str = "", question: str = "", link: str = "") -> dict:
        values = {"query": query, "url": url, "handle": handle, "name": name,
                  "address": address, "question": question, "link": link}
        calls.append(values)
        value = values[argument]
        if value != public:
            return {"status": "unavailable", "reason": "unexpected_target"}
        return {"status": "ok", "text": value}

    records, fetched = InMemoryComprehendRecords(), GraphFetched()
    state = LookupState(fetched, UNIT, 1, records, Metrics(), ("Generated Person",))
    tool = ComprehendRead(adapt(ToolSpec(
        name=name, description="", schema={}, call=outbound,
    )), state)

    async def call(value, use_id):
        events = [event async for event in tool.stream(
            {"toolUseId": use_id, "name": name, "input": {argument: value}}, {},
        )]
        return _result_of(events[-1])

    refused = await call("Generated Person", "private")
    assert refused["status"] == "refused" and refused["reason"] == "private_person"
    assert calls == [] and fetched.sources == {}
    assert records.refusals == [(1, "egress", "private_person")]
    permitted = await call(public, "public")
    assert permitted["status"] == "ok" and permitted["text"] == public
    assert len(calls) == 1 and calls[0][argument] == public
    assert len(fetched.sources) == 1




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


async def test_a_memory_read_marks_repeats_and_counts_what_is_new():
    from nora.agent.comprehend_tools import ComprehendRead, LookupState
    from nora.agent.fetch_watch import GraphFetched, _result_of
    from nora.agent.ports import ToolSpec, adapt
    from nora.obs.metrics import Metrics
    from nora.testing.comprehend import InMemoryComprehendRecords

    async def read():
        return {
            "status": "ok",
            "keys": [["person", "generated-person"], ["note", "generated-note"]],
            "relations": [],
        }

    state = LookupState(
        GraphFetched(), UNIT, 1, InMemoryComprehendRecords(), Metrics(), ())
    tool = ComprehendRead(
        adapt(ToolSpec(name="memory_search", description="", schema={}, call=read)),
        state,
    )

    async def call(use_id):
        events = [
            event async for event in tool.stream(
                {"toolUseId": use_id, "name": "memory_search", "input": {}}, {})
        ]
        return _result_of(events[-1])

    first = await call("first")
    assert first["status"] == "ok"
    assert first["new"] == 2 and first["repeated"] == []
    second = await call("second")
    assert second["status"] == "ok"      # repeats-only keeps its own status
    assert second["new"] == 0
    assert second["repeated"] == [
        ["note", "generated-note"], ["person", "generated-person"]]


async def test_an_unavailable_read_carries_no_saturation_claim():
    from nora.agent.comprehend_tools import ComprehendRead, LookupState
    from nora.agent.fetch_watch import GraphFetched, _result_of
    from nora.agent.ports import ToolSpec, adapt
    from nora.obs.metrics import Metrics
    from nora.testing.comprehend import InMemoryComprehendRecords

    async def read():
        return {"status": "unavailable", "reason": "database unreachable"}

    state = LookupState(
        GraphFetched(), UNIT, 1, InMemoryComprehendRecords(), Metrics(), ())
    tool = ComprehendRead(
        adapt(ToolSpec(name="memory_read", description="", schema={}, call=read)),
        state,
    )
    events = [
        event async for event in tool.stream(
            {"toolUseId": "u1", "name": "memory_read", "input": {}}, {})
    ]
    answer = _result_of(events[-1])
    assert answer["status"] == "unavailable"
    assert "new" not in answer and "repeated" not in answer
