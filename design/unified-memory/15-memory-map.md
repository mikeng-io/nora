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
2. **Persona** for every entity ([35](35-persona.md), ruled 2026-10-15): the
   Character tier widened to hold who or what the entity is, what it is like,
   and abduced hypotheses about why, one account with one writer.
3. **One index across the layers** ([00](00-intent.md) part 1), so a reader
   asks one verb, not the store.

## Every store

| store | layer · tier | memory of | indexed as | how it is corrected |
|---|---|---|---|---|
| messages, edits, deletes, reactions | Record | what was said | one row per message, its own time; an edit re-projects it | a platform delete sets `messages.hidden_at`: the message is never quoted, never a hit, and its node is dropped. What was derived from it stays and cites "a message since deleted" ([35](35-persona.md) §5; there is no retraction cascade since 2026-10-03) |
| media bytes and their readings (`media_objects`: the reading in `title`, `description`, `content`, keyed by `sha256`, no room, UPSERTed) | Record | what was shown | one node per **attachment** `(message, sha256)`, scoped by its message, with the reading copied in; a media vector only if [20](20-embedding-bakeoff.md) justifies it | a re-read UPSERTs the reading, and every attachment of that `sha256` is re-projected. There is no `media_descriptions` table: that module is a cache over `media_objects` and `sticker_index` |
| stickers (`sticker_index`) | Record | what she can send | no: a send-side catalogue, not a memory of anyone | — |
| external files | Record (the link only) | that a file was shared, and what was said about it | the link and its message | — (content is never kept, [external-files/00](../external-files/00-design.md)) |
| presence fixes, polls | Record | where people were | no: raw fixes are read through their episodes | — |
| social items, play samples (step 5) | Record | what people posted and played | one row per item | withdrawal on delete |
| face labels, `persona_dispute`, `feedback` (designed) | Record | who is in a photo; who disputed which line; what someone said about her act | disputes and feedback yes, as nodes with their edges; face labels no | the speaker's own Record item |
| her tool calls and results, decisions, sends; her own artifacts (documents, generated images) | Record | what she did and made | her sends and artifacts yes; tool plumbing no | — |
| stays, trips, outings, co-locations, a person's day | Rollup · episode | what happened, bounded | one row per episode, with its interval | rebuilt from the Record, never edited |
| response and act episodes ([50](50-learning-from-acts.md)) | Rollup · episode | how people answered what she did | one row per episode, `outcome_of` its act | rebuilt |
| place, rhythm, tie, play profile | Rollup · aggregate | patterns over a window | no node: numbers are read by key (`rollup_read`, `where_been`), not by meaning; `ask_memory` reaches them through the same key reads | rebuilt |
| note, memo, event | Notebook · Register | the state of the unit's affairs | one row per head revision | a later told row supersedes; the revision chain keeps the old. Notes gain citations in the build (00 «Sequence» step 4) |
| **Persona** (widens Character: person, room, unit, Nora) | Notebook · Persona | who an entity is, what it is like, why | one row per **line**, so a hit lands on one claim, not a whole account | a dispute ([35](35-persona.md) §2) |
| Lessons | Notebook · Lessons | how she has learned to act | one row per lesson | the operator's retire on the portal |
| Standing | Notebook · Standing | where she stands | one row per section; it stores no citations, so it has no `cites` edges | its next fold |
| Tension | operator-only | — | **never**: code guarantees she cannot read it (cognition-loop/10 §1), and an index row would break that | — |
| doctrine, `identity.toml`, config, policy | Authored | what the humans fixed | no: it is served whole and wins over anything derived | edited by hand |
| a turn's blocks, a portal page | Views | — | no: never stored as data | — |

## Three rules the index keeps

- **Visibility is the source's, evaluated at read time.** An index row copies
  its source's unit, origin room and `exclude_persons` (cognition-loop/05
  §3). The unit is the read boundary (`[lineage]` stays off, operator
  2026-10-03; [35](35-persona.md) §3), and evidence about a person counts only
  if it was said in a room that person is listed in. The search filters by the
  reader's scope **inside** the scan, before ranking, never after
  (conversational-recall/20, [25](25-memory-graph.md) §3).
- **A row is a pointer, not a copy of the truth.** The index holds text for
  matching and the source's id; the answer reads the source's current head.
  A superseded note never answers from a stale index row.
- **A hidden item leaves the index** (operator, 2026-10-15). A message deleted
  on the platform (`messages.hidden_at`) has its node dropped in the same
  write that hides it, so it is never found by meaning. Nothing derived from
  it is withdrawn: there is no retraction cascade (removed 2026-10-03,
  migrations 0092/0093), and lines that cite it stay, citing "a message since
  deleted" ([35](35-persona.md) §5).

**Person-input extension (proposed 2026-10-20):**
[45 §4a](45-persona-adaptor.md#4a-proposed-person-input-extension--2026-10-20)
is the single contract for wider Record/Rollup inputs and Register context
into the one person writer. Register entries are links/context, never
independent evidence; Persona is itself Notebook. The current exclusion of
person location remains: unit scope does not authorize presence across rooms.
That extension is not a receipt of shipped ingestion or model acceptance.

## Notes, memos and media are not demoted

They keep their own verbs (`note_create`, `media_fetch`, `event_search`) for
what only they do: writing, fetching an original, reading typed columns. The
one search verb finds them; their own verbs act on them. That is the line
between *one memory* and *one table*. The second would erase the reason each
store has its own write rules.
