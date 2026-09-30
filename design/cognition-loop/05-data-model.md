---
type: design
title: "Data model — observed and authored, one entry, four families, marts at read"
description: "The system-wide data model the operator agreed on 2026-10-01. Two kinds of data only: OBSERVED (Record and Rollup, the evidence) and AUTHORED (entries written about it, never evidence). Every authored thing is one object, the entry, with one set of machinery; its kinds fall into four families (Affairs, Portraits, Positions, Lessons) by what they are about and what kind of statement they make, each rule that differs per kind stated and tested on its own. References are the edges, read as a graph and stored relationally; nothing is a knowledge graph. Consumers read through marts, per-consumer views assembled at read time through one path. Who writes and who reads each family across the two layers, the flow, what is built today, the convergence order, and the conflicts left for the operator."
tags: [nora, data-model, entry, record, rollup, register, character, standing, lessons, lineage, mart, two-layers]
timestamp: '2026-10-01'
edges:
  - {dst: 10-tiers, rel: sequences, provenance: asserted}
  - {dst: 15-map, rel: motivates, provenance: asserted}
  - {dst: 55-self, rel: depends_on, provenance: asserted}
  - {dst: 70-migration, rel: motivates, provenance: asserted}
  - {dst: 80-prior-art, rel: depends_on, provenance: asserted}
---

# 05 · Data model

**This node is canonical for the concept**: the two kinds of data, the entry,
the families, the references and the marts. [10](10-tiers.md) stays
canonical for the tiers' columns, modes, keys and words, and uses this node's
grouping. Where the two disagree today, §11 lists it as an open question for
the operator; neither node silently wins.

## 1. In plain words

Nora keeps two kinds of data. **What happened** is recorded by code and never
argued with: the messages, reactions, edits, presence, photos and polls (the
Record), and the sums code computes from them (the Rollup). **What is written
about what happened** is her notebook: notes, memos, events, what a person or a
room is like, where she stands, what she has learned. The notebook is not
evidence. Every line in it says what it rests on, how it is known and how sure
it is, so anyone can follow it back to what happened and judge it.

