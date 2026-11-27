---
class: design
subject: the venue, singing together in the world, aligned by song position
owns:
  - venue: "the world's first building, where people sing or listen together over audio Nora's own connections carry"
  - song position tag: "the position in the backing track stamped on each frame of a singer's voice, by which listeners place it instead of by arrival time"
area: web
links:
  - {to: docs/design/web/world.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
---

# The venue

The venue is the first building in the [world](world.md): karaoke and concerts.
Nora owns the audio path here, which is why two problems that Discord's voice
cannot solve become solvable.

## Why Nora owns the audio

Inside a Discord voice channel each person's microphone goes straight from their
client to Discord, so nothing can change it on the way, and a voice that arrives
one round trip late over music is plainly audible. In the portal, every
connection is Nora's own, so alignment can be done. People in the venue do not
also sit in a voice channel with it open, or they hear everything twice.

## The backing track is a file, not a stream

A person pastes a link. The server downloads it, separates the vocals out to
leave a real instrumental, and finds time-synced lyrics where they exist. Every
client downloads the instrumental before the song starts and plays it locally
from one shared start signal.

## Alignment is by song position

- The singer's client plays the instrumental and captures the microphone,
  stamping each frame with the **song position** playing when it was captured.
  The tag travels with the audio.
- Each listener plays its own instrumental a fixed delay behind the singer and
  places the voice at its tagged position. Network delay and jitter stop
  mattering, because alignment is by song position, not arrival time.
- Lyrics, avatar animation and any score follow the same clock.

**Device latency is still real.** Bluetooth output adds delay the connection
cannot see, and a browser's reported output latency is unreliable. A one-time
clap calibration per device measures the offset and corrects the tag.

## Settings for singing

The audio uses music mode at high quality and stereo. Echo cancellation, noise
suppression and automatic gain are off, since they are tuned for speech and
damage singing, so the singer wears headphones. The singer monitors their own
voice locally, never through the server.

## Voice cover

A server step between singer and listeners can convert the voice into another
voice. Its added delay does not matter, because the tags survive conversion and
the listeners' fixed delay absorbs it; the singer still hears their own dry
voice. A voice is offered as a cover only for a voice whose owner has agreed to
that use, the same boundary a voice has everywhere
([generation](../media/generation.md)).

## Listening from Discord

For people who would rather listen from Discord, a server mixer renders one
aligned stream, the instrumental plus tagged voices, and Nora's voice bot plays
it into a channel ([voice](../voice/README.md)). It is the only place a
server-side mix is needed.

## What stays impossible

Live duets between remote singers: hearing each other in time needs well under
thirty milliseconds each way. Singers take turns, or chain, with the second
singing over the first's tagged voice.
