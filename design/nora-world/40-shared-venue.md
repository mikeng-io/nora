---
type: design
title: "Nora World: the shared venue (karaoke and concerts)"
description: "The first building. A YouTube link becomes a local backing track (yt-dlp, Demucs, LRCLIB lyrics) on every client; the singer's voice is tagged with song position and placed by that tag, so network delay stops mattering; our own WebRTC with singing-tuned settings; voice cover as a server step; what stays impossible."
tags: [nora, world, karaoke, concert, webrtc, livekit, latency, voice-cover, rvc, demucs]
timestamp: '2026-10-16'
status: proposed
governance: pre-governance-input
edges:
  - {dst: 00-overview.md, rel: realizes, provenance: asserted}
  - {dst: 20-presentation-and-avatars.md, rel: depends_on, provenance: asserted}
  - {dst: ../discord-music/_index.yaml, rel: relates_to, provenance: asserted}
---

# The shared venue

## 1. Why we own the audio

Inside Discord, each person's mic goes straight from their client to
Discord; nothing can change it on the way, and a voice that arrives one round
trip late over music is clearly audible. In the portal, every WebRTC
connection is ours (a LiveKit server), so both problems become solvable.
People in the venue do not also sit in a Discord voice channel with it open,
or they hear everything twice.

## 2. The backing track is a file, not a stream

1. A person pastes a YouTube link. The server downloads it with yt-dlp, as
   the music path already does (`design/discord-music/`, in the
   `voice-sidecar` container).
2. Demucs separates the vocals out, giving a real instrumental.
3. Time-synced lyrics come from LRCLIB when available.
4. Every client downloads the instrumental before the song starts and plays
   it locally from one shared start signal.

## 3. Alignment by song position

- The singer's client plays the instrumental and captures the mic in an
  AudioWorklet that tags each audio frame with the song position playing when
  it was captured. The tag travels with the audio (data channel or an RTP
  header extension).
- Each listener plays its own instrumental a fixed delay behind the singer
  (start at ~400 ms, to be measured) and places the voice at its tagged
  position. Network delay and jitter stop mattering, because alignment is by
  song position, not arrival time.
- Lyrics, avatar animation and any score follow the same clock.

**Device latency is still real.** Bluetooth headphones add 150–300 ms that
WebRTC cannot see, and the browser's reported output latency is unreliable on
some browsers. A one-time clap calibration per device measures the offset and
the tag is corrected by it.

## 4. Settings for singing

- Opus in music mode, stereo, high bitrate.
- Echo cancellation, noise suppression and auto-gain off; they are tuned for
  speech and damage singing. So the singer wears headphones.
- The singer monitors their own voice locally, never through the server.

## 5. Voice cover

A server step between the singer and the listeners: RVC (or a successor) on a
GPU converts the voice to a chosen voice model. Its added delay does not
matter, because the tags survive conversion and the listeners' fixed delay
absorbs it. The singer still monitors their dry voice. Each offered voice
needs a trained model; whose voices may be offered is a consent question for
the operator (`50-open-questions.md` §5).

## 6. Discord, optionally

For people who want to listen from Discord, a server mixer renders one
aligned stream (instrumental plus tagged voices) and the `voice-sidecar` bot
plays it into a voice channel. This is the only place a server-side mix is
needed.

## 7. What stays impossible

- **Live duets between remote singers.** Hearing each other in time needs
  under ~30 ms each way. Take turns, or chain: the second singer sings over
  the first one's tagged voice.
