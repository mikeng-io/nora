"""Shared eval-observability fixtures: the evidence root, row readers, the
capture builder and the synthetic model round trip."""

from __future__ import annotations

import json

import httpx
import pytest
from strands import tool

import evals.backbone as backbone
from nora.agent import factory
from nora.subject.eval_evidence import RunEvidence


@pytest.fixture
def evidence_root(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    monkeypatch.setenv("NORA_EVAL_EXCHANGES", str(root))
    from nora.subject import sandbox

    monkeypatch.setattr(sandbox, "EVAL_HOST_LOCK", tmp_path / "eval.lock")
    return root


def rows(evidence):
    return [json.loads(line) for line in (evidence.path / "events.jsonl").read_text().splitlines()]


def capture(lock_fd):
    backbone._prefill_capture()
    return RunEvidence(run_id="generated-run", metadata={"corpus": "generated"}, lock_fd=lock_fd)


async def model_round_trip(requests, *, sentinel="", rounds=2, reasoning_prefill=False,
                           judge=False, stream_chunks=0):
    @tool
    def next_number(value: int) -> str:
        """Return the next integer.

        Args:
            value: Integer to increment.
        """
        from nora.obs.tracing import record_eval_event
        record_eval_event("nora.proposal.sources", {"body": json.dumps({
            "1": {"text": "generated source"}})})
        return str(value + 1)

    async def respond(request):
        body = json.loads(request.content)
        from nora.subject.eval_evidence import current

        if evidence := current():
            assert any(r["kind"] == "event" and r["name"] == "nora.model.request"
                       and json.loads(r["attributes"]["nora.model.request.body"]) == body
                       for r in rows(evidence))
        requests.append(body)
        assert body["model"] == "generated-model"
        if not judge:
            assert body["tools"][0]["function"]["name"] == "next_number"
        if len(requests) < rounds:
            delta = {"role": "assistant", "reasoning": sentinel, "tool_calls": [
                     {"index": 0, "id": f"call_generated_{len(requests)}",
                     "type": "function", "function": {"name": "next_number",
                                                        "arguments": '{"value":5}'}}]}
            stop = "tool_calls"
        else:
            assert len(requests) == rounds
            if not judge:
                assert any(m.get("role") == "tool" and m.get("content") == "6"
                           for m in body["messages"])
            delta = {"role": "assistant", "content": "6 " + sentinel, "reasoning": sentinel}
            stop = "stop"
        chunks = [
            {"id": "generated", "object": "chat.completion.chunk", "model": "generated-model",
             "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"id": "generated", "object": "chat.completion.chunk", "model": "generated-model",
             "choices": [{"index": 0, "delta": {}, "finish_reason": stop}],
             "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15,
                       "prompt_tokens_details": {"cached_tokens": 4},
                       "completion_tokens_details": {"reasoning_tokens": 2}}},
        ]
        if stream_chunks and stop == "stop":
            chunks = [{"id": "generated", "object": "chat.completion.chunk",
                       "model": "generated-model", "choices": [{"index": 0,
                       "delta": {"content": f"piece-{i} "}, "finish_reason": None}]}
                      for i in range(stream_chunks)] + chunks
        data = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream",
                                            "x-omniroute-model": "generated-served",
                                            "x-omniroute-provider": "generated-provider"},
                              content=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        client = factory.create_client(base_url="https://generated.invalid/v1",
                                       api_key="generated-private-auth-value",
                                       http_client=transport)
        model = factory.create_model_provider(model="generated-model", max_tokens=100,
                                              temperature=0, client=client, tool_choice="auto")
        agent = factory.create_browse_agent(
            model=model, system_prompt=(
                "Judge the generated answer; return a score and reason." if judge
                else "Increment the supplied integer."),
            tools=[] if judge else [next_number],
            trace_attributes={"gen_ai.conversation.id": "generated"},
            retry=factory.build_retry_policy(timeout_seconds=5))
        if reasoning_prefill:
            from nora.agent.reasoning_strip import ReasoningStripWatch
            from nora.obs.metrics import Metrics
            agent.messages.append({"role": "assistant", "content": [
                {"reasoningContent": {"reasoningText": {"text": "Generated carried reasoning"}}},
                {"text": "Generated previous answer"},
            ]})
            agent.hooks.add_hook(ReasoningStripWatch(path="tag", metrics=Metrics()))
        result = await factory.invoke(
            agent, "5 " + sentinel, invocation_state={}, limits=factory.build_limits(
            turns=4, output_tokens=200, total_tokens=1000))
        await client.close()
        assert "6" in str(result)


