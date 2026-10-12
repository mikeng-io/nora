# 32. Shared media: notes for her, not a report to relay

**Date:** 2026-09-26. **State:** built on branch `mikeng-io/shared-media-register`,
not deployed. No eval: the operator ruled this cannot be meaningfully
evaluated, so the study below is the evidence for the change, and a live
read-back after deploy is what confirms or reopens it.

## The complaint

Operator, 2026-09-26: when he tags Nora in a group about a reel, story, post,
picture or clip, she "reports" it. Her answer opens by presenting the media
back (what the picture is, what is in it, in order) instead of reacting the
way a friend who saw it would. The public summons has its own doctrine and
was fixed separately (#575); this note covers the room tag path only.

## Method

- **Store and query.** Langfuse on norty, every trace named `engagement` (the
  tag path) with `fromTimestamp` 14 days back, each fetched whole
  (`/api/public/traces/<id>`). Read on norty, parsed there; only aggregates and
  patterns left the host.
- **Window.** 327 traces, 2026-09-12T09:49Z to 2026-09-25T16:49Z. That is the
  whole population in the window, not a sample: the tag path runs about 25
  turns a day.
- **Per turn.** The first `chat` generation's input: the `<tag_message>` (and
  its `<replying_to>` quote), the last three `<m>` rows of `<group_transcript>`
  sent within 10 minutes; every `TOOL` observation with its output; the reply
  (the `send_reply` text, else the final `invoke_agent nora` message).
- **Candidates.** 66 turns carried media: a placeholder (`[image …]`,
  `[video …]`, `[sticker …]`, `[cover image …]`, file, voice note) or a
  `[shared post|reel|story …]` line in the tag, its quote or the recent rows,
  or a call to `media_fetch` or `instagram_read`.
- **About the media.** 27 of the 66 were turns whose subject was the media.
  The other 39: 20 about images she had generated herself (their delivery was
  failing that day, and she described what she drew because it had not
  arrived; out of scope), 8 with an unrelated sticker in view, 4 about files
  or a voice note, 7 with an unrelated image or link in view.
- **Split.** 18 social shares (Instagram posts, reels and stories, photos,
  a game clip) and 9 help screenshots (an error dialog, an order page, a
  parcel tracker), because a screenshot sent for help is read to answer a
  practical question, and the complaint is about the first kind.

### The classification rule

Applied by one reader to all 27, the same way:

- **Reporting**: the reply's first sentence (to the first 。！？ or line break)
  has the media or what it depicts as its subject and states what it is,
  shows or contains (subject, composition, layout, visible text) before any
  stance of hers. An opening 「睇到」 or 「睇咗」 is classified by the clause
  after it.
- **Reacting or answering**: the first sentence is her own stance (a take, a
  joke, a question back) or the answer to a question the tag asked that is
  not «what is in it».

## What the numbers say

| slice | reporting |
|---|---|
| social shares | **16 / 18** |
| help screenshots | 3 / 9 |
| social, by type: Instagram post | 6 / 6 |
| Instagram reel | 3 / 4 |
| Instagram story | 3 / 3 |
| photo or image | 3 / 4 |
| video clip | 1 / 1 |
| social, by ask: «what do you see», «what is in this reel», «read this» | **13 / 13** |
| a bare tag, a comment, «what does this mean» | 3 / 3 |
| a question about something else («where is it»), or «what do you think» | 0 / 2 |

By how the media reached her input (a turn can carry several):

| framing in her input | reporting |
|---|---|
| a `media_fetch` reading with the note «Report it as what it looks like» | 5 / 5 |
| a `media_fetch` reading with no note (before that note existed, 09-24) | 11 / 12 |
| `instagram_read`, post read, note «report what an item shows» | 1 / 2 |
| `instagram_read`, story, or post before any item was read | 4 / 4 |
| a `[shared …]` line in the transcript | 10 / 11 |
| a placeholder with its description in the transcript (help included) | 4 / 10 |
| no tool call at all, social | 2 / 3 |

**The reply is the reading, translated.** In all 19 reporting turns (judged by
hand), the reply's opening follows the reading or caption she had, element by
element and in its order: the subject, then hair, expression, background and
lighting; left panel then right panel; the first, second and third image each
with its overlay text; a pet's position, gaze and collar. The vision readings
are English inventories («Two young women taking close-up selfie. Left woman:
long dark brown hair…»), and the reply is that inventory in Cantonese.

**Her own reports demonstrate the register back to her.** 14 of the 19
reporting turns had at least one of her earlier media reports in the
transcript in view (in one Instagram room, up to 10 of the 14 rows of hers in
view were reports); 1 of the 8 non-reporting turns did.

### Ten reporting examples, by pattern

1. Shared story, «what do you see»: the subject count and shot type, then
   hair, expression, background, lighting, then the on-screen text.
2. Shared post meme: «a split-screen meme», left panel, right panel, caption.
3. Shared post, three images: «looked, three panels», then each panel with its
   overlay text.
4. Shared post, three cards: «three panels, all pixel art», then card by card
   with the prices on them.
5. Shared reel, cover only: «one image:» the person, clothing, camera angle,
   setting, a banner; then a note that only that frame was read.
6. Shared reel, cover only: the people, what they hold, the table, the plants
   behind, the text overlay, in the reading's exact order.
7. Shared reel, video read: livery, helmet number, weather, camera position,
   then the corners in sequence, then the title overlay.
8. Their own story, «read my story»: the pet and each filter item, then what
   happens mid-clip.
9. A photo of a pet with a bare tag: its position, gaze and collar, the
   reading's two sentences in order.
10. A game clip with a one-line comment: «seen, it's game footage», then the
    action shot by shot.

### The non-reporting turns, by pattern (8 exist, not 10)

