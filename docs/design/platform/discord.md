---
class: design
subject: how Discord joins Nora through a gateway bot owned by the discord bridge
owns:
  - discord bridge: the process that holds the Discord bot session, its command tree and its member, channel and presence reads
area: platform
links:
  - {to: docs/design/platform/README.md, rel: uses}
  - {to: docs/design/platform/liveness.md, rel: uses}
  - {to: docs/design/platform/observations.md, rel: uses}
---

# Discord

## Account and service boundary

The Discord bridge owns one bot session: the gateway connection, the event
intents (message content, being a privileged one), the slash-command tree,
member, channel and presence reads, and sends. A maintained Discord client
library owns the handshake, heartbeat and resume; the bridge does not
reimplement them. The same process hosts the clients for voice and music
control, whose behaviour belongs to the voice bundle; the app never holds a
second Discord session, so replacing the app leaves the connection and any
voice session in place.

Inviting the bot to a server grants technical visibility, not behaviour. Nora
is present only where policy admits her.

## Identity and admission inputs

- A room is a channel, qualified by guild and channel. Policy is deny by
  default across platforms: a channel is ambient only when explicitly admitted,
  however much the bot's role can see.
- A sender is a Discord user id, resolved to a person through
  [identity](../people/identity.md). A Discord account with no
  identity binding is not admitted, in a room or at any login that relies on
  identity.
- A direct message is a room like any other, with the scope rules in
  [boundaries](../cognition/boundaries.md).
- Inside an admitted channel Nora behaves as on every platform: everything is
  recorded, she replies when addressed, and the same disclosure and egress
  rules apply.

## Inbound events and durable capture

Messages, raw edits, deletions and reactions are captured per channel in
order, so an edit cannot outrun its message. A message update that is not an
edit (a link preview resolving, a pin, a thread starting) carries no edit time
and is not captured as one.

A session that is still valid resumes and the platform replays what was
missed. A fully invalidated session replays nothing: the gap since the last
recorded message is a reported gap, recovered from the channel's history where
it can be and otherwise unknown ([liveness](liveness.md)). The bridge's journal
and queue still apply, so a message captured while the app is down waits for
it.

## Outbound effects and receipts

Sends, replies, reactions and edits follow the platform contract. A message id
is the quote and reaction reference. Member, channel, voice-state and presence
reads answer from the gateway's cache and are unavailable when the cache is not
ready; they never report a member as offline because the cache is cold.

## Media and native observations

Attachments are held in custody as on every platform. Polls, pins, threads,
membership and role changes, and reactions enter as typed events
([observations](observations.md)); a thread is a part of its parent room, not a
room of its own.

## Liveness and recovery

Gateway state, heartbeat age and session validity are reported separately. An
invalid session or a lost gateway degrades Discord alone and recovers without
restarting the app ([liveness](liveness.md)).
