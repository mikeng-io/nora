---
type: decision
title: "Discord Music — Deferred, With Evidence Bars"
description: "Everything considered and parked during the brainstorm, each with the concrete evidence that would justify building it — not a wishlist, a set of tripwires."
tags: [nora, discord, music, backlog, deferred]
timestamp: '2026-08-23'
edges:
  - {dst: 00-overview, rel: motivated_by, provenance: asserted}
---

# Discord Music — Deferred, With Evidence Bars

Each item follows `design/18-media-custody.md` §3's own precedent: *"When
there is evidence... make the swap. Please do not re-litigate it without the
evidence."* Same discipline applied here — a deferred item is not "maybe
someday," it's "built the moment this specific signal shows up."

## Saved / named playlists
**Deferred.** Only the live session queue exists in v1
(`03-state-and-persistence.md`).
**Evidence bar:** someone re-requests the same set of tracks across more
than one session. Until then, a playlist table is a schema built against
zero observed usage — exactly the shape of decision `18-media-custody.md`
warned against for RustFS.
**Open question to settle when building it:** per-person owner (natural fit
— Nora already resolves one `person_id` across platforms via
`identity.toml`, so a playlist would follow someone off Discord too) or
per-guild (shared, dies if the bot leaves the server). Per-person is the
current lean; not decided.

## Persistent audio library / cache
**Deferred.** Nothing is cached for replay; every play re-resolves through
`TrackSource` (`01-playback-and-source.md`).
**Evidence bar:** a specific, repeated cost shows up — re-resolving the same
popular track constantly, or `yt-dlp` failing intermittently on a track
people want reliably available. Only then does RustFS enter this bundle at
all (and even then, per the media-custody precedent, filesystem-behind-a-port
is the first thing to actually try, not RustFS by default).

## SoundCloud
Two separate, differently-sized deferrals (`01-playback-and-source.md`
keeps them distinct on purpose):
- **Scraped, via the existing `YtDlpSource`:** deferred, but cheap to flip
  on — relaxing a domain allowlist, no new code.
  **Evidence bar:** none technical; it's a ToS-risk policy call the operator
  makes deliberately, not a capability gap.
- **Official API adapter (`SoundCloudApiSource`):** deferred, and a real
  second `TrackSource` implementation.
  **Evidence bar:** the scraped path proves unreliable enough, in practice,
  to be worth OAuth app registration and a second adapter — and per the
  port convention, this is also the trigger for writing
  `tests/contract/test_track_source_contract.py`, not optional once a
  second implementation exists.

## Nora agent tools (`play_music`, `skip_music`, `music_status`, ...)
**Deferred.** Slash commands are a complete interface on their own
(`02-control-surface.md`).
**Evidence bar:** people are shown to prefer asking Nora conversationally
("play some jazz") over the slash command often enough that the redundancy
is worth the tool budget. If built, it wraps the identical `MusicPlayer`
methods the slash commands call — never a second mutation path.

## Auto-resume after a Gateway-reconnect voice drop
**Deferred**, named explicitly as a known gap in `02-control-surface.md`
rather than left implicit.
**Evidence bar:** reconnects are shown to happen often enough, during active
playback, to actually annoy someone. The fix (detect the drop, rejoin the
last channel, resume from the persisted queue at the next track — not
mid-song) is a `03-state-and-persistence.md` follow-up once that's true.

## Containerizing the `yt-dlp`/`ffmpeg` subprocess
**Deferred as a hardening pass**, not a blocker — v1 runs the subprocess
directly on the host (`01-playback-and-source.md`).
**Evidence bar:** none required to justify eventually doing this (it's a
reasonable default security posture for processing untrusted remote media);
it's deferred purely because it's orthogonal to shipping v1 and can be
added later with no change to the pipeline's shape.
