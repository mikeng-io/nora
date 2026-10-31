"""Generated snapshot experiment boundaries and fail-closed aggregation."""

import hashlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nora.agent.comprehend_tools import egress_reason
from nora.app.composition import search_provider
from scripts import memory_graph_spike as spike


@pytest.fixture(autouse=True)
def restore_logging_suppression():
    previous = logging.root.manager.disable
    try:
        yield
    finally:
        logging.disable(previous)


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


def test_age_summary_preserves_insufficient_population():
    """ERROR names a broken instrument only; INSUFFICIENT and FAIL keep theirs."""
    assert spike.age_status([]) == "ERROR"
    assert spike.age_status([{"status": "ERROR"}]) == "ERROR"
    assert spike.age_status([{"status": "INSUFFICIENT"}]) == "INSUFFICIENT"
    assert spike.age_status([{"status": "PASS"}, {"status": "INSUFFICIENT"}]) == "INSUFFICIENT"
    assert spike.age_status([{"status": "PASS"}, {"status": "FAIL"}]) == "FAIL"
    assert spike.age_status([{"status": "PASS"}, {"status": "PASS"}]) == "REPORTED"


def test_depth3_latency_verdict_binds_question_population():
    """A finite p95 under the ceiling is PASS only over a sufficient, unbroken
    population — small-n and absent readings never pass."""

    def verdict(status, questions, p95, minimum=20):
        run = {"status": status, "questions": questions, "sql_p95_ms": p95}
        return spike.depth3_verdict(run, minimum)

    assert verdict("INSUFFICIENT", 4, 50.0) == "INSUFFICIENT"
    assert verdict("ERROR", 4, 50.0) == "ERROR"
    assert verdict("PASS", 20, 50.0) == "PASS"
    assert verdict("PASS", 20, 250.0) == "FAIL"
    # A declared measured population with no reading is an instrument gap, and
    # a correctness-FAILED run still yields its honestly measured latency.
    assert verdict("PASS", 20, None) == "ERROR"
    assert verdict("FAIL", 20, 50.0) == "PASS"


