---
type: design
title: "Shared Reels and Posts — Three Depths, and Why Most Of Them Cost Nothing"
description: "A reel, post or story shared into a group is an XMA. Measured live (2026-09-24), it carries its author, its link and a thumbnail, and usually no caption. The volume problem the operator raised answers itself: render the kind, author and clean link always, and read the item through its link only when Nora is asked. The thumbnail is dropped when the link validated (a cover handle beside a link was read instead of the link, live) and kept, on demand, only when there is no link."
tags: [nora, instagram, xma, reels, media, cost]
timestamp: '2026-09-24'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 06-retention-and-custody.md, rel: constrained-by, provenance: asserted}
  - {dst: 03-transport-contract.md, rel: extends, provenance: asserted}
---

# Shared Reels and Posts

## The operator's question

*"Regarding reading the reels that have been sent out (like the posts): how
exactly can Nora read them? Obviously they must be cached, and I don't expect
Nora will or can read all of the reels, because most of us are always spamming
in the Instagram group."*

The premise is right and the conclusion is better than expected: **most shared
reels never need to be fetched at all.**

## What is broken TODAY

A reel or post shared into a group is a `MessageContentXMA`
(`SlideMessageXMAContent`). The bridge's content-type switch
(`deploy/instagram-bridge/internal/gateway/instameow/inbound.go`) does not handle it, so it falls to the
default and produces `{text: "", media: null}`.

**Nora does not see "someone shared a reel". She sees an empty message.** In a
group where sharing reels is most of the traffic, that is the single largest
hole in her view of the room — larger than not being able to watch them.

## What an XMA actually carries

From the library's own types, all present in the delta with **no extra
fetch**:

| field | what it is |
|---|---|
| `CaptionBodyText` | the post's caption |
| `XMATitle` / `HeaderTitleText` | the author |
| `HeaderSubtitleText` / `SubtitleText` | context line |
| `TargetID`, `TargetURL` | the post's own id and link |
| `PreviewImage` / `XMAPreviewImage` | the thumbnail |
| `Typename` | reel vs post vs profile vs link |
| `XMATextBody` | the sharer's own accompanying words |

## Three depths, three costs

**Depth 1 — the kind, the author and the link. Free, and the default.**
Already in the delta. Rendered into the transcript as ordinary text, the same
as anything else said in the room. Nora knows what was shared and by whom, for
zero fetches and zero model calls.

