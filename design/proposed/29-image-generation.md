# Image Generation — provider-agnostic port, xAI/OAuth first implementation

**Status:** Proposed (nothing built)
**Date:** 2026-08-26
**DEC:** 137 (indexed at the bottom of this document)
**Branch:** `feat/image-generation-design` (worktree)

## 1. What this is

Nora gains a `generate_image` tool. The capability is defined as a **port**, not
as an xAI binding: Nora asks "make an image from this prompt" and the wiring
decides which provider answers. The first implementation is xAI's image API
through Miles's X Premium OAuth entitlement (subscription lane, not metered
billing). Later implementations — Google Imagen, Stability, a local model —
plug into the same port and the model-facing tool does not change.

This is the same shape `GifSource` already established (`nora/domain/ports.py`
line 261): a protocol with two async verbs, a real provider behind it, and the
model-facing tool as a thin wrapper over an injected dependency.

## 2. Why provider-agnostic, not "just Grok"

Miles's explicit direction (2026-08-26): *"image generation should also be
abstracted, not natively bound on Grok."* Reasons this is the right call for
this codebase specifically:

1. **Nora is a Strands container, not a Hermes profile.** She already holds her
   own service keys (TAVILY, GIPHY, GOOGLE_MAPS, OMNIROUTE, DISCORD). The xAI
   OAuth lane is one more credential *she* owns — not a special host-side
   entitlement she borrows. A port keeps the credential detail inside the
   adapter, exactly where `GifSource` keeps Giphy's key.
2. **Provider cost/latency/quality shifts are already a recurring event here.**
   `config.toml`'s `[media.describe]` block is a graveyard of measured model
   migrations (minimax-m3 → mimo-v2.5 → minimax-m3, each with a live trace in
   the comments). The image provider will move the same way; the port makes a
   move a config + adapter swap, not a tool rewrite.
3. **The tool schema must not leak provider vocabulary.** A model-facing tool
   whose docstring says "grok" cannot be reused when the provider changes; one
   that says "generate an image from a text prompt" survives it.

## 3. The port

```python
@runtime_checkable
class ImageGenerator(Protocol):
    """Provider boundary for generating images from text prompts."""

    async def generate(
        self,
        *,
        prompt: str,
        reference_images: Sequence[ImageReference] | None = None,
        size: ImageSize | None = None,
    ) -> ImageResult: ...

    async def health(self) -> bool: ...
```

Where:

- `ImageReference` — a reference image for editing/conditioning (a URL, or a
  handle Nora already holds). NOT the raw bytes: providers differ on what they
  accept, and Nora's media custody (DEC-43/47) already gives her encrypted
  blobs — the adapter is responsible for turning a stored blob into whatever
  the provider wants (upload to a short-lived URL, base64 data URI, file_id).
- `ImageSize` — a small enum (`small`/`medium`/`large` or explicit
  width/height) that maps per-provider. The model-facing tool only ever names
  a size word; the adapter translates.
- `ImageResult` — `ok | nothing_found | unavailable` three-state, the R-27
  shape this repo's signatures demand. Plus the artifact handle (URL / local
  path / drop slug) and a `provider` tag so the reply can be honest about
  what made the image.

### The contract suite

Per the repo's test-seam policy: a port never gains a second implementation —
including a test fake — without a shared contract suite both pass
(`tests/contract/`, same as `test_blob_store_contract.py`). `ImageGenerator`
gets `tests/contract/test_image_generator_contract.py` with a `fake` and the
xAI implementation both running the same cases.

## 4. First implementation: xAI via X Premium OAuth (subscription lane)

### 4.1 Credential model — Option A (Nora owns her token)

Nora does her **own** xAI OAuth device flow, storing tokens in her own
Postgres (she has alembic). She does NOT borrow Hermes' auth store and does NOT
depend on a host-side credential proxy.

Why A over the alternatives:

