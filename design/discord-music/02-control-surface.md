---
type: design
title: "Discord Music — Control Surface"
description: "Slash commands as the sole, deterministic control surface; why the per-attempt-client architecture means a reconnect drops active voice; why no Nora agent tools ship in v1."
tags: [nora, discord, music, slash-commands, control]
timestamp: '2026-08-23'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 03-state-and-persistence, rel: depends_on, provenance: asserted}
---

# Discord Music — Control Surface

## Slash commands, not Nora conversation, is the primary interface

`/music play <query>`, `/music skip`, `/music queue`, `/music pause`,
`/music stop` — registered via `discord.app_commands.CommandTree`, Discord's
native slash-command mechanism. Typing `/music skip` triggers **no LLM
turn**: it is a direct Discord interaction dispatched to a method call on
the guild's player object (`03-state-and-persistence.md`). This is the
control surface in full; nothing about playback correctness depends on Nora
being involved at all.

## The command tree lives where the client lives — and dies when it does

`DiscordAdapter.listen()` (`nora/platform/discord/adapter.py:79-88`)
builds a fresh `discord.Client` **every connection attempt**, by design —
the class's own comment: a `discord.Client` cannot be restarted once closed,
so holding one for the process lifetime turns a supervised reconnect into a
silent no-op (this is `design/discord-integration/01-listener-and-resilience.md`'s
concern, re-encountered here rather than re-argued). Two consequences for
this bundle, both accepted rather than solved:

1. **The `CommandTree` is constructed alongside the client**, inside
   `listen()`, same lifecycle as the message listener already built there.
   It does not need to be re-`sync()`'d on every reconnect (Discord persists
   registered commands server-side), only registered once against the first
   successful connection — but rebuilding the `CommandTree` object itself on
   every attempt, even if `sync()` is skipped on reconnects, is the correct
   default: it costs nothing at this scale and avoids holding a reference to
   a client that may already be dead.
2. **A voice connection is scoped to the client that opened it.** When the
   Gateway forces a reconnect (network blip, Discord-side session
   invalidation), the old client is discarded and any `VoiceClient` hanging
   off it dies too — mid-song, with no warning. This is the same category of
   loss `03-state-and-persistence.md` already accepts for a process restart
   (now-playing is not recoverable), just triggered by a different, more
   frequent event. **Explicitly not solved in v1** — reconnect-triggered
   voice loss is a known limitation, not a silent gap: `04-deferred.md`
   names the fix (detecting the drop and auto-resuming from the persisted
   queue) as a candidate once it's shown to actually happen often enough to
   annoy someone, the same evidence bar the rest of this bundle holds to.

## No Nora agent tools in v1

The command surface above is a **complete, working bot** on its own —
nothing is missing without a `play_music`/`skip_music` tool exposed to
Nora's agent loop. Adding one later would let someone say "nora play some
jazz" instead of typing the slash command; that is pure convenience with
zero functional gap behind it, and this whole bundle's bias (per the
operator's own steer mid-session) is toward the deterministic, non-agentic
path wherever one exists. If added later, the tool is a thin wrapper calling
the exact same `MusicPlayer` methods the slash commands call — one state
object, two callers, never two sources of truth — tracked in
`04-deferred.md`.

Worth being precise about why this isn't in tension with Nora's
stateless-chat design (a question raised directly mid-session): "stateless"
describes Nora's *conversation* — no accumulated memory across turns,
context re-derived each tag — not whether tools may read/mutate shared
state. A hypothetical `play_music` tool would be exactly as stateless as
`remind` or `weather` already are: it reads/mutates `MusicPlayer`, returns a
result, done. The reason to skip it now is redundancy with slash commands,
not an architectural constraint.
