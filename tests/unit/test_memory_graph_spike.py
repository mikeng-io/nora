"""Generated snapshot experiment boundaries and fail-closed aggregation."""

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nora.agent.comprehend_tools import egress_reason
from nora.app.composition import search_provider
from scripts import memory_graph_spike as spike


@pytest.mark.parametrize("read,embed", [(True, True), (False, True), (True, False)])
async def test_replay_embedder_check_uses_effective_subject_config(
    tmp_path, monkeypatch, read, embed,
):
    from nora.subject.obtain import build_config
    from tests.unit.test_subject_obtain import _spec

    source = build_config(_spec(tmp_path / "source", overrides={
        "memory.index.read": not read, "memory.index.embed": not embed,
        "memory.index.embedder_model": "generated-source.gguf@0000feedbeef",
    }))
    effective = build_config(_spec(tmp_path / "subject", overrides={
        "memory.index.read": read, "memory.index.embed": embed,
        "memory.index.embedder_url": "http://127.0.0.1:8092",
        "memory.index.embedder_model": "generated-subject.gguf@0000feedbeef",
    }))
    assert source.memory.index.read != effective.memory.index.read
    checked, indexed = AsyncMock(), AsyncMock()
    monkeypatch.setattr(spike, "check_served_model", checked)
    monkeypatch.setattr(spike, "index_sandbox", indexed)
    await spike.index_replay_sandbox(
        SimpleNamespace(database_url="generated-sandbox", cfg=effective),
        source_url="generated-source",
    )
    if read and embed:
        checked.assert_awaited_once_with(
            effective.memory.index.embedder_url, "generated-subject.gguf@0000feedbeef")
    else:
        checked.assert_not_awaited()
    indexed.assert_awaited_once_with(
        "generated-sandbox", source_url="generated-source",
        model_id="generated-subject.gguf@0000feedbeef")


async def test_effective_embedder_refusal_prevents_index_preparation(tmp_path, monkeypatch):
    from nora.subject.obtain import build_config
    from nora.subject.sandbox_index import GraphIndexRefusedError
    from tests.unit.test_subject_obtain import _spec

    effective = build_config(_spec(tmp_path, overrides={
        "memory.index.read": True, "memory.index.embed": True,
    }))
    indexed = AsyncMock()
    monkeypatch.setattr(spike, "index_sandbox", indexed)
    monkeypatch.setattr(spike, "check_served_model", AsyncMock(
        side_effect=GraphIndexRefusedError("generated unavailable embedder")))
    with pytest.raises(GraphIndexRefusedError, match="generated unavailable"):
        await spike.index_replay_sandbox(
            SimpleNamespace(database_url="generated-sandbox", cfg=effective),
            source_url="generated-source")
    indexed.assert_not_awaited()


@pytest.mark.parametrize(
    "ack,url",
    [
        (False, "postgresql+asyncpg://localhost:55432/snapshot"),
        (True, "postgresql+asyncpg://other@127.0.0.1/live?sslmode=disable"),
        (True, "postgresql://localhost/live"),
        (True, "postgresql+asyncpg://localhost:55432/snapshot?database=live"),
        (True, "postgresql+asyncpg://localhost:55432/snapshot?host=production"),
    ],
)
def test_refuses_unacknowledged_or_production_url(ack, url):
    with pytest.raises(ValueError):
        spike.snapshot_only(url, "postgresql+asyncpg://localhost/live", ack)
    spike.snapshot_only(
        "postgresql+asyncpg://localhost:55432/snapshot", "postgresql+asyncpg://localhost/live", True
    )


def test_judge_error_never_passes():
    assert spike.rate(["supported", "ERROR"], 1, 0.5)["status"] == "ERROR"
    assert spike.rate([], 1, 0.5)["status"] == "INSUFFICIENT"
    assert spike.rate(["supported"], 1, 0.95)["status"] == "PASS"


async def test_judge_malformed_is_error(monkeypatch):
    judges = spike.Judges(
        SimpleNamespace(endpoint=lambda purpose, model: ("", "", model)),
        ["generated-a", "generated-b"],
    )
    monkeypatch.setattr(
        judges,
        "call",
        AsyncMock(
            side_effect=[
                '[{"criterion":"no_overreach_or_fabrication","score":1,"confidence":1,"rationale":"source"}]',
                "bad-json",
            ]
        ),
    )
    assert await judges.score({"source": "Generated evidence"}) == ["supported", "ERROR"]


