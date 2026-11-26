# Audio ownership

## One owner, two planes

The audio controller alone joins Discord voice, decrypts and decodes received
audio, runs media decoders and writes to the voice socket. Nora retains every
user-facing decision through its voice and music ports: whether to join or
leave, which track follows, when to skip or stop, listener volume and what to
say. An architecture boundary prevents production Nora code from opening a
second connection.

Music resolution yields a short-lived stream locator; the controller's decoder
pulls from that locator directly. Nora does not download or relay the music.
Speech is the small exception: synthesized audio crosses the bounded control
request from Nora to the controller. Neither side builds a persistent audio
cache.

The controller owns only the currently playing music slot and a bounded speech
queue. The upcoming music queue, autoplay and session persistence belong to
Nora. A play command replaces the current slot rather than enqueueing behind
it, and a natural completion asks Nora what should follow.

## Connection lifecycle

Connection operations are serialized per guild. A generation identifies the
current connection and invalidates on exit, death or replacement. Commands and
audio naming a stale generation cannot act on its successor. A generation
conflict may reconcile against a genuinely live connection, but never revives
one Nora deliberately left.

Durable desired state says whether a restart should rejoin. An explicit exit
changes it to idle. Nora adopts the controller's current generation and
playback when reconciling after a restart rather than restoring a competing
session from its own stale snapshot.

Join admission requires an authenticated guild member who is currently in a
voice channel and whose guild is admitted for speech. Nora follows that
caller; it does not choose an arbitrary channel or maintain a second list of
voice-channel room identities. An unknown guild does not allocate controller
state.

## Speech and music arbitration

Speech has priority over music. It suspends the current music generation,
plays without overlap, then revives a seekable track near the consumed
position. Buffered audio makes that position approximate. A non-seekable live
stream ends rather than pretending to resume.

A skip or stop during speech invalidates only the suspended music, not the
speech. Pause during speech is rejected because its meaning is ambiguous.
Stopping music leaves the connection and receive path intact; leaving voice
stops every audio activity. When no human listeners remain, controller
membership observation starts the owned leave policy; Nora itself does not
count as a listener.

Track failure is distinct from natural completion. Nora may refresh the source
session and re-resolve the same track within a bounded budget keyed by the
stream locator before advancing. A stale failure neither retries nor advances.
These decisions use the same per-guild serialization as play, skip and
teardown.

## Events, resources and failure

Control requests carry idempotency identity. Playback events carry both event
and playback identity, are retried through a bounded outbox and are consumed
under the guild lock. Only an event for the currently expected playback can
advance the queue; duplicate and late events are harmless.

Request authentication, body limits, connection limits, speech queue limits,
receive backpressure and subprocess termination bound both work and retained
audio. A stalled decoder terminates with a failure event rather than occupying
the player forever. Replacement and exit terminate and reap old subprocesses.

An unreachable controller is `unavailable`, not proof that nothing is playing
or that a track ended. A missing connection is different again. Status,
request identity, playback identity, event sequence and terminal reasons make
the hops auditable without recording the audio itself.

Crossfade, blended ducking, barge-in and other mixing policies are optional
changes to this contract, not hidden state in Nora. They must preserve speech
priority, listener-volume independence, bounded resources and the single
connection owner.
