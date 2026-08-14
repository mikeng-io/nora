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
  the test needs, deterministically.
* That same `threading.Event` device (`_hold_container_open`) is now what
  every test that needs a job held mid-flight uses, not just the semaphore
  one. `test_a_successful_job_reports_succeeded` originally waited only for
  the workspace directory to appear and then wrote `url.txt`/`summary.txt`
  into it, with NOTHING ordering those writes against `_run_job`'s own
  progress -- it passed by outrunning the background task, and a whole-branch
  review made it fail deterministically by inserting a 50ms sleep before the
  writes (reproduced here before the fix). `test_a_job_that_exits_without_
  writing_output_is_failed` had the same non-ordering plus a weaker
  assertion: `error is not None` is satisfied by a mint failure or a
  never-started container just as well as by the branch it names."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests.exceptions
from aioresponses import aioresponses

from nora.domain.ports import JobResult, JobStatus
from nora.infra.compute.docker_backend import MAX_ERROR_LOG_CHARS, LocalDockerBackend

DROP_URL = "http://drop:8081"
MASTER_TOKEN = "master-token"  # noqa: S105


@pytest.fixture
def docker_client():
    client = MagicMock()
    container = MagicMock()
    container.wait.return_value = {"StatusCode": 0}
    client.containers.run.return_value = container
    return client


def make_backend(
    *, docker_client, workspace_root: Path, drop_internal_url: str = DROP_URL,
    workspace_host_root: Path | None = None,
) -> LocalDockerBackend:
    """The `backend` fixture's construction, reusable by a test that needs a
    DIFFERENT `workspace_root` than `tmp_path` (and by
    `tests/contract/test_compute_backend_contract.py`, which drives this same
    adapter through the shared `ComputeBackend` suite against a real
    in-process token server, hence `drop_internal_url`). One constructor call
    site for both suites: a new required parameter breaks them together
    instead of leaving the contract suite building a stale backend.

    `workspace_host_root` defaults to `workspace_root` — the un-containerised
    deployment, where the two really are the same directory, and the only
    arrangement under which a test may read back what the sandbox "wrote".
    The test that cares about the DISTINCTION passes them separately."""
    return LocalDockerBackend(
        docker_client=docker_client,
        drop_internal_url=drop_internal_url,
        drop_master_token=MASTER_TOKEN,
        model_api_key="test-model-key",
        model_base_url="https://omniroute.example/v1",
        model_name="test-model",
        google_maps_api_key="test-maps-key",
        sandbox_image="smith:latest",
        job_timeout_seconds=30,
        max_concurrent_jobs=2,
        mem_limit="1g",
        nano_cpus=1_000_000_000,
        tracing_enabled=False,
        tracing_endpoint="",
        tracing_headers="",
        workspace_root=workspace_root,
        workspace_host_root=(
            workspace_root if workspace_host_root is None else workspace_host_root
        ),
    )


@pytest.fixture
def backend(tmp_path: Path, docker_client):
    return make_backend(docker_client=docker_client, workspace_root=tmp_path)


async def test_launch_returns_a_job_id_immediately(backend):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
    assert job_id.startswith("job_")


