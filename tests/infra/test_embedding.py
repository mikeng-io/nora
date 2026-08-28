"""Pass 4's one outbound call, and R-27 on it.

The endpoint is a REAL aiohttp server rather than a mock, the discipline the
other infra suites use: a fake that answers every request the same way cannot
produce the wrong-call shapes this module exists to refuse — a short batch, a
wrong-width vector, a service that is simply not there.
"""

from __future__ import annotations

from aiohttp import web

from nora.infra.embedding import (
    BGE_M3_DIMENSIONS,
    OllamaEmbedder,
    fingerprint,
    render_for_embedding,
)
from nora.obs.metrics import Metrics


async def _server(aiohttp_server, handler):
    app = web.Application()
    app.router.add_post("/api/embed", handler)
    return await aiohttp_server(app)


def _vec(n=BGE_M3_DIMENSIONS, fill=0.1):
    return [fill] * n


async def test_a_batch_comes_back_in_order(aiohttp_server):
    seen = {}

    async def handler(request):
        body = await request.json()
        seen.update(body)
        return web.json_response({"embeddings": [_vec(fill=0.1), _vec(fill=0.2)]})

    server = await _server(aiohttp_server, handler)
    metrics = Metrics()
    embedder = OllamaEmbedder(
        base_url=str(server.make_url("")), model="bge-m3:latest",
        metrics=metrics,
    )

    result = await embedder.embed(("a │ b │ c", "d │ e │ f"))

    assert result.status == "ok"
    assert len(result.vectors) == 2
    assert (result.vectors[0][0], result.vectors[1][0]) == (0.1, 0.2)
    assert seen["input"] == ["a │ b │ c", "d │ e │ f"]
    assert seen["model"] == "bge-m3:latest"
    assert metrics.get("nora_fact_embed_total", status="ok") == 1


async def test_a_short_batch_is_unavailable_not_a_partial_success(aiohttp_server):
    """Three vectors for four triples would write three rows and leave the
    fourth looking un-embeddable forever — a `nothing_found` wearing a shape
    nobody would check. The whole batch is refused and retried next pass, which
    costs one local call and no money at all."""
    async def handler(request):
        return web.json_response({"embeddings": [_vec()]})

    server = await _server(aiohttp_server, handler)
    metrics = Metrics()
    embedder = OllamaEmbedder(
        base_url=str(server.make_url("")), model="m", metrics=metrics,
    )

    result = await embedder.embed(("one", "two"))

    assert result.status == "unavailable"
    assert result.vectors == ()
    assert metrics.get("nora_fact_embed_total", status="unavailable") == 1


async def test_a_wrong_width_vector_is_unavailable(aiohttp_server):
    """The column is `vector(1024)` — a width MEASURED against the live model,
    not read off a card. A vector of another width fails at the write, in a
    background pass nobody is watching; refusing it here is the same failure
    one step earlier and with a reason attached."""
    async def handler(request):
        return web.json_response({"embeddings": [_vec(n=8)]})

    server = await _server(aiohttp_server, handler)
    embedder = OllamaEmbedder(base_url=str(server.make_url("")), model="m")

    assert (await embedder.embed(("one",))).status == "unavailable"


async def test_an_unreachable_service_is_unavailable():
    embedder = OllamaEmbedder(
        base_url="http://127.0.0.1:1", model="m", timeout_seconds=1.0,
    )

    result = await embedder.embed(("one",))

    assert result.status == "unavailable"
    assert result.vectors == ()


async def test_an_empty_batch_is_nothing_found_and_makes_no_call(aiohttp_server):
    """A real outcome — the pass ran and there was nothing to embed — and
    distinct from a service that could not be reached."""
    calls = []

    async def handler(request):
        calls.append(1)
        return web.json_response({"embeddings": []})

    server = await _server(aiohttp_server, handler)
    embedder = OllamaEmbedder(base_url=str(server.make_url("")), model="m")

    result = await embedder.embed(())

    assert result.status == "nothing_found"
    assert calls == []


def test_the_embedded_text_is_the_rendered_triple():
    """23 §6.2: the triple retrieves correctly across languages where the bare
    object does not.

    NOT the prompt's rendering — that carries the warrant, the source excerpt
    and now the dispute marker, none of which are the claim. Embedding those
    would put two facts with the same content but different sources in
    different places."""
    text = render_for_embedding(
        subject="big walk", predicate="made_by", obj="House House",
    )

    assert text == "big walk │ made_by │ House House"
    assert "source" not in text
    assert "DISPUTED" not in text


def test_the_fingerprint_moves_when_the_model_or_the_text_moves():
    """One comparison catches a model swap AND a changed row, with no second
    mechanism and no ordering trap."""
    base = fingerprint(model="bge-m3", text="a │ b │ c")

    assert fingerprint(model="bge-m3", text="a │ b │ c") == base
    assert fingerprint(model="other", text="a │ b │ c") != base
    assert fingerprint(model="bge-m3", text="a │ b │ d") != base


def test_the_fingerprint_cannot_be_confused_by_a_split():
    """model and text are separated rather than concatenated: without it,
    ("ab", "c") and ("a", "bc") hash identically, so a model rename could
    silently match a different row's fingerprint and skip re-embedding it."""
    assert fingerprint(model="ab", text="c") != fingerprint(model="a", text="bc")


def test_the_separator_is_one_a_caller_cannot_write():
    """**The version above passes against a separator that does not have the
    property**, which is how this shipped wrong.

    Any separator at all satisfies `("ab", "c") != ("a", "bc")`. The property
    that actually matters is that the separator cannot appear in either field
    — and no byte can: the code joined on four PRINTABLE bytes (a backslash,
    an `x`, two zeroes), where a model ending in those characters collided
    with a text beginning with them; switching to a real NUL only moves the
    collision to an input containing a NUL.

    The answer is to length-prefix each field rather than to look for an
    unforgeable byte — there is not one. Asserted by trying to forge the
    boundary with each candidate separator in turn, which is the only class of
    input that can tell the implementations apart.
    """
    # No empty joiner: `("m", "t")` against `("m", "t")` is the SAME pair, so
    # it cannot be a forgery and asserting it would be asserting that a hash
    # is not equal to itself.
    for joiner in (chr(92) + "x00", chr(0), "|"):
        assert fingerprint(model="m" + joiner, text="t") != fingerprint(
            model="m", text=joiner + "t"), (
            f"a caller writing {joiner!r} must not be able to forge the "
            f"boundary between the two fields"
        )
