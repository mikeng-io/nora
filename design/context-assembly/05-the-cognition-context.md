---
type: design
title: "The Cognition context — what the session reads, chosen by what it decides"
description: "Proposed 2026-11-08. The Cognition session's input was built as its own parallel set of blocks, grandfathered by the gate meant to forbid it, and it cannot ground the judgements it is asked to make: it does not know who is in the room, who the people are, what she already learned, or what was said around the item. This node maps every decision the session makes to the input that grounds it, selects only what is related to the agenda, reuses the shared block builders engagement already uses, replaces the people/culture/character blocks with the profile blocks that already exist, fixes one naming and one empty rule for every block, and states the gate and the measurement that decide it."
tags: [nora, context, prompt, blocks, assembly, cognition, profile, naming]
timestamp: '2026-11-08'
status: implementing
edges:
  - {dst: 04-every-occasion.md, rel: extends, provenance: asserted}
  - {dst: ../unified-memory/29-one-cognition-session.md, rel: depends_on, provenance: asserted}
---

# The Cognition context

**Status: source implemented; independent review and semantic acceptance pending.** Source and focused
validation coverage are recorded with the integrated implementation PR. The parent
owns independent review and the single approved recorded replay. The reading that would
reopen it is in §8.

## 1. What is wrong, measured

Measured on production and the source at `3568121da` (v7.16.4), 2026-11-08.

- **The session cannot say who is in the room.** No block lists the agenda
  rooms' members. A message row's `from` is a display name only, with no
  person id, against [ADR 0029](../../docs/adr/0029-a-prompt-block-names-its-subject-and-author-by-id.md)
  (a block names its subject and author by id). One of its own notes, live in
  `standing`, records that `group_members` could not be read and
  `room_state_read` had no record, «so I didn't count people».
- **It knows nothing about the people it writes facts about.** No person,
  room or unit account reaches it. The only profile it can receive is her own:
  `render_agenda` renders `grounding.self_profile` through the shared
  `render_profile` when the grounder read one, else the prose `self`
  (`nora/language/cognition_render.py` ~1073–1096). The profile store holds a
  fresh profile for 16 people, 19 rooms, 3 units and Nora's 3 (updated
  2026-11-06 to 11-08); the person, room and unit ones are not in the
  session's input.
- **It reads the conversation by a tool nobody planned.** In the largest
  session of 2026-11-07, the injected input was 14.6k characters (agenda
  3.4k); its first call was `load_transcript`, returning 42k characters.
- **Its blocks are its own.** `cognition_render` builds `note_index`, `events`,
  `memos`, `standing`, `graph`, `clock` and `reads` itself. Engagement reads
  the same note store as `notes`, a second block type with a second shape.
- **Empty means three different things.** `note_index` and `memos`
  self-close when empty; `events` and `graph` are omitted; `memos` is also
  omitted when there are no lines. An omitted block cannot be told apart from
  a block that was not provided.
- **The gate grandfathered it.** `tests/architecture/test_one_context_composer.py`
  (2026-11-04/06) froze 27 raw-text composers as a «historical ceiling»,
  including `cognition_render:render_agenda`, `_register`, `_item_blocks` and
  `comprehend_render:render_comprehend`/`render_reshape`. AGENTS.md forbids a
  debt ledger and any exception for an occasion; that list is one. The other
  gates check the mechanism (every block through `ContextBlock`, every type
  declared), so two types for one store both pass.
- **Every tool schema is resident.** Engagement defers tools (`[llm]
  defer_tools = true`, `tool_catalogue`); Cognition loads about 33 read tools
  and about 20 write verbs up front.

The [every-occasion track](04-every-occasion.md) put Cognition onto
`ContextBlock` in October. It converted the mechanism; it never decided what
the session should read. That decision is this node.

## 2. The rule: a block earns its place by a decision

The session makes a closed set of decisions, written in its instruction
(`nora/language/comprehend_render.py` `SESSION_INSTRUCTION`) and objective
(`nora/dreamer/objectives/cognition.md`). A block is in the session's input
**only if a decision reads it**, and every decision has the input that grounds
it (AGENTS.md: if the judgement turns on *who*, the input carries an index; on
*when*, a time; on *whether still open*, the thing that would close it).
Copying engagement's list would be the opposite mistake: engagement's blocks
serve a reply in one room, most of them for no decision this session makes.