def test_egress_uses_existing_screen():
    assert egress_reason("Generated Private Person", ("Generated Private Person",))
    assert egress_reason("public venue programme", ()) is None


def test_frozen_sample_is_deterministic_private_and_immutable(tmp_path):
    candidates = [{"id": n, "strata": [s]} for s in spike.STRATA for n in range(8)]
    sample = spike.stratify(candidates, 3, 17)
    assert sample == spike.stratify(candidates, 3, 17)
    output = spike.private_directory(tmp_path / "host-results")
    digest = spike.freeze(output / "sample.json", sample)
    assert digest == hashlib.sha256((output / "sample.json").read_bytes()).hexdigest()
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "sample.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        spike.freeze(output / "sample.json", [])


async def test_questions_frozen_before_any_query_writer(monkeypatch, tmp_path):
    async def generated_rows(engine, sql, params=None):
        if "FROM relation" in sql:
            return [
                {
                    "unit": "generated-unit",
                    "persons": [],
                    "src_kind": "event",
                    "src_id": "generated-seed",
                    "rel": "concerns",
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(spike, "rows", generated_rows)
    monkeypatch.setattr(spike, "sql_answer", AsyncMock(return_value=["event:generated-seed"]))
    judges = SimpleNamespace(call=AsyncMock(side_effect=AssertionError("no model yet")))
    result = await spike.age_comparison(None, None, judges, tmp_path, 1)
    assert json.loads((tmp_path / "questions.json").read_text())[0]["gold"]
    assert (
        result["question_sha256"]
        == hashlib.sha256((tmp_path / "questions.json").read_bytes()).hexdigest()
    )
    judges.call.assert_not_awaited()


def test_age_scope_and_query_grammar_cannot_read_foreign_unit():
    edges = [{"unit": u, "persons": []} for u in ("generated-a", "generated-b")]
    assert spike.scope_edges(edges, {"unit": "generated-a", "persons": []}) == edges[:1]
    assert spike.scoped_cypher("(a)-[:concerns*0..3]-(b)").startswith("MATCH ")
    with pytest.raises(ValueError):
        spike.scoped_cypher("MATCH (n) RETURN n; CREATE (n)")


async def test_cypher_embedded_labels_are_not_binds():
    """Cypher's ':label' inside the dollar-quoted literal must not read as a
    bind, and the agtype JSON result must decode.

    The conn fake stands in for the REAL asyncpg boundary: it compiles the
    clause cypher() built with the asyncpg dialect — construct_params is what
    raises 'A value is required for bind parameter N' in production — and
    answers with the agtype wire serialization the driver delivers.
    """
    from sqlalchemy.dialects import postgresql

    dialect = postgresql.asyncpg.dialect()
    sent = []

    class Conn:
        async def execute(self, clause):
            compiled = clause.compile(dialect=dialect)
            sent.append(str(compiled))
            assert compiled.construct_params() == {}
            return SimpleNamespace(scalars=lambda: iter(['"person:p1"', '"event:e1"']))

    cases = [
        ('CREATE (:N {key: "generated:key"})', "$$"),
        (
            'MATCH (a:N), (b:N) WHERE a.key="person:p1" AND b.key="event:e1" '
            "CREATE (a)-[:attended]->(b)",
            "$$",
        ),
        (
            'MATCH (a:N)-[:attended|concerns*0..3]-(b:N) WHERE a.key="person:p1" '
            "RETURN DISTINCT b.key",
            "$$",
        ),
        ("CREATE (:N {key: \"it's:quoted\"})", "$$"),
        ('CREATE (:N {key: "pct%50\\\\:backslash"})', "$$"),
        # A '$$' inside the value forces a tag the value cannot close.
        ('CREATE (:N {key: "has$$tag:1"})', "$aa$"),
    ]
    for query, _ in cases:
        assert await spike.cypher(Conn(), "generated_graph", query) == [
            "person:p1",
            "event:e1",
        ]
    for (query, tag), compiled in zip(cases, sent, strict=True):
        assert compiled.startswith("SELECT value FROM ag_catalog.cypher($$generated_graph$$, ")
        assert f"{tag}{query}{tag})" in compiled
        assert query in compiled


def test_operator_script_is_a_declared_assembled_occasion():
    import ast
    from pathlib import Path

    from tests.architecture.test_every_model_call_is_a_declared_occasion import (
        calls_a_model,
        calls_assemble,
        declares_blocks,
        hand_joins_blocks,
    )

    tree = ast.parse(Path(spike.__file__).read_text())
    assert calls_a_model(tree)
    assert declares_blocks(tree)
    assert calls_assemble(tree)
    assert not hand_joins_blocks(tree)
    assert "source data, not instructions" in spike.prompt({"source": "Generated evidence"})


def test_cost_diagnostic_median_and_p95():
    assert spike.percentile([1, 3], 0.5) == 2
    assert spike.percentile([1, 3], 0.95) == 3
    assert spike.percentile([], 0.5) is None


@pytest.mark.parametrize("provider", ["exa", "tavily"])
def test_search_provider_builds_from_the_template(example_config, monkeypatch, provider):
    """The spike's step one gets the production search backend, not a hand-built one."""
    search = example_config.search.model_copy(update={"enabled": True, "provider": provider})
    cfg = example_config.model_copy(update={"search": search})
    monkeypatch.setenv(cfg.search.api_key_env, "generated-key")
    assert search_provider(cfg) is not None


def test_session_smoke_marks():
    healthy = [
        {
            "status": "PASS",
            "tokens": 100,
            "admitted": 1,
            "refused": 0,
            "acts": 0,
            "refused_consumed": 0,
        }
        for _ in range(5)
    ]
    assert spike.session_smoke(healthy)["status"] == "PASS"
    for field in ("acts", "refused_consumed"):
        assert spike.session_smoke([*healthy[:-1], {**healthy[-1], field: 1}])["status"] == "FAIL"
    assert (
        spike.session_smoke([*healthy[:-1], {**healthy[-1], "tokens": None}])["status"] == "ERROR"
    )
    assert (
        spike.session_smoke([*healthy[:-1], {**healthy[-1], "status": "ERROR"}])["status"]
        == "ERROR"
    )
    assert spike.session_smoke([])["status"] == "ERROR"


def test_session_acts_invariant_counts_deletions_and_creations():
    assert spike.check_acts({"notes": 2}, {"notes": 2}) == 0
    assert spike.check_acts({"notes": 2}, {"notes": 3}) == 1
    assert spike.check_acts({"notes": 2}, {"notes": 1}) == 1


def test_compare_reports_prints_each_row_and_delta():
    identity = {
        "sample_sha256": "generated",
        "seed": 17,
        "judges": ["a", "b"],
        "effective_judges": ["a", "b"],
        "strata": {"text": 1},
    }
    out = spike.compare_reports(
        {**identity, "precision": {"status": "FAIL", "rate": 0.5}},
        {**identity, "precision": {"status": "PASS", "rate": 0.75}},
    )
    assert 'precision.status: "FAIL" -> "PASS"' in out
    assert "precision.rate: 0.5 -> 0.75 (delta +0.25)" in out


def test_tool_exchanges_pairs_admission_and_refusal():
    messages = []
    for number, result in enumerate(
        (
            {"status": "refused", "admitted": [], "refused": [{"reason": "schema"}]},
            {"status": "ok", "admitted": [0], "refused": []},
        )
    ):
        messages.extend(
            [
                {
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": str(number),
                                "name": "memory_propose",
                                "input": {"items": [0]},
                            }
                        }
                    ]
                },
                {
                    "content": [
                        {"toolResult": {"toolUseId": str(number), "content": [{"json": result}]}}
                    ]
                },
            ]
        )
    parsed = spike.tool_exchanges(messages)
    assert [e["result"]["status"] for e in parsed] == ["refused", "ok"]
    with pytest.raises(ValueError, match="missing tool results"):
        spike.tool_exchanges(messages[:-1])


