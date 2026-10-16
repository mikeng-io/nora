---
type: design
title: "Instagram Retention — Room Speech Is Kept, Social Reads Are Not"
description: "The operator's retention boundary: anything said in a group is durable custody including one-time media; anything read off a profile or story is ephemeral and never enters custody. Plus the one conflict inside that rule, and the voice-note path."
tags: [nora, instagram, retention, media-custody, consent, voice]
timestamp: '2026-09-21'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: ../18-media-custody.md, rel: answers-open-question, provenance: asserted}
  - {dst: 05-phase2-social-and-opencli.md, rel: constrains, provenance: asserted}
---

# Instagram Retention — Room Speech Is Kept, Social Reads Are Not

## The rule (operator, 2026-09-21)

> **Amended 2026-10-16 (operator) for the social stream only**
> (design/cognition-loop/22-social-stream.md §5): the poller keeps what a
> member's posts and reels whole (media included, no retention), and of a
> story keeps what it *said* (metadata, caption, perception text, its image
> or video embedding) and deletes its media bytes at a bound. A live read on the tag path is
> unchanged: ephemeral.

Two classes, and the line between them is **who sent it to whom**:

| Class | What it is | Retention |
|---|---|---|
| **Room speech** | anything sent into a group Nora is in — text, image, video, sticker, voice note, **and one-time media** | **persistent**, normal custody |
| **Social read** | anything Nora fetched off a profile, story or feed | **ephemeral**, never enters custody |

