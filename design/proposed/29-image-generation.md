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
        quality: Literal["standard", "quality"] | None = None,
    ) -> ImageResult: ...

    async def health(self) -> bool: ...
```

Where:

- `ImageReference` — a reference image for editing/conditioning: **a handle Nora
  already holds** (a blob from her media custody, DEC-43/47), never an
  arbitrary external URL. NOT the raw bytes: providers differ on what they
  accept, and Nora's media custody already gives her encrypted blobs — the
  adapter is responsible for turning a stored blob into whatever the provider
  wants (upload to a short-lived URL, base64 data URI, file_id).
- `ImageSize` — a resolution tier + aspect ratio, mapped per-provider:
  `tier: small|medium|large` and `aspect_ratio: "1:1"|"16:9"|"9:16"|"3:2"|"2:3"`
  (default `1:1`). The model-facing tool names the size word and aspect; the
  adapter translates (xAI's API takes `aspect_ratio` + `resolution` 1k/2k
  directly; another provider may map to width/height).
- `quality` — `"standard"` | `"quality"` (None = provider default). The
  model-facing tool may ask for the quality model on a re-run; the adapter
  maps it (xAI: `grok-imagine-image` vs `grok-imagine-image-quality`).
- `ImageResult` — `ok | nothing_found | unavailable` three-state, the R-27
  shape this repo's signatures demand. Plus the artifact handle (URL / local
  path / drop slug), a `provider` tag so the reply can be honest about what
  made the image, and a `reason: str = ""` field so the model can distinguish
  quota-exhausted from auth-expired from provider-down (the same note-carrying
  shape `MusicToolOutcome` already has — ports.py:1701-1712).

### The contract suite

Per the repo's test-seam policy: a port never gains a second implementation —
including a test fake — without a shared contract suite both pass
(`tests/contract/`, same as `test_blob_store_contract.py`). `ImageGenerator`
gets `tests/contract/test_image_generator_contract.py` with a `fake` and the
xAI implementation both running the same cases.

## 4. First implementation: xAI via X Premium OAuth (subscription lane)

### 4.1 Credential model — Option A (Nora owns her token)

Nora does her **own** xAI OAuth device flow, with tokens stored the same way
every other Nora credential is stored: **host `.env`**, injected through
compose (`DEC-8` — one name for one secret; `TAVILY_API_KEY`,
`GIPHY_API_KEY`, `GOOGLE_MAPS_API_KEY`, `OMNIROUTE_API_KEY`,
`DISCORD_BOT_TOKEN` all live there). NO Postgres table for credentials —
Nora's db is for her record store, not an auth store, and the `.env` pattern
already answers "who may talk to her, and where" without a new schema.

Why A over the alternatives:

- **A (self-owned, `.env`):** consistent with Nora's independence — she already
  holds five service keys from `.env`; this is the sixth, differing only in
  that it needs a refresh. Quota and expiry errors surface *to her* so she can
  answer the user honestly ("今日 quota 用晒" vs "我 auth 過期咗"), which is
  exactly the R-27 fail-closed discipline: `unavailable` with a reason the
  model can act on, never a generic 401.
- **A-with-db (tokens in Postgres):** rejected by the operator (2026-08-26) —
  a credential is not record-store data. Nora's db exists for messages,
  facts, notes, events; adding an auth table for one service key invents a
  new storage pattern where `.env` already is one.
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
- Scopes requested: `openid profile email offline_access grok-cli:access
  api:access` — the exact scope set Hermes' own `hermes_cli/auth.py` uses
  (`XAI_OAUTH_SCOPE`, verified live: Miles's `hermes auth add xai-oauth`
  login succeeded with this set on this host). `offline_access` is what
  causes a refresh token to be issued (without it, no refresh_token, and
  the whole proactive-refresh design dies silently); `grok-cli:access` +
  `api:access` together are what entitle image generation through the
  subscription lane. The CLI validates the returned scope list at login so
  a rejected scope fails the CLI, not the daemon later.
- Client: the official xAI device-flow client id Hermes uses —
  `b1a00492-073a-47ea-816f-4c329264a828` — so Miles's single approval covers
  the same entitlement both sides draw from. Device-code flow against
  `https://auth.x.ai` (`POST /oauth2/device/code` → `POST /oauth2/device/token`).