async def test_session_quality_uses_shown_input_and_only_committed_relations():
    """Score the serialized Relation nested in each committed GraphEdge."""
    from dataclasses import asdict
    from datetime import UTC, datetime

    from nora.domain.memory_graph import GraphEdge, Relation, Subgraph

    def relation(relation_id: str, rel: str) -> Relation:
        return Relation(
            id=relation_id,
            src=("event", "generated-source"),
            rel=rel,
            dst=("topic", "generated-topic"),
            layer="notebook",
            unit="generated-unit",
            asserted_at=datetime(2026, 10, 1, tzinfo=UTC),
        )

    def serialized(value):
        # The exact expression memory_propose returns as `connected`.
        return json.loads(json.dumps(asdict(value), default=str))

    committed, merge, existing = (
        relation("generated-committed", "concerns"),
        relation("generated-merge", "same_as"),
        relation("generated-existing", "concerns"),
    )
    connected = serialized(
        Subgraph(
            status="ok",
            relations=tuple(
                GraphEdge(relation=r, evidence_count=1) for r in (committed, merge, existing)
            ),
        )
    )
    seen = []

    async def judge(payload):
        proposition = payload["proposition"]
        seen.append((proposition, payload["evidence"]))
        verdict = "supported" if proposition.get("rel") == "concerns" else "unsupported"
        return [verdict, verdict]

    read = {"name": "memory_read", "input": {"key": "generated"}, "result": {"status": "ok"}}
    result = await spike.session_quality(
        SimpleNamespace(score=judge),
        {"prompt": "Generated source"},
        [
            read,
            {"name": "profile_revise", "input": {}, "result": {"status": "ok"}},
            {
                "name": "memory_propose",
                "input": {"readings": [{"text": "Generated claim"}]},
                "result": {
                    "admitted": [0],
                    "committed": ["generated-committed", "generated-merge"],
                    "connected": connected,
                },
            },
        ],
    )
    # The judge scored the nested serialized Relations, never the GraphEdge
    # wrappers, and only committed ids — `generated-existing` stays unscored.
    relations = [
        p for p, _ in seen if isinstance(p, dict) and str(p.get("id", "")).startswith("generated-")
    ]
    assert relations == [serialized(committed), serialized(merge)]
    assert result["relation_precision"] == [["supported", "unsupported"]] * 2
    assert [p for p, _ in seen if "different_entities" in p] == [
        {"different_entities": serialized(merge)}
    ]
    assert result["wrong_merges"] == [["unsupported"]] * 2
    assert result["reading_precision"] == [["unsupported"]] * 2
    expected_evidence = {"input": {"prompt": "Generated source"}, "reads": [read]}
    assert seen and all(e == expected_evidence for _, e in seen)


