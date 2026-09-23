---
type: design
title: "A Third Describer Provider — OpenCode Go, direct, for MiMo v2.6 Flash"
description: "The video describer gains a third transport value. OmniRoute strips `video_url`; `api.kimi.com` serves K3 but not MiMo. OpenCode Go's own endpoint serves MiMo v2.6 Flash with video at 2.7x lower token cost than K3 — and requires a mandatory session header the existing lanes do not have."
tags: [nora, media, provider, opencode-go, mimo, kimi, config, credential]
timestamp: '2026-09-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# A Third Describer Provider — OpenCode Go

## 1. Why a third transport, when one already exists

Nora's `DescriberConfig.provider` selects the transport:

```
nora/config.py:749    provider: Literal["omniroute", "kimi"] = "omniroute"
```

`"kimi"` builds a direct client to `api.kimi.com/coding/v1`, and it exists for one
reason recorded in code (`client.py:396-398`): **OmniRoute drops `video_url`
content blocks**, so a video through it reaches the model as text only and the
model answers about text it did receive. An ERROR served as a PASS.

That is still true and not in question. What changed is that the *kimi* direct
endpoint is no longer the only one worth having:

| transport | MiMo v2.6 Flash | k3-256k |
|---|---|---|
| OmniRoute `:20128` | ❌ dropped / 200 k-context rejection | ❌ dropped |
| `api.kimi.com/coding/v1` | ❌ not served there | ✅ works (incumbent) |
| **`opencode.ai/zen/go/v1`** | ✅ **works** | ❌ 400 (content-part schema) |

Each model has its own working direct endpoint. There is no single endpoint that
serves video for both — so adding MiMo means adding a transport, not repointing one.

## 2. The credential and the credential's home

```
OPENCODE_GO_API_KEY   — present in ~/.hermes/.env
                      — ABSENT from ~/workspace/nora/.env
```

**The deploy must inject it.** Compose does not inherit the host `.env` into a
service; the existing kimi lane already documents this trap (`docker-compose.yml`
injects `KIMI_CODE_API_KEY=${KIMI_CODE_API_KEY:-}` explicitly). The same explicit
injection is required here, plus an `.env.example` entry — and note the repo's
`config-template-key-discipline` gate: a new key is a **two-gate** change
(template must declare it as real TOML, and a differing live value must be argued
into `_MAY_DIFFER`).

The key follows DEC-8: the **name** goes in config, never the value.

## 3. The mandatory header — the one genuinely new mechanism

```
x-opencode-session: <opaque id>
```

Without it:
```
400 {"type":"MissingSessionID",
     "message":"Request is missing x-opencode-session and cannot be routed efficiently."}
```

This is the part that has no precedent in the codebase. The kimi lane needs no
special header, so `VisionClient._create` currently has no path for per-provider
header injection. Two acceptable shapes:

- **Per-provider header map** on the direct-client construction (preferred —
  keeps `_create` provider-agnostic, and the header is a property of the
  transport, not the call).
- A header parameter threaded through `describe()` (rejected: it would put
  transport detail into a call whose whole design says the modality picks the
  transport, `client.py:340`).

**⚠️ Added after adversarial review.** The preferred shape above is **not reachable
today**: the construction seam it depends on does not accept headers.
`nora/agent/factory.py:71-74` builds a client from `base_url` and `api_key` only —
there is no header parameter. So the integration table must include that file, and the
build must either extend `create_client` or wrap the constructed client. As written,
"follow the kimi precedent" produces a client that never sends the mandatory header.

**Session lifetime is also unresolved.** Only fresh-per-request sessions were measured
(`ses_<uuid4hex>`, one per call). Whether the provider prefers a stable session for
affinity, and whether one client should reuse a session, is **unverified** — the probe
saw no penalty for fresh sessions, which is not the same as knowing reuse is neutral.

**The verification obligation is specific:** test the client that **composition**
builds, not an independently constructed test client. A unit test with its own client
would pass while the wired path cannot send the header at all.

