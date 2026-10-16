---
type: design
title: "instagram_read and instagram_search — the per-network social tool convention"
description: "Nora reads Instagram through two tools of its own, over the bridge only: instagram_read takes a person (by name), a pasted link or a typed handle, as three separate parameters, and returns profiles and stories now and posts once a mechanism exists; instagram_search takes free text. Since 2026-09-24 a post, reel or story comes back with what each item shows, read through media_fetch itself, beside the caption, from typed views and one renderer. The naming is the convention every later network follows."
tags: [nora, instagram, social, tools, instagram_read, instagram_search, catalogue, naming]
timestamp: '2026-09-24'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 09-tool-surface-and-naming.md, rel: realizes, provenance: asserted}
  - {dst: 10-discovery-retrieval-and-paging.md, rel: constrained-by, provenance: asserted}
  - {dst: 15-bridge-is-the-only-entry.md, rel: realizes, provenance: asserted}
  - {dst: 17-implementation-plan.md, rel: amends, provenance: asserted}
  - {dst: ../tool-catalogue/00-overview.md, rel: constrained-by, provenance: asserted}
---

# instagram_read and instagram_search

## Decision (operator, 2026-09-23)

Social reads split **per network**. This realizes the reopening recorded in
`09-tool-surface-and-naming.md`, under the two conditions it kept:

1. **The model names the network, never the account.** The tool takes a
   person, a link, or a handle a human typed. The person index or the bridge
   resolves the account. Nora never infers one.
2. **One read-only allowlist underneath.** However many tools sit on top,
   "what may Nora read" is one table (R-86).

Two things made the split affordable. The tool catalogue
(`design/tool-catalogue/`) cuts a tool's resident cost from about 400 tokens
to one catalogue line. And `15` gives Instagram exactly one backend, so the
tools for one network map onto one backend.

> **2026-09-24: the first reason no longer holds.** The catalogue's deferral
> switch (`[llm] defer_tools`) measured worse per turn and stays `false`
> (`../tool-catalogue/05-measurement.md` §3a), so every tool, these included,
> ships RESIDENT with its full schema. `instagram_read` costs its schema on
> every turn: 282 tokens (cl100k, `json.dumps` of its `tool_spec`, the 05 §1
> method). The same PR cut `social_fetch`'s schema from 847 to 486 by removing
> its Instagram rows and meeting ADR 0015, so the resident total moved −79
> (27,044 → 26,965, against master at `4cc8f89b`). The split is still the
> decision; what it costs is now measured rather than assumed away. 05 §3a
> names this tool set's growth as a re-measure trigger.
>
> **P11 (2026-09-24), the same method:** `instagram_search` costs **186**
> tokens resident, and `social_fetch` dropped 486 → 454 by losing its
> Instagram clause, so the resident total moved **+154** (27,246 → 27,400,
> against master at `71166ba5`). The text Strands sends for
> `instagram_search` is 123 tokens (Level 3 ceiling 320), with no dash and no
> non-Latin character in its spec.
>
> **P10 and P12 (2026-09-24), the same method:** `instagram_read` grew
> `handle` and `link` and lost its two required fields, so its schema went
> **282 → 366** tokens and the resident total **27,400 → 27,484 (+84)**,
> against master at `c893e581`. About 20 of the 84 are the four
> `"default": null` entries Strands writes for an optional parameter. The
> text Strands sends is 205 tokens (was 183; Level 3 ceiling 320), with no
> dash and no non-Latin character in its spec. After the #446 review's
> `what="story"`-with-a-profile-link clause: schema **388**, resident total
> **27,506 (+106 over 27,400)**, description 214.

> **ADR 0015 (proposed 2026-09-24), met from the first commit.** Both tools
> declare `level=3` on `NativeTool` (the field was added here, ADR 0015 §3).
> The text Strands sends is 183 tokens for `instagram_read` and 312 for
> `social_fetch`, under the Level 3 ceiling of 320. Neither has an em or en
> dash or any non-English string. `instagram_read`'s `what` is a `Literal`,
> so the schema carries the enum. Its description names the statuses and
> says each non-`ok` result carries a `note`; the notes say what to tell
> them.

## The naming convention, for every network

Chosen to follow the names Nora's tools already use, not a new scheme:

| Existing pair | Suffix meaning |
|---|---|
| `web_fetch`, `media_fetch` | `_fetch`: retrieve what is at an **address** (a URL) |
| `note_read` | `_read`: read a **known thing by what it is called** (a note by its title, a person by their name), not by where it lives |
| `web_search`, `note_search`, `media_search` | `_search`: **free text**, always its own tool, never a mode of fetch or read |

So each network gets:

| Tool | Input | Output |
|---|---|---|
| `<network>_read` | a person, a pasted link, or a typed handle | the subject's profile, stories, posts: one item or a paged list |
| `<network>_search` | free text | matching accounts (or posts, where the network searches posts) |
| a write, if one is ever wanted | its own tool per action, e.g. a story post | never a mode of `_read` |

`<network>_fetch` was the first draft and was rejected. `_fetch` in Nora means
address-keyed retrieval, and 09's central rule is "a subject, not an address";
a `_fetch` name would encode the thinking 09 rejects. It also folded search in
as a `what` value. `social_fetch` does that today (`search` is one of its
`what` arms), and it is the shape being replaced; everywhere else Nora keeps
search as its own tool (`web_search` / `web_fetch`, `note_search` /
`note_read`), because search takes free text and read takes a subject.

Instagram gets `instagram_read` and `instagram_search` first. `x_read` /
`x_search`, `threads_read`, `linkedin_read` follow the same shape later.

> **2026-09-24:** X and LinkedIn are designed in `../social-per-network/`
> (DEC-201): `x_read`, `x_search`, `linkedin_read`, `linkedin_search`, and
> `social_fetch` retired. Threads waits for a backend.

## `instagram_read`

```
instagram_read(what: str | None = None, person: str | None = None,
               link: str | None = None, handle: str | None = None,
               limit: int = 5, cursor: str | None = None)
```

> **As built in P5 (2026-09-24): `instagram_read(what, person)`, both
> required.** `link` and `handle` are not in the schema until P10, and `limit`
> and `cursor` are not in it until `posts` (P13), per §"Verbs join only when
> they work end to end" below: a parameter in the schema is a word
> advertising an input, and a resident schema is paid for on every turn. A
> story is returned whole (the client clamps frames and text on its side).
> The signature above is the P13 end state.
>
> **As built in P10 and P12 (2026-09-24): `instagram_read(what=None,
> person=None, handle=None, link=None)`, nothing required.** Exactly one of
> `person`, `handle`, `link` is checked in the tool (none, two, or a blank
> one is `unavailable` and nothing is sent). `what` stays the enum
> `profile | story` and is required for `person` and `handle`. A `link`
> decides the read: a profile link reads the profile, a story link the
> story, a post or reel link the post; `what` beside a link must name the
> same read, and anything else (including any `what` with a post link) is
> `unavailable`, «the link already says what to read», per the paragraph
> below. `posts` is NOT in the enum: a person's post list has no mechanism
> (P13), and a word in the schema is a word advertising it. A link given
> without a scheme (`instagram.com/p/...`) gains `https://` before it is
> parsed; the parser still decides everything else.
>
> **Amended 2026-09-24 (#446 review): `what="story"` with a PROFILE link is
> allowed** and reads that account's current story: «what's on her story
> <profile link>» names the account by link and the read by `what`. It goes
> down the handle path exactly, under every story check (asked turn, egress
> screen, Tier 0, the asking room's roster). Every other mismatch between
> `what` and a link stays `unavailable`, and `what` beside a link is checked
> against the enum at runtime, so `what="post"` with a post link is
> `unavailable` too, not a match.
>
> **Also 2026-09-24:** a story-ring link, `/stories/<user>/` with no frame
> id, reads that user's current story under the same checks (the link parser
> returns it with an empty `story_pk`). `/stories/highlights/<id>/` is a
> saved highlight, addressed by the highlight's own id, and is refused by the
> parser: `highlights` passes the username charset and would otherwise read
> as a story by `@highlights`. The transcript render keeps its measured
> shapes: it asks the parser with `story_ring=False`, so a shared ring link
> is labelled as before. The egress screen (`scan_identifiers`) runs on EVERY
> username about to leave, a link's as well as a typed handle's.
>
> **Amended 2026-09-24, after #471: `url` is `link`** (13 §«a long caption
> reads as the whole post»). Every replayed call from the deferred
> catalogue passed `url=`, which Strands dropped because the signature did
> not declare it, so each failed «exactly one». `url` is now a declared
> optional parameter, «same as link.» (22 tokens on the spec); `url` and
> `link` naming different links (compared as the link parser reads them,
> so two spellings of one post agree) are `unavailable`, and `url` counts
> as the link for
> «exactly one». `web_fetch` routes an Instagram page link here, as
> `media_fetch` does.

