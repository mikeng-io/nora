---
type: design
title: "Instagram Transport — Bridge ↔ Adapter Wire Contract"
description: "The JSON shapes on the instagram-bridge HTTP/SSE surface and the id normalization the Nora adapter applies. Four inbound media shapes, two thread id spaces, and the caption inversion against WhatsApp."
tags: [nora, instagram, contract, sse, media, identity]
timestamp: '2026-09-21'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 01-bridge-container.md, rel: details, provenance: asserted}
  - {dst: 02-identity-and-policy.md, rel: implements, provenance: asserted}
  - {dst: ../whatsapp-adoption/03-transport-contract.md, rel: precedent, provenance: asserted}
---

# Instagram Transport — Bridge ↔ Adapter Wire Contract

The bridge (`deploy/instagram-bridge`, Go/instameow) owns the Instagram wire;
the adapter (`nora/platform/instagram/`, Python) owns normalization into
Nora's vocabulary. This document is the only place either side's shapes are
defined; drift is a contract-test failure, not a judgment call.

## HTTP surface (bound on the private `nora` net)

| Route | Purpose |
|---|---|
| `GET /health` | liveness + session state as a FIELD (see `01-bridge-container.md`) |
| `GET /events` | SSE stream of normalized inbound events |
| `POST /send` | text, with optional mentions and reply |
| `POST /send-media` | one attachment; **carries no text** (see below) |
| `POST /react` | emoji reaction on a message |
| `POST /typing` | typing indicator |
| `POST /unsend` | retract a message |

### Room key is the SHORT thread id, sends use the LONG one

Inbound deltas carry `thread_fbid` (short). Every send requires
`thread_igid` (long) — measured as `1009543911792316` and
`340282366841710301281152937372201004392` for the same thread. A send
addressed with the short id does not arrive.

**The bridge owns this translation.** It resolves the pair once via
`FetchThreadID` and caches it; the contract exposes only the short id, so the
adapter never sees the long one and cannot use the wrong one. This is the
same reasoning that puts JID normalization on the WhatsApp adapter side: put
the trap where exactly one piece of code can step in it.

## Inbound event shape

```json
{
  "room_id": "1009543911792316",
  "message_id": "mid.$gAASG4nvQLWim9MCLrWgw3tChgAAq",
  "sender": {"fbid": "110966695387753", "igid": "221731317", "username": "nka_bpumvg"},
  "timestamp_ms": 1789985625166,
  "text": "@not_____nora",
  "mentions": [{"fbid": "14963523149149302", "offset": 0, "length": 13}],
  "reply_to": null,
  "media": null,
  "kind": "message"
}
```

`sender.fbid` is `interop_messaging_user_fbid`, which is the id mentions
carry. `sender.igid` is the public Instagram id, which is what the Phase 2
social calls take. Both are emitted because different subsystems need
different ones — see `02-identity-and-policy.md`.

## Inbound media: four shapes, not one

Measured 2026-09-21. Each content type nests its media differently, so a
single generic attachment path will not work:

| `content_type` | content typename | where the media is | resolvable from the delta? |
|---|---|---|---|
| `IMAGES` | `SlideMessageImageContent` | `attachments[]` with `preview_cdn_url`, `attachment_cdn_url`, `attachment_fbid`, `preview_width/height` | **yes** |
| `VIDEOS` | `SlideMessageVideosContent` | `videos` (not `attachments`) — preview + attachment CDN urls, `attachment_fbid` | **yes** |
| `INSTAGRAM_MESSAGING_ANIMATED_IMAGE` | `SlideMessageAnimatedMediaContent` | `animated_media`, external CDN url | **yes** (sticker / GIF) |
| `RAVEN_IMAGE` (and `RAVEN_VIDEO`) | `SlideMessageRavenImageContent` | `attachment` is **`null`**; only `view_mode` | **no** |

The bridge normalizes the first three into one `media` object:

```json
"media": {"kind": "image|video|animated", "url": "https://...",
          "attachment_fbid": "970288316113989", "width": 720, "height": 960}
```

### One-time media is a policy question, not a parsing problem

`RAVEN_*` messages announce that view-once media exists and carry nothing —
`attachment: null`. Retrieving it requires an explicit fetch which, on
Instagram, is the act of *viewing* it and is visible to the sender.

v1 therefore emits `{"kind": "one_time", "url": null}` and does not fetch.
Nora knows something was sent and cannot see it. Making her open view-once
media is a consent decision about the people in the room and belongs to the
operator, not to a parser default.

## Outbound: text and media are always separate messages

`slidetypes.SendMediaRequest` has fields for attachment, thread, threading id
and reply — and **no text field of any kind**. A caption cannot be expressed
on this wire.

