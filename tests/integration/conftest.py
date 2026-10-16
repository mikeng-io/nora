"""Registers the shared `harness` fixture used by every M5 integration test."""

import importlib

import pytest

from tests.fakes import FakeHoncho
from tests.integration.harness import build_handler


@pytest.fixture
async def draw_honcho(monkeypatch):
    """A Honcho for `evals.backbone._drive`'s per-draw workspace .

    Every draw creates a workspace of its own before its subject is
    composed, and refuses to run when it cannot, so a test driving `_drive`
    needs a server to create it on. The subject's own memory traffic still
    goes where the template says (unreachable in a test, best-effort):
    only the draw's workspace lifecycle is pointed here.
    """
    obtain_module = importlib.import_module("nora.subject.obtain")
    _, deployment = obtain_module.memory_endpoint()
    fake = FakeHoncho()
    url = await fake.start()
    monkeypatch.setattr(obtain_module, "memory_endpoint", lambda: (url, deployment))
    yield fake
    await fake.close()


@pytest.fixture
async def harness(tmp_path, monkeypatch, pg_url):
    # `pg_url` (tests/conftest.py) creates a fresh Postgres database for this test
    # and exports it as DATABASE_URL — the composition root and the harness read
    # it from there. A "restart" (simulate_restart) reuses the same DATABASE_URL,
    # so the record survives exactly as a real process exit would leave it.
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    built: list = []
    h = await build_handler(tmp_path, register=built.append)
    yield h
    for built_h in built:
        await built_h.aclose()


@pytest.fixture
async def harness_factory(tmp_path, monkeypatch, pg_url):
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
