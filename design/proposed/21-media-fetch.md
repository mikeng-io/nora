---
kind: design
title: "Nora — media_fetch: reading a stored image through MiMo-V2.5"
description: "The READ half of media custody (design/18): a scoped tool that decrypts a blob Nora already holds and describes it via MiMo-V2.5 through OmniRoute, under its OWN timeout and byte ceiling, caches by content hash, and treats the model-written description as untrusted egress — honest about what the identifier screen does and does not catch."
tags: [nora, design, media, vision, mimo, omniroute, grounding]
timestamp: '2026-08-07'
status: superseded
---

> **SUPERSEDED — this shipped.** `media_fetch` is live (`nora/agent/tools.py`),
> and the shipped tool differs from this spec in two ways worth knowing before
> you read it as current: the parameter is `code`, not `handle` (DEC-98 — the
> model kept sending the word the docstring uses), and the vision model is
> `minimax-m3`, not MiMo-V2.5, because MiMo's routes were rate-limited when it
> was wired. Kept for the reasoning, not for the shape.

# media_fetch — reading stored media

Media custody (design/18, DEC-43/47) is the WRITE half: inbound attachments are
copied into Nora's hands, content-addressed (sha256 of the EXIF-stripped
plaintext), sealed with `BlobCipher`, and recorded on the message row as
`media_json` — `{sha256, media_type, size, filename}`. The transcript shows the
group a handle (`[image a3f2c1]`, `MediaBlob.handle` = the first 6 hex of the
sha256). **Nothing reads it back.** `media_fetch` is that read: the operator-idea
(2026-08-07) that Nora should be able to *look at* an image the group sent, using
**MiMo-V2.5 through OmniRoute** (`opencode-go/mimo-v2.5`), which the 2026-08-07
probe verified end to end (a red PNG → "Red", prompt_tokens 159).

**Image first.** Audio and video are out — a different model call, a different
cost, no measured need. The tool is shaped so they are a later table entry.

**This revision folds a design review (2026-08-07).** The architecture verified
against real code; five gaps did not, and every one is closed below — the
dominant one being that a slow vision call inside the shared 30 s loop budget is
the silent `invocation_timeout` failure class R-23/R-25/A8 exist to prevent.

## 0. What it is, in one line

A room-scoped tool that turns a media handle the model already sees into a
sentence about the picture — `resolve the handle in THIS conversation → confirm
it is an image → decrypt the blob → ask MiMo under its own timeout and byte
ceiling → cache by content hash → hand back a description egress treats as
untrusted`.

## 1. The seam — it reads what custody wrote; new collaborators, no ingest change

- **Input is a handle** (DEC-24) — the `[image a3f2c1]` the model was shown; never
  a sha256, a path, or a key. Resolved **within this conversation's own
  `media_json`**, the closure-scope `read_buffer` uses (`bound_conversation_id`),
  backed by R-21 — a handle from another conversation is un-resolvable, not
  resolved-then-refused (design/12 §3).
- **Two handle caveats the resolver must honour** (review):
  - **Resolution window.** `store.recent` is bounded (`READ_BUFFER_MAX = 60`). The
    resolver must scan a window **at least as deep as the prefill plus
    `read_buffer`'s reach**, so a handle the group can still see resolves; a blob
    older than that window is `nothing_found` (honest — she cannot see it either).
  - **6-hex is 24 bits.** Two in-scope blobs *can* share a handle prefix
    (`media.py` concedes this). Two matches → **`ambiguous`** (ask which), never
    "pick the first".
- **The blob is read through the store Nora already has** —
  `FilesystemBlobStore.get(key=sha256)` decrypts (`BlobCipher.open`). It returns
  `None` for an absent blob AND **raises** `InvalidTag`/`ValueError` for a
  present-but-corrupt or wrong-key blob (the live case the day
  `MEDIA_ENCRYPTION_KEY` rotates). §4 wraps BOTH into `unavailable`.
