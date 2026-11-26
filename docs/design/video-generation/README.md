# Video generation

Nora creates a short video from words and, when permitted, media she already
holds. Creation is asynchronous: the turn starts one provider job and returns,
and the finished clip later reaches the same room as an attachment.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fvideo-generation%2F%22)

## Parts

- **`video_create`** is the turn-facing boundary. It binds the request to the
  current room and requester; the model supplies creative intent, never
  deployment settings or a destination.
- **The video desk** owns spend admission. It enforces one clip per turn and a
  durable daily allowance per room before any provider call.
- **The video backend** owns the provider vocabulary. It starts and polls a
  remote job, normalizes references, and reports a typed outcome without
  leaking provider details into the agent layer.
- **The compute outbox and poller** own persistence, recovery and deferred
  delivery. A video is one kind of external job, not a reasoning sub-agent.
- **Media custody and the media library** resolve permitted inputs and retain
  finished clips. The room's media window and Nora's canonical self-image are
  the only reference sources; arbitrary external URLs are not.
- **The video-generation skill** owns creative judgement: whose image or voice
  may be used, which mode fits the request, and how the prompt describes it.

## Job and spend invariants

The desk reads its allowance from durable job state, so a restart cannot reset
it. The allowance is per room and local day, checked atomically with creation,
and unavailable when the count cannot be read. A repeated call in one turn
returns the first outcome rather than spending again.

A provider-refused create spends no allowance. A request that may have reached
the provider without returning a followable handle is recorded as uncertain,
counts against the allowance, and is never retried automatically. Only a task
the provider explicitly failed is free; timeouts, expiry and lost delivery may
still have produced a billed clip. Accepted work is persisted for the poller,
and a video job survives process recovery because its provider handle and
launch time are durable.

Synchronous refusal is preferred to promising work that cannot start. Errors
that only the provider's later task state can reveal are delivered as failures,
with the provider error retained for diagnosis.

## Media boundaries

A reference is a held photo, clip or voice note with a declared purpose:
identity or setting, motion or camera, or voice or sound. Each media kind has
its own permission gate. A person's face and voice receive the same consent
boundary: a voice or likeness is used only for the use its owner requested.

Reference mode and first-frame mode are mutually exclusive. References support
a newly written scene; a first frame is the photograph from which the clip
opens. A first frame is always a photo. Deployment settings choose the model,
shape and duration; the model cannot override them.

Input bytes exist in memory for preparation and creation. Durable job state
keeps the prompt, custody hashes and settings actually sent, never the media
bytes. Clip and sound references are normalized before upload so provider
limits are enforced locally and source metadata does not cross the boundary.

## Completion and delivery

The poller does not download a finished clip until it holds the room's delivery
slot, so a blocked room costs no fetch. A valid clip is stored in Nora's media
library and attached to her message by code. This makes the result available
to later media lookup and sending after the provider's signed URL expires.

A transient fetch failure remains pending for a bounded retry window. Invalid
or oversized content, or exhaustion of that window, falls back to the signed
link. Code attaches that link rather than asking the model to copy it, and its
signature is kept out of logs. The provider URL never becomes an informal part
of her authored answer.

## Why this shape

A render is slow external work with no model running while it waits. The
compute outbox already provides durable deferred delivery, so a separate
background reasoning worker would add coordination without adding capability.
A provider-neutral desk and typed request keep cost, custody and room rules
independent of one vendor's API.

Attachment-first delivery is preferable to link-only delivery because the clip
becomes part of Nora's own media custody and remains usable after provider
expiry. The signed link is the bounded degradation path, not the primary
product surface.

[Prompting contract](prompting.md)
