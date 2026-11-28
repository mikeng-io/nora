"""`JourneyDriver` owns what it acquires: the app task, the App's
shutdown and the journey SQL engine — on every exit, not only the clean
one. Each control composes a REAL subject over owned generated services,
starts the real `App` task (or fails during startup), and observes
the teardown calls land before the original exception or cancellation
continues.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from nora.infra.sql.schema import STIMULUS_PENDING
from nora.subject.environment import IsolatedServices
from nora.subject.eval_evidence import EvidenceError
from nora.subject.journey import JourneyDriver
from nora.subject.obtain import SubjectSpec, build_config, default_policy, obtain
from tests.integration.subject.protocol_services import services

# The driver runs `prepare_effect_reads`, which creates eval indexes —
# DDL — so every case here owns a dedicated database.
pytestmark = pytest.mark.fresh_database


@contextlib.asynccontextmanager
async def _world(clean_pg_url: str, tmp_path: Path, name: str):
    """One real composed subject over case-owned generated services."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    policy = (default_policy(("signal", "g1"), unit="generated-unit")
              + "home_room = true\n")
    overrides = {
        "platforms.signal.mode": "bridge", "platforms.signal.enabled": True,
        "obs.metrics.port": port,
    }
    spec = SubjectSpec(database_url=clean_pg_url, workdir=tmp_path / name,
                       policy=policy, overrides=overrides)
    cfg = build_config(spec)
    requests: list[Any] = []
    apps = services(cfg, requests)
    async with IsolatedServices(database_url=clean_pg_url,
                                applications=apps, redis=True) as env:
        yield env, await obtain(replace(spec, environment=env)), port


def _spy(driver: JourneyDriver,
         monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[int]]:
    """Count the two teardown calls without changing what they do."""
    shutdowns, disposes = [0], [0]
    shutdown, dispose = driver.app.shutdown, AsyncEngine.dispose

    async def watched_shutdown() -> None:
        shutdowns[0] += 1
        await shutdown()

    async def watched_dispose(self: AsyncEngine) -> None:
        if self is driver.engine:
            disposes[0] += 1
        await dispose(self)

    driver.app.shutdown = watched_shutdown
    monkeypatch.setattr(AsyncEngine, "dispose", watched_dispose)
    return shutdowns, disposes


async def test_driver_clean_exit_ends_task_shuts_app_and_disposes_engine(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """Positive control over real resources: `async with` entry runs the
    app to real bridge-readiness, and exit calls exactly one shutdown and
    one dispose before the engine is gone."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    async with _world(clean_pg_url, tmp_path, "clean") as (_env, subject, port):
        driver = JourneyDriver(subject)
        shutdowns, disposes = _spy(driver, monkeypatch)
        started_with_seed = []
        run = driver.app.run

        async def observe_start() -> None:
            started_with_seed.append(any(
                row["unit"] == subject.unit and row["mode"] == "live" and row["stimulus_id"] == 0
                for row in await driver.rows(STIMULUS_PENDING)
            ))
            await run()

        driver.app.run = observe_start
        async with driver:
            assert driver.task is not None and not driver.task.done()
            assert started_with_seed == [True]
        assert driver.task is None
        assert shutdowns == [1] and disposes == [1]
        assert not subject.deps.background._tasks
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


async def test_driver_start_failure_still_unwinds_and_surfaces(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """A failed preflight disposes the engine before the App starts."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    async with _world(clean_pg_url, tmp_path, "start-fail") as (_env, subject, _):
        driver = JourneyDriver(subject)
        shutdowns, disposes = _spy(driver, monkeypatch)

        async def fail(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("generated start failure")

        monkeypatch.setattr(
            "nora.subject.journey.prepare_effect_reads", fail)
        with pytest.raises(RuntimeError, match="generated start failure"):
            async with driver:
                pass  # pragma: no cover — entry fails before the body
        assert driver.task is None
        assert shutdowns == [0] and disposes == [1]


async def test_driver_start_cancellation_still_unwinds(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """Cancelling preflight disposes the engine and propagates cancellation."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    async with _world(clean_pg_url, tmp_path, "start-cancel") as (_env, subject, _):
        driver = JourneyDriver(subject)
        shutdowns, disposes = _spy(driver, monkeypatch)

        async def cancelled(*args: Any, **kwargs: Any) -> None:
            raise asyncio.CancelledError

        monkeypatch.setattr(
            "nora.subject.journey.prepare_effect_reads", cancelled)
        with pytest.raises(asyncio.CancelledError):
            async with driver:
                pass  # pragma: no cover — entry is cancelled before the body
        assert driver.task is None
        assert shutdowns == [0] and disposes == [1]


async def test_driver_failed_app_task_is_retrieved_and_unwound(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """A runtime task that already failed still gets `shutdown()` and the
    engine dispose — and its exception is retrieved, never left to
    surface as an unretrieved-task warning."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    async with _world(clean_pg_url, tmp_path, "task-fail") as (_env, subject, _):
        driver = JourneyDriver(subject)
        shutdowns, disposes = _spy(driver, monkeypatch)

        async def dead_run() -> None:
            await asyncio.sleep(0.05)
            raise RuntimeError("generated app failure")

        driver.app.run = dead_run
        with pytest.raises(EvidenceError, match="stopped during the journey"):
            async with driver:
                pass  # pragma: no cover — readiness sees the failed task
        assert driver.task is None
        assert shutdowns == [1] and disposes == [1]


async def test_driver_body_failure_unwinds_and_surfaces_the_body_error(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """A drive-time exception gets the same teardown as a clean exit,
    and the body's own exception is what propagates."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    async with _world(clean_pg_url, tmp_path, "body-fail") as (_env, subject, _):
        driver = JourneyDriver(subject)
        shutdowns, disposes = _spy(driver, monkeypatch)
        with pytest.raises(RuntimeError, match="generated body failure"):
            async with driver:
                assert not driver.task.done()
                raise RuntimeError("generated body failure")
        assert driver.task is None
        assert shutdowns == [1] and disposes == [1]
        assert not subject.deps.background._tasks


async def test_driver_exit_cancelled_still_finishes_teardown(
        clean_pg_url, evaluation_config, tmp_path, monkeypatch):
    """Cancelling the caller mid-`__aexit__` cannot interrupt teardown:
    shutdown and dispose complete first, then the CancelledError
    propagates. The cancel lands deterministically — inside the
    `asyncio.wait` the exit drains — because the shutdown itself marks
    when teardown is underway."""
    monkeypatch.setenv("OMNIROUTE_API_KEY", "generated-token")
    async with _world(clean_pg_url, tmp_path, "exit-cancel") as (_env, subject, _):
        driver = JourneyDriver(subject)
        _, disposes = _spy(driver, monkeypatch)
        teardown_started = asyncio.Event()
        shutdown_done = asyncio.Event()
        shutdown = driver.app.shutdown

        async def observed_shutdown() -> None:
            teardown_started.set()
            await asyncio.sleep(0.3)
            await shutdown()
            shutdown_done.set()

        driver.app.shutdown = observed_shutdown

        async def run() -> None:
            async with driver:
                return

        journey = asyncio.create_task(run())
        async with asyncio.timeout(15):
            await teardown_started.wait()
        journey.cancel()
        with pytest.raises(asyncio.CancelledError):
            await journey
        assert shutdown_done.is_set() and disposes == [1]
        assert not subject.deps.background._tasks
