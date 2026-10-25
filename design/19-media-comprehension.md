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

> Content-policy update, 2026-10-25: phone/UUID/coordinate, place/person-name
> and location audience filters described here remain implemented and tested in
> enabled mode, but runtime defaults them off through `[content_checks]`.
> Credentials, doctrine, scope, consent and availability are independent.
> See the [current decision](../docs/adr/0030-content-filters-are-disabled-by-default-and-reversible.md).


> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


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
`nora.domain.media_join`, on every render on all EIGHT paths — and separately
at the Honcho feed, which does not render (§5.1).

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

**Honcho is screened at the FEED, and that is not a contradiction.** The
paragraph above is true of the transcript and false of memory: before this
change the same text rode in `message.body`, which the custody stage screened
before `memory_feed` forwarded it, and the fork has two consumers — only one of
which renders. A comprehension written unscreened into a peer is worse than one
written into a transcript, because nothing re-screens a peer card at render. So
`nora/ingest/comprehension.py::_feed` runs the same screen before it feeds, and
a hit costs the detail and keeps the marker, exactly as the render join does.

### Every transcript builder, not just the tag one

**The first cut of this put the join on `TranscriptInjection` and nowhere
else**, and that was a regression rather than an omission. On master the
sticker description and the voice-note transcript were baked into
`messages.body`, so EVERY consumer of a transcript had them. Behind a join one
consumer performs, five did not:

| builder | what it renders for |
|---|---|
| `nora/engagement/context.py` | the tag prompt |
| `nora/app/fire.py` | reminders, announcements, heartbeat, participation |
| `nora/engagement/stages/debrief.py` | the tag-path standing fold |
| `nora/ingest/stages/standing_event.py` (deleted 2026-10-03, DEC-277) | the event-driven fold |
| `nora/app/runtime.py` | the Dreamer's batch — both scope-keyed adaptors, including the note tier's candidates pass |
| `nora/language/note_curator.py` (deleted 2026-10-03, DEC-277) | a note's evidence |
| `nora/app/adoption.py` | lazy adoption's Honcho back-feed |
| `nora/agent/tools.py` | `load_transcript`, the one builder inside a tool |

`load_transcript` is the subtlest of them and was missed twice: it hands
the model rows the PREFILL has already rendered with their comprehensions,
so a join it does not perform puts `[image a3f2c1: a plate of noodles - …]`
and `[image a3f2c1]` in the same prompt — DEC-80's disagreeing-renderers
bug, in the one tool whose own comments say it exists not to reproduce it.
A bare handle there does not read as a formatting difference; it reads as
«nobody has looked at this», which is a claim about the record.

The note tier is on that list, which is what makes this more than tidiness:
«ten photos of a meal and the memory holds only the replies» is §0, and the
note tier is one of the things that builds memory.

`nora.domain.media_join.MediaJoin` is the one implementation. It lives in the
DOMAIN because `nora/language/` sits two layers below `nora/agent/` in the
import contract and could never have reached a join beside `scan_identifiers`;
the screen is INJECTED instead, which puts the class at the bottom of the graph
where all eight callers can import it. `tests/architecture/
test_media_join_reaches_every_transcript.py` is the gate — a static read that
every builder imports it and calls `enrich`, with each builder's own store read
as the positive control that it is still a builder at all.

**Two Honcho paths, and the asymmetry is deliberate.** The LIVE ambient
path is fed by `nora/ingest/comprehension.py` at the moment the read
completes — the comprehension has just been made and the row it belongs to
was audited seconds ago, so feeding it there is one call with everything in
hand. Lazy adoption is the REPAIR path: it replays rows whose sender mapped
to nobody at ingest, months later, and the comprehension for those bytes
already exists — there is nothing to feed at read time because the read is
long over. So the join is what carries it, out of `media_objects`, at the
moment the row is finally attributable. Neither one is a fallback for the
other and a row cannot take both: `_feed` is skipped for an unmapped
sender (D6), which is exactly the set adoption later repairs.

**Scoped by `media_json`, never by the body.** A handle is six hex characters
and a body is sender-supplied, so resolving `[image a3f2c1]` out of prose would
let a member type another room's handle and have its description rendered here.
`media_json` is written by custody from bytes Nora held for THAT row.
`UnattributedRow` gained the column for exactly this — without it, adoption
could not join safely at all.

**And the LABEL is taken from the record too**, because the body is the only
place `voice note` survives (it and `audio` are one media type) and
`voice note` is the one label whose detail renders UNCLIPPED. A member who
typed the literal string `[voice note a3f2c1]` could therefore switch off
`comprehension_render_chars` — the only bound on per-attachment prompt spend
— and push a whole OCR'd page into eight renders, two of which write to
Honcho. A claimed label is now accepted only when it AGREES with the media
type custody recorded (`label_agrees`): the body may refine, never
contradict.

