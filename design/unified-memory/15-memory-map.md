# The memory map: every store is part of one memory

Status: **proposed**, 2026-10-14.

Operator, 2026-10-14: the memory system is not only identity and character;
notes, memos, media storage and the rest are all part of it. This node places
every store Nora has into one map, says what each is the memory *of*, and
says how each one is reached by the one search verb and by a correction.

## The map is the data model, with Honcho folded in

[cognition-loop/05](../cognition-loop/05-data-model.md) (DEC-249) already cuts
Nora's state into layers that each answer one question: Authored → Record →
Rollup → Notebook → Views, with Memory (Honcho) beside the flow. "One memory"
does not need a new topology. It needs three changes to that one:

1. **Nothing beside the flow.** What Honcho did moves into a layer or is
   dropped ([10](10-honcho-audit.md) §4).
2. **An Identity facet** for every entity, beside Character
   ([30](30-identity-and-character.md)). Character answers *what is it like*;
   Identity answers *who or what is it*, a question no tier answers today.
3. **One index across the layers** ([00](00-intent.md) part 1), so a reader
   asks one verb, not the store.

## Every store

| store | layer · tier | memory of | indexed as | how it is corrected |
|---|---|---|---|---|
| messages, edits, deletes, reactions | Record | what was said | one row per message, its own time | not edited: a delete withdraws it, and the retraction cascade (cognition-loop/80 §4.1) reaches everything derived from it |
| media bytes (`media_objects`) and their readings (`media_descriptions`) | Record | what was shown | the reading as text; a media vector only if [20](20-embedding-bakeoff.md) justifies it | a re-read replaces nothing; a new perception row is added |
| stickers (`sticker_index`) | Record | what she can send | no: a send-side catalogue, not a memory of anyone | — |
| external files | Record (the link only) | that a file was shared, and what was said about it | the link and its message | — (content is never kept, [external-files/00](../external-files/00-design.md)) |
| presence fixes, polls | Record | where people were | no: raw fixes are read through their episodes | — |
| social items, play samples (step 5) | Record | what people posted and played | one row per item | withdrawal on delete |
| face labels, `character_dispute` (designed) | Record | who is in a photo; what a subject disputed | no: they are inputs to derivation | the subject's own Record item |
| her tool calls and results, decisions, sends | Record | what she did | her sends yes; tool plumbing no | — |
| stays, trips, outings, co-locations, a person's day | Rollup · episode | what happened, bounded | one row per episode, with its interval | rebuilt from the Record, never edited |
| place, rhythm, tie, play profile | Rollup · aggregate | patterns over a window | no: numbers are read by key (`rollup_read`, `where_been`), not by meaning | rebuilt |
| note, memo, event | Notebook · Register | the state of the unit's affairs | one row per head revision | a later told row supersedes; the revision chain keeps the old |
| Character (person, room, unit, Nora) | Notebook · Character | what an entity is like | one row per section, so a hit lands on a paragraph, not a whole account | dispute (cognition-loop/50 §4) |
| **Identity** (new) | Notebook · Identity | who or what an entity is | one row per line | [30](30-identity-and-character.md) |
| Lessons | Notebook · Lessons | how she has learned to act | one row per lesson | the operator's retire on the portal |
| Standing | Notebook · Standing | where she stands | one row per section | its next fold |
| Tension | operator-only | — | **never**: code guarantees she cannot read it (cognition-loop/10 §1), and an index row would break that | — |
| doctrine, `identity.toml`, config, policy | Authored | what the humans fixed | no: it is served whole and wins over anything derived | edited by hand |
| a turn's blocks, a portal page | Views | — | no: never stored as data | — |

## Three rules the index keeps

- **Visibility is the source's, evaluated at read time.** An index row copies
  its source's lineage `(kind, subject, origin room)` and `exclude_persons`
  (cognition-loop/05 §3). The search filters by the reader's room **before**
  ranking, never after (conversational-recall/20).
- **A row is a pointer, not a copy of the truth.** The index holds text for
  matching and the source's id; the answer reads the source's current head.
  A superseded note never answers from a stale index row.
- **A withdrawal reaches the index.** The retraction cascade already marks
  derived revisions `unsupported`; the index drops their rows in the same
  transaction, or a deleted message stays findable by meaning.

## Notes, memos and media are not demoted

They keep their own verbs (`note_create`, `media_fetch`, `event_search`) for
what only they do: writing, fetching an original, reading typed columns. The
one search verb finds them; their own verbs act on them. That is the line
between *one memory* and *one table*. The second would erase the reason each
store has its own write rules.