> **Corrected 2026-09-24 (see [the measured shape](#2026-09-24--the-measured-shape-and-what-changed)).**
> This paragraph first said the CAPTION was the free depth — «Nora knows what
> it says» — and that a group discusses the caption more than the frames. The
> live shares measured that day carried **no caption at all**, and no title or
> subtitle either. The free depth is the kind, the author and the link; the
> caption is rendered when a delta happens to carry one, and nothing here may
> assume it does.

**Depth 2 — the thumbnail. Cheap, fetched at ingest, comprehended on demand.**
(As designed. As first BUILT, the thumbnail was comprehended at ingest like
any photo; on-demand is what it is since 2026-09-24 — see the dated section
below.)

> **Superseded for a share with a link, 2026-09-24 (see [a linked share
> carries no picture](#2026-09-24-after-454-a-linked-share-carries-no-picture)).**
> This depth exists now only for a share whose line has NO validated link.
> When the link validated, the thumbnail is not fetched, not held and not
> rendered: live, a handle beside the link was read instead of the link.
> The item's own frames come from depth 3, through the link.
The preview image goes through the existing custody path: bytes fetched at
ingest, sha256'd, a handle minted, rendered as `[reel a3f2c1]`. Comprehension
— the vision call — happens **only** when `media_fetch` is called, which is
only when the conversation makes the picture matter. This is exactly the
two-layer shape `media_fetch` already documents.

Why fetch the bytes eagerly but comprehend lazily: Instagram CDN urls are
signed and **expire**. A url stored and used later is a dead link, so lazy
*fetching* would silently stop working for anything older than its window.
Bytes are cheap (a thumbnail is tens of KB); the model call is the expensive
half, and that is the half kept lazy.

**Depth 3 — the video itself. Expensive, and deliberately not automatic.**
`TargetURL` names the reel. Fetching and comprehending a video is a real cost
and is worth it rarely — "what actually happens in that clip" is a question
someone has to ask. Left to an explicit call, and honestly time-bounded: by
the time it is asked, the url may be dead.

## Why the volume problem solves itself

The operator's worry is that a spammy group means a lot of reels. It does —
and the cost of a reel at depth 1 is **one row and one line**, which is the
same cost as somebody typing a sentence. Nothing is comprehended unless asked
for.

Two existing mechanisms do the rest:

- **`media_objects` is keyed by sha256 of the bytes** (R-90), so the same reel
  shared by three people in the same hour is fetched and comprehended **once**.
  In a group where the same clip circulates, that is where the saving is.
- **The comprehension is joined at render** rather than baked into the body,
  so a reel that is never asked about never costs a model call at all — not at
  ingest, not at recall.

## What this needs

1. **Bridge**: an `XMA` branch in the content-type switch, emitting the caption,
   author, target id/url, typename, and the preview image url as an ordinary
   `media` object so custody handles it with no special case.
2. **Parser**: a `reel`/`post`/`link` media kind and its label, so the
   transcript reads `[reel a3f2c1]` beside the caption. *Not built this way —
   see 2026-09-24: the kind is a word in the shared line, read off the url,
   and the thumbnail is labelled `[cover image a3f2c1]` (it kept the
   ordinary `[image a3f2c1]` until «the cover says it is one» below), and
   only when the line has no link: a linked share renders no thumbnail at
   all (the section after that one).*
3. **Nothing else.** Depth 2 rides the custody path that now exists for images;
   depth 3 is `media_fetch` with a url, which already works.

## The retention question, answered in advance

A shared reel is **room speech** (`06`): somebody sent it into the group, so
its caption (and, for a share with no link, its thumbnail) persist like any
other message content. That is
not in tension with the story rule — a story is something Nora *went and
looked at*, a shared reel is something a member *said to the room*.

The `TargetURL` points at content that lives outside the room, and following
it is a Tier 2 read under `07` — the sharer put the address in the
conversation, which is exactly what Tier 2 describes.

## 2026-09-24 — the measured shape, and what changed

**Measured** on a live capture of one shared reel and one shared story, with
nobody tagging Nora. No usernames, shortcodes or ids are recorded here.

| field | reel | story |
|---|---|---|
| `author` | present | present |
| `target_url` | present | present |
| `typename` | `SlideMessagePortraitXMA` | `SlideMessagePortraitXMA` |
| `caption`, `title`, `subtitle`, `layout` | empty or absent | empty or absent |
| `media` | `{kind: image, url, width, height}` — the thumbnail (`fromXMAPreview`) | the same |

Three findings follow from that table.

1. **`typename` cannot tell a reel from a story.** Both carried the same
   string. The table in *What an XMA actually carries* lists `Typename` as
   «reel vs post vs profile vs link»; on the live wire it is not.
2. **The url is the only discriminator, and its query is not safe to
   render.** A reel's `target_url` is `/reel/<shortcode>/` followed by a long
   query of tracking flags. A story's is a three-segment path,
   `/stories/<username>/<story_pk>`, whose query carries `reel_id` and
   `reel_owner_id` — and `reel_owner_id` is the owner's **igid**, which R-18
   keeps off every surface a model reads.
3. **The thumbnail was comprehended at ingest.** Both thumbnails had
   `media_objects.description` filled by the image describer with nobody
   asking. Depth 2 above says «comprehended on demand»; the build ran the
   same ingest pass for a shared thumbnail as for a photo somebody sent.

And the transcript line was `[shared post by <author>] [image <handle>]`: the
link dropped, and the story called a post.

### The kind comes from the url's path

`nora/platform/instagram/parser.py` (`_shared_target`) classifies on the PATH
of `target_url` and nothing else:

| path | kind |
|---|---|
| `/reel/<code>/` or `/reels/<code>/` | reel |
| `/p/<code>/` | post |
| `/stories/<user>/<pk>` | story |
| anything else | post, as before |

No other vocabulary is introduced — `typename` and `layout` stay
uninterpreted, as the bridge's comments require, because no measurement
supports a reading of them. The bridge is unchanged: it classifies nothing,
and the adapter is where a url is read.

### The link is rebuilt, never passed through

The rendered link is assembled from validated path segments onto a fixed
origin — `https://www.instagram.com/reel/<code>/`, `/p/<code>/`,
`/stories/<user>/<pk>/` — with **no query string and no fragment**. Each
segment must match a strict charset (shortcode: base64url; user: letters,
digits, `.`, `_`, at most 30; pk: digits). A host that is not Instagram's, a
port, credentials in the netloc, or any segment outside its charset leaves
the link **out** rather than rendering a raw one. The kind still renders.

**A story's username segment must be the author.** The one segment in any
shape that carries a name must hold a non-digit (an all-digit segment is the
shape of an igid, and the egress screen deliberately lets bare digit runs
through) AND equal `shared.author`, `@` stripped, case-insensitively.
Anything else leaves no story link.

**Nothing a stranger wrote can open a bracket.** The author renders, with its
`@`, only when it is a whole handle by the same rule (username charset, at
least one non-digit); otherwise it is omitted — which also drops a
link-preview title such as `The Site Title` from the author slot. In the
caption, `[` and `]` become `(` and `)`. Without both, a caption like
`x] [image 0a1b2c] [y` closed the bracket and forged a media placeholder, and
the render join would have hung another message's reading off it.

The line is now `[shared <kind> by @<author> <link> — <caption>]`, each part
present only when it is. The link sits inside the bracket, before the
caption: inside, because it belongs to the shared item and not to the
sharer's words; before the caption, because the caption is a stranger's free
text. The placeholder run, when there is one (a share with no link, since
the section «a linked share carries no picture»), stays trailing, which
`body_with_handles` needs.

### The thumbnail depth is on-demand only, by operator rule

The operator's rule: **Nora reads shared content only when tagged, never on
every share or forward.** The parser marks the thumbnail of a shared item
`MediaRef.on_demand`, and the custody stage
(`nora/ingest/stages/media.py`) schedules no comprehension subject for an
on-demand ref. Custody itself is unchanged — the bytes are fetched at ingest,
because the CDN url expires, and the handle is rendered — so:

- the transcript shows `[cover image <handle>]` (it said `[image <handle>]`
  until the later 2026-09-24 section below) with no description, claiming
  nothing (the render join adds a reading only for a row that has one).
  Since «a linked share carries no picture» below, this holds only for a
  share whose line has no link; a linked share has no thumbnail to hold;
- `media_fetch` on that handle finds no stored reading and does a fresh look,
  at a real cost, when somebody asks;
- the sha256 dedupe keys on whether the ROW has been read, so the same bytes
  later sent as a direct photo are read then. In the other order — a photo
  read first, then shared — the share renders the existing reading and costs
  nothing.

Photos and videos people send directly are read at ingest exactly as before.

### 2026-09-24, later: the cover says it is one

> **Superseded for a share with a link** by the next section: measured live
> after this shipped, the label and the note did not change what she did.
> What stands from here is the fallback, a share with no link, which still
> renders `[cover image <handle>]` with the role and the note.

**Measured live**, once, the same day: a reel shared into a group rendered as
`[shared reel by @x <link>] [image <handle>]`, and a member asked Nora what
was in it. She called `media_fetch` on the handle and nothing else, read the
one still frame the cover is, and told the room she could see one frame and
not the clip. `instagram_read` was in the turn and reads a reel by its link;
the link was in the line she was answering about.

Following AGENTS.md («read the input before rewriting the instruction»), the
input was what could not ground the judgement: the line called the cover an
`image`, the same word a member's own photo gets, and `media_fetch`'s answer
did not say what it had served. Three changes, input first:

- **The transcript names the role.** An on-demand preview image renders as
  `[cover image <handle>]` (`nora.domain.media.label_for`). `cover image` is a
  member of `LABELS`, so every reader of the placeholder grammar
  (`placeholders_in`, `enrich_placeholders`, `body_with_handles`, the join's
  `label_agrees`, which accepts it only over `image/*`, and the outbound
  marker strip, now built from `LABELS`) handles it like any other label.
- **The result says what it served.** Custody records the role on the blob
  (`MediaBlob.role = "cover"`, written into `media_json` only when set, so
  every older row reads as an ordinary attachment). The role is set on the
  label's own predicate (`label_for(ref) == "cover image"`), so a preview
  that is a video or a sticker carries neither. When `media_fetch` serves a
  handle whose every sighting in view is a cover, its `note` says it is only
  the cover of a shared reel, post or story and that the item is behind the
  item's link, if that line carries one (the parser leaves an invalid link
  out, so the note does not promise one).
- **The when-clause names a share.** `instagram_read`'s L1 is now «Call this
  when somebody asks about someone's Instagram or a shared or linked post or
  reel» (39 tokens as the L1 gate counts it, was 38). *Replaced later that
  day by an L1 naming what the read returns (every item, videos included);
  measured on a replay of the live prompt, see `18` §«the tool-catalogue
  contract».*

The routing is measured by `route_instagram_read_shared_*` in
`evals/corpora/tool_selection.yaml`; the shape those cases can and cannot
carry is written beside them. They also carry an ORDER criterion
(`metadata.before`): `media_fetch` running before `instagram_read` is a FAIL,
because the live turn read the cover first and then stopped, and a typed
handle that names no blob otherwise lets that order pass. Until they run on
the deployed model, this is a fix to the input with no routing number behind
it.

**Known edge: a share recorded before this re-parses differently.** A share
stored before this change says `[image x]`; the same delta parsed now says
`[cover image x]`. Anything that compares a re-parse against the stored body
would read that as a change to the message's content. Measured against the
code as it stands, nothing does on Instagram: an Instagram edit
(`_parse_edit`) carries only the new text, with no `media` and no `shared`,
so `nora/ingest/stages/edit_media.py` returns before comparing placeholders.
It would matter the day an edit re-delivers a share with its media, and then
only for shares already in the record. Not fixed.

**Possible follow-up: the reaction picker's reading of the label.**
`nora/doctrine/reaction.md` tells the GIF picker that `[sticker …]` and
`[image …]` lines are «people answering with a picture instead of words». A
`[cover image …]` is not that; it is the still of something shared. One clause
saying so was drafted in #454 and taken back out: it is a doctrine change,
live on the next checkout pull, and unmeasured. It lands only with a run of
the reaction replay set (the 104 replayed replies its version history is
measured on) before and after, and a version bump with the served-section sha
repinned in `tests/test_doctrine_version_parity.py`.

### 2026-09-24, after #454: a linked share carries no picture

**Measured live**, twice, with #454 deployed. The line read
`[shared reel by @x <link>] [cover image <handle>]`. Asked «what is in this
reel» in one turn and «what do u see» in another, Nora called `media_fetch`
on the cover handle and nothing else. It returned a real reading of one
frame WITH the cover note (only the cover, the item is behind its link), and
she answered «I only read this frame». `instagram_read(link=...)` was never
called. The read path itself was fine: `instagram_read`'s client on the same
live post returned its six items (one video, five images), and on the reel
its video.

**Why wording failed.** #454 changed two words the model reads (the label,
and the note on the result) and one it could act on (the L1 when-clause).
None of them removed the call that ended the turn. A handle in the line is
an affordance that a cheap call SUCCEEDS on, and a successful call with a
readable answer is a reason to stop; a note attached to that success is
read as a caveat on an answer she already has, not as an instruction to
make a second call. The eval had said about eight in ten would route to
the link first. It was wrong for a structural reason: its handle was typed
and named no blob, so `media_fetch` on it FAILED, and the failure is what
pushed her to the link. Live the handle is held, the call succeeds, and she
stops. The eval measured a different input from production (AGENTS.md: read
the input before rewriting the instruction; here the input was the eval's,
and it disagreed with production's).

**The structural rule: a line offers only the call that answers it.** When
the shared item's link validated, the parser emits NO media for its
thumbnail (`nora/platform/instagram/parser.py`, `parse_event`):

- no placeholder in the body: the line is `[shared <kind> by @<author>
  <link>]`, plus the caption as before, and nothing after it;
- no `MediaRef`, so custody downloads nothing, holds nothing and writes no
  `media_json`, and no reader (`body_with_handles`, the render join,
  `media_fetch`) can reference a handle the body does not carry. Keeping the
  thumbnail in custody without a placeholder was considered and refused: a
  held blob no line names is unreachable by construction, and the link reads
  the item's own frames.

`media_fetch` also closes the other door. Given an instagram.com page link
(a post, reel, story or profile, judged by `nora.domain.instagram_links.
parse_link`, the parser the transcript and `instagram_read` share), it
answers `nothing_found` at once, before any download and before the
media-configured gate, with the note «That link is an Instagram page, not a
picture or video file. Open it with instagram_read using this link, then
look at the media it returns.» `nothing_found` because R-27 gives that
status to «the URL is not a describable picture/video», and nothing was
tried that could have failed. A CDN url, an item's own file, is not an
instagram.com page and still downloads. `instagram_read`'s post result
already says each item's url is what `media_fetch(url=...)` looks at
(`_POST_FRAMING`), now pinned by a test.

**The fallback is #454's, unchanged.** A share with NO validated link (none
sent, a foreign host, a story whose user segment is not the author) keeps
`[cover image <handle>]`, `on_demand`, `MediaBlob.role = "cover"` and the
cover note: the cover is all there is.

**Measured by** `route_instagram_read_shared_*` in
`evals/corpora/tool_selection.yaml`, whose inputs are now exactly this
rendering: the link and no handle. That removes the typed-handle
distortion, because the eval's input is production's. The order criterion
(`media_fetch` before `instagram_read` is a FAIL) stays. The live test is
the real check; this reading reopens if a live turn on a linked share still
answers from anything but `instagram_read`'s result.

**Known edge, widened.** A share stored before this change carries a
`[cover image <handle>]` (or, before #454, `[image <handle>]`) beside its
link, and those rows stay as they are: `media_fetch` on one still serves the
cover with the note. A re-parse of such a delta now yields no placeholder at
all. As recorded above, nothing on Instagram compares a re-parse against the
stored body today (an Instagram edit carries no media and no `shared`).

### Known gaps, recorded and not fixed here

- **A shortcode can look like a key to the egress screen.** The shortcode
  charset admits `-` and `_`, so a code beginning `sk-`, `pk_`, `api-` and so
  on with 16 or more characters after it matches `_KEYLIKE` in
  `nora/agent/egress.py`. The failure is closed, not open: a reply that
  quotes such a link is refused, and nothing leaks. It costs an answer, rarely.
- **An XMA whose text fields are all empty still brings its thumbnail.** The
  bridge's `sharedOf` answers `nil` rather than an empty object when an XMA
  carries nothing readable, but the preview image is extracted separately
  (`fromXMAPreview`). Such a message reaches the adapter with media and no
  `shared`, so its thumbnail is NOT marked on-demand: it renders as a bare
  `[image]` as if a member had sent a photo, and it is read at ingest. Not
  seen in the 2026-09-24 capture, where both shares carried an author.
