"""Shared fixtures. Grows as the plan progresses; keep every fixture offline."""

import pytest

import nora.obs.metrics as metrics_module
from nora.obs.metrics import Metrics


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the module-level `METRICS` name at a fresh `Metrics()` — a fresh
    `CollectorRegistry` — for the duration of the test, instead of resetting a
    shared one. prometheus-client's `CollectorRegistry` has no `reset()`; this
    is why that method is gone from `Metrics` too.

    `METRICS` is still a module-level singleton (DEC-13 defers deleting it to
    Task 4); this only isolates code that looks it up fresh via
    `nora.obs.metrics.METRICS` at call time (`nora.app.composition`'s object
    graph does). It does NOT reach the couple of `metrics=METRICS` *default
    parameter values* already bound at import time
    (`nora.persona.loader.PersonaStore`, `nora.agent.guards`) — a rebind can't
    retroactively change an object a function signature already captured.
    Nothing in this suite asserts against that shared instance's accumulated
    state, so it costs nothing today; Task 4 removes the singleton (and the
    default-argument capture along with it) entirely.
    """
    monkeypatch.setattr(metrics_module, "METRICS", Metrics())
