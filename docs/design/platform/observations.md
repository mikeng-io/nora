---
class: design
subject: native platform events beyond speech, captured as typed observation items with explicit coverage
owns:
  - native observation: a platform event that is not an utterance, such as a reaction, poll vote, pin, membership change or shared artifact
  - coverage: the statement of what a platform, account mode and field could and could not report for an observation
area: platform
links:
  - {to: docs/design/platform/README.md, rel: uses}
  - {to: docs/design/cognition/observation.md, rel: uses}
  - {to: docs/design/cognition/retrieval.md, rel: uses}
---

# Native observations

Platforms report more than speech: reactions, votes, shared artifacts, and
changes to rooms, membership and pins. A bridge captures each supported event as
a typed [observation item](../cognition/observation.md) beside ordinary
messages, with explicit coverage. Nora never infers social meaning from a raw
payload, and a service event never becomes a fabricated utterance.

The graph owns what happens next: items, the current heads folded from them
and the stimuli they raise are defined in
[observation](../cognition/observation.md). This file owns what a platform
event must carry to be folded honestly.

## What an observation carries

- Its platform, account and conversation scope, the object it concerns, the
  target it affects and the operation reported.
- Source time and receipt time, kept apart, each with its quality. A source
  revision orders evidence only within the stream that supplied it; arrival
  order is a fallback, never invented global causality.
- Object identity that survives revision, and a transition identity that tells
  add, remove, add from a replay.
- Actor, subject, target and room as four distinct things: a voter, a departing
  member or a pinned message is not necessarily who caused the change. An
  actor is known, anonymous, not supplied or not applicable.
- Native identifiers stay private evidence; the model sees scoped opaque
  handles and safe labels.

An utterance delivered beside an observation has two references: the message
that carried the fact and the target the fact affects. A pin notice is not the
pinned message.

## Object semantics

| object | what is kept, and what is never inferred |
|---|---|
| message metadata | reply, forward, mention, expiry and view-once facts as constraints; never a licence to retain media |
| reaction | actor deltas, covered clears, observed counts; an unknown actor stays unattributed; a reaction is not agreement |
| poll | definition, stable options, each voter's selection set, aggregate snapshots, open or closed; a complete empty selection withdraws a vote, an omitted one is unknown; counts prove no unique people and no abstention |
| room and thread | only the reported fields change; an explicit source null clears; a thread stays inside its parent room |
| membership and role | subject, native state and the scope a snapshot covers; a partial snapshot removes nobody; pending, departed and banned are not members |
| pin | a covered pin set changed by deltas or snapshots |
| shared artifact | source metadata linked to custody and perception; a preview is not the content; each responder's answer is separate, and an unreadable response is no withdrawal or attendance claim |

Typing is never evidence: it is not persisted, shown or allowed to wake a
pass. Presence, read receipts and passive social collection do not widen
because this contract exists.

## Coverage

Coverage differs by platform, account mode, permission, room mode, source
version and field. **Unsupported, unimplemented, disabled, unavailable and not
inspected are different states.** A source capability is not capture, a stored
payload is not a projection, and a projection is not what the model sees. An
unreadable media item, an undecryptable vote or a missing creation secret is a
coverage gap, never an empty message, a zero tally or a removed member.

## Folding

Capture, the item append and the cheap current-head update commit together;
costly perception and model work are deferred obligations with a durable
owner. One deterministic fold serves incremental updates, current reads, as-of
reads and rebuilds, as the graph defines for current heads.

- A delta applies once. A complete snapshot replaces only what it covers; a
  partial one merges without erasing unknowns; a stale snapshot never overlays
  a newer delta.
- Evidence that cannot be ordered leaves an ordering gap on the head.
- A response that arrives before its definition stays unresolved and links
  later, idempotently.
- Replay never duplicates an observation or a count.

## Reads

Context and read tools use the same scoped projection. A result names the
object kind and a safe handle, its current or as-of state, observation time,
revision, coverage and evidence handles; a collection is bounded and reports
what it omits.

- An unavailable read is unknown, never absence. Partial describes readable
  evidence with its limits, and an unknown field is never zero, false or
  empty.
- Handles resolve inside the reader's own room and unit on every read;
  holding one authorises nothing else, and a current-room query never claims
  a unit-wide answer.
- A current annotation on an old message carries its own later time, so it
  cannot read as knowledge available when the message was sent.
- An observation reaches a pass through the declared context blocks, never
  through a hidden query. Recency can admit new evidence; it is not
  relevance, and an old open poll is not pinned into every prompt.
- Closing a poll changes source state only; it never settles the matter it
  was about.
- A source observation missing from a pass is investigated at capture,
  projection, selection and rendering, not explained away as irrelevant.

A new platform joins through mappings and generated native fixtures, never a
new cognition workflow.
