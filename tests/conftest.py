"""Shared fixtures. Grows as the plan progresses; keep every fixture offline."""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"
