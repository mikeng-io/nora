---
type: design
title: "Discord Music — Notification Routing"
description: "A dedicated music channel per guild, named in policy.toml rather than discovered from wherever the first play ran; one send-time resolver for every announcing surface."
tags: [nora, discord, music, notifications, policy, routing]
timestamp: '2026-09-21'
edges:
  - {dst: 02-control-surface, rel: extends, provenance: asserted}
  - {dst: 06-session-persistence, rel: extends, provenance: asserted}
  - {dst: 09-message-conventions, rel: extends, provenance: asserted}
---

# Discord Music — Notification Routing

## The problem this node closes

`09-message-conventions.md` made every music message say the same thing from
every surface. It said nothing about **where** a message goes, and that half
was never designed — it was discovered, once, by accident, per session.

`02-control-surface.md` set the rule: auto-advance announces "to whichever
text channel the session's **first** `/music play` was invoked from —
captured once, at session start". `06-session-persistence.md` then persisted
it as `music_session.notify_channel_id`. Three properties follow, and all
three were found by the operator live (2026-09-21, "我記得而家都幾混亂吓"):

1. **It is invisible.** Nothing surfaces where a guild's announcements are
   pointing. `/music queue` does not say, no command reports it, and the only
   evidence is a log line at the moment of a post.
2. **It is sticky.** Persisted, survives a restart via
   `resume_persisted_sessions`, and no command moves it. The only way to
   redirect is to end the session.
3. **It is unbounded.** `/music play` is gated by GUILD
   (`voice_join_decision` against `[speech.tts] sidecars`) and **never by
   room** — no `policy.toml` check runs on the slash surface at all. So the
   captured channel need not be an allowlisted room. Typed into a Discord
   voice channel's built-in text chat, `interaction.channel_id` is the VOICE
   channel's id, and the whole session then announces into a place with no
   room policy behind it.

And because the player is per-GUILD while the commands are per-CHANNEL, one
event routinely splits across two channels: `/music skip` in `#random` replies
`Skipped: X` there while `Now playing: Y` lands wherever the session started.

## D1 — the destination is named in `policy.toml`, not discovered

A new per-room boolean, `music = true`, on `[[conversations]]`. The guild is
matched; the channel half of that room's id is where the guild's music
announcements are posted, for every session in that guild.

`policy.toml` rather than `config.toml`'s `[music]` section, and a per-ROOM
flag rather than a per-guild channel id, because of what each choice buys:

- The answer is a **room**, so it belongs beside the room's trust flags — the
  same argument `Conversation.description` makes for living in policy rather
  than config.
- Routing through the allowlist closes problem 3 **by construction**: an
  announcement can only land somewhere Nora is already permitted to speak.
  A channel id in `config.toml` would have re-opened it.
- `voice = true` already established the shape one layer down: a Discord
  capability that is guild-resolved, at most one room per guild, a second
  being a startup error rather than a guess. Music is the same shape for the
  same structural reason — Discord allows ONE voice connection per guild, so
  `MusicPlayerManager` holds one player and one notify target per guild, and
  "which channel" has exactly one answer.

**Unset is the fallback, not a refusal.** This is the one place `music_room`
deliberately differs from `voice_room`: a guild that has not opted in keeps
the where-it-started behaviour exactly, so no deployment changes by upgrading.
Speech had no prior behaviour to keep — there was never anywhere to put an
utterance other than the room the operator named — which is why `None` there
means 400 and `None` here means "announce where it started".

## D2 — system announcements only

`music = true` moves the posts **nobody asked for**: `Now playing:` on a
natural track-end, `Autoplaying:`, the next track after a skip, the
connection-lost teardown. Everything reached through
`MusicPlayerManager._notify_now_playing` and `_announce_teardown`.

It does **not** move:

- **A slash command's own reply.** The person who typed `/music skip` is
  looking at the channel they typed it in; answering somewhere else would be
  worse than the split it would fix. The `_NowPlayingControls` panel stays
  with its reply for the same reason — it is a control, not an announcement.
- **Nora's conversational reply on the tag path.** A reply lands in the room
  she was tagged in, for every tool; music is not special
  (`09-message-conventions.md` D4).

So `09`'s announcement-contract table is unchanged. Only the notify path's
channel moves, and the split named above narrows rather than closing: the
`Skipped: X` reply still lands where it was typed. That is the intended
outcome, not a gap — the reply is addressed to a person, the announcement to
the room.

## D3 — one resolver, `music_notify.py`, consulted at send time

Three surfaces each built their own notify closure and each answered "which
channel" differently: `/music play` off `interaction.channel`, the agent
bridge off the `conversation_id`'s channel half, boot resume off the persisted
row. They agreed by accident, not by construction. `music_notify.make_notify`
is now the only builder, and all three call it —
`music_messages.py` for the WORDING, `music_notify.py` for the DESTINATION,
each single-owner so a surface can only change either by changing one file.

**Resolved on every post, not captured at session start.** `music = true` is
a configuration fact, not a session fact. Capture-time resolution is precisely
what made the old routing invisible and uncorrectable; a send-time lookup
means an operator edit takes effect on the next announcement instead of the
next session, and a boot resume honours a room that was set while Nora was
down.

The resolution order is: the guild's `music = true` channel, else
`MusicSession.notify_channel_id` (which keeps its meaning — "the channel where
it started" — and becomes the FALLBACK rather than the destination). A named
room that does not resolve right now (deleted, or the bot lost access) falls
back rather than dropping: a misrouted announcement is recoverable, and a
session that goes silent is the exact failure this path was built for
(2026-09-15, `connection_lost_line`).

No migration. `notify_channel_id` is still written and still means what it
meant, so nothing in the schema moves.

## What this deliberately does not do

- **No room gate on `/music`.** The slash commands stay guild-gated. Closing
  that would change who may start music, which is a different decision from
  where its announcements go, and it needs its own evidence.
- **No per-guild music channel in `config.toml`.** See D1.
- **No command to report or change the routing.** `policy.toml` is the report;
  a `/music where` verb is mechanism for a question the file already answers.

## Corrected on the way through

`music_messages.py`'s module docstring claimed the agent path suppresses the
session-start post via `MusicPlayerManager.play(notify_on_play=False)`. That
parameter does not exist and never did — `09-message-conventions.md`'s own
"announcement contract" section withdrew the plan on reading `play()`, and the
docstring described it as shipped for three weeks. Fixed here because this node
re-reads the same contract and would otherwise have inherited the claim.

## The reading that would reopen this

A live transcript showing an announcement in a channel that is neither the
guild's `music = true` room nor the session's start channel would mean a
fourth notify path was added outside `make_notify` — re-audit
`MusicPlayerManager._notify`'s call sites. A `music = true` room whose
announcements stop arriving while playback continues would mean the fallback
is masking a resolution failure; the `falling back to` warning in
`music_notify` is the line to grep for. And an operator asking to split
announcements per voice channel within one guild would reopen D1's
one-room-per-guild rule — which would require Discord to allow more than one
voice connection per guild first, so it is not a near-term reading.
