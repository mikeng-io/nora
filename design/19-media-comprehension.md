---
kind: design
title: "Nora — Media comprehension: one read per medium, three fields, joined at render"
description: "Why one durable string could not serve both Honcho and the prefill, what the three LLM-derived fields are, why the read is spawned while custody stays inline, where the identifier screen moved and why that is stricter, and how a long video is read in chunks that admit their own ceiling."
tags: [nora, design, media, comprehension, honcho, transcript, video, egress]
timestamp: '2026-09-08'
edges:
  - {dst: 18-media-custody, rel: extends, provenance: asserted}
  - {dst: 09-decision-index, rel: indexed-by, provenance: asserted}
---

# Media comprehension

Issues **#116** (the three fields, the join, the two layers) and **#117** (the
chunked video read). Both landed on one branch: video's `content` shape is part
of the comprehension contract, and two branches cut from one alembic head would
each write `down_revision = <that head>`, git would merge both files without a
conflict, and the container would die at boot on two heads.

## 0. The gap, in one story

Miles sends ten photos of a meal. The group replies 「哇好正！喺邊度？」, 「幾錢啊？」.

What Honcho held:

```
(an image)
哇好正！喺邊度？
幾錢啊？
```

The `[image a3f2c1]` marker rides in `messages.body`, so memory knew a photo was
sent. It did not know it was a meal, what the sign in the background said, or
what was on the plate. **The subject of the conversation is the one thing not
recorded.** Every reply around it is preserved and unreadable without it.

That is the whole of it: the marker stays, and what it points at stops being
empty.

## 1. Why the old shape made it impossible, not merely untuned

`messages.body` is one string and **both consumers read it verbatim**:

* `nora/ingest/stages/memory_feed.py` → `text=message.body` → Honcho, which
  wants everything;
* `nora/agent/prefill.py` → `escape(row.body or "")` → `<group_transcript>`,
  which wants a line.

Sticker descriptions were baked INTO that string by `body_with_handles`. So
"Honcho detailed, transcript short" was not a tuning problem with a knob nobody
had turned — it was structurally unreachable while one string served both, and
the 200-character `max_description_chars` clip existed only because every
character of it was paid for on every prefill forever.

The description had to stop riding in `body` and live in `media_objects`, with
each consumer selecting the fields it wants at render time. That is **R-90**.

## 2. Three fields, one call

`media_objects` gains `title` and `content` beside `description` (migration
**0052**; `describe_model` already existed):

| field | what it is | who reads it |
|---|---|---|
| `title` | what the thing IS — a few words, how a person would refer to it | transcript, `media_search`, Honcho |
| `description` | what it SHOWS — a sentence or two | transcript, `media_fetch`, Honcho |
| `content` | everything legible or audible IN it, in full — the text on the whiteboard, the words in a voice note | `media_fetch`, Honcho |

**One model pass produces all three, and that is an economics argument rather
than a taste one.** Reading the bytes is what costs — tokens, latency, quota.
Asking the same call for a title and for the legible content as well is
near-free. So `max_description_chars` goes: the stored comprehension is
unbounded, and the bound moved to the RENDER
(`[media] comprehension_render_chars`), where the tokens are actually spent.
`media_fetch` is what serves the rest.

**All three empty is an INDEX ENTRY, not a description of nothing.** Custody
writes a row for every byte Nora holds, before any model has run; the row says
she holds the bytes and claims nothing about them. `MediaObject.comprehended`
is the predicate everything downstream branches on — without it, a job that
treated "a row exists" as "already read" would never read a single photo.

## 3. Custody inline, comprehension spawned

```
ingest (inline, unchanged)   bytes -> sha256 -> encrypted blob -> body = "[image a3f2c1]"
comprehension (spawned)      one model pass -> {title, description, content}
                             -> media_objects
                             -> Honcho (all three)
transcript render            join media_objects -> [image a3f2c1: <title> - <description>]
media_fetch                  layer 1: serve the stored reading
                             layer 2: a caller's question, cache-bypassed
```

