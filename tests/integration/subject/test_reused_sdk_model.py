"""Repeated SDK-owned clients retain their real per-request resource lifetime."""

import json

from nora.agent.structured import ask_text, build_structured_model
from nora.domain.run_budget import active_run_budget
from nora.subject.eval_evidence import read_events
from nora.subject.recorded_services import RecordedServices
from tests.integration.subject.test_recorded_wake_runtime import completion
from tests.integration.subject.test_sdk_recorded_caller import sdk_evidence as sdk_evidence


async def test_reused_model_opens_a_fresh_bounded_sdk_client_for_each_invocation(
    sdk_evidence, monkeypatch,
):
    expected = ("Generated first result.", "Generated second result.")
    recorded = RecordedServices({"version": 1, "bindings": {"llm.base_url": "model"},
        "services": {"model": [completion("generated-model", text, stream=True)
                               for text in expected]}})
    budget = active_run_budget.get()
    before = budget.requests
    with sdk_evidence.case("generated-reused-sdk", input={}, metadata={"behavior": "UNMEASURED"}):
        async with recorded.open("sqlite://") as environment:
            model = build_structured_model(base_url=environment.urls["llm.base_url"] + "/v1",
                api_key="generated-key", model_id="generated-model", max_tokens=200,
                temperature=0)
            pools = []
            resolve = model._resolve_client_args

            def clients():
                args = resolve()
                pools.append(args["http_client"])
                return args

            monkeypatch.setattr(model, "_resolve_client_args", clients)
            answers = []
            for prompt in ("Generated first input.", "Generated second input."):
                result = await ask_text(model=model, prompt=prompt, timeout_seconds=5,
                                        system_prompt="Generated repeated invocation control.")
                assert result.finish_reason == "end_turn"
                answers.append(result.content)
            assert answers == ["Generated first result.", "Generated second result."]
            assert len(pools) == len({id(pool) for pool in pools}) == 2
            assert all(pool.is_closed for pool in pools)
            assert budget.requests - before == 2
            assert not environment.denied
    events = read_events(sdk_evidence.path)
    requests = [json.loads(e["attributes"]["nora.model.request.body"])
                for e in events if e.get("name") == "nora.model.request"]
    responses = [e for e in events if e["kind"] == "transport_response"]
    terminals = [event for event in responses
                 if event["response"]["choices"][0].get("finish_reason") == "stop"]
    assert len(requests) == len(terminals) == 2
    assert [request["messages"][-1]["content"] for request in requests] == [
        [{"text": "Generated first input.", "type": "text"}],
        [{"text": "Generated second input.", "type": "text"}]]
    assert len({event["invocation_id"] for event in responses}) == 2