The session value is an opaque per-request identifier; it is **not** a secret and
**not** a conversation id.

## 4. Integration surface — follow the kimi precedent exactly

The kimi lane is a working template for every one of these hops. Reuse the shape
rather than inventing a second pattern:

| file | change |
|---|---|
| `nora/config.py:749` | `Literal["omniroute","kimi"]` → `+ "opencode-go"` |
| **`nora/agent/factory.py:71-74`** | **`create_client` accepts only `base_url`/`api_key` — must gain a header seam or the client must be wrapped. MISSED in the first version; added after review.** |
| `nora/app/composition.py:1727` | build the direct client; `create_client(base_url="https://opencode.ai/zen/go/v1", api_key=…)` |
| `nora/app/composition.py:_kimi_api_key_env` | generalise — the env-name resolver must know the new provider's default |
| `nora/infra/vision/client.py:~399` | route `"opencode-go"` to its client; inject the session header |
| `config.toml` `[media.describe.video]` | `provider = "opencode-go"`, `model = "mimo-v2.6-flash"` |
| `config.toml` `[media.describe.audio]` | `enabled = true`, `model = "mimo-v2.6-flash"`, `provider = "opencode-go"` (see `02-§6`) |
| `docker-compose.yml` / `.env.example` | inject `OPENCODE_GO_API_KEY` |

**Fail-closed is mandatory** and already the house pattern: a provider whose key
does not resolve is a **wiring gap** — the modality answers `unavailable`
(«I couldn't check»), never a silent fallback to the Image model, which would
send a video to a model that cannot read it.

`_describe_providers` (`composition.py:617`) uses the same enable+model gate as
`_describe_models` and `_describe_max_bytes`, so all three maps cannot disagree
about which modalities are live. The new provider value must flow through
**all three** — the third is easy to miss.

## 5. Why MiMo v2.6 Flash, in numbers

| | MiMo v2.6 Flash | k3-256k |
|---|---|---|
| 57 s clip media Δ tokens | **13,166** | 35,195 |
| tokens per MB | **5,267** | 14,078 |
| 57 s latency | 21–56 s | 46.2 s |
| OpenCode Go quota | **~30,100 req / 5 h** (MiMo-V2.5 tier) | **110 req / 5 h** (~490/mo) |

Two independent wins: **2.7× fewer video tokens** and roughly **two orders of
magnitude more request headroom**. The quota is the argument that matters for
Instagram: if Stories and Reels are read regularly, K3's ~490 requests/month
binds long before its token price does.

**Accuracy is comparable, not superior.** On a 35 s Hong Kong stage clip, MiMo
transcribed every neon sign present in an independently-built ffmpeg frame
ground-truth (`昂坪360`, `叱咤903`, `星空下音樂會`, `二十周年`); K3 got the
same signs plus on-screen timecodes MiMo did not report. Neither is strictly
better — **this is a cost/quota change, not an accuracy upgrade, and should not
be sold as one.**

## 6. What is explicitly NOT claimed

- **Not a claim that K3 is deficient.** It works, it is the incumbent, and it
  stays in the config as the documented fallback.
- **Not an image-lane change.** `[media.describe.image]` keeps its own model; MiMo
  on images measured correct but *slower* than the image-lane alternatives
  (20.9 s dense / 12.2 s clean vs GLM's 6.0 s). The lanes stay split.
- **Not verified at production `max_bytes`.** The benchmark's largest clip was
  2.5 MB; `[media.describe.video] max_bytes` ships at **100 MiB**. MiMo accepted
  2.5 MB without difficulty and there is no evidence of a ceiling, but a clip at
  the top of the configured range has **not** been tested — worth one probe before
  the range is relied on.
- **`mimo-v2.5-pro` returns 404 on this endpoint** despite being catalogued;
  `mimo-v2.5` also works and is the model Xiaomi's own docs name for video. The
  recommendation is **v2.6-flash**; the others are recorded, not adopted.