async def _poll_until_done(backend, *, job_id: str, max_seconds: float = 10.0) -> JobResult:
    """`launch` schedules `_run_job` as a background `asyncio.Task` it never
    awaits directly, and neither `launch` nor `poll` contains an internal
    `await` that actually suspends -- so a tight retry loop with no sleep
    between iterations never yields to the event loop at all, and the
    scheduled task never gets a turn to run. Yielding on every iteration is
    what lets it progress. Every test in this file that waits for a job to
    finish should call this rather than hand-rolling a bare retry loop.

    **BOUNDED BY WALL CLOCK, NOT BY A COUNT OF EVENT-LOOP TURNS.** It was
    `for _ in range(200): await asyncio.sleep(0)`, and that is an instrument
    measuring the wrong thing: `_run_job`'s real suspension points are two
    `asyncio.to_thread(...)` calls, so what this waits on is a WORKER THREAD
    finishing — wall-clock work — while `sleep(0)` yields the loop without
    advancing time at all. On an idle machine 200 turns happen to outlast the
    thread; under CPU contention they are burnt in microseconds before the
    thread is ever scheduled, the loop exits with a RUNNING result, and the
    assertion after it fails.

    That is why SEVEN tests in this file went red together under load
    (measured 2026-08-15, `nproc*2` busy loops) rather than one being "flaky":
    they share this helper, and it could not measure the thing.

    `sleep(0.001)` rather than `sleep(0)` for the same reason — it hands time
    to the thread pool, not just a turn to the loop."""
    deadline = time.monotonic() + max_seconds
    result = None
    while True:
        result = await backend.poll(job_id=job_id)
        if result.status != JobStatus.RUNNING:
            return result
        if time.monotonic() >= deadline:
            return result
        await asyncio.sleep(0.001)


async def _wait_for(predicate: Callable[[], bool], *, max_seconds: float = 10.0) -> None:
    """Yield to the event loop (see `_poll_until_done`'s docstring) until
    `predicate()` is true, for cases -- like a workspace directory landing on
    disk mid-job -- where there is no `JobResult` to poll yet.

    Wall-clock bounded for `_poll_until_done`'s reason: the condition is
    usually a worker thread having done something, and a count of event-loop
    turns is not a measure of that."""
    deadline = time.monotonic() + max_seconds
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.001)
    assert predicate(), "condition never became true"


def _hold_container_open(docker_client, *, status_code: int = 0) -> threading.Event:
    """Make the mocked `container.wait` block until the returned `Event` is
    set, so a test can hold a job open at exactly the point the real container
    would still be running.

    This is the only real synchronisation available here. `_run_job`'s
    remaining steps (`_kill_and_remove` -> `_read_result`) run inside the
    background task with no suspension point a test can hook, so a test that
    merely waits for the workspace directory to appear and then writes into it
    is racing `_read_result`, not ordering against it -- it passes only by
    outrunning the rest of `_run_job`. Blocking `wait` (in `asyncio
    .to_thread`, so the event loop keeps turning) makes the ordering a fact.
    """
    release = threading.Event()
    docker_client.containers.run.return_value.wait.side_effect = (
        lambda **kwargs: release.wait(timeout=5) and {"StatusCode": status_code}
    )
    return release


async def test_a_successful_job_reports_succeeded(backend, docker_client, tmp_path):
    """The workspace files must be in place BEFORE `_run_job` reads them, and
    that ordering is enforced by holding `container.wait` open rather than by
    hoping this test outruns the background task -- inserting a 50ms sleep
    before the writes used to fail this test deterministically."""
    release = _hold_container_open(docker_client)

    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")

        # The job has started its container and is now parked inside
        # `container.wait` -- `_read_result` is unreachable until `release`.
        await _wait_for(lambda: docker_client.containers.run.call_count == 1)
        workspace = tmp_path / job_id
        await _wait_for(lambda: workspace.exists())
        assert (await backend.poll(job_id=job_id)).status == JobStatus.RUNNING

        # Simulate what Smith's sandbox (Task 4) writes to the bind-mounted
        # workspace before exiting.
        (workspace / "url.txt").write_text("https://nora.example.net/drop/abc")
        (workspace / "summary.txt").write_text("wrote a haiku about autumn")

        release.set()
        result = await _poll_until_done(backend, job_id=job_id)
    assert result.status == JobStatus.SUCCEEDED
    assert result.url == "https://nora.example.net/drop/abc"
    assert result.summary == "wrote a haiku about autumn"


