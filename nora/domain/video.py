"""A video job's typed request.

A clip takes one to three minutes to render, so `video_create` starts a job and returns; the
finished clip reaches the room later through the deferred-job outbox (`compute_jobs`,
`ComputeJobPoller`, `compute_occasion`). This module is the provider-neutral vocabulary both
sides share: what the tool asks for (`VideoRequest`, `LaunchRequest.payload`), what a backend
answers when it is asked to start one (`VideoStart`), and the kind its rows carry.

The references (photos, clips and voice notes) travel as BYTES in memory only: they are needed
once, by the create call. What `compute_jobs.payload` keeps is `VideoRequest.as_payload()`, the
prompt, the sha256 and kind of each reference and the sha256 of the first frame (the custody
handle each was resolved from), never the media itself.
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


#: What a reference is: a photo keeps a subject or a place, a clip its motion or camera, a voice
#: note or other audio its voice or sound.
ReferenceKind = Literal["image", "video", "audio"]


@dataclass(frozen=True)
class VideoReference:
    """One reference Nora already holds: a photo, clip or voice note sent in this conversation,
    or her canonical picture (`ImageReference`'s custody rule). `data` is the blob's bytes, as
    held; the backend fits a clip or a voice note to the provider's limits before sending.

    `sound` is whether a clip may keep its sound track: the tool sets it only when audio
    references are allowed, since a clip of someone talking lends their voice as a voice note
    does. `mime` and `seconds` are empty until the backend has fitted the bytes (`prepare`):
    set, `data` is what is sent, as it is."""

    sha256: str
    data: bytes = field(repr=False)
    kind: ReferenceKind = "image"
    sound: bool = False
    mime: str = ""
    seconds: float = 0.0


@dataclass(frozen=True)
class VideoRequest:
    """What `video_create` asks for. Resolution, duration, ratio and model are the
    deployment's (`[video_gen]`), never the model's: the backend applies them.

    Two modes, never both in one request (the provider refuses a mix): `references` (photos,
    clips and voice notes, in the order given) keep a subject, a motion or a voice in a scene
    the prompt writes; `first_frame` is a photo that becomes the clip's opening frame, and the
    prompt writes only what moves after it (image-to-video)."""

    prompt: str
    references: tuple[VideoReference, ...] = ()
    first_frame: VideoReference | None = None

    def __post_init__(self) -> None:
        if self.first_frame is not None and self.references:
            raise ValueError("a video request has a first frame or references, never both")
        if self.first_frame is not None and self.first_frame.kind != "image":
            raise ValueError("a first frame is a photo")

    def as_payload(self) -> dict[str, Any]:
        """JSON-shaped, for `compute_jobs.payload`: the prompt, each reference's sha256 and kind
        (in the same order), and the first frame's sha256 (None in reference or words-only
        mode)."""
        return {
            "prompt": self.prompt,
            "reference_sha256": [r.sha256 for r in self.references],
            "reference_kinds": [r.kind for r in self.references],
            "first_frame_sha256": self.first_frame.sha256 if self.first_frame else None,
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
#: task, which carries an empty `usage`.
FREE_FAILURE = "failed"


def clip_counts(*, failure_reason: str | None, result_url: str | None) -> bool:
    """Whether a video row counts against the daily cap. Only a task the provider failed
    (`FREE_FAILURE`) is free, and the row's STATE cannot say that: the poller marks a row
    `done` whenever the room was told, failure or not. So it is read from `failure_reason`.
    Every other row counts: pending, delivered, timed out, lost mid-delivery (a clip may
    have rendered), or uncertain at create."""
    return bool(result_url) or failure_reason != FREE_FAILURE