- **A (self-owned):** consistent with Nora's independence — she already holds
  five service keys; this is the sixth, differing only in that it needs a
  refresh. Quota and expiry errors surface *to her* so she can answer the user
  honestly ("今日 quota 用晒" vs "我 auth 過期咗"), which is exactly the
  R-27 fail-closed discipline: `unavailable` with a reason the model can act
  on, never a generic 401.
- **B (host Hermes proxy):** rejected. It re-couples Nora to Hermes infra —
  a new host service, a dependency on Hermes' `auth.json` shape, and a
  "why is image generation down because Hermes auth rotated" failure mode.
  The proxy also can't speak Nora's three-state result shape without
  translating every error, at which point the abstraction has been re-built
  inside a proxy instead of inside Nora where the tool lives.

### 4.2 The OAuth flow

- New CLI: `nora xai-login` — device-code flow against `https://auth.x.ai`
  (same endpoints Hermes uses: `POST /oauth2/device/code` →
  `POST /oauth2/device/token`), Miles approves in browser once.
- Tokens (`access_token`, `refresh_token`, `expires_in`, `token_type`) stored
  in a new `xai_oauth` table (alembic migration, owned by Nora's chain).
- Refresh: standard OIDC `POST /oauth2/token` with `grant_type=refresh_token`,
  reusing the same skew policy Hermes uses (proactive refresh before expiry,
  not on-401). The adapter refreshes lazily under a lock, single-flight, and
  **fails closed** to `unavailable` if the refresh_token itself is dead
  (which means: re-run `nora xai-login`, not "retry forever").
- The access token is a JWT with scopes `grok-cli:access api:access` — the
  `api:access` scope is what entitles image generation through the
  subscription lane. This is confirmed working: Hermes' own `xai-oauth`
  credential pool resolves today and its image plugin calls
  `api.x.ai/v1/images/generations` with a bearer token, no API key.

### 4.3 The API calls

- `POST https://api.x.ai/v1/images/generations` — text → image.
- `POST https://api.x.ai/v1/images/edits` — with reference images (see §5).
- Model: `grok-imagine-image` / `grok-imagine-image-quality` (same models
  Hermes' `image_gen/xai` plugin uses). Cost on the subscription lane is
  quota-bounded, not per-call metered — the whole point of using OAuth rather
  than an API key.

### 4.4 Config

```toml
[image_gen]
enabled = false            # inert by default, DEC-43 posture
provider = "xai"           # port selector
[image_gen.xai]
model = "grok-imagine-image"
quality_model = "grok-imagine-image-quality"
timeout_seconds = 90.0
```

`enabled = false` by default: a config.toml written before this exists keeps
loading unchanged, and a misconfigured lane degrades the tool to
`unavailable` rather than crashing the daemon — the same "a bonus feature
never takes the daemon down" posture DEC-136's RustFS wiring took.

## 5. Reference images — supported, via the edits API

xAI's `/v1/images/edits` accepts source images as **public URL / base64 data
URI / file_id**, up to 3 per request, and supports style transfer and
multi-turn editing. So the answer to "can she use a reference image, or only a
prompt?" is: **yes, reference images work.**

The pipeline reuses components Nora already has:

1. `media_fetch` already retrieves a stored image (DEC-56) — a photo a friend
   sent in the conversation.
2. Nora's `drop` service already turns bytes into a public URL
   (`nora.67.zone/drop/<slug>`, via rustfs). The xAI adapter uploads the
   reference image to drop (or passes a base64 data URI) and calls
   `/v1/images/edits`.
3. The generated result also goes through drop → public URL → `send_reply`
   with the URL (or attachment, per the platform).

### 5.1 Privacy gate (reference images specifically)

Reference images are third-party content (a friend's photo) uploaded to xAI's
cloud. That deserves an explicit gate, not an implicit default:

- **`[image_gen] allow_reference_images = false` by default.**
- When `true`, the tool's `when` clause requires the reference image to be
  already in the conversation (came in via `media_fetch`, i.e. Nora already
  holds it) — Nora never fetches an external image *because* the user asked to
  edit it; the user must have already sent it.
- The tool docstring tells the model to state plainly when a generated image
  used a reference photo, so the group knows the image was edited, not
  invented.

## 6. The model-facing tool

```python
"generate_image": NativeTool(
    8,
    "make an image from a text prompt (and optionally from a photo already "
    "in this conversation — say so when you do)",
    read_only=False,
)
```

Budget 8 — sized like `media_fetch` × 2 (one call to generate, possibly one
call to re-run with a size/quality tweak, plus the reply). The `ReplyGuard`
budget mechanism (DEC-10) enforces it; `NATIVE_TOOL_BUDGETS` in
`nora/agent/tools.py` is the single source both draw from.

## 7. Failure semantics (R-27 three-state)

| Condition | Result | Model sees |
|---|---|---|
| Prompt invalid / refused by provider | `nothing_found` | "generate 唔到，可能 prompt 有問題" |
| Quota exhausted (subscription) | `unavailable` + reason | "今日 quota 用晒" |
| Token expired, refresh failed | `unavailable` + reason | "auth 過期，要 Miles 重新 login" |
| Provider down / timeout | `unavailable` | "服務暫時唔得" |
| Success | `ok` + URL | "整好咗：<url>" |

`unavailable` never retries blindly — the adapter distinguishes retryable
(timeout, 5xx) from terminal (quota, auth) and only the former retries, once,
bounded.

## 8. Out of scope (this proposal)

- **Voice/image input to the generator** (Nora describing an image she can't
  retrieve) — future.
- **Multiple providers wired at once** — the port supports it, but the first
  cut wires `xai` only; a second provider is a separate DEC with its own
  contract cases.
- **The `drop` upload for the RESULT** — implemented as part of the xAI
  adapter's `ImageResult` (a public URL is required by the reply surface), but
  the exact drop integration is the adapter's detail, not the port's.
- **Hermes-side skill/reference cleanup** (the ~20 stale `~/.hermes/profiles/nora`
  references in skills) — separate task, tracked in the dispatch thread, not
  part of this repo change.

## 9. Files touched (when built)

```
nora/domain/ports.py                     # + ImageGenerator, ImageReference, ImageResult, ImageSize
nora/infra/imagegen/xai_adapter.py       # new — xAI HTTP + OAuth refresh
nora/infra/imagegen/xai_oauth.py         # new — device flow + refresh
nora/infra/imagegen/__init__.py          # provider factory from config
nora/config.py                           # [image_gen] section
nora/agent/tools.py                      # + generate_image NativeTool + wiring
nora/infra/sql/migrations/versions/0008_xai_oauth.py   # tokens table
nora/cli.py                              # + xai-login command
tests/contract/test_image_generator_contract.py        # shared suite
tests/test_config.py                     # [image_gen] keys, forbid/optional cases
config.toml                              # [image_gen] block (enabled = false)
design/09-decision-index.md              # DEC-137 row
```

## 10. DEC-137 (index row)

| ID | Statement | Enforcing test |
|---|---|---|
| DEC-137 | **Image generation is a provider-agnostic port (`ImageGenerator`), never a native binding to xAI/Grok; the first implementation is the xAI subscription lane through Nora's OWN OAuth tokens (Option A — no host Hermes credential proxy).** The port's contract suite is shared by the xAI adapter and a fake; the model-facing tool never names a provider; reference-image editing goes through `/v1/images/edits` gated by `allow_reference_images` (default false). | `tests/contract/test_image_generator_contract.py` (xai + fake params), `tests/test_tools.py` (tool docstring contains no provider name), `tests/test_config.py` (unknown `[image_gen]` key is a startup failure; `enabled=false` keeps the tool `unavailable`), `tests/test_composition.py` (provider factory honors config selector) |