async def test_a_job_that_exits_without_writing_output_is_failed(backend, docker_client, tmp_path):
    """Same `threading.Event` synchronisation as the success case, for the
    same reason plus one more: with no ordering at all, `assert result.error
    is not None` passes for ANY failure -- including a mint that never
    happened or a container that never started -- so the test would stay green
    even if the container path it claims to exercise were never reached.
    Holding the container open pins that the job really did start, really was
    RUNNING, and only failed once `_read_result` found no output."""
    release = _hold_container_open(docker_client)

    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")

        await _wait_for(lambda: docker_client.containers.run.call_count == 1)
        workspace = tmp_path / job_id
        await _wait_for(lambda: workspace.exists())
        assert (await backend.poll(job_id=job_id)).status == JobStatus.RUNNING

        # No url.txt/summary.txt written -- the container exits without
        # producing a result.
        release.set()
        result = await _poll_until_done(backend, job_id=job_id)
    assert result.status == JobStatus.FAILED
    assert result.error is not None
    assert "without producing a result" in result.error


async def test_a_job_that_overruns_its_timeout_is_failed_and_cleaned_up(backend, docker_client):
    """docker-py's `container.wait(timeout=...)` overrunning raises
    `requests.exceptions.ReadTimeout` -- NOT the builtin `TimeoutError` (see
    `docker_backend.py`'s import comment and the `except` clause in
    `_run_job`). This is the one branch the task brief itself called out as
    "resolved, not left open" and worth exercising directly, not just
    asserting by inspection: the timeout path must report FAILED with the
    documented error message AND still clean up the container (`_kill_and_
    remove`), the same as any other failure path -- a timed-out job must not
    keep running past its own guardrail. The container's own output must
    survive that removal too: a job killed at its guardrail is exactly a job
    someone needs to see the logs of."""
    container = docker_client.containers.run.return_value
    container.wait.side_effect = requests.exceptions.ReadTimeout()
    container.logs.return_value = b"still thinking about it\n"
    # `_kill_and_remove` looks the container up again by job_id via
    # `containers.get` -- wire it to the same mock container so `.remove`
    # calls are observable on the object under test.
    docker_client.containers.get.return_value = container

    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
        result = await _poll_until_done(backend, job_id=job_id)

    assert result.status == JobStatus.FAILED
    assert result.error is not None
    assert result.error.startswith("job exceeded its wall-clock timeout")
    assert "still thinking about it" in result.error
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


async def test_the_bind_source_is_the_host_path_not_this_process_path(
    tmp_path: Path, docker_client,
):
    """A bind-mount SOURCE is resolved by the Docker daemon on the HOST
    filesystem, never inside the container that asked for it. Nora runs with
    `./data:/app/data`, so her own `workspace_root` is `/app/data/...` — a
    path that does not exist on the host. Handing THAT to the daemon raises
    nothing: it creates an empty host directory, the sandbox finds no
    `task.txt`, and the job fails with no error anyone can read.

    Two DIFFERENT roots here on purpose. The container-side one is where the
    `task.txt` this test reads back was actually written; the host-side one is
    the only thing that may appear in `volumes`. A backend that used one path
    for both — the shipped defect — fails this on whichever assertion it got
    wrong."""
    container_root = tmp_path / "container-side" / "compute-workspaces"
    host_root = tmp_path / "host-side" / "compute-workspaces"
    backend = make_backend(
        docker_client=docker_client,
        workspace_root=container_root,
        workspace_host_root=host_root,
    )
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
        await _poll_until_done(backend, job_id=job_id)

    _, kwargs = docker_client.containers.run.call_args
    assert list(kwargs["volumes"]) == [str(host_root / job_id)]
    assert kwargs["volumes"][str(host_root / job_id)]["bind"] == "/workspace"
    # ... while this process still wrote the brief where IT can see it.
    assert (container_root / job_id / "task.txt").read_text() == "write a haiku"


async def test_context_is_written_to_the_workspace_as_json(
    backend, docker_client, tmp_path: Path,
):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(
            task="chart the rainfall",
            context=[{"kind": "place", "name": "Yoho Mall", "rating": 4.2}],
        )
        await _poll_until_done(backend, job_id=job_id)
    written = json.loads(
        (backend._workspace_root / job_id / "context.json").read_text()
    )
    assert written == [{"kind": "place", "name": "Yoho Mall", "rating": 4.2}]


