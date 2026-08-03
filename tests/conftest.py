"""Shared fixtures. Grows as the plan progresses; keep every fixture offline."""

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_metrics():
    from nora.obs.metrics import METRICS

    METRICS.reset()
    yield
    METRICS.reset()
