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

The platform agrees with the rule, which is a good sign: a story carries
`expiring_at`, measured at 24 hours. Media Nora kept would outlive its source.

## The conflict inside the rule: one-time media — RESOLVED by measurement

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
