---
type: design
title: "Sequencing and Flows — Build Order, and What Happens On The Wire"
description: "Four milestones with their dependencies, and sequence diagrams for the flows that matter: inbound room message, outbound media, a story read end to end, and what a checkpoint does. Plus what is deliberately out of each milestone."
tags: [nora, instagram, sequencing, flows, milestones, implementation]
timestamp: '2026-09-22'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 03-transport-contract.md, rel: implements, provenance: asserted}
  - {dst: 10-discovery-retrieval-and-paging.md, rel: implements, provenance: asserted}
---

# Sequencing and Flows

## Build order

```
  M0  registry degradation         ── blocks nothing, blocked by M1's config shape
      │                               (lands WITH M2, not before — see 08/01)
      ▼
  M1  instagram-bridge (Go)        ── the bulk. Independent of everything in nora/.
      │                               Provable standalone; needs no Python.
      ▼
  M2  platform adapter (Python)    ── mirrors nora/platform/whatsapp/ file for file
      │                               + registry 4th block + degradation
      │                               + [platforms.instagram] config
      │                               + `instagram` identity kind (fbid, igid)
      ▼
  M3  Phase 1 live in one room     ── ambient ingest, tag detection, reply, media
      │                               THE MILESTONE THAT MATTERS
      ▼
  M4  social v1 (story, Tier 0)    ── one vocabulary row + router + client
                                      + /social/story on the bridge
```

**M1 and M2 can be built in parallel** — they meet at the transport contract
(`03`), which is already written and is the reason they can. M0 is listed
first because it is a prerequisite *concept*, but it lands inside M2, since a
degradation path with no adapter to degrade is machinery with no user.

**M4 must not start before M3 is live.** A social read is occasioned by a
conversation; without a working room there is no occasion, and the tier check
has nothing to check against.

## Flow 1 — inbound room message (M3)

```
someone in the IG group
        │  types "@nora what's for dinner"
        ▼
Instagram servers
        │  MQTT push
        ▼
instagram-bridge ──── Delta{SlideUQPPNewMessage, thread_fbid, mentions[]}
        │             resolve media URLs (4 shapes), normalize
        │  SSE /events
        ▼
nora/platform/instagram/listener.py
        │  parser.py: sender_fbid + igid -> Identifier
        ▼
persons.py ─────────► person `miles`         (unmapped -> `pending`, still recorded)
        │
        ▼
R-12 admission: is mentions[].user_fbid == GetOwnFBID()?
        │  yes
        ▼
engagement pipeline ──► agent turn ──► send_reply
        │
        ▼
InstagramAdapter.send ── POST /send {room_id: <SHORT id>, text, reply_to}
        │
        ▼
instagram-bridge ── FetchThreadID cache: short -> LONG ── SendMessage
```

The **short→long id translation lives in the bridge**, so the adapter can
never use the wrong one. That is the whole reason it is there.

## Flow 2 — Nora sends text plus a picture (M3)

```
agent ── send_reply(text, attachments=[png])
        │
        ▼
InstagramAdapter.send
        │
        ├─ 1. POST /send      {room_id, text}          ← text FIRST, alone
        │
        └─ 2. POST /send-media {room_id, bytes, mime}  ← no caption field exists
                    │
                    ▼
            bridge: SendMercuryUploadRequest(bytes) -> attachment_fbid
                    SendMedia{AttachmentFBID, ThreadID: LONG, otid}
```

Two calls, never one. **This inverts the WhatsApp adapter**, which puts the
text in the first attachment's `caption` and deliberately skips `/send` so the
group does not hear the reply twice. On Instagram a caption cannot be
expressed at all, so copying that shape silently loses the text.

## Flow 3 — "Nora, read my latest story" (M4)

```
Miles, on SIGNAL: "check my latest IG story"
        │
        ▼
agent turn ── social_fetch(platform="instagram", what="story", target="miles")
        │                                                      └── a PERSON id,
        │                                                          never a handle
        ▼
tool: screen target for identifiers (egress), validate (platform, what)
        │
        ▼
SocialRouter ── resolve: persons.miles.instagram.igid = 221731317
        │              (no entry -> `ambiguous`, never a guess)
        │       ── tier:    known person in a room with Nora -> Tier 0 -> allowed
        │       ── route:   (instagram, story) is THE exception row
        ▼
InstagramSocialClient
        │
        ▼
instagram-bridge /social/story ── re-checks its OWN allowlist
        │
        ├─ discover: GetProfile(igid).latest_reel_media   ← cheap, no media
        │            nothing there -> {"status":"nothing_found"} and STOP
        │
        └─ retrieve: FetchReel([igid]) -> items, newest first, clamp to limit=1
        │
        ▼
{"status":"ok", items:[{id, kind:"video", taken_at, url}], expires_at}
        │                                          └── a REFERENCE, not bytes
        ▼
agent ── media_fetch(url=…)   ← 200 unauthenticated (measured); the signature
        │                        IS the authorization
        ▼
Nora answers in the SIGNAL room
        │
        ▼
cursor for (miles, story) advances.  Nothing else persists.
```

Note the platform the question was asked on is irrelevant: the person index is
the cross-platform join.

## Flow 4 — a checkpoint, which is a normal Tuesday

```
Instagram ── AuthError / socket rejects the session
        │
        ▼
instagram-bridge ── keeps serving HTTP. /health stays 200:
        │            {"ok":true, "session":"checkpointed", "connected":false}
        │            ^^^^ a FIELD, never a status code
        ▼
InstagramAdapter ── sends answer 409 (not retryable without a human)
        │            events stop; no exception into the loop
        ▼
Nora ── keeps running. Signal and Discord unaffected.
        │  ^^^^ this is what M0 buys, and what today's fail-fast would break
        ▼
operator ── re-auths in a browser, drops fresh cookies in the session dir
        │
        ▼
bridge ── resume from the persisted state AND the persisted seq
         (LoadState alone does NOT restore the seq — see 01)
```

Under today's `AdapterRegistry`, a checkpoint at **boot** raises
`AdapterRegistryError` → `StartupError` and **Nora does not start at all**,
taking Signal and Discord with it. That is the single behaviour M0 changes.

## What is deliberately OUT of each milestone

| Milestone | Out of scope | Why |
|---|---|---|
| M1 | social routes | no consumer until M4 |
| M2 | DM (1:1) support | group-only, deny-by-default, mirroring WhatsApp v1 |
| M3 | opening one-time media | restriction R1, parked |
| M3 | reading message requests (`PENDING`) | pagination is broken upstream (R2) |
| M4 | `cursor` on the port | stories do not paginate; posts are not in v1 |
| M4 | Tier 1 and above | `policy.toml` has no per-person capability |
| M4 | `target` as a free subject | v1 takes a `person_id`, which the index already resolves |
| M4 | the `media_fetch` retention conflict | shared tool; not this bundle's decision |

## The one thing that gates nothing

Session longevity. It determines **re-auth cadence**, which is a runbook
number, not an architectural one — the design already absorbs session loss at
every layer (pairing as a field, degradation, and a resume that persists the seq
separately from the state — `01`). An earlier
revision of this bundle said no bridge should be written until the soak
produced a number; that was over-cautious and is withdrawn. The soak keeps
running and its number tunes operations.

Measured so far: 7h continuous, 45 deltas, zero disconnects, zero auth errors,
alongside a concurrent browser session for the same account.
