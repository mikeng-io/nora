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

## The conflict inside the rule: one-time media

The rule says one-time images and videos sent **to the group** are persistent.
The wire makes that non-trivial and possibly non-free.

`RAVEN_IMAGE` / `RAVEN_VIDEO` deltas arrive with `"attachment": null` — the
message announces that view-once media exists and carries nothing. Persisting
it therefore requires an explicit fetch.

**Whether that fetch is visible to the sender is UNMEASURED.** An earlier
draft of this bundle asserted that fetching view-once media *is* viewing it
and is visible to the sender. That was an inference, and the story measurement
showed exactly how such inferences fail: `FetchReel` does **not** advance the
`seen` watermark, so reading a story is invisible — the opposite of what the
capability surface suggested. The same separation may or may not hold for
raven media.

**The test, which needs one message from the operator:** send a one-time image
to the group, let the bridge attempt to resolve it, then check in the
Instagram UI whether it shows as opened. Decidable in one exchange, and it
changes the design:

- **Fetch is invisible** → persist one-time media as the rule says, no further
  question.
- **Fetch marks it opened** → persisting it makes Nora visibly open every
  view-once message in the room, immediately, forever. That is a behaviour the
  group will notice and should be told about, not a storage detail.

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