1. Shared reel, «where is it»: the place first, then one clause on the room.
2. A mock notice, «what do you think»: a take on the person and the episode,
   compared with an earlier one, ending on a tease.
3. An error screenshot, bare tag: what the error means and the fix.
4. A product screenshot, «what do you think»: a verdict first, then why.
5. A parcel screenshot, «why is it held»: the reasons, no description.
6. A screenshot of a post about a show, «what time»: the airtime in local
   time, then a joke off the post.
7. A restaurant photo, «can we book»: yes, and how.
8. A game page, «which one updated»: the game and the update.

Every one answers a question other than «what is in it». There is no turn in
the window where she was asked about a shared item's content and reacted
rather than reported.

## Where each framing lives (origin/master before this change)

| what she reads | where |
|---|---|
| `media_fetch` note: «A vision model wrote this about media somebody else sent or linked. Report it as what it looks like, not as fact you can vouch for.» | `nora/agent/tools.py:6973` |
| `media_fetch` docstring, first line: «Look at an image or video and say what it shows.» | `nora/agent/tools.py:6318` |
| `media_fetch` reading body: title, description, content, caveat | `nora/agent/tools.py:1930` (`_stored_reading`) |
| `media_fetch` cover note | `nora/agent/tools.py:3340` (`_COVER_NOTE`) |
| `instagram_read` post note: «…report what an item shows as how it looks, not as fact.» | `nora/infra/social/render.py:124` (`POST_READ_FRAMING`) |
| `instagram_read` story note: «Each shows line is what a vision model saw in that frame…» | `nora/infra/social/render.py:80` (`STORY_READ_FRAMING`) |
| `instagram_read` item line label `shows: ` | `nora/infra/social/render.py:520` |
| `instagram_read` docstring: «Show what an Instagram post or reel contains…» | `nora/agent/tools.py:8210` |
| transcript placeholder `[image a3f2c1: title - description]` | `nora/domain/media.py:731` (`render_placeholder`), joined at `:651`, applied by `enrich_placeholders` `:682` |
| the reading's register: «"description": one or two sentences on what it shows», «Describe this image…», «Describe what happens in this segment…» | `nora/infra/vision/comprehend.py:84`, `:103`, `:112` |
| the shared line `[shared reel by @x <link> — caption]` | `nora/platform/instagram/parser.py:449` |
| doctrine: «the words after the code are the short form: what it is, and what it shows» | `nora/doctrine/rooms/constraints.md:86` (operator-only; in `rooms/pictures.md` until 2026-10-12) |
| `rooms/voice.md` | no line on media; its «冒號拉個小標題出嚟 … 你已經喺度寫報告» already names the colon-heading tell her reports use |

## Decision

**The tell is carried by the input, not by an instruction.** The explicit
«Report it» sentence made no difference (5 of 5 with it, 11 of 12 before it
existed), so rewording an instruction is not what moves this. What every
reporting turn has is a machine's inventory of the media, handed to her as a
description, with nothing in the input saying what it is FOR or that the
person who sent it is already looking at the thing. She translates it, and
each translation becomes a demonstration for the next turn.

The reading itself stays: it is what she needs to see the media, and the
change is framing, not information. So:

1. **Tool-result framing (reaches 15 of 18 social turns, the ones that called
   a tool).** The `media_fetch` note (`_MEDIA_READING_NOTE`) and both
   Instagram read notes now say what she is reading: a vision model's notes,
   so she can see it too, with the hedge (how it looks, not fact) kept. The
   `media_fetch` and post notes add that whoever sent or linked it has usually
   seen it. The story note does not: a story is read by person or handle, and
   the asker may not have seen it. Nothing asks her to report.
2. **One `rooms/conduct.md` paragraph (v33), because the framing cannot reach
   the rest.** 2 of the 16 reporting social turns read the media only from a
   transcript placeholder or a shared line, with no tool call; the only
   framing there is `pictures.md`, which is operator-only, and the block type
   is shared with audiences that hold no tools. The paragraph sits under
   «Follow the conversation», beside «do not restate the request», and is
   written as what she is reading and who has the thing, not as a list of
   moves: something somebody shows her is already in front of them, what she
   was given on it is for her own eyes, and even asked what is in it, the
   answer is what she noticed, not the list.

**Not changed, and why:**

- The vision prompts. Their register is the demonstration, but the readings
  are stored, feed memory and the media join, and serve other audiences;
  changing them is an information change for every reader, not a framing one.
- The `media_fetch` docstring's first line («say what it shows»). It was
  constant across every turn, so the data cannot speak to it, and a
  catalogue line is routing text that ADR 0015 changes only with a
  `tool_selection` measurement.
- `rooms/constraints.md:86`. Operator-only. See below.

## For the operator

`rooms/constraints.md:86` frames a transcript description as «the short form: what it
is, and what it shows». That is accurate, and it is also the only framing on
the path the conduct paragraph now covers. A clause there saying the thing is
in front of whoever sent it would put that framing where the placeholder is
defined. Your file, your call.

## Read-back after deploy, and what would reopen this

- Re-run this study (same method, same rule) over the tagged media turns in
  the 14 days after the deploy. Before: 16 of 18 social shares, 13 of 13
  describe-type asks.
- **Reopen** if describe-type asks still open with an in-order inventory in
  most turns once 10 or more have accumulated. The next lever is then the
  demonstration itself: the register of the vision reading
  (`comprehend.py:84`) or a friend's-eye summary served beside it, which is an
  information change and needs its own design.
- **Reopen the other way** if answers about a screenshot sent for help stop
  carrying the fact it was sent for (an error code, a price): the paragraph is
  about what a friend showed her, not about reading a practical screenshot.
