"""The run budget on the REAL gated dispatch path: `factory.create_client`
under `httpx.MockTransport`.

No paid calls: the transport is the mock. The proof is that an admitted
streamed call lands its terminal usage on the budget, and a refused call
raises BEFORE it reaches the transport. Evidence composition (`RunEvidence`
+ formatted-request records through `python -m evals` itself) is covered by
`tests/integration/test_eval_official_entry_scoped.py`, not here.
"""

from __future__ import annotations

import json

import httpx
import pytest

from nora.agent import factory
from nora.domain.run_budget import (
    RunBudget,
    RunBudgetError,
    active_run_budget,
)
from nora.subject.eval_evidence import RunEvidence


def _sse(chunks):
    data = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
    return data + "data: [DONE]\n\n"


def _respond(requests):
    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        chunks = [
            {"id": "g", "object": "chat.completion.chunk",
             "model": body["model"],
             "choices": [{"index": 0, "delta": {"role": "assistant",
                                              "content": "ok"},
                          "finish_reason": None}]},
            {"id": "g", "object": "chat.completion.chunk",
             "model": body["model"],
             "choices": [{"index": 0, "delta": {},
                          "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 11, "completion_tokens": 4,
                       "total_tokens": 15}},
        ]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=_sse(chunks))
    return respond


async def test_the_budget_counts_and_caps_the_real_dispatch_path():
    """One admitted streamed call through the REAL gated client counts
    request + terminal stream usage; a second call is refused BEFORE it
    reaches the transport (requests stays 1)."""
    requests: list[dict] = []
    budget = RunBudget(max_requests=1, max_input_bytes=10_000,
                       max_output_tokens=10_000, output_reserve=100)
    token = active_run_budget.set(budget)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                _respond(requests))) as transport:
            client = factory.create_client(
                base_url="https://generated.invalid/v1",
                api_key="generated-key", http_client=transport)
            stream = await client.chat.completions.create(
                model="generated-model", stream=True,
                stream_options={"include_usage": True},
                messages=[{"role": "user", "content": "hi"}])
            async for _ in stream:
                pass
            assert budget.requests == 1
            assert budget.input_tokens == 11 and budget.output_tokens == 4
            with pytest.raises(RunBudgetError):
                await client.chat.completions.create(
                    model="generated-model", stream=True,
                    stream_options={"include_usage": True},
                    messages=[{"role": "user", "content": "again"}])
    finally:
        active_run_budget.reset(token)
    assert len(requests) == 1          # the refused call never left


def test_the_override_file_is_schema_validated_before_composition():
    """`validate_subject_overrides` accepts the scoped fixture's real
    fields and refuses a truthy string, an unknown path, a section, and
    a raw credential — all before any case composes."""
    from nora.subject.obtain import validate_subject_overrides

    validate_subject_overrides({
        "computer.scoped.enabled": True,
        "computer.scoped.registry": "generated-registry"})
    with pytest.raises(ValueError, match="real bool"):
        validate_subject_overrides({"computer.scoped.enabled": "false"})
    with pytest.raises(ValueError, match="no config field"):
        validate_subject_overrides({"computer.scoped.nope": 1})
    with pytest.raises(ValueError, match="section"):
        validate_subject_overrides({"computer.scoped": {}})
    with pytest.raises(ValueError, match="credential"):
        validate_subject_overrides({"computer.token": "sk-raw"})
    # The real Config schema runs — field constraints and model
    # validators against the base the overrides land on, not just
    # path/type existence.
    import math
    from pathlib import Path

    from nora.config import load as load_config
    example = Path(__file__).parents[2] / "config.example.toml"
    base = load_config(example, require_secrets=False)
    for bad in (-1.0, math.nan):
        with pytest.raises(ValueError):
            validate_subject_overrides(
                {"computer.scoped.browser_sweep_seconds": bad},
                base=base, context_dir=example.parent)
    # A relational invariant: run_timeout_seconds must not exceed
    # max_run_timeout_seconds — individually-legal scalars that break
    # the model validator still refuse.
    with pytest.raises(ValueError):
        validate_subject_overrides(
            {"computer.run_timeout_seconds": 9_999_999},
            base=base, context_dir=example.parent)


def test_a_non_private_evidence_dir_is_refused_before_dispatch(
        tmp_path, monkeypatch):
    """`RunEvidence` opens its run dir before any case composes; a dir
    that is not owned+0700 is `EvidenceError` at construction — the
    refusal is the proof evidence is mandatory, not optional."""
    import os

    from nora.subject.eval_evidence import EvidenceError

    world = tmp_path / "world"
    world.mkdir()
    world.chmod(0o777)
    monkeypatch.setenv("NORA_EVAL_EXCHANGES", str(world))
    lock = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR)
    try:
        with pytest.raises(EvidenceError, match="private"):
            RunEvidence(run_id="generated-run", lock_fd=lock,
                        metadata={"corpus": "generated"})
    finally:
        os.close(lock)
