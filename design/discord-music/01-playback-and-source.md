---
type: design
title: "Discord Music — Playback Pipeline & Track Source"
description: "yt-dlp resolves a stream URL, ffmpeg transcodes it live into Discord voice — no download, no disk, no RustFS — and the TrackSource port this rides on, YouTube now, SoundCloud later."
tags: [nora, discord, music, yt-dlp, ffmpeg, ports, streaming]
timestamp: '2026-08-23'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# Discord Music — Playback Pipeline & Track Source

## The pipeline is stream-through; nothing is stored

`yt-dlp` does not need to download bytes to identify where they live — given
a query or URL it resolves a **direct, streamable media URL** (this is
`extract_info(url, download=False)` in library terms, not a CLI download).
That URL is handed straight to Discord's voice audio source
(`discord.FFmpegPCMAudio`/`FFmpegOpusAudio`, part of the `discord.py`
dependency already in `pyproject.toml`), which shells out to `ffmpeg` with
`-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5` so it fetches
and transcodes over HTTP itself, piping Opus frames into the voice socket as
it goes:

```
query/URL → yt-dlp (resolve, no download) → direct media URL
          → ffmpeg (fetch + transcode, no download) → Discord voice socket
```

Nothing in the ordinary path touches disk. This is the entire reason
`00-overview.md` can say "no RustFS" flatly rather than "a lighter RustFS":
there is no artifact — a live audio stream is not a thing you register
anywhere, it is a thing you point ffmpeg at. The earlier framing of this
feature (an artifact produced by a sandboxed job, registered with `drop`,
delivered as a URL) does not apply because nothing is *produced*; playback
is transient by construction, the same way an HTTP response body is.

**Fallback, not the ordinary path.** Some formats (DASH-fragmented audio,
certain post-processing requirements) aren't directly ffmpeg-streamable from
a bare URL. When `yt-dlp` reports that shape, the fallback is a scratch temp
file — download to a local tmp path, play from disk, delete when the track
ends. This is ordinary ephemeral tmpfile handling, not a storage subsystem:
nothing about it is meant to outlive the track, so it needs no schema, no
port, and no retention policy.

## Isolation is a hardening question, not an architectural one

`yt-dlp` and `ffmpeg` both process untrusted remote input (arbitrary URLs,
arbitrary media containers) and both have had real CVEs. Running them in a
container is worth doing eventually, but it is orthogonal to this design —
the pipeline above works identically whether the subprocess runs on the
host or inside a container. **v1 runs the subprocess directly**; sandboxing
is a follow-up hardening pass, not a blocker, per the operator's own framing
mid-session ("probably even no need container, just for security
practice").

## The `TrackSource` port

Mirrors this codebase's existing narrow, fail-closed provider ports
(`WeatherProvider`, `SearchProvider` in `nora/domain/ports.py`) rather than
`ComputeBackend` — the shape is "resolve an external thing into a typed
result, never raise," not "launch an arbitrary job":

- **Input:** a query or URL string.
- **Output:** zero or more resolved tracks (title, duration, the direct
  stream URL, a source identifier) — an empty sequence, never an exception,
  when nothing resolves. Never raises; a dead link or an unsupported site is
  a normal, expected outcome this port must be able to say plainly, same
  discipline as every other provider port in this file. **More than one
  result means a playlist link expanded** (below) — an ordinary search or a
  single video URL always resolves to at most one.
- **One implementation ships now**: `YtDlpSource`, YouTube only (allowlist
  enforced at the source, not left to whatever `yt-dlp` happens to accept —
  a query someone pastes is untrusted input, same category `SearchProvider`
  and `LinkPreviewSource` already treat carefully).

### Playlist-URL expansion is narrow, not "whatever yt-dlp would expand"

A `/playlist?list=...` link — what YouTube's own UI gives you FROM a
playlist's page — expands into every video it contains, capped at 50 (a
link shared out of a 500-track "Mix" or someone's personal playlist must
not flood a guild's queue from one `/music play` call). A `/watch?v=...
&list=...` link — an ordinary video that happens to carry an incidental
`list=`/`index=` (sharing a video FROM inside a playlist, or YouTube's
autoplay "Mix" queue) — resolves to that ONE video only, by construction:
the `list`/`index`/`start_radio` params are stripped before the URL ever
reaches `yt-dlp`, so there is nothing left for it to expand. Least-surprise
is the whole argument: pasting a video link must never silently queue fifty
unrelated tracks because it happened to be opened from inside a playlist.

`MusicPlayer.play` enqueues every resolved track, in order, before deciding
whether to start playback — only the FIRST call for an idle guild advances;
the rest of an expanded playlist just joins the queue behind it, same as if
they'd been added one at a time (03-state-and-persistence.md).

### A playlist listing is flat; the actual stream is resolved lazily

Fully resolving 50 tracks' playable stream URLs (one `extract_info` call per
video) before a single `/music play` reply can even be sent made that call
block for however long 50 sequential yt-dlp round-trips take — discovered
live 2026-08-23, minutes long, and indistinguishable from Nora being stuck.
`YtDlpSource` now expands a playlist TARGET with `extract_flat=True` — title
and video ID per entry only, no per-track format lookup — and
`ResolvedTrack.locator` carries a re-derivable `query_or_url` for that exact
track (never the raw search text, which could re-resolve to a different top
result later). `MusicQueueStore`'s `url` column has always been documented
as "a resolve INPUT to re-derive from, not a cache" (`ResolvedTrack`'s own
docstring, `nora/domain/ports.py`) — this closes the gap between that stated
contract and what `MusicPlayer` actually stored. `MusicPlayer._advance`
resolves the locator fresh, right before a track plays; a track gone stale
by then (deleted, region-locked, a dead playlist entry) is skipped rather
than failing playback.

Per this repo's port convention (`CLAUDE.md`: *"A port never gains a second
implementation — including a test fake — without a shared contract suite
both pass"*), a single implementation needs no contract suite yet — an
ordinary fake behind `nora.testing` (matching `FakeWeatherProvider`'s shape)
is enough until a second `TrackSource` actually exists.

## YouTube now, SoundCloud later — two different "later"s

Worth keeping distinct, because they're different-sized decisions:

1. **Cheap SoundCloud**: `yt-dlp`'s own extractor set already covers
   `soundcloud.com` under the identical `YtDlpSource` — turning it on later
   is relaxing the domain allowlist, not new code. Same ToS-risk shape as
   YouTube (scraping, not an authorized API), so the decision to flip it on
   is a policy call, not an engineering one.
2. **Real SoundCloud**: an OAuth-based `SoundCloudApiSource` against their
   official API — more reliable, ToS-clean, needs app registration and
   credentials. This IS a second `TrackSource` implementation, and per the
   port convention above, that's exactly when a contract suite
   (`tests/contract/test_track_source_contract.py`, both fakes/adapters
   proven against it) becomes required, not optional.

`04-deferred.md` tracks both as parked, with the distinction preserved.
