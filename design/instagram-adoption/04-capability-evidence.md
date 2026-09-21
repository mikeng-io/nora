---
type: design
title: "Instagram Capability Matrix — Measured, With Evidence"
description: "What the Instagram wire actually does, established by running probes against a live account rather than by reading a capability table. Includes the two places the declared capabilities were wrong."
tags: [nora, instagram, capabilities, evidence, measurement]
timestamp: '2026-09-21'
edges:
  - {dst: 00-overview.md, rel: parent, provenance: asserted}
  - {dst: 02-identity-and-policy.md, rel: details, provenance: asserted}
  - {dst: 06-retention-and-custody.md, rel: constrains, provenance: asserted}
---

# Instagram Capability Matrix — Measured

Every row here was produced by a probe run against the live account
`@not_____nora` in the group "Ridgeline" on **2026-09-21**, except where marked
UNMEASURED. Rows marked *declared* come from `mautrix-meta`'s
`pkg/igconnector/capabilities.go` and are the bridge's own policy, not the
protocol's behaviour — two of them turned out not to match the wire.

## Outbound

| Capability | Result | Evidence |
|---|---|---|
| send text | PASS | `SendMessage` → `mid.$gAASG4nvQLWim9MCLrWgw3tChgAAq` |
| send with mention | PASS | mention structure round-tripped in the echo delta |
| send reply | PASS | `replied_to_message` present in the echo |
| send reaction | **FAIL with the LONG id** | measured 2026-09-22: accepted, then **absent from the thread on read-back**. With the SHORT id the reaction is present. "Accepted" was not "arrived" |
| edit message | PASS | `EditMessage` accepted |
| unsend message | PASS | 5 of 5 probe messages removed |
| typing indicator | **UNVERIFIED** | `SetTyping` accepted; arrival never read back, and upstream addresses it with the SHORT id |
| upload image bytes | PASS | 140-byte PNG → attachment fbid `2280432885466163` |
| send image to group | PASS | `SendMedia` with that attachment fbid |
| caption on media | **impossible** | `SendMediaRequest` has **no text field at all** |

## Inbound

| Capability | Result | Evidence |
|---|---|---|
| live group message | PASS | `SlideUQPPNewMessage` over the realtime socket |
| mention as structure | PASS | `mentions:[{user_fbid, offset, length}]` — not plaintext |
| reply as structure | PASS | `replied_to_message` populated on a live reply |
| image from another member | PASS | `ct=IMAGES` → `attachments[]` with `attachment_cdn_url`, `attachment_fbid`, 720x960 |
| video from another member | PASS | `ct=VIDEOS` → `videos` key, preview + attachment CDN urls |
| sticker / animated | PASS | `ct=INSTAGRAM_MESSAGING_ANIMATED_IMAGE` → `animated_media`, external CDN url |
| one-time (view-once) image | **PARTIAL** | `ct=RAVEN_IMAGE` arrives, but `attachment` is **`null`** — see below |
| emoji reaction from another member | UNMEASURED | probe armed; none sent during the window |
| voice note from another member | UNMEASURED (structurally supported) | `SlideMessageAudiosContent` → `AudioAttachment{AttachmentCDNURL, PlayableDurationMS, WaveformData}` |
| one-time media, `view_mode` 0 and 1 | **no bytes served** | `content.attachment` is `null` in the live delta AND in thread history |
| one-time media, `view_mode` 2 ("keep in chat") | PASS | attachment present with a CDN url, behaves like a normal image |
| system/member events | PASS | `GROUP_THREAD_CREATED_XMAT`, `ADD_PARTICIPANT_XMAT`, `CHANGE_THREAD_IMAGE_XMAT` arrive as message types |
| typing notification | PASS (type exists) | `slidetypes.TypingNotification` |

### Inbound media is four shapes, not one

Each content type nests its media differently — `attachments[]` for images,
`videos` for video, `animated_media` for stickers — so a single generic
attachment path does not work. The full mapping is in
`03-transport-contract.md`.

### One-time media announces itself and carries nothing

`SlideUQPPNewRavenMessage` / `RAVEN_IMAGE` arrives with `view_mode: 1` and
`"attachment": null`. Nora learns that view-once media was sent and cannot
see it. Resolving it requires an explicit fetch which, on Instagram, *is* the
act of viewing and is visible to the sender — a consent decision, not a
parser default. v1 surfaces `{"kind": "one_time", "url": null}`.

The remaining UNMEASURED row is a Phase 1 requirement. It is listed as
unmeasured rather than inferred from `capabilities.go`, because the two rows
below show what inference costs.

### The thread id is MIXED per verb, not uniform — measured 2026-09-22

`03-transport-contract.md` said every send needs the LONG `thread_igid`. That
is wrong for at least one verb, and the failure is silent.

| verb | LONG | SHORT | how established |
|---|---|---|---|
| `SendMessage` | **works** | (upstream uses it) | echo delta + thread history |
| `SendMedia` | **works** | — | image appeared in the thread |
| `UnsendMessage` | **works** | — | every probe cleanup succeeded |
| **`SendReaction`** | **accepted, NEVER ARRIVES** | **works** | one carrier message, 👍 with long then 🔥 with short; only 🔥 present on read-back |
| `SetTyping` | unverified | (upstream uses it) | never read back |

