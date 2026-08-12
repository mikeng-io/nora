"""`LocalDockerBackend` — the only `ComputeBackend` adapter (Nora Compute
spec §2). One Docker container per job, bind-mounted workspace, a scoped
per-job Drop credential minted before launch. No FUSE, no sync protocol --
the host filesystem already is the single source of truth once the
container and this process share one machine (the same argument the Nora
Drop spec's §1 makes about storage, applied here to compute).

`launch` returns a `job_id` immediately and starts an `asyncio.Task` that
does the actual container run/wait/cleanup in the background; `poll` just
checks whether that task has finished. This keeps `poll` cheap (no Docker
API call on every poll) and keeps `ComputeBackend`'s own "never raises"
contract easy to hold -- the task's own exception handling is internal, never
surfaced to a caller as a raised exception out of `poll`.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

import aiohttp

# docker-py's `container.wait(timeout=...)` overrun raises
# `requests.exceptions.ReadTimeout`, not the builtin `TimeoutError` -- see the
# except clause below.
import requests.exceptions

from nora.domain.ports import JobResult, JobStatus

log = logging.getLogger("nora.compute")

DEFAULT_MEM_LIMIT = "1g"


class LocalDockerBackend:
    """Satisfies `nora.domain.ports.ComputeBackend`."""

    def __init__(
        self,
        *,
        docker_client: Any,
        drop_internal_url: str,
        drop_master_token: str,
        model_api_key: str,
        model_base_url: str,
        model_name: str,
        sandbox_image: str,
        job_timeout_seconds: int,
        max_concurrent_jobs: int,
        workspace_root: Path,
    ) -> None:
        self._docker = docker_client
        self._drop_internal_url = drop_internal_url.rstrip("/")
        self._drop_master_token = drop_master_token
        # Smith's own LLM credential (Nora Compute spec §3 assumed Smith could
        # call a model without ever specifying HOW it gets one -- a real gap,
        # caught only by a subagent review that actually tried to run this
        # plan's own code and found `agent.py` had no `model=` anywhere).
        # Reuses the SAME OmniRoute-shaped (api_key, base_url, model name)
        # credential Nora herself is configured with
        # (`nora/app/composition.py`'s own `OMNIROUTE_API_KEY` construction)
        # rather than inventing a second model-provider story for Smith.
        self._model_api_key = model_api_key
        self._model_base_url = model_base_url
        self._model_name = model_name
        self._sandbox_image = sandbox_image
        self._job_timeout_seconds = job_timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent_jobs)
        self._workspace_root = workspace_root
        self._tasks: dict[str, asyncio.Task[JobResult]] = {}

    async def launch(self, *, task: str, artifact_id: str | None = None) -> str:
        job_id = f"job_{uuid.uuid4().hex}"
        self._tasks[job_id] = asyncio.ensure_future(
            self._run_job(job_id=job_id, task=task, artifact_id=artifact_id)
        )
        return job_id

    async def poll(self, *, job_id: str) -> JobResult:
        running = self._tasks.get(job_id)
        if running is None:
            return JobResult(status=JobStatus.FAILED, error="unknown job_id")
        if not running.done():
            return JobResult(status=JobStatus.RUNNING)
        return running.result()

    async def _run_job(
        self, *, job_id: str, task: str, artifact_id: str | None,
    ) -> JobResult:
        async with self._semaphore:
            try:
                token = await self._mint_token(artifact_id=artifact_id)
            except Exception as exc:  # noqa: BLE001 - launch failure is a result, not a raise
                log.warning("compute job %s could not mint a drop token: %s", job_id, exc)
                return JobResult(status=JobStatus.FAILED, error="could not obtain a drop token")

            workspace = self._workspace_root / job_id
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / "task.txt").write_text(task)

            try:
                container = await asyncio.to_thread(
                    self._start_container, job_id, token, workspace, artifact_id,
                )
                await asyncio.to_thread(container.wait, timeout=self._job_timeout_seconds)
            except requests.exceptions.ReadTimeout:
                # NOT the builtin `TimeoutError` -- docker-py's `container.wait
                # (timeout=...)` overrun raises `requests.exceptions.
                # ReadTimeout` (confirmed against docker-py's own issue
                # tracker: docker/docker-py#1966, #618, #1950, #2266, #3034,
                # #1374 all report this same shape; `ReadTimeout` does NOT
                # inherit from `TimeoutError`, so catching the builtin here
                # would silently fall through to the generic `except
                # Exception` below instead -- fail-closed either way (the job
                # still ends up FAILED and killed), but with the wrong error
                # message, which is why this is worth getting right rather
                # than leaving the wrong exception type "harmlessly" caught
                # by the broader clause.
                await asyncio.to_thread(self._kill_and_remove, job_id)
                return JobResult(
                    status=JobStatus.FAILED, error="job exceeded its wall-clock timeout",
                )
            except Exception as exc:  # noqa: BLE001
                await asyncio.to_thread(self._kill_and_remove, job_id)
                return JobResult(status=JobStatus.FAILED, error=f"container run failed: {exc}")
            else:
                await asyncio.to_thread(self._kill_and_remove, job_id)

            return self._read_result(workspace)

    def _start_container(
        self, job_id: str, token: str, workspace: Path, artifact_id: str | None,
    ) -> Any:
        return self._docker.containers.run(
            self._sandbox_image,
            detach=True,
            network="nora-compute",
            volumes={str(workspace): {"bind": "/workspace", "mode": "rw"}},
            environment={
                "DROP_JOB_TOKEN": token,
                "DROP_INTERNAL_URL": self._drop_internal_url,
                "TASK_FILE": "/workspace/task.txt",
                "ARTIFACT_ID": artifact_id or "",
                "SMITH_MODEL_API_KEY": self._model_api_key,
                "SMITH_MODEL_BASE_URL": self._model_base_url,
                "SMITH_MODEL_NAME": self._model_name,
            },
            mem_limit=DEFAULT_MEM_LIMIT,
            name=job_id,
        )

    def _kill_and_remove(self, job_id: str) -> None:
        """A killed-or-finished container is always removed -- a timed-out
        job must not keep running past its own guardrail (spec §5), and a
        finished one shouldn't accumulate as dead weight. `force=True`
        tolerates "already exited" without raising."""
        try:
            container = self._docker.containers.get(job_id)
            container.remove(force=True)
        except Exception as exc:  # noqa: BLE001 - already gone is fine
            log.debug("compute job %s container already gone: %s", job_id, exc)

    def _read_result(self, workspace: Path) -> JobResult:
        url_path = workspace / "url.txt"
        summary_path = workspace / "summary.txt"
        if not url_path.exists() or not summary_path.exists():
            return JobResult(
                status=JobStatus.FAILED, error="job exited without producing a result",
            )
        return JobResult(
            status=JobStatus.SUCCEEDED,
            url=url_path.read_text().strip(),
            summary=summary_path.read_text().strip(),
        )

    async def _mint_token(self, *, artifact_id: str | None) -> str:
        scope = "update" if artifact_id is not None else "create"
        body: dict[str, Any] = {"scope": scope, "ttl_seconds": self._job_timeout_seconds}
        if artifact_id is not None:
            body["artifact_id"] = artifact_id
        async with aiohttp.ClientSession() as session, session.post(
            f"{self._drop_internal_url}/api/tokens",
            headers={"Authorization": f"Bearer {self._drop_master_token}"},
            json=body,
        ) as response:
            if response.status != 200:
                raise ConnectionError(f"drop refused token mint (http {response.status})")
            data: Any = await response.json()
            return str(data["token"])