**Exactly one of `person`, `link`, `handle`.** Three parameters, not one
overloaded `target`: a person's name and an Instagram handle are the same
shape of string ("miles" could be either), and nothing could tell them apart
reliably. `media_fetch` already solves the same problem the same way (`code`
for something in the conversation, `url` for an address).

| Input | Means | Resolved by |
|---|---|---|
| `person` | a person, named the way the message named them (a nickname is fine) | **the tool** resolves the name with the same room-scoped lookup `about` uses (`room.scope().find`), because only the tool holds the turn's `room`; the router then takes the resolved person through the person index to their Instagram account. No match → `nothing_found`; several people → `ambiguous` (ask which). Then the index (`igids_for`, 2026-09-24) returns every igid on file: none → `nothing_found` with a note saying no account is on file and nothing was looked at; several → `ambiguous`, «I can't tell which, so I didn't look» (never a pick, and not «ask» — the room cannot settle it, only the file can); an unreadable index → `unavailable`. An unreadable room roster is `unavailable`. **No tool hands Nora a person id**, so the tool never asks for one |
| `link` | a pasted Instagram URL | the link parser; the LINK decides the read (profile, post, reel or story, `15` §Links) |
| `handle` | an Instagram username someone typed (no `@`) | the bridge (`GET /accounts?username=`) |

`what` is required unless the input is a `link`. A link whose kind
contradicts `what` answers `unavailable` rather than guessing which was meant.
`cursor` is only meaningful with `what="posts"`; passed with anything else it
answers `unavailable` (a profile or a story has no next page, `10`).

### Listing and reading one item are both `_read`; looking at the media is not

