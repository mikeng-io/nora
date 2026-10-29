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

A new optional field on `[[conversations]]`: `music_channel = "<channel_id>"`.
The room's guild is matched; the named channel — a **sibling** in that guild,
not the room's own channel — is where the guild's music announcements are
posted, for every session in that guild.

`policy.toml` rather than `config.toml`'s `[music]` section because the answer
is about a **room's people**: it belongs beside the room's trust flags, the
same argument `Conversation.description` makes.

### Why a field and not a `voice = true`-shaped flag

The first implementation used a boolean — `music = true` claiming the declaring
room's own channel, exactly mirroring `voice = true`. It was built and gated
before anyone named a real channel; then the operator named two (2026-09-21,
Zone 42 and Ridgeline) and **neither was an allowlisted room**. A dedicated music
channel is not the guild's discussion room — that is what "dedicated" means — so
the flag shape forces a SECOND `[[conversations]]` entry per guild.

**The argument that holds.** A `[[conversations]]` entry is a bundle of answers
*about people*: `persons` (who may trigger her), `unit` (which rooms are one
social unit), `location` (whose positions may be disclosed here), `proactive`
and `relevance_gate` (may she speak uncalled), `home_room` (where she stands for
the unit). Every one of them is meaningless for a channel that is written to and
never read. Listing an announcement destination means inventing trust answers
about a non-conversation. That is the whole reason, and it needs neither of the
two below.

**Two consequences, both weaker than this node first claimed.** Each was written
as load-bearing and each is thinner than it reads (architecture review + audit,
2026-09-21):

1. *Historical DEC-105 rationale, superseded by room-topology migration step 3:
   unique room labels now replace the platform-count limit.* A unit held at most one room per platform, and `friends` and
   `ridgeline` each already hold their Discord room — but `unit` is free-form with
   a `"nora"` default and **nothing in code enforces DEC-105**, so a second
   entry could have carried its own unit and breached nothing. The real cost is
   then a spurious social unit in memory and culture. A choice between costs,
   not a violation.
2. *Ingest.* "A channel of `Now playing:` lines would become room speech and
   feed the Dreamer" is **false**: `nora/platform/discord/parser.py` drops
   Nora's own sends before the pipeline. What a listed music channel would
   actually ingest is other humans' and other bots' chatter there — a real cost,
   and the one the relay-bot lesson is about, but not the one claimed.

**Enforced, not documented.** "The destination is not a room" was written in
five places and checked in none, and the test naming it compared two of its own
literals so it could not fail. `load_policy` now REFUSES a `music_channel` that
is itself a listed conversation id in that guild — including the declaring
room's own channel, which is a listed conversation like any other. Announcing
into the discussion room is what leaving the field unset already does.

The value is a **bare channel snowflake, digits only**, in the declaring room's
own guild. Not a `<guild>/<channel>` pair — that reads plausibly and would post
one guild's announcements into another — and not a channel name, because names
are not stable ids. Both are startup errors. A blank string normalises to
absent, so a line that meant nothing does not refuse startup.

**Still one answer per guild.** Discord allows one voice connection per guild,
so `MusicPlayerManager` holds one player and one notify target per guild. At
most one room per guild may carry the field; a second is a startup error, not a
preference to resolve. A non-Discord room carrying it is likewise refused —
music is guild-resolved and no other platform reaches this path.

**Unset is the fallback, not a refusal.** This is the one place `music_channel`
deliberately differs from `voice`: a guild that has not opted in keeps the
where-it-started behaviour exactly, so no deployment changes by upgrading.
Speech had no prior behaviour to keep — there was never anywhere to put an
utterance other than the room the operator named — which is why `None` there
means 400 and `None` here means "announce where it started".

## D2 — system announcements only

`music_channel` moves the posts **nobody asked for**: `Now playing:` on a
natural track-end, `Autoplaying:`, the next track after a skip, the
connection-lost teardown. Everything reached through
`MusicPlayerManager._notify_now_playing` and `_announce_teardown`.

It does **not** move:

- **A slash command's own reply.** The person who typed `/music skip` is
  looking at the channel they typed it in; answering somewhere else would be
  worse than the split it would fix. (This once also covered the
  `_NowPlayingControls` button panel, which travelled with that reply for the
  same reason. The panel was REMOVED 2026-09-22 — music is driven by asking
  Nora or by a slash command, and a third control surface was not worth the
  interaction deadline it kept losing. Only the reply itself remains.)
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

**Resolved on every post, not captured at session start.** The resolver reads the live policy holder. It DOES mean a boot resume honours a `music_channel` set
while Nora was down (fresh policy, old session), and that there is one code path
where there were three. Valid edits to `music_channel` and `voice` now apply mid-session after the
next validated policy reload, within three seconds. The original 2026-09-21
implementation required recreation; the policy-reload design supersedes that
limitation.