- Refresh skew: **3600 seconds** — the same constant Hermes ships
  (`XAI_ACCESS_TOKEN_REFRESH_SKEW_SECONDS = 3600`): xAI access tokens are
  short-lived (~6h) and a 120s window is too narrow for a gateway that may
  sit idle for 30 minutes. Refresh up to an hour early keeps the token warm.
- The CLI **writes only the refresh token into `.env`**
  (`XAI_OAUTH_REFRESH_TOKEN` — the long-lived secret, same shape every other
  secret takes), then tells the operator to `docker compose up -d nora` —
  the same "edit .env, recreate" loop the deployment already uses.
  `docker-compose.yml` gains the one new `environment:` entry (plus a
  bind-mount for the refresh state, below).
- Access token: **never lives in `.env`.** It lives solely in the
  container-writable state file `./state/xai-oauth.json` (the same repo-tree
  state pattern `./state/signal-cli` already uses). Boot precedence is
  explicit: read the state file; only if it is absent or corrupt, the adapter
  derives an initial access token by refreshing with the `.env` refresh token.
  One secret, one place — no two-source-of-truth restart race.
- Refresh: standard OIDC `POST /oauth2/token` with `grant_type=refresh_token`,
  reusing the same skew policy Hermes uses (proactive refresh before expiry,
  not on-401). The adapter refreshes lazily under a lock, single-flight, and
  **fails closed** to `unavailable` if the refresh_token itself is dead (which
  means: re-run `nora xai-login`, not "retry forever").
- Refresh flakiness: xAI's refresh endpoint is intermittently blocked by a
  Cloudflare challenge (403 on the refresh call while the token is still
  valid). The adapter treats a Cloudflare-challenged refresh as a **retryable**
  bucket (bounded backoff), not a terminal auth failure — otherwise a
  transient challenge forces a manual `xai-login` for a token that was never
  dead.
- The access token is a JWT with scopes `api:access` (plus OIDC claims) — the
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
allow_reference_images = false   # privacy gate — see §5.1
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

### 5.0 How a chat-history image becomes a reference (the "fetch")

There is **no external fetch** — the image is already Nora's. The marker the
model sees in the transcript (`[image a3f2c1]`) is the whole resolution path:

1. The model passes the **short marker code** (`a3f2c1`) — the same value
   `media_fetch` takes — never a URL, never a path.
2. The xAI adapter resolves it internally, exactly the way `media_fetch`
   does: `_handle_hex(code)` → `sha256` → `store.recent(conversation_id)` to
   confirm the handle belongs to THIS conversation (R-21/DEC-24) →
   `blob_store.get(key=sha256)` → **decrypted bytes**.
3. The adapter turns bytes into what xAI wants (upload to `drop` → public
   URL, or a base64 data URI) and calls `/v1/images/edits`.

Custody is structural: the code can only resolve to a blob Nora already
holds. A code that resolves to nothing, or belongs to another conversation,
is `nothing_found` — never an attempt to reach out.

### 5.1 Privacy gate (reference images specifically)

