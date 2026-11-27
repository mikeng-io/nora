---
class: design
subject: music as a deterministic per-guild player, a control surface of voice
owns: [music session, track source, autoplay]
area: voice
links:
  - {to: docs/design/voice/README.md, rel: uses}
  - {to: docs/design/cognition/acts.md, rel: uses}
---

# Music

Music is a **per-guild state machine**, not an agent deciding what plays. Slash
commands and Nora's Discord music tools call the same manager. The agent is another
control surface; it never owns a parallel queue, connection or playback decision. A
play queue is an effect under the [act envelope](../cognition/acts.md), and play and
listening are observed ([observation](../cognition/observation.md)).

## Control

- A session starts only for an admitted requester currently in a voice channel, so the
  destination comes from that person, never a guessed channel. Once connected,
  ordinary controls work without every controller being in it.
- Tool calls resolve the actual Discord account behind the person, including several
  mapped accounts, and exist only on Discord.
- Every transition that can change a guild's player runs under that guild's lock:
  natural completion, skip, stop, stream failure, connection death, idle expiry, an
  empty channel and restart reconciliation. They cannot interleave into a double
  advance or a resurrected session.
- Ending music clears the queue and playback and leaves voice connected. Idle expiry
  applies to an inactive session. None of these needs a model call.

## Sources and streams

- A **track source** resolves a query or link into typed track locators and, only when
  the source represents a playlist, a bounded flat listing. A single video link never
  expands because of incidental playlist parameters.
- Locators are durable; playable stream addresses are not. A stream is resolved
  lazily just before use and never stored as though durable. The controller pulls and
  decodes it; ordinary playback writes no audio library or object.
- A stream failure may invalidate the source session and re-resolve the same track
  within a bounded budget before advancing. Failing to resolve differs from failing to
  play, and a stalled or superseded stream is never a natural completion.
- A second source joins through the same port and its shared contract. Widening the
  allowed hosts is a policy decision, never inherited from an extractor's breadth.

## Queue and continuity

The upcoming queue and the session snapshot are durable per guild: requested
locators, the current track, pause and volume, notification destinations and the seed
autoplay needs. The live controller stays authoritative for its connection and
playback, so a restart adopts live state where it can and never starts a second
player. Exact audio position is not a durable promise; recovery restarts or advances
from the persisted locator.

## Autoplay

With autoplay on, an exhausted queue may add one related pick seeded from the last
track. It uses the same queue and playback path and is announced as autoplay. A track
already played this session is excluded; a requested repeat is allowed. No candidate,
an all-repeat set or a failed lookup falls back to idle without retry or invented
substitute. A future pick may be prepared while the current track plays and is
discarded if a skip or stop makes it stale; it is checked again at the boundary.

## Announcements

An announcement is a deterministic notification, not a model-composed delivery: it
goes through the delivery guards to its resolved destination with no agent turn. One
message table owns the wording; one send-time resolver owns the destination.

- A session start or enqueue is announced in the caller's own reply. Natural
  advancement, autoplay, next-after-skip and recovery use the notify path, so an agent
  turn cannot duplicate one.
- A guild may name an output-only music channel; otherwise the session's starting text
  channel is used. The output channel is not a room and gains no intake, membership
  or trust meaning.
- Routing is re-read from live policy at each announcement. The channel must belong to
  the same guild and be writable. A definite refusal may fall back to the session
  channel; an uncertain send is not retried into a possible duplicate. A notification
  failure never reaches playback, a command or an event acknowledgement.
