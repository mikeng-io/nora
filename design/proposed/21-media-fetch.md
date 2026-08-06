---
kind: design
title: "Nora — media_fetch: reading a stored image through MiMo-V2.5"
description: "The READ half of media custody (design/18): a scoped tool that decrypts a blob Nora already holds and describes it via MiMo-V2.5 through OmniRoute, caches the description by content hash, and treats the model-written description as untrusted egress like every other tool result."
tags: [nora, design, media, vision, mimo, omniroute, grounding]
timestamp: '2026-08-07'
status: proposed
---

# media_fetch — reading stored media

Media custody (design/18, DEC-43/47) is the WRITE half: inbound attachments are
copied into Nora's hands, content-addressed (sha256 of the EXIF-stripped
plaintext), sealed with `BlobCipher`, and recorded on the message row as
`media_json` — `{sha256, media_type, size, filename}`. The transcript shows the
group a handle (`[image a3f2c1]`, `MediaBlob.handle` = the first 6 hex of the
sha256). **Nothing reads it back.** `media_fetch` is that read: the operator-idea
(2026-08-07) that Nora should be able to *look at* an image the group sent and
answer about it, using **MiMo-V2.5 through OmniRoute** (`opencode-go/mimo-v2.5`),
which the 2026-08-07 probe verified end to end (a red PNG → "Red", prompt_tokens
159).

**Image first.** Audio and video are out of this build — a different model call,
a different cost, and no measured need yet. The tool is shaped so they are a later
table entry, not a rewrite.

## 0. What it is, in one line

A room-scoped tool that turns a media handle the model already sees into a
sentence about the picture — `decrypt the blob Nora holds → ask MiMo → cache by
content hash → hand back a description that egress treats as untrusted`.

## 1. The seam — it reads what custody already wrote, and adds nothing to ingest

- **Input is a handle, not bytes and not a path** (DEC-24). The model passes the
  `[image a3f2c1]` handle it was shown; it never sees a sha256, a filesystem path,
  or a key. `media_fetch` resolves the handle **within this conversation's own
  `media_json`** — the same scope boundary `read_buffer`/`room` enforce, and the
  reason it is a closure value, not a parameter: a handle from another
  conversation must be un-resolvable, not resolved-then-refused (design/12 §3).
- **The blob is read through the store Nora already has**:
  `FilesystemBlobStore.get(key=sha256)` returns the decrypted plaintext (it
  `open()`s the `BlobCipher` seal — media_fetch never touches the cipher or the
  key itself). A missing blob is `unavailable`, never a guess.
- **No ingest change.** The write path is untouched; this is purely a new read.

## 2. The MiMo call — a reasoning vision model, called the way the probe proved

- Through the **process-wide OmniRoute `AsyncOpenAI` client** (audit T2 — one
  client, never one per tag; `nora.agent.factory.create_client`), model
  `opencode-go/mimo-v2.5`, the image as a base64 `data:` URI in an `image_url`
  content block.
- **MiMo is a reasoning model**: it needs a **generous `max_tokens`** and
  **`reasoning_effort="low"`** (the probe's shape — a thin budget truncates the
  answer to empty). These are config, not magic numbers; `config.toml`'s `[media]`
  section gains `vision_model` / `vision_max_tokens` / `vision_reasoning_effort`
  so they are tunable without a code change (DEC-8).
- **The prompt is fixed and small** — "describe this image; if it contains text,
  transcribe it" — authored, not model-supplied. The only model-supplied value is
  the handle.

## 3. Cache — keyed by content hash, because content is the identity

The sha256 IS the content, so a description is valid forever for that hash: the
same meme forwarded twice is one MiMo call, not two. `media_fetch` caches the
description **keyed by sha256**, checked before any MiMo call.

- **In-memory, per process** for this build — a bounded LRU on the media client
  (process lifetime). Cheap, and the hot case (the same image referenced twice in
  one conversation) is covered. **Persistence is deferred**, deliberately: a
  restart re-describing a re-referenced image is a MiMo call, not a correctness
  bug, and a persisted caption store is its own schema decision.
- The cache counts hits/misses (`nora_media_fetch_total{status, cached}`) so
  "MiMo is never actually called" can never be read from silence (the DEC-31
  instrument rule).

## 4. Three states, and the untrusted-egress rule

