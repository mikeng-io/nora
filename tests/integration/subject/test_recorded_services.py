import asyncio
import json

import aiohttp
import pytest

from nora.subject.eval_evidence import EvidenceError
from nora.subject.recorded_services import RecordedServices


def fixture(tmp_path, rows):
    path = tmp_path / "recording.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "bindings": {"honcho.base_url": "one", "llm.base_url": "one"},
                "services": {"one": rows},
            }
        )
    )
    return RecordedServices.load(path)


def row(method="POST", path="/v1?q=1", **response):
    return {
        "method": method,
        "path": path,
        "request_json": {"id": "generated"},
        "response": response,
    }


@pytest.mark.asyncio
async def test_json_sse_shared_and_planned_404(tmp_path):
    recorded = fixture(
        tmp_path,
        [
            row(status=200, json={"id": "generated"}),
            row(status=404),
            row(status=200, chunks=[{"delta": "Generated"}], headers={"x-model": "generated"}),
        ],
    )
    async with recorded.open("sqlite://") as env:
        assert env.urls["honcho.base_url"] == env.urls["llm.base_url"]
        async with aiohttp.ClientSession() as client:
            url = env.urls["llm.base_url"] + "/v1?q=1"
            async with client.post(url, json={"id": "generated"}) as result:
                assert await result.json() == {"id": "generated"}
            async with client.post(url, json={"id": "generated"}) as result:
                assert result.status == 404 and await result.read() == b""
            async with client.post(url, json={"id": "generated"}) as result:
                assert result.headers["x-model"] == "generated"
                assert await result.text() == 'data: {"delta":"Generated"}\n\ndata: [DONE]\n\n'
        env.check()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/wrong", {"id": "generated"}),
        ("GET", "/v1?q=1", {"id": "generated"}),
        ("POST", "/v1?q=1", {"id": "wrong"}),
    ],
)
async def test_unmatched_does_not_consume(tmp_path, method, path, body):
    recorded = fixture(tmp_path, [row(status=200, json={"ok": True})])
    async with recorded.open("sqlite://") as env, aiohttp.ClientSession() as client:
        async with client.request(method, env.urls["llm.base_url"] + path, json=body) as result:
            assert result.status == 500
        with pytest.raises(EvidenceError, match="external_response_not_recorded"):
            env.check()
        async with client.post(
            env.urls["llm.base_url"] + "/v1?q=1", json={"id": "generated"}
        ) as result:
            assert result.status == 200
        async with client.post(
            env.urls["llm.base_url"] + "/v1?q=1", json={"id": "generated"}
        ) as result:
            assert result.status == 500


@pytest.mark.asyncio
async def test_missing_response_and_exception_cleanup(tmp_path):
    recorded = fixture(tmp_path, [row(status=200)])
    with pytest.raises(EvidenceError, match="external_response_not_consumed"):
        async with recorded.open("sqlite://") as env:
            assert env.guarded
    assert not env.active
    for exception in (RuntimeError("original"), asyncio.CancelledError()):
        with pytest.raises(type(exception)):
            async with recorded.open("sqlite://") as env:
                raise exception
        assert not env.active


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.update(version=True),
        lambda x: x.update(extra=1),
        lambda x: x["bindings"].update({"llm.base_url": "missing"}),
        lambda x: x["services"]["one"][0]["response"].update(status=True),
        lambda x: x["services"]["one"][0]["response"].update(json={}, chunks=[]),
        lambda x: x["services"]["one"][0].update(path="//bad"),
    ],
)
def test_reject_invalid_schema(tmp_path, change):
    data = {
        "version": 1,
        "bindings": {"llm.base_url": "one"},
        "services": {"one": [row(status=200)]},
    }
    change(data)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        RecordedServices.load(path)