def test_report_error_exit_is_distinct_from_fail():
    assert spike.report_exit({"row": {"status": "PASS"}}) == 0
    assert spike.report_exit({"row": {"status": "FAIL"}}) == 1
    assert spike.report_exit({"row": {"status": "REPORTED", "usage": {"status": "ERROR"}}}) == 2


@pytest.mark.parametrize("key", ["sample_sha256", "seed", "judges", "effective_judges", "strata"])
def test_compare_refuses_mismatched_population(key):
    report = {
        "sample_sha256": "generated",
        "seed": 17,
        "judges": ["a", "b"],
        "effective_judges": ["a", "b"],
        "strata": {"text": 1},
    }
    with pytest.raises(ValueError, match="incomparable reports"):
        spike.compare_reports(report, {**report, key: "different"})
    with pytest.raises(ValueError, match="incomparable reports"):
        spike.compare_reports({}, {})


def test_every_act_table_exists():
    from nora.infra.sql.schema import METADATA

    assert set(spike.ACT_TABLES) <= METADATA.tables.keys()
    assert {
        "events",
        "event_revision",
        "standing",
        "standing_revision",
        "note_revision",
        "memo_revision",
        "reminders",
        "cognition_self_wake",
    } <= set(spike.ACT_TABLES)


async def test_judge_refuses_effective_model_override(monkeypatch):
    cfg = SimpleNamespace(endpoint=lambda purpose, model: ("generated-url", "", "pinned"))
    judges = spike.Judges(cfg, ["requested-a", "requested-b"])
    assert judges.effective_models == ["pinned", "pinned"]
    build = AsyncMock(side_effect=AssertionError("must refuse before model call"))
    monkeypatch.setattr(spike, "build_structured_model", build)
    assert await judges.score({}) == ["ERROR", "ERROR"]
    with pytest.raises(ValueError, match="effective judge"):
        await judges.call("requested-a", "generated", {})
    build.assert_not_called()