| Decision | What grounds it | Block |
|---|---|---|
| who a row is from, who a statement is about | the agenda rooms' members, by id and every name they are called | `members` |
| match an existing entity or create one; correct a contradiction | what is already known about the related people, rooms, unit and her | `profile` (one per related subject) |
| | the graph around the item's entities | `graph` |
| what an item states | the item's rows, by id, author id and send time | `agenda` |
| what the rows mean in their conversation, and whether something said in one room links to another | the unit's conversation around the items, every row tagged with its room | `transcript` (one block, §5) |
| which rooms exist, and where a read or a reminder can go | every room of the unit: id, label, platform, purpose, recent activity | `rooms` (§5) |
| write a note, or revise the one that exists | the open notes | `notes` |
| write a memo, or not | the settled memos | `memos` |
| propose or revise an event | the rooms' events | `events` |
| what she owes, holds open, already did | her standing | `standing` |
| move or cancel a reminder she wrote | her pending reminders in the agenda rooms, by id and due time (today: the `reminds` field on each note, `nora/app/cognition_grounding.py` ~769–797, scoped to agenda rooms because `remind_move`/`remind_cancel` refuse others) | `notes` (`reminds` field, kept) |
| whether something should reach a room as a reminder | the note or rows it rests on, and whether one is already pending | `notes`, `agenda` |
| set, move or cancel a self-wake | her pending self-wakes, by id, due time and subject: already in `standing`'s Waiting section (`nora/app/cognition_session.py` ~1342 passes them into the shared view, `nora/agent/shared_state.py` ~340 makes them pending `wake` entries, `nora/language/shared_render.py` renders them) | `standing` (kept) |
| how she should write, from past corrections | her lessons for the unit | `lessons` |
| reinforce or reword a held lesson, or retire one | the unit's held lessons, by `L` handle | `held_lessons` |
| write a lesson from a correction, or not | the corrections aimed at her since the cursor | `lesson_corrections` |
| write a lesson from how people responded, or not | the closed `response` episodes past the cursor | `lesson_episodes` |
| when, and how long ago | now, and her last session | `clock` |
| which rooms the read tools look through | the read scope | `reads` |
| detect documentation superseded by agenda evidence; read its current revision and revise it | the agenda-room shared document index, then its full current revision and cited rows | `documents`, `agenda`, `transcript` |
| which tool to reach for | the deferred catalogue | `tool_catalogue` |

**Read but not injected**, each with the reading that would add it:

| Engagement block | Why not | Add it if |
|---|---|---|
| `presence`, `computer`, `subagents`, `spoken_turn`, `replying_to`, `tag_message` | facts about one reply turn; no Cognition decision reads them | a decision is added that does |
| `peer_card`, `peer_history`, `people_in_window`, `character`, `culture`, `unit_culture`, `self` | the same subjects, from four older stores; `profile` replaces all seven (§4) | — |
| `recalled` | the reply-side pre-fetch; the session's own `graph` serves its seeds | the two are merged into one retrieval block |

## 3. Selection: related, never everyone

Every subject block is selected from the agenda, by code, before the session
starts.

- **Rooms**: the agenda's rooms. Each gets its `members` and its room `profile`.
- **People**: the persons who wrote a row in an agenda item, are mentioned in
  one (`mention_persons`), or are a resolved alias of a name in one. Each gets a
  person `profile`. Room members who are not related get their `members` line
  only (who they are), not a profile.
- **The unit**: one unit `profile`.
- **Nora**: one `profile` (her own, the one `self` shows today).

Bound by the existing profile card bounds; when the related set exceeds the
budget, the people with the most rows in the agenda come first and the block
says how many were left out (`not_listed`), as every list block does.

## 4. Profile replaces the people, culture and character blocks

The switch already exists for engagement (unified-memory node 45 §5,
`nora/agent/prefill.py` `_PROFILE_REPLACES`): with `[memory.profile] read`, one
`profile` per entity replaces `self`, `peer_card`, `peer_history`,
`people_in_window`, `culture`, `unit_culture` and `character`. The renderers are
shared (`nora/language/profile_render.py` `render_profile`,
`render_profile_people`) and are not on the legacy list.