This **inverts** the WhatsApp adapter, where `send.py` puts the reply text in
`caption` on the first attachment and deliberately skips `/send` so the group
does not hear the reply twice. Copying that shape here silently drops the
text. On Instagram:

1. `POST /send` with the text, then
2. `POST /send-media` per attachment, with no text.

`POST /send-media` takes raw bytes; the bridge uploads them via
`HTTPClient.SendMercuryUploadRequest` to obtain an `attachment_fbid` and then
calls `SendMedia`. Measured end to end with a 140-byte PNG.

## Text length

`mautrix-meta` declares `MaxTextLength = 1000`, which is **its own policy**.
Measured on the wire: 20,000 characters accepted, 50,000 refused with
`Message Too Long`. The contract sets no Instagram-specific text cap; Nora's
existing cross-platform limit (Discord's 2,000) remains the binding one.

## Request shapes — pinned 2026-09-22

The first revision of this document named the routes and not their fields,
which the bridge implementation correctly flagged as unpinnable. Pinned here
so the adapter is written against the same contract the bridge already
implements:

```
POST /send        {room_id, text, mentions:[{fbid,offset,length}], reply_to}
POST /send-media  {room_id, data_base64, filename, mimetype, reply_to, voice_note}
POST /react       {room_id, message_id, emoji}
POST /typing      {room_id, typing}
POST /unsend      {room_id, message_id}
```

`room_id` is the SHORT thread id everywhere. `data_base64` follows the
WhatsApp bridge's precedent rather than a raw body, so one JSON shape covers
every route. `/send-media` carries **no caption field** and ignores one if
sent — the wire has nowhere to put it.

## The social route — specified here, NOT YET IMPLEMENTED

`08` routes `(instagram, story)` to the bridge, and `11` scopes social routes
out of M1, so the bridge has **no `/social/*` route today**. The Python client
is written against this shape and currently answers `unavailable`, which is
true. Pinned so the two halves cannot drift:

```
POST /social/story   {"igid": "<numeric>", "limit": <int>}

200  {"status":"ok","items":[{id,kind,taken_at,url}],"expires_at":<unix>}
200  {"status":"nothing_found"}          ← no story up. NOT an error
200  {"status":"unavailable"}            ← could not check
non-200                                  ← the client reads as `unavailable`
```

`igid` is the **public/social id** (`Users[].ID`), not the messaging fbid —
the one place the bridge is handed the social id rather than deriving it.
The bridge re-checks its own read-only allowlist before performing the read
(`08` §the security invariant restated): the request names a verb, and the
verb must be on the list.

**The bridge binds `18082`** (`whatsapp-bridge` is 18081). Both sides chose it
independently; recorded so it stays a decision.

## Inbound event kinds

`kind: "message"` is the one this document originally defined. The bridge
emits five more, and they are part of the contract:

| kind | why it exists |
|---|---|
| `heartbeat` | the real socket-death detector — `last_event_age_seconds` is meaningless without it, since a quiet room and a dead socket look identical |
| `dropped` | the offline buffer overflowed; the count is the honest signal that history was lost |
| `reaction` / `edit` / `unsend` | the corresponding inbound deltas |
| `typing` | carries `thread_id`; `room_id` is filled **only** when the id cache can vouch for it, because guessing would put a long id in a field defined as short and every consumer would miss silently |

Every event also carries `from_me`, without which the adapter can only infer
its own echo from a config value.

## `/health` carries identity

`/health` returns the four documented fields plus:

```json
"self": {"fbid": "...", "igid": "...", "username": "..."},
"dropped_events": 0, "buffered_events": 0, "clients": 1, "uptime_seconds": 4210
```

`self` is there because the contract had no `/self` route (WhatsApp has one),
and the alternative — the adapter carrying `GetOwnFBID()` in config — is a
value whose drift has **no symptom**: R-12 would simply stop recognising
Nora's own mentions, silently, which is the exact bug class
`02-identity-and-policy.md` exists to prevent.

## Status mapping

| Bridge response | Meaning |
|---|---|
| 200 | delivered; body carries `{ok, id, timestamp_ms}` |
| 409 | session is checkpointed — **not** retryable without human action |
| 503 | socket down or reconnecting — retryable |
| 413 | payload over an Instagram limit |
| 400 | malformed request — including a `room_id` that is actually the LONG thread id, which is all digits and would pass a naive numeric check |
| 500 | unclassifiable refusal. **The fail-closed default**: 503 is a claim that retrying will work, and an unknown failure has not earned it |

`timestamp_ms` is the platform's own sent-timestamp, which is what a later
reply's quote resolves against — the same lesson `signal.send._sent_timestamp`
records.
