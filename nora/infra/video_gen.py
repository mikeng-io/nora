"""`MiniMaxVideoBackend` — `video_create`'s `ComputeBackend`, against MiniMax CN's video API
(design/video-generation §3, §5; DEC at merge).

Provider vocabulary lives here and nowhere above it: the model name, `content[]`, `role:
"reference_image"`, `task.error`, the 480P string. The tool and the poller speak
`nora.domain.video` and the `ComputeBackend` port.

Two calls, both on `api.minimaxi.com` with the CN plan key (`MINIMAX_CN_API_KEY` by NAME,
DEC-8, the convention `nora.infra.tts` and `[speech.tts]` already use):

- **create**, `POST /v2/video_generation` with `model`, `content[]` (the prompt as a text item,
  each reference as an image item with `role: "reference_image"` and a `data:` URI built from a
  blob Nora holds), `resolution`, `duration` and an explicit `ratio`. `start` reads the answer:
  a `task_id` is `started`; an HTTP or vendor (`base_resp`) refusal is `refused`, and nothing
  was spent; a transport failure is `unavailable`.
- **poll**, `GET /v2/query/video_generation/{task_id}`: `queued` / `running` are RUNNING,
  `succeeded` with `task.content.url` is SUCCEEDED, `failed` (with `task.error {code,
  message}`) and `cancelled` are FAILED. A refused parameter surfaces HERE, seconds after a
  create that returned a task id (measured 2026-10-02, the `adaptive` ratio), so `task.error`
  is logged on EVERY failed task: the one 768P failure of that day was never read because
  nothing logged it.

The backend is STATELESS. `backend_job_id` is `<task_id>@<launch epoch seconds>`: the task id
is the provider's (queryable for 7 days), and the launch time is what lets `poll` stop a job
that never finishes (`MAX_WAIT_SECONDS`) after a restart as well as before one. So a video
row survives a restart and boot recovery leaves it pending (`recover(durable_kinds=...)`).

NEVER raises (the port's rule): every failure is a `VideoStart` or a FAILED `JobResult`.

Unverified until the first live call (no call was made building this, by the operator's
instruction: a 15 s clip costs about 11% of a weekly quota shared with production): the exact
item shape of a reference image in `content[]` and the query response's nesting. `_task` reads
the task object at the top level or under `task`, so either shape reads the same.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from nora.domain.ports import JobResult, JobStatus, LaunchRequest
from nora.domain.video import VideoRequest, VideoStart

log = logging.getLogger("nora.video_gen")

class Counter(Protocol):
    """`nora.obs.metrics.Metrics` as this module uses it; injected, since `nora.infra` sits
    below `nora.obs` in the layers contract."""

    def inc(self, name: str, value: float = 1.0, **labels: object) -> None: ...


__all__ = ["MAX_REFERENCE_BYTES", "MAX_WAIT_SECONDS", "MiniMaxVideoBackend"]

#: A clip measured 52 to 178 s on 2026-10-02. A job still queued or running this long after its
#: launch is failed as a timeout rather than polled forever.
MAX_WAIT_SECONDS = 1800.0

#: The vendor's own per-image limit (design §5); refused here so nothing is sent that cannot pass.
MAX_REFERENCE_BYTES = 30 * 1024 * 1024

#: What she is told a finished clip is. The link is MiniMax's signed storage URL, valid 24 hours;
#: code attaches it to the delivery (`LinkCarry.attach`), so she says it rather than copies it.
READY_SUMMARY = (
    "The video clip is ready. Its link is attached to your message for you, so do not write "
    "the link out yourself. Tell them the link stops working 24 hours after the clip was made, "
    "so they should open or save it today."
)

def _image_format(data: bytes) -> str | None:
    """The `data:image/<format>` subtype for bytes the vendor accepts, or None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _task(payload: Any) -> dict[str, Any]:
    """The task object of a query answer, at the top level or under `task`."""
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("task")
    return inner if isinstance(inner, dict) else payload