The resolution order is: the guild's `music_channel`, else
`MusicSession.notify_channel_id` (which keeps its meaning — "the channel where
it started" — and becomes the FALLBACK rather than the destination). A named
room that does not resolve right now (deleted, or the bot lost access) falls
back rather than dropping: a misrouted announcement is recoverable, and a
session that goes silent is the exact failure this path was built for
(2026-09-15, `connection_lost_line`).

**Nothing in the notify path may raise.** `_notify_now_playing` is awaited at
the end of `skip()` and `on_track_finished()` and guards neither call, while
`_announce_teardown` wraps its own — so the manager already disagreed with
itself about whether a `Notify` may raise. An escape was traced all the way out
(audit, 2026-09-21): a `Forbidden` from `send` left `/music skip`'s deferred
interaction unresolved — "The application did not respond" while the skip had
actually happened, re-opening the B-3 defect — and turned every natural
track-end into an HTTP 500 back to the audio-controller with the event
unconsumed, freezing the `last_event_seq` baseline. `make_notify` is where that
becomes true rather than assumed: the send is caught, a routed channel that
refuses the write is retried once against the fallback, and the whole body is
wrapped, because `resolve_music_channel` and the client getter are
caller-supplied too.

**The guild is checked at send time, because that is the first moment it is
knowable.** `policy.toml` refuses a `<guild>/<channel>` pair and its error
message claimed that prevented cross-guild routing. It does not: a BARE
snowflake from another guild has the identical shape, and
`ConnectionState.get_channel` searches private channels FIRST and then every
guild the bot is in — and a `DMChannel` is `Messageable`. So one guild's
announcements could post into another, or as a DM, with the `falling back to`
warning never firing because resolution SUCCEEDED. Digits-only blocked the
syntax that expresses cross-guild routing, never the capability. `_messageable`
now compares the resolved channel's own guild and treats a mismatch as
unroutable, which routes it into the fallback that already existed.

**A closed client is not `None`.** `DiscordAdapter.listen` builds a fresh
`discord.Client` per connection attempt and closes the old one, while this
closure lives in `MusicPlayerManager._notify` and outlives the attempt — and
discord.py deliberately keeps its channel cache on close ("users rely on cache
being available post-close"), so a closed client passes an `is None` guard,
resolves a real channel, and fails at `send`. Nothing re-registers the closure
(`play` installs one only when `is_new_session`; `resume_persisted_session`
returns early on a live player), so the failure would be every announcement for
the rest of the session. Two fixes: the surfaces pass
`DiscordAdapter.current_client` rather than a lambda over a captured client,
and `_live_client` treats `is_closed()` as absent.


No migration. `notify_channel_id` is still written and still means what it
meant, so nothing in the schema moves.

## What this deliberately does not do

- **No room gate on `/music`.** The slash commands stay guild-gated. Closing
  that would change who may start music, which is a different decision from
  where its announcements go, and it needs its own evidence.
- **No per-guild music channel in `config.toml`.** See D1 — the answer is
  about a room's people.
- **No startup check that the named channel EXISTS.** `policy.toml` is loaded
  before the Discord client is, so it genuinely cannot run there; `make_notify`
  falls back and logs instead. Note what is NOT in this item: *is this channel
  in this guild* and *did the send succeed* are send-time questions, and both
  are now checked — the first draft of this section bundled all three as
  "cannot be checked", which is how the two cheap ones went missing.
- **No startup error for a `music_channel` whose guild has no sidecar.** It is
  dead configuration — `[speech.tts] sidecars`, not `policy.toml`, gates
  `/music play` — but the two files change on different cadences, so naming the
  channel before wiring the sidecar is correct-but-early.
  `build_dependencies` logs a warning.
- **No command to report or change the routing.** `policy.toml` is the report;
  a `/music where` verb is mechanism for a question the file already answers.

## Corrected on the way through

**The flag shape, before it reached a deployment.** See D1. `music = true` was
implemented, gated and committed before anyone supplied a real channel id, and
the ids are what showed it could not express the ask. `voice = true`'s shape was
adopted because both capabilities are guild-resolved, and that similarity is
real — but voice's destination IS a room (speech is conversation) while music's
is not (an announcement is output). Mirroring the shape imported an assumption
the new case does not hold. A named example would have caught it at design
time; a symmetric-looking design did not.

**Three of this node's own arguments, on review.** The reload claim in D3, and
the DEC-105 and ingest claims in D1, were all written confidently and none
survived being checked against the code it cited. Each is corrected in place
above rather than deleted, because the corrected version is the argument that
actually holds and a reader who sees only the conclusion will re-derive the
wrong reason for it. The pattern is worth naming: all three were plausible
readings of files this node did not open.

`music_messages.py`'s module docstring claimed the agent path suppresses the
session-start post via `MusicPlayerManager.play(notify_on_play=False)`. That
parameter does not exist and never did — `09-message-conventions.md`'s own
"announcement contract" section withdrew the plan on reading `play()`, and the
docstring described it as shipped for three weeks. Fixed here because this node
re-reads the same contract and would otherwise have inherited the claim.

## The reading that would reopen this

A live transcript showing an announcement in a channel that is neither the
guild's `music_channel` nor the session's start channel would mean a
fourth notify path was added outside `make_notify` — re-audit
`MusicPlayerManager._notify`'s call sites. A `music_channel` whose
announcements stop arriving while playback continues would mean the fallback
is masking a resolution or a SEND failure; the `not usable`, `falling back to`
and `send to channel ... failed` warnings in `music_notify` are the lines to
grep for, and they say which half. And an operator asking to split
announcements per voice channel within one guild would reopen D1's
one-channel-per-guild rule — which would require Discord to allow more than one
voice connection per guild first, so it is not a near-term reading.
