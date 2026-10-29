"""Generated-only confirmation and stdout/stderr custody checks."""
import json
import logging
import sys

import httpx
import pytest

from scripts import confirm_cognition_anchors as confirm


@pytest.mark.parametrize(("content", "finish", "expected"), [
    ('{"match":true}', "stop", "match"),
    ('{"match":false}', "stop", "no_match"),
    ('{"match":"false"}', "stop", "unavailable"),
    ('{"match":true,"reason":"generated secret"}', "stop", "unavailable"),
    ('{"match":true}', "length", "unavailable"),
    ('[]', "stop", "unavailable"),
    ('{"ok":true}', "stop", "unavailable"),
])
async def test_boolean_model_contract(content, finish, expected, monkeypatch):
    monkeypatch.setenv("GENERATED_MODEL_KEY", "generated-key")

    def serve(request):
        payload = json.loads(request.content)
        if "response_format" in payload:  # what the deployment's route answers
            return httpx.Response(503, json={"error": {"code": "ALL_TARGETS_SKIPPED"}})
        assert payload["model"] == "generated-production-model"
        assert '{"match": true}' in payload["messages"][0]["content"]
        assert '{"match": false}' in payload["messages"][0]["content"]
        assert "Must not:" in payload["messages"][0]["content"]
        moment = json.loads(payload["messages"][1]["content"])
        assert moment["messages"][0] == {"at_ms": 1000, "speaker": "speaker_1", "text": "k"}
        return httpx.Response(200, json={"choices": [{"finish_reason": finish,
                                                    "message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        result = await confirm.judge(client, {
            "base_url": "https://generated.invalid/v1", "api_key_env": "GENERATED_MODEL_KEY",
            "model": "generated-production-model", "max_tokens": 2000, "temperature": 0.7,
        }, confirm.scenarios()[2], {"messages": [
            {"at_ms": 1000, "speaker": "speaker_1", "text": "k"}]})
    assert result == expected


@pytest.mark.parametrize("fails", [False, True])
def test_private_processing_never_prints_text(tmp_path, monkeypatch, capsys, fails):
    inputs = tmp_path / "candidates.jsonl"
    inputs.write_text(json.dumps({"room": "room_generated", "before_ms": 1001,
                                 "scenario": 2, "counts": {"prior": 0}}))
    monkeypatch.setattr(sys, "argv", ["confirm", "--source-url", "generated",
                                     "--config", "generated", "--candidates", str(inputs)])
    monkeypatch.setattr(confirm, "room_aliases", lambda: {"generated-real": "room_generated"})

    async def private_processing(source_url, config, rows, aliases):
        print("GENERATED PRIVATE MESSAGE")
        print("GENERATED PRIVATE MESSAGE", file=sys.stderr)
        logging.getLogger("generated").critical("GENERATED PRIVATE MESSAGE")
        if fails:
            raise RuntimeError("GENERATED PRIVATE MESSAGE")
        return [{**rows[0], "result": "match"}]

    monkeypatch.setattr(confirm, "confirm", private_processing)
    previous = logging.root.manager.disable
    assert confirm.main() == (2 if fails else 0)
    assert logging.root.manager.disable == previous
    output = capsys.readouterr()
    assert "GENERATED PRIVATE MESSAGE" not in output.out + output.err
    if fails:
        assert output.err.startswith("anchor confirmation unavailable;")
        assert not output.out
    else:
        assert json.loads(output.out) == {"room": "room_generated", "before_ms": 1001,
                                         "scenario": 2, "result": "match"}
        assert not output.err


@pytest.mark.parametrize("value", [
    {"room": "generated-real", "before_ms": 1001, "scenario": 2},
    {"room": "room_other", "before_ms": 1001, "scenario": 2},
    {"room": "room_generated", "before_ms": True, "scenario": 2},
    {"room": "room_generated", "before_ms": 1001, "scenario": 7},
])
def test_output_projection_refuses_unmapped_or_invalid_candidates(value):
    with pytest.raises(ValueError):
        confirm.candidate(value, {"generated-real": "room_generated"})