async def test_no_context_writes_an_empty_json_list(backend, docker_client):
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
        await _poll_until_done(backend, job_id=job_id)
    written = json.loads(
        (backend._workspace_root / job_id / "context.json").read_text()
    )
    assert written == []


async def test_the_written_context_json_hashes_to_what_make_artifact_promises(
    backend, docker_client,
):
    """`ComputeJob`'s own docstring (`nora/domain/compute.py`) promises a
    caller can hash the on-disk `context.json` and get back `context_sha256`
    -- the hash `nora/agent/tools.py`'s `make_artifact` closure computes as
    `hashlib.sha256(json.dumps(context or [], sort_keys=True).encode("utf-8"))
    .hexdigest()` BEFORE this backend ever writes a file (context-channel spec
    §5: hashed in the closure that already holds `context` in hand, not here).

    This is a genuine round trip, not two tests that each assume the other's
    serialization: it hashes the ACTUAL BYTES this backend put on disk
    (`.read_bytes()`, no re-parse/re-dump that could paper over a mismatch)
    and compares against an independent computation of `make_artifact`'s own
    formula. A dict with keys already out of alphabetical order
    (`{"zebra": ..., "apple": ...}`) is the fixture that actually exercises
    the parity -- Python's dict preserves insertion order, so if either side
    dropped its `sort_keys=True`, this dict's on-disk bytes and the
    independently-computed hash would diverge and this test would catch it;
    an already-sorted fixture could pass with either side unsorted."""
    context = [{"zebra": "z-place", "apple": "a-place", "kind": "route"}]
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="chart the rainfall", context=context)
        await _poll_until_done(backend, job_id=job_id)

    on_disk_bytes = (backend._workspace_root / job_id / "context.json").read_bytes()
    on_disk_sha256 = hashlib.sha256(on_disk_bytes).hexdigest()

    # `make_artifact`'s own formula (tools.py, context_sha256), computed here
    # independently of whatever bytes the backend actually wrote.
    context_sha256 = hashlib.sha256(
        json.dumps(context, sort_keys=True).encode("utf-8")
    ).hexdigest()

    assert on_disk_sha256 == context_sha256


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
    release = _hold_container_open(docker_client)

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


async def test_a_workspace_that_cannot_be_created_is_a_failed_poll_not_a_raise(
    tmp_path: Path, docker_client,
):
    """`ComputeBackend`'s contract is "NEVER raises": a backend that cannot
    even start a job "reports that as a `poll` result ... not an exception out
    of `launch`", so a caller never needs a try/except around it.

    A whole-branch review broke that with the scenario reproduced here --
    `workspace_root` pointing at a FILE, so `workspace.mkdir(...)` raises
    `NotADirectoryError`. `mkdir`, the `task.txt` write and the final
    `_read_result` all sat OUTSIDE `_run_job`'s only try/except, and `poll`
    read `running.result()` bare, so the error came straight out of `poll`
    into the caller. Any pre-container failure would do; this is the one that
    was actually demonstrated."""
    workspace_root = tmp_path / "workspaces"
    workspace_root.write_text("a file where a directory should be")

    backend = make_backend(docker_client=docker_client, workspace_root=workspace_root)
    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
        result = await _poll_until_done(backend, job_id=job_id)

    assert result.status == JobStatus.FAILED
    assert result.error is not None
    assert "NotADirectoryError" in result.error
    # The job died before any container existed -- nothing to launch, nothing
    # to clean up.
    docker_client.containers.run.assert_not_called()


