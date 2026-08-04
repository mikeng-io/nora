"""Registers the shared `harness` fixture used by every M5 integration test."""

import pytest

from tests.integration.harness import build_handler


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    h = await build_handler(tmp_path, monkeypatch=monkeypatch)
    yield h
    await h.aclose()


@pytest.fixture
async def harness_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    built: list = []

    async def factory(**kwargs):
        h = await build_handler(tmp_path, monkeypatch=monkeypatch, **kwargs)
        built.append(h)
        return h

    yield factory
    # simulate_restart() closes its *parent* harness's store itself before
    # building the replacement, so this only needs to close what `built` holds.
    for h in built:
        await h.aclose()
