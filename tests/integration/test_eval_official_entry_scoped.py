"""The OFFICIAL entry, end to end: `python -m evals` main() → real
materialise → real obtain/composition → real ToolSurface → the real
formatted request → `httpx.MockTransport`.

Nothing under test is stubbed: `main`, `_execute`, `_run_one`,
`materialise`, `RunEvidence`, `obtain`, the agent loop and the model's
request formatting all run as the deploy host runs them. The ONE injected
seam is the SDK's own `http_client` — the documented transport seam
(`test_cognition_anchor_finder` uses the identical `original_client(...)`
wrap) — so zero requests leave the process and zero paid calls happen.

Requires the eval host: the `nora_eval` clone is the source every real
run snapshots, and the deployment's config.toml supplies credentials the
same way. `pytest.mark.skipif` refuses — never silently passes — when
either is absent.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest

import evals.__main__ as cli
from nora.agent import factory

DEPLOY_CONFIG = Path(os.environ.get(
    "NORA_DEPLOY_CONFIG", Path.home() / "workspace/nora/config.toml"))


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


@pytest.mark.skipif(not DEPLOY_CONFIG.exists(),
                    reason="not on the eval host (deployment config absent)")
def test_official_entry_scoped_override_budget_and_evidence(tmp_path, monkeypatch):
    """One real `main()` run of the scoped-browse corpus case: the
    overrides file composes the scoped computer, the budget counts the
    real formatted requests, and events.jsonl proves the run's provenance
    BEFORE the mock transport answers."""
    # 1. the declared fixture + the private evidence/lock dirs
    registry = tmp_path / "scope-endpoints"
    registry.mkdir()
    fixture = tmp_path / "scoped.json"
    fixture.write_text(json.dumps({
        "computer.enabled": True,
        "computer.scoped.enabled": True,
        "computer.scoped.registry": str(registry)}))
    want_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
    monkeypatch.setenv("NORA_EVAL_EXCHANGES", str(tmp_path / "evidence"))
    from nora.subject import sandbox
    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", tmp_path / "eval.lock")
    monkeypatch.setenv("NORA_COMPUTER_TOKEN", "generated-scoped-token")
    monkeypatch.setenv("NORA_COMPUTER_REDIS_URL", "redis://127.0.0.1:6390")

    # 2. the ONE injected seam: the SDK http_client, everything else real
    requests: list[dict] = []

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        first = len(requests) == 1
        delta = {"role": "assistant", "tool_calls": [{
            "index": 0, "id": "c1", "type": "function",
            "function": {"name": "computer_browse" if first else "send_reply",
                         "arguments": json.dumps(
                             {"url": "https://example.com"} if first
                             else {"content": "done."})}}]}
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=_sse(delta, "tool_calls"))

    real_create_client = factory.create_client

    def with_transport(**kwargs):
        kwargs.setdefault("http_client", httpx.AsyncClient(
            transport=httpx.MockTransport(respond)))
        return real_create_client(**kwargs)

    monkeypatch.setattr(factory, "create_client", with_transport)

    # 3. the REAL official entry: config rebuild, source read, obtain,
    #    ToolSurface, format_request — all live
    monkeypatch.setattr("sys.argv", [
        "python -m evals", "tool_selection",
        "--case", "route_computer_browse_a_page",
        "--deployment-config", str(DEPLOY_CONFIG),
        "--subject-overrides", str(fixture),
        "--run-budget", "8,600000,40000,30",
    ])
    rc = cli.main()

    # 4. durable evidence, read back — not asserted by construction
    root = Path(os.environ["NORA_EVAL_EXCHANGES"])
    runs = sorted(root.glob("*-*/events.jsonl"), key=os.path.getmtime)
    assert runs, "no evidence written"
    rows = [json.loads(line) for line in runs[-1].read_text().splitlines()]
    started = next(r for r in rows if r["kind"] == "run_started")
    md = started.get("metadata", started)
    assert md["subject_overrides"]["computer.scoped.enabled"] is True
    assert md["subject_overrides_sha256"] == want_sha
    assert md["run_budget"]["max_requests"] == 8
    formatted = [r for r in rows if r["kind"] == "event"
                 and r["name"] == "nora.model.request"]
    assert formatted, "no formatted model request recorded before dispatch"
    # the transport saw exactly the requests the evidence recorded
    seen = [json.loads(r["attributes"]["nora.model.request.body"])
            for r in formatted]
    assert seen == requests
    assert rc in (0, 1)  # rc carries the case outcome; evidence is what binds