async def test_judge_malformed_is_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        spike, "model_endpoint", lambda path, purpose, model="": ("", "", model)
    )
    judges = spike.Judges(
        SimpleNamespace(llm=SimpleNamespace(model="", timeout_seconds=1)),
        ["generated-a", "generated-b"],
        tmp_path / "config.toml",
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
            wire = ['"person:p1"', '"event:e1"'] if "RETURN DISTINCT b.key" in str(compiled) else []
            return SimpleNamespace(scalars=lambda: iter(wire))

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
        expected = ["person:p1", "event:e1"] if query.startswith("MATCH (a:N)-[") else []
        assert await spike.cypher(Conn(), "generated_graph", query) == expected
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
    assert result["verdicts"]["relation_precision"] == [["supported", "unsupported"]] * 2
    assert [p for p, _ in seen if "different_entities" in p] == [
        {"different_entities": serialized(merge)}
    ]
    assert result["verdicts"]["wrong_merges"] == [["unsupported"]] * 2
    assert result["verdicts"]["reading_precision"] == [["unsupported"]] * 2
    assert result["population"] == {"committed": 2, "preview": 3, "scored": 2, "missing": 0}
    expected_evidence = {"input": {"prompt": "Generated source"}, "reads": [read]}
    assert seen and all(e == expected_evidence for _, e in seen)


async def test_session_quality_marks_unscored_committed_as_missing():
    """A committed id absent from the connected preview is never judged from the
    proposal: it is an unavailable measurement (ERROR per judge), counted in the
    population, so relation rows cannot silently PASS on a dropped denominator."""
    seen = []

    async def judge(payload):
        seen.append(payload["proposition"])
        return ["supported", "supported"]

    result = await spike.session_quality(
        SimpleNamespace(score=judge),
        {"prompt": "Generated source"},
        [
            {
                "name": "memory_propose",
                "input": {},
                "result": {
                    "admitted": [],
                    "committed": ["generated-committed"],
                    "connected": {"status": "ok", "nodes": [], "relations": []},
                },
            },
            {
                "name": "memory_propose",
                "input": {},
                "result": {
                    "admitted": [],
                    "committed": ["generated-scored"],
                    "connected": {
                        "status": "ok",
                        "relations": [
                            {"relation": {"id": "generated-scored", "rel": "concerns"}}
                        ],
                    },
                },
            },
        ],
    )
    assert result["verdicts"]["relation_precision"] == [["supported", "ERROR"]] * 2
    assert result["verdicts"]["wrong_merges"] == [["ERROR"]] * 2
    assert result["population"] == {"committed": 2, "preview": 1, "scored": 1, "missing": 1}
    # The missing relation was measured as unavailable, never sent to a judge.
    assert seen and all(p.get("id") != "generated-committed" for p in seen if isinstance(p, dict))


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


async def test_judge_refuses_effective_model_override(monkeypatch, tmp_path):
    monkeypatch.setattr(
        spike, "model_endpoint",
        lambda path, purpose, model="": ("generated-url", "", "pinned"),
    )
    cfg = SimpleNamespace(llm=SimpleNamespace(model="", timeout_seconds=1))
    judges = spike.Judges(cfg, ["requested-a", "requested-b"], tmp_path / "config.toml")
    assert judges.effective_models == ["pinned", "pinned"]
    build = AsyncMock(side_effect=AssertionError("must refuse before model call"))
    monkeypatch.setattr(spike, "build_structured_model", build)
    assert await judges.score({}) == ["ERROR", "ERROR"]
    with pytest.raises(ValueError, match="effective judge"):
        await judges.call("requested-a", "generated", {})
    build.assert_not_called()


@pytest.mark.parametrize("container", [False, True])
async def test_judges_dial_the_eval_route_where_the_measurer_runs(
    tmp_path, monkeypatch, example_config_path, container
):
    """Every judge call resolves `eval_judge` through `model_endpoint`: the
    route's container-only provider name is rewritten for a host measurer and
    kept inside the compose network — `cfg.endpoint` alone dials the
    container name on the host, which is an NXDOMAIN, not a verdict."""
    import importlib

    text = example_config_path.read_text()
    text = text.replace(
        'base_url = "http://localhost:20128/v1"',
        'provider = "generated-gateway"\nbase_url = "http://localhost:20128/v1"',
        1,
    )
    example_config_path.write_text(
        text
        + '\n[providers.generated-gateway]\n'
        + 'base_url = "http://host.docker.internal:20128/v1"\n'
        + 'api_key_env = "OMNIROUTE_API_KEY"\n'
        + '\n[model_routes.eval_judge]\n'
        + 'provider = "generated-gateway"\n'
    )
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-key")
    obtain = importlib.import_module("nora.subject.obtain")
    monkeypatch.setattr(obtain, "_resolves", lambda host: container)
    dialed = []

    def record(**kwargs):
        dialed.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(spike, "build_structured_model", record)
    monkeypatch.setattr(
        spike,
        "ask_text",
        AsyncMock(return_value=SimpleNamespace(finish_reason="stop", content="[]")),
    )
    cfg = SimpleNamespace(
        llm=SimpleNamespace(model="generated-session", timeout_seconds=1),
        endpoint=lambda purpose, model: (
            "http://host.docker.internal:20128/v1",
            "generated-key",
            model,
        ),
    )
    judges = spike.Judges(cfg, ["cx/gpt-6.1-sol", "kmc/k3-256k"], example_config_path)
    assert judges.effective_models == ["cx/gpt-6.1-sol", "kmc/k3-256k"]
    for model_id in ("cx/gpt-6.1-sol", "kmc/k3-256k", "generated-session"):
        await judges.call(model_id, "generated", {})
    expected = (
        "http://host.docker.internal:20128/v1"
        if container
        else "http://127.0.0.1:20128/v1"
    )
    assert [d["base_url"] for d in dialed] == [expected] * 3
    assert [d["model_id"] for d in dialed] == [
        "cx/gpt-6.1-sol",
        "kmc/k3-256k",
        "generated-session",
    ]
    assert all(d["api_key"] == "generated-key" for d in dialed)


async def test_age_comparison_measures_the_source_once_outside_replay(
    tmp_path, monkeypatch
):
    """The engine comparison binds the frozen projected source once per run;
    the as-of sandbox — correctly holding no graph rows at the wake instant —
    serves the session replay and is never asked for the graph population."""
    import contextlib

    import evals.backbone
    import nora.subject
    from nora.infra.sql import cognition_replay

    source_url = "postgresql+asyncpg://127.0.0.1:9/generated-source"
    sandbox_url = "postgresql+asyncpg://127.0.0.1:9/generated-sandbox"
    calls = []

    class FakeEngine:
        def __init__(self, url):
            self.url = url

        async def dispose(self):
            assert logging.root.manager.disable == logging.CRITICAL

    def create(url):
        assert logging.root.manager.disable == logging.CRITICAL
        return FakeEngine(url)

    async def generated_rows(engine, sql, params=None):
        calls.append((engine.url, sql))
        if "FROM cognition_wake" in sql:
            return [{"id": 1, "at_ms": 1}, {"id": 2, "at_ms": 2}]
        if "SELECT body FROM messages" in sql:
            body = "[image generated]" if params["id"] == 2 else "generated text"
            return [{"body": body}]
        if "is_nora" in sql:
            return [{"n": 0}]
        if "string_agg" in sql:
            return [{"digest": "generated-" + engine.url}]
        if "FROM tag_trace" in sql:
            return [{"messages": []}]
        if "FROM relation" in sql:
            # The projected source holds the relation population; the restored
            # sandbox holds none at the wake instant.
            return [
                {
                    "unit": "generated-unit",
                    "persons": ["generated-person"],
                    "src_kind": "event",
                    "src_id": "generated-edge",
                    "dst_kind": "topic",
                    "dst_id": "generated-topic",
                    "rel": "concerns",
                }
            ] if engine.url == source_url else []
        raise AssertionError(f"unexpected SQL on {engine.url}: {sql}")

    async def fake_wake(engine, wake_id):
        return (
            1_700_000_000_000 + wake_id,
            SimpleNamespace(
                unit="generated-unit",
                pending=[
                    SimpleNamespace(kind="message", ref=f"messages:{wake_id}")
                ],
            ),
        )

    @contextlib.asynccontextmanager
    async def fake_sandbox(database_url, at):
        yield sandbox_url, SimpleNamespace()

    class FakeMetrics:
        def __init__(self):
            self.values = iter([0, 7, 0])

        def get(self, name):
            return next(self.values, 0)

    runner = SimpleNamespace(
        inspect=AsyncMock(return_value={"prompt": "generated"}),
        replay=AsyncMock(
            return_value=SimpleNamespace(session_id="generated-session", closed=[])
        ),
    )

    async def fake_obtain(spec):
        return SimpleNamespace(
            database_url=spec.database_url,
            cfg=SimpleNamespace(),
            deps=SimpleNamespace(cognition_runner=runner, metrics=FakeMetrics()),
        )

    cfg = SimpleNamespace(
        store=SimpleNamespace(database_url_env="GENERATED_DB"),
        llm=SimpleNamespace(
            model="generated-session", timeout_seconds=1, model_dump=dict
        ),
        providers={},
        model_routes={},
        cognition=SimpleNamespace(model_dump=dict),
    )
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text("generated")
    identity_file = tmp_path / "identity.toml"
    identity_file.write_text("generated")
    bound = []
    real_age = spike.age_comparison

    async def spy(engine, age_url, judges, output_dir, minimum):
        bound.append(engine.url)
        return await real_age(engine, age_url, judges, output_dir, minimum)

    quality = AsyncMock(
        return_value={"verdicts": {}, "population": {"committed": 0, "missing": 0}}
    )
    monkeypatch.setattr(spike, "age_comparison", spy)
    monkeypatch.setattr(spike, "session_quality", quality)
    monkeypatch.setattr(spike, "index_replay_sandbox", AsyncMock(
        return_value=SimpleNamespace(line=lambda: "generated-index")))
    monkeypatch.setattr(spike, "sql_answer", AsyncMock(return_value=["event:generated-edge"]))
    monkeypatch.setattr(spike, "model_endpoint",
                        lambda path, purpose, model="": ("generated-url", "", model))
    monkeypatch.setattr(spike, "load", lambda path: cfg)
    monkeypatch.setattr(spike, "credentials", lambda: {
        "GENERATED_DB": "postgresql+asyncpg://127.0.0.1:9/generated-production"})
    monkeypatch.setattr(spike, "create_sql_engine", create)
    monkeypatch.setattr(spike, "rows", generated_rows)
    monkeypatch.setattr(spike, "load_policy", lambda path: SimpleNamespace(
        rooms=lambda: [SimpleNamespace(
            unit="generated-unit", platform="signal", primary_id="generated-room")]))
    monkeypatch.setattr(spike, "load_persons", lambda path: SimpleNamespace(by_person={}))
    monkeypatch.setattr(spike, "projection_context", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(cognition_replay, "recorded_wake", fake_wake)
    monkeypatch.setattr(nora.subject, "sandbox_as_of", fake_sandbox)
    monkeypatch.setattr(nora.subject, "obtain", fake_obtain)
    monkeypatch.setattr(evals.backbone, "_close", AsyncMock())

    args = SimpleNamespace(
        config=tmp_path / "config.toml",
        policy=policy_file,
        identity=identity_file,
        database_url=source_url,
        snapshot=True,
        age_url=None,
        judge_a="generated-judge-a",
        judge_b="generated-judge-b",
        judge_vendor_a="generated-vendor-a",
        judge_vendor_b="generated-vendor-b",
        output=tmp_path / "out",
        per_stratum=1,
        minimum=1,
        seed=17,
        projection_rate=100,
        project_only=False,
        skip_projection=True,
        smoke=False,
    )
    report = await spike.run(args)

    # The comparison ran once, against the populated source — never the
    # empty-at-the-instant sandbox the session runner replayed in.
    assert bound == [source_url]
    assert [u for u, sql in calls if "FROM relation" in sql] == [source_url]
    assert [u for u, sql in calls if "FROM tag_trace" in sql] == [sandbox_url] * 2
    assert runner.inspect.await_count == 2
    assert all(c.kwargs["historical"] for c in runner.replay.await_args_list)
    assert quality.await_count == 2
    runs = report["age"]["runs"]
    assert len(runs) == 1 and runs[0]["questions"] == 1
    frozen = json.loads((tmp_path / "out" / "age" / "questions.json").read_text())
    assert frozen[0]["seed"] == ["event", "generated-edge"]
    assert frozen[0]["gold"] == ["event:generated-edge"]
