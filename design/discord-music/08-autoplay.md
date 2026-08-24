---
type: design
title: "Discord Music — Autoplay"
description: "When the queue empties with nothing next, seed YouTube's own Mix/autoplay-radio recommendations from the track that just finished instead of going idle; config-gated, off by default."
tags: [nora, discord, music, autoplay, recommendations]
timestamp: '2026-08-24'
edges:
  - {dst: 01-playback-and-source, rel: extends, provenance: asserted}
  - {dst: 03-state-and-persistence, rel: extends, provenance: asserted}
---

# Discord Music — Autoplay

**Operator's own evidence** (2026-08-24): "i expect after it FINISH it will
continue to add recommend/related song NOT stop" — YouTube's own autoplay
behaviour is the expectation, not a bot that goes silent the moment the
last queued track ends.

## What changes

`MusicPlayer._advance()` (03-state-and-persistence.md's own state machine)
already loops: pop the next queued track, or — if the queue is empty — stop
and wait out `idle_timeout_seconds`. Autoplay inserts ONE new option before
that "queue is empty" branch gives up: ask `TrackSource.related()`
(`nora/domain/ports.py`) for recommendations seeded from whatever JUST
finished, enqueue the first one, and loop again — the same `pop_next` path
picks it straight back up, so no second code path plays a track.

`[music] autoplay_enabled` (default `false`, `MusicConfig`) gates it —
a deployment that hasn't opted in behaves exactly as before. `[music]
enabled = false` (the whole bot off) makes it moot the same way every
other music setting already is moot when unset.

## `TrackSource.related(locator)`

New `TrackSource` method (`nora/domain/ports.py`), NEVER raises — same
discipline as `resolve()`. `YtDlpSource`'s implementation
(`nora/infra/music/ytdlp_source.py`) is YouTube's own well-known trick: a
video's own watch URL with `list=RD<video_id>` appended is the same "Mix"
playlist YouTube's own UI offers as autoplay next-up, seeded from that one
video. Flat-extracted exactly like an ordinary playlist listing — no
per-track stream resolution up front, lazily resolved right before it
plays, same as every other queued track. The Mix always lists its own seed
video first; that entry is filtered out so autoplay never re-queues the
track that just ended.

`locator` is `QueuedTrack.url` of the track that just finished — a
previously-RESOLVED URL (`https://www.youtube.com/watch?v=<id>`), never a
raw search query. Recommending from an unresolved guess would compound one
uncertain match into another.

## What autoplay is NOT

- **Not a second mutation path.** `_advance()`'s existing `pop_next` /
  `voice.play()` call is the ONLY thing that ever starts a track; autoplay
  just enqueues one more row for it to find, via the identical
  `MusicQueueStore.enqueue` every ordinary `/music play` uses.
- **Not distinguishable in the queue table's schema.** `QueuedTrack.
  added_by_person_id` is reused as the sentinel (`AUTOPLAY_PERSON_ID`,
  `nora/domain/music.py`) rather than adding a column — the queue store
  doesn't need to know autoplay exists at all.
- **Not a retry loop.** `related()` returning empty (no Mix, the seed
  track's Mix has nothing new, or the lookup failed) falls straight
  through to the SAME idle-disconnect behaviour that already existed —
  never retried, never substituted with a guess.
- **Not interruptible by anything special.** An autoplayed track behaves
  exactly like any other queued track for `/music skip`/`stop`/`top` — it
  IS one, indistinguishable except by the sentinel person id, which only
  `MusicPlayerManager._notify_now_playing` reads (to say "Autoplaying: …"
  instead of "Now playing: …" so a listener isn't left wondering why a
  track nobody requested started).

## Open, not yet answered

- Whether autoplay should chain more than one related track ahead (queue
  2-3 at once) rather than one-at-a-time reseeded from whatever most
  recently played — one-at-a-time was chosen because it stays closest to
  YouTube's own adaptive behaviour (a skipped autoplay track doesn't leave
  stale queued picks behind) and needed no new "how many" config knob.
- No per-guild opt-out — today it is one deployment-wide flag, the same
  granularity every other `[music]` setting has.
