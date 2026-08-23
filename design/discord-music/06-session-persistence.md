---
type: design
title: "Discord Music — Session Persistence"
description: "Extends persistence from just the upcoming queue to the whole session (now-playing, paused, volume, channels) via a new music_session table — stage 1: write-through. Boot-time resume is a deliberate follow-up, not built here."
tags: [nora, discord, music, postgres, persistence, session]
timestamp: '2026-08-24'
edges:
  - {dst: 03-state-and-persistence, rel: extends, provenance: asserted}
---

# Discord Music — Session Persistence

**Status: stage 1 of 2.** This lands the write-through half — every state
transition `MusicPlayer` makes is now persisted to Postgres — but does NOT
yet make Nora reconnect and resume on boot. A restart still drops active
sessions exactly as before; the difference is the state to resume FROM now
survives the restart, ready for stage 2 to read.

## What changed from `03-state-and-persistence.md`'s original split

That document drew a hard line: the upcoming queue persists, now-playing/
paused/volume never does ("you cannot resume mid-audio-frame... a restart
is a deliberate deploy event"). That was the right call when it was made,
before crossfading, pagination, and a control panel existed to make music
sessions feel less disposable. The operator's own instruction now: restart
survival matters enough to build.

**Playback POSITION within a track is still never attempted** — that part
of the original reasoning stands. What's new is persisting enough to
restart the CURRENT track from position 0 and pick the session back up,
rather than losing it outright.

## `music_session` — a snapshot, not a log

One row per guild (`guild_id` is the primary key itself), upserted in
place — contrast `music_queue`, an append/pop log with many rows per
guild. Columns: `voice_channel_id`/`notify_channel_id` (captured once, at
session start — "the channel where it started", `02-control-surface.md`'s
existing rule, extended to the voice channel too), `now_playing_source`/
`now_playing_locator`/`now_playing_title` (all `NULL` together means
"session alive, nothing playing right now" — waiting on the idle timer,
not the same as the session having ended), `paused`, `volume`,
`updated_at`.

Port: `MusicSessionStore` (`nora/domain/music.py`), same shape as
`MusicQueueStore` — `upsert`/`get`/`all`/`delete`/`aclose`. `all()` exists
now specifically for stage 2's boot-time scan; unused until then.

## `MusicPlayer` writes through on every transition

`MusicPlayer` gained `session: MusicSessionStore`, `voice_channel_id`,
`notify_channel_id` (both captured once at construction — mirrors how
`MusicPlayerManager._notify`'s callback is captured only when a NEW player
is created, `02-control-surface.md`). Every transition that changes what a
restart would need to know calls `_persist_session()`:

- `_advance()` — after picking a new now-playing track, OR after finding
  the queue empty (persists `now_playing_* = NULL`, row stays).
- `pause()`/`resume()` — the `paused` flag.
- `set_volume()` — now ASYNC (was sync) precisely because persisting
  requires an `await`; `MusicPlayerManager`'s callers already awaited it.
- `stop()` — deletes the row instead of upserting. This is the one
  transition that actually ENDS the session, not just changes what's in it.

`voice_channel_id`/`notify_channel_id` are threaded as new OPTIONAL
`MusicPlayerManager.play()` parameters (`voice_channel_id`, matching
`notify`'s existing "captured only when creating a fresh player" pattern)
— the raw Discord IDs, not derivable from the opaque `connect`/`notify`
closures, because stage 2 needs to persist and later read back real
channel IDs, not just live callables.

## Migration `0029_music_session`

Hand-written (same reason 0028/0004 are): `ck_music_session_volume_range`
(mirrors `MusicPlayer.set_volume`'s own `[0.0, 2.0]` validation) is
invisible to `alembic check`. Contract-suite-covered
(`tests/contract/test_music_session_store_contract.py`) per this repo's
port convention — `SqlMusicSessionStore` and `FakeMusicSessionStore` both
pass it.

## Stage 2 (not built here): boot-time resume

On boot, scan `MusicSessionStore.all()`; for each row, reconnect to
`voice_channel_id` ONLY IF it currently has human members (operator's own
call, mirroring the existing "leave when a channel empties of humans"
rule symmetrically — a deploy must not make Nora silently rejoin and play
into an empty room). If reconnecting: restart the persisted now-playing
track from position 0 (operator's own call — never attempt mid-frame
resume), re-apply `paused`/`volume`, and let the already-persisted queue
continue normally from there. If the channel has no humans (or the row's
`voice_channel_id` no longer exists): delete the stale row, do not
reconnect.

This needs the Discord platform layer to run channel-membership checks and
drive reconnects at boot — real, separate work (a new hook off
`DiscordAdapter`'s `on_ready`, likely alongside `_register_music_commands`)
— deliberately scoped OUT of this PR to keep it reviewable.
