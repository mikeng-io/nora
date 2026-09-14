---
type: design
title: "Online Status — digital activity state as a tool surface"
description: "One read-only tool, closed `what` vocabulary, for who is online / playing / in voice — the digital counterpart of where_is/where_been."
tags: [tool, presence, digital-activity, steam, discord, proactive]
timestamp: '2026-09-15'
edges: []
---

# Online Status — design overview

## Why

2026-09-14, Blake said in Discord Zone 42 that he was sick and wanted the
night off. Nora's note tier folded that into a friends-workspace note
(`90d5d715`, established_in Zone 42), and two hours later the **proactive**
fire path answered it into the **Signal** home room (#248 — the source room
and the delivery room diverged). The deeper failure: when the decide turn
reads a note saying "Blake sick", it has **no way to observe that Blake is
currently online and playing**. The group was mid-game; the note was stale
the moment it was written.

This bundle gives Nora a live-state read on the **digital activity dimension**
— who is online, who is playing what, who is in a Discord voice channel —
so her reasoning about "are these people actually doing the thing the note
says they cannot" can be grounded, not guessed.

## What this change is

- A **new read-only agent tool** with a closed `what` vocabulary, named
  after the capability ("who is online / playing / in voice"), NOT the
  mechanism (`steam_status` / `discord_status` are mechanism names — same
  class of error GLOSSARY and ADR 0004 forbid).
- Two live data sources: **Steam Web API** (`GetPlayerSummaries` keyed off
  the `steam_id`s already in identity.toml) and **Discord gateway voice
  state** (the `voice_states` intent Nora already holds — no privileged
  upgrade in v1).
- **Room-scoped audience, no guard machinery.** The tool answers ONLY
  about the invoking room's own members — the boundary is the roster
  itself. Deliberately NOT the `where_is`/`where_been` R-31
  `DisclosureGuard` machinery: digital activity is not a location data
  class, and the friend-circle already sees its own presence (operator
  2026-09-15). No new policy flag; R-27 core vocabulary only.
- Wiring so the **proactive decide turn** can consume it, which is the
  direct fix for the #248 failure family: a note's claim about someone's
  state should be checked against live state before it is spoken.

## What this change is NOT

- **Not spatial presence.** `where_is` / `where_been` own the physical
  location dimension (Life360-derived). This tool answers the *digital*
  dimension. `who_is_around` was rejected in discussion precisely because
  "around" collides with the spatial scope those tools already own.
- **Not rich presence / custom status.** Discord custom status and playing
  activity require the privileged `presences` intent and a developer-portal
  change. Out of v1; recorded as deferred (02-data-sources).
- **Not a history substrate.** This is live state only, like `where_is` is
  live position. A derived history of activity (so Nora can answer
  「琴日幾點一齊打緊機」 from her own record) would be a separate event
  substrate mirroring `where_been`'s role — v2, recorded in
  `_index.yaml` `scope.deferred`.
- **Not a participation change.** The lightweight participation
  determination stays no-tool (operator-established shape). The consumer
  is proactive (and eventually tag turns).

## Reading order

1. `01-tool-shape-and-naming.md` — capability shape, the `what` vocabulary,
   naming candidates + the DEC-58-style measurement gate.
2. `02-data-sources.md` — Steam API + Discord intents, what is already
   wired, what needs a new credential / intent.
3. `03-guards-and-disclosure.md` — scope and disclosure: the room's own
   roster is the boundary; no R-31 machinery, R-27 core vocabulary.
4. `04-consumers.md` — proactive decide consumption, the #248 tie-in,
   tool-surface policy (ADR 0007) obligations.

## Status checkpoint

Proposed. Awaiting operator decisions in `open_questions` (Steam key,
naming run timing). Implementation happens in a worktree per repo
convention; master receives only an approved merge.

## Open questions

- Steam Web API key: obtain a free key, or reuse an existing key in the
  Pulse stack?
- Naming: confirmed only after a DEC-39/DEC-58-style identifier run.
