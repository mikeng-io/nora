"""`OpenAIImageGenerator`: GPT Image on the metered platform API, against a
local aiohttp server, no network and no spend.

What this lane adds over the shared GPT Image wire is only its credential:
a static key, no ChatGPT account header, no refresh chain, and a spent budget
read as `quota`, never as `down`.
"""

from __future__ import annotations

import base64

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.app.composition_media import _image_gen_wiring
from nora.domain.ports import ImageGenerator, ImageReference
from nora.infra.image_gen import build_image_generator
from nora.infra.image_gen.openai_api_adapter import OpenAIImageGenerator
from nora.obs.metrics import Metrics

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _image_response() -> web.Response:
    return web.json_response({
        "data": [{"b64_json": base64.b64encode(PNG).decode()}],
        "usage": {"input_tokens": 50, "output_tokens": 1056},
    })


@pytest.fixture
async def api_server():
    """A platform Images API: `state["reply"]` answers, every request recorded."""
    state: dict = {"requests": [], "reply": lambda req, body: _image_response()}

    async def images(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        state["requests"].append((request.path, dict(request.headers), body))
        return state["reply"](request, body)

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_post("/v1/images/generations", images)
    app.router.add_post("/v1/images/edits", images)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("/v1")), state
    await server.close()


def _gen(base: str, **kw) -> OpenAIImageGenerator:
    return OpenAIImageGenerator(api_key="sk-test", base_url=base, timeout_seconds=5.0, **kw)


async def test_a_generation_carries_the_key_and_no_chatgpt_headers(api_server, caplog):
    base, api = api_server
    caplog.set_level("INFO", logger="nora.image_gen.gpt_image")
    result = await _gen(base, standard_quality="low").generate(prompt="a red apple")
    assert (result.status, result.provider, result.raw) == ("ok", "openai", PNG)
    path, headers, body = api["requests"][0]
    assert path == "/v1/images/generations"
    assert headers["Authorization"] == "Bearer sk-test"
    assert "chatgpt-account-id" not in headers and "originator" not in headers
    assert body == {
        "model": "gpt-image-2.6-flare", "prompt": "a red apple",
        "quality": "low", "size": "auto", "output_format": "png",
    }
    assert "output_tokens=1056" in caplog.text


async def test_references_use_the_edits_endpoint(api_server):
    base, api = api_server

    async def resolver(code: str) -> bytes | None:
        return PNG if code == "a3f2c1" else None

    result = await _gen(base).generate(
        prompt="make it green", reference_images=[ImageReference("a3f2c1")],
        reference_resolver=resolver, quality="quality",
    )
    assert result.status == "ok"
    path, _, body = api["requests"][0]
    assert path == "/v1/images/edits"
    assert body["quality"] == "high"
    assert body["images"] == [
        {"image_url": "data:image/png;base64," + base64.b64encode(PNG).decode()},
    ]


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (429, {"error": {"code": "insufficient_quota", "type": "insufficient_quota"}}, "quota"),
        (429, {"error": {"code": "rate_limit_exceeded"}}, "rate_limited"),
        (401, {"error": {"code": "invalid_api_key"}}, "auth"),
        (400, {"error": {"code": "invalid_value"}}, "bad_request"),
        (500, {}, "down"),
    ],
)
async def test_failures_map_to_unavailable_reasons(api_server, status, body, reason):
    base, api = api_server
    api["reply"] = lambda req, b: web.json_response(body, status=status)
    result = await _gen(base).generate(prompt="a cat")
    assert (result.status, result.reason) == ("unavailable", reason)
    assert len(api["requests"]) == 1  # a static key is never retried


async def test_a_moderation_refusal_is_nothing_found(api_server):
    base, api = api_server
    api["reply"] = lambda req, b: web.json_response(
        {"error": {"code": "moderation_blocked"}}, status=400,
    )
    result = await _gen(base).generate(prompt="a cat")
    assert (result.status, result.reason) == ("nothing_found", "refused")


def test_the_factory_builds_the_api_lane_only_with_a_key():
    built = build_image_generator(provider="openai", api_key="sk", model="gpt-image-2.6-flare")
    assert isinstance(built, OpenAIImageGenerator) and isinstance(built, ImageGenerator)
    assert build_image_generator(provider="openai", api_key="") is None


def test_the_api_section_is_validated():
    from pydantic import ValidationError

    from nora.config import ImageGenConfig

    cfg = ImageGenConfig.model_validate({"provider": "openai", "openai": {"high_quality": "max"}})
    assert cfg.openai.model == "gpt-image-2.6-flare"
    assert cfg.openai.api_key_env == "OPENAI_IMAGE_API_KEY"
    for bad in ({"refresh_token_env": "X"}, {"standard_quality": "ultra"}, {"timeout_seconds": 0}):
        with pytest.raises(ValidationError):
            ImageGenConfig.model_validate({"openai": bad})


async def test_composition_selects_the_api_lane_by_its_key(example_config, tmp_path):
    image_gen = example_config.image_gen.model_copy(update={"enabled": True, "provider": "openai"})
    cfg = example_config.model_copy(update={"image_gen": image_gen})
    generator, keeper = _image_gen_wiring(
        cfg, Metrics(), {"OPENAI_IMAGE_API_KEY": "sk"}, state_dir=tmp_path,
    )
    assert isinstance(generator, OpenAIImageGenerator)
    assert keeper is None  # a key has no login to keep alive
    missing, _ = _image_gen_wiring(cfg, Metrics(), {}, state_dir=tmp_path)
    assert missing is None
