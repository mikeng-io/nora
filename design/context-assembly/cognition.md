# Cognition context

## Blocks follow decisions

Cognition's context is selected for the judgements its session makes, not by
copying the reply turn's blocks. Each decision has the input needed to ground
it:

- authorship and subject resolution read the agenda rooms' members and aliases;
- entity resolution and contradiction read related person, room, unit and Nora
  profiles plus the graph around the agenda;
- interpretation reads the agenda rows with identity, room and assertion time;
- conversation context reads an interleaved unit transcript whose rows retain
  their room;
- Register writes read the corresponding open notes, settled memos and events;
- reminder and self-wake changes read pending intentions with their subjects
  and due state;
- lessons, Standing and the clock ground practice, position and freshness;
- a lesson write reads the unit's held lessons by handle, the corrections
  aimed at her since the lessons cursor and the closed response episodes past
  it;
- the room catalogue and deferred tool catalogue name available destinations
  and reads without injecting unrelated inventories.

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
