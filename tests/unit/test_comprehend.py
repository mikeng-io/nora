"""Generated inputs through the two graph Cognition stages, no live tier writes."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import tiktoken

from nora.agent.comprehend_tools import ComprehendRead, LookupState, egress_reason
from nora.agent.fetch_watch import GraphFetched, _result_of
from nora.app.comprehend import ComprehendRunner
from nora.domain.comprehend import ComprehendItem, Preview, build_preview, card_facts, item_window
from nora.domain.memory_graph import (
    Entity,
    Evidence,
    GraphEdge,
    GraphNode,
    Relation,
    Source,
    Subgraph,
    Walk,
    WorkItem,
    pointer_entity,
)
from nora.language.comprehend_render import preview_text
from nora.obs.metrics import Metrics
from nora.testing.cognition_store import InMemoryCognitionStore
from nora.testing.comprehend import InMemoryComprehendRecords, ScriptedCognitionRunner
from nora.testing.memory_graph import FakeMemoryGraph

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


def runner(graph, records, model, *, enabled=True, graph_enabled=True, clock=lambda: AT):
    cfg = SimpleNamespace(
        enabled=enabled,
        turns=8,
        total_tokens=120000,
        output_tokens=12000,
        timeout_seconds=240,
        live_concurrency=1,
        historical_concurrency=1,
        preview_tokens=1500,
        web_searches=3,
        page_fetches=3,
    )
    return ComprehendRunner(
        graph=graph,
        records=records,
        sessions=InMemoryCognitionStore(),
        agent=model,
        tools=SimpleNamespace(build=lambda **kw: []),
        config=cfg,
        graph_enabled=graph_enabled,
        metrics=Metrics(),
        private_names=("Generated Person",),
        model_id="runtime-model",
        model_route="gateway",
        clock=clock,
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


async def test_item_seeds_use_unit_pointers_aliases_and_own_clock():
    graph = FakeMemoryGraph()
    await graph.upsert_entity(PLACE)
    item = replace(
        ITEM,
        mentions=("mentioned",),
        faces=("labelled",),
        reply_parent=("message", "parent"),
        urls=("https://example.invalid/event",),
    )
    preview = await build_preview(graph, item)
    assert PERSON.key in preview.seeds
    assert pointer_entity("person", UNIT, "mentioned", "").key in preview.seeds
    assert pointer_entity("person", UNIT, "labelled", "").key in preview.seeds
    assert PLACE.key in preview.seeds and ("message", "parent") in preview.seeds
    assert ("person", "generated-person") not in preview.seeds
    assert preview.window[0].weekday() == 5
    assert preview.window[0].date().isoformat() == "2026-01-10"
    assert item_window("last Saturday", AT)[0].date().isoformat() == "2026-01-03"


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


async def test_end_to_end_backfill_clock_and_shadow_order():
    graph = FakeMemoryGraph()
    await graph.upsert_entity(PERSON)
    await graph.upsert_entity(PLACE)
    work = await graph.enqueue(replace(WORK, historical=True))
    records = InMemoryComprehendRecords({WORK.source: ITEM})
    model = ScriptedCognitionRunner(
        [
            [("memory_stage", {"nonsense": 1}), ("memory_stage", WRITE)],
            *[[("profile_stage", {"proposals_in": []})] for _ in range(3)],
        ]
    )
    r = runner(graph, records, model, clock=lambda: AT + timedelta(days=100))
    assert await r.run_once(historical=True)
    assert graph.work[work.id].state == "settled"
    assert [s["entity"][0] for s in records.shadows] == ["person", "room", "unit"]
    assert AT.isoformat() in model.prompts[0]
    assert (AT + timedelta(days=100)).isoformat() not in model.prompts[0]
    assert model.answers[0]["reason"] == "bad_arguments"
    assert len(graph.relations) == 1
    assert records.claims[(work.id, work.input_hash)] == []
    assert r.budget.turns == 8 and r.budget.total_tokens == 120000


async def test_unseen_source_is_refused_and_counted_while_valid_sibling_commits():
    graph = FakeMemoryGraph()
    for entity in (PERSON, PLACE):
        await graph.upsert_entity(entity)
    await graph.enqueue(WORK)
    records = InMemoryComprehendRecords({WORK.source: ITEM})
    bad = {"id": "unseen", "src": list(PERSON.key), "rel": "likes", "dst": list(PLACE.key)}
    write = {
        **WRITE,
        "relations": [*WRITE["relations"], bad],
        "evidence": [
            *WRITE["evidence"],
            {"source": ["message", "unseen"], "relation_id": "unseen", "span": "invented"},
        ],
    }
    model = ScriptedCognitionRunner(
        [[("memory_stage", write)], *[[("profile_stage", {"proposals_in": []})] for _ in range(3)]]
    )
    r = runner(graph, records, model)
    assert await r.run_once()
    assert len(graph.relations) == 1
    assert any(reason == "unseen_source" for _, _, reason in records.refusals)
    assert graph.work[WORK.id].state == "settled"


async def test_backfill_walk_excludes_relations_after_items_time():
    graph = FakeMemoryGraph()
    await graph.upsert_entity(PERSON)
    await graph.upsert_entity(PLACE)
    relation = Relation(
        "future",
        PERSON.key,
        "likes",
        PLACE.key,
        "notebook",
        UNIT,
        valid_from=AT + timedelta(days=1),
        asserted_at=AT,
    )
    await graph.stage_and_admit(
        entities=(),
        relations=(relation,),
        evidence=(Evidence(SOURCE.key, relation_id="future"),),
        sources={SOURCE.key: SOURCE},
    )
    preview = await build_preview(graph, ITEM)
    assert not preview.graph.relations
    assert (await graph.walk((PERSON.key,), Walk(UNIT, frozenset()))).relations


async def test_failed_items_retry_five_times_then_park():
    graph = FakeMemoryGraph()
    await graph.enqueue(WORK)
    records = InMemoryComprehendRecords({WORK.source: ITEM})
    records.fail_on.add("load")
    for attempt in range(5):
        r = runner(
            graph,
            records,
            ScriptedCognitionRunner([]),
            clock=lambda attempt=attempt: AT + timedelta(days=attempt),
        )
        assert await r.run_once()
    assert graph.work[WORK.id].state == "failed"
    assert graph.work[WORK.id].attempts == 5



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


async def test_no_change_does_not_reshape_and_disabled_does_not_claim():
    graph = FakeMemoryGraph()
    await graph.enqueue(WORK)
    records = InMemoryComprehendRecords({WORK.source: ITEM})
    model = ScriptedCognitionRunner([[("memory_stage", EMPTY)]])
    r = runner(graph, records, model, enabled=False)
    assert not await r.run_once() and graph.work[WORK.id].attempts == 0
    r.enabled = True
    assert await r.run_once()
    assert not records.shadows and len(model.prompts) == 1


async def test_web_caps_and_page_record_match_exactly_what_the_session_reads():
    from nora.agent.ports import ToolSpec, adapt

    calls = []

    async def web_fetch(url: str) -> dict:
        """Read a page. Call this when the session has a URL.

        Args:
            url: The page URL.
        """
        calls.append(url)
        return {"status": "ok", "text": "generated page " * 4000}

    records = InMemoryComprehendRecords()
    metrics = Metrics()
    fetched = GraphFetched()
    state = LookupState(fetched, UNIT, 1, records, metrics, ("Generated Person",))
    wrapped = ComprehendRead(
        adapt(ToolSpec(name="web_fetch", description="", schema={}, call=web_fetch, budget=0)),
        state,
    )

    async def call(url):
        answers = [
            _result_of(event)
            async for event in wrapped.stream(
                {"name": "web_fetch", "toolUseId": str(len(calls)), "input": {"url": url}}, {}
            )
        ]
        return next(a for a in answers if a is not None)

    private = await call("https://example.invalid/Generated%20Person")
    assert private["reason"] == "private_person" and not calls
    first = await call("https://example.invalid/page")
    assert len(first["text"]) == 32000
    assert records.pages[0]["text"] == first["text"]
    assert fetched.hydrate([tuple(first["source"])])[tuple(first["source"])].text == first["text"]
    await call("https://example.invalid/page-two")
    await call("https://example.invalid/page-three")
    assert (await call("https://example.invalid/page-four"))["reason"] == "lookup_cap"
    assert len(calls) == 3 and len(records.pages) == 3
    assert metrics.get("nora_comprehend_web_lookups_total", tool="web_fetch") == 3
    assert len(records.refusals) == 2


async def test_nora_reshapes_only_after_response_episode_and_no_live_stores_are_held():
    graph = FakeMemoryGraph()
    for entity in (PERSON, PLACE):
        await graph.upsert_entity(entity)
    await graph.enqueue(WORK)
    records = InMemoryComprehendRecords({WORK.source: replace(ITEM, response_closed=True)})
    model = ScriptedCognitionRunner(
        [[("memory_stage", WRITE)], *[[("profile_stage", {"proposals_in": []})] for _ in range(4)]]
    )
    r = runner(graph, records, model)
    await r.run_once()
    assert [s["entity"][0] for s in records.shadows] == ["person", "room", "unit", "nora"]
    assert all(name not in vars(r) for name in ("profile", "culture", "lessons"))
    assert all(s["parent"] == 1 for s in records.shadows)


async def test_readings_reuse_span_checks_and_keep_valid_parts():
    graph = FakeMemoryGraph()
    await graph.enqueue(WORK)
    valid = {
        "about": [{"person_id": "generated-person", "reference": "I"}],
        "what": "likes Hall",
        "happened_at": {"source_id": 1, "expression": "Saturday"},
        "where": "Hall",
        "how": "stated",
        "source_ids": [1],
        "evidence": [{"source_id": 1, "quote": "I like Hall"}],
    }
    bad = {**valid, "evidence": [{"source_id": 1, "quote": "something never shown"}]}
    records = InMemoryComprehendRecords({WORK.source: ITEM})
    model = ScriptedCognitionRunner([[("memory_stage", {**EMPTY, "readings": [valid, bad]})]])
    await runner(graph, records, model).run_once()
    assert len(records.claims[(WORK.id, WORK.input_hash)]) == 1
    assert records.refusals[0][1:] == ("reading", "evidence_absent")


async def test_graph_unavailable_is_failed_not_no_change():
    graph = FakeMemoryGraph()
    await graph.enqueue(WORK)
    graph.available = False
    model = ScriptedCognitionRunner([[("memory_stage", EMPTY)]])
    records = InMemoryComprehendRecords({WORK.source: ITEM})
    r = runner(graph, records, model)
    await r.run_once()
    assert graph.work[WORK.id].state == "pending"
    assert not model.prompts
    assert r.metrics.get("nora_comprehend_items_total", outcome="failed", lane="live") == 1



async def test_retry_after_graph_commit_keeps_reshape_owed_without_duplicate_relations():
    graph = FakeMemoryGraph()
    for entity in (PERSON, PLACE):
        await graph.upsert_entity(entity)
    await graph.enqueue(WORK)
    records = InMemoryComprehendRecords({WORK.source: ITEM})
    records.fail_on.add("graph_write")
    await runner(graph, records, ScriptedCognitionRunner([[("memory_stage", WRITE)]])).run_once()
    assert len(graph.relations) == 1 and graph.work[WORK.id].state == "pending"
    records.fail_on.clear()
    model = ScriptedCognitionRunner(
        [[("memory_stage", WRITE)], *[[("profile_stage", {"proposals_in": []})] for _ in range(3)]]
    )
    await runner(graph, records, model, clock=lambda: AT + timedelta(days=1)).run_once()
    assert graph.work[WORK.id].state == "settled" and len(graph.relations) == 1
    assert len(records.shadows) == 3


async def test_history_worker_uses_its_own_slot_and_cancellation_keeps_work(monkeypatch):
    import asyncio

    graph = FakeMemoryGraph()
    await graph.enqueue(WORK)
    second = replace(WORK, id="second-live", source=("message", "2"))
    history = replace(WORK, id="history", source=("message", "3"), historical=True)
    await graph.enqueue(second)
    await graph.enqueue(history)
    records = InMemoryComprehendRecords()
    entered = []
    both = asyncio.Event()

    async def blocked(work):
        entered.append(work.historical)
        if len(entered) == 2:
            both.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(records, "load", blocked)
    r = runner(graph, records, ScriptedCognitionRunner([]))
    task = asyncio.create_task(r.run())
    try:
        await asyncio.wait_for(both.wait(), 3)
        assert sorted(entered) == [False, True]
        assert graph.work[second.id].state == "pending"
        assert graph.work[history.id].state == "running"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert all(w.state in ("pending", "running") for w in graph.work.values())


async def test_search_has_its_own_three_call_cap():
    from nora.agent.ports import ToolSpec, adapt

    calls = []

    async def web_search(query: str) -> dict:
        """Search public information. Call this when a venue needs context.

        Args:
            query: The public venue query.
        """
        calls.append(query)
        return {"status": "ok", "results": []}

    records = InMemoryComprehendRecords()
    state = LookupState(GraphFetched(), UNIT, 1, records, Metrics(), ())
    wrapped = ComprehendRead(
        adapt(ToolSpec(name="web_search", description="", schema={}, call=web_search, budget=0)),
        state,
    )
    for n in range(4):
        events = [
            e
            async for e in wrapped.stream(
                {
                    "name": "web_search",
                    "toolUseId": str(n),
                    "input": {"query": "Generated Hall on 2026-01-10"},
                },
                {},
            )
        ]
    assert len(calls) == 3
    assert _result_of(events[-1])["reason"] == "lookup_cap"
    assert state.used == {"web_search": 3}


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


async def test_parent_graph_switch_is_required_too():
    graph = FakeMemoryGraph()
    await graph.enqueue(WORK)
    r = runner(graph, InMemoryComprehendRecords(), ScriptedCognitionRunner([]), graph_enabled=False)
    assert not await r.run_once()
    assert graph.work[WORK.id].attempts == 0


async def test_reshape_keeps_valid_siblings_with_malformed_proposals_and_evidence():
    from nora.domain.memory_graph import Admission, Evidence, GraphEdge, GraphNode

    relation = Relation(
        "generated-current-relation",
        PERSON.key,
        "likes",
        PLACE.key,
        "notebook",
        UNIT,
        asserted_at=AT,
    )
    evidence = Evidence(SOURCE.key, relation_id=relation.id, span="like Hall")
    subgraph = Subgraph(
        "ok",
        (GraphNode(PERSON.key, PERSON), GraphNode(PLACE.key, PLACE)),
        (GraphEdge(relation, 1),),
    )
    valid = {
        "part": "trait",
        "text": "Generated grounded trait",
        "relation_ids": [relation.id],
        "evidence": [list(SOURCE.key)],
    }
    unshown = {**valid, "text": "Generated unshown trait", "evidence": [["message", "unseen"]]}
    records = InMemoryComprehendRecords()
    malformed = {**valid, "text": 123}
    bad_key = {**valid, "evidence": [["message"]]}
    model = ScriptedCognitionRunner([[("profile_stage", {
        "proposals_in": [malformed, bad_key, valid, unshown]})]])
    r = runner(FakeMemoryGraph(), records, model)
    await r._reshape_entity(
        ITEM, PERSON.key, subgraph, Admission(relations=(relation,), evidence=(evidence,)), 7
    )
    assert records.shadows[0]["would_writes"] == [valid]
    assert records.shadows[0]["parent"] == 7
    assert records.refusals[-1][1:] == ("profile", "ungrounded")
    assert sum(reason == "schema" for _, _, reason in records.refusals) == 2


def test_preview_never_labels_a_notebook_item_as_record_evidence():
    from nora.domain.memory_graph import GraphNode

    graph = Subgraph("ok", (GraphNode(("note", "generated-note")),))
    text = preview_text(Preview((), graph, (), (AT, AT)))
    assert "note:generated-note" in text
    assert "time=unavailable" in text and "state=unavailable" in text
    assert "Record/Rollup" not in text


@pytest.mark.parametrize("kind,verb", [("note", "concerns"), ("memo", "settles"),
                                       ("event_entry", "records")])
async def test_register_worker_retries_links_and_carries_cited_scope(kind, verb, monkeypatch):
    graph = FakeMemoryGraph()
    await graph.upsert_entity(PLACE)
    work = replace(WORK, source=(kind, "generated-register"))
    cited = replace(SOURCE, persons=("generated-person",))
    item = replace(ITEM, work=work, sources=(
        Source(work.source, "notebook", UNIT, cited.persons, "Generated Hall"), cited))
    records = InMemoryComprehendRecords({work.source: item})
    model = ScriptedCognitionRunner([[('memory_stage', EMPTY)]])
    await graph.enqueue(work)  # phase one's row writer owns the sole queue item
    original = graph.stage_and_admit
    async def unavailable(**kwargs):
        raise RuntimeError("generated graph outage during link admission")
    monkeypatch.setattr(graph, "stage_and_admit", unavailable)
    r = runner(graph, records, model)
    await r.run_once()
    assert graph.work[work.id].state == "pending" and not graph.relations
    monkeypatch.setattr(graph, "stage_and_admit", original)
    r.clock = lambda: AT + timedelta(days=1)
    await r.run_once()
    assert graph.work[work.id].state == "settled"
    links = [v for v in graph.relations.values() if v.src == work.source and v.rel == verb]
    assert len(links) == 1
    assert links[0].persons == cited.persons
    assert len(graph.work) == 1


async def test_bad_relation_timestamp_and_evidence_leave_valid_siblings_committed():
    from nora.app.comprehend import GraphWrite
    graph = FakeMemoryGraph()
    for e in (PERSON, PLACE):
        await graph.upsert_entity(e)
    records = InMemoryComprehendRecords()
    r = runner(graph, records, ScriptedCognitionRunner([]))
    bad = {**WRITE['relations'][0], 'id': 'bad', 'valid_from': 'not-a-time'}
    output = GraphWrite.model_validate({**WRITE, 'relations': [bad, *WRITE['relations']],
        'evidence': [*WRITE['evidence'], {'source': list(SOURCE.key)}]})
    fetched = GraphFetched()
    fetched.show(SOURCE)
    admitted = await r._admit(WORK, output, fetched, 1)
    assert len(admitted.relations) == 1
    assert (1, 'relation', 'schema') in records.refusals
    assert (1, 'evidence', 'schema') in records.refusals


async def test_correction_outside_preview_reshapes_its_person():
    graph = FakeMemoryGraph()
    other = pointer_entity('person', UNIT, 'other-generated-person', 'Other Generated')
    hidden = replace(PLACE, id="generated-hidden-place", name="Hidden Venue", aliases=())
    for e in (other, hidden):
        await graph.upsert_entity(e)
    rel = Relation('generated-hidden-relation', other.key, 'likes', hidden.key,
                   'notebook', UNIT, asserted_at=AT)
    await graph.stage_and_admit(entities=(), relations=(rel,),
        evidence=(Evidence(SOURCE.key, relation_id=rel.id),), sources={SOURCE.key: SOURCE})
    await graph.enqueue(WORK)
    records = InMemoryComprehendRecords({WORK.source: ITEM})
    model = ScriptedCognitionRunner([[('memory_stage', {**EMPTY, 'corrections': [{
        'relation_id': rel.id, 'kind': 'dispute', 'at': AT.isoformat(),
        'evidence': [list(SOURCE.key)]}]})],
        *[[('profile_stage', {'proposals_in': []})] for _ in range(3)]])
    await runner(graph, records, model).run_once()
    assert graph.work[WORK.id].state == 'settled'
    assert 'generated-hidden-relation' not in model.prompts[0]
    assert records.shadows[0]['entity'] == other.key


async def test_fake_entities_share_sql_id_namespace_across_kinds():
    graph = FakeMemoryGraph()
    await graph.upsert_entity(PLACE)
    assert graph.entities[PLACE.id] == PLACE
    with pytest.raises(ValueError):
        await graph.upsert_entity(replace(PLACE, kind="event"))


def test_web_page_preview_uses_found_layer_word():
    graph = Subgraph("ok", (GraphNode(("web_page", "generated-page")),))
    assert "found" in preview_text(Preview((), graph, (), (AT, AT)))


async def test_register_readded_mention_has_a_current_link_at_the_item_time():
    from nora.app.register_graph import RegisterGraphLinks

    graph = FakeMemoryGraph()
    await graph.upsert_entity(PLACE)
    records = InMemoryComprehendRecords()
    links = RegisterGraphLinks(graph=graph, records=records)
    source = ("note", "generated-reopening-note")

    def revision(body, version):
        work = replace(WORK, id=f"generated-revision-{version}", source=source,
                       as_of=AT + timedelta(days=version))
        return replace(ITEM, work=work, sources=(
            Source(source, "notebook", UNIT, (), body), SOURCE))

    await links.project(revision("Hall", 0), 1)
    await links.project(revision("No venue mentioned", 1), 2)
    closed = await graph.walk((source,), Walk(
        unit=UNIT, persons=frozenset(), valid_at=AT + timedelta(days=1)))
    assert not closed.relations
    assert any(r.valid_to == AT + timedelta(days=1) for r in graph.relations.values())
    await links.project(revision("Hall again", 2), 3)
    current = await graph.walk((source,), Walk(
        unit=UNIT, persons=frozenset(), valid_at=AT + timedelta(days=2)))
    assert len(current.relations) == 1
    assert current.relations[0].relation.dst == PLACE.key
    assert current.relations[0].relation.valid_to is None


async def test_register_partial_walk_raises_before_projecting_any_changes():
    from nora.app.register_graph import RegisterGraphLinks

    graph = FakeMemoryGraph()
    venues = [replace(PLACE, id=f"generated-venue-{i}", name=f"Venue {i}", aliases=())
              for i in range(21)]
    for venue in venues:
        await graph.upsert_entity(venue)
    source = ("note", "generated-capped-note")
    item = replace(ITEM, work=replace(WORK, source=source), sources=(
        Source(source, "notebook", UNIT, (), "; ".join(v.name for v in venues)), SOURCE))
    records = InMemoryComprehendRecords()
    links = RegisterGraphLinks(graph=graph, records=records)
    await links.project(item, 1)
    assert len(graph.relations) == 21
    changed = replace(item, sources=(Source(source, "notebook", UNIT, (), "No mentions"), SOURCE))
    with pytest.raises(RuntimeError, match="Register links unavailable"):
        await links.project(changed, 2)
    assert all(r.valid_to is None for r in graph.relations.values())
    assert len(records.graphs) == 1


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
