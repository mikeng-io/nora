# Plan — Music message convention (wt/music-messages)

**Problem (Miles, 2026-08-29):** Nora's Discord music messages are inconsistent —
mixed languages, mixed verbs ("playing" vs "queuing" vs "started"), and the
slash-command path vs the agent (LLM) path announce differently.

**Done looks like:** one canonical message renderer for every surface; the
double-announcement on the agent path eliminated; design doc + decision-index
entry + tests; `make gate` green.

## Root cause (verified in code)

Three surfaces publish music messages, each with its own wording:

1. **Slash commands** (`nora/platform/discord/music_commands.py`) — hardcoded
   English: `Now playing: X — url` / `Queued: X — url` (`_reply_for` L120),
   `Skipped: X` (L348, **no URL**), `Now next: X` (L369, third verb),
   `Paused.` / `Resumed.` / `Stopped.` / `Volume set to N%.`
2. **Auto-notify** (`music_manager.py:341-354` `_notify_now_playing`) —
   `Now playing: X — url` / `Autoplaying: X — url`, silent channel post, fires
   on session start (slash path), skip, natural track end, and boot-resume.
   Both paths flow through here.
3. **Agent path** (tag → LLM → `play_music` tool) — the tool returns a JSON
   result `{status, title, url, queued}` (`tools.py:1584-1597`); the LLM words
   the reply itself in Cantonese per doctrine. Only nudged by a docstring
   ("say 'queued' or 'now playing' accurately", tools.py:4186) — no mechanism.

**Consequences:** language split (slash/notify always English, agent path
Cantonese); verb split (`Skipped:` without URL, `Now next:` off-canon); and on
the agent path a **double announcement** — the system posts `Now playing: X —
url` to the channel AND the LLM posts its own wording of the same fact.

## Decided (2026-08-29, Miles approved option 1)

**One canonical announcer + a single string table; the channel-level
announcement is owned by exactly one surface per event.**

### D1 — `music_messages.py`: one string table, one renderer

New module `nora/platform/discord/music_messages.py`. Every user-facing music
string lives there as pure functions over typed inputs:

- `now_playing_line(track, *, paused=False)` — moved verbatim from
  `music_commands._now_playing_line` (the existing canonical line,
  message-consistency pass 2026-08-26)
- `queued_line(track, queued_count)` — the `Queued:`/playlist-suffix branch
  out of `_reply_for`
- `skipped_line(track)` — **gains the URL**, fixing the current
  `Skipped: X` (title-only) off-canon drift: `Skipped: {title} — {url}`
- `next_line(track)` — replaces the bare-title `Now next:` reply;
  renders `Now next: {title} — {url}` (verb kept; URL added, same rule)
- `autoplay_now_playing_line(track)` — `Autoplaying: {title} — {url}`
- `nothing_playing_line()`, `paused_line()`, `resumed_line()`,
  `stopped_line()`, `volume_line(percent)`, and the error/rejection lines
  (`couldn't find`, `join a voice channel`, `music only works in a server`,
  `nothing queued at that position`, `not paused`, `couldn't join the voice
  channel`) — the full vocabulary, so no surface ever formats its own.

Pure, typed, unit-testable without discord.py objects — the same seam
`tests/unit/test_music_commands.py` already uses for `_reply_for`/
`render_page`.

### D2 — surfaces call the table; no surface formats its own strings

- `music_commands.py` drops `_now_playing_line` and `_reply_for`, imports the
  table. Controls view (`render`), queue paginator (`render_page`), and every
  slash reply switch to table functions.
- `music_manager._notify_now_playing` composes its line from the table
  (`now_playing_line` / `autoplay_now_playing_line`).
- `music_tool_bridge` outcome notes (`unavailable`/`nothing_found` notes)
  pull their wording from the table too, so the LLM-facing `note` and the
  channel line can never drift.

### D3 — double announcement: the agent path suppresses the system notify

