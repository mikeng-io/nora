"""Shared contract for `nora.domain.memory_index.QueryEmbedder`:
`nora.infra.embedder.LlamaEmbedder` over the fake llama-server, and
`nora.testing.memory_index.FakeQueryEmbedder`, which the search's tests use.
What the search relies on: one question is one vector of the production width
and unit length, the same question gives the same vector, two questions do not,
and a down embedder RAISES (the search then falls back), never answers with
something empty.
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from aiohttp.test_utils import TestServer

from nora.domain.memory_index import QueryEmbedder
from nora.infra.embedder import DIMENSIONS, LlamaEmbedder
from nora.testing.memory_index import DIMENSIONS as FAKE_DIMENSIONS
from nora.testing.memory_index import FakeQueryEmbedder
from tests.unit.test_embedder import FakeLlamaServer

#: `make(down)`: an embedder that works, or one whose server is down.
Make = Callable[[bool], QueryEmbedder]


@pytest.fixture(params=["fake", "llama"])
async def make(request: Any) -> AsyncIterator[Make]:
    if request.param == "fake":
        yield lambda down: FakeQueryEmbedder(fail=OSError("down") if down else None)
        return
    fake = FakeLlamaServer()
    server = TestServer(fake.app())
    await server.start_server()
    url = str(server.make_url("")).rstrip("/")

    def llama(down: bool) -> QueryEmbedder:
        fake.status, fake.healthy = (503, False) if down else (200, True)
        return LlamaEmbedder(url, timeout_seconds=5)

    try:
        yield llama
    finally:
        await server.close()


def test_the_fake_is_the_production_width() -> None:
    assert FAKE_DIMENSIONS == DIMENSIONS


async def test_one_question_is_one_unit_vector_and_the_same_one_again(make: Make) -> None:
    embedder = make(False)
    first = await embedder.embed_query("where did we eat on friday")
    assert len(first) == DIMENSIONS
    assert math.isclose(math.fsum(x * x for x in first), 1.0, rel_tol=1e-6)
    assert await embedder.embed_query("where did we eat on friday") == first
    assert await embedder.embed_query("who booked the boat") != first


async def test_a_down_embedder_raises(make: Make) -> None:
    with pytest.raises(Exception):  # noqa: B017 — any error: the search falls back
        await make(True).embed_query("where did we eat on friday")
