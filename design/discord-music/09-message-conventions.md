---
type: design
title: "Discord Music — Message Conventions"
description: "One canonical message table for every user-facing music string; why the agent path never double-announces; the language policy for system posts vs Nora's replies."
tags: [nora, discord, music, messages, conventions]
timestamp: '2026-08-29'
edges:
  - {dst: 02-control-surface, rel: extends, provenance: asserted}
  - {dst: 07-agent-tool, rel: extends, provenance: asserted}
  - {dst: 08-autoplay, rel: extends, provenance: asserted}
---

# Discord Music — Message Conventions

## The problem this node closes

Three surfaces publish music messages — the slash commands
(`music_commands.py`), the manager's channel auto-notify
(`MusicPlayerManager._notify_now_playing`), and the agent tool bridge's
model-facing notes (`DiscordMusicToolBridge`) — and before this node each
formatted its own strings. That is how `Skipped:` lost the URL (the
2026-08-26 consistency pass unified `Now playing:`/`Queued:` but left skip
and top alone), how `Now next:` became a third verb, and how the two control
paths could read as two different bots. Found by the operator live, 2026-08-29.

## D1 — one string table, `music_messages.py`

Every user-facing music string renders in
`nora/platform/discord/music_messages.py`: pure functions over typed inputs
(`QueuedTrack`, ints), no `discord.py` objects, so the module is
unit-testable without a fake Discord graph (the same seam
`test_music_commands.py` already used for `_reply_for`). The full
vocabulary lives there — track-naming lines (`now_playing_line`,
`autoplay_now_playing_line`, `queued_line`, `skipped_line`, `next_line`),
fixed-state lines (`paused_line` … `volume_line`), and every
error/rejection line both surfaces share. A surface can only rephrase a
message by changing THIS file — one diff, pinned by
`tests/unit/test_music_messages.py`.

## D2 — every surface calls the table; none formats its own

`music_commands.py` and `music_manager._notify_now_playing` and
`music_tool_bridge`'s outcome notes all import the table. The bridge's
notes matter as much as the channel posts: the `note` string is what the
LLM reads to phrase her answer, and it must be the same vocabulary the
channel-level lines use, or the two paths drift again by construction.

## The announcement contract (supersedes this plan's own D3)

The plan for this node originally proposed suppressing a system
"session-start" announcement on the agent path (`notify_on_play=False`) —
on the belief that `MusicPlayerManager.play` posts one. **That belief was
wrong**, and reading `play()` against
`test_the_first_play_does_not_notify_the_channel_that_started_it` showed
it: `play()` never notifies. The notification contract is already
single-owner per event:

| Event | Who announces |
|---|---|
| Session start / enqueue via `/music play` or `play_music` | The caller's own reply (slash reply text, or Nora's conversational reply) — never the notify path |
| Track ends naturally → next starts | `_notify_now_playing` (the ONE silent transition) |
| Skip → next starts | The slash reply names the skipped track; `_notify_now_playing` names what's next |
| Autoplay pick starts | `_notify_now_playing`, as `Autoplaying:` |
| Boot resume | `_notify_now_playing` |

So the agent path has NO double announcement to suppress — Nora's reply is
already the only session-start announcement a tag-path play produces. The
proposed parameter would have added mechanism to solve a problem that did
not exist; it was withdrawn before implementation. `play_music`'s docstring
now states the contract explicitly ("YOUR reply is the only announcement
the channel gets") instead of the old nudge-only framing, because the
consistency of the two paths now rests on her reply being *the*
announcement.

## Language policy (D4)

- **System announcements** (notify posts, slash replies): fixed English
  strings from the table. Deterministic, locale-independent, and the slash
  surface carries no LLM to localise.
- **Nora's conversational reply** (agent path): her own doctrine-register
  wording, as for every other tool — music is not special.
- The two never mix in one message. On the tag path the system posts
  nothing at session start, so her reply is the one announcement; on later
  transitions she is long gone and the system posts the fixed English
  line.

## The reading that would reopen this

A live transcript showing a tag-path play producing BOTH a system
"Now playing" post AND Nora's reply would mean a second notify path was
added without this node's contract — re-audit `_notify_now_playing`'s call
sites. Or: a user complaint that system English lines feel out of place in
a Cantonese room would reopen D4, with the fix being a locale parameter on
the table (one file), never per-surface rewording.