> **WITHDRAWN during implementation** — see
> `design/discord-music/09-message-conventions.md` ("The announcement
> contract"). Reading `MusicPlayerManager.play` against
> `test_the_first_play_does_not_notify_the_channel_that_started_it` showed
> `play()` never posts a session-start announcement: the caller's own reply
> is already the only one. The proposed `notify_on_play` parameter would
> have solved a problem that does not exist. Kept below as written, for the
> record.

The LLM is Nora's authored voice; when the tag path starts/queues a track,
Nora herself says it — the system's silent `Now playing:` post is redundant
and is what makes the two paths look like two different bots. Mechanism, not
prose: `MusicPlayerManager.play` gains a `notify_on_play: bool = True`
parameter. When the tool bridge calls `play()`, it passes
`notify_on_play=False` for the session-start event; the auto-advance/skip
notifications for a session STARTED via the agent path stay ON (the LLM is
long gone by then — nobody would hear what starts next).

Suppression mechanics: `play()` only notifies on a fresh session via
`_notify_now_playing` after `_after_transition`. With `notify_on_play=False`
that call is skipped; `_notify` is still captured for later transitions
(unaffected: skip/track-end paths don't take the flag). The slash path passes
nothing → default `True` → behaviour unchanged.

Edge case: agent-path `play` that queues behind an existing session (`status
= "queued"`) never triggers `_notify_now_playing` anyway (no transition) —
nothing to suppress. Agent-path skip still notifies next-track (correct: the
skipped-track attribution is the LLM's job, the *next* track's start is the
system's).

### D4 — language policy, stated in the design doc

- **System announcements** (notify posts, slash replies): **English, fixed
  strings** — deterministic, locale-independent, and slash commands carry no
  LLM to localise.
- **Nora's conversational reply** (agent path): follows her doctrine register
  (Cantonese mix), as she already does for every other tool — music is not
  special.
- The two never mix in one message: system posts are system posts; Nora's
  reply is Nora's reply. With D3, a tag-path play produces exactly ONE
  announcement (hers).

### D5 — tool docstring updated to match the mechanism

`play_music`'s docstring keeps the `queued` flag guidance but drops the
"say 'queued' or 'now playing' accurately instead of guessing" framing where
it now describes a suppressed system message; it instead states that the
system does not announce an agent-path session start — Nora's reply IS the
announcement, so accuracy of the playing-vs-queued distinction is her job and
there is no second copy to contradict her.

## Not decided / deferred

- **LLM verbatim echo of rendered strings** (option 3 from the discussion) —
  rejected: fights Nora's authored-voice design.
- **Cantonese system announcements** — rejected per D4 (no LLM on that path);
  revisit only if a real evidence bar appears (e.g. users ask).
- **Announcements for pause/resume/volume** — none exist today; not adding
  any (no evidence bar).

## Workplan

1. `music_messages.py` string table + unit tests (move `_now_playing_line`,
   `_reply_for` logic; add `skipped_line`/`next_line` with URL).
2. Rewire `music_commands.py` (slash replies, controls, paginator) to the
   table; delete local helpers.
3. Rewire `music_manager._notify_now_playing` to the table.
4. Add `notify_on_play` param through `MusicPlayerManager.play`; tool bridge
   passes `False`; tests: manager test for the suppressed fresh-session
   notify; bridge test asserting it passes the flag.
5. Rewire `music_tool_bridge` outcome notes to the table.
6. Update `play_music` docstring in `tools.py`.
7. Design node `design/discord-music/09-message-conventions.md` (+ `_index.yaml`
   member/edge), decision-index entry (next free R-84 — verify at merge),
   named tests in `test_acceptance_rules.py::RULE_TESTS`.
8. `make gate` (lint, typecheck, imports, full test).

## Tests to touch/add

- `tests/unit/test_music_messages.py` — NEW: every table function's output,
  including `Skipped`/`Now next` now carrying URLs.
- `tests/unit/test_music_commands.py` — `_reply_for` tests move/retarget to
  the table; controls/paginator assertions unchanged (same strings).
- `tests/unit/test_music_player_manager.py` — new: `notify_on_play=False`
  suppresses the fresh-session announcement but later skip/track-end
  notifications still fire; slash path default unchanged.
- `tests/unit/test_music_tool_bridge.py` — bridge passes `notify_on_play=False`.
- `tests/unit/test_music_agent_tools.py` — docstring-behaviour tests if any
  assert on the guidance text.

## Deployment note

Doctrine/config untouched; pure code change → `make build` +
`docker compose up -d --force-recreate nora presence analytics` on merge
(three services share `nora:latest`).