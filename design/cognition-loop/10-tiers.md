---
type: design
title: "Tiers — the Notebook's four tiers (Register, Character, Lessons, Standing), the Record and Rollup layers beneath them, and the words"
description: "The Record and Rollup layers and the Notebook's four tiers (05-data-model.md fixes the layers; a tier is a subdivision of the Notebook only), each answering exactly one question, each with one write path, one key and one citation direction; every derived row carries its reasoning mode, its confidence, its lineage and its age. Lineage is a set of (kind, subject, origin room) per field, checked per room at assembly. Every store is append-only in system time, so Nora as of any past moment can be restored, memory excepted. Cognition is the loop, memory is Honcho, doctrine is authored, tension is the operator's. The terminology table and the words retired."
tags: [nora, tiers, terminology, record, rollup, register, character, lessons, standing, culture, lineage, reasoning-mode, versioning, as-of]
timestamp: '2026-09-28'
edges:
  - {dst: 20-observe, rel: sequences, provenance: asserted}
  - {dst: 25-rollup, rel: motivates, provenance: asserted}
  - {dst: 40-register, rel: motivates, provenance: asserted}
  - {dst: 50-character-and-standing, rel: motivates, provenance: asserted}
---

# 10 · Tiers

> 2026-10-28 (operator, ruled; [unified-memory/27](../unified-memory/27-the-memory-is-a-graph.md#9-profiles-are-reshaped-in-the-loop-from-the-graph)): the Character row's writer changes. The Cognition loop's step 2 writes traits and guesses; facts are rendered by code from the memory graph; the Character adaptor retires.

> 2026-10-03 (DEC-277): the shadow period is over. The Cognition session writes live (`[cognition] enabled`) and is the one background writer of notes, events, Standing and her reminders. The clocked note/proactive adaptors, the shadow machinery and `[cognition] mode` / `session_mode` / `shadow_session_seconds` named here are deleted; wording below that describes them is the plan as written, not current behaviour. See `70-migration.md`.


> 2026-10-21 (18 / ADR 0026): [18](../unified-memory/18-extraction.md) amends the topology below: Honcho is decomposed into the existing layers; Memory no longer sits beside the flow. Statements live in Record extraction, Character entries are bounded cards, and Views retrieve over the layers.

**The topology and the vocabulary are [05](05-data-model.md)** (operator
2026-10-01, DEC-249): five layers in flow order, Authored → Record → Rollup →
Notebook → Views, with Memory (Honcho) beside them, and the hierarchy
Layer › Tier › Kind › Entry. **A tier is a subdivision inside the Notebook
only**: Register, Character, Standing, Lessons. The Record and the Rollup are
layers, not tiers; this node keeps its filename and describes them because
the Notebook's rules are cut against them.

**This node is canonical for** the Notebook's tiers and the Record and Rollup layers they
cite: their questions, columns, modes, keys and words. Every other node, the
GLOSSARY and every objective or skill uses these words with exactly these
meanings; a new word for any of these things is a defect. The rules below
that the operator changed on 2026-10-01 are marked «Amended by DEC-249».

## 1. The rule the layers and tiers are cut by

> 2026-10-28 (operator, ruled; [30](30-cognition.md#0-the-loop-as-ruled-2026-10-28)): the Character and Lessons adaptors named in this section retire into step ② of the one Cognition loop, which writes Character (traits and guesses; facts are rendered by code), culture and Lessons right after step ① changes an entity's relations; it has no wake rule of its own. Read "the Character adaptor" / "the Lessons adaptor" below as step ②.

**Each of these answers one question, and no question is answered by two.**
The first two rows are layers; the other four are the Notebook's tiers.

| layer or tier | the one question | holds | written by | reasoning |
|---|---|---|---|---|
| **Record** (layer) | *what was observed?* | messages (hers included) with their edits, deletes and reactions; her loop decisions and reminder results; presence fixes and the collector's polls; social items; play samples; perceptions of media (what it shows, whose face); face labels and `character_dispute` items; her tool calls and their results; her artifacts | **code**, at the moment it happens: ingestion ([20](20-observe.md)) for what others do; the turn and fire paths for her own sends, acts, tool calls and artifacts; two tag-path recording verbs, `face_label` and `character_dispute`, each recording what one person said as an item authored by that person and citing their message | none: observed (a perception carries its model's confidence) |
| **Rollup** (layer) | *what does the Record add up to over an interval?* | **episodes** (bounded happenings: a stay, a trip, a co-location, a face co-appearance, an outing, a play session, a person's day, a `response` to her send) and **aggregates** (counts, patterns and clusters over a window: place, rhythm, tie, play profile) | **code only**: a pure function of the Record up to a logical watermark ([25](25-rollup.md)) | **computed** |
| **Register** | *what is the state of the unit's affairs?* | **note** (open, closes), **memo** (settled, kept), **event** (dated, attended) | the Cognition adaptor and the tag path, through write verbs; the Cognition adaptor alone after the cut-over (DEC-249) | **told**, **deduced**, **abduced** |
| **Character** | *what is this entity like, and how does it behave?* | one account per entity: a **person**, a **room**, the **unit**, **Nora** | the Character adaptor only, through `character_revise` | **induced** |
| **Lessons** | *what has Nora learned about how to act here?* | per unit, **lessons**: short prescriptive lines of learned practice, each with the outcomes that taught it ([12](12-schemas.md) §4a) | the Lessons adaptor only, through `lesson_write` and `lesson_retire`; the operator's rollback on the portal goes through `lesson_retire` | **induced**, from outcomes |
| **Standing** | *where does Nora stand?* | her held position toward a **room**, the **unit**, a **person** | the Cognition adaptor and the tag-path debrief, through `standing_revise`; the Cognition adaptor alone after step 4 (DEC-249) | **deduced**, **abduced**, **said** |

Outside these, and neither the Record, the Rollup nor a tier:

- **Doctrine** — authored by the operator (`nora/doctrine/`). Never derived,
  never changed by Nora in whole or in part, changed only when the humans
  all agree, and it wins over anything derived about her. **AMENDED 2026-10-07
  (DEC-291):** what is *given* narrows to its law bucket; until the cut the whole
  doctrine is still served and still wins, and the law subset is what the
  doctrine check reads ([self-growth/31](../self-growth/31-doctrine-audit.md)).

> 2026-10-21 (18 / ADR 0026): the Memory bullet below is historical. [18](../unified-memory/18-extraction.md) gives source statements a Record home as extraction and decomposes Honcho into Record, Notebook and Views; it no longer owns a separate model beside the layers.

- **Memory** — Honcho, and only Honcho: a third party's derived model of
  people. Read as a hint, never cited. `remember` still writes a person's own
  settled claim about themselves into it.
- **Tension** — operator-only rows ([55](55-self.md) §4). Written by the
  Character and Lessons adaptors through `character_revise` and
  `lesson_write`, the exceptions to "one write path per tier" (§2 rule 3).
  Code guarantees she never reads them: no read tool returns them, no block
  renders them, and a test pins both.
- **Assess reports** — operator-only rows the Assess adaptor writes
  ([30](30-cognition.md) §6). The same guarantee: no read tool, no block, a
  test pins both.
- **Reminders** — the machinery of act: every act is a reminder row
  ([30](30-cognition.md) §2), never a tier.

And the words that name processes, never stores:

- **The Dreamer** — the runtime that hosts four adaptors ([00](00-intent.md)
  §3): Cognition, Character, Lessons, Assess.
- **Cognition** — the fast adaptor ([30](30-cognition.md)): orient, decide
  and act in one session per unit per wake; it writes Register and Standing
  and acts only through `remind`. There is no "cognition tier".
- **Reflection** — what the two slow adaptors do: the Character adaptor
  writes Character, the Lessons adaptor writes Lessons
  ([50](50-character-and-standing.md) §6).
- **Assess** — the adaptor that measures finished sessions; it writes reports
  and metrics, never a tier ([30](30-cognition.md) §6).

## 2. The boundaries, as rules code enforces

1. **Links point down; citations reach only the Record and the Rollup**
   (amended by DEC-249, [05](05-data-model.md) §5; this rule said
   "Citations point down" until then). An entry cites Record ids and Rollup
   keys, and nothing else is evidence. A reference to another entry is a
   **link**, never evidence, and the cascade does not walk it. Links follow
   the order Register → Character → Lessons → Standing, and **nothing links
   upward**: a room's or the unit's Character may link to its members'
   Characters; a lesson to a Character line; a Standing line to a Character
   line or a lesson; a note may not rest on "that person tends to cancel",
   which is a Character line, not a claim about the plan.
2. **Everything derived rests on the Record.** Every citation chain ends in
   Record ids; a Rollup key qualifies, because every Rollup row is computed
   from Record ids. A derived row with a chain that does not end there is
   refused.
3. **One writer per layer, one write path per tier.** Code writes the Record and the Rollup; the
   two recording verbs of §1 write Record items authored by a person. Only
   write verbs write Register, Character, Lessons and Standing, and each verb
   belongs to exactly one tier (the pace verbs write the stimulus table, no
   tier). The exceptions are `character_revise` and `lesson_write`, which
   may also write a tension row. **Amended by DEC-249** ([05](05-data-model.md)
   §7): the subconscious writes the Notebook, one writer per tier: Cognition
   the Register and Standing, the Character adaptor Character, the Lessons
   adaptor Lessons; Assess writes none. The tag
   path's Register writes and the debrief's Standing write, named in §1,
   move behind Cognition at or after the cut-over; the tag turn hands off
   through a strong-cue stimulus instead ([05](05-data-model.md) §9, E2).
4. **Nora's own words are Record, and ground almost nothing.** Her rows
   cannot ground the Register or anyone's Character (the note brake,
   GLOSSARY **note**, kept and widened). They may ground her Standing only in
   the mode `said`, and her own Character and her Lessons only paired with
   how people responded. This is **the echo rule**; every other node links
   here. **A person's assent to her own guess** ("you ended up at C,
   right?" — "yeah") **does not upgrade it**: the row stays abduced.
5. **Counts and routines live in the Rollup.** A Character, Lessons or
   Standing line about frequency, regularity or closeness ("usually",
   "often", "closest") cites a Rollup aggregate and never states a count or a time itself.
6. **Keys are fixed.** The unit is the key of everything derived, inside
   DEC-214's hierarchy (workspace > unit > room; unique room labels per
   unit, amended by room-topology step 3), with one exception: the person-scoped Rollup rows (stay, trip,
   place, occupancy, rhythm, corridor) are keyed by person and shared
   across that person's units, because a person moves as one stream; lineage decides which rooms see
   them. Record: by room, by person, by platform account. Rollup: (kind,
   person, interval) or (kind, unit, subject, interval), cited by (kind,
   subject, interval) and resolved by overlap ([25](25-rollup.md) §2).
   Register: by unit, with the origin room kept. Character: by (unit,
   entity). Lessons: by unit. Standing: by (unit, subject). A person in two
   units has two Characters, each from that unit's evidence.
> **SUPERSEDED 2026-10-03 (guard removal; AGENTS.md «A guard needs a consequence»).** The cascade, `nora person forget`/`unforget` and `--clear-unsupported` are deleted. The `citation` edges and `unsupported_at` columns that remained were dropped in that follow-up (migrations `0092`, `0093_drop_withdrawal_leftovers`). Withdrawal today is `messages.hidden_at` only; what derived entries do about a deleted message is ruled in [unified-memory/35](../unified-memory/35-persona.md) (2026-10-15: they stay).

7. **Withdrawal cascades.** When a Record item is withdrawn (a message deleted
   or unsent, the PII scrub, a face label removed, a person leaving a unit,
   `nora person forget`), every derived row whose citation chain reaches it
   is marked `unsupported`, stops rendering, and goes to its writer's next
   session for re-derivation. (Built today: a message a platform deletes or unsends calls the cascade over its evidence rows only (notes, events, staged proposals; not the Standing and culture heads, whose citation is a fold watermark, DEC-276) and `nora person forget --apply` calls it over every store, each in one transaction with the hide, and a re-derived head keeps its mark; the PII scrub and a person leaving a unit have no caller yet; [context-assembly/04](../context-assembly/04-every-occasion.md) §7.) Code walks a `citation` edge table; it never
   parses bodies ([80](80-prior-art.md) §4). Two things are distinct: forget
   **hides** the rows authored by or about the person (§8); the cascade marks
   the rows that only **cite** them `unsupported`. Neither deletes. An
   accidental cascade (a person forgotten, then unforgotten, in error) has a
   narrow operator lever: `nora person unforget --clear-unsupported`
   (`nora.app.person_forget.clear_unsupported`) clears `unsupported_at` on a
   row only when every message it cites is visible again — it does not undo
   `unforget`'s own hide/un-hide, and it re-checks soundness from the
   citations themselves, not from which `forget` produced the mark.

## 3. What every derived row carries

Columns, computed or checked by code:

| column | what it is | on | set by |
|---|---|---|---|
| **reasoning** | how it was reached ([§4](#4-reasoning-modes)) | every derived row; **per field** on an event, **per section** on a Character or Standing row | code for `computed`; otherwise the write verb's argument, checked against the tier's allowed modes (§1) and capped by code (§4) |
| **confidence** | the writer's stated certainty: `low`, `medium`, `high` | Register, Character, Lessons and Standing rows; **per field** on an event, **per section** on a Character or Standing row | the write verb |
| **lineage** | the set of `(kind, subject, origin room)` its citations reach down to; **per field** on an event, **per section** on a Character or Standing row, per row on a lesson | every derived row | code, from the citation chain; never the model |
| **exclude_persons** | people this row is never shown to (a surprise) | Register rows, and by union every row, section or lesson citing or linked to one (**per section** on a Character or Standing row); always empty on the Rollup | the write verb; code unions it from everything the row cites and across Register links (below) |
| **as_of** | when its newest evidence was observed | every derived row; **per field** on an event, **per section** on a Character or Standing row | code |
| **valid_from / valid_to** | when what it says held in the world, not when it was written | Rollup episodes and Register rows only | code for Rollup; the write verb for Register |
| **last_supported_at / support_count** | the newest Record time its citations reach, and how many independent citations support it | Character and Standing sections; lessons | code |
| **base_revision_id** | the revision a write replaces; a stale one is refused `stale_base` with the current row | Register, Character, Lessons and Standing revisions | the write verb |
| **build** | the code version that computed it | Rollup rows | code |
| **recorded_at / superseded_at / superseded_by** | system time: when this row entered the store, and when and by which row it stopped being current (§8) | every row of every store | code |

### Lineage decides who may see a row

**Do not enable (operator, 2026-10-03).** Lineage stays built and `[lineage]
mode` stays `off`. A boundary is drawn by scope, never by a filter inside it:
the unit is the read boundary (DEC-231), and rooms whose talk must not meet
belong in different units. A filter inside a unit blurs that scope, and it
cannot hold anyway: Memory carries no lineage, so the peer card carries the
same facts across. Kept as the worked example in AGENTS.md «Build the least
that works».

**Applied at assembly, never at egress** (GLOSSARY, "enforced at assembly,
not at egress"): when a read tool returns and when a block renders, for the
room the turn or agenda item is bound to. By the time text exists the
disclosure is decided.

Each lineage kind has one written per-room predicate, in code:

| kind | room B may be shown it when |
|---|---|
| `message:<A>` | B is A; or B's listed persons are a subset of A's and B lists no `*` person (R-49, amended for derived rows). The origin room is shown with it |
| `presence` (subject P) | B's `location` flag is on (DEC-79) and the live DisclosureGuard passes for P in B (R-31) |
| `face` (subject P) | B is the origin room of the media, or P is a listed person of B and B passes the media's own lineage |
| `social` (subject P) | P is a listed person of B |
| `play` (subject P) | P is a listed person of B |
| `perception` | B passes the lineage of the item it reads |

Memory has no lineage kind: it is never cited, so nothing derived rests on it.

- **Per field, every triple.** A field (on an event) or section (on a
  Character or Standing row) renders in a room only if every triple of its
  lineage passes there; one that fails is not shown, and the rest of the row
  is. A status or people field that places people is `presence`-lineaged.
- **Placing a person is presence.** Any row that places a person somewhere at
  a time carries `presence` for that person, whatever its source: a
  co-location, a face in a media item, a story's location tag.
- **The guard runs live.** Rendering re-runs DisclosureGuard; R-31's subset
  check is a rule about a *person*, which is why every lineage triple names
  its subject.
- **Unknown is deny.** An empty or unknown lineage renders only in its origin
  room, and nowhere if it has none. Existing rows get their lineage by a
  backfill before the filter ships ([70](70-migration.md) step 2).
- **Surprises.** A row with a non-empty `exclude_persons` never renders in a
  room holding an excluded person, never in a room with unnamed platform
  accounts, and is re-checked when a reminder about it fires. Code unions it
  from everything a row cites, and across links between Register rows (a
  note and its event through `Note.event_id`; a note and the memo it closes
  into), re-checking on every link write.
- **As built (step 2, lineage half, DEC-236; 2026-09-29).** The column has
  one format in every store, the one step 2a writes on Rollup rows: a
  sorted JSON array of `[kind, subject, origin]` triples, rooms keyed
  `platform:conversation_id` (this node fixes no room key; 2a's was the
  first in the store). A row with lineage per row (a note, a Rollup row)
  stores the array; a row with lineage per section stores an object of
  arrays: a Standing row `body` and `working_set`, the two sections today's
  schema has, a culture row `body` (prose until the sectioned account, step
  4). `message` triples carry the room as origin and no subject;
  `unresolved` carries the row's own room. The predicates are `nora/agent/lineage.py`
  and fix the details the table leaves open: `message:<A>` passes only
  between rooms of one unit and never out of a `*` room (R-49's boundary),
  and only when B's live roster is readable, names every account and holds
  only listed persons of A (a listed-persons subset alone says nothing about
  who is in B; tightened on review);
  `presence` needs the flag AND the guard, the guard not waived by the flag
  as `where_is` waives it (DEC-79), and it denies with no subject or no live
  roster; an unknown kind, including the `unresolved` code writes for a
  citation it cannot resolve, passes only in the row's origin room. A
  Standing or culture row rests on its own room plus `unresolved`, because
  its fold is shown the unit's `<notes>` and cites nothing from it, so both
  stay in their own room until their writers cite per section.
  `exclude_persons` is checked against the room's listed persons and its
  live roster. The four pushed blocks filter behind `[lineage] mode`
  (`off`, `shadow`, `on`; shipped `off`); `record_query` applies lineage in
  every mode. Block assembly reads no live roster yet, so until it does a
  presence triple or a surprise withholds its section in every room, and a
  `message:<A>` triple in every room but A.
- **Per kind: lineage from `about` for unit-wide kinds** (DEC-249,
  [05](05-data-model.md) §3). A lesson, a unit's or Nora's Character section
  and a unit Standing section whose text places nobody take their lineage
  from the persons they are about, not from the union of their citations,
  which would make them unrenderable across rooms with different rosters. A
  section that places a person keeps the citation rule.

## 4. Reasoning modes

`told`, `deduced` and `induced` are Honcho's `explicit`, `deductive` and
`inductive` levels under Nora's names; `abduced`, `computed` and `said` are
Nora's own (Honcho's code has no abductive level, [80](80-prior-art.md) §1):

| mode | meaning | allowed in | rule |
|---|---|---|---|
| **computed** | arithmetic over the Record; no inference | Rollup | same Record and watermark, same row |
| **told** | a person stated it in words, to Nora or to each other; members agreeing among themselves counts ("ok B then" tells B) | Register | cites the message |
| **deduced** | follows necessarily from rows already held | Register, Standing | "the event is at C from 20:00, and a co-location on fresh fixes puts P at C at 20:05, so P attended" |
| **induced** | a generalisation from many instances | Character, Lessons | cites a Rollup aggregate or, for a lesson, the several outcomes that taught it; one instance is never induction |
| **abduced** | the best explanation of evidence nobody stated | Register, Standing | a value resting only on abduction goes out as a question ("you ended up at C, right?"); never upgraded by assent (§2 rule 4) |
| **said** | Nora herself said it; the row asserts only that she said it | Standing | cites her own Record row; grounds nothing but itself |

**On a Notebook entry** ([05](05-data-model.md) §3) the stated basis is
`observed` or `inferred`, with its confidence; whether these six modes stay as
the detail under the two (the recommendation) or are dropped at convergence
is [05](05-data-model.md) §10 Q2. Until then this table holds.

**Code caps the mode.** A value resting on presence fixes, or on a face in
media ([60](60-faces.md)), older than the freshness threshold (starting at
900 s, sized from the collector's measured cadence) at the time it asserts is
capped at `abduced` whatever the verb's argument. Attendance
deduced from a co-location therefore holds only on fresh fixes; otherwise it
is abduced.

## 5. Culture is Character

Culture answers the question person Character answers — *what is it like,
how does it behave* — about a room or the unit instead of a person. "Culture"
survives as the word for the Character of a collective; the existing
`culture` store becomes its room case. Confirmed by the operator 2026-09-28.
In the data model it is a Character entry like any person's, on the same
machinery ([05](05-data-model.md) §4).

## 6. The words

> 2026-10-28 (operator, ruled; [30](30-cognition.md#0-the-loop-as-ruled-2026-10-28)): the Character and Lessons adaptors named in this section retire into step ② of the one Cognition loop, which writes Character (traits and guesses; facts are rendered by code), culture and Lessons right after step ① changes an entity's relations; it has no wake rule of its own. Read "the Character adaptor" / "the Lessons adaptor" below as step ②.

> 2026-10-21 (18 / ADR 0026): [18](../unified-memory/18-extraction.md) amends the **layer**, **Memory** and lowercase **memory** definitions in the historical table below: Honcho no longer sits beside the layers or owns what people said. Source statements live in Record extraction, bounded Character entries are Notebook judgements, and Views retrieve over readings, Rollup and entries.

| word | means exactly | never means |
|---|---|---|
| **layer** | one of the five of [05](05-data-model.md) §1, defined by who writes it and how it is derived: Authored, Record, Rollup, Notebook, Views; Memory sits beside them | a tier; a prompt block |
| **Authored** | the layer humans write: doctrine, the Dreamer's objectives and skills, the config files | anything a model writes |
| **Notebook** | the layer of Nora's judgement: entries in four tiers, written only by the subconscious (one writer per tier), citing the Record and Rollup, never evidence ([05](05-data-model.md)) | the Record; Memory |
| **Views** | the layer of per-reader slices, assembled at read time under lineage and withdrawal through one assembly path, never stored ([05](05-data-model.md) §6) | a store; a cache; a SQL view |
| **Memory** | Honcho, beside the layers: per-person understanding and what people say about themselves; read into Views, never copied into the Notebook | any store of ours |
| **tier** | a subdivision inside the Notebook only, by what it is about: Register, Character, Standing, Lessons | the Record or the Rollup (layers) |
| **kind** | the row type at every layer: a Record kind (message, reaction, fix), a Rollup kind (stay, outing, tie), a stimulus kind, and in the Notebook a row type within a tier (note, memo, event; person, room, unit, Nora; room, unit, person; lesson) | a platform |
| **entry** | one Notebook row: home, entity, kind, body, citations, reasoning, confidence, visibility, lifecycle, revisions ([05](05-data-model.md) §3); a lineage item is a **triple**, a `policy.toml` block a table, never an entry | a Record item; a log line |
| **basis** | how a View shows an entry's reasoning: `observed` or `inferred` ([05](05-data-model.md) §3, Q2) | a stored column |
| **triple** | one `(kind, subject, origin room)` item of a lineage (§3) | an entry |
| **home** | the person, room, unit or workspace a row belongs to ([05](05-data-model.md) §1 rule 5) | `record_query`'s `scope` argument |
| **link** | a typed reference from one entry to another; never evidence ([05](05-data-model.md) §5) | a citation |
| **Record** | the observed layer | a database row in general |
| **Rollup** | the computed layer | a model's summary; Standing's fold of folds (below) |
| **episode** | a Rollup row for one bounded happening, with a start, an end and participants | a raw ingested item (Graphiti's episode); an interaction exemplar (LangMem's) |
| **aggregate** | a Rollup row of counts, patterns or clusters over a window (a place cluster is one) | a Character line |
| **daybook** | the Rollup episode of one person's day | a diary Nora writes |
| **Register** | the unit's affairs: note, memo, event | the voice register of `nora/doctrine` (always "voice register" there) |
| **note** | an open thing the unit must not drop; it closes | a jotting, an idea |
| **memo** | a settled thing the unit wants kept; it does not close | a thing about the speaker alone (that is `remember`) |
| **event** | a dated thing people attend (code: `Gathering`) | a fire-turn occasion (code: `Event`, DEC-139), a wake stimulus, an episode |
| **Character** | what an entity is like and how it behaves | a person's attributes (Honcho), a persona (DEC-29) |
| **account** | the body of one Character row | a platform account (a login: always "platform account") |
| **culture** | the Character of a room or the unit | `<room>`, the operator's statement of what a room is FOR |
| **Lessons** | the tier of what Nora has learned about how to act in a unit | doctrine; her Character (what she is like) |
| **lesson** | one row of Lessons: a short prescriptive line, why (cited outcomes), and when it applies | a rule she may not break (that is doctrine); a runtime skill |
| **outcome** | how people responded to something she did: a reply that landed, a correction, a reaction, silence where a reply was expected; recorded as a `response` episode ([25](25-rollup.md) §3) | an agenda item's close reason |
| **Standing** | Nora's held position toward a subject | status, queue position, `NoteStore.standing()` |
| **tension** | an operator-only row: evidence pulling against a doctrine line | Honcho's `contradiction`; a Character line |
| **reasoning** | the mode a derived row was reached by (§4) | the model's chain of thought |
| **confidence** | the writer's stated certainty (§3); on a perception, its model's score | a probability; the reasoning mode |
| **lineage** | the `(kind, subject, origin room)` set a row's citations reach down to (§3) | the citations themselves |
| **citation** | a Record id or a Rollup key a line rests on | a hint read from memory |
| **fold** | a model call that rewrites a row from its last version plus new evidence (the Standing fold; a note's `Folded`) | a Rollup; a revision chain |
| **memory** | Honcho | any store of ours |
| **conclusion** | a derived row in Honcho | a Record item (Honcho calls its conclusions "observations"; Nora never does) |
| **stimulus** | what ingestion, the Rollup or the clock raises to wake cognition | the Signal platform; a Python/OS signal; an event |
| **agenda** | the items code computes from a unit's pending stimuli for one session, each bound to a room | a plan the model writes; a menu of actions |
| **cue** | a deterministic feature of an item that makes it matter (a date, an amount, a keep instruction, a surprise) | a model's judgement |
| **wake** | the moment a unit's pending stimuli start a session | a clock tick |
| **strike** | one session that ended with an agenda item still open, or closed `waiting`; at `max_strikes` code closes the item | a note's strike (a raise nobody answered), though it is the same idea; a self-wake's miss |
| **miss** | a self-wake whose session wrote nothing and acted on nothing; at `self_wake_misses` her self-wakes for that subject are refused for a day ([20](20-observe.md) §3) | a strike |
| **deviation** | a Rollup stimulus: a person's pattern broke, raised only on healthy sources | a rule that triggers an act |
| **co-location** | the Rollup episode of members at one place at one time | an event |
| **outing** | the Rollup episode of co-located members away from every member's home | an event |
| **tie** | the Rollup aggregate of how much two people interact, per week | a Character line about closeness |
| **echo rule** | her own rows ground only `said` Standing, and her Character and Lessons paired with responses | a filter on her replies |
| **unsupported** | the state of a derived row whose citation chain reaches a withdrawn Record item; it does not render | deleted |
| **hidden** | a row kept in history but excluded from every read, block, tool, Rollup build, portal page and new derivation (forget, retention, prune); reversible | deleted; `unsupported` |
| **tombstone** | what a media blob hard-deleted for storage leaves: id, kind, time; a restore is incomplete for it | a hidden row; an `unsupported` row |
| **system time** | when a row entered and left the store (`recorded_at`, `superseded_at`) | valid time |
| **valid time** | when what a row says held in the world (`valid_from`, `valid_to`) | system time |
| **as of T** | every row with `recorded_at` ≤ T and not superseded at T (§8) | "valid at T" |
| **head** | the current revision of a chain, derivable from it, never a row updated in place (as built, a live row kept by the trigger, below) | the chain; a View |
| **snapshot** | an eval sandbox holding Nora as of T ([70](70-migration.md) §2) | a Honcho export |
| **assess pass** | the measurement of finished sessions, off any live path ([30](30-cognition.md) §6) | a gate on a write |
| **session** | one run of a Dreamer adaptor for one key (a unit, or a unit and an entity); bare "session" is a Cognition session | a Honcho session (a conversation thread; always written "Honcho session") |
| **cognition** | the fast adaptor: orient, decide, act | a tier; reflection |
| **adaptor** | one job the Dreamer runtime hosts, with its own key, wake rule and write verbs | a unit; a platform adapter |
| **reflection** | the slow adaptors' work: inducing Character and Lessons | cognition; a process name for the whole loop |
| **Dreamer** | the runtime hosting the four adaptors | a tier; a background pass that never acts |
| **write verb** | a tool that writes one tier's row, validated by code before commit; the pace verbs (`wake_at`, `wake_move`, `wake_cancel`) write the stimulus table instead | an act |
| **act verb** | `remind`: the one tool whose row leaves through the fire path; plus `remind_move`/`remind_cancel` on her own reminder rows, which change a reminder and send nothing | a send; a self-wake |
| **self-wake** | a `self` stimulus Cognition writes with `wake_at`, due when it forecasts the subject next needs a look; inward, nothing leaves ([20](20-observe.md) §3) | a reminder (outward, to a room) |
| **reminder** | a row holding what is to be done in a room and when; every act is one | a note |
| **brief** | what a reminder cognition writes carries: an intent (`tell` or `ask`) and the subject rows, never prose | the words she will say |
| **subconscious** | the operator's name (潛意識) for the Dreamer's adaptors, always running underneath ([55](55-self.md) §6); not a code name | a tier; memory; a data layer |
| **conscious** | the operator's name (明意識) for the turns that speak: the tag turn and the fire turn; not a code name | a tier; a data layer |
| **tag path** | the turn a tag starts; the reflex | cognition |
| **fire path** | `FirePath.speak` (`nora/app/fire.py`), the turn a due row starts; one of the senders of R-1b | a background session |
| **unit** | the social unit a room belongs to (DEC-105); the key of everything derived | a Honcho named scope (how memory stores it); a "circle" |
| **room** | one conversation Nora is in, on one platform (`[[conversations]]` in `policy.toml`) | a unit; a voice channel (a surface of a room, DEC-191) |
| **workspace** | a group of units, the boundary of a person's model (DEC-214) | a Honcho workspace (always written so) |
| **block** | one labelled unit of context in a prompt (`_BLOCKS`, `nora/agent/prefill.py`) | a Letta memory block; "context block" |
| **perception** | a model's reading of a Record item (vision, face match), stored on that item with its confidence | an interpretation of what it means |
| **play** | the stream of who is in which game or voice channel, with whom | a game's own match data (that is a source feeding it) |

**"Rollup" collides twice.** `nora/analytics/rollup.py` is the presence part
of this layer and keeps its name. Standing's `[standing] rollup_max_folds`
and `nora_standing_rollup_folds_total` name a fold of Standing folds, not
this layer: both go when the ingest Standing fold is retired
([70](70-migration.md) step 4).

Retired: **idea** (a proposal is a `## Proposed` line of a note, DEC-213);
**stance** (it is Standing); **knowledge** and **fact** (the deleted tier,
#115; say "claim" or "typed row"); **gathering** in prose (the code name
`Gathering` stays); **circle** for the people of a unit (say unit; R-47's
"circle", the presence roster, keeps its meaning); **observation** (Honcho's
word for a conclusion; say "Record item" or "report"); **storage tier**; **tier** for the Record or the Rollup (they are layers, DEC-249);
**mart** and **trace** (the operator's metaphors; "trace" is Langfuse's in
this repo), and with them **lake** and **warehouse** (say Record, Notebook,
Views; DEC-249); **bronze**, **silver**, **gold** (never used);
**inferred** as a reasoning mode (replaced by the three inference modes; it
survives only as an entry's basis, [05](05-data-model.md) §3, DEC-249); **community**
(Graphiti's clustered group; rooms and units are configured).

## 7. Where today's stores land

| today | lands in |
|---|---|
| transcript rows, presence fixes, media, `media_objects` comprehension | Record |
| reactions, deletes, unsends | Record (`message_reaction`, `message_delete`, since [70](70-migration.md) step 1) |
| `decision_log` | Record |
| presence stays, trips, places, rhythm, occupancy, corridors (`nora/analytics/`, design 27) | **Rollup** (derived and rebuilt; this bundle's first drafts filed them as Record) |
| `note`, `note_revision` | Register · note |
| `Gathering` (per room today) | Register · event, **unit-wide** |
| — | Register · memo (new) |
| `culture`, `culture_revision` | Character · room |
| — | Character · person, unit, Nora (new) |
| `standing` (per room) and its working set | Standing · room |
| — | Standing · unit, person (new) |
| — | Lessons (new); Character · Nora's `What lands and what does not` moves here |
| Honcho representation, self card, peer card | memory (a hint, never a citation, never Character evidence) |
| — | tension (new, operator-only) |
| reminders | the machinery of act, not a tier |
| her tool calls and their results, her artifacts (documents, generated images, computer sessions and exports), her Instagram comment replies | Record, her acts (DEC-249; today tool calls are logged only when cacheable and `ok`, and comment replies live only in `instagram_summons`) |
| face labels · face templates | Record · Rollup |
| `presence_places` | two layers in one table: Life360's saved places are Record, the fields the analytics process derives are Rollup; split when touched |
| `messages`' delivery-status and adoption columns | machinery beside the Record row |
| `geocode_cache`, `sticker_index`, `video_readings`, the vision cache | caches, outside the layers: rebuildable, never cited |
| a note body's `## From memory` section | Memory crossing into the Notebook: retires when notes converge onto the entry ([05](05-data-model.md) §9, E4), rendered by the View from Memory instead |
| the curator's discharge into Honcho (`[notes] discharge_enabled`, off) | the Notebook writing Memory: dies with the curator at step 4 |

## 8. Time: every store is append-only, so Nora can be restored as of T

**The principle** (operator 2026-09-28). Every store Nora writes is
append-only in **system time**: each row carries `recorded_at`, and a revision
carries `superseded_at` and `superseded_by` on the row it replaces. This
holds for the Record, Rollup builds, Register, Character, Lessons, Standing,
tension rows, face labels and templates, perceptions (versioned by model and
prompt), stimuli and self-wakes, sessions and their agenda close reasons,
assess reports, reminders and every state transition of one, the decision
log, and the `citation` edge table.
Lineage and `exclude_persons` are columns of the versioned rows, so they are
versioned with them.

- **As of T** is every row with `recorded_at` ≤ T and not superseded at T.
  It is what Nora held at T, and a query over it is how she is restored.
- **System time is not valid time.** `valid_from`/`valid_to` (§3) say when a
  thing was true in the world; `recorded_at`/`superseded_at` say when Nora
  held a row. A dinner at 20:00 recorded at 18:40 has valid time 20:00 and
  system time 18:40.
- **No UPDATE in place on anything derived.** A head is derived from its chain
  (the revision not superseded). A head pointer kept for compare-and-swap is
  a cache derivable from the chain, and no as-of read uses it. Mutable loop
  state (a note's strikes and revisit time, a reminder's status, an event's
  fields) is written as transition rows; the current value is the newest.
- **As built (step 1b, DEC-232, amending the bullet above).** The tables
  that changed in place STAY the live heads, updated in place as before;
  the chain is `row_history`, written by a trigger in the same transaction
  as each write, and no as-of read uses the head. No head was rebuilt as a
  SQL view: rewriting ~60 write statements and every reader to get one was the
  cost, and the trigger gives the same as-of answer with no reader
  changed. Versions are dated when the write ran, not when its
  transaction committed. History capture has no flag, deliberately: a
  switch that can turn history off defeats the restore guarantee.
- **Authored and configured state is versioned too**: the doctrine and the
  Dreamer's objectives loaded, the skills, the three config files and the model route in force at T
  ([70](70-migration.md) §5).

**Operator-only rows** (tension, assess reports) are versioned like the rest
and, like the rest, never read by her.

> **SUPERSEDED 2026-10-03 (guard removal; AGENTS.md «A guard needs a consequence»).** `nora person forget`/`unforget` and the cascade are deleted; a platform delete hides the message (`hidden_at`).

**Removal is soft.** `nora person forget`, retention and prune **hide** rows:
a hidden row is excluded from every read, block, tool, Rollup build, portal
view and new derivation, and kept in history, so a restore to T is complete.
Hiding is itself a transition row, and `nora person unforget` reverses it.
The withdrawal cascade marks rows `unsupported`, also a transition.

**The one exception is memory** (Honcho): live and mutable by design, with no
chain to read backwards, it cannot be restored to T. The only other gap is a
media blob hard-deleted because storage forces it ([70](70-migration.md) §9):
it leaves a tombstone that marks a restore incomplete for that blob.
