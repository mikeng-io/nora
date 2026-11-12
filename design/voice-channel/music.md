# Music

## Deterministic control

Music is a per-guild state machine, not an agent deciding what should play.
Slash commands and Nora's Discord-scoped music tools call the same manager and
player methods. The agent is only another control surface; it never owns a
parallel queue, voice connection or playback decision.

Starting a session requires an admitted requester currently in a voice channel,
so the destination comes from that person rather than a guessed configured
channel. Once connected, ordinary controls operate on the existing session
without requiring every controller to be in it. Tool calls resolve the actual
Discord account behind the person, including multiple mapped accounts, and are
absent outside Discord.

Every transition that can change a guild's player runs under that guild's lock.
Natural completion, skip, stop, stream failure, connection death, idle expiry,
channel-empty observation and restart reconciliation cannot interleave into a
double advance or a resurrected session.

## Sources and streams

A `TrackSource` resolves a query or URL into typed track locators and, when the
source explicitly represents a playlist, a bounded flat listing. A single video
link never expands merely because it carries incidental playlist parameters.
Asking for a playlist requires a source URL that actually is one, not a video
whose title contains the word.

Playlist entries and related recommendations retain re-derivable locators.
Their playable stream addresses are resolved lazily immediately before use,
not all up front and not persisted as though they were durable. The controller
pulls and decodes the stream; ordinary playback writes no audio library,
artifact row or object store.

Source and extractor sessions can expire independently of the track locator. A
stream failure may invalidate the source session and re-resolve the same track
within its bounded budget before advancing. Failure to resolve is different
from failure to play, and a stalled or superseded stream never counts as a
natural completion.

A second source implementation joins through the same port and shared contract
suite. Widening the source host allowlist is a policy decision, not something
inherited accidentally from the extractor's full website support.

## Queue and session continuity

The upcoming queue and the music-session snapshot are durable per-guild state.
They retain requested locators, current track, pause and volume, voice and
fallback notification destinations and the last-played seed needed by autoplay.
The live controller remains authoritative for its generation and actual
playback, so restart reconciliation adopts live state where possible rather
than creating a competing player.

Exact audio-frame position is not a durable guarantee. Recovery can restart or
advance from the persisted locator, while a stale track follows the ordinary
bounded resolution path. An existing live player is never replaced by a second
resume pass.

Ending music clears the queue and current playback but leaves voice connected.
Leaving voice ends music and disconnects. Idle expiry applies to an inactive
music session; losing every human listener follows the controller's leave
policy. These transitions are deterministic and do not require a model call.

## Autoplay

With autoplay enabled, exhausting the requested queue can enqueue one related
pick seeded from the last-played locator. The pick goes through the same queue
and playback path as a requested track and is announced as autoplay.

Selection excludes tracks already played in the current session, but user-
requested duplicates remain allowed. No candidate, an all-repeat candidate set
or a failed related lookup falls back to ordinary idle behavior without retry
or an invented substitute.

Optional prewarm resolves a future pick and its stream while the current track
is still audible. Its result is discarded when a skip or stop makes it stale.
An early empty answer is checked again at the actual boundary because the
recommendation set may have changed; prewarm never bypasses the source-session
refresh needed after a failed stream.

## Messages and destinations

One message table owns user-facing music wording for slash responses, system
announcements and the notes Nora reads. A session-start or enqueue event is
announced by the caller's own reply. Natural advancement, autoplay, next-after-
skip and recovery announcements use the notify path, so an agent turn cannot
produce a duplicate system announcement.

One send-time resolver owns announcement destination. A guild may name a
dedicated output-only music channel; otherwise the session's starting text
channel is the fallback. The dedicated destination is not a conversation room
and gains no ingest, membership or trust meaning. Slash and conversational
replies still go to the person and room that invoked them.

Routing is re-read from live policy for each announcement. The resolved channel
must belong to the same guild and be writable. A definite refusal may fall back
to the session channel; an uncertain send is not retried into a possible
duplicate. Notification failure never escapes into playback, command completion
or event acknowledgement, and a closed Discord client counts as unavailable
rather than a stale but usable channel cache.

Crossfade, additional conversational controls, saved playlists and persistent
audio libraries are optional capabilities with their own evidence boundaries;
none changes the single-player, single-source and single-notification owners.