- **New wiring, stated plainly** (the review's correction): the OmniRoute client,
  the blob store, and the vision config are **not** on the tool-construction path
  today — `ToolRegistry` is handed `store, room, transit_source, route_source`,
  never the client. `media_fetch` adds them as `ToolRegistry` collaborators,
  exactly like `_transit`/`_route`. This is the bulk of the wiring work.
- **No ingest change.** The write path is untouched.

## 2. The MiMo call — its OWN timeout and byte ceiling (the must-fix)

The whole agent loop — every model turn and every tool call, MiMo included —
runs inside ONE `asyncio.wait_for(timeout=llm.timeout_seconds)` (30 s). A MiMo
*reasoning* call with generous `max_tokens` is seconds, not `about`'s ~0.2 s. Two
of them plus the text turns overrun 30 s → `invocation_timeout`, silent under R-5.
So:

- **A per-call vision timeout DERIVED from `llm.timeout_seconds`**, the way
  `build_retry_policy` derives its budget from the timeout — provably smaller than
  the loop budget, so a slow or hung MiMo call becomes this tool's `unavailable`
  inside the turn, never the loop's silent timeout. This is the single most
  important requirement (§6.2).
- **A byte ceiling well below `MediaConfig.max_bytes` (100 MiB).** A 100 MiB blob
  is a ~133 MiB base64 body. `media_fetch` caps the bytes sent to MiMo
  (`vision_max_bytes`, config) and returns `unavailable` with an honest note above
  it (downscaling is a later option, not this build).
- **The call is a raw `client.chat.completions.create`**, so it gets **none** of
  `OpenAIModel`'s strands retry/timeout/trace span — this tool owns its own
  timeout (above), one bounded retry at most, and its own metric/log line.
- Through the **process-wide OmniRoute `AsyncOpenAI` client**, model
  `opencode-go/mimo-v2.5`, image as a base64 `data:` URI in an `image_url` block,
  **`reasoning_effort="low"`** and a generous `max_tokens` (the probe's shape).
  `vision_model` / `vision_max_tokens` / `vision_reasoning_effort` /
  `vision_max_bytes` / `vision_timeout_seconds` are **real `MediaConfig` fields**
  (it is `extra="forbid"`, so they cannot be smuggled in) (§6.7, DEC-8).
- **The prompt is fixed and small** — "describe this image; if it contains text,
  transcribe it" — authored, never model-supplied.

## 3. Cache — by content hash, screened on every serve

The sha256 IS the content, so a description is valid forever for that hash. A
bounded in-memory LRU keyed by sha256, checked before any MiMo call.

- **In-memory, per process**; persistence deferred (a restart re-describing a
  re-referenced image is a MiMo call, not a correctness bug). Sound, not a footgun:
  a cache hit in another conversation still requires that conversation to hold the
  same bytes (the handle resolves in ITS scope first), and the description is a
  pure function of content, so a hit leaks nothing about the other room.
- **The cache stores the RAW description; `scan_identifiers` (§4) runs on every
  SERVE, not once at store time** — so a cache hit can never bypass the screen.
- `nora_media_fetch_total{status, cached}` counts hits/misses, so "MiMo is never
  actually called" can never be read from silence (DEC-31).

## 4. States, non-image blobs, and the untrusted-egress rule — honestly

`status` is `ok` / `nothing_found` / `ambiguous` / `not_an_image` / `unavailable`:

- **`ok`** — `description` is MiMo's sentence.
- **`nothing_found`** — the handle resolves to no blob in this conversation's
  window. Distinct from `unavailable`.
- **`ambiguous`** — two in-scope blobs share the handle prefix; ask which.
- **`not_an_image`** — the handle resolves, but `media_type` is not `image/*` (a
  voice note, a video, a PDF). Gated at resolution, **before** any MiMo call —
  sending audio bytes as `image_url` would be an OmniRoute error surfaced as a
  silent `unavailable`. Honest: "that's a voice note / a file, not something I can
  look at."
- **`unavailable`** — the blob is absent OR the decrypt raised (both wrapped, §1),
  OR MiMo did not answer within the vision timeout. Say so; never invent, never
  serve it as `nothing_found` (R-5). `store.get` is wrapped in try/except so this
  tool obeys `test_a_tool_never_raises_into_the_agent_loop`.

**The description is UNTRUSTED** — text a vision model wrote about bytes a group
member sent, the trust class of a fetched web page (design/14 §2.1), less trusted
than a tool result Nora authored.

- **Identifier screen — and what it does NOT catch, stated plainly.** The
  description runs through **`scan_identifiers`**, dropped-with-a-note if it hits,
  the shape `about` uses. But `scan_identifiers` matches an **ACI/UUID**, a
  **key**, and a **`+`-prefixed E.164** — it deliberately does **not** match a
  bare local number (`9000 0101`), because blocking free-standing digit runs would
  mute her in ordinary chat (R-18's standing blind spot, `egress.py`). So a photo
  of a business card whose number MiMo transcribes as `9000 0101` **passes** the
  screen. This is a **residual risk EQUAL to R-18's**, not a solved case — the
  spec says so rather than claiming a protection it does not deliver. (A stricter
  OCR-number screen applied *only to vision descriptions* — where a bare number is
  far more likely a real disclosure than incidental chat — is a possible later
  hardening; it is named, not mandated, here.)
- **Prompt injection.** An image can carry text reading as an instruction. The
  description returns as a tool RESULT dict, which the loop already treats as
  content (identical to sender-supplied `read_buffer` bodies), never concatenated
  into a system/prompt position. The build's acceptance check pins that.

## 5. Budget, gating, and the tool table

- **A `NATIVE_TOOLS` entry, budget 2, `terminal=False`, `time_shiftable=None`.**
  Two lets a message name two images; the per-call vision timeout (§2) is what
  keeps two calls inside the loop budget — budget and timeout together, not budget
  alone.
- **Gated behind `[media] enabled` + present `MEDIA_ENCRYPTION_KEY` + reachable
  OmniRoute.** Any absent → the tool is `unavailable` (the `route`-with-no-key
  inert shape, `_build_media_custody` already degrades to `(None, None)`). Today
  `[media] enabled=false`, so it ships **inert-by-config** — the honest default
  for a capability that reads people's photos.
- **Its `when` names the trigger**: someone asks about an image in the
  conversation. It must not fire on a bare `[image]` with no handle (custody holds
  nothing → `nothing_found`).

## 6. What must be true before it ships

1. The handle resolves **only within this conversation's `media_json`**, over a
   window ≥ prefill + `read_buffer` reach; a cross-conversation handle →
   `nothing_found`; a shared 6-hex prefix → `ambiguous`, never pick-first.
2. **The MiMo call has its own timeout derived from `llm.timeout_seconds`, proven
   smaller than the loop budget** (a hung MiMo → this tool's `unavailable`, never
   the loop's `invocation_timeout`), AND a `vision_max_bytes` ceiling below
   `max_bytes` with `unavailable` above it. **(The blocker.)**
3. `store.get` is wrapped: both `None` (absent) and a raised `InvalidTag`/
   `ValueError` (corrupt / wrong key) map to `unavailable`; the tool never raises
   into the loop.
4. A non-`image/*` blob returns `not_an_image` at resolution, before any MiMo call.
5. The description is cached by sha256; a second `media_fetch` of the SAME sha256
   makes no MiMo call (fake MiMo's call-count asserted across two invocations of
   one hash; reverting the cache lookup turns it red), and `scan_identifiers` runs
   on every serve, not once at store time.
6. `scan_identifiers` screens the description; the spec/tests state honestly that
   a bare local number is NOT caught (residual risk = R-18's), and a transcribed
   ACI/UUID IS (reverting that screen turns a test red on a UUID-bearing image).
7. `vision_*` are real `MediaConfig` fields (`extra="forbid"`); the model, tokens,
   effort, byte-ceiling and timeout are config, not literals.
8. Gated behind `[media] enabled` + key + reachable OmniRoute; `unavailable` when
   any is absent; a `NATIVE_TOOLS` budget of 2 and a RULE test; the fake MiMo
   returns the wrong-shape for a wrong call (CLAUDE.md fake rule).

## 7. Decision-index entries this creates

- **DEC-56** — `media_fetch` reads custody's blobs through MiMo-V2.5, scoped per
  conversation over a bounded window, cached by content hash and screened on every
  serve, under its own vision timeout (derived from `llm.timeout_seconds`) and
  byte ceiling. The MiMo description is untrusted egress: identifier-screened like
  `about`, with the honest caveat that a bare local number is a residual risk
  equal to R-18's. Image only; `not_an_image` for the rest; inert-by-config behind
  `[media] enabled`. Criteria 1–8.
- A RULE test in `tests/test_acceptance_rules.py::RULE_TESTS`.

(DEC number provisional — reconciled against the other branches at merge time.)
