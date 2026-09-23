---
type: design
title: "Shared Reels and Posts — Three Depths, and Why Most Of Them Cost Nothing"
description: "A reel, post or story shared into a group is an XMA. Measured live (2026-09-24), it carries its author, its link and a thumbnail, and usually no caption. The volume problem the operator raised answers itself: render the kind, author and clean link always, hold the thumbnail cheaply, and comprehend it only when Nora is asked."
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
   and the thumbnail keeps the ordinary `[image a3f2c1]` label.*
3. **Nothing else.** Depth 2 rides the custody path that now exists for images;
   depth 3 is `media_fetch` with a url, which already works.

## The retention question, answered in advance

A shared reel is **room speech** (`06`): somebody sent it into the group, so
its caption and its thumbnail persist like any other message content. That is
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
text. The placeholder run stays trailing, which `body_with_handles` needs.

### The thumbnail depth is on-demand only, by operator rule

The operator's rule: **Nora reads shared content only when tagged, never on
every share or forward.** The parser marks the thumbnail of a shared item
`MediaRef.on_demand`, and the custody stage
(`nora/ingest/stages/media.py`) schedules no comprehension subject for an
on-demand ref. Custody itself is unchanged — the bytes are fetched at ingest,
because the CDN url expires, and the handle is rendered — so:

- the transcript shows `[image <handle>]` with no description, claiming
  nothing (the render join adds a reading only for a row that has one);
- `media_fetch` on that handle finds no stored reading and does a fresh look,
  at a real cost, when somebody asks;
- the sha256 dedupe keys on whether the ROW has been read, so the same bytes
  later sent as a direct photo are read then. In the other order — a photo
  read first, then shared — the share renders the existing reading and costs
  nothing.

Photos and videos people send directly are read at ingest exactly as before.

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
