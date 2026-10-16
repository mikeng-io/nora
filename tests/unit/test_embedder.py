"""The embedder client and the one formatter: production formats a query, a document and an
image exactly as the bake-off adapter that was measured; a vector comes back
2048 wide and unit length; an unavailable server and a refused input are two
different errors; every image is decoded here, bounded, and sent as a PNG;
a text is cut to the server's micro-batch.

`FakeLlamaServer` behaves like llama-server b11541 where it matters: it
answers HTTP 500 (with /health still 200) for a prompt longer than its
micro-batch, an image prompt whose marker is not its own, and image bytes it
cannot decode; it picks a new marker when it "restarts"; and it refuses a
prompt that is not the model's turn. A client that gets any of these wrong
fails here as it would against the real server."""

from __future__ import annotations

import asyncio
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
    QUERY_INSTRUCTION,
    TAIL,
    head,
    image_prompt,
    text_prompt,
)
from nora.infra import embedder as embedder_module
from nora.infra.embedder import (
    DIMENSIONS,
    MAX_SEND_PIXELS,
    SAFE_TEXT_CHARS,
    SERVED_MODEL_TTL_SECONDS,
    TOKEN_BUDGET,
    EmbedderRejectedError,
    EmbedderUnavailableError,
    LlamaEmbedder,
    fingerprint,
    model_file,
)
from tests.evals.test_bakeoff_qwen_llama import MARKER, FakeServer, embedder

PNG = b"\x89PNG\r\n\x1a\n"
JPEG = b"\xff\xd8\xff"
MODEL_PATH = "/models/model-a.gguf"


def png_bytes(
    colour: tuple[int, int, int] = (200, 30, 30), size: tuple[int, int] = (8, 8),
) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="PNG")
    return out.getvalue()


def tokens_of(text: str) -> int:
    """The fake's tokenizer: a character outside ASCII is two tokens, the
    rate of rare Hong Kong characters on the real one (2.11)."""
    return sum(2 if ord(c) > 127 else 1 for c in text)


class FakeLlamaServer:
    """llama-server's /props, /health, /tokenize and /embedding (module
    docstring). `status` forces every /embedding answer's HTTP status,
    `healthy` /health's, `width` the vector's, `delay` how long /props and
    /embedding each take. Records every /embedding content it accepted, and
    counts /props calls."""

    def __init__(self, *, marker: str = MARKER, ubatch: int = 2048) -> None:
        self.marker = marker
        self.ubatch = ubatch
        self.model_path = MODEL_PATH
        self.status = 200
        self.healthy = True
        self.width = DIMENSIONS
        self.accepted: list[dict[str, Any]] = []
        self.calls = 0
        self.props_calls = 0
        self.delay = 0.0
        self.restarts = 0

    def restart(self) -> None:
        """A start without LLAMA_MEDIA_MARKER picks a new random marker."""
        self.restarts += 1
        self.marker = f"<__media_r{self.restarts}__>"

    def app(self) -> web.Application:
        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.router.add_get("/props", self._props)
        app.router.add_get("/health", self._health)
        app.router.add_post("/tokenize", self._tokenize)
        app.router.add_post("/embedding", self._embedding)
        return app

    async def _props(self, _request: web.Request) -> web.Response:
        self.props_calls += 1
        await asyncio.sleep(self.delay)
        return web.json_response({"media_marker": self.marker, "model_path": self.model_path})

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok" if self.healthy else "loading"},
                                 status=200 if self.healthy else 503)

    async def _tokenize(self, request: web.Request) -> web.Response:
        content = (await request.json())["content"]
        return web.json_response({"tokens": list(range(tokens_of(content)))})

    @staticmethod
    def _error(message: str, status: int) -> web.Response:
        return web.json_response({"error": {"message": message}}, status=status)

    async def _embedding(self, request: web.Request) -> web.Response:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.status != 200:
            return self._error("forced", self.status)
        content = (await request.json()).get("content")
        if not isinstance(content, dict) or not isinstance(content.get("prompt_string"), str):
            return self._error("no prompt", 400)
        prompt = content["prompt_string"]
        images = content.get("multimodal_data") or []
        if not prompt.startswith("<|im_start|>system\n") or not prompt.endswith(TAIL) \
                or EOT in prompt:
            return self._error("not the model's turn", 400)
        if prompt.count(self.marker) != len(images) or len(images) > 1:
            # mtmd's tokenize fails: the server reports it as its own error.
            return self._error("failed to tokenize prompt", 500)
        if tokens_of(prompt) > self.ubatch:
            return self._error("input is too large to process", 500)
        seed = prompt.encode()
        for item in images:
            data = base64.b64decode(item)
            if not (data.startswith(PNG) or data.startswith(JPEG)):
                return self._error("failed to load image", 500)
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


