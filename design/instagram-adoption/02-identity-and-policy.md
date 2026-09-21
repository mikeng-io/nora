---
type: design
title: "Instagram Identity — Three Id Spaces, and Which One Answers Which Question"
description: "Instagram hands out several ids per account and per thread, and they are not interchangeable. Getting this wrong makes Nora silently fail to notice she was tagged. Measured field values included."
tags: [nora, instagram, identity, policy, mentions]
timestamp: '2026-09-21'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: ../10-identity-and-policy-schema.md, rel: implements, provenance: asserted}
  - {dst: 04-capability-evidence.md, rel: evidence, provenance: measured}
---

# Instagram Identity — Three Id Spaces

## The failure this document exists to prevent

R-12 (`nora.engagement.stages.admission`) answers "is this sender Nora?" by
membership in a resolved `PlatformIdentity.aliases`. On Instagram the obvious
id is the wrong one, and choosing it produces **no error anywhere** — Nora
simply never recognises that she was tagged, in every room, forever.

This is the same class as the Discord plaintext-tag bug, asked in advance
rather than found in production.

## Measured, for the account `@not_____nora`, 2026-09-21

| Source | Value | Is it what a mention carries? |
|---|---|---|
| `viewer.GetFBID()` | `18781497603028340` | **no** |
| `viewer.GetUserId()` / `viewer.ID` | `79597894900` | no |
| cookie `ds_user_id` | `79597894900` | no |
| **`client.GetOwnFBID()`** | **`14963523149149302`** | **yes** |

`viewer.GetFBID()` is returned by `LoadIndex` directly beside the username,
which is exactly why it is the one a reader reaches for. It does not match.

The same split appears for other people. A live inbound message from the
operator carried:

```json
"sender":  {"name":"Miles Novak", "id":"110966695387753", "igid":"221731317"},
"mentions":[{"user_fbid":"14963523149149302","offset":0,"length":13,
             "profile_range_type":"PROFILE"}]
```

and the thread's member list gave `@nka_bpumvg` an
`interop_messaging_user_fbid` of `110966695387753` — the same number as
`sender.id`. So:

- **`interop_messaging_user_fbid`** is the messaging identity. It is what
  `sender.id` carries, what `mentions[].user_fbid` carries, and what
  `GetOwnFBID()` returns. **This is the id `identity.toml` must record.**
- **`igid`** (`221731317`) is the public Instagram numeric id. It is what
  `GetProfile`, `FetchReel` and the rest of the Phase 2 social surface take.
  It is NOT a messaging id.
- `ds_user_id` identifies the logged-in session, not the messaging peer.

Both are needed: messaging binds on interop fbid, social reads bind on igid.
An Instagram person entry therefore carries **two** identifiers, not one.

### One call yields both, for every member of a room

`GetThread` returns `ThreadInfo.Users[]`, and each entry carries **both**:

```
member @nka_bpumvg   interop_messaging_user_fbid=110966695387753   ID=221731317
```

`User.ID` is the igid — the same `221731317` that `GetProfile` and
`FetchReel` accept. So for anyone in a room Nora is already in, the mapping
from "person in this group" to "whose story can be read" is **derivable from
the thread itself**. No hand-maintained handle table is needed for a room's
own members; one is only needed for people outside it.

That is what makes Phase 2's circle-first scope cheap: the circle is the
thread membership, and it resolves itself.

Verified on a three-person room (2026-09-21), after a second member joined:

```
member @nka_bpumvg        interop_fbid=110966695387753     ID=221731317
member @gemini_.xoxo.04   interop_fbid=12116372067347151   ID=70503037937
```

Every message's `sender_fbid` matched that member's `interop_messaging_user_fbid`
exactly, so attribution across a real roster is an id join and never a name
match. `ADD_PARTICIPANT_XMAT` arrived as an ordinary message attributed to the
member who did the adding, which is how roster changes reach the adapter.

R-12's discrimination was then exercised directly. Two messages from the same
third-party sender, in the same room, seconds apart:

```
"@nka_bpumvg GH"        mentions[0].user_fbid = 110966695387753     -> not Nora
"@not_____nora love I"  mentions[0].user_fbid = 14963523149149302   -> IS Nora
```

`14963523149149302` is exactly `client.GetOwnFBID()`. So "was I tagged?"
reduces to one integer comparison against that value, with no name matching
anywhere in the path — and Nora correctly hears mentions of *other* people
without treating them as her own. This is the behaviour the Discord
plaintext-tag bug got wrong, measured before a line of adapter code exists.

**Do not validate the id by shape.** The two interop fbids above are
`110966695387753` (15 digits) and `12116372067347151` (17 digits, sharing the
`1784…` prefix with Nora's own). The space is not uniformly formatted, so any
length or prefix check would reject real members.

## Thread ids are also two

| Field | Value (group "Ridgeline") | Used for |
|---|---|---|
| `thread_fbid` / `ShortID` | `1009543911792316` | inbound deltas (`Delta.ThreadIGID`), `GetThread`, upload |
| `thread_igid` / `LongID` | `340282366841710301281152937372201004392` | **every send**: text, media, reaction, edit, unsend |

`FetchThreadID(shortID)` returns both. A send addressed with the short id does
not reach the thread. The bridge must resolve and cache the pair per room, and
the transport contract carries the short id as the room key because that is
what arrives inbound.

## What a mention looks like, in both directions

Inbound, a tag is a range over the text — the same shape Signal and WhatsApp
use, so the existing normalization has somewhere to land:

```json
"text_body": "@not_____nora",
"mentions": [{"user_fbid":"14963523149149302","offset":0,"length":13}]
```

Outbound, `SendTextRequest` carries `Mentions []InputMention{Offset, Length,
FBID}` **and** `MentionedUserIDs []string`. Both were populated in the
measured send and the mention round-tripped in the echo. Unlike the WhatsApp
v1 adapter — which accepts `mentions` and deliberately ignores them
(`nora/platform/whatsapp/send.py`) — Instagram should implement them, because
the capability is there and Discord parity is the Phase 1 bar.

## Consequences for `identity.toml`

An Instagram person needs both ids, and neither is the handle. Handles are
renameable; both numeric ids are not. Recording only `@username` would break
on any rename, silently, in the same way.

The room allowlist rule stands and matters more here: `persons` gates
answering, not recording. Everything said in an Instagram group is recorded
and reaches Dreamer notes, including by people who are not in `persons`.

## Open decisions

1. **Does a person entry span platforms by workspace, as today, with two new
   identifier kinds — or does Instagram's igid belong somewhere else because
   it is a social handle rather than a messaging address?** The schema
   (`design/10-identity-and-policy-schema.md`) treats identifiers as a flat
   set, which admits both, but the two are used by different subsystems.
2. **ANSWERED by `07`, 2026-09-22 — this no longer blocks Phase 2.** The tier
   ladder resolves it: the per-person policy gap blocks **Tier 1 only**, and
   Tier 0 — every person in a room Nora is in, which is all Phase 2 was asked
   for — needs no new policy at all. Kept for the reasoning. Original text:
   **Phase 2 reads are per-person, not per-room.** Policy today is room-shaped
   (`voice`, `relevance_gate`, `music`). Reading someone's stories is a
   capability against a *person*, and there is no precedent for that in
   `policy.toml`. This needs its own decision before Phase 2, not during it.
