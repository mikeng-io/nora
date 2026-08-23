---
type: design
title: "Discord Music — Agent Tool"
description: "Nora agent tools (play_music/skip_music/stop_music/music_status) for conversational control, gated to Discord only — the MusicToolBridge port and why it exists, given nora.agent and nora.platform are layer-peers that cannot import each other."
tags: [nora, discord, music, agent, tools]
timestamp: '2026-08-24'
edges:
  - {dst: 02-control-surface, rel: extends, provenance: asserted}
---

# Discord Music — Agent Tool

Promotes `04-deferred.md`'s "Nora agent tools" item from deferred to built,
per the operator's own evidence: wanting to say "play some kpop" or "play
apt" conversationally, not just via `/music play`.

## Discord-only, enforced where the LLM actually reads it

`Room` (`nora/agent/room.py`) already carries `.platform` for every tool —
its own docstring explains why platform comes from there rather than a new
`build_tool_specs` parameter ("two parameters carrying the same fact is
how the two drift"). Every music tool's FIRST line checks
`room.platform != "discord"` and returns `unavailable` immediately, before
ever touching the bridge — Signal (or any future platform) never
constructs a voice-channel lookup, never resolves a guild, nothing.

## Why a new port, not a direct `MusicPlayerManager` import

`nora.agent` and `nora.platform` are PEERS in this repo's
layered-architecture import-linter contract (`pyproject.toml`) — "platform
is independent of the other four [packages]" is the contract's own
comment. `nora.agent.tools` cannot import
`nora.platform.discord.music_manager.MusicPlayerManager` directly; the
contract would fail the build. `MusicToolBridge` (`nora/domain/ports.py`)
is the seam every other cross-layer capability in this codebase already
uses (`TrackSource`, `WeatherProvider`, ...) — declared in the
framework-free domain layer beneath both, implemented in
`nora.platform.discord` (`DiscordMusicToolBridge`), wired together at the
composition root (`nora.app`, which is allowed to import everything).

## `DiscordMusicToolBridge` — the one new capability, not a rebuild

Everything the bridge needs beyond what `MusicPlayerManager` already
does is a single thing: resolve a Discord guild member's CURRENT voice
channel from `(conversation_id, sender_id)` alone, since
`NormalizedMessage`/`TagContext` never carry live Discord state (a Signal
message and a Discord message are both flattened to the same shape by
the time a tool sees them). That needs a live `discord.Client`, which
`DiscordAdapter` didn't previously expose — it now holds
`self._current_client`, set/cleared alongside the per-attempt client
`listen()` already builds, read through a new `current_client()` accessor
(`None` whenever there's no live connection attempt right now, same
"wiring gap = unavailable" discipline as everywhere else).

The bridge reuses `_connect_factory` (`music_commands.py`) VERBATIM for
actually joining a voice channel, and calls the exact same
`MusicPlayerManager.play`/`skip`/`stop`/`status` methods the slash
commands call — never a second mutation path, matching
`02-control-surface.md`'s own "if added later" plan for this feature
written back when it was still deferred.

## Four tools, not the whole slash-command surface

`play_music`, `skip_music`, `stop_music`, `music_status` — the natural
conversational asks ("play X", "skip this", "stop the music", "what's
playing"). Pause/resume/volume stay slash-command-only for now: lower
conversational value for the added tool-budget cost, same evidence-bar
discipline `04-deferred.md` already holds every other item in this bundle
to. Promote them the same way this item itself got promoted, if someone
actually asks Nora to pause/resume/adjust volume conversationally often
enough.

## `play_music`'s query: a plain string, the model decides how to fill it

No separate "search the web first" step is built into the tool — `query`
is documented as accepting either a URL or a search string, and the
existing `YtDlpSource` ytsearch fallback already resolves a plain term
like "apt" directly to its top YouTube match. For a vaguer request ("play
some kpop"), the model can use its OWN existing web-search tool first to
find a real curated playlist link, then pass that URL to `play_music` —
ordinary multi-tool reasoning in one turn, not new plumbing. The tool's
own description carries this guidance; no separate skill or doctrine
mechanism is needed, same as every other native tool in this codebase.
