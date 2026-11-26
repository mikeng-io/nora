---
class: design
subject: how Signal joins Nora through a wrapped signal-cli account
owns:
  - signal bridge: the wrapper that supervises the unmodified signal-cli and owns capture, queueing and the platform contract for Signal
area: platform
links:
  - {to: docs/design/platform/README.md, rel: uses}
  - {to: docs/design/platform/liveness.md, rel: uses}
---

# Signal

## Account and service boundary

The Signal bridge supervises the unmodified `signal-cli` executable and adds
capture, the queue and the common platform contract around it. The executable's
own account and device store stays where it is and is private to the bridge;
nothing else holds Signal keys. The account is never relinked, re-registered,
unlinked or moved by Nora. The executable's raw control interface is reachable
only inside the bridge.

Receiving is started in the mode that delivers what arrived while it was down,
and only after the bridge's journal has proved it can append and sync. Without
that mode every message is lost.

## Identity and admission inputs

- A sender arrives as a Signal account identifier and resolves to a person only
  through the identity mapping; a display name never does. An unresolved sender
  matches nobody.
- Rooms are Signal groups, admitted by policy; a direct conversation is admitted
  only for an allowed sender. The admission projection carries the enabled
  groups, admitted direct senders and Nora's own aliases.
- Signal addresses a message by its author and its sent time. That pair is
  the identity of a message, a quote target and a reaction target.

## Inbound events and durable capture

The bridge subscribes to the executable's event stream continuously and
journals each admitted event before anything else
([platform](README.md)). If capture breaks, the bridge stops the executable and
exits rather than leave a healthy-looking daemon draining messages it cannot
keep. A failure of the queue alone may continue capture while the journal holds
every event.

Signal offers no transactional hand-off, so a message delivered to the
executable before the journal records it is outside the guarantee.

- An **edit** names its target by sent time. A later edit names the previous
  revision's sent time, and a reply to an edited message quotes the revision's
  time, so the bridge and the conversation record resolve revision times back
  to the original message ([conversation](../conversation/README.md)).
- A **reaction** names its target, its reactor, the emoji and whether it is a
  removal. Signal gives a reaction no revision, so the bridge keeps the last
  observed state per target and reactor; add, remove, add is three events, and a
  replay with no distinguishing identity is retained as ambiguous, never
  guessed.
- In a message body each mention is one placeholder character; the structural
  mention list is what identifies Nora.

## Outbound effects and receipts

Text, quoted replies, reactions and attachments go out through the platform
contract. A quote names the author and sent time of its target. A confirmed
send returns the sent time that identifies the message later; an unconfirmed one
returns none and is never resent. A reaction is also an act with its own
identity.

## Media and native observations

Attachments are held in bridge custody until the app has copied them
([platform](README.md)). Reactions, edits and deletions enter as typed events
([observations](observations.md)); Signal reports no poll or pin objects.

## Liveness and recovery

A Signal account that is logged out or whose daemon is unreachable degrades
Signal alone ([liveness](liveness.md)). Process health, daemon connection,
capture health and queue depth are separate readings.
