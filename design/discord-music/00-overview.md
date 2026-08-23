---
type: design
title: "Discord Music — Overview & Scope"
description: "A voice-channel music bot riding on the existing DiscordAdapter: why it's deterministic-first, what v1 covers, and everything explicitly deferred."
tags: [nora, discord, music, voice, scope]
timestamp: '2026-08-23'
edges: []
---

# Discord Music — Overview & Scope

## What this is, and what it deliberately is not

A Discord voice-channel music bot: join a channel, play audio resolved from a
URL/search query, maintain a queue, respond to skip/pause/stop. Brainstormed
2026-08-23, starting from a Discord-bot pattern (yt-dlp → ffmpeg → voice) and
narrowed hard across the session — the narrowing IS the design, recorded here
so it isn't re-litigated node by node.

**Not an agent feature.** Every control action (play, skip, pause, queue,
stop) is a deterministic operation on a per-guild state machine — no LLM
judgment involved in resolving a play request or deciding what plays next.
This was the operator's own correction mid-session ("nothing about llm/agent,
maximum just exposing tools for nora for control") after an initial framing
routed this through Nora Compute/Smith, which turned out to be the wrong
shape entirely — see `02-control-surface.md` for why.

**Not built on Nora's existing produced-artifact infrastructure.** RustFS,
`drop`, and the Smith sandbox (`nora/compute/smith/`, `nora/infra/compute/
docker_backend.py`) exist for a different problem: genuine arbitrary code
execution producing a one-off deliverable, handed to an LLM as a task brief.
A music download is a fixed template — the same operation every time, no
LLM interpretation needed — and routing it through `LocalDockerBackend`
would mean paying an LLM turn to run a deterministic shell command. See
`01-playback-and-source.md` for the actual pipeline (streamed, not
downloaded — so no RustFS at all, not even a lighter version of it).

## Builds on `design/discord-integration/`, does not restate it

This bundle assumes `DiscordAdapter` (`nora/platform/discord/adapter.py`)
already exists and is deployed — see `design/discord-integration/00-overview.md`
for what it is. One fact from that bundle is load-bearing here and is
covered in `02-control-surface.md` rather than restated: `DiscordAdapter
.listen()` builds a **fresh `discord.Client` per connection attempt**
(`adapter.py:47-58`'s own comment explains why — a `discord.Client` cannot
be reused after it closes), which means anything hung off that client
(voice connections, slash-command registration) does not survive a Gateway
reconnect either.

## What v1 is

- Discord slash commands (`/music play|skip|queue|pause|stop`) as the sole
  control surface — `02-control-surface.md`.
- `yt-dlp` resolves a query/URL to a direct, streamable audio URL; `ffmpeg`
  transcodes it live into Discord's voice socket. No file ever written to
  disk in the ordinary path — `01-playback-and-source.md`.
- A per-guild in-memory player (voice connection, now-playing, loop mode,
  volume) plus a Postgres-backed session queue that survives a process
  restart (though not a live voice reconnect) — `03-state-and-persistence.md`.
- Auto-disconnect on an empty voice channel, and after an idle timeout with
  an empty queue — `03-state-and-persistence.md`.

## What's explicitly out of scope for v1

Each item below was considered and deliberately deferred, not overlooked —
`04-deferred.md` states what evidence would justify building each one:

- **No saved/named playlists** — only the live session queue.
- **No persistent audio library** (nothing cached for replay after a track
  finishes).
- **No RustFS, no object storage of any kind.** Streaming needs none, and
  nothing else in this bundle produces bytes worth keeping.
- **No Nora agent tools** (`play_music`, `skip_music`, etc.). Slash commands
  are a complete, working interface on their own; a Nora tool would be a
  pure convenience wrapper with no functional gap behind it.
- **No SoundCloud support yet**, official-API or otherwise — YouTube only,
  via `yt-dlp`. See `01-playback-and-source.md` for why turning SoundCloud
  on later is cheap, and why an *official-API* SoundCloud adapter is a
  separate, larger decision.

## Status / Checkpoint

**2026-08-23** — design bundle drafted from a brainstorming session (this
conversation). Nothing built yet. Worktree
`.claude/worktrees/discord-music-bot` (branch `worktree-discord-music-bot`)
exists with a clean baseline gate (4271 passed, 2 skipped, 1 xfailed) but no
feature code — implementation starts after this bundle is reviewed.