| `what` | Returns |
|---|---|
| `profile` | the account: bio, private or public, follow state both ways, follower / following / post / reel counts |
| `story` | the current story as a list of frames (whole; no paging) |
| `posts` (when it exists, P13) | a paged list of recent posts: `items` plus `next_cursor` (`10`'s cursor rule: opaque, `null` is the end) |
| (a post or reel `link`: the link decides, no `what`) | that one item |

Every item, listed or single, carries the **text layer** and **references**:
caption, author, when it was posted, Instagram's own alt text
(`accessibility_caption`, e.g. "a photo of a plate"), kind (image / video /
carousel), and media URLs. It does **not** carry what the media shows.

> **2026-09-24: no item `id`.** A story item is `{kind, taken_at, url}` and
> the text layer. The frame id was never used (`media_fetch` takes the URL),
> and it leaked: when a reel item has no `pk`, the bridge's `storyFrame`
> falls back to `item.ID`, shaped `<pk>_<owner igid>`, so the story owner's
> igid reached the model (R-18). The client no longer reads the field; the
> shared fixture `story_found.json` carries a frame of that shape.

**Seeing an image or video is `media_fetch(url=…)`**, called on the one item
that matters, exactly as `10-discovery-retrieval-and-paging.md` divides it:
the social tool finds *that* something exists and where; `media_fetch` finds
out *what it is*. Three reasons it stays that way:

- **Cost.** A story of ten frames read with vision is ten vision calls to
  answer one question. The text layer is one cheap read; she looks at the one
  frame the question is about.
- **The text layer often answers alone.** Alt text and caption say "she's at
  the beach" without any vision call.
- **One place understands media.** `media_fetch` already handles Instagram's
  CDN (its domain allowlist and custody) and the two layers the operator set
  for it: prepared answers, and a fresh question that bypasses the cache.
  A second copy inside `instagram_read` would drift from it.

> **Superseded for posts, reels and stories, 2026-09-24 (operator).** A post,
> reel or story read now carries what each item SHOWS, read through the same
> `media_fetch(url=...)` path, beside the caption. The reason is measured: each
> extra hop to `media_fetch` is a place she stopped early, and it happened live
> twice. The third bullet still holds, and is why the readings go through
> `media_fetch` itself rather than a copy; the first is answered by a cap and
> the caches. A profile and a search still read no media. See §"Typed views,
> one renderer, and readings" below.

### Verbs join only when they work end to end

"No verb admitted because one backend might grow it" (`09`):

| Read | Lands in | Bridge route |
|---|---|---|
| `profile`, `story` by `person` | the tool's first PR | `GET /accounts/{igid}`, `GET /accounts/{igid}/story` |
| `profile`, `story` by `link` or `handle` | P10 | `GET /accounts?username=` |
| a post / reel by link | P12 | `GET /posts/{shortcode}` |
| `posts` (a person's list) | not before P13 finds a mechanism (found 2026-10-16: design/cognition-loop/22-social-stream.md §1a) | none today |

A verb, an input kind, and the words advertising them all land together. The
first PR's tool accepts only `person` and only `profile` / `story`; `link` and
`handle` arrive with P10; `posts` is absent from the vocabulary, the
docstring and the catalogue line until it works. A catalogue line that names
a read she cannot do is the line she will call from (see the catalogue
contract below).

## Typed views, one renderer, and readings (operator, 2026-09-24)

**The goal.** A post's caption is a helper. It should reach Nora together
with what the photos and videos in the post actually show, so she can cross
the two and answer from both. Until now `instagram_read` returned the caption
and a bare link per item, and seeing any item was a second call,
`media_fetch(url=...)`, per item. Each extra hop is a place she stops early:
measured, it happened live twice. This section is the design; the build is
the same PR.

### Typed result views

`nora/infra/social/views.py` holds frozen dataclasses, built ONLY by
`InstagramSocialClient` from the bridge's JSON, field by field, with the value
checks the client already had (a handle is shape checked, a url must be on a
measured Instagram CDN host, a count is a non-negative `int` and never a
`bool`, free text is a `str` or nothing). Nothing from the bridge is ever
copied through raw: a view is the only thing that crosses from "what the
bridge said" to "what the model reads".

| view | fields |
|---|---|
| `ItemView` | `kind` (`image`, `video` or `media`), `url` (the item's CDN url), `duration_seconds?`, and for a story frame its own `taken_at?`, `caption?` and `alt_text?`; `reading?` |
| `Reading` | `state` (`seen`, `withheld`, `failed`, `timed_out`, `not_started`, and the three NOT-READ-HERE states `too_long`, `over_video_cap`, `unknown_length`), `text?` and `coverage?` (only when `seen`; `coverage` is a video reading's #117 sentence, split off so the layout never cuts it) |
| `PostView` | `author?`, `caption?`, `taken_at?`, `items`, `more` (the bridge sent more than the 20 Instagram allows in a carousel) |
| `StoryView` | `author?`, `expires_at?` (the reel's own expiry, which bounds every frame), `items` |
| `ProfileView` | `username`, `full_name?`, `following?`, `has_story?`, `social?` (the second call's half: follow back, private, the four counts, the bio; absent when that call failed) |
| `AccountView` / `SearchView` | `username`, `is_verified`, `full_name?` / `accounts`, `truncated` |
| `Result` | `status` (`ok`, `nothing_found`, `unavailable`, `refused`), `note`, `view` |

Three places where the brief's sketch and the build differ, each on purpose:

- **`ItemView` keeps `url`.** It is how a reading is taken, and it stays in
  the result's `items` so she can still ask an item a QUESTION
  (`media_fetch(url=..., question=...)`, layer 2), which a reading does not
  answer. The url is the only thing on an item that is not text Instagram or
  a model wrote, and it is host checked. The CDN urls were read back at
  deploy free of the author's igid (§Results, n=2).
- **`PostView` has no `link`.** The only link the client could build is
  `instagram.com/p/<shortcode>/`, and the shortcode is the one value this
  client already declines to echo (§Results, P12). She has the link she
  pasted.
- **`StoryView` carries `expires_at`, not `taken_at`.** A story has no single
  posting time; each frame has its own, on its `ItemView`.

`Result` is total: `ok` requires a view and a non-`ok` status requires a note
and no view (checked in `__post_init__`), and the renderer turns a non-`ok`
one into its status and note alone. The `ok` answer carries no note of its
own: what the note says depends on what the view holds (were the items
looked at, was any seen), so the renderer frames it. The non-`ok` answers
the client and the router already give (`unavailable`, `nothing_found`,
`ambiguous`, `refused`, `not_set_up`, the post route's own two) stay the
shared constants they are, one copy per claim, in the same two-key shape; the
router is not changed by this beyond forwarding one argument.

**No ids, ever (R-18).** No view has a field that could hold one: no `id`,
`igid`, `fbid`, `pk` or shortcode. A test enumerates every view's fields, and
another renders every fixture and asserts no fixture id reaches the output.

### ONE renderer

`nora/infra/social/render.py::render(result, *, max_chars)` is the only code
that turns a view into what the model reads. Wrapping and clamping untrusted
text (R-13), the absence of ids, and the caption-beside-readings layout all
live there and nowhere else; the client's four hand-built dict literals
(profile, story, post, search) are gone. Profile and search render byte for
byte what they did before, pinned against a snapshot the pre-migration client
wrote (`tests/unit/test_instagram_profile_search_snapshot.py`).

A post renders as one wrapped `social_post` block:

```
Post by @example_author · 3d ago · 3 items

1. image
shows: <what the vision model saw>

2. video, 12s
shows: <what the vision model saw>

3. image
not seen: it could not be looked at just now

caption: <the poster's own words>
```

Past the cap, each unread item keeps its numbered line and says, on its
own line under it, `not read here (past the first 8); media_fetch reads
it` (#486 re-review: one line per item, not one shared line after them;
short, so a small budget keeps more items).

- **The item urls leave the text** and stay in `items`, in the same order as
  the numbers. A CDN url is 150 to 400 characters of signature; with readings
  in the text, twenty of them would spend most of the clamp on noise that is
  re-sent on every later model call. `items` is the machine handoff, and the
  note says so.
- **The caption is LAST**, as before: it is stranger prose, and nothing Nora
  states follows it. A reading is model text about stranger bytes, so it gets
  the caption's treatment: one line, square brackets turned round, labelled,
  inside the wrap.
- **Everything fits, and nothing is dropped** (amended on the #486 review).
  The first build clamped the finished text at a block boundary, which cut
  whole blocks off the END: the caption of a large carousel, and the last
  frames of a story whose captions and alt text were long. Now the layout
  keeps every line that is Nora's whole (the header, each item's number line,
  a not-seen line, the «only the first 8» line, every label, a video's
  coverage sentence) and shares what is left of the budget among the prose
  runs (readings, captions, Instagram's descriptions) by water-filling: each
  gets an equal share, capped at its own length, and what a short run does
  not use goes to the long ones. So a long reading gives way before a short
  caption is touched, the caption always survives (shortened if it must be),
  and no frame loses its line. Ceilings: a reading 1,500 characters, a
  story frame's caption or Instagram's description 800, a POST caption
  2,200 (Instagram's own limit). The post caption has FIRST claim on the
  budget, leaving every other run a floor: since #484 the transcript
  carries only a shared item's first sentence, on the promise that this
  read returns the rest, so the rest comes back whole and the readings share
  what it leaves. One reel's reading keeps 1,500; eight long readings beside
  a short caption share about 460 each in the default 4,000. The full reading is not
  lost: `media_fetch(url=...)` on the same item is then a cache hit, except
  for a story frame (below). Shares are EXACT: every run is first given
  its floor (24 characters) PLUS the two of « …», which is what a floored
  run renders as (#486 re-review: reserving 24 for a 26-character run
  overran the budget, and the clamp then cut the caption off a 20-item post
  at 2,800). If even the floors do not fit, the backstop shortens the post
  caption, never drops it; past even that (Nora's own lines alone over
  budget) the blocks BEFORE the caption are cut and its block is kept.
- **A video's coverage sentence is never cut.** #117's «...I have not seen
  the rest» is split off the reading and appended whole after the shortened
  description; a partial reel read that loses it reads as whole. Only the
  KNOWN forms are split off (`VideoCut.note()`'s three, `describe_coverage`'s
  two, `media_fetch`'s fallback), matched by their fixed openings and at
  most 240 characters; any other last line in parentheses («(No speech is
  audible.)») is ordinary description and is shortened with it. A test
  builds every form from the real code, so a reworded sentence fails there.
- **A story** renders the same way, one block per frame: the kind and age,
  then its `shows` line, then its own caption and Instagram's description.

### Readings

For a post, reel or story read, each item's `reading` is filled by
**`media_fetch` itself**: the tool hands its own `media_fetch` function to the
read (through the router, which forwards it only for `instagram/post` and
`instagram/story`), and `nora/infra/social/readings.py` calls it as
`media_fetch(url=item.url)` for each item. That is the same path a manual
`media_fetch(url=...)` takes, byte for byte: the same egress screen on the
url, the same download ceilings, the same image vision call and the same
chunked video read, and the same content caches, the vision cache by sha256
and the `video_readings` cache (#441). A second copy of any of it inside
`instagram_read` would drift from it, which is the argument 18 already made
for keeping one place that understands media.

**Security rule: the caption never reaches a vision prompt.** Vision prompts
are authored (`nora/infra/vision/comprehend.py`); text a poster or a room
wrote must not steer them, because the model's output returns to the room
(`media_fetch`'s layer 2 note says why its one exception is acceptable, and
that exception is a question NORA composed, not a poster's words). So
`readings.py` passes the url and nothing else: no `question`, ever. With no
question `media_fetch` sends an empty prompt and the vision client uses its
authored `DESCRIBE_PROMPTS`. Nora does the crossing, with both in front of
her. Pinned on the prompts a recording vision client receives, end to end
through the tool, and by a mutation that hands the caption to the read as a
question.

**Budgets.**

| budget | value | why |
|---|---|---|
| items read per open | the first **8**, in the post's order | Instagram allows 20 in a carousel. Eight at a concurrency of four is two waves at most. The rest are listed, numbered, with their kind, and one line says only the first 8 were looked at; each one's link is in `items` for `media_fetch(url=...)`. |
| videos read per open | at most **3**, each no longer than **one reading window** (the CONFIGURED `[media.describe.video] chunk_seconds`, 90 s by default) | Added on the #486 review. A video window is the expensive read (2.8k to 6.2k completion tokens measured, DEC-197). A longer video would be several windows in sequence inside one open, so it is not read here and says so (`not read here: longer than one reading window`); a fourth video says `not read here: only 3 videos...`. A POST video with no stated length is not read here either (`not read here: a video of unknown length`): nothing bounds it. A STORY frame carries no length at all and Instagram caps a story video at 60 s, so a story frame is read. Every one of these points at `media_fetch(url=...)` on the link. |
| concurrency | **4, per TURN** | One semaphore per turn (built with the turn's tools), shared by every `instagram_read` in it, so two calls the model batches cannot run eight looks at once (#486 review). | `media_fetch`'s per-turn budget is 4 and the model already emits them in one batch, which the executor runs at once: four media reads in flight is the operating point already running (design/tool-concurrency/01 §4 measured three at once finishing in 8 to 9 s wall), and 4 is the ceiling that bundle proposes for all tools. |
| per item | **150 s** | the single-window vision ceiling (120 s, sized on the measured tail of a 90 s window) plus the url download ceiling (25 s) plus 5 s for ffmpeg. A live reel read took 82 s uncached. |
| whole call | **180 s** | two waves of the one measured uncached reel (2 x 82 s = 164 s). Inside a turn it is also held under `seconds_left() x 0.6`, the share the vision client already leaves the turn, so the call can never outlast the turn's `wait_for` (at the start of a 360 s turn that share is 216 s, so 180 binds). |

- **Partial failure.** An item whose read fails, is withheld by the
  identifier screen, or runs past its own timeout or the whole-call deadline
  says so on its own line (`not seen: ...`, `not shown: ...`), and every other
  item still returns. A timed-out read is cancelled and caches nothing; the
  same link through `media_fetch` gets the full ceiling. One failure never
  fails the call.
- **Cancelled means stopped** (#486 review). When the read itself is
  cancelled (the turn's `wait_for` fired), every look still running is
  cancelled AND awaited before the cancellation goes on, so nothing
  downloads, reads or writes a cache after the tool has returned. The two
  ffmpeg runners (`preprocess._run`, `_run_video`) kill and reap ffmpeg and
  remove the scratch copy of the clip on every path out, cancellation
  included; before, only a timeout did, and a cancelled read left the
  process and up to 64 MiB on disk. In `_run_video` ONE outer `try`/`finally`
  owns the scratch file (#486 re-review), so it also goes when the cancel
  lands during the spawn, or when a second cancel lands during the shielded
  reap; the shield keeps the killed child's `wait` running, so it is reaped.
- **A look that never started says so.** One still waiting for a slot at
  the whole-call deadline is `not_started` («there was no time left to look
  at it»), not `timed_out` («took too long»).
- **Every reading fails.** The status stays `ok`: the post WAS read, and its
  author, age, item count and caption are true. What could be wrong is a
  claim about what the media shows, so that claim is what is withheld, item
  by item and in the note, which says she could not see any of it and must
  not describe it from the caption. `unavailable` would discard the true half
  and invite a retry that is not what failed.
- **Nothing was read HERE, by design** (#486 re-review, blocking). A reel
  past one window, a post video of unknown length, videos past the cap and
  items past the first 8 are not failures: each can be read on request. When
  none was seen and any of these is present, the note is its own
  (`POST_NOT_READ_FRAMING`, `STORY_NOT_READ_FRAMING`): what the items show is
  not in front of her yet, `media_fetch(url=...)` on an item's link reads
  it, and the caption alone does not describe the post. It never tells her
  to say she could not see it. The first fix round sent a single 140 s reel
  to the «say you couldn't see the post» note, a reel the first build had
  read.
- **Mixed: every look that ran failed, and some items were not read here**
  (#486 final review). Sending her to `media_fetch` on every item would
  invite a retry of the ones that just failed, so the note is its own
  (`POST_MIXED_FRAMING`, `STORY_MIXED_FRAMING`): the items looked at could
  not be seen, and only those marked not read here can be opened with
  `media_fetch`. And the read note no longer says an item with no shows
  line «was not seen»: it says why, and where it can be read.
- **Media reading not configured** (no vision client or custody) is the same
  shape: each item says it could not be looked at, because that is what
  `media_fetch` answers.

**Stories get the same treatment**, frame by frame, under the same cap and
budgets. **A profile and a search read no media**: the router forwards
`media_fetch` for the post and story rows only, whatever the caller passes.

**A story frame's reading is never persisted (current rule, 2026-09-24,
#486 review; operator decision pending, and this is the privacy-preserving
default).** A story expires in about 24 hours; a durable reading of it would
outlive it. So a story frame is read through `media_fetch` with its cache
WRITES off: nothing goes to the durable `media_descriptions` table, to the
in-memory vision cache, or to `video_readings`. A cache HIT is still served
(the same bytes were read before, as a post or in a room). The cost is that
opening the same story twice reads it twice. Posts and reels cache as
above. The switch is `media_fetch`'s own per-read context
(`tools._MEDIA_READ`), set only by `instagram_read`'s story looks, because a
parameter on `media_fetch` would be in the model's schema. Pinned by
`test_a_story_frame_is_read_but_never_written_to_a_cache`. **Holds until the
operator decides otherwise.**

The story result tells her to `media_fetch(url=...)` a frame, and that call
is the MODEL's own (`caller=model`), so the switch above does not cover it.
Each turn therefore remembers the frame links its story reads returned, and
`media_fetch` on one of them writes no cache either, whoever asks (#486
re-review). Pinned by
`test_a_story_frame_the_model_opens_itself_is_not_persisted_either`.

**Cost.** One vision pass per UNCACHED item, at most 8 per open. An image is
one call to the image describer (`glm-5.3-flash`, 2 to 4 s measured); a reel
of 90 s or less is one window of the video describer, measured at 2,808 to
6,159 completion tokens a window (config, DEC-197, n=3). So a typical
three-image carousel is three cheap calls, and the worst case per open is
three uncached single-window videos and five images: roughly 8k to 19k video
completion tokens plus five image calls (the first build allowed eight video
windows, 22k to 50k, which the #486 review capped). A turn's looks share four
slots, so batching two opens does not double the rate. A second open of the
same post costs no vision call: the downloads still run (the cache key is the
bytes' sha256), and every reading is a hit; a story is read again (above).
The readings fill at most the same text budget the result already had
(`[social] content_max_chars`, 4,000 by default), re-sent on every later
model call of the turn. These reads do not spend `media_fetch`'s per-turn budget of 4 (they
are not model tool calls); the caps of 8 items and 3 videos are their
bound. Each one is counted in `nora_media_fetch_total`, because it IS a media
read, with `caller="instagram_read"`, so the automatic readings are never
read as Nora's own looks (`caller="model"`).

**Background turns.** Readings run only where the read itself is allowed. A
story, post or reel is already refused in a fire turn or a Dreamer pass
before any name, handle or link is resolved (§"A story is opened only when a
person asks"), so no reading can start there; that is asserted on the vision
client's calls and the downloader's, beside a positive control.

**The note tells Nora what she is reading.** For a post: the caption is what
the poster wrote, each `shows` line is what a vision model saw in that item,
and the two can disagree; an item with no `shows` line was not seen, and its
link is in `items`. When no item was read here by design, the note sends
her to `media_fetch`; when every look failed, it says she could not see it.
It frames what she is reading; it does not list cases. No dashes (ADR 0015).

**The description.** The L1 stays verbatim: it still holds, since every item
is still returned and, up to the cap, shown. The body now says a post, reel or
story returns what each item shows beside the caption.

**What would reopen this.** A live turn in which the readings' latency is
what the room notices, or a carousel where the ninth item is the one asked
about, reopens the cap; a provider 429 at four concurrent reads reopens the
concurrency.

## `instagram_search`

```
instagram_search(query: str, limit: int = 5)
```

Accounts only: Instagram's search finds people, not post content. Backed by
`GET /accounts?query=` (P11). It returns accounts with their handles, so a
follow-up read can name one; it never reads anyone's content itself.

> **As built in P11 (2026-09-24): `instagram_search(query)`.** No `limit`:
> the cap is fixed at **5** and stated in the description ("up to 5
> matches"), the same count design 18 had as the default. P9 found the exact
> username at rank 0 in 20 of 21 probes, so a refined query, not a longer
> list, finds the rest, and every account is re-sent on every later model
> call of the turn.
>
> - **Result.** `ok` carries `accounts`, one `{username, is_verified}` per
>   hit in the bridge's order, and `text`, one numbered line per account
>   with its display name, wrapped as ONE `social_post` block and clamped
>   (R-13). No igid or fbid anywhere (R-18): the client never reads them.
>   The display name is kept out of `accounts` for the reason a caption is
>   kept out of a story's `items` (prose outside the wrap), and one wrap
>   covers the list because a `social_post` block carries its own ~60-token
>   description, so one per name would cost more than the names.
>   `is_verified` is `true` only when the bridge says `true`. `truncated`
>   says the bridge returned more than 5.
> - **States.** Zero items is `nothing_found`; 409, 502 and 503 (and any
>   other non-200) are `unavailable`; items with no usable handle are
>   `unavailable` (our failure, not a fact about Instagram). Every result
>   carries a `note`; the `ok` note says the first result is not always the
>   one they mean, which is P9's rank-1 case.
> - **Query checks, before any call.** Trimmed, non-empty, at most 64 BYTES
>   (the bridge's Go `len` counts UTF-8 bytes, so 21 Chinese characters is
>   the ceiling); a bad query is `unavailable` and nothing is sent. The
>   egress screen `social_fetch` applies to its target also runs here: a
>   phone number, an id or a raw coordinate is `refused` and never sent.
> - **Not held to a turn a person asked for.** No rule restricts it the
>   way the story rule does. It is in `UNIT_SAFE_TOOLS` because its answer
>   carries no room state (no name resolved against a roster), the same
>   shape as `web_search`. `cacheable=True`, like `web_search`: it carries
>   no roster or Tier 0 decision.
> - **OPEN operator decision: may Nora look people up unprompted in fire
>   and Dreamer turns?** Being unit-safe makes the tool grantable to a
>   Dreamer adaptor; it does not grant it. **No Dreamer adaptor declares
>   `instagram_search` today** (proactive and culture list their tools, and
>   neither names it). A FIRE turn (reminder, proactive, participation) is
>   built with the whole tool surface, so it carries the tool now, with no
>   rule against using it. Whether Nora may search Instagram for people in a
>   turn nobody asked for is the operator's call. It is recorded here so the
>   current state reads as undecided rather than as a default somebody
>   chose; a story-style turn rule in the tool is the one-line answer if the
>   decision is no. The first draft of this PR argued "a
>   search is not seen by the account it finds" as the permission; that is
>   a fact about Instagram, not an answer to the privacy question.
> - **Router.** `(instagram, search)` joined `EXCEPTION_ROUTES` with a
>   `named` input: the query reaches the bridge as typed and the person index
>   is never consulted, so wiring the search row needs no index.

## Results

The same envelope as every social read: R-27's `ok`, `nothing_found`,
`unavailable`, plus `ambiguous` and `refused` where resolution and the Tier 0
check (story only) produce them. The body is the curated DTO (`16`
§Resources), never Instagram's raw response, wrapped as untrusted and clamped.

> **2026-09-24, as built.** `refused` says «I only open stories of people the
> operator has listed in one of my rooms», which is true of everyone it
> covers — including someone who is IN the chat but not listed, about whom
> «outside the rooms I'm in» was false. A backend that is not composed (its
> flag is off) answers one stable `unavailable` sentence, «Instagram isn't
> set up», and logs once at boot rather than on every call.
>
> **Superseded wording, later 2026-09-24 (#446 review).** The sentence is now
> «I only open the stories of people in this conversation whom the operator
> has listed». The old one stopped being true once a story by handle or link
> was scoped to the asking room: a friend listed in ANOTHER room is refused
> too, and «listed in one of my rooms» would be false about them.

> **2026-09-24, P10 and P12 as built.** A handle nobody holds is
> `nothing_found`, «Instagram has no account with that username; don't guess
> a similar one» (`results.NO_SUCH_HANDLE`), distinct from `NO_ACCOUNT`,
> which is about the operator's file. A story named by a handle or link whose
> account maps to nobody listed (a stranger included) is `refused` with the
> same sentence as a person-named one. A post or reel link returns
> `source_kind: instagram_post`, `items` (every item in the bridge's order,
> each `{kind, url}` plus `duration_seconds` for a video, at most 20, which
> is Instagram's carousel ceiling, `truncated` if more), `author` (a
> shape-checked handle), `taken_at`, and `text`: one wrapped, clamped block
> with the author and age first, one `[kind url]` line per item, and the
> caption LAST with its square brackets turned round, so a caption can
> neither forge an item nor push one past the clamp. The `note` tells Nora
> to `media_fetch` each item she needs to see. The bridge's 404 is
> `nothing_found`, its 501 (a shortcode longer than 11 characters) is
> `unavailable` saying a retry will not help, and every other failure is
> `unavailable`. No igid, fbid or person id is in any of it, notes and
> errors included; the shortcode the bridge echoes is not read back.
>
> **UNMEASURED (2026-09-24): whether the CDN urls carry account ids.** Every
> item url is handed to the model (it is `media_fetch`'s input), and
> Instagram CDN urls carry opaque query parameters such as `efg` (a
> base64-encoded JSON blob) and `ig_cache_key`. Whether either encodes the
> owner's igid, or any account id, has not been measured. The client checks
> the HOST only. It is to be read back live at deploy; until then R-18's «no
> id reaches the model» holds for every field this client builds and is
> unverified for the url's query.
>
> **Read back at deploy, 2026-09-24:** on one carousel and one reel the
> author's igid was absent from all 3 urls, raw and with `efg` /
> `ig_cache_key` decoded (17 §P12). n is 2; the host check remains the only
> enforced rule.
>
> **UNMEASURED: a STORY frame's url.** The read-back above covered post and
> reel urls only. A story frame's url reaches the model the same way (in
> `items`) and, since the readings, is also downloaded by `instagram_read`
> itself. Whether its query encodes the owner's igid has not been read back.
> To be read back live at the next deploy.

## A story is opened only when a person asks (operator safety rule, 2026-09-24)

Nora does not read people's stories unprompted. That is a privacy choice,
and it is the whole reason for this rule. So
`instagram_read(what="story")` is answered `refused`, before any name is
resolved or any request is sent, in every turn that does not answer a person
— a fire turn (reminder, proactive, participation) or a Dreamer pass. It runs
only in a turn a person addressed (a tag or a reply). The tool knows which
it is from provenance its builder already carries (`fire_turn`,
`dream_adaptor`), not from a guess. A profile read stays allowed wherever
the tool is. Pinned by
`test_a_story_is_never_opened_in_a_turn_nobody_asked_for`. **This holds until
the operator decides otherwise.**

> **Extended 2026-09-24 (P10, P12).** The same rule covers a story named by
> a handle or a link, and a post or reel link: the operator's rule that Nora
> reads shared content only when asked (13 §2026-09-24). In a fire turn or a
> Dreamer pass all of them are `refused` before any name, handle or link is
> resolved, so no lookup and no read reaches the bridge. The rule lives in
> the TOOL, the one layer holding the turn's provenance; `SocialRouter`'s
> `read` docstring says so, because a future caller that reaches the router
> directly would bypass it. A profile, by any
> input, stays allowed everywhere. Pinned by
> `test_a_background_turn_is_refused_a_story_post_or_reel_before_any_call`
> (`tests/unit/test_instagram_read_links.py`).

> **Reason corrected 2026-09-24 (P11 PR).** This section first justified the
> rule by saying a story view is visible to its author. That was never
> measured: whether the author sees the view is unmeasured (design 04, 05
> §Reading a story). The `seen` watermark that did not move across two
> reads on 2026-09-21 is withdrawn evidence: it tracks the account
> holder's own viewing, so it cannot see this property. The rule and its
> code are unchanged; only the reason is: privacy, not visibility. The
> `refused` note no longer tells the model the author can see the view.

`side_effect_state` stays `none`, and the `NativeTool` comment says what it
does not settle: whether a story view reaches the author's viewer list is
open (see the correction above). ADR 0008 §1 binds `read_only=True` to
`none` (a tested invariant), and `unknown` would make `instagram_read` an
act verb — removing it from the Relevance Gate's digest, from R-97's lookup
set and from the Dreamer's ceiling for the profile read as well. The story
half is held by the privacy rule above instead. Changing that trade is an
ADR 0008 decision, not a flag on this tool.

## Where each concern lives

| Concern | Owner |
|---|---|
| parse the link, pick the read | the link parser, `nora/domain/instagram_links.py` (P10; see the note below) |
| resolve a NAME → person | the tool, with the turn's `room` (the lookup `about` uses) |
| resolve person → igid, igid → person | `SocialSubjectIndex` (`17` P3), called by the router |
| authorize (Tier 0, story only) | `SocialRouter`, the single choke point for EVERY social tool, old and new |
| call the bridge | `InstagramSocialClient` (`17` P4) |
| the model-facing contracts | the two tools in `nora/agent/tools.py` |

> **2026-09-24, P10: the link parser landed in `nora/domain`, not
> `nora/infra/social`.** Two readers need it: the Instagram adapter's
> transcript render (`nora/platform/instagram/parser.py`, #435's rules) and
> `instagram_read`. `nora.domain` is the layer both may import and it is
> standard-library only, which a URL parser is. #435's rules moved into it
> unchanged (the parser's tests did not change); the tool parses a link into
> `(kind, key)` there, so the router is handed a username or a shortcode and
> never a URL. The username lookup is a new port,
> `nora.domain.ports.InstagramAccountLookup`, implemented by
> `InstagramSocialClient` and handed to the router as `accounts`.

The tools stay thin: validate arguments, resolve a spoken name to a person
(the one resolution that needs the conversation, and the tool is the only
layer that has it, exactly as `about` does today), call the router, return
its answer. Neither resolves an account nor authorizes: both stay in the
router, which is composed once and holds no per-conversation state. A tool with its own authorization copy is
the drift `10` §"Who owns what" forbids.

### What the router has to learn

Today `SocialRouter` is keyed on `(platform, what)` and resolves only
`instagram/story` through the person index; every other row's target passes
through untouched (`TIER_NAMED`). That cannot serve this contract, so the
router changes, and the change is part of the tool's first PR (for `person`)
and P10 (for `link` / `handle`), not a detail left to either:

- **Resolution is keyed on `(platform, what, input kind)`.**
  - `person` → the tool has already resolved the name to a person id →
    `igids_for` (exactly one is read). Any read.
  - `handle` → the bridge's username lookup → igid.
  - `link` → the parser → (read, username or shortcode) → as for `handle`, or
    straight to the post route.
- **Authorization stays keyed on the read, not the input.** Only `story` has
  a reach check. For `person` it is `is_tier_zero(person)`. For `link` and
  `handle` it is `igid → person_for_igid → is_tier_zero`, **on the igid the
  input named, never re-derived through `igids_for`** (`15` §Links). An igid
  that maps to nobody is not in any room: `refused`, and no story request is
  sent. `profile` and `post` need resolution only.

  > **Current rule, 2026-09-24 (operator's conservative default, #446
  > review): a story by handle or link is scoped to the ASKING room.** Its
  > person must be Tier 0 AND on the roster of the conversation the question
  > was asked in, the same scope a spoken name resolves in. Anything else is
  > `refused`, with no story request, and with the one `refused` sentence every
  > story refusal uses («I only open the stories of people in this conversation
  > whom the operator has listed»), so a stranger, a friend listed nowhere and
  > a friend listed only in another room get byte-identical answers. An
  > unreadable roster is `unavailable` and nothing is looked up. A profile, a
  > post and a reel are unaffected.
  >
  > **OPEN operator decision: widen it to «Tier 0 in any group room»?** That is
  > what this section first said, and it has a cost the first draft did not
  > see: it makes a pasted story link a **cross-room membership oracle**.
  > Anyone in any room could paste `instagram.com/stories/<username>/` for a
  > candidate and learn, from «read» versus «refused», whether that account
  > belongs to someone listed in one of Nora's OTHER rooms, which the
  > person-name path deliberately refuses to disclose (design/12 §3: «not in
  > this room» and «does not exist» answer identically). Scoping to the asking
  > room's roster closes it, because the answer then depends only on who is in
  > the room the asker is already in. Widening is a one-line change in the
  > router (`room_people`); whether the convenience is worth the disclosure is
  > the operator's call.

  > **Tier 0 note, 2026-09-24.** `is_tier_zero` counts any `[[conversations]]`
  > entry that lists the person, and does not ask whether that entry is a
  > group: a direct-chat entry would make its person Tier 0. The policy parser
  > cannot produce one today (Nora does not accept DMs, and `[dm] persons` is
  > a separate field that never counts), so this is recorded rather than
  > fixed.
- **The port gains `cursor` and the input kind.** `SocialProvider.read` and
  `SocialRouter.read` take `(platform, what, target, limit)` today; both grow
  a cursor and a typed target, and the contract suite
  (`tests/contract/test_social_provider_contract.py`) grows with them.

  > **As built in P5 (2026-09-24).** `SocialRouter.read` grew `target_kind`
  > (`named` | `person`, default `named`, the port's own meaning), with
  > resolution keyed on `(platform, what, target_kind)` (`SUBJECT_INPUTS`)
  > and reach on the read alone (`TIER_ZERO_READS`, story only). The
  > BACKENDS' port did not grow: the kind is consumed by resolution, and a
  > backend only ever sees a resolved target (an igid, or a typed string), so
  > the argument would be one every backend ignores. `cursor` joins with the
  > read that pages (P13). The Instagram rows admit `person` only; a `named`
  > target there is `unavailable` until P10's username lookup exists.

  > **As built in P10 and P12 (2026-09-24).** `target_kind` gained
  > `handle`: a typed username, or the username of a profile or story link.
  > `SUBJECT_INPUTS` admits `(instagram, profile|story, handle)` and
  > `(instagram, post, named)` (a post's shortcode, never resolved);
  > `(instagram, post)` joined `EXCEPTION_ROUTES`. A handle resolves through
  > the bridge's exact lookup (`InstagramAccountLookup.igid_for_username`):
  > `none` is `nothing_found`, `unavailable` is `unavailable`. For a story
  > the router then asks `person_for_igid` of THAT igid and `is_tier_zero` of
  > that person; `igids_for` is never called on this path, so a link to one
  > of a two-account friend's accounts reads that account while the same
  > friend by name stays `ambiguous` (pinned in
  > `tests/unit/test_social_router.py` and end to end in
  > `tests/unit/test_instagram_read_links.py`). A missing lookup is not a
  > boot error: it grants nothing (every handle read is `unavailable`), and
  > the `person` path keeps working. The client does not re-pick: it keeps
  > only rows whose username is the one asked for, and anything but exactly
  > one is `unavailable` (P9 wrote «ambiguous» for two matches; that word's
  > sentence is about the operator's file, so it was not reused).

## Read-only, at three layers, still one table

R-86 pins read-only at three layers: the tools' closed vocabulary, the backend
allowlist, and `read_only=True`. The bridge also serves writes (`/send`,
`/react`, …), so on this path read-only is enforced by the **client**:
`InstagramSocialClient` issues only `GET` to `/accounts…` and `/posts…`, and a
test pins that its request set contains nothing else. `test_r86_…` derives all
three layers from one vocabulary table covering every social tool.

## The tool-catalogue contract (porgy-58, 2026-09-23; amended 2026-09-24)

> **Amended 2026-09-24 (catalogue owner; landed with P5).** Two things this
> section assumed changed after it was written. (1) The catalogue line is no
> longer the docstring's first SENTENCE; it is the first PARAGRAPH, and that
> paragraph is ADR 0007's L1 — what the tool does AND when to call it —
> gated by `tests/test_tool_surface_policy.py::
> test_the_opening_paragraph_is_an_l1`: a `WHEN_TRIGGERS` phrase, and ≤40
> cl100k tokens rendered as `- name — <paragraph>`; the block ceiling is
> 2,000 and the mean 33 (`tests/unit/test_tool_catalogue.py`). (2) Deferral
> is OFF (`../tool-catalogue/05-measurement.md` §3a), so the tools ship
> resident with their full schemas and the "she calls from the catalogue
> line" argument below applies only if the switch is ever turned on. The
> line must still name the input and separate the siblings, because it is
> also the opening of the resident description. The bullets below are
> updated in place; the drafts are replaced by what shipped.

Each item is an existing test that goes red, applied to both tools:

- **Catalogue line = the docstring's first paragraph**, what + when, ≤40
  tokens (cl100k) as a catalogue line, mean ≤33 across tools, no identical
  lines. Each PR's line is re-measured by the gate itself.
- **The line names the input the tool takes** (a person, a link, a handle;
  free text), not only the network, names Instagram, and separates each tool
  from its confusable siblings (`social_fetch`, `web_fetch`, `media_fetch`,
  and each other). It is what she would call from if deferral were on
  (measured, porgy-58), and it opens the resident description either way.

  > **Amended 2026-09-24, for `instagram_read`: the L1 names what the tool
  > RETURNS and when to call it (ADR 0007's what + when); the inputs
  > (person, handle, link) are named in the description BODY.** The reason is
  > a measurement. A replay of the live prompt (the recorded prompt of trace
  > ce094b70, a shared reel asked about, n=12 per variant) counted
  > `instagram_read` called first: **V3** (the handle gone from the line,
  > the old L1 below) **0/12**; **V4** (V3 plus the L1 and body sentence
  > that shipped) **7/12**; **V5** (V4 plus the purged belief, already done
  > in production) **10/12**. An L1 that listed the inputs told her how to
  > address the tool and not that it returns a reel's video, which is the
  > one thing the question needed. The body now carries «Give exactly one of
  > `person`, `handle` or `link`», pinned by
  > `test_the_description_body_names_person_handle_and_link`; the L1 is
  > pinned by `test_the_catalogue_line_names_every_item_and_a_share`
  > (`tests/unit/test_instagram_read_tool.py`). `instagram_search` is
  > unchanged. This holds unless a replay or a live turn shows the new L1
  > losing the name or handle routes it did not measure.
  - `instagram_read`, as shipped in P5 (34 tokens as the L1 gate counts it): *"Read someone's
    Instagram profile or current story, by their name. Call this when
    somebody asks about a person's Instagram or what is on their story."*
    From P10 it names handles and pasted links; `posts` joins the line only
    with P13.
  - `instagram_read`, as shipped in P10 and P12 (38 tokens as the L1 gate
    counts it): *"Read an Instagram profile, story, post or reel, by name,
    handle or link. Call this when somebody asks about someone's Instagram
    or pastes an Instagram link."* "post or reel" is the one item a link
    names (P12), not a person's list; the description body says a post or
    reel is read from its link only. The draft that named the inputs per
    read ("…profile or story by name, handle or link, or a post or reel by
    its link…") measured 43 and did not fit.
  - `instagram_read`, from 2026-09-24 (39 tokens): *"Read an Instagram
    profile, story, post or reel by name, handle or link. Call this when
    somebody asks about someone's Instagram or a shared or linked post or
    reel."* A reel SHARED into a room, not pasted, was answered from its
    cover picture with this tool in the turn; see `13` §«the cover says it
    is one». Keeping "an Instagram link" beside "a shared post or reel"
    measured 43 and did not fit. The when-clause did not fix it: live, a
    cover handle beside the link was still read instead of the link, so a
    post or reel share whose link validated now renders the link and NO
    handle, and
    `media_fetch` answers an Instagram page link by naming this tool (`13`
    §«a linked share carries no picture»). A share renders
    `[cover image …]` only for a story or when its line has no link.
  - `instagram_read`, from 2026-09-24 after #454 (38 tokens): *"Show what an
    Instagram post or reel contains, every item, videos included, or read
    a profile or story. Call this when somebody shares, links or asks about
    one."* With it, the body sentence «A story or a post returns its media
    with a link each; to see what one shows, call `media_fetch(url=...)` on
    it.» became «A post or a reel returns every item in it, videos
    included, each with a link that `media_fetch(url=...)` describes; a
    story returns its media the same way.» Measured on the replay recorded
    in the amendment above (trace ce094b70, n=12): 0/12 → 7/12 →
    10/12 across V3, V4, V5. The inputs are no longer in the line; see that
    amendment.
  - `instagram_search`, as shipped in P11 (35 tokens as the L1 gate counts
    it): *"Search Instagram for accounts by name or handle, returning up to 5
    matches. Call this when somebody wants to find a person's Instagram
    account or handle."*
  - `social_fetch`, from P11 (27 tokens): *"Read X (Twitter) posts,
    profiles and searches. Call this when somebody links or asks about
    something on X."* No Instagram in it at all.
- **The opening** contains a `WHEN_TRIGGERS` phrase
  (`tests/test_tool_surface_policy.py`), with no permission-suppression wording.
- **A `NativeTool` entry with every field**, shaped like `social_fetch`'s:
  `read_only=True`, `output_contract="three_state"`, `source_tier`,
  `cacheable`, and the side-effect / idempotency / retry triple.
- **Unit-safe for the Dreamer** (`UNIT_SAFE_TOOLS` in `nora/agent/dreamer.py`).
  > **Not as built (P5, 2026-09-24): `instagram_read` is classified
  > ROOM-scoped**, beside `about` and `online_now`
  > (`tests/unit/test_dreamer_runtime.py`). This bullet was written before
  > name resolution moved into the tool; the tool now resolves the name
  > against the room's live roster, which a unit-keyed Dreamer tick does not
  > have, and `UNIT_SAFE_TOOLS`' own rule refuses room-scoped reads. In a
  > unit-keyed tick it could only ever answer `unavailable`. Moving it is a
  > one-line change and a separate decision.
  > **`instagram_search` IS unit-safe (P11, 2026-09-24)**: it takes free
  > text, resolves no name against a roster and runs no Tier 0 check, so
  > nothing in its answer is room state. No adaptor declares it; whether one
  > may is open (§`instagram_search`).
- **Not in `HOT_TOOLS`**: that set is measured, not hand-picked.
- No `ToolScope` change: they read Instagram from any room.

## What happens to `social_fetch`

It loses its Instagram rows **verb by verb, each in the PR that lands its
replacement** (`15`'s per-verb cutover), so no Instagram read ever has two
tools and none goes dark in between:

| `social_fetch` row | Today (measured) | Leaves in |
|---|---|---|
| `instagram/profile` | OpenCLI answers **429**, all day, 2026-09-22 | `instagram_read`'s first PR: nothing working is lost |
| `instagram/story` | never had a backend (the Python side is unwired) | first PR |
| `instagram/posts` | OpenCLI's `user` returns Instagram's **HTML shell**, even from its own browser | first PR: nothing working is lost; the capability waits for P13 |
| `instagram/search` | **works** through OpenCLI | **P11**, when `instagram_search` lands, not before |

Until P11 `social_fetch` keeps exactly one Instagram verb, search. Then its
`platform` enum becomes `{"x"}`, until `x_read` / `x_search` replace it and it
is deleted. That replacement is `../social-per-network/10-changes.md` §"The order",
PR 1.

> **Done in P11 (2026-09-24).** The `instagram/search` row left
> `social_fetch`'s vocabulary and OpenCLI's `READ_ONLY_ALLOWLIST` in the PR
> that landed `instagram_search`, so `social_fetch` is X only and
> `test_r86_…` asserts no `instagram` key remains in the allowlist.
> `platform` stays a `str` in the schema rather than a one-value enum, so a
> model that still asks `social_fetch` for Instagram reaches the tool and is
> answered `unavailable` with a note naming `instagram_read` and
> `instagram_search`, and nothing is sent, instead of a schema error.

The story row's docstring has a live defect that goes with it: it tells Nora
the target is a person id "the same one `lookup_person` and `about` take".
Neither tool takes or returns an id. `instagram_read`'s `person` parameter
replaces it.

**Its first sentence changes in that same PR too.** It currently reads "Read a
post, profile, or search results from X or Instagram", and it is `social_fetch`'s
catalogue line. Left as is, it overlaps both new lines: exactly the
confusable pair the catalogue gates exist to prevent (porgy-58, 2026-09-23).
It must name X only.

> **As built in P5 (2026-09-24):** *"Read X (Twitter) posts, profiles and
> searches, or search Instagram accounts. Call this when somebody links or
> asks about something on X, or wants an Instagram account found."* (39
> tokens as the L1 gate counts it). It names X for every read, and Instagram for the one verb it still
> serves: the overlap with `instagram_read` is gone (no profile, no story),
> and there is no `instagram_search` line to overlap until P11, when the
> Instagram clause leaves with the row. Naming X only would have left
> Instagram account search, which works today, out of the line that
> advertises it — the per-verb cutover's "none goes dark in between", one
> level up. A `story` or Instagram `profile` asked of `social_fetch` answers
> `unavailable` with a note naming `instagram_read`.

## Order

`instagram_read` **replaces P5** of `17`: it builds on P2–P4 and lands after
the catalogue's #367 and #393 (rebased onto them). `instagram_search` lands
with P11. P10 and P12 each add a read to `instagram_read`.

## How to prove it

Routing cases in the catalogue's tool_selection eval corpus (on #393's
branch), run in both catalogue arms (resident and deferred), n ≥ 3. They are
added after this design merges, so the corpus never names a tool that does
not exist:

> **2026-09-24:** with deferral off (05 §3a) the resident arm is the one that
> ships; the deferred arm is worth running only as part of re-measuring the
> switch. P5 added four rows (`route_instagram_read_story`,
> `route_instagram_read_posted`, `route_social_fetch_instagram_search`,
> `route_social_fetch_x_link` in `evals/corpora/tool_selection.yaml`); until
> P11 an Instagram account search still routes to `social_fetch`. The link,
> post and `instagram_search` rows land with P10–P12.
>
> **P11 (2026-09-24):** `route_social_fetch_instagram_search` became
> `route_instagram_search_account` (same message, now expects
> `instagram_search`), and three rows joined:
> `route_instagram_search_find_person` ("find <name>'s Instagram"),
> `route_instagram_search_handle` ("what's <name>'s IG handle") and
> `route_social_fetch_x_search` (an X search stays on `social_fetch`). The
> X link row also names `instagram_search` as a tool it must not call.
> `route_instagram_read_find_member` pins the boundary as a pair: "find
> Miles's Instagram", about a member of room A, expects `instagram_read` (a
> read by name), while the same request about a synthetic name in no room
> expects `instagram_search`. Not yet run.
>
> **P10 and P12 (2026-09-24):** four rows joined:
> `route_instagram_read_reel_link` (a pasted `/reel/SynthReel01/` link,
> "what's in this reel"), `route_instagram_read_profile_link` (a pasted
> profile link, "who's this"), `route_instagram_read_handle` ("who is
> @synth.example on IG"), all expecting `instagram_read`, and
> `route_instagram_search_find_name_en` ("find Taylor Example on
> Instagram"), still `instagram_search`. Not yet run; the L1 line now names
> handles, so `route_instagram_search_handle` ("what's <name>'s IG handle")
> is the row most likely to move.

| Message | Must call | Must not call |
|---|---|---|
| a pasted `instagram.com/<user>/` link, "who's this?" (from P10) | `instagram_read` | `social_fetch`, `web_fetch` |
| a pasted `instagram.com/p/<code>/` link (from P12) | `instagram_read` | `web_fetch` |
| "what's on <friend>'s story" | `instagram_read` | `social_fetch` |
| "what's in that photo on her story" (after a read) | `media_fetch` | a second `instagram_read` |
| "find the account for <name> on IG" (from P11) | `instagram_search` | `instagram_read` |
| a pasted `x.com/…/status/…` link | `social_fetch` | `instagram_read` |
| a pasted news article link | `web_fetch` | `instagram_read` |

Live read-back: one trace in Jaeger per case, containing Nora's tool span and
the bridge's server and upstream spans. That needs tool spans in Jaeger: the
collector's filter was changed on 2026-09-23 to stop dropping them (it had
dropped every span carrying `gen_ai.operation.name`, which Strands also puts
on tool spans). Confirmed live on 2026-09-23: Jaeger now holds Nora's
`invoke_agent`, `execute_event_loop_cycle` and `execute_tool` spans, and no
`chat` spans, which is the intended split.