async def test_a_cancelled_job_task_is_a_failed_poll_not_a_raise(backend, docker_client):
    """`poll`'s own guard, not `_run_job`'s. `CancelledError` derives from
    `BaseException`, so `_run_job`'s `except Exception` cannot convert it --
    a task cancelled by loop shutdown would re-raise out of `.result()` and
    break the same contract from the other side."""
    _hold_container_open(docker_client)  # never released -- the job stays in flight

    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
        await _wait_for(lambda: docker_client.containers.run.call_count == 1)

        task = backend._tasks[job_id]  # simulating loop shutdown cancelling the job
        task.cancel()
        await _wait_for(lambda: task.done())
        result = await backend.poll(job_id=job_id)

    assert result.status == JobStatus.FAILED
    assert result.error == "compute job was cancelled"


async def test_a_failed_jobs_container_logs_survive_its_removal(backend, docker_client, tmp_path):
    """`_kill_and_remove`'s `container.remove(force=True)` used to destroy the
    only evidence a failed job ever produces -- and `container.wait`'s own
    `{"StatusCode": N}` was discarded on top of that. This sandbox's expected
    failure mode is "LLM-authored code went wrong", so both must reach the
    caller: the logs are read BEFORE the removal, and the exit code rides
    along with them."""
    container = docker_client.containers.run.return_value
    container.logs.return_value = (
        b'  File "/workspace/plot.py", line 3\n    ZeroDivisionError: division by zero\n'
    )
    docker_client.containers.get.return_value = container
    release = _hold_container_open(docker_client, status_code=1)

    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="plot something")
        await _wait_for(lambda: docker_client.containers.run.call_count == 1)
        await _wait_for(lambda: (tmp_path / job_id).exists())
        release.set()
        result = await _poll_until_done(backend, job_id=job_id)

    assert result.status == JobStatus.FAILED
    assert result.error is not None
    assert "ZeroDivisionError: division by zero" in result.error
    assert "container exit code 1" in result.error
    container.logs.assert_called_once_with(tail=200)
    # Read BEFORE the removal, not after -- afterwards there is nothing to read.
    called = [name for name, _, _ in container.method_calls]
    assert called.index("logs") < called.index("remove")


async def test_an_oversized_container_log_is_truncated_in_the_error(
    backend, docker_client, tmp_path,
):
    """A `JobResult.error` ends up in an audit row and potentially in a reply.
    LLM-authored code can emit an arbitrarily large traceback, so the tail
    that rides out on the error is bounded -- `tail=200` bounds the lines,
    `MAX_ERROR_LOG_CHARS` bounds the characters."""
    container = docker_client.containers.run.return_value
    container.logs.return_value = b"x" * 50_000
    docker_client.containers.get.return_value = container
    release = _hold_container_open(docker_client, status_code=1)

    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="print a lot")
        await _wait_for(lambda: docker_client.containers.run.call_count == 1)
        await _wait_for(lambda: (tmp_path / job_id).exists())
        release.set()
        result = await _poll_until_done(backend, job_id=job_id)

    assert result.status == JobStatus.FAILED
    assert result.error is not None
    assert "(truncated)" in result.error
    assert len(result.error) < MAX_ERROR_LOG_CHARS + 500


async def test_unreadable_container_logs_do_not_break_the_result(
    backend, docker_client, tmp_path,
):
    """Log capture is diagnostics. A docker client that refuses `logs()` must
    cost the caller the evidence, never the result -- turning a reportable
    failure into an unreportable one is the exact shape this whole batch of
    fixes exists to prevent."""
    container = docker_client.containers.run.return_value
    container.logs.side_effect = RuntimeError("daemon says no")
    docker_client.containers.get.return_value = container
    release = _hold_container_open(docker_client, status_code=1)

    with aioresponses() as mocked:
        mocked.post(f"{DROP_URL}/api/tokens", payload={"token": "scoped-token"})
        job_id = await backend.launch(task="write a haiku")
        await _wait_for(lambda: docker_client.containers.run.call_count == 1)
        await _wait_for(lambda: (tmp_path / job_id).exists())
        release.set()
        result = await _poll_until_done(backend, job_id=job_id)

    assert result.status == JobStatus.FAILED
    assert result.error is not None
    assert "without producing a result" in result.error
    container.remove.assert_called_once_with(force=True)
