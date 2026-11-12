# Cognition context

## Blocks follow decisions

Cognition's context is selected for the judgements its session makes, not by
copying the reply turn's blocks. Each decision has the input needed to ground
it:

| Decision | What grounds it | Block |
|---|---|---|
| who a row is from, who a statement is about | the agenda rooms' members, by id and every name they are called | `members` |
| match an existing entity or create one; correct a contradiction | what is already known about the related people, rooms, unit and Nora | `profile` (one per related subject) |
| | the graph around the agenda's entities | `graph` |
| what an item states | the item's rows, by id, author id and send time | `agenda` |
| what the rows mean in their conversation, and whether something said in one room links to another | the unit's conversation around the items, every row tagged with its room | `transcript` |
| which rooms exist, and where a read or a reminder can go | every room of the unit: id, label, platform, purpose, recent activity | `rooms` |
| write a note, or revise the one that exists | the open notes, with their pending reminders by id and due time | `notes` |
| write a memo, or not | the settled memos | `memos` |
| propose or revise an event | the rooms' events | `events` |
| what she owes, holds open, already did | her Standing, including pending self-wakes | `standing` |
| how she should write, from past corrections | her lessons for the unit | `lessons` |
| reinforce or reword a held lesson, or retire one | the unit's held lessons, by handle | `held_lessons` |
| write a lesson from a correction, or not | the corrections aimed at her since the lessons cursor | `lesson_corrections` |
| write a lesson from how people responded, or not | the closed response episodes past the cursor | `lesson_episodes` |
| when, and how long ago | now, and her last session | `clock` |
| which rooms the read tools look through | the read scope | `reads` |
| detect documentation superseded by agenda evidence; read its current revision and revise it | the agenda-room shared document index | `documents` |

A block exists only when a decision reads it. Documents are the exception in
kind, not in rule: the session decides whether agenda evidence supersedes
shared documentation, so the agenda-room document index is injected while
bodies and revisions stay deferred tools. A revision requires the document's
current revision read inside the session, its exact base and fully shown
sources — clipped rows cannot authorize one — and reaches only an active
document shared with the whole agenda room under an explicit named-audience
policy whose named people are all known. A stale base is refused, never
retried automatically; creator-only documents, their metadata and their
counts never enter the index or an error. Other large stores are pulled
through deferred tools unless a session decision specifically needs their
index. Related profiles replace older peer, culture and character blocks
rather than rendering several accounts of the same subject.

**Read but not injected**: blocks that serve one reply turn (presence,
computer, subagents, the spoken or replied-to turn) have no Cognition decision
that reads them, and the older peer, culture and character accounts are
replaced by `profile`. Either is added only when a decision reads it.

## Related selection

Code selects subjects from the agenda: its rooms, row authors, mentioned or
resolved people, the unit and Nora. Room members who are unrelated to the
agenda contribute identity lines only, not full profiles. Selection stays
bounded and reports who or what was left out.

The unit transcript interleaves rooms in time order. It includes the agenda's
surrounding conversation and related people's activity elsewhere in the unit,
not an unbounded dump of every room. Each row retains its room and agenda
association so a cross-room reply reads as one conversation.

A separate room catalogue lists every room of the unit with its purpose and
recent activity. It makes quiet rooms available as destinations without changing
the item-specific binding used for disclosure.

## One allocation

Agenda, profiles, transcript and other selected context share one adaptive
budget. Small needs are satisfied first, quiet rooms keep a floor and unused
space flows to bursts that need it. Truncation and omitted subjects are explicit;
a small pushed block cannot conceal what it left out.

The unit remains the read boundary. Each item's disclosure resolves through its
own room, and derived rows pass the same checks used elsewhere. Cross-room
transcript visibility does not widen what any destination may later be told.

## Naming and empty states

Stores keep one block type across occasions. A field needed only by Cognition
is a field of that type, not a parallel block name. Profile subjects are
attributes; per-room variants are not new type names. An entity reference
keeps the canonical source or tool id under `of` and carries the kind
separately under `entity`; a room profile uses its stored id and no kind
prefix is added to a person id.

Every declared block renders with content, empty or unavailable. An empty store
and a failed read therefore remain distinguishable, while an undeclared block
needs no placeholder. Block types explain themselves and every field the
renderer emits.

The reply surface's external-memory switch is independent. Cognition reads the
typed profiles it maintains; whether an older speaking-turn surface should
switch is a separate acceptance decision.