`mautrix`'s own connector passes the SHORT id (`meta.IGID`) for `SendMessage`,
`SendMedia`, reactions and typing, and the LONG one only for unsend. The
measurement says send and media tolerate either while **reactions do not**.

**The lesson is the method, not the table.** This row read PASS for a day
because the probe checked the API's answer instead of the thread. An
acknowledgement is a claim about a request; only a read-back is a claim about
the world. Every other outbound row above was verified by echo or history —
this one was not, and it was the one that was wrong.

## Where the declared capabilities were wrong

### 1. The 1000-character ceiling is not real

`capabilities.go` declares `const MaxTextLength = 1000`. Measured:

| length | result |
|---|---|
| 1,000 | accepted |
| 1,001 | **accepted** |
| 2,000 / 5,000 / 10,000 / 20,000 | accepted |
| 50,000 | refused — `Message Too Long` |

So 1000 is mautrix's own conservative policy. The real wall is somewhere
between 20,001 and 50,000 and was not bisected, because nothing in Nora needs
to know it precisely — what matters is that **Instagram is not the narrow
surface the table implies**. Discord's 2000 remains the binding constraint
across platforms; Instagram does not add a new one.

Do **not** size Nora's reply budget on 1000. That would be deriving a budget
from a policy constant, which is how a cap becomes unraisable.

### 2. Captions are not "dropped by policy" — they cannot be expressed

The table records `Caption: event.CapLevelDropped` for every media type,
which reads like a choice. It is not: `slidetypes.SendMediaRequest` has
fields for attachment, thread, threading id and reply — and no text field
anywhere. Text with media is structurally two messages.

This **inverts** the WhatsApp adapter's pattern. `nora/platform/whatsapp/send.py`
deliberately puts the reply text in `caption` on the first attachment and
skips `/send`, so the group does not hear the reply twice. Copying that shape
to Instagram silently loses the text. The Instagram sender must send the text
as its own message, then the media.

## Phase 2 surface (reconnaissance)

All on the **same authenticated session**, no second credential:

| Call | Result | Returns |
|---|---|---|
| `GetProfile(igid)` | PASS, thin | 14 fields, messaging-oriented; **no** follower/following/media counts or biography |
| `FetchReel([igid])` | PASS | full `user_reel`: per-item media type, playable CDN url, `taken_at`; per-reel `expiring_at`, `media_ids`, `seen` |
| story read marks it viewed? | **NO** | `seen` unchanged across two reads 20s apart, with a newer item outside the watermark both times |
| member → igid join | PASS | `GetThread` → `Users[].ID` is the igid `FetchReel`/`GetProfile` take |
| multi-sender attribution (3-person room) | PASS | each message's `sender_fbid` matches that member's roster `interop_messaging_user_fbid` |
| member added to the room | PASS | `ADD_PARTICIPANT_XMAT`, attributed to the member who did the adding |
| message from a member other than the operator | PASS | `@gemini_.xoxo.04` text and image, both correctly attributed |
| one member `@`-mentioning ANOTHER (not Nora) | PASS | `"@nka_bpumvg GH"` from a third party — `mentions[].user_fbid=110966695387753`, not Nora's |
| Nora distinguishes "tagged me" from "tagged someone else" | PASS | same room, same sender: one mention carries Nora's `GetOwnFBID()`, the other does not |
| `SearchUsers(query)` | PASS | 2,941 bytes of results |
| `FetchMedia(mediaID, shortcode)` | **FAIL** on a story media id | `HTTP 400` from `/api/v1/media/<id>/info/`; untested on a real feed post |

Phase 2 is therefore substantially cheaper than Phase 1 in new machinery: the
transport already carries it. What Phase 2 actually needs is **policy**, not
protocol — see `02-identity-and-policy.md` §Open decisions.

## Not measured, and load-bearing

- **Session longevity.** The whole question of whether this is operable. A
  soak has been running on norty since 2026-09-21 (`~/workspace/ig-soak/`).
  Until it produces a number, every fresh probe misleads by succeeding,
  because age is the variable.
- **Pagination is broken in this library version, both kinds.** Explicit
  `PaginateMailbox` folder queries (`INBOX`/`PENDING`/`SPAM`) and
  `PaginateMessages` both fail with `Query Error - Error performing query`.
  `GetThread` returns recent history fine, so this is a gap in two specific
  GraphQL documents rather than a permissions problem — but it means the
  message-requests folder, where a group invite from a non-follower lands,
  cannot currently be read at all. A group invite from a non-follower lands in PENDING, so this
  path matters and is currently unvalidated. `AcceptMessageRequest` exists.
- **Voice messages, stickers, reels shared into a thread.** `xma.go` handles
  external media attachments inbound; none was exercised.
- **Behaviour under a second concurrent session.** The probes ran while a
  browser session for the same account existed; no conflict was observed, but
  nothing was done to provoke one.