def _decoded(sent: dict[str, Any]) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(sent["multimodal_data"][0])))


async def test_the_bake_off_adapter_and_production_format_identically(llama, tmp_path):
    """One formatter: the strings production sends are the ones the bake-off
    adapter, measured token by token, sends for the same inputs, and the
    image is the same picture (production re-encodes it)."""
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
    await client.embed_image(img.read_bytes())

    assert [c["prompt_string"] for c in fake.accepted] == [
        c["prompt_string"] for c in bake.embedded
    ]
    with _decoded(fake.accepted[2]) as ours, _decoded(bake.embedded[2]) as theirs:
        assert ours.convert("RGB").tobytes() == theirs.convert("RGB").tobytes()
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
    vec = (await LlamaEmbedder(url).embed_document("the lantern festival plan")).vector
    assert len(vec) == DIMENSIONS
    assert math.isclose(math.fsum(x * x for x in vec), 1.0, rel_tol=1e-9)


@pytest.mark.parametrize("text", [
    "龘" * 3500,                     # rare characters: two tokens each
    "今晚去邊度食飯，" * 600,        # ordinary Cantonese past the safe length
    "x" * 9000,
], ids=["rare-hk", "cantonese", "ascii"])
async def test_a_long_text_is_cut_to_fit_the_micro_batch(llama, text):
    """The server refuses a prompt longer than `-ub` with a 500; the client
    counts with /tokenize and cuts, so the text is embedded, from its head."""
    fake, url = llama
    embedded = await LlamaEmbedder(url).embed_document(text)
    sent = fake.accepted[0]["prompt_string"]
    assert tokens_of(sent) <= TOKEN_BUDGET
    assert text[:SAFE_TEXT_CHARS] in sent
    assert embedded.truncated  # the worker counts it: the trigger for chunking


@pytest.mark.parametrize("text", [
    "the lantern festival plan",     # under the safe length: never counted
    "x" * (SAFE_TEXT_CHARS + 500),   # counted with /tokenize, and it fits
], ids=["short", "long-but-fits"])
async def test_a_text_that_fits_is_not_truncated(llama, text):
    fake, url = llama
    embedded = await LlamaEmbedder(url).embed_document(text)
    assert not embedded.truncated
    assert text in fake.accepted[0]["prompt_string"]


async def test_a_query_is_refused_while_the_server_serves_another_model(llama):
    """A question is never embedded by a model other than the one the
    stored vectors came from; the search falls back on this error."""
    fake, url = llama
    now = [0.0]
    client = LlamaEmbedder(url, query_model_file=Path(MODEL_PATH).name, clock=lambda: now[0])
    assert len(await client.embed_query("where did we eat")) == DIMENSIONS
    assert QUERY_INSTRUCTION in fake.accepted[-1]["prompt_string"]
    fake.model_path = "/models/model-b.gguf"
    now[0] += SERVED_MODEL_TTL_SECONDS  # the last /props match has expired
    calls = fake.calls
    with pytest.raises(EmbedderUnavailableError):
        await client.embed_query("where did we eat")
    assert fake.calls == calls  # refused before any embedding