`design/18-media-custody.md` §8 named retention "THE OPEN QUESTION
(operator's, not ours)". This is the first half of its answer, and it arrives
with a principle rather than a number: *Nora keeps what was said to her
circle, and does not keep what she went and looked at.*

## Why the ephemeral half is an architectural constraint, not a config flag

`media_objects` (R-90, migration 0052) is a **durable cache keyed by sha256,
shared across rooms and restarts**. That is deliberate and correct for room
speech. It also means a story frame that enters it **persists forever by
construction** — there is no per-object lifetime to set, and the existing
sweep (`SqlRecordStore.prune_all`) prunes blobs whose *messages* are gone.
A social read has no message, so it would never be swept.

So "temporary" cannot be implemented as a short TTL on the existing path. It
requires a path that **never writes to custody at all**: bytes fetched,
comprehended, rendered into the turn, dropped. The comprehension may be
quoted into the conversation the way any tool result is — that is Nora
recounting what she saw, not Nora holding it — but the bytes and any
content-addressed row do not survive the turn.

Two consequences worth stating before implementation:

1. **Re-reading costs a re-fetch.** If Nora looks at the same story twice she
   pays for it twice, and if it has expired the second read simply fails.
   That is the intended behaviour, not a gap.
2. **The comprehension is the durable artefact, not the media.** Whatever
   Nora writes into a note or a memory about a story outlives the story. The
   boundary bounds bytes, not recollection — and recollection is the part
   that actually reaches other people.

   **Three named paths carry it, and the third is automatic:**

   | path | what it keeps | triggered by |
   |---|---|---|
   | the note tier | an entry Nora wrote | a Dreamer pass deciding to |
   | `media_objects` | the bytes themselves | `media_fetch`'s cache write |
   | **`feedback.py` → Honcho** | **every reply Nora sends** | **every reply, always** |

   The third was missed in the first draft of this document and is the one
   that needs no decision to fire. `nora/engagement/stages/feedback.py` feeds
   **Nora's own replies** under the `nora` aiPeer into the conversation's
   session — *every* sent message, since `deliver.sent_texts` is a list, with
   only refused or failed messages excluded. The fire path drops
   `memory_feed_stage` and deliberately keeps this one, because "a fire turn
   is still something she said".

   So if Nora reads a story and **says what is in it**, that sentence is in
   Honcho. The stage's own docstring notes why that is worse than a note: the
   store keeps CONCLUSIONS rather than text, and it is "the one Honcho failure
   a backfill cannot undo".

   Nothing here is a defect — the mechanism is correct for every existing
   caller, exactly like `media_fetch`'s cache. It means the retention rule
   cannot be enforced downstream of the model at all: **by the time Nora has
   phrased an answer, the durable copy is already inevitable.** Any real bound
   on social recollection has to act on what she is allowed to *say*, not on
   what is stored afterwards — which is a doctrine question, not a storage
   one.

## The mitigation: a story must be SAID to be a story

Operator, 2026-09-22: Nora must name a story *as* a story, because stories are
perishable.

This is the only mitigation that reaches the leak, and the reason is that it
travels **with the claim** rather than sitting under it. A storage rule cannot
touch a Honcho conclusion; a phrasing rule is already inside it.

### Attributed versus asserted

The same information, two half-lives:

| phrasing | what it is | how it ages |
|---|---|---|
| "his **story** from 3h ago showed a beach" | a claim about a **perishable artefact**, with a timestamp | reads as stale on its own; a reader knows the source is gone |
| "he was at the beach" | a claim about **the world** | never expires, never sourced, indistinguishable from something he said |

Both are true when written. Only the first stays honest a week later. The
second has **laundered a perishable observation into durable knowledge**, and
nothing downstream can tell that it did — which is exactly how a dossier gets
built out of permitted acts (`07` §correlation).

### This has precedent in Nora

It is the pattern the codebase already uses for content whose trust or
lifetime differs from ordinary speech:

- **R-13 `untrusted` wrapping with provenance** — retrieved stranger-written
  text is marked as such, and `SocialProvider`'s docstring already requires it
  for social reads.
- **Media labels and handles** (R-90) — `[image a3f2c1]` says *what kind of
  thing* this is, in the body, where every consumer sees it.
- **Note anchor dates** — an entry carries when the thing happens, so it can
  retire.

A story marker is the same move applied to a source whose *lifetime* is the
unusual property rather than its trustworthiness.

### What this requires, in two places

**1. The tool must emit what the phrasing needs.** A story result already
carries `taken_at` and the reel's `expiring_at` (measured: 24h). Both must
reach the model, and the source kind must be explicit — `instagram_story`,
not a bare URL that could be anything. A model cannot attribute what it was
not told.

**2. The instruction belongs in the TOOL RESULT, with doctrine as the
backstop.** Revised 2026-09-22 on a measurement from the session compressing
prompt tokens: **54% of all tool-description text — 8,602 of 16,045 tokens —
is result-handling prose that is only read after a call returns, and it is
paid on every call of every turn for all 54 tools.**

That argues the schema is the wrong home, and so, more weakly, is doctrine:

| home | cost | when read |
|---|---|---|
| tool **description** | paid every turn, for every tool, forever | before any call |
| **doctrine** | paid every turn | always |
| tool **result** | **paid only when a story is actually read** | exactly when it applies |

The result wins on all three counts. It is free unless a story was fetched, it
sits **adjacent to the `taken_at` and `expires_at` it is talking about**, and
it arrives at the moment the model is deciding how to phrase the answer rather
than thousands of tokens earlier.

So a story result carries its own framing — that this is a story, when it was
posted, when it expires, and that reporting it means saying so. Doctrine keeps
one short line as the backstop, because a result-carried instruction is weaker
than a standing rule and a model that ignores the result should still meet the
rule.

The general lever is worth noting beyond this bundle: **instructions about how
to handle an answer are cheaper next to the answer than in the schema that
described the question.**

### It is a UX improvement, not a bound — external review, 2026-09-22

Relabelled after an outside architect read this section and rejected its
framing (`12`). The judgement: relying on an instruction to protect a property
already classified as unenforceable is "a comfortable sentence", and **this
bundle's own evidence convicts it** — in the Relevance Gate measurement,
rewriting a model's instruction made it worse three times running, once
unsafely.

So attribution phrasing stays, because it costs nothing and helps when it
works, but it is **not the bound**. The real bound is a provenance class on
every memory write (`room-speech` vs `ephemeral-read`), so an
ephemeral-derived conclusion can be expired and a deletion request is a query
rather than an excavation. That changes a shared memory path and is out of
this bundle's scope; it is named in `12` so it is owned rather than implied.

### The honest limit

**This is a soft bound and will sometimes fail.** A model can be told to
attribute and assert anyway; nothing enforces it, and no test can pin the
phrasing of free text.

But it fails *gracefully*, which the storage rules do not. A missed
attribution is one wrongly-durable sentence. A missing storage rule is every
sentence, silently, forever. And when it works, the durable artefact carries
its own expiry notice — which is the best available outcome once the
conclusion is written, because at that point deletion is not on the table.

Worth stating plainly: **this does not make social reads ephemeral.** It makes
what survives *legible as perishable*. Those are different, and the second is
what is actually achievable.

The platform agrees with the rule, which is a good sign: a story carries
`expiring_at`, measured at 24 hours. Media Nora kept would outlive its source.

## The conflict inside the rule: one-time media — RESOLVED, and PARKED as restriction R1

The rule says one-time images and videos sent **to the group** are persistent.
Instagram only lets that be true for one of its three modes.

Measured 2026-09-21, three photos sent into the group seconds apart, one in
each mode, then read back from thread history:

| sent as | `view_mode` | `content.attachment` | bytes retrievable without opening |
|---|---|---|---|
| view once | `0` | `null` | **no** |
| allow replay | `1` | `null` | **no** |
| keep in chat | `2` | present, with a CDN url | **yes** |

The `0`/`1`/`2` to mode mapping is read off the operator's stated send order,
not off a label in the payload. What is directly measured is the part that
matters: **two of the three modes serve no bytes at all**, in the live delta
and in thread history alike, to a client that has not opened them.

So the rule is satisfiable exactly where Instagram permits it:

- **Keep in chat** → persists normally. No fetch, no state change, no
  visibility question. It behaves like any other image.
- **View once and allow replay** → *cannot* be persisted passively. There is
  nothing to store; the message announces media that the server will not hand
  over.

This also retires the question this document previously posed. It is no longer
"is fetching visible to the sender" — there is no fetch available. Persisting
these two modes would require Nora to **open** them, which is a deliberate
state change, not a read. Whether opening is visible to the sender remains
unmeasured, and is now a question only worth asking if the operator wants
opening to happen at all.

**Recommendation: do not open.** A one-time message whose sender chose "view
once" is the clearest possible statement of intent, and unlike the story case
there is no ambiguity to interpret. v1 records that one-time media was sent,
by whom and when, with no bytes — which is exactly what a human in the room
who did not tap it would also have.

## The consent note, recorded rather than assumed

Persisting view-once media deliberately defeats what its sender chose. They
picked "view once"; Nora keeps it. In a group of the operator's friends that
is the operator's call and it has been made — but **the other people in the
room did not make it**, and unlike the story case they have an explicit,
expressed intent that is being overridden rather than merely unexamined.

This is recorded so that it stays a decision. The failure mode is not that the
call is wrong; it is that in six months it reads as a default nobody chose.

Corollary from `05`: story reads are invisible to their authors, so the circle
has no signal that Nora watches. Between the two, everything Instagram
normally tells a person about who is looking at their content is silent for
this account. That is worth saying out loud once, here.

> **Withdrawn 2026-09-24.** The corollary above rests on `05`'s claim that
> story reads are invisible to their authors, which `05` itself withdrew:
> whether the author sees the view is unmeasured (design 04, 05 §Reading a
> story). Nora's story rule is justified as a privacy choice, not by either
> answer (`18` §"A story is opened only when a person asks").

## Voice notes

**Structurally supported, unmeasured live.** The protocol carries them as a
first-class content type:

```
SlideMessageAudiosContent -> MessageContentAudio{ AudioAttachments[] }
AudioAttachment{ AttachmentCDNURL, PlayableDurationMS, WaveformData, AttachmentFBID }
```

A direct CDN url, a duration and a waveform — the same shape as image and
video, so the transport needs no special case beyond one more branch in the
media normalizer (`03-transport-contract.md` already lists four inbound
shapes; this is the fifth).

Voice notes are **room speech**, so they are persistent, and they land on
Nora's existing path: R-90 makes `voice note` a media label, and it is the one
label rendered **unclipped**, because it is conversation content rather than a
description of content. Nothing Instagram-specific is required for the
comprehension — the STT work already in flight applies unchanged.

No voice note was sent during the measurement window, so "the bytes arrive and
play" is claimed from the type definitions and the converter, not from a run.
It is listed in `04-capability-evidence.md` as unmeasured for that reason.

## What this does not yet decide

- **How long "ephemeral" is within a turn.** A story fetched for one question
  and referenced again three messages later either re-fetches or was held; the
  rule says do not persist, and says nothing about a per-turn buffer. This
  matters because the natural implementation — hold bytes for the life of the
  agent turn — is already a cache, and the question is whether it may outlive
  a single tool call.
- **Whether a story's comprehension may enter a note.** The bytes are bounded
  by this document; the note tier is not, and a note is exactly the thing
  designed to outlive the conversation.
