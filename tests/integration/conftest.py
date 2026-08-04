"""Registers the shared `harness` fixture used by every M5 integration test."""

import pytest

from tests.integration.harness import build_handler


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    built: list = []
    h = await build_handler(tmp_path, register=built.append)
    yield h
    for built_h in built:
        await built_h.aclose()


@pytest.fixture
async def harness_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    built: list = []

    async def factory(**kwargs):
        return await build_handler(tmp_path, register=built.append, **kwargs)

    yield factory
    # Every Harness reachable from a test lands in `built`: build_handler registers
    # the one it constructs, and simulate_restart passes the parent's own `_register`
    # (this same `built.append`) to the replacement it builds — so a chain of restarts
    # is covered too, not just the first Harness a test gets back.
    for h in built:
        await h.aclose()