async def test_a_query_reads_props_once_a_minute_not_once_a_search(llama):
    """/props is not called on every search, which would double a turn's calls."""
    fake, url = llama
    now = [0.0]
    client = LlamaEmbedder(url, query_model_file=Path(MODEL_PATH).name, clock=lambda: now[0])
    for _ in range(3):
        await client.embed_query("where did we eat")
        now[0] += 10
    assert (fake.props_calls, fake.calls) == (1, 3)
    now[0] += SERVED_MODEL_TTL_SECONDS
    await client.embed_query("where did we eat")
    assert fake.props_calls == 2


async def test_one_deadline_covers_every_call_of_a_query(llama):
    """/props and /embedding share one deadline: separate timeouts would let a
    slow server hold a turn for the sum. Each call here is under the per-call
    timeout; together they are past the query's deadline."""
    fake, url = llama
    fake.delay = 0.3
    client = LlamaEmbedder(
        url, timeout_seconds=1.0, query_model_file=Path(MODEL_PATH).name,
        query_timeout_seconds=0.45,
    )
    with pytest.raises(EmbedderUnavailableError):
        await client.embed_query("where did we eat")
    fake.delay = 0.1
    assert len(await client.embed_query("where did we eat")) == DIMENSIONS


async def test_every_image_is_decoded_and_sent_as_a_bounded_png(llama):
    fake, url = llama
    client = LlamaEmbedder(url)
    webp, gif, jpeg = io.BytesIO(), io.BytesIO(), io.BytesIO()
    Image.new("RGBA", (8, 8), (0, 200, 0, 128)).save(webp, format="WEBP")
    frames = [Image.new("RGB", (8, 8), c) for c in ((255, 0, 0), (0, 0, 255))]
    frames[0].save(gif, format="GIF", save_all=True, append_images=frames[1:])
    Image.new("RGB", (3000, 2000), (10, 10, 10)).save(jpeg, format="JPEG")

    for data in (webp.getvalue(), gif.getvalue(), jpeg.getvalue()):
        await client.embed_image(data)

    sent = [base64.b64decode(c["multimodal_data"][0]) for c in fake.accepted]
    assert all(s.startswith(PNG) for s in sent), "JPEG too: every image goes through Pillow"
    with Image.open(io.BytesIO(sent[1])) as first:
        assert first.convert("RGB").getpixel((0, 0)) == (255, 0, 0)
    with Image.open(io.BytesIO(sent[2])) as big:
        assert big.width * big.height <= MAX_SEND_PIXELS
        assert abs(big.width / big.height - 1.5) < 0.01


@pytest.mark.parametrize("data", [
    b"not an image",
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00garbage",
    # A PNG header claiming 100000 x 100000 pixels: refused before decoding.
    PNG + b"\x00\x00\x00\rIHDR" + (100000).to_bytes(4, "big") * 2 + b"\x08\x02\x00\x00\x00"
    + b"\x00\x00\x00\x00",
], ids=["junk", "truncated-gif", "bomb-header"])
async def test_an_undecodable_image_is_rejected_before_any_call(llama, data):
    fake, url = llama
    with pytest.raises(EmbedderRejectedError):
        await LlamaEmbedder(url).embed_image(data)
    assert fake.calls == 0


async def test_any_exception_pillow_raises_is_a_rejection(llama, monkeypatch):
    """`DecompressionBombError` subclasses Exception directly; Pillow raises
    `SyntaxError` and `struct.error` on malformed files too. Every one is
    this input's rejection, never an escape."""
    from PIL import Image as PILImage

    fake, url = llama
    for raised in (PILImage.DecompressionBombError("bomb"), SyntaxError("bad"), KeyError("x")):
        def _open(*_a: Any, raised: BaseException = raised, **_k: Any) -> Any:
            raise raised

        monkeypatch.setattr(PILImage, "open", _open)
        with pytest.raises(EmbedderRejectedError):
            await LlamaEmbedder(url).embed_image(png_bytes())
    assert fake.calls == 0


