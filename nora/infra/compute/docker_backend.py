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
surfaced to a caller as a raised exception out of `poll`. That contract is
enforced by TWO explicit barriers rather than by inspection of every
statement: `_run_job` wraps the whole job body, and `poll` guards the
`.result()` read (which can still raise `CancelledError`, a `BaseException`
the first barrier cannot see).
"""

from __future__ import annotations

import asyncio
import json
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

# How much of a failed container's own output survives it. `tail` bounds what
# Docker hands back; `MAX_ERROR_LOG_CHARS` bounds what reaches a `JobResult
# .error` string, which ends up in an audit row and potentially in a reply --
# an unbounded traceback from LLM-authored code must not be able to blow
# either up. The full bounded tail is also logged, so nothing is lost to
# whoever is reading `nora.compute`'s logs.
LOG_TAIL_LINES = 200
MAX_ERROR_LOG_CHARS = 2000


def _unexpected(exc: BaseException) -> str:
    """An unexpected failure's error string. Carries the exception TYPE, not
    just its message: `[Errno 20] Not a directory: '/var/...'` alone leaves a
    reader guessing, `NotADirectoryError: [Errno 20] ...` does not -- and this
    branch exists precisely for failures nobody anticipated."""
    return f"compute job failed to run: {type(exc).__name__}: {exc}"


def _exit_code(wait_result: Any) -> Any:
    """docker-py's `container.wait()` returns `{"StatusCode": N, "Error": ...}`.
    That return value used to be discarded entirely, which threw away the one
    number that says whether the sandboxed process died or exited cleanly
    without producing output. Returns None when the shape is anything else."""
    if isinstance(wait_result, dict):
        return wait_result.get("StatusCode")
    return None


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
        google_maps_api_key: str,
        sandbox_image: str,
        job_timeout_seconds: int,
        max_concurrent_jobs: int,
        mem_limit: str,
        nano_cpus: int,
        tracing_enabled: bool,
        tracing_endpoint: str,
        tracing_headers: str,
        workspace_root: Path,
        workspace_host_root: Path,
    ) -> None:
        self._docker = docker_client
        self._drop_internal_url = drop_internal_url.rstrip("/")
        self._drop_master_token = drop_master_token
        # Smith's own LLM credential (Nora Compute spec §3 assumed Smith could
        # call a model without ever specifying HOW it gets one -- a real gap,
        # caught only by a subagent review that actually tried to run this
        # plan's own code and found `agent.py` had no `model=` anywhere).
        # Reuses the same OmniRoute-shaped (api_key, base_url, model name)
        # TRIPLE FORMAT Nora herself is configured with
        # (`nora/app/composition.py`'s own `OMNIROUTE_API_KEY` construction),
        # so Smith constructs its model client exactly the way Nora does,
        # rather than inventing a second model-provider story for Smith.
        # The SHAPE is shared; the VALUE is not necessarily Nora's own literal
        # secret -- this class takes `model_api_key` as an opaque parameter,
        # and whether the caller passes Nora's own key or a separate,
        # Smith-scoped one is decided by whoever constructs this class
        # (composition wiring, a later phase), not by this class itself.
        # `nora/compute/smith/agent.py`'s own docstring says the same thing
        # from Smith's side ("reused SHAPE, not a shared secret").
        self._model_api_key = model_api_key
        self._model_base_url = model_base_url
        self._model_name = model_name
        # `render_map`'s own Google Static Maps API key (nora/compute/smith/
        # skills/maps/render.py) -- a later skill this class's Phase 2
        # constructor didn't anticipate; threaded through the same way as
        # the model credential above, as an opaque per-job container env var.
        self._google_maps_api_key = google_maps_api_key
        self._sandbox_image = sandbox_image
        self._job_timeout_seconds = job_timeout_seconds
        self._mem_limit = mem_limit
        self._nano_cpus = nano_cpus
        # Already-resolved values, not `*_env` names -- Smith's container has
        # no access to Nora's own `.env` to resolve a name from itself, the
        # same reason `model_api_key`/`google_maps_api_key` above are opaque
        # secrets rather than env-var names too.
        self._tracing_enabled = tracing_enabled
        self._tracing_endpoint = tracing_endpoint
        self._tracing_headers = tracing_headers
        self._semaphore = asyncio.Semaphore(max_concurrent_jobs)
        # TWO roots for ONE directory, and the distinction is load-bearing.
        # `workspace_root` is where THIS process reads and writes (`task.txt`
        # out, `url.txt`/`summary.txt` back) — a path in this process's own
        # filesystem. `workspace_host_root` is the same directory as the HOST
        # Docker daemon sees it, and it is the ONLY thing that may be used as a
        # bind-mount SOURCE: the daemon resolves a bind source on the host, not
        # inside whatever container asked for it. When Nora runs in a container
        # with `./data:/app/data`, the two are `/app/data/compute-workspaces`
        # and `<repo>/data/compute-workspaces` — different strings for the same
        # bytes. Passing the container-side one to the daemon does not error; it
        # silently creates an empty host directory and the job produces nothing.
        # (Running Nora directly on the host, the two are simply equal.)
        self._workspace_root = workspace_root
        self._workspace_host_root = workspace_host_root
        self._tasks: dict[str, asyncio.Task[JobResult]] = {}

    async def launch(
        self, *, task: str, artifact_id: str | None = None,
        context: list[dict[str, Any]] | None = None,
    ) -> str:
        job_id = f"job_{uuid.uuid4().hex}"
        self._tasks[job_id] = asyncio.ensure_future(
            self._run_job(job_id=job_id, task=task, artifact_id=artifact_id, context=context)
        )
        return job_id

    async def poll(self, *, job_id: str) -> JobResult:
        running = self._tasks.get(job_id)
        if running is None:
            return JobResult(status=JobStatus.FAILED, error="unknown job_id")
        if not running.done():
            return JobResult(status=JobStatus.RUNNING)
        # `_run_job` already converts every exception into a FAILED result, so
        # in practice `.result()` returns. This is the SECOND barrier for the
        # port's "NEVER raises" contract, and it is not redundant: a task that
        # was CANCELLED (loop shutdown, `task.cancel()`) re-raises
        # `CancelledError` from `.result()`, and `CancelledError` derives from
        # `BaseException`, so `_run_job`'s own `except Exception` never sees it.
        try:
            return running.result()
        except asyncio.CancelledError:
            return JobResult(status=JobStatus.FAILED, error="compute job was cancelled")
        except BaseException as exc:  # a raise out of poll breaks the port
            log.exception("compute job %s raised out of its task", job_id)
            return JobResult(status=JobStatus.FAILED, error=_unexpected(exc))

    async def _run_job(
        self, *, job_id: str, task: str, artifact_id: str | None,
        context: list[dict[str, Any]] | None,
    ) -> JobResult:
        """The port's "NEVER raises" contract (`ComputeBackend`'s docstring:
        a backend that cannot even start a job "reports that as a `poll`
        result ... not an exception out of `launch`"), enforced in ONE place
        rather than trusted to every statement below it.

        This wrapper exists because the inner body's non-container work is
        just as capable of raising as the container run is, and used not to
        be covered: a review pointed `workspace_root` at a FILE, and
        `workspace.mkdir(...)` raised `NotADirectoryError` straight out of
        `poll` into the caller -- `mkdir`, the `task.txt` write and the final
        `_read_result` all sat outside the only try/except in the method.
        """
        try:
            return await self._execute_job(
                job_id=job_id, task=task, artifact_id=artifact_id, context=context,
            )
        except Exception as exc:  # a raise out of poll breaks the port
            log.exception("compute job %s failed before it could report a result", job_id)
            return JobResult(status=JobStatus.FAILED, error=_unexpected(exc))

    async def _execute_job(
        self, *, job_id: str, task: str, artifact_id: str | None,
        context: list[dict[str, Any]] | None,
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
            # A second file, never folded into `task`'s prose (context-channel
            # spec §4) — `[]` when nothing was given, so Smith's own reader
            # never needs an `exists()` branch for the common "no context" case.
            (workspace / "context.json").write_text(json.dumps(context or []))

            exit_code: Any = None
            try:
                container = await asyncio.to_thread(
                    self._start_container,
                    job_id, token, self._workspace_host_root / job_id, artifact_id,
                )
                wait_result = await asyncio.to_thread(
                    container.wait, timeout=self._job_timeout_seconds,
                )
                exit_code = _exit_code(wait_result)
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
                logs = await asyncio.to_thread(self._kill_and_remove, job_id)
                return self._failed(
                    job_id, "job exceeded its wall-clock timeout", exit_code=None, logs=logs,
                )
            except Exception as exc:  # noqa: BLE001
                logs = await asyncio.to_thread(self._kill_and_remove, job_id)
                return self._failed(
                    job_id, f"container run failed: {exc}", exit_code=None, logs=logs,
                )
            else:
                logs = await asyncio.to_thread(self._kill_and_remove, job_id)

            result = self._read_result(workspace)
            if result.status is not JobStatus.FAILED:
                return result
            # The expected failure mode of this sandbox is "LLM-authored code
            # went wrong", and the container is already gone by now -- its own
            # output is the only evidence of what went wrong, so it rides out
            # on the result rather than dying with the container.
            return self._failed(
                job_id, result.error or "job failed", exit_code=exit_code, logs=logs,
            )

    def _failed(
        self, job_id: str, message: str, *, exit_code: Any, logs: str,
    ) -> JobResult:
        """Every FAILED result this backend produces after a container existed
        goes through here, so a failure's evidence (container exit code and a
        bounded tail of its own output) is both LOGGED and carried on the
        result -- `container.remove(force=True)` used to destroy all of it."""
        detail = message
        if exit_code is not None:
            detail = f"{detail} (container exit code {exit_code})"
        log.warning(
            "compute job %s failed: %s; container log tail:\n%s", job_id, detail, logs or "(none)",
        )
        if logs:
            detail = f"{detail}; container log tail: {logs}"
        return JobResult(status=JobStatus.FAILED, error=detail)

    def _start_container(
        self, job_id: str, token: str, host_workspace: Path, artifact_id: str | None,
    ) -> Any:
        """`host_workspace`, never `self._workspace_root / job_id` — see the
        two-roots comment in `__init__`. The parameter is named for the side of
        the boundary it belongs to precisely so a future edit cannot quietly
        hand this the container-side path again.

        `network="nora-compute"` is a LITERAL, matched by `docker-compose.yml`'s
        own `name: nora-compute` on that network (Compose would otherwise
        prefix it with the project name and this call would raise `NotFound`).
        The compose file is the side that adapts; this string is the fixed
        point, and `tests/infra/compute/test_docker_backend.py` pins it."""
        return self._docker.containers.run(
            self._sandbox_image,
            detach=True,
            network="nora-compute",
            # Launched ad-hoc via docker.from_env(), never through Compose --
            # so it does NOT inherit the `extra_hosts: ["host.docker.internal:
            # host-gateway"]` docker-compose.yml gives Nora's own container.
            # Without this, `SMITH_MODEL_BASE_URL`/the OTLP endpoint (both
            # `http://host.docker.internal:...`) fail to resolve at all --
            # confirmed live: the first real job crashed with
            # openai.APIConnectionError before producing any output. Same
            # target Compose's own entry names, added explicitly here since
            # this container was never subject to Compose's own config.
            extra_hosts={"host.docker.internal": "host-gateway"},
            volumes={str(host_workspace): {"bind": "/workspace", "mode": "rw"}},
            environment={
                "DROP_JOB_TOKEN": token,
                "DROP_INTERNAL_URL": self._drop_internal_url,
                "TASK_FILE": "/workspace/task.txt",
                "ARTIFACT_ID": artifact_id or "",
                "SMITH_MODEL_API_KEY": self._model_api_key,
                "SMITH_MODEL_BASE_URL": self._model_base_url,
                "SMITH_MODEL_NAME": self._model_name,
                "GOOGLE_MAPS_API_KEY": self._google_maps_api_key,
                "SMITH_OTLP_ENABLED": "1" if self._tracing_enabled else "0",
                "SMITH_OTLP_ENDPOINT": self._tracing_endpoint,
                "SMITH_OTLP_HEADERS": self._tracing_headers,
            },
            mem_limit=self._mem_limit,
            nano_cpus=self._nano_cpus,
            name=job_id,
        )

    def _kill_and_remove(self, job_id: str) -> str:
        """A killed-or-finished container is always removed -- a timed-out
        job must not keep running past its own guardrail (spec §5), and a
        finished one shouldn't accumulate as dead weight. `force=True`
        tolerates "already exited" without raising.

        Returns a bounded tail of the container's own output, read BEFORE the
        removal that would otherwise destroy it. Removal is what makes this
        the last possible moment to read it, and this sandbox runs code an LLM
        wrote: throwing the logs away made every failure undiagnosable."""
        try:
            container = self._docker.containers.get(job_id)
        except Exception as exc:  # noqa: BLE001 - already gone is fine
            log.debug("compute job %s container already gone: %s", job_id, exc)
            return ""
        logs = self._capture_logs(job_id, container)
        try:
            container.remove(force=True)
        except Exception as exc:  # noqa: BLE001 - already gone is fine
            log.debug("compute job %s container could not be removed: %s", job_id, exc)
        return logs

    def _capture_logs(self, job_id: str, container: Any) -> str:
        """Never raises and never returns unbounded text -- log capture is
        diagnostics, so it must not be able to turn a reportable failure into
        an unreportable one."""
        try:
            raw = container.logs(tail=LOG_TAIL_LINES)
        except Exception as exc:  # noqa: BLE001 - no logs is a worse report, not a failure
            log.debug("compute job %s logs unavailable: %s", job_id, exc)
            return ""
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace").strip()
        elif isinstance(raw, str):
            text = raw.strip()
        else:
            # docker-py returns bytes for a non-streaming `logs()`; anything
            # else is a client we don't understand, and guessing at its repr
            # would put noise where evidence belongs.
            return ""
        if len(text) > MAX_ERROR_LOG_CHARS:
            text = f"...(truncated){text[-MAX_ERROR_LOG_CHARS:]}"
        return text

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