The subconscious (the Dreamer's adaptors) thinks and writes the notebook. The
conscious (the turns that speak) speaks from it. Each consumer reads a slice
of the notebook cut for it at the moment it reads, with what its room may not
see and what has been withdrawn already taken out.

## 2. Two kinds of data, and only two

| kind | what it is | stores | written by | may be cited as evidence |
|---|---|---|---|---|
| **observed** | what happened, and what it adds up to | **Record** (messages with edits, reactions and deletes; her own sends and decisions; presence fixes and polls; social items; play; perceptions of media) and **Rollup** (episodes and aggregates computed from the Record) | code only: ingestion, the fire path for her own acts, the Rollup builder ([20](20-observe.md), [25](25-rollup.md)) | **yes**: this is the evidence |
| **authored** | what is written about what happened | **entries** (§4): notes, memos, events, Character, culture, Standing, Lessons | a model, through write verbs code validates ([30](30-cognition.md) §3) | **no**: an entry cites evidence; it is never evidence itself |

Outside the two, as in [10](10-tiers.md) §1: doctrine and configuration
(written by the operator: the law, not claims about the world); memory
(Honcho, a third party's model of people, read as a hint and never cited);
operator-only rows (tension, assess reports), which are never read by her;
the machinery of act and pace (reminders, stimuli). Record items a person
authors through a tag-path verb (`face_label`, `character_dispute`) are
observed: they record what that person said.

"Authored data is not evidence" is what [10](10-tiers.md) §2 rule 2 already
requires (every citation chain ends in Record ids), stated as the first rule
rather than the second.

## 3. Not a knowledge graph: claims with their chain

A group chat cannot prove what is true. What can be audited is the chain from
a claim to what was observed. So Nora never stores a bare fact:

| never stored | stored instead |
|---|---|
| Ada is vegetarian | Ada said she is vegetarian: mode `told`, cites the message, its age shown (§11 Q1 on the word) |
| the dinner is at C | place C, `told` at 18:40 by a member, citing the message; people: three, `deduced` at 20:05 from a co-location on fresh fixes, citing the episode |
| Bo and Cy are close | Character · Bo, `Who they are close to`: `induced`, citing the tie aggregate over twelve weeks |
| they went to C (nobody said so) | Nora guesses they ended up at C: `abduced`, citing the route request and the co-location on stale fixes, confidence `low` |

**An abduced claim becomes a question, never a stated fact**
([40](40-register.md) §4): code sets the brief's intent to `ask`, and a "yeah"
to her own guess leaves it abduced ([10](10-tiers.md) §2 rule 4).

Nothing here builds a store of subject-predicate-object facts, extracted
entities or clustered communities: [80](80-prior-art.md) §5 refuses them
(Graphiti's, Mem0's graph, which Mem0 itself removed), and the deprecated fact
tier (#115) was that store. What exists is typed rows that cite evidence, and
references between them (§6).

## 4. The entry: one authored object

Every authored item is an **entry**. One shape, one set of machinery:

| part | what it holds | set by |
|---|---|---|
| **subject** | what it is about: a `person`, a `room` or the `unit`, `nora`, or an `affair` (the entry is itself the affair: a note, a memo, an event) | the write verb |
| **kind** | note, memo, event, character, standing, lesson (§5) | the verb |
| **body** | Markdown under the kind's fixed schema ([12](12-schemas.md)); an event also has typed columns | the model; parsed by code, refused not repaired |
| **evidence** | citations to observed items only: Record ids and Rollup keys | the model names them; code resolves them and writes the edges (§6) |
| **how known** | the reasoning mode ([10](10-tiers.md) §4), per row, per field (event) or per section (Character, Standing) | the verb, checked against the kind's allowed modes and capped by code |
| **confidence** | `low`, `medium`, `high`, at the same grain | the verb |
| **visibility** | lineage (the `(kind, subject, origin room)` set its evidence reaches) and `exclude_persons` | code, from the evidence; never the model |
| **time** | `as_of` (newest evidence), `valid_from`/`valid_to` (affairs), `last_supported_at`/`support_count` (portraits, positions, lessons), system time (`recorded_at`, superseded) | code |
| **lifecycle** | the kind's states (§5) | the verb, per the kind's rules |
| **revision history** | an append-only chain; every write names `base_revision_id`, a stale one is refused `stale_base` | code |
| **withdrawal** | `unsupported` (its evidence was withdrawn, [10](10-tiers.md) §2 rule 7) and `hidden` (forgotten, retained out, [10](10-tiers.md) §8); neither deletes | code |
| **about** | the persons, places, rooms it names: an index for finding it, not evidence | the verb, resolved by code |
| **links** | typed references to other entries (§6) | the verb |
| **provenance** | writer (adaptor or turn), session or tag id, model, doctrine and instruction hash | code |

**The same machinery for every kind**, built once: storage, the revision chain
and compare-and-swap, the citation edges, the withdrawal cascade and hiding,
lineage and its per-room predicates ([10](10-tiers.md) §3), system time and
as-of reads ([10](10-tiers.md) §8), the read and search verbs, and the marts
(§7).

**What differs per kind is a rule, stated and tested on its own.** Unifying
the machinery hides the differences between kinds; it does not remove them.
Each row below is one rule per column, and each gets its own named test when
its kind is built on the entry, never one test parametrized over a table that
would pass whatever the table says:

| kind | family | subject | writer | body schema | lifecycle | modes | revisited when |
|---|---|---|---|---|---|---|---|
| note | Affairs | affair | Cognition; the tag path (§8, Q3) | [12](12-schemas.md) §3 (DEC-213) | open → retired or replaced | told, deduced, abduced | anchor, `revisit_at`, strikes |
| memo | Affairs | affair | Cognition; the tag path's `memo_write` | [12](12-schemas.md) §3 | kept → withdrawn; never closes | told, deduced | never on a clock; on a cited message's edit or delete |
| event | Affairs | affair | Cognition; the tag path's event verbs | columns + [12](12-schemas.md) §3 | staged → proposed → confirmed → happened; cancelled, lapsed, evicted ([40](40-register.md) §3) | per field: told, deduced, abduced | its date; self-wakes |
| character | Portraits | person, room, unit, Nora | the Character adaptor only | [12](12-schemas.md) §4 | one current account; a section past 60 days moves to `Unsure` | induced | evidence past `character_batch`, or 7 days stale; a dispute at once |
| standing | Positions | room, unit, person | Cognition; the debrief for a room (Q3) | [12](12-schemas.md) §5 | one current position; a section past 14 days is dropped | deduced, abduced, said | each session that touches the subject; each tag turn's debrief |
| lesson | Lessons | the unit | the Lessons adaptor only; the operator retires | [12](12-schemas.md) §4a | active → retired; a size cap per unit | induced | 30 new `response` episodes, 7 days stale, a 60-day horizon |

## 5. Four families, cut by two axes

An entry's family is fixed by **what it is about** and **what kind of
statement it makes**. Each family is one of [10](10-tiers.md)'s authored
tiers; the family name is the plain word, the tier name the code word
(Q6):

| family | about | kind of statement | the question | tier ([10](10-tiers.md)) | kinds |
|---|---|---|---|---|---|
| **Affairs** | what the group is doing | a state of affairs: open, settled or dated | what is going on in the unit? | Register | note (open question; closes on its condition), memo (settled; never closes, revised or withdrawn), event (dated plan or happening; lifecycle) |
| **Portraits** | what someone or something is like | a description | what is this entity like, how does it behave? | Character | a person's, a room's or the unit's (today's culture), Nora's |
| **Positions** | where Nora stands | her position | where does she stand toward this room, the unit, this person? | Standing | room, unit, person |
| **Lessons** | how Nora acts here | a prescription | what has she learned about acting in this unit? | Lessons | lesson |

The axes keep a question from having two homes: "who has not paid" is an
affair (a note), "Cy usually pays late" is a portrait (it cites an aggregate),
"I owe them the receipt" is a position, "when they split a bill, give the
per-head amount first" is a lesson.

## 6. References are the edges; a graph is a way of reading

Storage stays relational. The edges are three kinds of typed reference, each
its own table or column, never parsed out of a body:

| reference | from → to | carries evidence? | what code does with it |
|---|---|---|---|
| **cites** | entry → Record item or Rollup key | **yes** | lineage is computed from it; the withdrawal cascade walks it (`nora/app/withdrawal.py` over the `citation` table) |
| **about** | entry → person, place, room, unit | no | finds entries by subject (`event_search(person=…)`); forget hides the entries about a person ([70](70-migration.md) §9) |
| **links** | entry → entry, typed: a note *tracks* an event (`Note.event_id`), a note *closes into* a memo, an event *leaves behind* a memo, a position *is informed by* a portrait | no (Q4) | `exclude_persons` is unioned across it ([10](10-tiers.md) §3); a link to a hidden or unsupported entry is not followed at render |

A graph is a way of reading these: "everything about Bo", "what this note
became", "what rests on this message" are walks over typed references in SQL.
There is no graph database and no graph store.

**Recommendation needs no new store** (the operator's future use case). Worked
through, with synthetic people: Bo asks the room where to eat on Friday.

1. The tag turn's mart already holds the unit's `<events>` and `<memos>`
   indices, the room's portrait (culture) and the unit's lessons (§7).
2. It pulls what the question needs: `character_read(Bo)` ("prefers noodles
   over rice", induced, cites a place aggregate); `memo_read` ("Cy said she
   is vegetarian", told, cites a message, 5 weeks old); `event_search(person=
   Bo, interval=last 60 days)` (three dinners, two at one place);
   `rollup_read(place, Bo)` (where Bo actually spends Friday evenings); a
   memory hint from `about`; and the lesson "give two options, not five".
3. It decides in the turn: two places near Friday's usual area, one with
   vegetarian dishes, each with why. If the unit then settles on one, that is
   an event; if the question stays open, a note.

Nothing stored was new: a read across the families, the Rollup and memory,
then a decision. What is new is the reply (Record) and whatever it leads to.

## 7. Marts: every consumer reads one slice, cut at read time, by one path

The operator's analogy ([25](25-rollup.md) §1) was lake → warehouse →
dashboard. Between the warehouse and the dashboard sits the **mart**:

| layer | Nora | stored? |
|---|---|---|
| lake | **Record** | yes, append-only |
| warehouse | **Rollup** + **entries** | yes, versioned |
| **mart** | a **per-consumer read view**: the slice one consumer reads, for one room, now (or as of T) | **never stored**; assembled at read time |
| dashboard, analyst | the **conscious turn** (speaks from it) and the **Cognition session** (thinks with it) | — |

A mart:

1. selects from the warehouse what its consumer declares (families, kinds,
   subjects, window, size cap), as of now, or as of T for a snapshot;
2. applies **lineage for the consumer's room**: the tag or fire turn's room,
   each agenda item's bound room for a session, the viewer's rooms for a
   portal page ([10](10-tiers.md) §3);
3. applies **withdrawal**: an `unsupported` or `hidden` entry is absent, in
   every lineage mode;
4. renders each row with its age and how it is known, through `ContextBlock`
   for a prompt.

**One assembly path.** Every consumer gets its slice from one builder: the
consumer declares its slice, it never writes its own selection, lineage or
withdrawal code. This is the rule the repo already holds one layer down: one
block injector (`ContextBlock`, `tests/architecture/test_one_block_injector.py`),
one transcript renderer (`render_messages`,
`tests/architecture/test_one_transcript_renderer.py`) and one media join.
The per-consumer difference stays visible as a declared slice, pinned by that
consumer's own test.

**The marts that exist today, and where they depart from one path** (read
from the tree on 2026-10-01; each departure is a migration item, §10, or an
open question, §11):

| mart | consumer | assembled by | lineage | withdrawal | departure |
|---|---|---|---|---|---|
| the tag turn's blocks | tag turn | `nora/engagement/context.py` with `build_prefill` (`nora/agent/prefill.py`) | per room, behind `[lineage] mode` (shipped `off`) | notes in the note store's reads; culture and Standing in `nora/engagement/context.py` | the reference path; no `<events>`, `<memos>`, `self` or `lessons` block yet |
| the fire turn's blocks | fire turn | its own `build_context` in `nora/app/fire.py`, sharing `build_prefill` | the same `LineagePolicy` | as the tag path's blocks | a second assembler (it avoids `ContextAssembler.assemble` on purpose) |
| the agenda's grounding | Cognition session | `nora/app/cognition_grounding.py` with its own renderer | its own call into `nora/agent/lineage.py`, stricter: a row must pass for every room on the agenda | applied in every mode | a third assembler and a third renderer; Standing not pushed until `standing_read` |
| read tools | both layers | each tool: `note_read`/`note_search` in `nora/agent/tools.py`, `nora/agent/standing_read.py`, `nora/infra/sql/record_query.py`, `rollup_read` | each applies it itself | each applies it itself | one lineage call site per tool |
| the clocked adaptors' prompts | note-candidates, the curator, note-merge, proactive, culture, the ingest Standing fold | each module's own prompt builder (`nora/language/note_candidates.py`, `nora/language/proactive.py`, `nora/language/culture.py`, `nora/ingest/stages/standing_event.py`, …) | none found; they read unit-wide and never speak | notes through the store's reads | retired at step 4, except the debrief (`nora/engagement/debrief_render.py`) |
| portal pages | a person | `nora/web/gatherings.py` (events they created or answered), `nora/web/memory.py` (their Honcho entries), `nora/web/documents.py` | none: participation-scoped | none: events are outside the cascade today | no page yet for notes, memos, portraits, positions or lessons; [50](50-character-and-standing.md) §4's correction page needs one |

## 8. The two layers × the families

The operator's model ([55](55-self.md) §6): **the subconscious thinks and
writes the notebook; the conscious speaks from it.** `remind` is the only
bridge from the subconscious to the conscious; the conscious reaches the
subconscious only as Record and as shared entries.

| family | the subconscious adaptor that owns it | what the conscious layer writes | the conscious reads it (pushed · pulled) | the subconscious reads it | timescale |
|---|---|---|---|---|---|
| Affairs · note | Cognition (after step 4; note-candidates and the curator until then) | the tag turn's `note_create`, `note_update`: what it was just told | `<notes>` titles · `note_read`, `note_search` | index lines in the grounding · the same tools | minutes |
| Affairs · memo | Cognition | «記低» about the unit or someone else: `memo_write` (step 7); about the speaker it stays `remember`, into memory (Q5) | `<memos>` titles · `memo_read` | the same | minutes |
| Affairs · event | Cognition | the tag turn's event verbs | `<events>`, 14 days ahead and 48 hours back · `event_search` | index lines · `event_search` | minutes, and its date |
| Portraits | the Character adaptor, alone | nothing; a person's `character_dispute` is Record that wakes the adaptor | `culture` (room), `self` (Nora, step 8) · `character_read` | Cognition pulls; the adaptor reads its own chain | days |
| Positions | Cognition | the debrief revises the room's Standing in the reply slot, through `standing_revise` (live, DEC-239) | `standing` and its working set (room) · `standing_read` | pulled only, never pushed into the grounding | per session; per tag turn |
| Lessons | the Lessons adaptor, alone | nothing; the operator retires on the portal | `lessons` (step 8) | pushed | days |

**How the conscious layer's writes enter** (the position held until §11 Q3 is
answered): through the same verbs and the same validator as the owner's, as
entries in the owner's family, never as a command to the subconscious. The
tag turn writes what it was **told** on that turn (a note, a memo, an event);
the debrief writes room Standing. The owner adaptor revisits them on its next
session like any other entry. «記低» about the speaker alone goes to memory,
not to an entry ([40](40-register.md) §1).

## 9. The flow

```
OBSERVED: the evidence                         AUTHORED: the notebook (entries)
sources ──ingestion (code)──▶ RECORD ──Rollup builder (code)──▶ ROLLUP
                               │  ▲                               │
                               │  │ her sends, reactions to them, │
                               │  │ tag-path face_label, dispute  │
                               └──┼──── stimuli ◀─────────────────┘
                                  │        │
                                  │        ▼  wake evaluator (code)
                                  │   SUBCONSCIOUS: the Dreamer's adaptors
                                  │     Cognition ──writes──▶ Affairs, Positions ──┐
                                  │     Character ──writes──▶ Portraits            ├─▶ ENTRIES ──cite──▶ Record, Rollup
                                  │     Lessons   ──writes──▶ Lessons              │   (warehouse: Rollup + entries)
                                  │     Cognition ──wake_at──▶ stimuli             │
                                  │     Cognition ──remind()──┐  (the one bridge)  │
                                  │                           │                    ▼
                                  │                           │      MARTS (read time, one builder:
                                  │                           │       slice + lineage + withdrawal + age)
                                  │                           │        ├─▶ agenda grounding ─▶ Cognition (thinks)
                                  │                           ▼        ├─▶ tag-turn blocks ──▶ tag turn  ─┐
                                  │                    due reminder ──▶├─▶ fire-turn blocks ─▶ fire turn ─┤ CONSCIOUS
                                  │                                    └─▶ portal pages ────▶ a person    │ (speaks)
                                  └────────────── her words are Record ◀─────────────────────────────────┘
                                   tag turn: told notes, memos, events; debrief: room Standing ──▶ ENTRIES (same verbs)
```

Evidence flows up; claims point down at it; each reader gets a slice cut for
its room at the moment it reads.

## 10. Migration: built, converging, in what order

**Built today** (read from `nora/infra/sql/schema.py` on 2026-10-01):

| family · kind | tables | key | chain | derived columns ([10](10-tiers.md) §3) | citation edges | in the cascade |
|---|---|---|---|---|---|---|
| Affairs · note | `note`, `note_revision` | unit | `note_revision`, body restated (ADR 0018) | yes | message ids parsed from the body | yes |
| Affairs · event | `events`, `event_revision`, `event_rsvps` | origin room, plus `unit_id` (legacy rows backfilled by a command) | `event_revision` (DEC-237) | per field | **none** | **no** |
| Positions · room | `standing`, `standing_revision` | room (platform, conversation) | `standing_revision` | yes | one message id (the fold's watermark) | yes |
| Portraits · room | `culture`, `culture_revision` | room | `culture_revision` | yes | one message id (the room's newest) | yes |
| Affairs · memo; Portraits · person, unit, Nora; Positions · unit, person; Lessons | not built | — | — | — | — | — |

Every table above keeps `row_history` (DEC-232). The `citation` table accepts
only `note`, `standing` and `culture` as citing stores and only `message` as a
cited store (`nora/infra/sql/citation.py`): Rollup keys are not edges yet, and
Standing's and culture's single edge is a stand-in for the evidence their
bodies do not yet cite.

**The convergence order.** Additive throughout: no table is dropped
([70](70-migration.md) §5), and a kind moves onto the entry only with render
parity (its blocks and tool results byte-for-byte unchanged, the gate steps 1b
and 2 already use).

| order | what | when ([70](70-migration.md) §2) |
|---|---|---|
| E1 | **memo, the first kind built on the entry**: the entry store and chain, the widened `citation` table (every citing kind; Record and Rollup cited stores), `about` and `links`, the generic verbs and validator skeleton, and the mart builder serving `<memos>` and `memo_read` | step 7, or earlier if the operator moves it (Q7) |
| E2 | **Portraits and Lessons are born on it**: person, unit and Nora portraits, lessons; unit and person Positions | step 8 |
| E3 | **existing kinds converge as they are touched**: culture when it is re-sectioned (step 4 or step 8, Q8); room Standing when it gains sections with their own age (step 8, [70](70-migration.md) §4); events and notes last, each when its next schema change lands, behind its tag tools' `tool_selection` measurement | per kind |
| E4 | **the marts converge on one builder**, consumer by consumer, each with a parity test; the clocked adaptors' builders are retired with the adaptors at step 4, not converged | alongside E1–E3 |

**What step 4 (cut-over) needs from this node.** (a) The owner rule for
Affairs and Positions (Q3), since the cut-over makes Cognition their owner and
retires note-candidates, the curator, note-merge and the ingest Standing fold.
(b) Events inside the cascade: Cognition revising events live must write
citation edges, or a withdrawn message keeps supporting an event. (c) The
grounding mart and the tag-turn mart agreeing on withdrawal and lineage for
the same rows, because what Cognition writes is what the tag turn reads. (d)
Where culture lands when step 4 re-sections it (Q8).

**What must not be disrupted.** The live Standing path: the debrief and the
ingest fold writing through `standing_revise` (`nora/infra/sql/standing_revise.py`,
DEC-239) into `standing` and `standing_revision`, and the `standing` blocks
read from them. The live notes: the tag turn's `note_create` and
`note_update`, the clocked note passes until step 4, and the `<notes>` block.
The tag path's event verbs (DEC-237). History capture (DEC-232), the cascade
(DEC-238) and `[lineage] mode`'s behaviour (DEC-236). None of these changes
shape until its kind converges, and then only under render parity.

## 11. Open questions for the operator

Each is a place where the agreed concept and the existing design or the built
code disagree. This node states both sides and resolves none of them.

| # | the conflict | the two readings |
|---|---|---|
| Q1 | **"said"**. The concept's example is "Ada said she is vegetarian (said, …)". D2 (00 Status) and [10](10-tiers.md) §4 make a person's statement `told`, and reserve `said` for Nora's own words, Standing only | keep D2 (a person `told`, Nora `said`; §3 above follows it), or rename the modes |
| Q2 | **"computed" on an entry.** The concept lists it among how an entry is known; [10](10-tiers.md) §4 allows it on the Rollup only | an entry never carries `computed` (a value restating a Rollup row is `deduced`, citing it), or entries gain it for fields copied from the Rollup |
| Q3 | **One writer per family.** The concept says one; the design and the code have two for Affairs (Cognition and the tag path) and for Positions (Cognition and the debrief, live since DEC-239) | "one writer" means one owner adaptor plus one write path (shared verbs and validator), the conscious layer writing only what it was told (§8, held until answered); or the conscious layer's writes become Record the owner turns into entries on its next session (strictly one writer, at the cost of latency: a «記低» appears after the next wake, 120 s at best) |
| Q4 | **Entry citing entry.** [10](10-tiers.md) §2 rule 1 lets Character cite the Register and Standing cite Character and Lessons; "authored is not evidence" says an entry never rests on another entry | entry-to-entry references become links (no evidence, not walked by the cascade) and every entry cites the observed items itself; or rule 1 stands, and an entry's evidence may include entries whose own chains end in the Record |
| Q5 | **A person's claim about themselves.** The concept stores "Ada said she is vegetarian" with its message and age; [40](40-register.md) §1 and today's `remember` tool send a settled fact about the speaker to memory (Honcho), which keeps no citation, lineage or age of ours | keep `remember` for the speaker's own facts; or write them as memos about the person (Affairs · memo, subject person), with memory fed as today |
| Q6 | **Family names beside tier names.** [10](10-tiers.md) makes a second word for a tier a defect; the concept names families Affairs, Portraits, Positions, Lessons. "Portrait" also names the deprecated `room_portrait` table (DEC-121) | families as plain-language names beside the code words (this node's choice, pending), or the family names replace Register, Character and Standing everywhere |
| Q7 | **Memo first.** The concept builds memo first on the entry; [70](70-migration.md) §2 ships memos at step 7, after social and faces | memo (E1) ships after step 4 and before steps 5–6, or stays at step 7 |
| Q8 | **Culture at cut-over.** Step 4 re-sections culture and hands it to the Character adaptor; moving it onto the entry then adds a storage migration to the riskiest step | converge culture at step 4, or re-section it in place at step 4 and move it onto the entry at step 8 with the other portraits |
| Q9 | **One mart path against the built consumers.** Three prompt assemblers (tag, fire, grounding), a lineage call per read tool, and portal pages with no lineage or withdrawal (§7) | converge them all (E4), or accept named exceptions, each pinned by a test the way `markup=False` is |
| Q10 | **Documents** (`documents`, `document_revision`, ADR 0020) are neither observed nor written about what happened: they are her work product | Record (her own act, under the echo rule), or a fifth authored family |
| Q11 | **The portal's events page** shows every event a person created or answered, across their groups, with no lineage and no withdrawal (`nora/web/gatherings.py`) | keep it participation-scoped, or serve it from the one mart builder with the viewer's rooms |
| Q12 | **"Authored" means two things.** [15](15-map.md) §1 calls doctrine and config "Authored" (a person decided it); this node calls entries "authored" (written about what happened) | rename [15](15-map.md)'s row (for example "Configured"), or name this node's kind differently |

## Status / Checkpoint

**2026-10-01 — proposed, design only; the concept agreed with the operator the
same day** (DEC-249). Nothing is built by this node. The answers to §11 move
the rows they name; until then the bundle's other nodes hold as written, with
pointers here.
