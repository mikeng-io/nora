"""One suite, both implementations of `ComputeBackend` (Nora Compute spec §2).

CLAUDE.md: "A port never gains a second implementation — including a test fake
— without a shared contract suite both pass." `nora.testing.FakeComputeBackend`
was that port's sole implementation until `nora.infra.compute.docker_backend
.LocalDockerBackend`, and this is the suite the rule asks for — the same shape
`tests/contract/test_artifact_ingest_contract.py` already has on this branch.

`LocalDockerBackend` talks to two collaborators, and each gets the double this
repo already uses for its kind:

* **Docker** — a `MagicMock` client, matching `tests/infra/compute/
  test_docker_backend.py`'s own fixture. Real container launches need a live
  daemon and are verified by hand (that file's module docstring).
* **`drop`'s token mint** — a real in-process `aiohttp` server, the same choice
  `test_artifact_ingest_contract.py` makes, rather than `aioresponses`. That is
  deliberate: the `aioresponses`/`aiohttp` 3.14 `stream_writer` shim lives in
  `tests/infra/compute/conftest.py` and is scoped to THAT directory, so a
  contract suite reaching for `aioresponses` here would fail on a detail that
  has nothing to do with the port.

The properties both implementations must hold, and which the port's own
docstring states in prose:

1. Each **is** a `ComputeBackend` (`runtime_checkable`, so structural).
2. `launch` returns a **`job_id` string, immediately** — the job runs
   out-of-band.
3. A job in flight polls **`RUNNING`**, and a finished one polls a **terminal**
   status (`SUCCEEDED` with `url`/`summary`, or `FAILED` with `error`), never
   the wrong half of `JobResult`'s fields.
4. **NEVER raises.** An unknown `job_id` is a FAILED result, not a `KeyError` —
   the fail-closed-but-never-crash shape `ComputeBackend`'s docstring promises
   a caller ("so a caller ... never needs a try/except around a call it expects
   to be load-bearing").

Each implementation is driven through a small `_Case` adapter, because "hold
this job in flight, then let it finish" is arranged differently for each (the
fake counts polls; the real backend blocks its container). The ASSERTIONS below
are shared; only the arranging differs — which is the point of a contract
suite, and the reason a fake that "answers every request the same way" would
not survive it.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from nora.domain.ports import ComputeBackend, JobResult, JobStatus
from nora.infra.compute.docker_backend import LocalDockerBackend
from nora.testing import FakeComputeBackend
from tests.infra.compute.test_docker_backend import make_backend

URL = "https://nora.example.net/drop/contract"
SUMMARY = "did the thing"


async def _settle(backend: ComputeBackend, *, job_id: str, attempts: int = 200) -> JobResult:
    """Poll until the job leaves RUNNING, yielding to the event loop between
    attempts. `LocalDockerBackend`'s job is a background `asyncio.Task` whose
    only suspension points are its own `asyncio.to_thread` calls, so a retry
    loop that never awaits would spin forever without letting it progress
    (that file's `_poll_until_done` documents this at length)."""
    result = await backend.poll(job_id=job_id)
    for _ in range(attempts):
        if result.status is not JobStatus.RUNNING:
            return result
        await asyncio.sleep(0)
        result = await backend.poll(job_id=job_id)
    return result


async def _wait_for(predicate, *, attempts: int = 200) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate(), "condition never became true"


class _FakeCase:
    """`FakeComputeBackend` held in flight by its own `polls_before_done`
    lever — the deterministic stand-in for a real timer that the fake's
    docstring says exists for exactly this."""

    def __init__(self) -> None:
        self.backend: ComputeBackend = FakeComputeBackend()

    async def launch_held(self, *, task: str, artifact_id: str | None = None) -> str:
        job_id = await self.backend.launch(task=task, artifact_id=artifact_id)
        self._arrange(job_id, _success(), polls_before_done=1_000)
        return job_id

    async def release_succeeding(self, job_id: str) -> None:
        self._arrange(job_id, _success(), polls_before_done=0)

    async def release_failing(self, job_id: str) -> None:
        self._arrange(job_id, _failure(), polls_before_done=0)

    def _arrange(self, job_id: str, result: JobResult, *, polls_before_done: int) -> None:
        backend: Any = self.backend
        backend.set_result(job_id=job_id, result=result, polls_before_done=polls_before_done)


class _DockerCase:
    """`LocalDockerBackend` held in flight by blocking its container's `wait`
    on a real `threading.Event` (it runs inside `asyncio.to_thread`, so
    blocking it does not block the event loop). Releasing it after writing —
    or not writing — the workspace's `url.txt`/`summary.txt` is what makes the
    same job end SUCCEEDED or FAILED, which is how the real sandbox reports
    its own outcome (spec §2)."""

    def __init__(self, *, drop_internal_url: str, workspace_root: Path) -> None:
        self.docker = MagicMock()
        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.return_value = b""
        self.docker.containers.run.return_value = container
        self.docker.containers.get.return_value = container
        self.workspace_root = workspace_root
        self.backend: ComputeBackend = make_backend(
            docker_client=self.docker,
            workspace_root=workspace_root,
            drop_internal_url=drop_internal_url,
        )
        self._release = threading.Event()

    async def launch_held(self, *, task: str, artifact_id: str | None = None) -> str:
        self.docker.containers.run.return_value.wait.side_effect = (
            lambda **kwargs: self._release.wait(timeout=5) and {"StatusCode": 0}
        )
        job_id = await self.backend.launch(task=task, artifact_id=artifact_id)
        # Wait until the container has actually started: from here the job is
        # parked inside `container.wait` and cannot reach its result read.
        await _wait_for(lambda: self.docker.containers.run.call_count >= 1)
        await _wait_for(lambda: (self.workspace_root / job_id).exists())
        return job_id

    async def release_succeeding(self, job_id: str) -> None:
        workspace = self.workspace_root / job_id
        (workspace / "url.txt").write_text(URL)
        (workspace / "summary.txt").write_text(SUMMARY)
        self._release.set()

    async def release_failing(self, job_id: str) -> None:
        # No url.txt/summary.txt -- the container exits without a result.
        self._release.set()


def _success() -> JobResult:
    return JobResult(status=JobStatus.SUCCEEDED, url=URL, summary=SUMMARY)


def _failure() -> JobResult:
    return JobResult(status=JobStatus.FAILED, error="job exited without producing a result")


async def _mint(request: web.Request) -> web.Response:
    """`drop`'s `POST /api/tokens` (Task 2), enough of it for a launch."""
    if not request.headers.get("Authorization", "").startswith("Bearer "):
        return web.json_response({"error": "forbidden"}, status=403)
    await request.json()
    return web.json_response({"token": "contract-scoped-token", "expires_in": 30})


@pytest.fixture(params=["fake", "docker"])
async def case(request, tmp_path: Path):
    """One working `ComputeBackend` of each kind."""
    if request.param == "fake":
        yield _FakeCase()
        return
    app = web.Application()
    app.router.add_post("/api/tokens", _mint)
    server = TestServer(app)
    await server.start_server()
    try:
        yield _DockerCase(
            drop_internal_url=str(server.make_url("/")).rstrip("/"),
            workspace_root=tmp_path,
        )
    finally:
        await server.close()


def test_both_implementations_are_compute_backends(tmp_path: Path):
    fake = FakeComputeBackend()
    docker = make_backend(docker_client=MagicMock(), workspace_root=tmp_path)
    assert isinstance(fake, ComputeBackend)
    assert isinstance(docker, ComputeBackend)


@pytest.mark.parametrize("impl", [FakeComputeBackend, LocalDockerBackend])
@pytest.mark.parametrize(("method", "params"), [
    ("launch", ("task", "artifact_id", "context")),
    ("poll", ("job_id",)),
])
def test_port_methods_are_keyword_only_after_self(impl, method, params):
    """The convention `tests/test_ports.py` pins for every port method, held by
    both implementations — a positional parameter on one of them is a breaking
    change at every call site the other would not catch."""
    signature = inspect.signature(getattr(impl, method)).parameters
    for name in params:
        assert signature[name].kind is inspect.Parameter.KEYWORD_ONLY


async def test_launch_returns_a_job_id_string(case):
    job_id = await case.launch_held(task="write a haiku")
    assert isinstance(job_id, str)
    assert job_id
    await case.release_succeeding(job_id)
    await _settle(case.backend, job_id=job_id)


async def test_two_launches_never_share_a_job_id(case):
    first = await case.launch_held(task="one")
    second = await case.launch_held(task="two")
    assert first != second
    # Drain both: a job left held is a real container thread (or a fake job
    # stuck RUNNING) outliving the test that started it.
    await case.release_succeeding(first)
    await case.release_succeeding(second)
    await _settle(case.backend, job_id=first)
    await _settle(case.backend, job_id=second)


async def test_a_job_in_flight_polls_running(case):
    """RUNNING carries neither a result nor an error — `JobResult`'s own
    docstring: "`RUNNING` carries neither (there is nothing to report yet)"."""
    job_id = await case.launch_held(task="write a haiku")
    result = await case.backend.poll(job_id=job_id)
    assert result.status is JobStatus.RUNNING
    assert result.url is None
    assert result.summary is None
    assert result.error is None
    await case.release_succeeding(job_id)
    await _settle(case.backend, job_id=job_id)


async def test_a_finished_job_polls_a_terminal_status_with_its_output(case):
    job_id = await case.launch_held(task="write a haiku")
    await case.release_succeeding(job_id)
    result = await _settle(case.backend, job_id=job_id)
    assert result.status is JobStatus.SUCCEEDED
    assert result.url == URL
    assert result.summary == SUMMARY
    assert result.error is None


async def test_a_failed_job_polls_failed_with_an_error(case):
    """The inverse population rule, from the same docstring: `error` only when
    FAILED, and never alongside `url`/`summary`."""
    job_id = await case.launch_held(task="write a haiku")
    await case.release_failing(job_id)
    result = await _settle(case.backend, job_id=job_id)
    assert result.status is JobStatus.FAILED
    assert result.error
    assert result.url is None
    assert result.summary is None


async def test_a_terminal_status_is_stable_across_repeated_polls(case):
    """A caller polls until it sees a terminal status; polling once more must
    not change the answer (or lose the result)."""
    job_id = await case.launch_held(task="write a haiku")
    await case.release_succeeding(job_id)
    first = await _settle(case.backend, job_id=job_id)
    second = await case.backend.poll(job_id=job_id)
    assert second == first


async def test_polling_an_unknown_job_id_is_failed_not_a_raise(case):
    """`ComputeBackend` "NEVER raises" — a caller must not need a try/except
    around a load-bearing poll, so even a job_id that was never launched comes
    back as a result."""
    result = await case.backend.poll(job_id="job_never_launched")
    assert result.status is JobStatus.FAILED
    assert result.error