The asymmetry is the argument. `[image a3f2c1]` is a **promise that `a3f2c1` can
be produced**, so the copy runs before the audit write — spawning it would write
the promise first and keep it second, which is the exact A9 violation
`design/18` exists to end, reintroduced in a smaller size (DEC-43 §5).

**Comprehension has no such property.** An un-comprehended image is exactly the
state every image was in before this existed, so a comprehension that lands
late, or never, has not made anything untrue. `memory_feed` is already spawned
on precisely this reasoning and Honcho is already never load-bearing.

And inline would be untenable in the one case that matters. Signal ingest is
serialized — the listener awaits each message's stages in turn — so an inline
video read (chunked, several model calls, tens of seconds; §6) would stall every
message behind it in the room.

The job re-reads the bytes from the `BlobStore` rather than carrying them across
the spawn. Two reasons, and the second is the real one: a burst of ten photos
would otherwise hold ten payloads alive on the listener's own loop until a
background task drained them, and the handle in the body is a promise those
bytes are retrievable — the job exercising that promise is the honest thing for
it to do.

## 4. One mechanism, not two

Stickers were the only medium ever read automatically, on the argument that
pack art is published while a photo is somebody's life. That argument was about
what may be READ; it is not an argument for two mechanisms, and §0 is what it
cost.

So `StickerReader` keeps the half of its ladder that is about CUSTODY — the
platform identity is known at parse time, so a repeat sighting costs zero
platform RPC, measured at 41% of sightings and rising as a group's sticker
vocabulary saturates — and loses its model rung to the comprehension job. Its
`remember` call now writes `description=""`, meaning «index this identity,
describe nothing»; `SqlMediaDescriptionStore.remember` keeps whatever
description is already on the row rather than clobbering it, or every re-sighting
of a sticker would erase what the job had learned about it.

`MediaInterpreter`'s docstring said "stickers only". It does not any more. It
gained a `label` argument (a member of `LABELS`, derived from the platform's own
flags and the blob's MIME type — a system-controlled fact, never anything a room
typed) which selects the authored prompt, and a second verb, `summarize`, for
the audio lane.

`nora_sticker_describe_seconds` was **removed** with the rung it measured — a
declared metric with no call site is a lie, and it would have rendered as a
series that is genuinely always zero. `nora_media_comprehension_seconds`
replaces it, with buckets that reach 300 s because a chunked video read is ten
model calls rather than one.

## 5. Where the identifier screen went, and why that is stricter

`scan_identifiers` used to run once, at ingest, over the placeholder run the
custody stage composed. It runs at RENDER time now, in
`nora/engagement/context.py`, on every render.

**That is not a relaxation.** `media_objects` is a durable cache keyed by sha256
and shared across rooms and across restarts, so a row screened once at write
time would be served unscreened for the life of the deployment. It is
`nora/ingest/stickers.py`'s own rule — «a screen applied before caching is one a
cache hit skips» — applied to the path that finally has a cache in it, and it is
the same discipline `_serve_media` has always followed on every serve.

