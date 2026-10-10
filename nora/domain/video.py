"""A video job's typed request (design/video-generation, DEC at merge).

A clip takes one to three minutes to render, so `video_create` starts a job and returns; the
finished clip reaches the room later through the deferred-job outbox (`compute_jobs`,
`ComputeJobPoller`, `compute_occasion`). This module is the provider-neutral vocabulary both
sides share: what the tool asks for (`VideoRequest`, `LaunchRequest.payload`), what a backend
answers when it is asked to start one (`VideoStart`), and the kind its rows carry.

The reference images travel as BYTES in memory only: they are needed once, by the create call.
What `compute_jobs.payload` keeps is `VideoRequest.as_payload()`, the prompt and the sha256 of
each reference (the custody handle it was resolved from), never the image itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: The kind a video job's row carries (`compute_jobs.kind`); the poller resolves the backend by it.
VIDEO_KIND = "video"

#: What `nora_video_jobs_total{outcome}` counts: a create that `started`, was `refused` or was
#: `unavailable`, and how a started clip ended (`succeeded`, or a FAILED poll's
#: `failure_reason`).
VIDEO_OUTCOMES: tuple[str, ...] = (
    "started", "refused", "unavailable", "succeeded", "failed", "cancelled", "timeout",
    "expired", "no_url",
)

#: What `nora_video_clip_keep_total{outcome}` counts: fetching a finished clip's bytes from the
#: provider's link (`downloaded`, a transient `retry`, the permanent `too_large` / `not_mp4`,
#: and `gave_up` when retries ran past their deadline), then keeping them in Nora's own storage
#: (`stored`, or `unstored` when the blob store or the media index refused or is not wired).
CLIP_KEEP_OUTCOMES: tuple[str, ...] = (
    "downloaded", "retry", "too_large", "not_mp4", "gave_up", "stored", "unstored",
)


@dataclass(frozen=True)
class VideoReference:
    """One reference image Nora already holds: a photo sent in this conversation, or her
    canonical picture (`ImageReference`'s custody rule). `data` is the blob's bytes."""

    sha256: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class VideoRequest:
    """What `video_create` asks for. Resolution, duration, ratio and model are the
    deployment's (`[video_gen]`), never the model's: the backend applies them."""

    prompt: str
    references: tuple[VideoReference, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        """JSON-shaped, for `compute_jobs.payload`: the prompt and each reference's sha256."""
        return {
            "prompt": self.prompt,
            "reference_sha256": [r.sha256 for r in self.references],
        }


@dataclass(frozen=True)
class VideoStart:
    """What a video backend answers when asked to start a job.

    `started` carries `job_id` (what the row's `backend_job_id` holds and the poller polls
    with) and `params`, the settings the backend actually sent (for the row's payload, so a
    clip can be traced to what produced it). Otherwise no task id came back: `refused` when
    the provider turned the request away (`reason` says why, in plain words), `unavailable`
    when it could not be reached or answered something unreadable.

    `uncertain` is True for an `unavailable` whose request may have REACHED the provider (it
    timed out or broke after sending): a clip may be rendering and billed with no task id to
    follow. The desk records it against the cap rather than let a retry spend again.
    """

    status: Literal["started", "refused", "unavailable"]
    job_id: str = ""
    reason: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    uncertain: bool = False


#: The one `failure_reason` that is known to have cost nothing: the provider's own `failed`
#: task (measured 2026-10-02, a failed task carries an empty `usage`).
FREE_FAILURE = "failed"


def clip_counts(*, failure_reason: str | None, result_url: str | None) -> bool:
    """Whether a video row counts against the daily cap. Only a task the provider failed
    (`FREE_FAILURE`) is free, and the row's STATE cannot say that: the poller marks a row
    `done` whenever the room was told, failure or not. So it is read from `failure_reason`.
    Every other row counts: pending, delivered, timed out, lost mid-delivery (a clip may
    have rendered), or uncertain at create."""
    return bool(result_url) or failure_reason != FREE_FAILURE
