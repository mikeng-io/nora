"""The OFFICIAL entry, end to end on a generated source: `python -m evals`
main() → real materialise → real obtain/composition → real ToolSurface →
the real formatted request → `httpx.MockTransport`.

Nothing under test is stubbed: `main`, `_execute`, `_run_one`,
`materialise`, `RunEvidence`, `obtain`, the seeded snapshot, the agent
loop and the model's request formatting all run as they run in
production. The source is GENERATED — a `generated_eval` database on the
session's own testcontainer Postgres, migrated by `run_migrations` to the
candidate's head and seeded by the existing `seed_source` fixture
machinery at the corpus `room_a` coordinates (conversation id +
before_ms). The ONE injected seam is the SDK's own `http_client` — the
documented transport seam — so zero requests leave the process.

`respond` asserts the `nora.model.request` row is durable BEFORE it
answers (transport-time evidence, not after-the-fact), and the
write-failure control proves a refused record precludes the request
leaving at all. Zero paid calls, zero shared-DB reads or writes.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import httpx
import psycopg
import sqlalchemy as sa

import evals.__main__ as cli
from nora.agent import factory
from nora.subject import sandbox

#: The corpus `room_a` snapshot names a PSEUDONYM; the eval translates it
#: through `NORA_EVAL_PSEUDONYM_MAP`, which this test generates: the
#: pseudonym maps to a generated real id the synthetic source is seeded at.
PSEUDONYM_ID = "MuM4gROdh0/9GIRlOa9ckXgykZMkkNmZquM4gROdh08="
REAL_ID = "Z2VuZXJhdGVkLXN5bnRoZXRpYy1yb29tLWlkLWJhc2U2NA=="
BEFORE_MS = 1789909891000


def _sse(delta, finish, usage=(12, 3, 15)):
    chunks = [
        {"id": "g", "object": "chat.completion.chunk", "model": "generated-model",
         "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {"id": "g", "object": "chat.completion.chunk", "model": "generated-model",
         "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
         "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1],
                   "total_tokens": usage[2]}},
    ]
    return "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"


def _rows(evidence_root: Path) -> list[dict]:
    runs = sorted(evidence_root.glob("*-*/events.jsonl"), key=os.path.getmtime)
    if not runs:
        return []
    return [json.loads(line) for line in runs[-1].read_text().splitlines()]


def _scripted_respond(evidence_root: Path, requests: list[dict]):
    """Answers every SDK request with the deferred-load → browse → reply
    sequence; INSIDE the call it proves the formatted request was already
    written durable — the evidence-before-dispatch contract itself."""
    async def respond(request):
        body = json.loads(request.content)
        rows = _rows(evidence_root)
        formatted = [json.loads(r["attributes"]["nora.model.request.body"])
                     for r in rows if r["kind"] == "event"
                     and r["name"] == "nora.model.request"]
        assert body in formatted, (
            "request left before it was durably recorded")
        requests.append(body)
        step = len(requests)
        if step == 1:
            tool = {"name": "load_tools",
                    "arguments": json.dumps({"tools": ["computer_browse"]})}
        elif step == 2:
            tool = {"name": "computer_browse",
                    "arguments": json.dumps(
                        {"objective": "open the page",
                         "start_url": "https://example.com"})}
        else:
            tool = {"name": "send_reply",
                    "arguments": json.dumps({"text": "done."})}
        delta = {"role": "assistant", "tool_calls": [
            {"index": 0, "id": f"c{step}", "type": "function",
             "function": tool}]}
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=_sse(delta, "tool_calls"))
    return respond


def _run_one_case(tmp_path, monkeypatch, pg_admin, pg_template,
                  *, break_evidence_write: bool = False):
    """One real `main()` run of the scoped-browse case against a generated
    source. Returns `(rc, evidence_root, requests)`."""
    _run_one_case.refusals = []
    _run_one_case.fd_map = {}
    from nora.infra.sql.migrations_runner import run_migrations
    from tests.sandbox_fixtures import Knowledge, Message, seed_source

    # --- a generated source DB at the candidate's migration head ---
    admin_url = (f"postgresql://{pg_admin.user}:{pg_admin.password}"
                 f"@{pg_admin.host}:{pg_admin.port}/{pg_admin.maintenance_db}")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute('DROP DATABASE IF EXISTS "generated_eval" WITH (FORCE)')
        conn.execute('CREATE DATABASE "generated_eval"')
    source_url = (f"postgresql+asyncpg://{pg_admin.user}:{pg_admin.password}"
                  f"@{pg_admin.host}:{pg_admin.port}/generated_eval")
    run_migrations(source_url)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-key")
    import asyncio
    asyncio.run(seed_source(
        source_url, tmp_path / "seed",
        Knowledge(transcript=(Message(body="hello room", at_ms=BEFORE_MS - 60_000),)),
        template=pg_template,
        room=("signal", REAL_ID), before_ms=BEFORE_MS))
    # Date the generated rows' history at their own logical ts — the same
    # controlled-recorded_at pattern `test_full_restore_recall` uses, on
    # this test's OWN generated rows only.
    eng0 = sa.create_engine(source_url.replace("+asyncpg", "+psycopg"))
    with eng0.begin() as c:
        c.execute(sa.text(
            "UPDATE row_history SET recorded_at = "
            "to_timestamp((image->>'ts')::bigint / 1000.0) "
            "WHERE table_name='messages'"))
    # The private map a pseudonymised anchor resolves through — generated,
    # pointing the corpus pseudonym at the seeded real room.
    pmap = tmp_path / "pseudonyms.map"
    pmap.write_text(f"{PSEUDONYM_ID}==>{REAL_ID}\n")
    monkeypatch.setenv("NORA_EVAL_PSEUDONYM_MAP", str(pmap))

    # --- a generated deployment config (never the deployment's own) ---
    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    example = Path("config.example.toml").read_text()
    assert 'database_url_env = "DATABASE_URL"' in example
    (deploy_dir / "config.toml").write_text(example.replace(
        'database_url_env = "DATABASE_URL"',
        'database_url_env = "GEN_DB_URL"', 1).replace(
        'model_path = "/app/models/喂Nora.onnx"',
        f'model_path = "{tmp_path}/wake.onnx"'))
    # The subject's policy/identity are the deployment's OWN files — the
    # generated deployment carries a generated room and generated identity,
    # never the real host's.
    from nora.subject.obtain import _DEFAULT_IDENTITY, default_policy
    (deploy_dir / "policy.toml").write_text(
        default_policy(("signal", REAL_ID)))
    (deploy_dir / "identity.toml").write_text(_DEFAULT_IDENTITY)
    import evals.backbone as bb
    monkeypatch.setattr(bb, "_DEPLOYMENT_DIR", deploy_dir)
    monkeypatch.setenv("GEN_DB_URL", source_url)

    # --- private evidence + the run's own lock, on the synthetic source ---
    evidence_root = tmp_path / "evidence"
    monkeypatch.setenv("NORA_EVAL_EXCHANGES", str(evidence_root))
    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", tmp_path / "eval.lock")
    monkeypatch.setenv("NORA_COMPUTER_TOKEN", "generated-token")
    monkeypatch.setenv("NORA_COMPUTER_REDIS_URL", "redis://127.0.0.1:6390")
    registry = tmp_path / "scope-endpoints"
    registry.mkdir()
    fixture = tmp_path / "scoped.json"
    fixture.write_text(json.dumps({
        "computer.enabled": True,
        "computer.state_dir": str(tmp_path / "computer-state"),
        "computer.scoped.enabled": True,
        "computer.scoped.registry": str(registry)}))
    want_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()

    # --- the ONE injected seam: the SDK http_client ---
    requests: list[dict] = []
    real_create_client = factory.create_client

    def with_transport(**kwargs):
        kwargs.setdefault("http_client", httpx.AsyncClient(
            transport=httpx.MockTransport(
                _scripted_respond(evidence_root, requests))))
        return real_create_client(**kwargs)

    monkeypatch.setattr(factory, "create_client", with_transport)

    if break_evidence_write:
        import os as _os
        original_write = _os.write
        refusals = _run_one_case.refusals
        fd_map = _run_one_case.fd_map

        def fail_model_request(fd, data):
            if b"nora.model.request" in bytes(data):
                refusals.append({
                    "fd": fd, "target": fd_map.get(fd),
                    "sha256": hashlib.sha256(bytes(data)).hexdigest(),
                })
                raise OSError("generated write refusal")
            return original_write(fd, data)
        monkeypatch.setattr(_os, "write", fail_model_request)

    # --- the REAL official entry ---
    monkeypatch.setattr("sys.argv", [
        "python -m evals", "tool_selection",
        "--case", "route_computer_browse_a_page",
        "--deployment-config", str(deploy_dir / "config.toml"),
        "--source-url", source_url,
        "--subject-overrides", str(fixture),
        "--run-budget", "12,2000000,40000,30",
    ])
    # `main()` owns its asyncio.run + SIGTERM handler — it must run on the
    # main thread with no running loop, which is exactly what a
    # synchronous test gives it.
    rc = cli.main()
    return rc, evidence_root, requests, want_sha


def test_official_entry_composes_scoped_tool_and_records_evidence(
        tmp_path, monkeypatch, pg_admin, pg_template):
    """The real entry: override reaches the composed scoped computer, the
    budget counts the real requests, and events.jsonl binds provenance
    before the transport answers."""
    rc, evidence_root, requests, want_sha = _run_one_case(
        tmp_path, monkeypatch, pg_admin, pg_template)
    assert rc == 0, "the scripted trajectory routes computer_browse → PASS"
    rows = _rows(evidence_root)
    started = next(r for r in rows if r["kind"] == "run_started")
    md = started.get("metadata", started)
    assert md["subject_overrides"]["computer.scoped.enabled"] is True
    assert md["subject_overrides_sha256"] == want_sha
    assert md["run_budget"]["max_requests"] == 12
    formatted = [json.loads(r["attributes"]["nora.model.request.body"])
                 for r in rows if r["kind"] == "event"
                 and r["name"] == "nora.model.request"]
    # Every dispatched request is durably recorded (the respond assertion
    # already proves each one BEFORE the answer); background model calls
    # may interleave, so the wire sequence is a subsequence of the record.
    it = iter(formatted)
    assert all(any(b == f for f in it) for b in requests)
    assert len(formatted) >= len(requests)
    assert stat.S_IMODE(evidence_root.stat().st_mode) & 0o777 != 0o000
    final = next(r for r in reversed(rows)
                 if r["kind"] == "run_budget_final")
    assert final["requests_used"] == len(requests)


def test_evidence_write_failure_precludes_any_dispatch(
        tmp_path, monkeypatch, pg_admin, pg_template):
    """A refused `nora.model.request` write is `EvidenceError` BEFORE the
    request leaves — the transport sees zero requests, exactly one
    injected refusal, and the run records no successful finish."""
    rc, evidence_root, requests, _ = _run_one_case(
        tmp_path, monkeypatch, pg_admin, pg_template,
        break_evidence_write=True)
    assert rc != 0                    # sticky failure, invalid run
    assert _run_one_case.refusals and len(_run_one_case.refusals) == 1
    assert requests == []             # nothing left the process
    rows = _rows(evidence_root)
    assert not [r for r in rows if r["kind"] == "event"
                and r["name"] == "nora.model.request"]
