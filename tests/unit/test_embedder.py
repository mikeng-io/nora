"""The embedder client and the one formatter (design/unified-memory/
65-build-step-3-vectors.md V1): production formats a query, a document and an
image exactly as the bake-off adapter that was measured; a vector comes back
2048 wide and unit length; an unavailable server and a refused input are two
different errors; a sticker is sent as a PNG llama-server can decode.

`FakeLlamaServer` behaves like llama-server where it matters: it refuses a
prompt that is not the model's turn, an image prompt without exactly one of
its own markers, and image bytes that are neither PNG nor JPEG, so a client
that formats wrongly fails here as it would against the real server."""

from __future__ import annotations

import base64
import hashlib
import io
import math
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image

from nora.domain.embedder_input import (
    EOT,
    FORMAT_VERSION,
    TAIL,
    head,
    image_prompt,
    text_prompt,
)
from nora.infra.embedder import (
    DIMENSIONS,
    MAX_TEXT_CHARS,
    EmbedderRejectedError,
    EmbedderUnavailableError,
    LlamaEmbedder,
    fingerprint,
)
from tests.evals.test_bakeoff_qwen_llama import MARKER, FakeServer, embedder

PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"


def png_bytes(colour: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), colour).save(out, format="PNG")
    return out.getvalue()


class FakeLlamaServer:
    """llama-server's /props, /health and /embedding (see module docstring).
    `status` forces every /embedding answer's HTTP status; `width` the
    vector's. Records every /embedding content it accepted."""

    def __init__(self, *, marker: str = MARKER) -> None:
        self.marker = marker
        self.status = 200
        self.width = DIMENSIONS
        self.accepted: list[dict[str, Any]] = []
        self.calls = 0

    def app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_get("/props", self._props)
        app.router.add_get("/health", lambda _r: web.json_response({"status": "ok"}))
        app.router.add_post("/embedding", self._embedding)
        return app

    async def _props(self, _request: web.Request) -> web.Response:
        return web.json_response({"media_marker": self.marker})

    async def _embedding(self, request: web.Request) -> web.Response:
        self.calls += 1
        if self.status != 200:
            return web.json_response({"error": "forced"}, status=self.status)
        content = (await request.json()).get("content")
        if not isinstance(content, dict) or not isinstance(content.get("prompt_string"), str):
            return web.json_response({"error": "no prompt"}, status=400)
        prompt = content["prompt_string"]
        images = content.get("multimodal_data") or []
        if not prompt.startswith("<|im_start|>system\n") or not prompt.endswith(TAIL) \
                or EOT in prompt:
            return web.json_response({"error": "not the model's turn"}, status=400)
        if prompt.count(self.marker) != len(images) or len(images) > 1:
            return web.json_response({"error": "markers and images disagree"}, status=400)
        seed = prompt.encode()
        for item in images:
            data = base64.b64decode(item)
            if not (data.startswith(PNG) or data.startswith(JPEG)):
                return web.json_response({"error": "failed to load image"}, status=400)
            seed += data
        self.accepted.append(content)
        digest = hashlib.sha256(seed).digest()
        # Not unit length on purpose: the client normalises.
        vec = [3.0 * ((digest[i % 32] / 255.0) - 0.5) + 0.01 for i in range(self.width)]
        return web.json_response([{"index": 0, "embedding": [vec]}])


@pytest.fixture
async def llama() -> AsyncIterator[tuple[FakeLlamaServer, str]]:
    fake = FakeLlamaServer()
    server = TestServer(fake.app())
    await server.start_server()
    try:
        yield fake, str(server.make_url("")).rstrip("/")
    finally:
        await server.close()


async def test_the_bake_off_adapter_and_production_format_identically(llama, tmp_path):
    """One formatter: the strings production sends are the ones the bake-off
    adapter, measured token by token, sends for the same inputs."""
    fake, url = llama
    client = LlamaEmbedder(url)
    bake = FakeServer()
    adapter = embedder(bake)
    text = "two bowls of noodles, café at noon"
    img = tmp_path / "a.png"
    img.write_bytes(png_bytes())

    adapter.embed_text([text], "query")
    adapter.embed_text([text], "document")
    adapter.embed_media([img])
    await client.embed_query(text)
    await client.embed_document(text)
    await client.embed_image(img.read_bytes(), "image/png")

    assert [c["prompt_string"] for c in fake.accepted] == [
        c["prompt_string"] for c in bake.embedded
    ]
    assert fake.accepted[2]["multimodal_data"] == bake.embedded[2]["multimodal_data"]
    # And the strings themselves, so a change to the one formatter is seen.
    assert fake.accepted[0]["prompt_string"] == (
        "<|im_start|>system\nRetrieve images or text relevant to the user's query."
        "<|im_end|>\n<|im_start|>user\n" + text + "<|im_end|>\n<|im_start|>assistant\n"
    )
    assert fake.accepted[1]["prompt_string"] == text_prompt(text, "document")
    assert "Represent the user's input." in fake.accepted[1]["prompt_string"]
    assert fake.accepted[2]["prompt_string"] == head("image") + MARKER + TAIL
    assert fake.accepted[2]["prompt_string"] == image_prompt(MARKER)