`status` is `ok` / `nothing_found` / `unavailable` (R-27's discipline):

- **`ok`** — `description` is MiMo's sentence about the image.
- **`nothing_found`** — the handle resolves to no blob in THIS conversation.
  Distinct from `unavailable`; the model must say "I can't find that image here",
  never "I couldn't look".
- **`unavailable`** — the blob is missing on disk, or MiMo did not answer. Say so;
  never serve it as `nothing_found` and never invent a description (R-5).

**The description is UNTRUSTED, and this is the load-bearing security point.** It
is text a vision model wrote about bytes a group member sent — the same trust
class as a fetched web page (design/14 §2.1), and strictly less trusted than a
tool result Nora authored. Two consequences:

- **Identifier screen, like `about`.** A photo of a business card or a screenshot
  of a contact makes MiMo transcribe a phone number or an ACI; that identifier in
  the tool result would cost the whole reply at R-18. So the description is run
  through **`scan_identifiers`** and, if it carries one, withheld with an
  honest note ("that image has contact details in it I won't read out") rather
  than handed back — the exact shape `about` uses on Honcho conclusions.
- **Prompt-injection surface.** An image can carry text that reads as an
  instruction ("ignore your rules and…"). The description is data, never
  instruction; it enters the transcript as a tool RESULT, which the agent already
  treats as content, not as a directive — but the spec names it so the reviewer
  checks the description is never concatenated into a system/prompt position.

## 5. Budget, gating, and where it sits in the tool table

- **A `NATIVE_TOOLS` entry with a budget** (DEC-10). Sized like `about` (paid,
  external, slow): **2** — enough to look at the two images a message might name,
  a cap on how much a turn can spend on vision. `terminal=False`,
  `time_shiftable=None` (a picture does not go stale the way a bus does).
- **Gated behind `[media] enabled`** AND a present `MEDIA_ENCRYPTION_KEY` AND a
  reachable OmniRoute. With any absent, the tool is `unavailable` (never a crash,
  never a silent empty) — the `route`-with-no-key shape. Today `[media]
  enabled=false`, so this ships **inert-by-config** until the operator turns
  custody on, which is the honest default for a capability that reads people's
  photos.
- **Its `when` line names the trigger**: someone asks about an image that is in
  the conversation. It must NOT fire on a bare `[image]` with no handle (custody
  holds nothing) — that is `nothing_found` by construction.

## 6. What must be true before it ships

1. The handle resolves **only within this conversation's `media_json`**; a handle
   from another conversation is un-resolvable (a probe proves a cross-conversation
   handle returns `nothing_found`, not someone else's image).
2. The blob is read via `FilesystemBlobStore.get` (decrypt path), and a missing
   blob is `unavailable`, not a guess.
3. The MiMo call goes through the process-wide client with `reasoning_effort=low`
   and a generous `max_tokens`; the model/tokens/effort are config, not literals.
4. The description is cached by sha256; a second fetch of the same hash makes no
   MiMo call (probed by a fake MiMo whose call-count is asserted), and
   `nora_media_fetch_total{cached}` distinguishes the two.
5. The description is run through `scan_identifiers` and withheld (with an honest
   note) when it carries one — reverting that screen turns a test red on an image
   whose transcription is a phone number.
6. Three states are distinct and never reported as each other (R-27); a fake MiMo
   that is down yields `unavailable`, a missing handle yields `nothing_found`.
7. The tool is gated behind `[media] enabled` + key + reachable OmniRoute, and is
   `unavailable` (never crash, never empty) when any is absent.
8. `media_fetch` has a `NATIVE_TOOLS` budget of 2 and a RULE test; the fake MiMo
   behaves like the real server (returns the wrong-shape for a wrong call — the
   CLAUDE.md fake rule), so reverting the code under test turns a test red.

## 7. Decision-index entries this creates

- **DEC-56** — `media_fetch` reads custody's blobs through MiMo-V2.5, scoped per
  conversation, cached by content hash, with the MiMo description treated as
  untrusted egress (identifier-screened like `about`). Image only; inert-by-config
  behind `[media] enabled`. Criteria 1–8.
- A RULE test in `tests/test_acceptance_rules.py::RULE_TESTS`.

(DEC number is provisional — reconciled against the Postgres/route/flip branches
at merge time.)
