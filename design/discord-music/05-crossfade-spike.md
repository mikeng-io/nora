---
type: design
title: "Discord Music — Crossfade Spike"
description: "How true audio crossfading works within discord.py's one-source-at-a-time voice player, config-gated and off by default; explicitly a spike, not hardened."
tags: [nora, discord, music, crossfade, audio, spike]
timestamp: '2026-08-24'
edges:
  - {dst: 01-playback-and-source, rel: extends, provenance: asserted}
  - {dst: 03-state-and-persistence, rel: extends, provenance: asserted}
---

# Discord Music — Crossfade Spike

**Status: spike, 2026-08-24.** Built fast on the operator's own instruction
("spike this first") rather than fully hardened — verified by unit tests
against synthetic timing and a fake voice client, NOT yet verified for
actual audio quality against a real Discord voice channel. `[music]
crossfade_seconds = 0.0` (default) means every deployment that hasn't
explicitly turned this on is completely unaffected — behaviour is bit-for-
bit identical to before this spike landed.

## The core problem: Discord only ever holds ONE audio source

`discord.VoiceClient.play(source, after=...)` accepts exactly one
`AudioSource`; calling it again while already playing raises
`ClientException`. There is no native "play two things at once." True
crossfade — both tracks audible, one fading out as the other fades in —
needs a single object that LOOKS like one source to Discord but internally
reads and blends two real ones.

## `CrossfadeSource` (`nora/platform/discord/crossfade.py`)

Wraps the outgoing track's still-live `discord.AudioSource` and the
incoming track's fresh one. Every 20ms frame, for `crossfade_seconds`, it
reads both and linearly ramps gain (`old`: 1→0, `new`: 0→1) using the
stdlib `audioop` module (`audioop.mul`/`audioop.add` — fast, C-
implemented). After the fade window, it delegates purely to `new`.

**`audioop` is deprecated since Python 3.11 and removed in 3.13** (this
repo pins 3.12, `pyproject.toml`). If crossfading survives past the spike
stage, replace this before any Python upgrade past 3.12 — `array`-based
mixing or a small extension are the candidates; not attempted here because
the spike's job was proving the mechanic works, not picking its permanent
implementation.

Swapped in via `VoiceClient.source = mixer` (a live property setter that
swaps the `AudioPlayer` thread's source in place) rather than
stop-then-play — the thread never sees a gap.

## Timing: fire the completion signal EARLY, not at true EOF

The existing pull architecture (`MusicPlayer._advance` pops the next track
and calls `voice.play()` only once `on_finished` fires) is reused almost
unchanged — the ONLY new mechanism is that `DiscordVoiceSession` now fires
its completion signal `crossfade_seconds` BEFORE a track's true end
(`duration_seconds`, resolved fresh per track,
`01-playback-and-source.md`'s lazy-resolve), scheduled via
`loop.call_later`. `_advance()`'s subsequent `play()` call lands while the
old track is still technically "playing" — which is exactly what makes
`DiscordVoiceSession.play()` choose the live-swap-into-`CrossfadeSource`
path instead of a fresh `voice_client.play()`.

**No changes to `MusicPlayer`/`MusicPlayerManager` at all.** All the
complexity is contained inside `DiscordVoiceSession` — the state machine
that's fully unit-tested doesn't need to know crossfading exists.

The REAL `after` callback, when the mixed source eventually hits its own
true EOF, must not double-signal completion — reuses the exact suppression
mechanism `stop()`'s own race fix already established
(`voice_session.py`'s module docstring).

## Explicitly NOT crossfaded

- **`/music skip`** — an instant cut, on purpose. Skipping means "get me
  out of this now," not "start a DJ transition." `MusicPlayer.skip()`
  already calls `voice.stop()` directly; this spike does not touch that
  path.
- **A track with no known `duration_seconds`** (yt-dlp couldn't determine
  one — a live stream, some edge-case extractor result) — no timer is
  scheduled, falls back to a hard cut for that boundary only.
- **The very last track in a session** — nothing to crossfade INTO; it
  just plays to its own true end.

## Open, not yet answered

- Real audio quality: linear gain (not equal-power) may sound like a dip
  in loudness mid-fade rather than a smooth blend — untested by ear.
- `audioop`'s Python-3.13 removal (above).
- Whether `crossfade_seconds` should have an upper sanity bound (a value
  larger than a short track's own duration is currently just silently
  capped to "the whole track" by `CrossfadeSource`'s own frame-count math,
  never validated at the config layer).