A hit costs that attachment's DETAIL and nothing else: the placeholder renders
bare, exactly as an un-comprehended one does, and the message is untouched. What
the screen does not catch is unchanged and stated where it always was
(`_serve_media`'s docstring): a bare local number passes, which is R-18's
standing blind spot rather than a solved case.

**The join belongs in the assembler, not in the renderer.** `nora/agent/
prefill.py` is a pure function of the `TagContext` it is handed; giving it a
store would make one prompt's text depend on a database read taken at render
time, which is the property that makes a prompt reproducible from its trace.
`TranscriptInjection` is where reads live, so that is where the join is.

### The voice-note exception, written where a reader will hit it

A voice note renders its **full `content`** — the words somebody actually said —
rather than title+description, and the render bound does not apply to it.
`nora/ingest/stages/media.py` has said since the STT lane landed that a voice
note is conversation CONTENT, not an attachment description; collapsing it would
mean Nora stops hearing what people said and starts reading a summary of it.
The title and description are added for Honcho and for search; they do not
replace the words. `CONTENT_LABELS` in `nora/domain/media.py` is the one place
this is expressed, as a set rather than a string comparison, so a future label
with the same property is one entry rather than a second branch.

## 6. Audio, and video

**Audio.** A voice note's `content` is already in hand: `nora/infra/speech.py`
(SenseVoice, on the local `stt` service) transcribes it INLINE at ingest, and
that stays inline. The title and description come from a cheap TEXT pass over
those words — never a second listen, which would spend a larger call to learn
what Nora already knows. A failed text pass stores the transcript with no title:
the words are evidence, the title is a convenience. Non-voice audio
(`[media.describe.audio] enabled = false`) stays inert.

**Video (issue #117).** `VIDEO_MAX_SECONDS = 120` cut every clip before the
model saw it, with no metric, no log line, and nothing in the answer admitting
it — a ten-minute video produced a description of its first two minutes, phrased
as a description of the video. A partial read served as a complete answer, which
is R-5's shape exactly.

* **Storage is untouched.** One blob, the whole original. `cut_video` produces
  transient windows at READ time and discards them — exactly what
  `preprocess.py` already did with its single view.
* Each window goes to the **existing** describer (kimi direct) unchanged. No new
  provider, no frame extraction of our own (the provider samples frames
  internally), and `-an` stays, so the chunks are silent: audio extraction plus
  STT was proposed and rejected as scope.
* `content` is the chunk outputs **in order, timecoded**. `title`/`description`
  are one cheap TEXT pass over them — not a second pass through the video, which
  would double the cost of the read for a few words.
* **The fix is not «never truncate» but «never truncate silently».** `chunk_max`
  is a real ceiling (10 × 90 s = 15 minutes read in full, both configurable
  under `[media.describe.video]`), and when it binds, the clip's length, how
  much reached a model, and the fact that the rest did not are written into
  `media_objects.provenance` AND said in the description. An UNMEASURABLE
  duration is `complete=False` as well: unknown is not covered, and the note
  says so rather than inventing a number.
* One refused window is a **visible gap** in the timecoded run, not a refused
  read — failing the whole thing over one bad window would throw away fourteen
  good minutes.

`provenance` MERGES on a comprehension write (`jsonb ||`) rather than being
replaced: what a READ leaves behind is a fact about the reading, and a generated
image's prompt/provider/aspect_ratio is a fact about its birth. Both have to
coexist on one row, and DEC-138's «the birth record is never overwritten» still
holds for every key the read does not own.

## 7. `media_fetch`, in two layers

**Layer 1 serves the stored reading** — title, description and the untruncated
content — with no model call, no blob decrypt and no wait. It is the SAME
reading the transcript rendered a line of, so the tool cannot contradict the
prompt about what a picture shows. A voice note reaches this and nothing else:
there is no vision path for one, but its `content` IS the words, and refusing to
hand back something already in the record would be withholding for no reason.

**Layer 2 takes a caller-supplied question**, reads the media again with it,
**bypasses the cache on read and writes nothing back.**

That is not an optimisation. The whole cache rests on «a description is a pure
function of the BYTES, so a sha256 hit is valid forever». A query-conditioned
answer is a function of **(bytes, question)**: serving one from the cache would
answer somebody else's question, and writing one to
`media_objects.description` would poison that hash for every room and every
future sighting of the same image — the transcript would then render one room's
answer to one room's question as what the picture IS.

**Layer 2 breaks the authored-prompt invariant deliberately.**
`nora/infra/vision/client.py` states that describe prompts are «AUTHORED and
never model-supplied», because a prompt conditioned on what a room typed is
group-authored text steering a model whose output returns to that room. Layer 2
is Nora supplying one. It is written down here and at the call site rather than
slipped in as a parameter, and what makes it acceptable is that the prompt was
never the barrier: `_QUERY_FRAME` is authored and the model's question is DATA
inside it, and `scan_identifiers` still runs on every serve at the exit,
including this one.

## 8. Instruments

| name | what it says |
|---|---|
| `nora_media_comprehension_total{platform,label,status,reason}` | R-27's three states. `ok` = a comprehension was written. `nothing_found` = a real, final answer ABOUT the bytes: `unreadable` (a PDF, a lottie) or `already_read` (this sha256 was read before). `unavailable` = every wiring gap and every failure — `no_<top>_describer`, `blob`, `vision`, `timeout`, `empty`, `ffmpeg`, `error`. |
| `nora_media_comprehension_seconds` | one read's latency — the model call for a still, the whole chunk run for a video. Cache hits never reach it, so the dedup cannot dilute what it says about a describer. |
| `nora_sticker_descriptions_total{platform,status,level}` | now the identity ladder's CUSTODY hit rate. `status=ok` means the bytes are held; `level` is identity/content/none. |
| `nora_errors_total{stage="media_join"}` | the render join could not read the index. The transcript degrades to bare handles, which is what the row actually says. |

A cache hit is deliberately **not** counted `ok`: nothing was comprehended on
that pass, and counting it would make the ok rate say the describer is working
when it might not have run for days.

The comprehension job opens its **own root span**, declared plumbing (R-76).
Spawned work does not inherit the caller's context, and attaching to the ingest
root would hold that trace open for as long as a video read takes.

## 9. What is verified against what

| claim | evidence |
|---|---|
| the body carries a label and a handle, and the render carries the reading | `tests/test_acceptance_rules.py::test_r90_…` — one row written by the real custody stage, read back through the real assembler |
| a failed read never writes a blank | `tests/test_acceptance_rules.py::test_r51_…` (R-51), with a positive control that DOES comprehend |
| one pass, three fields | `tests/contract/test_media_interpreter_contract.py`, parametrized over `FakeMediaInterpreter` and `MediaComprehender` |
| custody's row and the read's row are the same row, in either order | `tests/contract/test_media_object_store_contract.py::test_a_comprehension_that_beats_custody_creates_the_row` |
| a voice note keeps its words | `tests/unit/test_engagement_context.py::test_a_voice_note_renders_its_whole_transcript_and_is_never_clipped`, `tests/unit/test_media_comprehension.py::test_a_voice_note_keeps_its_words_even_when_the_summary_fails` |
| layer 2 writes nothing | `tests/test_media_fetch.py::test_layer_2_writes_nothing_to_either_cache_tier`, with a positive control showing the cache IS written on the no-question path |
| the video cap is said, not just done | `tests/unit/test_vision_preprocess.py::test_the_cap_binding_is_recorded_and_said`, `tests/unit/test_media_comprehender.py::test_a_capped_video_says_so_in_the_description_and_in_provenance` |

**Not verified, and stated rather than glossed.** Nothing here has run against a
live gateway on this branch: the describers' actual JSON compliance, the real
per-chunk latency of a 15-minute clip through the kimi direct lane, and the
token cost of the render join on a full 40-row window are all unmeasured. The
JSON parse is fail-SOFT precisely because the first of those is unknown — prose
where an object was asked for becomes the description rather than an
`unavailable`, which is exactly what the pre-#116 path produced from the same
call.

## 10. What would reopen this

* **The render join costs a measurable amount of prefill latency.** It is one
  `get` per distinct blob in the window, bounded by `context_window` rows, taken
  after the existing `gather`. If a real window makes that visible in
  `nora_tag_latency_seconds`, the answer is a single batched read on the port,
  not moving the join back into the body.
* **`comprehension_render_chars` is the wrong number.** 240 is a guess against
  no measurement of what a title+description actually costs on a real window.
* **A describer that will not answer in JSON at all.** The fail-soft parse
  degrades to the old behaviour, so this would show as titles being empty
  everywhere rather than as failures — watch for `title = ''` across
  `media_objects` rather than for a red metric.
* **`chunk_seconds = 90` / `chunk_max = 10`.** Both are the middle of the
  operator's stated range and a round ceiling, not measurements. A clip that
  routinely exceeds 15 minutes in this group would move them.
