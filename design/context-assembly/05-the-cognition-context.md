---
type: design
title: "The Cognition context — what the session reads, chosen by what it decides"
description: "Proposed 2026-11-08. The Cognition session's input was built as its own parallel set of blocks, grandfathered by the gate meant to forbid it, and it cannot ground the judgements it is asked to make: it does not know who is in the room, who the people are, what she already learned, or what was said around the item. This node maps every decision the session makes to the input that grounds it, selects only what is related to the agenda, reuses the shared block builders engagement already uses, replaces the people/culture/character blocks with the profile blocks that already exist, fixes one naming and one empty rule for every block, and states the gate and the measurement that decide it."
tags: [nora, context, prompt, blocks, assembly, cognition, profile, naming]
timestamp: '2026-11-08'
status: proposed
edges:
  - {dst: 04-every-occasion.md, rel: extends, provenance: asserted}
  - {dst: ../unified-memory/29-one-cognition-session.md, rel: depends_on, provenance: asserted}
  - {dst: ../unified-memory/45-persona-adaptor.md, rel: depends_on, provenance: asserted}
---

# The Cognition context

**Status: proposed, for review.** Nothing here is built. The reading that would
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
| what the rows mean in their conversation | the rows around them in the same room | `transcript` (room window) |
| whether something said in one room links to another | what the unit's other rooms said in the same window, about the same people | `transcript` (unit window), §5 |
| write a note, or revise the one that exists | the open notes | `notes` |
| write a memo, or not | the settled memos | `memos` |
| propose or revise an event | the rooms' events | `events` |
| what she owes, holds open, already did | her standing | `standing` |
| move or cancel a reminder she wrote | her pending reminders in the agenda rooms, by id and due time (today: the `reminds` field on each note, `nora/app/cognition_grounding.py` ~769–797, scoped to agenda rooms because `remind_move`/`remind_cancel` refuse others) | `notes` (`reminds` field, kept) |
| whether something should reach a room as a reminder | the note or rows it rests on, and whether one is already pending | `notes`, `agenda` |
| set, move or cancel a self-wake | her pending self-wakes, by id, due time and subject: already in `standing`'s Waiting section (`nora/app/cognition_session.py` ~1342 passes them into the shared view, `nora/agent/shared_state.py` ~340 makes them pending `wake` entries, `nora/language/shared_render.py` renders them) | `standing` (kept) |
| how she should write, from past corrections | her lessons for the unit | `lessons` |
| when, and how long ago | now, and her last session | `clock` |
| which rooms the read tools look through | the read scope | `reads` |
| which tool to reach for | the deferred catalogue | `tool_catalogue` |

**Read but not injected**, each with the reading that would add it:

| Engagement block | Why not | Add it if |
|---|---|---|
| `presence`, `computer`, `subagents`, `spoken_turn`, `replying_to`, `tag_message` | facts about one reply turn; no Cognition decision reads them | a decision is added that does |
| `documents` | the session decides nothing about documents, so no list is injected. Ruled 2026-11-08: it reaches them by tool instead, a list read and `document_read`, deferred like every other tool (§7) | a document verb joins its catalogue |
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

The switch already exists for engagement ([45 §5](../unified-memory/45-persona-adaptor.md),
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
can skip.

Engagement's switch stays a separate decision, on its own acceptance. This
node does not flip it.

## 5. The transcript

Two windows, one renderer (the one `group_transcript` uses; the shared media
join applies):

- **Room window**: for each agenda room, the rows before the item's first row,
  so the item's rows are read in their conversation. Bounded by rows and
  characters; `earlier_not_shown` says what was cut. This replaces the
  unplanned `load_transcript` call as the default.
- **Unit window**: rows from the unit's *other* rooms in the same time window
  that are written by, or mention, a related person (§3). Not the whole unit:
  the link this window exists for is «the same people, elsewhere, at the same
  time». Its size is **dynamic and adaptive** (ruled 2026-11-08), with no
  fixed row count. It is one more need in the allocation the agenda already
  uses (`cognition_render._shares`): each item and each window asks for what
  its rows need, the smallest need is met first, a floor keeps a quiet window
  from being starved, and unused budget flows to whatever needs it, all under
  one hard total. A window with no related rows asks for nothing; a burst
  across rooms takes the budget a quiet agenda leaves. What was cut is said
  (`earlier_not_shown`, `not_listed`), never silent.

The unit is the read boundary (DEC-231), so the unit window crosses rooms only
inside one unit. R-18/R-28 screening applies as on every transcript.

`load_transcript` stays as a tool for looking further back.

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

1. One Cognition context assembly over the shared builders: `members`,
   `profile`, `transcript` (both windows), `notes`, `memos`, `events`,
   `standing`, `lessons`, `clock`, `reads`, `tool_catalogue`; Cognition keeps
   only `agenda` and `graph` as its own types.
2. Delete Cognition's entries from `LEGACY_COMPOSERS` and `_INITIAL_LEGACY`
   (`cognition_render:render_agenda`, `_register`, `_item_blocks`). The
   `comprehend_render` entries have no production caller; delete them with
   their functions.
3. Deferred tools on the session, through the same `ToolSurface` engagement
   uses. The document reads join the catalogue: `document_read`, which exists,
   and a list read over the same source the `documents` block renders
   (`nora/language/documents_render.py`), so there is one reader of
   documents, not two.
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

- **The unit window** is dynamic and adaptive, inside the agenda's one
  allocation (§5). The replay reads its cost; the one total is the only
  number the build sets.
- **`documents`** stay out of the injected input; the session gets a list
  read and `document_read` as deferred tools (§2, §7).
- **Engagement's profile switch** (§4) is separate work, not part of this
  build.