async def test_an_image_still_embeds_after_the_server_restarts_with_a_new_marker(llama):
    """A server that picks its marker per start answers 500 to the old one;
    the client reads /props again and retries once."""
    fake, url = llama
    client = LlamaEmbedder(url)
    await client.embed_image(png_bytes())
    fake.restart()
    await client.embed_image(png_bytes((1, 2, 3)))
    assert fake.marker in fake.accepted[-1]["prompt_string"]
    assert len(fake.accepted) == 2


async def test_a_5xx_is_the_inputs_fault_while_the_server_is_healthy(llama):
    fake, url = llama
    client = LlamaEmbedder(url)
    fake.status = 500
    with pytest.raises(EmbedderRejectedError):
        await client.embed_document("the lantern festival plan")
    fake.healthy = False
    with pytest.raises(EmbedderUnavailableError):
        await client.embed_document("the lantern festival plan")


async def test_a_refused_input_and_a_down_server_are_different_errors(llama):
    fake, url = llama
    client = LlamaEmbedder(url)
    fake.status = 400
    with pytest.raises(EmbedderRejectedError):
        await client.embed_document("the lantern festival plan")
    fake.status, fake.healthy = 503, False
    with pytest.raises(EmbedderUnavailableError):
        await client.embed_document("the lantern festival plan")
    fake.status, fake.healthy = 200, True
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


async def test_the_served_model_is_the_file_name_the_server_loaded(llama):
    _fake, url = llama
    assert await LlamaEmbedder(url).served_model() == "model-a.gguf"


def test_the_fingerprint_carries_the_format_version_and_names_the_file():
    assert fingerprint("model-a.gguf@abc") == f"model-a.gguf@abc+fmt{FORMAT_VERSION}"
    assert model_file("model-a.gguf@abc") == "model-a.gguf"


def test_the_safe_length_cannot_overflow_at_the_worst_measured_rate():
    """880 characters at 2.11 tokens each, plus the prompt's own turns."""
    worst = math.ceil(SAFE_TEXT_CHARS * 2.11) + len(text_prompt("", "document"))
    assert worst <= TOKEN_BUDGET
    assert embedder_module.UBATCH == 2048


def test_the_bake_off_module_uses_the_one_formatter():
    """No second copy of the instructions in the bake-off adapter."""
    source = Path(__file__).resolve().parents[2] / "evals/retrieval_bakeoff/qwen_llama.py"
    text = source.read_text(encoding="utf-8")
    assert "Represent the user's input." not in text
    assert "from nora.domain.embedder_input import" in text


def test_the_embedded_kinds_are_every_kind_but_the_hubs_and_the_episodes():
    """Hubs and the Rollup episodes are lexical only; every other kind
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


def test_the_embedder_service_runs_the_bake_offs_flags_with_a_pinned_marker():
    """The flags the bake-off measured (memory, and the image
    token cap the server ties to -ub), and a marker that survives a restart."""
    import yaml

    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8"),
    )
    service = compose["services"]["embedder"]
    argv = [str(a) for a in service["command"]]
    flags = {argv[i]: argv[i + 1] for i in range(len(argv) - 1) if argv[i].startswith("-")}
    bake_off = {"-c": "2048", "-b": "2048", "-ub": "2048", "-t": "4", "-ngl": "0",
                "--parallel": "1", "--pooling": "last"}
    assert {k: flags.get(k) for k in bake_off} == bake_off
    assert "--embedding" in argv
    assert str(embedder_module.UBATCH) == flags["-ub"]
    assert "LLAMA_MEDIA_MARKER=<__media__>" in service["environment"]
    assert compose["networks"]["embedder"]["internal"] is True
