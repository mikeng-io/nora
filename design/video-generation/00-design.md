---
type: design
title: "Video generation: MiniMax-H3, 480P, 15 s, with a reference image"
description: "Built 2026-10-09, ships off. `video_create` starts one clip on MiniMax CN as a deferred job in the compute outbox and returns; the poller delivers the finished clip into the room as a link (signed URL, 24 h) attached by code. Hard daily cap read from compute_jobs before every create; reference images only from custody, behind a privacy gate. §9 is what was built and what it changed in this proposal."
tags: [nora, video, minimax, compute-jobs, outbox, tool, skill]
timestamp: '2026-10-09'
status: built
governance: operator-directed
edges:
  - {dst: ../subagent/00-design.md, rel: relates_to, provenance: asserted}
---

# Video Generation — MiniMax-H3, 480P, 15 s, with a reference image

**Status:** Built 2026-10-09, shipped `enabled = false` (§9). Sections 1 to 8 are the proposal as
written on 2026-10-02; where the build settled one of its open points, §9 says how.
**Date:** 2026-10-02 (proposal), 2026-10-09 (built)
**DEC:** DEC-304 (numbered from `origin/master` at merge time, `nora-decision`)
**Operator decision (2026-10-02):** one model, one resolution, one duration. MiniMax-H3, 480P, 15 s, with reference images supported. Hailuo, 768P and 2K are out of scope.

## 1. What this is

Nora gains a `generate_video` tool. A clip takes one to three minutes to render, so the tool does not wait for it: it starts a job and returns, and the finished clip is delivered to the room later through the deferred-job outbox (`compute_jobs`, `ComputeJobPoller`, `compute_occasion`) that design/sandbox-runtime decision 4 kept for a future `delegate_task`. The path is generic (launch now, deliver later, survive a restart), so a video job can use it. The provider is MiniMax CN (`api.minimaxi.com`, key `MINIMAX_CN_API_KEY`), the same account that already serves her speech.

