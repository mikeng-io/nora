"""Registers the shared `harness` fixture used by every M5 integration test."""

import pytest

from tests.integration.harness import build_handler


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "test-key")
    return await build_handler(tmp_path)
