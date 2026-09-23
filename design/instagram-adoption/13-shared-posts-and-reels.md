---
type: design
title: "Shared Reels and Posts — Three Depths, and Why Most Of Them Cost Nothing"
description: "A reel shared into a group is an XMA, and it carries its caption, author and thumbnail in the delta itself. The volume problem the operator raised answers itself: read the caption always, fetch the thumbnail cheaply, and comprehend only on demand."
tags: [nora, instagram, xma, reels, media, cost]
timestamp: '2026-09-22'
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

**Depth 1 — the caption and author. Free, and the default.**
Already in the delta. Rendered into the transcript as ordinary text, the same
as anything else said in the room. Nora knows what was shared, by whom, and
what it says, for zero fetches and zero model calls. *This is what "reading
the reels" means for almost every case*, because a group discussing a reel is
discussing its caption and its author far more often than its frames.

**Depth 2 — the thumbnail. Cheap, fetched at ingest, comprehended on demand.**
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
and the cost of a reel at depth 1 is **one row and one caption**, which is the
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
   transcript reads `[reel a3f2c1]` beside the caption.
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