Reference images are third-party content (a friend's photo) uploaded to xAI's
cloud. That deserves an explicit gate, not an implicit default:

- **`[image_gen] allow_reference_images = false` by default.**
- When `true`, the **adapter enforces** — not the model's compliance — that
  every reference image is a Nora-held custody handle (came in via
  `media_fetch`). `ImageReference` admits no raw external URL: the port
  rejects any reference that is not a Nora-held custody handle. Nora never
  fetches an external image *because* the user asked to edit it; the user
  must have already sent it.
- The tool docstring tells the model to state plainly when a generated image
  used a reference photo, so the group knows the image was edited, not
  invented.

### 5.2 Prompt convention (image → prompt)

A reference image is bytes the MODEL cannot see — the transcript only shows
the marker. So a good edit prompt needs a convention, not improvisation:

- **Describe before transforming.** When the transformation depends on what
  the image shows, call `media_fetch` on the marker FIRST and read its
  `description`; then compose the prompt as:
  `[what the image shows, from the description] + [the transformation] +
  [what to keep / not keep]`.
- **No identifiers in the prompt.** The prompt is uploaded to a third-party
  cloud (xAI). Names, handles, phone numbers, and other identifiers never go
  into it (R-18/R-28 — the same screen `media_fetch`'s serve path runs on
  its own description).
- **The transformation is the point.** A pure style transfer does not need
  the description at all — the prompt should say what to apply ("turn this
  into a watercolour", "add rain at night"), not restate the content. The
  model should not guess what is in the image it cannot see; if the
  transformation depends on content it does not know, call `media_fetch`
  first or say it cannot do that.
- These rules live in the `generate_image` docstring, so the model sees them
  at call time — they are practice, not infra.

### 5.3 The pipeline (reuses existing components)

1. `media_fetch` already retrieves a stored image's bytes (DEC-56) — the
   adapter reuses the same resolution (marker code → blob bytes).
2. Nora's `drop` service already turns bytes into a public URL
   (`nora.67.zone/drop/<slug>`, via rustfs). The xAI adapter uploads the
   reference image to drop (or passes a base64 data URI) and calls
   `/v1/images/edits`.
3. The generated result also goes through drop → public URL → `send_reply`
   with the URL (or attachment, per the platform).

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
| Rate limited (429) | `unavailable` (retryable) | "服務暫時唔得，過陣再試" |
| Provider down / timeout | `unavailable` (retryable) | "服務暫時唔得" |
| Success | `ok` + URL | "整好咗：<url>" |

`unavailable` never retries blindly — the adapter distinguishes retryable
(timeout, 5xx, 429) from terminal (quota, auth) and only the former retries,
once, bounded.

**403 is ambiguous — classify by response body, not status alone.** On the
OAuth/subscription lane, BOTH subscription-quota exhaustion and entitlement
failures surface as HTTP 403 ("403 subscription/resource error"), so the
adapter must parse the response body / `error.code` to map quota vs auth vs
unknown. If the body does not disambiguate, fail closed to `unavailable` with
a generic reason — never guess "quota" or "auth" from the status code alone.

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
nora/cli.py                              # + xai-login command
tests/contract/test_image_generator_contract.py        # shared suite (incl. reason-distinguishes-quota-vs-auth case, reference-custody rejection case)
tests/test_config.py                     # [image_gen] keys, forbid/optional cases
config.toml                              # [image_gen] block (enabled = false)
docker-compose.yml                       # + XAI_OAUTH_REFRESH_TOKEN env entry, ./state/xai-oauth.json bind-mount
.env (operator, gitignored)              # + XAI_OAUTH_REFRESH_TOKEN only (access token lives in ./state/xai-oauth.json)
design/09-decision-index.md              # DEC-137 row
```

## 10. DEC-137 (index row)

| ID | Statement | Enforcing test |
|---|---|---|
| DEC-137 | **Image generation is a provider-agnostic port (`ImageGenerator`), never a native binding to xAI/Grok; the first implementation is the xAI subscription lane through Nora's OWN OAuth tokens (Option A — no host Hermes credential proxy).** The port's contract suite is shared by the xAI adapter and a fake; the model-facing tool never names a provider; reference-image editing goes through `/v1/images/edits` gated by `allow_reference_images` (default false); `ImageResult.reason` distinguishes quota/auth/down; reference images are adapter-enforced Nora-held custody handles only. | `tests/contract/test_image_generator_contract.py` (xai + fake params; reason-distinguishes-quota-vs-auth case; external-URL reference rejected), `tests/test_tools.py` (tool docstring contains no provider name), `tests/test_config.py` (unknown `[image_gen]` key is a startup failure; `enabled=false` keeps the tool `unavailable`), `tests/test_composition.py` (provider factory honors config selector) |