### Why the join is per-builder, and what would reopen that

**It is opt-in at each call site, and this branch's own history is the argument
against that.** It reached one of eight builders, then seven; the eighth
(`load_transcript`) was missed twice, including by the gate written to catch
exactly that class of miss. Four review rounds found media-join placement bugs
and every one of them was a call site that did not call.

The placement that makes the leak unreachable **by construction** is doing the
join inside the READ — a decorating `RecordStore` that returns already-joined
rows, sitting below all eight builders, so a builder cannot forget because it
never had the option. That would have made the round-two and round-three bugs
impossible rather than merely caught.

It is not what shipped, for three reasons worth stating rather than assuming:

1. **A decorator makes the join unconditional and invisible.** `nora persons
   pending` renders unattributed rows for a human on a CLI; the operator
   surfaces want the stored body, and a store that always joins gives every
   reader the model's view whether or not a model is reading. The exemption
   would move from one line in a gate to a second un-decorated store, which is
   the same forgettable decision one layer down.
2. **The `path` label stops existing.** The eight callers differ by two orders
   of magnitude in blob count and by everything in latency budget; below the
   read, the join cannot know which one it is serving, and the cost instrument
   that made §10's reopening condition readable collapses to one series.
3. **`RecordStore` is a port with a contract suite and two implementations.**
   A decorator either wraps both (and the fake stops being a fake) or wraps
   one (and the two disagree about what a row's `body` says) — which is the
   drift the contract suite exists to prevent.

**The reading that would reopen it:** a NINTH builder shipping unjoined. The
AST gate (`tests/architecture/test_media_join_reaches_every_transcript.py`)
derives the builder set from the source and fails on any module reading the
record store without joining, so the next miss should be a red test rather than
a review round. If one gets past it anyway, the gate is not the answer and the
decorator is — take the three costs above and pay them.

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
that stays inline. **So does the WRITE.** The custody stage puts the transcript
on the row itself, without waiting for any model — the words were captured
synchronously, and holding them behind a spawned task meant two things, both
wrong: with `[media] comprehension = false` the job does not exist and the words
were discarded outright (STT ran and its output went nowhere), and with it on,
somebody asking 「佢講咩」 two seconds later saw a bare handle because the words
were queued behind a title. The comprehension pass adds title and description
afterwards over the same row, passing the identical `content` through, so the
two writers cannot disagree about the words; the job's already-read gate asks
whether the TEXT PASS has run for a content label rather than whether the row is
non-empty, or the inline write would lock the title out forever.

The title and description come from a cheap TEXT pass — never a second listen,
which would spend a larger call to learn what Nora already knows. A failed text
pass leaves the transcript with no title: the words are evidence, the title is a
convenience. Non-voice audio (`[media.describe.audio] enabled = false`) stays
inert.

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
* `content` is the chunk outputs **in order, timecoded** — `0:00 — …`, with no
  square brackets anywhere, because `sanitize_description` strips `[` and `]`
  (a description may not forge a placeholder) and a stored shape the renderer
  silently rewrites is two shapes. `title`/`description` are one cheap TEXT
  pass over them — not a second pass through the video, which would double the
  cost of the read for a few words. **With no text model configured the read
  still stands**: `content` is the expensive half, and `media_detail` falls
  back to it when there is no headline, so a titleless row reaches the prompt
  with its window run instead of rendering bare while `comprehended` stops the
  job retrying it.
* **The caveat is its own field**, in `provenance["media_caveat"]`, not glued
  onto the description. Appended to it, the transcript's
  `comprehension_render_chars` bound clipped it off — worst of all when the
  description was rich, which is exactly when a confident partial read does the
  most damage. Every renderer appends it LAST, after its own clip. And **`media_fetch` opts the whole VIDEO modality out
  of the vision cache, in both directions.** Caching the run without the
  caveat served #117's own bug back through the cache; caching it WITH the
  caveat is worse, because raising `chunk_max` then serves a stale «I have
  not seen the rest» from the durable table forever about a clip now read
  whole. A chunked read is a function of (bytes, chunk geometry) and the
  cache is keyed on bytes, so the geometry-dependent modality opts out —
  the same call layer 2 makes. `media_objects` is the right cache for a
  clip because it stores the caveat WITH the reading.
* **The fix is not «never truncate» but «never truncate silently».** `chunk_max`
  is a real ceiling (10 × 90 s = 15 minutes read in full, both configurable
  under `[media.describe.video]`), and when it binds, the clip's length, how
  much reached a model, and the fact that the rest did not are written into
  `media_objects.provenance` AND said in the description. An UNMEASURABLE
  duration is `complete=False` as well: unknown is not covered, and the note
  says so rather than inventing a number.
* One refused window is a **visible gap** in the timecoded run, not a refused
  read — failing the whole thing over one bad window would throw away fourteen
  good minutes. Two different things can refuse a window and BOTH are said:
  ffmpeg failing to cut it (the window never exists, `covered_seconds` shrinks,
  and `VideoCut.note()` says so because it keys on `complete` rather than on
  `capped`) and the MODEL failing to read one it did cut (the gap marker is
  rendered in `content`, and `describe_coverage` measures the sentence against
  the windows a model actually described rather than against what ffmpeg
  produced).
* **A window carries its own start time.** `VideoWindow.start_seconds` is what
  the timecode is rendered from, never the position in the list: a dropped
  window would otherwise shift every later label, and a 6-minute clip losing
  its 1:30 window rendered the 3:00 content as `[1:30]` — Nora saying «at 1:30
  he does X» about something at 3:00.
* **`media_fetch` reads a clip the same way.** The deliberate look and the
  automatic one share `read_video_windows`, because the first cut of this fixed
  the ingest path and left `media_fetch` on the old 120-second single view —
  the surface she reaches for when she CHOOSES to look was still answering
  about the first two minutes of a ten-minute clip as though it had seen all of
  it.

`provenance` MERGES on a comprehension write (`jsonb ||`) rather than being
replaced: what a READ leaves behind is a fact about the reading, and a generated
image's prompt/provider/aspect_ratio is a fact about its birth. Both have to
coexist on one row, and DEC-138's «the birth record is never overwritten» still
holds for every key the read does not own. It **travels on the result**
(`MediaDescription.provenance`) rather than being stashed on the adapter: the
comprehension job spawns one task per subject, so a message carrying a video and
a photo runs two `describe` calls concurrently on one shared `MediaComprehender`
— stashed state had the still's reset racing the video's chunk loop, and
single-flight is per-sha256, not per-instance.

`video_covered_seconds` and `video_complete` are computed from the windows a
MODEL described, not from the cut: ten clean windows with nine model refusals
would otherwise record «whole clip covered» beside nine gap markers in the
content, which is the same partial-read-as-complete-answer one level in.

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

**And the question is screened on the way OUT** (operator, 2026-09-08).
`nora/agent/egress.py` names an argument to a third party as never safe, and
`web_search` screens its query before the call for exactly that reason — two
egress points in one process with two different rules is how the next
contributor learns the wrong precedent. A question that trips
`scan_identifiers` is refused `nothing_found` and never sent, never stripped
and re-sent (which would answer a question she did not ask), and the value is
never echoed back.

What this buys is narrow and worth saying plainly rather than overclaiming:
the media BYTES reach that provider on every read anyway, so the screen adds
nothing for an identifier that is in the picture. It protects one that is in
the CONVERSATION and not in the media — a photo of a business card, and a
question Nora composed from the chat text around it.

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
| `nora_media_join_seconds` / `nora_media_blobs_joined` | the join's COST, now on EIGHT paths rather than one, labelled by which one paid it (`JOIN_PATHS`, which the help text is derived from). §10 names the join as what would reopen this decision, and a reopening condition nothing can read is a hope rather than a condition. Observed only when the join actually ran, so a window with no media never dilutes it. |
| `nora_egress_blocks_total{kind}` | an identifier in a comprehension, blocked — at the render join AND at the Honcho feed. |
| `nora_media_join_total{path,status,reason}` | R-27 on the join: `ok` rendered a detail, `nothing_found` read the index and nothing in the window was comprehended yet, `unavailable` is the index refusing to be read or the query timing out. The distinction is only expressible because `MediaObjectStore.lookup` carries an availability axis that `get` structurally cannot. |

**There is no `nora_errors_total{stage="media_join"}`, deliberately.** An
earlier cut declared one and wrapped the store read to increment it — but
`MediaObjectStore.get` catches everything and answers `None`, which its
contract suite pins for both implementations, so that label could never be
reached: a series genuinely always zero, which CLAUDE.md names as a lie. The
join still catches (a fake or a future implementation could break the port's
promise, and this runs on a reply's critical path), and it degrades to the
bare handles; it does not claim to have measured something it cannot.

A cache hit is deliberately **not** counted `ok`: nothing was comprehended on
that pass, and counting it would make the ok rate say the describer is working
when it might not have run for days.

The comprehension job opens its **own root span**, declared `generation`
(R-76). Spawned work does not inherit the caller's context, and attaching to
the ingest root would hold that trace open for as long as a video read takes —
so it detaches. It does not follow that it should also be DROPPED, and the two
were one decision until 2026-09-09: the root declared `plumbing`, whose own
definition is "no model call anywhere under it", over a job whose every branch
is a describer or a summarizer. Measured rather than argued: 1041 live traces
held zero `comprehend` roots while `nora_media_comprehension_total` counted the
reads happening. See `docs/observability.md`.

## 9. What is verified against what

| claim | evidence |
|---|---|
| the body carries a label and a handle, and the render carries the reading | `tests/test_acceptance_rules.py::test_r90_…` — one row written by the real custody stage, read back through the real assembler |
| a failed read never writes a blank | `tests/test_acceptance_rules.py::test_r51_…` (R-51), with a positive control that DOES comprehend |
| one pass, three fields | `tests/contract/test_media_interpreter_contract.py`, parametrized over `FakeMediaInterpreter` and `MediaComprehender` |
| custody's row and the read's row are the same row, in either order | `tests/contract/test_media_object_store_contract.py::test_a_comprehension_that_beats_custody_creates_the_row` |
| a voice note keeps its words | `tests/unit/test_media_join.py::test_a_voice_note_renders_its_whole_transcript_and_is_never_clipped`, `tests/unit/test_media_comprehension.py::test_a_voice_note_keeps_its_words_even_when_the_summary_fails` |
| layer 2 writes nothing | `tests/test_media_fetch.py::test_layer_2_writes_nothing_to_either_cache_tier`, with a positive control showing the cache IS written on the no-question path |
| every transcript builder joins, not just the tag one | `tests/architecture/test_media_join_reaches_every_transcript.py` |
| the join screens, clips, and never clips the caveat | `tests/unit/test_media_join.py` |
| a forged `[voice note …]` cannot switch off the render bound | `tests/unit/test_media_join.py::test_a_forged_label_cannot_switch_off_the_render_bound` |
| an unreadable index is `unavailable`, never `nothing_found` | `tests/unit/test_media_join.py::test_an_unreadable_index_is_unavailable_and_never_nothing_found` |
| no await inside a held conversation slot is unbounded | `tests/architecture/test_slot_held_awaits_are_bounded.py` |
| a new transcript builder cannot be forgotten | `tests/architecture/test_media_join_reaches_every_transcript.py::test_the_builder_set_is_not_hand_maintained` (derived from the source) |
| every fix in this bundle fails on revert | the mutation sweep in the round-four commit message — 18 reverts, 18 reds |
| a handle a sender TYPED never resolves | `tests/unit/test_media_join.py::test_a_handle_a_sender_typed_is_never_resolved` |
| layer 2's authored frame is actually there | `tests/test_media_fetch.py::test_layer_2_wraps_the_question_in_the_authored_frame` (probed: `prompt = question` turns it red) |
| a question carrying an identifier never leaves | `tests/test_media_fetch.py::test_a_question_carrying_an_identifier_never_reaches_the_provider` — asserted on the PROMPT that reached the seam, not on a call count |
| the video cap is said, not just done | `tests/unit/test_vision_preprocess.py::test_the_cap_binding_is_recorded_and_said`, `tests/unit/test_media_comprehender.py::test_a_capped_video_says_so_in_the_description_and_in_provenance` |
| a read shortened by a REFUSED window is also said | `tests/unit/test_vision_preprocess.py::test_a_read_shortened_by_a_refused_window_says_so_too`, `tests/unit/test_media_comprehender.py::test_a_model_refusal_shortens_what_the_read_may_claim` |
| a timecode names the window it came from | `tests/unit/test_media_comprehender.py::test_a_dropped_window_does_not_shift_its_neighbours_timecodes` |
| the deliberate look chunks too | `tests/test_media_fetch.py::test_media_fetch_reads_a_clip_in_windows_and_admits_what_it_missed` |
| a comprehension carrying an identifier never reaches Honcho | `tests/unit/test_media_comprehension.py::test_an_identifier_in_a_comprehension_never_reaches_memory` |
| a voice note's words survive with the read switched off | `tests/unit/test_media_custody.py::test_a_voice_notes_words_are_recorded_even_with_no_comprehension_wired` |
| the read runs off its own trace root | `tests/unit/test_media_comprehension.py::test_the_read_opens_its_own_root_rather_than_hanging_off_the_ingest_trace` |

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
  after the existing `gather`, and `nora_media_join_seconds` /
  `nora_media_blobs_joined` (labelled by path) are what make that readable
  rather than arguable. The batched read the earlier version of this bullet
  named as the answer is now what it does; if the latency is still visible in
  `nora_tag_latency_seconds`, the next move is caching the join's OUTPUT per
  window, not moving it back into the body.
* **`comprehension_render_chars` is the wrong number.** 240 is a guess against
  no measurement of what a title+description actually costs on a real window.
* **A describer that will not answer in JSON at all.** The fail-soft parse
  degrades to the old behaviour, so this would show as titles being empty
  everywhere rather than as failures — watch for `title = ''` across
  `media_objects` rather than for a red metric.
* **`chunk_seconds = 90` / `chunk_max = 10`.** Both are the middle of the
  operator's stated range and a round ceiling, not measurements. A clip that
  routinely exceeds 15 minutes in this group would move them.
