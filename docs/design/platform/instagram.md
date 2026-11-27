---
class: design
subject: how Instagram group messaging joins Nora through the instagram bridge
owns:
  - instagram bridge: the only door to Instagram, holding its one session for both messaging and social reads
  - instagram id spaces: messaging people, social accounts, usernames and thread references, which are distinct types and never inferred from one another
area: platform
links:
  - {to: docs/design/platform/README.md, rel: uses}
  - {to: docs/design/platform/liveness.md, rel: uses}
  - {to: docs/design/platform/observations.md, rel: uses}
---

# Instagram messaging

## Account and service boundary

The Instagram bridge holds one authenticated session and its realtime
connection. Messaging (threads, messages, reactions, members) and social
(accounts, stories, posts, comments; see the [social bundle](../social/instagram.md))
share the session and the upstream gateway, not their interfaces. Every
Instagram capability crosses the bridge: there is no second client, credential
or fallback integration whose failure could pass for a different answer. The
messaging adapter cannot fetch a profile and the social reader cannot send a
room message.

Inside the bridge, domain values and errors, use cases, the gateway and the
transport are separate; raw upstream responses never become its contract, and
an upstream error inside a successful transport response is still a failure.

## Identity and admission inputs

- Messaging identity and social identity are different id spaces, and thread
  references are a third. Typed identifiers keep a decimal string from
  crossing between them; the person index maps them.
- The bridge resolves a thread to one canonical room key. The roster supplies
  the messaging identity for mentions and the social identity for the person
  index; an incomplete roster is unavailable, never smaller, because
  disclosure checks depend on knowing every account.
- Rooms and senders are admitted by policy and identity as on every platform.

## Inbound events and durable capture

Realtime events become messages, reactions, edits, unsends, summons and
liveness signals. Messages, edits, reactions and unsends keep distinct kinds
([conversation](../conversation/README.md)); an edit event carries no sender or
mentions, so the original's are used and an unknown mention set is stated.
Heartbeats and explicit loss tell a quiet socket from a dead one, and dropped
history from a complete transcript. A summons card is a durable event kind
routed to the public summons occasion, with no room.

## Outbound effects and receipts

Outbound text and each attachment are separate messages: this transport has no
caption. Replies, reactions, typing and unsend use their own resource
semantics; delivery times and identifiers stay in the delivery record, not in
model-facing text.

## Media and native observations

Text, image, video, animated media and audio normalise into the common media
model. View-once content that supplies no retrievable bytes is recorded as an
announcement with author and time; it is never opened, because opening would
change state the sender chose.

## Liveness and recovery

Session state is durable enough to resume without replaying an unreliable
history. A checkpoint or logout needs a person; a lost connection is retried;
malformed input, an unsupported capability and an upstream rejection are
distinct outcomes, and an unknown failure is never classed as retryable. A
degraded session affects messaging and social reads together without changing
what either means: a failed profile is not an empty one, and an unreachable
bridge is not evidence of absence. Health reports session, stream, identity and
buffer loss, and a checkpoint is never a healthy empty room
([liveness](liveness.md)).
