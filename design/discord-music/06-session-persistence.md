---
type: design
title: "Discord Music — Session Persistence"
description: "Extends persistence from just the upcoming queue to the whole session (now-playing, paused, volume, channels) via a new music_session table — stage 1 write-through, stage 2 boot-time resume. Both built."
tags: [nora, discord, music, postgres, persistence, session]
timestamp: '2026-08-24'
edges:
  - {dst: 03-state-and-persistence, rel: extends, provenance: asserted}
---

# Discord Music — Session Persistence

**`notify_channel_id` is the FALLBACK since 2026-09-21, not the destination**
(`10-notification-routing.md`). The column is still written and still means "the
channel where it started"; what changed is that a guild whose `policy.toml`
names a `music_channel` announces there instead, resolved per-post rather than
read from this row. No migration — every "the notify channel" below should read
"the notify FALLBACK channel".

**Status: both stages built.** Stage 1 is the write-through half — every
state transition `MusicPlayer` makes is persisted to Postgres. Stage 2
(below) makes Nora actually reconnect and resume on boot, guarded by the
same "only if a human is still there" rule the empty-channel disconnect
already uses.

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

## Stage 2: boot-time resume

`resume_persisted_sessions` (`music_commands.py`) scans
`MusicPlayerManager.persisted_sessions()` (a thin wrapper over
`MusicSessionStore.all()`) and, for each row, reconnects to
`voice_channel_id` ONLY IF it currently has human members — mirrors the
existing "leave when a channel empties of humans" rule symmetrically, so a
deploy never makes Nora silently rejoin and play into an empty room. A
channel that's gone entirely (`client.get_channel`/`fetch_channel` both
come up empty), or has no humans in it right now, gets its row discarded
via `MusicPlayerManager.discard_persisted_session` — no `MusicPlayer` or
voice connection is ever constructed for it.

`MusicPlayerManager.resume_persisted_session` builds the `MusicPlayer`
directly from the persisted row (not through `play()`'s query-resolving
flow — there's no new query, just an already-known track). It calls
`MusicPlayer.resume_from_persisted`, which restarts that track from
position 0 (never mid-frame — the one limitation from stage 1 that
`03-state-and-persistence.md`'s original reasoning still holds) and
re-applies `paused`/`volume`. If the persisted track no longer resolves
(deleted, region-locked), it falls back to `on_track_finished()`'s normal
advance — picking up wherever the ACTUAL queue continues, or staying idle
if it's empty — exactly like a stale queued track already does elsewhere
in this bundle.

**Idempotent against re-running.** `resume_persisted_session` refuses to
touch a guild that already has a live `MusicPlayer`, so wiring the scan
into `on_ready` (which can fire more than once per process, per
`02-control-surface.md`'s own note about reconnects) is safe — it only
ever does real work the first time a given guild's session gets resumed.
