---
type: design
title: "Discord Listener — Gateway, Intents, and Reconnect Resilience"
description: "How DiscordAdapter receives messages: Gateway WebSocket via discord.py, required intents, RESUME semantics, and REST-history backfill in place of a separate collector process."
tags: [nora, discord, gateway, resilience, platform-adapter]
timestamp: '2026-08-14'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# Discord Listener — Gateway, Intents, and Reconnect Resilience

## Transport: Gateway WebSocket, not SSE

Discord has no SSE-equivalent API. Real-time delivery is the **Gateway API**,
a persistent WebSocket:

1. `GET /gateway/bot` (REST) returns a WebSocket URL.
2. Connect; receive `HELLO` (heartbeat interval).
3. Send `IDENTIFY` (bot token + **intents** — a bitmask of event categories,
   including the **privileged `MESSAGE_CONTENT` intent**, which must be
   toggled on for the bot application in the Discord Developer Portal before
   message text is visible at all).
4. Receive `READY` (`session_id`); from then on, sequenced dispatch events
   (`MESSAGE_CREATE`, etc.) arrive over the socket.
5. Client must send `HEARTBEAT` on the given interval or Discord closes the
   connection — unlike SSE, this is bidirectional.

## Library: `discord.py`

Recommended over a hand-rolled Gateway client. It owns the handshake,
heartbeat loop, and reconnect/RESUME logic, the same way the existing SSE
client already owns Signal's reconnect-with-backoff — `DiscordAdapter`
should be a thin wrapper registering a message handler, not a reimplementation
of Gateway plumbing. Matches the existing preference for reusing maintained
infrastructure over building it from scratch.

## Reconnect and gap-filling

Two tiers, both better than what Signal's SSE stream is known to offer:

- **Short gap (session still valid):** send `RESUME` (token + `session_id` +
  last-seen `seq`). Discord replays every dispatch event missed since that
  sequence number natively — no application-level backfill needed.
- **Long gap / invalidated session:** a full re-`IDENTIFY` is required, which
  does **not** replay anything. Backfill then means calling
  `GET /channels/{id}/messages` (REST) since the last message id Nora has
  recorded, per allowlisted channel, before resuming live processing.

  **Re-scoped 2026-08-14 (operator): this is a PORT concern, not a Discord
  one.** Signal has the same unanswered question (does signal-cli's SSE
  replay what buffered while Nora was away?), so the mechanism is designed
  once against `PlatformAdapter` rather than twice per adapter — see
  `00-overview.md`'s "Backlogged: gap-filling is a PORT concern". What is
  written above stays true as Discord's ANSWER to that port question; it is
  no longer a Discord-local to-do.

## No separate collector process

Signal-cli exists as a standalone daemon specifically because Signal's
delivery model is push-only with no server-side history endpoint — once a
message is delivered and not captured, it may be gone. Discord is different:
its servers retain channel history regardless of whether the bot is
currently connected. The REST backfill above **is** Discord's equivalent of
signal-cli's durability guarantee — it does not require standing up a new
always-on process the way Signal's transport does.

## Observability

`DiscordAdapter` should surface Gateway connection state through the same
`/health` shape `SignalAdapter` uses for `sse.connected` /
`sse.last_event_age_seconds` — R-5's silent-failure posture (a wiring gap
must be loud, never mistaken for "nothing happened") applies identically
here.