This does not need `delegate_task` (issue #22). That design is a background sub-agent that researches and writes. A video is one external job with no model running while it renders. The outbox has no adapter today (`composition.py`: "NO adapter ships"); this document adds one. Three fit gaps are real and are settled when it is built: `ComputeBackend.launch(task, artifact_id, context)` has no slot for a ratio or reference images, `composition.py` holds a single `compute_backend` so a later `delegate_task` adapter would collide with this one, and the delivered message is a summary plus a URL, which fits link delivery (§6).

## 2. The fixed settings

Every value is read from `config.toml`, never from code, and the shipped default is the operator's decision.

```toml
[video_gen]
enabled = false
provider = "minimax_cn"
resolution = "480P"
duration_seconds = 15
ratio = "3:4"               # explicit; `adaptive` is refused for text-only AND reference tasks
allow_reference_images = false
max_clips_per_day = 1       # hard cap, see §4

[video_gen.minimax_cn]
model = "MiniMax-H3"        # fixed; see §7 on 480P
api_key_env = "MINIMAX_CN_API_KEY"   # resolved by NAME from the container env (DEC-8)
```

The shape mirrors `[image_gen]` and `[image_gen.xai]`: provider-agnostic settings at the top, the provider's model and credential name in its own sub-table. The adapter also needs `[compute] enabled = true`, because the poller is only built with a `ComputeBackend`.

## 3. API shape (read from the vendor docs and measured 2026-10-02)

| Step | Call |
|---|---|
| Create | `POST /v2/video_generation` with `model`, `content[]`, `resolution`, `duration`, `ratio`, optional `extra`, `callback_url` |
| Poll | `GET /v2/query/video_generation/{task_id}`; `status` is `queued`, `running`, `succeeded`, `failed` or `cancelled` |
| Result | `task.content.url`, a signed Aliyun OSS link valid for 24 hours; `usage` reports `total_seconds`, `output_seconds` and `input_image_count` |
| Failure | `status: failed` with `task.error {code, message}`. Create returns a `task_id` even for a request that is then refused, so a bad parameter surfaces seconds later in the poll and the adapter must read `error` from the task, not only the create response |
| Retention | a task id can be queried for 7 days |

Measured on the plan: latency 52 to 178 s per clip. Failed tasks carry an empty `usage` and cost nothing; one failed 11 s after creation with `aspect ratio "adaptive" is not supported for task ref2va`.

## 4. Cost

Measured on the CN plan's weekly `general` bucket (`GET /v1/token_plan/remains`, integer percent). No video bucket exists, so a clip draws from the pool that Nora's turns and Hermes also use.

| Clip | Weekly quota |
|---|---|
| H3 480P, 4 s | 3% |
| H3 480P, 4 s, one reference image | 3% (146 s; `input_image_count: 1`), the same as without a reference |
| H3 768P, 8 s | 13% (260,392 completion tokens) |
| **H3 480P, 15 s (estimate)** | **about 11%, range 9 to 13%** |

The estimate is 15 s at 0.75% per second, extrapolated from one sample and not measured. At that rate nine clips use the week. A hard cap is therefore part of the design: `max_clips_per_day` is checked in code before a job is created, and a refusal tells the model why in the tool result. The default of one is a placeholder for the operator to set after the first measured 15 s clip.

## 5. Reference image

H3 accepts reference images in the `content` array with `role: "reference_image"`. The vendor limits are: at most 9 images, each 30 MB or less, width and height 256 to 5760 px, aspect ratio (width over height) 0.4 to 2.5. The vendor page says `ratio` may be `adaptive` when a reference image is present; the live API refuses it (`aspect ratio "adaptive" is not supported for task ref2va`, 2026-10-02), so `ratio` is always explicit. `3:4` rendered the portrait canonical picture. An image may be a public URL, `mm_file://{file_id}` or a `data:image/<format>;base64,` URI. Reference images and `first_frame` / `last_frame` are mutually exclusive.

- The adapter sends each reference as a data URI built from a blob Nora already holds (a marker code from the current conversation, or her canonical picture). No external URL is accepted and nothing is uploaded to a public host.
- The canonical picture `assets/nora-canonical-self.png` is 1024 by 1536 px (aspect 0.67) and 2.0 MB, so it satisfies every limit above.
- The privacy gate is the one image generation already uses. A reference leaves the box for a third-party cloud, so `allow_reference_images` defaults to off and is read from config.
- The `video-generation` runtime skill follows `image-generation`: decide whether she appears, attach her canonical picture only then, and treat a photo of a person in the room as that person and not as her.

## 6. Delivery

The poller already delivers through the fire path. The open question is the file, not the mechanism.

The clip lives on `algeng-video-infer.oss-cn-shanghai.aliyuncs.com`. Measured 2026-10-02, neither norty nor the Mac can open a connection to any mainland Aliyun OSS endpoint (Shanghai, Hangzhou, Beijing, Wulanchabu), while Aliyun Hong Kong and Singapore connect. DNS is not the cause: five resolvers return the same address. Both machines share one public address, and the route goes silent in China Unicom's backbone. The operator's own device opens the same signed link. Until this is resolved the adapter cannot download the file, so the options are:

1. **Link delivery.** Send the signed link as text. It works when the viewer's device can reach the host, and it expires after 24 hours.
2. **Relay.** Fetch through a host that can reach mainland Aliyun, then attach the file.
3. **Wait.** The path may recover.

This document recommends link delivery first, because it needs no new infrastructure and the clip can be re-linked for 7 days. The choice is the operator's.

## 7. Unverified, and what would settle each

| Claim | State | Settled by |
|---|---|---|
| H3 accepts 480P | The vendor page lists 768P and 2K for H3 and 480P for H3-Max (5 to 15 s). The live API rendered `MiniMax-H3` at 480P and 4 s, with and without a reference image, on 2026-10-02. | **Measured at 4 s** |
| 15 s at 480P | Not run | One 15 s call, about 11% of the week |
| A reference image at 480P | **Measured**: accepted, rendered in 146 s, 3% | done |
| Whether the plan's Ultra tier caps video at 5 per day | Documented in the Token Plan FAQ, tier of this account unknown | the plan page |
| Why a 768P 4 s task failed | The error was not captured; the `adaptive` refusal above is the one failure read in full | log `task.error` on every failed task |

If the 15 s call is refused for H3, the fallback inside the same decision is `model = "MiniMax-H3-Max"`, which the vendor lists for 480P. That is a config change.

## 8. Not decided here

Video editing and reference video or audio inputs, resolutions other than 480P, durations other than 15 s, and any use of `delegate_task`.

## 9. As built (2026-10-09)

**The tool is `video_create`, not `generate_video`.** ADR 0015 §1 names a new tool `<domain>_<verb>` and `tests/architecture/test_tool_names.py` refuses any other shape; `generate_image` is exempt only because it predates the rule. `create` is the verb `subagent_create` uses for starting a thing that did not exist. Level 3, budget 1, a tag-turn tool only (`TAG_ONLY_TOOLS`), withheld from every surface unless `[video_gen]` is wired (`VIDEO_TOOLS`).

**The three fit gaps of §1.**

- *A single `compute_backend`.* Already resolved on master before this build: `composition.py` holds `compute_backends: dict[str, ComputeBackend]` keyed by `compute_jobs.kind`. The video backend registers under `VIDEO_KIND = "video"` beside the sub-agent's `subagent`.
- *No slot for the ratio or reference images in `launch`.* Also already resolved: `ComputeBackend.launch(request: LaunchRequest)` carries a typed `payload`, and `ComputeJob.payload` persists one. The video payload is `nora.domain.video.VideoRequest` (the prompt and each reference as `sha256` plus bytes). Ratio, resolution, duration and model are the deployment's, applied by the adapter, never chosen by the model. The row keeps `VideoRequest.as_payload()` (the prompt and the reference shas, never the bytes) plus the settings the adapter actually sent. Nothing in the payload is needed to poll: the backend is stateless and its `backend_job_id` is `<task_id>@<launch epoch>`, the provider's handle (queryable 7 days) plus what `poll` needs to give up on a job that never finishes (30 min against a measured 52 to 178 s). So a restart loses nothing, and boot recovery leaves a video row pending (`recover(durable_kinds=...)`, `ComputeJobStore.sweep_pending(keep_kinds=...)`) where it still sweeps an in-memory sub-agent row as lost.
- *A summary plus a URL.* Link delivery, §6 option 1. The finished clip's signed URL rides on the occasion (`Event.attached_urls`) and the fire path attaches it to her message by code (`LinkCarry.attach`): a signed URL is long and one wrong character breaks its signature, so she is told the link is attached and that it stops working 24 hours after the clip was made, and she says that rather than copying it.

**The cap (§4)** is `VideoDesk`: `[video_gen] max_clips_per_day` per local day (`[analytics] tz_name`), counted from `compute_jobs` (`ComputeJobStore.kind_since`), so a restart does not reset it, and checked under one lock before any provider call. Only a task the provider failed is free (a failed task costs nothing, §3), read from the row's `failure_reason` because the poller marks a delivered failure `done`; a clip that timed out, expired or was lost mid-delivery counts, since it may have rendered. A create that broke after it was sent (a read timeout) may have started a billed clip with no task id, so it is written as a terminal `uncertain` row that counts and is never delivered, and the tool tells her not to try again that day. The refusal is the tool's `refused` status, whose note says the day's limit is used and that it resets tomorrow. A second call in one turn returns the first clip.

**Create refusals are synchronous.** The desk calls the backend's `start`, not the port's `launch`, so a create the provider refuses (HTTP error, a `base_resp` refusal, a reference it cannot take) writes no row, spends no cap and answers `unavailable` at once rather than "I'm on it" followed by a failure. A refusal that only the poll reports (`task.error`) is delivered as a failure, and `task.error` is logged on every failed task.

**Reference images (§5)** resolve through the same custody helper `generate_image` uses (`_held_image`: this conversation's media window first, then only the canonical self row), are sent as `data:image/<png|jpeg|webp>;base64,` URIs, and are refused unless `allow_reference_images`. An external URL is never a reference. The `video-generation` runtime skill carries the judgement (does she appear, attach the canonical picture only then, a photo of a person is that person).

**Config (§2)** is as proposed, with two refusals at load: `ratio = "adaptive"` (and any value that is not `W:H`), and `[video_gen] enabled` without `[compute] enabled`. `resolution` accepts `480P` only. Enabled without the key builds nothing and withholds the tool.

**Unverified, needs the operator.** No live call was made while building (a 15 s clip is about 11% of a weekly quota shared with production). Unmeasured: the 15 s clip itself (§7), the exact `content[]` item shape for a reference image (the adapter sends `{"type": "image_url", "image_url": {"url": ...}, "role": "reference_image"}`), the nesting of the query answer (read at the top level or under `task`), whether a signed OSS URL passes R-18 intact in production (a test URL of the documented shape does), and the `tool_selection` routing measurement (`nora-tool` §7).
