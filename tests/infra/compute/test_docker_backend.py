"""`LocalDockerBackend` against a MOCKED docker client -- real container
launches need a live Docker daemon and are verified by hand (Task 3, Step 5),
the same convention `deploy/drop`'s own test suite already follows for
`PgRustfsArtifactStore` (its module docstring).

Deviations from the task brief's literal Step-1 listing, and why (see also
`task-3-report.md`):

* `launch()` never suspends internally -- it schedules `_run_job` as a
  background `asyncio.Task` via `asyncio.ensure_future` and returns without
  awaiting anything. A newly `ensure_future`d task's first step is only
  *scheduled* (`loop.call_soon`), not run, so it never gets a turn until the
  calling coroutine itself actually yields to the loop. Concretely: `with
  aioresponses() as mocked: ... job_id = await backend.launch(...)` followed
  by code OUTSIDE that `with` block closes the HTTP mock before `_run_job`
  ever calls `_mint_token` -- the mint then hits a real (unmocked, failing)
  network call, and every job fails at the token-mint step before
  `containers.run` is ever reached. Every test below therefore keeps the
  `aioresponses()` context open across the wait/poll that lets `_run_job`
  actually progress, using the `_wait_for`/`_poll_until_done` helpers, rather
  than closing it right after `launch()` returns.
* `test_a_third_job_waits_for_the_concurrency_semaphore`'s original
  `container.wait` mock resolves instantly, so job1/job2 can run their whole
  `_run_job` (mint -> start -> wait -> kill -> read_result) to completion --
  and release the semaphore -- well within the `await asyncio.sleep(0.05)`
  window the brief used to catch job3 "still blocked". That makes the
  assertion a wall-clock race, not a deterministic check. Here `container
  .wait` instead blocks on a real `threading.Event` (run via
  `asyncio.to_thread`, so it doesn't block the event loop) until the test
  explicitly releases it, so job1/job2 are held open for exactly as long as
  the test needs, deterministically."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests.exceptions
from aioresponses import aioresponses

from nora.domain.ports import JobResult, JobStatus
from nora.infra.compute.docker_backend import LocalDockerBackend

DROP_URL = "http://drop:8081"
MASTER_TOKEN = "master-token"  # noqa: S105


@pytest.fixture
def docker_client():
    client = MagicMock()
    container = MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    client.containers.run.return_value = container
    return client


@pytest.fixture
def backend(tmp_path: Path, docker_client):
    return LocalDockerBackend(
        docker_client=docker_client,
        drop_internal_url=DROP_URL,
        drop_master_token=MASTER_TOKEN,
        model_api_key="test-model-key",
        model_base_url="https://omniroute.example/v1",
        model_name="test-model",
        sandbox_image="smith:latest",
        job_timeout_seconds=30,
        max_concurrent_jobs=2,
        workspace_root=tmp_path,
    )


async def test_launch_returns_a_job_id_immediately(backend):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
    assert job_id.startswith("job_")


async def _poll_until_done(backend, *, job_id: str, attempts: int = 200) -> JobResult:
    """`launch` schedules `_run_job` as a background `asyncio.Task` it never
    awaits directly, and neither `launch` nor `poll` contains an internal
    `await` that actually suspends -- so a tight retry loop with no sleep
    between iterations never yields to the event loop at all, and the
    scheduled task never gets a turn to run. `asyncio.sleep(0)` on every
    iteration is what actually lets it progress (its own real suspension
    points are the two `asyncio.to_thread(...)` calls inside `_run_job`, which
    only get checked when this coroutine yields). Every test in this file
    that waits for a job to finish should call this rather than hand-rolling
    a bare retry loop."""
    result = None
    for _ in range(attempts):
        result = await backend.poll(job_id=job_id)
        if result.status != JobStatus.RUNNING:
            return result
        await asyncio.sleep(0)
    return result


async def _wait_for(predicate: Callable[[], bool], *, attempts: int = 200) -> None:
    """Yield to the event loop (see `_poll_until_done`'s docstring) until
    `predicate()` is true, for cases -- like a workspace directory landing on
    disk mid-job -- where there is no `JobResult` to poll yet."""
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate(), "condition never became true"


async def test_a_successful_job_reports_succeeded(backend, docker_client, tmp_path):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")

        # Wait for `_run_job` to actually create the workspace directory
        # (after minting its token, which needs this `aioresponses()` context
        # still open -- see the module docstring) before this test writes
        # into it.
        workspace = tmp_path / job_id
        await _wait_for(lambda: workspace.exists())

        # Simulate what Smith's sandbox (Task 4) writes to the bind-mounted
        # workspace before exiting -- the container mock's `wait` already
        # resolves immediately, so the workspace files just need to exist by
        # the time this test polls.
        (workspace / "url.txt").write_text("https://nora.example.net/drop/abc")
        (workspace / "summary.txt").write_text("wrote a haiku about autumn")

        result = await _poll_until_done(backend, job_id=job_id)
    assert result.status == JobStatus.SUCCEEDED
    assert result.url == "https://nora.example.net/drop/abc"
    assert result.summary == "wrote a haiku about autumn"


async def test_a_job_that_exits_without_writing_output_is_failed(backend, tmp_path):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")

        # No url.txt/summary.txt written -- the container "exited" (per the
        # mock) without producing a result.
        result = await _poll_until_done(backend, job_id=job_id)
    assert result.status == JobStatus.FAILED
    assert result.error is not None


async def test_a_job_that_overruns_its_timeout_is_failed_and_cleaned_up(backend, docker_client):
    """docker-py's `container.wait(timeout=...)` overrunning raises
    `requests.exceptions.ReadTimeout` -- NOT the builtin `TimeoutError` (see
    `docker_backend.py`'s import comment and the `except` clause in
    `_run_job`). This is the one branch the task brief itself called out as
    "resolved, not left open" and worth exercising directly, not just
    asserting by inspection: the timeout path must report FAILED with the
    documented error message AND still clean up the container (`_kill_and_
    remove`), the same as any other failure path -- a timed-out job must not
    keep running past its own guardrail."""
    container = docker_client.containers.run.return_value
    container.wait.side_effect = requests.exceptions.ReadTimeout()
    # `_kill_and_remove` looks the container up again by job_id via
    # `containers.get` -- wire it to the same mock container so `.remove`
    # calls are observable on the object under test.
    docker_client.containers.get.return_value = container

    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
        result = await _poll_until_done(backend, job_id=job_id)

    assert result.status == JobStatus.FAILED
    assert result.error == "job exceeded its wall-clock timeout"
    container.remove.assert_called_once_with(force=True)


async def test_launch_mints_a_create_scoped_token_by_default(backend):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
        await _poll_until_done(backend, job_id=job_id)
    request = next(iter(mocked.requests.values()))[0]
    assert request.kwargs["json"] == {"scope": "create", "ttl_seconds": 30}


async def test_launch_mints_an_update_scoped_token_for_an_artifact_id(backend):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="revise the chart", artifact_id="art_1")
        await _poll_until_done(backend, job_id=job_id)
    request = next(iter(mocked.requests.values()))[0]
    assert request.kwargs["json"] == {
        "scope": "update", "artifact_id": "art_1", "ttl_seconds": 30,
    }


async def test_launch_attaches_the_container_to_nora_compute_only(backend, docker_client):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
        await _poll_until_done(backend, job_id=job_id)
    _, kwargs = docker_client.containers.run.call_args
    assert kwargs["network"] == "nora-compute"


async def test_a_third_job_waits_for_the_concurrency_semaphore(backend, docker_client):
    """max_concurrent_jobs=2 (the `backend` fixture) -- a third launch must
    not start its container until one of the first two finishes. Verified
    indirectly: the semaphore itself is asyncio-internal, so this asserts the
    OBSERVABLE effect -- `containers.run` is not called a third time until
    the first job's workspace signals completion and its task is awaited.

    `container.wait` blocks on a real `threading.Event` (it runs inside
    `asyncio.to_thread`, so blocking it doesn't block the event loop) so
    job1/job2 stay "in flight" holding the semaphore for exactly as long as
    this test needs, rather than racing a fixed wall-clock sleep against how
    fast the mocked container actually finishes."""
    release = threading.Event()
    docker_client.containers.run.return_value.wait.side_effect = (
        lambda **kwargs: release.wait(timeout=5) and {"StatusCode": 0}
    )

    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"}, repeat=True)
        job1 = await backend.launch(task="job one")
        await backend.launch(task="job two")
        launch3 = asyncio.ensure_future(backend.launch(task="job three"))

        # Wait until both job1 and job2 have actually started their
        # containers (mint completed, `containers.run` called) -- they're
        # now blocked inside `container.wait` on `release`.
        await _wait_for(lambda: docker_client.containers.run.call_count >= 2)
        # Give job three's task a real chance to try (and fail) to acquire
        # the semaphore before asserting it is still blocked.
        await asyncio.sleep(0)
        assert docker_client.containers.run.call_count == 2

        # let job1 "finish" so the semaphore releases for job three
        release.set()
        await _poll_until_done(backend, job_id=job1)
        job3 = await launch3
        # `launch3` resolving only means job three's `launch()` returned a
        # job_id -- its `_run_job` task still needs its own turn to actually
        # reach `containers.run`.
        await _poll_until_done(backend, job_id=job3)
        assert docker_client.containers.run.call_count == 3