class MiniMaxVideoBackend:
    """One process-lifetime backend, built by the composition root under `[video_gen]`."""

    def __init__(
        self, *, api_key: str, model: str, resolution: str, duration_seconds: int,
        ratio: str, metrics: Counter, host: str = "https://api.minimaxi.com",
        timeout_seconds: float = 45.0, transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._resolution = resolution
        self._duration = duration_seconds
        self._ratio = ratio
        self._metrics = metrics
        self._host = host.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds)
        self._transport = transport
        self._clock = clock

    @property
    def params(self) -> dict[str, Any]:
        """What every create sends besides the prompt and references."""
        return {
            "model": self._model, "resolution": self._resolution,
            "duration": self._duration, "ratio": self._ratio,
        }

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    def _count(self, outcome: str) -> None:
        self._metrics.inc("nora_video_jobs_total", outcome=outcome)

    # ---- create ----------------------------------------------------------------------------

    async def start(self, request: VideoRequest) -> VideoStart:
        """Create one task. Never raises."""
        if not request.prompt.strip():
            self._count("refused")
            return VideoStart("refused", reason="the prompt was empty")
        content: list[dict[str, Any]] = [{"type": "text", "text": request.prompt}]
        for ref in request.references:
            fmt = _image_format(ref.data)
            if fmt is None:
                self._count("refused")
                return VideoStart("refused", reason="a reference is not a PNG, JPEG or WebP image")
            if len(ref.data) > MAX_REFERENCE_BYTES:
                self._count("refused")
                return VideoStart("refused", reason="a reference image is larger than 30 MB")
            uri = f"data:image/{fmt};base64,{base64.b64encode(ref.data).decode('ascii')}"
            content.append({
                "type": "image_url", "image_url": {"url": uri}, "role": "reference_image",
            })
        body = {"model": self._model, "content": content, "resolution": self._resolution,
                "duration": self._duration, "ratio": self._ratio}
        launched = self._clock()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport,
            ) as client:
                response = await client.post(
                    f"{self._host}/v2/video_generation", headers=self._headers(), json=body,
                )
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as exc:
            # A connect failure never sent the request; anything later (a read timeout, a
            # broken response) may have, and a clip may be rendering with no id to follow.
            sent = not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
            log.warning("video create %s (%s)",
                        "broke after sending" if sent else "could not reach the provider",
                        type(exc).__name__)
            self._count("unavailable")
            return VideoStart("unavailable", reason="the video service could not be reached",
                              uncertain=sent)
        try:
            payload: Any = response.json()
        except ValueError:
            payload = None
        base = (payload or {}).get("base_resp") if isinstance(payload, dict) else None
        base = base if isinstance(base, dict) else {}
        if response.status_code != 200 or base.get("status_code", 0) != 0:
            log.warning(
                "video create refused (http %s, status_code=%s: %s)",
                response.status_code, base.get("status_code"), base.get("status_msg"),
            )
            self._count("refused")
            answered = f"the service answered {response.status_code}"
            return VideoStart("refused", reason=str(base.get("status_msg") or answered))
        task_id = ""
        if isinstance(payload, dict):
            task_id = str(payload.get("task_id") or _task(payload).get("id") or "").strip()
        if not task_id or "@" in task_id:
            log.warning("video create answered 200 with no usable task id")
            self._count("unavailable")
            return VideoStart("unavailable", reason="the video service gave no task id")
        log.info("video task %s created (%d reference image(s))", task_id, len(request.references))
        self._count("started")
        return VideoStart("started", job_id=f"{task_id}@{int(launched)}", params=self.params)

    async def launch(self, *, request: LaunchRequest) -> str:
        """The port's verb. `video_create` calls `start` (it needs to know at once whether
        anything started); this exists so the backend satisfies `ComputeBackend`, and a
        request that did not start is an id whose poll answers FAILED."""
        payload = request.payload
        if not isinstance(payload, VideoRequest):
            return f"not-started-{uuid.uuid4().hex}@0"
        out = await self.start(payload)
        return out.job_id if out.status == "started" else f"not-started-{uuid.uuid4().hex}@0"

    # ---- poll ------------------------------------------------------------------------------

    async def poll(self, *, job_id: str) -> JobResult:
        task_id, _, stamp = job_id.rpartition("@")
        try:
            launched = float(stamp)
        except ValueError:
            launched = 0.0
        if not task_id or task_id.startswith("not-started-") or launched <= 0:
            return JobResult(
                status=JobStatus.FAILED, error="the video was never started",
                failure_reason="failed",
            )
        overdue = self._clock() - launched > MAX_WAIT_SECONDS
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport,
            ) as client:
                response = await client.get(
                    f"{self._host}/v2/query/video_generation/{task_id}", headers=self._headers(),
                )
        except asyncio.CancelledError:
            raise
        except httpx.HTTPError as exc:
            # Transient: the next tick asks again, until the job is overdue.
            log.warning("video poll %s could not reach the provider (%s)",
                        task_id, type(exc).__name__)
            return self._still_running(task_id, overdue)
        if response.status_code == 404:
            log.warning("video task %s is no longer known to the provider", task_id)
            return self._failed("expired", "the video service no longer has this clip")
        if response.status_code != 200:
            log.warning("video poll %s answered http %s", task_id, response.status_code)
            return self._still_running(task_id, overdue)
        try:
            payload: Any = response.json()
        except ValueError:
            log.warning("video poll %s answered something that is not JSON", task_id)
            return self._still_running(task_id, overdue)
        task = _task(payload)
        status = str(task.get("status") or "").lower()
        if status in ("queued", "running", ""):
            return self._still_running(task_id, overdue)
        if status == "succeeded":
            content = task.get("content")
            url = str(content.get("url") or "") if isinstance(content, dict) else ""
            if not url:
                log.warning("video task %s succeeded with no url: %s", task_id, task.get("usage"))
                return self._failed("no_url", "the clip finished but no link came back")
            log.info("video task %s succeeded (usage %s)", task_id, task.get("usage"))
            self._count("succeeded")
            return JobResult(status=JobStatus.SUCCEEDED, url=url, summary=READY_SUMMARY,
                             report=READY_SUMMARY)
        if status == "cancelled":
            log.warning("video task %s was cancelled by the provider", task_id)
            return self._failed("cancelled", "the video service cancelled it")
        error = task.get("error")
        error = error if isinstance(error, dict) else {}
        # Logged on EVERY failed task (design §7): a refused parameter arrives here, not at create.
        log.warning("video task %s %s: code=%s message=%s", task_id, status or "failed",
                    error.get("code"), error.get("message"))
        message = str(error.get("message") or "").strip()
        return self._failed(
            "failed", f"the video service could not make it: {message}" if message
            else "the video service could not make it",
        )

    def _still_running(self, task_id: str, overdue: bool) -> JobResult:
        if not overdue:
            return JobResult(status=JobStatus.RUNNING)
        log.warning("video task %s still unfinished after %.0f s; giving up",
                    task_id, MAX_WAIT_SECONDS)
        return self._failed("timeout", "the clip took too long and was given up on")

    def _failed(self, reason: str, error: str) -> JobResult:
        self._count(reason)
        return JobResult(status=JobStatus.FAILED, error=error, failure_reason=reason)

    async def cancel(self, *, job_id: str) -> None:
        """No cancel call is used: a clip is one short provider job, and `video_create` has no
        cancel verb. A no-op, as the port allows for a job it cannot stop."""
        return None