**Cognition reads profiles from the start, whatever that flag says.** The
flag governs the reply surface, where Honcho is still the default and the
switch has its own acceptance (45). The session is the one that writes
profiles; it already reads them by tool (`profile_read`) before reshaping.
Injecting the related ones makes that read the default instead of a call it
can skip. `of` retains the canonical source/tool id; `entity` carries its kind
separately. Room profiles use the stored primary id in every occasion, while
readable room names remain in the room and membership context. A kind prefix
is not prepended to a person id.

Engagement's switch stays a separate decision, on its own acceptance. This
node does not flip it.

## 5. The transcript and the rooms

**One `transcript` block for the unit, not one per room** (ruled 2026-11-08).
Rows from every room of the unit are interleaved in time order, and every row
carries its room as an attribute (`room="<label>"`) beside its id, author id
and send time, so a reply in one room to something said in another reads as
the conversation it is. The agenda items' rows are marked in place
(`item="<index>"`); the rows around them are context. One renderer (the one
`group_transcript` uses; the shared media join applies).

- **What is in it**: for each agenda room, the rows before and around the
  item; from the unit's other rooms, the rows in the same time span written
  by, or mentioning, a related person (§3). Not every row of the unit: the
  cross-room link this exists for is «the same people, elsewhere, at the same
  time».
- **Its size is dynamic and adaptive** (ruled 2026-11-08), with no fixed row
  count. Each room's share is one more need in the allocation the agenda
  already uses (`cognition_render._shares`): the smallest need is met first,
  a floor keeps a quiet room from being starved, and unused budget flows to
  whatever needs it, under one hard total. A room with no related rows asks
  for nothing; a burst across rooms takes the budget a quiet agenda leaves.
  What was cut is said (`earlier_not_shown`, `not_listed`), never silent.

**A `rooms` block lists every room of the unit**, not only the agenda's: id,
label, platform, purpose and recent activity, the same reader `room_list`
uses (`nora/agent/tools.py` `room_list`). Today the session is told only the
agenda rooms (`reads`, built in `nora/app/cognition_session.py` ~1741) and
has no `room_list` in its catalogue (`nora/app/cognition_wiring.py`
`COGNITION_READ_CATALOGUE`), so a room with nothing new this wake does not
exist for it.

**Read scope: the unit, resolved through the item's room.** The session
already reads across the unit: DEC-231 makes the unit its read boundary, and
`record_query scope="all_rooms"` and `search_memory_workspace` read the
unit's other rooms (`nora/app/cognition_session.py` ~1721–1736). The
`agenda_room` argument is not a read limit; it picks the binding (roster,
lineage) a read resolves through, and DEC-231 keeps that: each item's
disclosure resolves through its own room, and what reaches a room is decided
by lineage at assembly. Grounding checks every derived note against every
agenda room and withholds it when lineage is on
(`nora/app/cognition_grounding.py` ~747–758, ~782–788). This node keeps all
of that unchanged:

- the unit `transcript` and `rooms` blocks are read under the unit boundary,
  and each row and each room line passes the same lineage/derived-view check
  against the agenda rooms that notes pass today, withheld and counted when
  lineage is on;
- reads keep resolving through an agenda room's binding; the `rooms` block
  makes the unit's other rooms visible as destinations and as `scope`
  targets, not as new bindings;
- the write rule (a line rests only on what it cites; what one room said
  does not surface in another) stays as a second guard, never as the
  disclosure check itself.

The unit is the read boundary, so nothing crosses units. R-18/R-28 screening
applies as on every transcript. `load_transcript` stays as a tool for looking
further back.

## 6. Naming and empty: one rule for every block

These apply to every occasion, not just this one; Cognition is the first
consumer converted to them.

1. **A block is named for what it holds, never for the loop that reads it.**
   One store, one block type, everywhere: `notes`, not `note_index` beside
   `notes`. A field one occasion needs (`review`, `may_duplicate`, `reminds`)
   is a field of the one type, rendered where it is filled.
2. **The subject is an attribute, not a name.** `profile of="person:<id>"`,
   `of="room:<id>"`, `of="unit:<id>"`, `of="nora"`, never `unit_culture` or
   `peer_card`. `members room="<label>"`, never a per-room block name.
