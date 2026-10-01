# Video Generation — MiniMax-H3, 480P, 15 s, with a reference image

**Status:** Proposed (nothing built)
**Date:** 2026-10-02
**DEC:** to be numbered from `origin/master` at merge time (`nora-decision`)
**Operator decision (2026-10-02):** one model, one resolution, one duration. MiniMax-H3, 480P, 15 s, with reference images supported. Hailuo, 768P and 2K are out of scope.

## 1. What this is

Nora gains a `generate_video` tool. A clip takes one to three minutes to render, so the tool does not wait for it: it starts a job and returns, and the finished clip is delivered to the room later through the deferred-job outbox (`compute_jobs`, `ComputeJobPoller`, `compute_occasion`) that design/sandbox-runtime decision 4 kept for exactly this purpose. The provider is MiniMax CN (`api.minimaxi.com`, key `MINIMAX_CN_API_KEY`), the same account that already serves her speech.

This does not need `delegate_task` (issue #22). That design is a background sub-agent that researches and writes. A video is one external job with no model running while it renders. The outbox has no adapter today (`composition.py`: "NO adapter ships"); this document adds one.

## 2. The fixed settings

Every value is read from `config.toml`, never from code, and the shipped default is the operator's decision.

```toml
[video_gen]
enabled = false
provider = "minimax_cn"
model = "MiniMax-H3"        # fixed; see §5 on 480P
resolution = "480P"
duration_seconds = 15
ratio = "3:4"               # explicit; `adaptive` is refused for text-only AND reference tasks
allow_reference_images = false
max_clips_per_day = 1       # hard cap, see §4
```

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