async def test_a_vector_is_2048_wide_and_unit_length(llama):
    _fake, url = llama
    vec = await LlamaEmbedder(url).embed_document("the lantern festival plan")
    assert len(vec) == DIMENSIONS
    assert math.isclose(math.fsum(x * x for x in vec), 1.0, rel_tol=1e-9)


async def test_a_long_text_is_cut_to_the_context(llama):
    fake, url = llama
    await LlamaEmbedder(url).embed_document("x" * (MAX_TEXT_CHARS + 500))
    assert fake.accepted[0]["prompt_string"].count("x") == MAX_TEXT_CHARS


async def test_a_sticker_and_a_gif_go_as_the_png_of_their_first_frame(llama):
    fake, url = llama
    client = LlamaEmbedder(url)
    webp, gif = io.BytesIO(), io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 200, 0, 128)).save(webp, format="WEBP")
    frames = [Image.new("RGB", (8, 8), c) for c in ((255, 0, 0), (0, 0, 255))]
    frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:])

    await client.embed_image(webp.getvalue(), "image/webp")
    await client.embed_image(gif.getvalue(), "image/gif")

    sent = [base64.b64decode(c["multimodal_data"][0]) for c in fake.accepted]
    assert all(s.startswith(PNG) for s in sent)
    with Image.open(io.BytesIO(sent[1])) as first:
        assert first.convert("RGB").getpixel((0, 0)) == (255, 0, 0)


async def test_undecodable_image_bytes_are_rejected_before_any_call(llama):
    fake, url = llama
    with pytest.raises(EmbedderRejectedError):
        await LlamaEmbedder(url).embed_image(b"not an image", "image/webp")
    assert fake.calls == 0


async def test_a_refused_input_and_a_down_server_are_different_errors(llama):
    fake, url = llama
    client = LlamaEmbedder(url)
    fake.status = 400
    with pytest.raises(EmbedderRejectedError):
        await client.embed_document("the lantern festival plan")
    fake.status = 503
    with pytest.raises(EmbedderUnavailableError):
        await client.embed_document("the lantern festival plan")
    fake.status = 200
    fake.width = 1024
    with pytest.raises(EmbedderUnavailableError, match="1024"):
        await client.embed_document("the lantern festival plan")


async def test_an_unreachable_server_is_unavailable():
    with pytest.raises(EmbedderUnavailableError):
        await LlamaEmbedder("http://127.0.0.1:9", timeout_seconds=2).embed_document("x")


async def test_an_error_never_carries_the_input(llama):
    fake, url = llama
    fake.status = 400
    with pytest.raises(EmbedderRejectedError) as caught:
        await LlamaEmbedder(url).embed_document("a private sentence")
    assert "private" not in str(caught.value)


def test_the_fingerprint_carries_the_format_version():
    assert fingerprint("model@abc") == f"model@abc+fmt{FORMAT_VERSION}"


def test_the_bake_off_module_uses_the_one_formatter():
    """No second copy of the instructions in the bake-off adapter."""
    source = Path(__file__).resolve().parents[2] / "evals/retrieval_bakeoff/qwen_llama.py"
    text = source.read_text(encoding="utf-8")
    assert "Represent the user's input." not in text
    assert "from nora.domain.embedder_input import" in text


def test_the_embedded_kinds_are_every_kind_but_the_hubs_and_the_episodes():
    """25 §7: hubs and the Rollup episodes are lexical only; every other kind
    gets a vector, so a new kind is a decision here, not a silent default."""
    from nora.domain.memory_index import EMBEDDED_KINDS, EPISODE_KINDS, HUB_KINDS, NODE_KINDS

    assert set(EMBEDDED_KINDS) == set(NODE_KINDS) - set(HUB_KINDS) - set(EPISODE_KINDS)


@pytest.mark.parametrize(("media_type", "from_bytes"), [
    ("image/jpeg", True), ("image/webp", True), ("image/gif", True), ("IMAGE/PNG", True),
    ("video/mp4", False), ("audio/ogg", False), ("application/pdf", False), ("", False),
])
def test_an_image_a_sticker_or_a_gif_is_embedded_from_its_bytes(media_type, from_bytes):
    from nora.domain.memory_index import embeds_bytes

    assert embeds_bytes(media_type) is from_bytes