3. **Ids, not names, identify** (ADR 0029). A row's author carries the person
   id beside the name; `members` lines carry the id, the name and the aliases.
4. **Empty is said, never omitted, and unavailable is not empty.** A block
   declared for the occasion always renders, in one of R-27's three states:
   with content; empty, self-closed, meaning the store holds none («no open
   notes»); or `status="unavailable"`, self-closed, meaning the read failed
   and nothing can be said about the store. The objective already teaches the
   difference for tool reads (`nora/dreamer/objectives/cognition.md`
   `what_you_are_reading`); the blocks carry the same three states. A block
   not declared for the occasion is simply not in the declaration; there is
   no omitted-by-accident state.
5. **Every type describes itself**, including `graph` and `reads`, which have
   no field description today.

Renames apply on engagement in the same change where the store is the same
(`note_index` → `notes`). The profile names arrive with §4. A rename that
would change the reply prompt is measured by the same replay (§8).

## 7. The build, as one change

One integrated change from this spec, measured once (AGENTS.md):

1. One Cognition context assembly over the shared builders: `rooms`,
   `members`, `profile`, `transcript` (one unit block), `notes`, `memos`, `events`,
   `standing`, `lessons`, `clock`, `reads`, `documents`, `tool_catalogue`; Cognition keeps
   only `agenda` and `graph` as its own types.
2. Delete Cognition's entries from `LEGACY_COMPOSERS` and `_INITIAL_LEGACY`
   (`cognition_render:render_agenda`, `_register`, `_item_blocks`). The
   `comprehend_render` entries have no production caller; delete them with
   their functions.
3. Deferred tools on the session through the shared `ToolSurface`. Documents
   use the existing source, typed index, renderer and service. The index is
   injected; full bodies are read on demand. `document_list`, `document_read`
   and `document_revise` are the autonomous surface. Revision changes existing
   string content only, preserves unrelated content, and requires a prior read,
   its exact base revision and ids of completely shown sources. Clipped source rows
   cannot authorize a revision because an omitted qualifier is unknown. A stale revision is refused,
   never retried automatically.

   Autonomous authority is an explicit session capability, not a human
   requester. It reaches only active documents shared with the entire relevant
   agenda room under current explicit, nonempty named-audience policy, with all
   named people known. Wildcard, missing, unknown and other-unit audiences deny.
   The same authorization predicate filters the index before its limit and
   rechecks ids and handles on reads and writes. Creator-only metadata and
   counts never enter the index or an error. Human-requester access is unchanged.
   The original creator stays on the document; revision history identifies the
   Cognition session and cited sources. Creation and lifecycle changes are outside
   this authority. Replacements quote shown evidence; decisions must preserve
   uncertainty. The service validates and persists through the existing publish
   path and compare-and-swap; the tool reads the saved revision back.
4. Gates, each failing on the defect it guards:
   - one block type per store across occasions;
   - a declared block always renders, in exactly one of content / empty /
     unavailable, and a failed read renders unavailable, never empty;
   - every block an occasion reads has a decision in that occasion's table
     (this node §2 for Cognition), discovered from the declaration, not listed.

## 8. Measurement and what would reopen it

**Acceptance**: one recorded replay on the same five-session sample against the
#637 baseline (10 saved / 8 supported / 2 unsupported / 3 missed / 1 refusal),
same scorer and judges, plus, from the recorded trajectories:

- `load_transcript` calls per session (expected to fall);
- `unknown_subject` and `wrong_ends` refusals (expected to fall, from ids);
- tokens per session (the input grows; the transcript calls shrink).

The judges read the same input the session read, including the new blocks, so
a fact grounded in the transcript or a profile is scored against it.

**Reopens** if the replay shows no movement in supported or missed facts with
the related context present: then the limit is the model's judgement, not its
input, and the next lever is the instruction (the memory/notebook split).

## 9. Ruled by the operator (2026-11-08)

- **The transcript** is one unit block, interleaved across rooms with each
  row tagged by its room, and its size is dynamic and adaptive inside the
  agenda's one allocation (§5). The replay reads its cost; the one total is the only
  number the build sets.
- **Documents** are the shared agenda-room index and deferred read/revision
  surface described in §7. Creator-only documents remain private.
- **Engagement's profile switch** (§4) is separate work, not part of this
  build.
