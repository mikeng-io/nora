---
type: design
title: "Data model — the warehouse: entries that aggregate the lake, written only by the subconscious"
description: "The data model the operator agreed on 2026-10-01 and corrected the same day: this node models the WAREHOUSE only, the aggregated entries (Register: note, memo, event; Character; Standing; Lessons). The lake (the Record: transcripts, audio, photos, reactions, presence) and memory (Honcho: per-person facts, who said what about themselves) are its neighbours and are not modelled here. Every entry is one object with one machinery; it references the lake for audit, says whether it was observed or inferred and how sure, links to other entries, and is never evidence. The subconscious (the Dreamer's adaptors) is the only writer; the conscious turn speaks and hands off. Each consumer reads a mart assembled at read time through one path. The flow, what is built, the convergence order, and the questions left."
tags: [nora, data-model, warehouse, entry, register, character, standing, lessons, lineage, mart, two-layers]
timestamp: '2026-10-01'
edges:
  - {dst: 10-tiers, rel: sequences, provenance: asserted}
  - {dst: 15-map, rel: motivates, provenance: asserted}
  - {dst: 55-self, rel: depends_on, provenance: asserted}
  - {dst: 70-migration, rel: motivates, provenance: asserted}
  - {dst: 80-prior-art, rel: depends_on, provenance: asserted}
---

# 05 · Data model: the warehouse

**This node is canonical for the warehouse**: what an entry is, the machinery
every entry shares, who writes it, how it is read (the marts). [10](10-tiers.md)
stays canonical for the tiers' columns, keys and words, amended where the
operator ruled on 2026-10-01 (DEC-249).

## 1. In plain words

Nora has three places data lives. The **lake** holds what happened, raw: every
transcript line, voice recording, photo, reaction and location fix, who said
what to whom and when. **Memory** (Honcho) holds what she knows about each
person, including what they have said about themselves. The **warehouse**,
this node, holds what she has made of it: the unit's open questions, settled
things and plans, what a person or a room is like, where she stands, what she
has learned.

Raw statements live in the lake; per-person facts live in Honcho; the
warehouse aggregates. An entry in the warehouse points back into the lake so it
can be audited, says whether it was observed or inferred and how sure it is,
and nothing more about who said what: that is the lake's and memory's job.

The subconscious (the Dreamer's adaptors) is the only writer of the warehouse.
The conscious (the turns that speak) speaks from it, and when something
should be written it hands off: the subconscious wakes within minutes and
writes it.

## 2. Scope: the warehouse and its two neighbours

| place | what it holds | modelled |
|---|---|---|
| **lake** | the Record: messages with edits, reactions and deletes, audio, media and what they show, presence, social items, play, her own sends and decisions; and the Rollup, the sums code computes from it and can rebuild ([20](20-observe.md), [25](25-rollup.md)) | in [20](20-observe.md) and [25](25-rollup.md), not here |
| **memory** | Honcho: per-person facts, what a person said about themselves (`remember`), the peer card | Honcho's model, not ours ([80](80-prior-art.md) §6) |
| **warehouse** | entries: the Register (note, memo, event), Character (a person, a room or the unit, which is culture, and Nora), Standing, Lessons | **here** |

Outside all three, unchanged: doctrine and configuration (the operator's, the
law), operator-only rows (tension, assess reports), and the machinery of act
and pace (reminders, stimuli).

**Nothing here is a knowledge graph.** A group chat cannot prove truth, so the
warehouse never stores a bare fact as settled truth, and no store of
extracted subject-predicate-object facts or entity clusters is built
([80](80-prior-art.md) §5 refuses them; the deprecated fact tier, #115, was
one). What the warehouse keeps is aggregates with a path back into the lake.

## 3. The entry

Every warehouse item is an **entry**: one shape, one machinery.

| part | what it holds | set by |
|---|---|---|
| **subject** | what it is about: a person, a room or the unit, Nora, or the affair itself (a note, a memo, an event) | the write verb |
| **kind** | note, memo, event, character, standing, lesson | the verb |
| **body** | Markdown under the kind's fixed schema ([12](12-schemas.md)); an event also has typed columns | the model; parsed by code, refused not repaired |
| **lake references** | the Record ids and Rollup keys the entry was built from, for audit | the model names them; code resolves them and writes the edges |
| **basis** | `observed` (the lake shows it: people settled it in the room, or code computed it) or `inferred` (Nora concluded it) | the verb, checked by code: presence fixes or a face older than the freshness threshold cap it at `inferred` ([10](10-tiers.md) §4) |
| **confidence** | `low`, `medium`, `high` | the verb |
| **visibility** | lineage (the rooms its lake references may be shown in, [10](10-tiers.md) §3) and `exclude_persons` | code, from the references; never the model |
| **time** | `as_of`, valid time on the Register, support and age on Character, Standing and lessons, system time | code |
| **lifecycle** | the kind's states (below) | the verb, under the kind's rules |
| **revisions** | an append-only chain; a write names `base_revision_id`, and a stale one is refused `stale_base` | code |
| **withdrawal** | `unsupported` when a lake item it references is withdrawn, `hidden` when forgotten or retained out; neither deletes | code |
| **about** | persons, places, rooms it names: an index for finding it | the verb, resolved by code |
| **links** | typed references to other entries (§5) | the verb |
| **provenance** | the adaptor and session that wrote it, the model, the instruction hash | code |

The grain of `basis` and `confidence` is the row, an event's field, or a
section of a Character or Standing entry, as [10](10-tiers.md) §3 sets it.
**An inferred value goes out as a question, never as a stated fact**, unless
its confidence is `high` ([40](40-register.md) §4's rule, restated over the
basis); a "yeah" to her own guess changes nothing ([10](10-tiers.md) §2
rule 4).

**One machinery for every kind**: storage, the revision chain and
compare-and-swap, the reference edges, the withdrawal cascade and hiding,
lineage, as-of reads, the read and search verbs, the marts (§6).

**What differs per kind is a rule, stated and tested on its own.** Unifying
the machinery hides the differences between kinds; it does not remove them.
Each cell is one rule, and each gets its own named test when its kind is
built on the entry, never one test run over a table that passes whatever the
table says.

| kind | tier | writer | body schema | lifecycle | basis | revisited when |
|---|---|---|---|---|---|---|
| note | Register | Cognition | [12](12-schemas.md) §3 (DEC-213) | open → retired or replaced | observed, inferred | its anchor, `revisit_at`, strikes |
| memo | Register | Cognition | [12](12-schemas.md) §3 | kept → withdrawn; never closes | observed | when a referenced message is edited or deleted |
| event | Register | Cognition | columns + [12](12-schemas.md) §3 | staged → proposed → confirmed → happened; cancelled, lapsed, evicted ([40](40-register.md) §3) | per field | its date; self-wakes |
| character | Character | the Character adaptor | [12](12-schemas.md) §4 | one current account; a section past 60 days moves to `Unsure` | inferred, from many instances | enough new evidence, or 7 days stale; a dispute at once |
| standing | Standing | Cognition | [12](12-schemas.md) §5 | one current position; a section past 14 days is dropped | observed, inferred | each session that touches its subject |
| lesson | Lessons | the Lessons adaptor; the operator retires | [12](12-schemas.md) §4a | active → retired, under a size cap per unit | inferred, from outcomes | 30 new outcomes, 7 days stale, a 60-day horizon |

## 4. Four families: a grouping, not new names

The kinds group by **what they are about** and **what kind of statement they
make**. The groups are the tiers; they keep the tiers' names:

| tier | about | kind of statement | the question | kinds |
|---|---|---|---|---|
| **Register** | the group's affairs | a state of affairs: open, settled or dated | what is going on in the unit? | note (an open question; closes on its condition), memo (settled; never closes, revised or withdrawn), event (a dated plan or happening) |
| **Character** | what someone or something is like | a description | what is this entity like? | a person's, a room's or the unit's (culture), Nora's |
| **Standing** | where Nora stands | her position | where does she stand toward this room, the unit, this person? | room, unit, person |
| **Lessons** | how Nora acts here | a prescription | what has she learned about acting in this unit? | lesson |

The two axes keep a question from having two homes: "who has not paid" is a
note, "Cy usually pays late" is a Character line (it rests on an aggregate),
"I owe them the receipt" is Standing, "when they split a bill, give the
per-head amount first" is a lesson.

## 5. References: into the lake, about, and between entries

Storage stays relational. The edges are three typed references, each a table
or a column, never parsed out of a body:

| reference | from → to | what code does with it |
|---|---|---|
| **lake reference** | entry → Record id or Rollup key | lineage is computed from it; the withdrawal cascade walks it (`nora/app/withdrawal.py` over the `citation` table) |
| **about** | entry → person, place, room, unit | finds entries by subject; forget hides the entries about a person ([70](70-migration.md) §9) |
| **link** | entry → entry, typed: a note *tracks* an event, a note *closes into* a memo, an event *leaves behind* a memo, a Standing line *is informed by* a Character line | `exclude_persons` is unioned across it ([10](10-tiers.md) §3); a link to a hidden or unsupported entry is not followed at render |

**Evidence is only the lake** (operator 2026-10-01). An entry may link to
another entry, and a link is never evidence: the cascade does not walk it,
and an entry's own lake references are what it rests on. This amends
[10](10-tiers.md) §2 rule 1, which let Character cite the Register and
Standing cite Character and Lessons.

A graph is a way of reading these references ("everything about Bo", "what
this note became"): walks in SQL over typed rows. There is no graph store.

**Recommendation needs no new store** (the operator's future use case),
worked through with synthetic people. Bo asks the room where to eat on Friday.

1. The turn's mart already holds the unit's `<events>` and `<memos>` indices,
   the room's culture and the unit's lessons (§6).
2. It pulls what the question needs: `character_read(Bo)` (prefers noodles,
   inferred from a place aggregate); `event_search(person=Bo)` (the last
   three dinners); `rollup_read(place, Bo)` (where Bo spends Friday
   evenings, from the lake); Honcho's `about(Cy)`, which says Cy is
   vegetarian, a per-person fact memory holds; and the lesson "two options,
   not five".
3. It decides in the turn: two places, one with vegetarian dishes, each with
   why. If the unit settles on one, that is later an event, written by
   Cognition.

A read across the warehouse, the lake's Rollup and memory, then a decision.

## 6. Marts: every consumer reads one slice, cut at read time, by one path

| layer | Nora | stored? |
|---|---|---|
| lake | the Record (and the Rollup computed from it) | yes |
| warehouse | the entries | yes, versioned |
| **mart** | a **per-consumer read view**: the slice one consumer reads for one room, now or as of T | **never**; assembled at read time |
| dashboard, analyst | the conscious turn (speaks from it) and the Cognition session (thinks with it) | — |

A mart selects what its consumer declares (tiers, kinds, subjects, window,
size cap); applies **lineage for the consumer's room** (the turn's room, each
agenda item's bound room, the viewer's rooms on the portal); applies
**withdrawal** (an unsupported or hidden entry is absent, in every lineage
mode); and renders each row with its age, basis and confidence, through
`ContextBlock` for a prompt.

**One assembly path.** Every consumer gets its slice from one builder. A
consumer declares its slice and never writes its own selection, lineage or
withdrawal code. The repo already holds this rule one layer down: one block
injector (`ContextBlock`, `tests/architecture/test_one_block_injector.py`),
one transcript renderer (`render_messages`,
`tests/architecture/test_one_transcript_renderer.py`), one media join. The
per-consumer difference stays visible as the declared slice, pinned by that
consumer's own test.

**The marts that exist today, and where they depart from one path** (read
from the tree on 2026-10-01; each departure is a migration item, §9):

| mart | consumer | assembled by | lineage | withdrawal | departure |
|---|---|---|---|---|---|
| the tag turn's blocks | tag turn | `nora/engagement/context.py` with `build_prefill` (`nora/agent/prefill.py`) | per room, behind `[lineage] mode` (shipped `off`) | notes in the note store's reads; culture and Standing in `nora/engagement/context.py` | the reference path; no `<events>`, `<memos>`, `self` or `lessons` block yet |
| the fire turn's blocks | fire turn | its own `build_context` in `nora/app/fire.py`, sharing `build_prefill` | the same `LineagePolicy` | as the tag turn's | a second assembler |
| the agenda's grounding | Cognition session | `nora/app/cognition_grounding.py`, with its own renderer | its own call into `nora/agent/lineage.py`; a row must pass for every room on the agenda | in every mode | a third assembler and renderer |
| read tools | both layers | each tool: `note_read`/`note_search` (`nora/agent/tools.py`), `nora/agent/standing_read.py`, `nora/infra/sql/record_query.py`, `rollup_read` | each applies it itself | each applies it itself | one call site per tool |
| the clocked adaptors' prompts | note-candidates, the curator, note-merge, proactive, culture, the ingest Standing fold, the debrief | each module's own builder (`nora/language/note_candidates.py`, `nora/language/proactive.py`, `nora/language/culture.py`, `nora/ingest/stages/standing_event.py`, `nora/engagement/debrief_render.py`) | none: they read unit-wide and never speak | notes through the store's reads | retired with the adaptors at step 4 |
| portal pages | a person | `nora/web/gatherings.py` (events they created or answered), `nora/web/documents.py` | none: participation-scoped | none: events are outside the cascade | no page for notes, memos, Character, Standing or lessons yet |

## 7. The two layers: the subconscious writes, the conscious speaks and hands off

The operator's model ([55](55-self.md) §6): **the subconscious thinks and
writes the warehouse; the conscious speaks from it.** Ruled 2026-10-01: the
subconscious is the **only** writer of the warehouse. Cognition writes the
Register and Standing; the Character and Lessons adaptors write theirs.

| tier | written by | how the conscious turn hands off | the conscious reads it (pushed · pulled) | timescale |
|---|---|---|---|---|
| Register · note, event | Cognition | the messages are already in the lake and raise stimuli; a date, time or RSVP is a strong cue | `<notes>`, `<events>` · `note_read`, `note_search`, `event_search` | minutes |
| Register · memo | Cognition | «記低» about the unit is a keep instruction, a strong cue; «記低» about the speaker stays `remember`, into Honcho | `<memos>` · `memo_read` | minutes |
| Standing | Cognition | the tag turn raises a hand-off stimulus with a strong cue when it ends, in place of the debrief | `standing` (room) · `standing_read` | minutes |
| Character | the Character adaptor | none; a person's `character_dispute` is a lake item that wakes it | `culture`, `self` · `character_read` | days |
| Lessons | the Lessons adaptor | none; the operator retires on the portal | `lessons` | days |

**The hand-off.** A strong cue wakes Cognition after `cue_quiet_seconds` of
quiet, 120 s today ([20](20-observe.md) §3), so a «記低» about the unit lands
in about two minutes; the unit's cooldown (300 s) can delay it, and a strong
cue may cut the cooldown once. The turn therefore never says a thing is
already written; it may say it will be. Reminders stay the tag turn's: they
are machinery, not the warehouse.

## 8. The flow

```
LAKE (code writes)                                    WAREHOUSE (only the subconscious writes)
sources ──ingestion──▶ RECORD ──Rollup builder──▶ ROLLUP
                        │   ▲                          │
                        │   │ her words, and people's  │
                        │   │ answers to them          │
                        └───┼──── stimuli ◀────────────┘ ◀── hand-off: «記低», end of a tag turn
                            │        │
                            │        ▼  wake evaluator (code), ~2 min on a strong cue
                            │   SUBCONSCIOUS: the Dreamer's adaptors
                            │     Cognition ─────▶ Register, Standing ──┐
                            │     Character ─────▶ Character            ├─▶ ENTRIES ──lake refs──▶ Record, Rollup
                            │     Lessons ───────▶ Lessons              ┘      │ links between entries
                            │     Cognition ── remind() ──┐                    ▼
                            │                             │      MARTS (read time, one builder:
                            │                             │       slice + lineage + withdrawal + age)
                            │                             │        ├─▶ agenda grounding ─▶ Cognition (thinks)
                            │                             ▼        ├─▶ tag-turn blocks ──▶ tag turn  ─┐ CONSCIOUS
                            │                      due reminder ──▶├─▶ fire-turn blocks ─▶ fire turn ─┤ (speaks,
                            │                                      └─▶ portal pages ────▶ a person    │ hands off)
                            └───────────── what she says is lake ◀────────────────────────────────────┘
MEMORY (Honcho): per-person facts, fed from the lake; `remember` for the speaker's own; read as a hint by both layers
```

## 9. Migration

**Built today** (read from `nora/infra/sql/schema.py` on 2026-10-01):

| tier · kind | tables | key | chain | lineage columns | lake references | in the cascade |
|---|---|---|---|---|---|---|
| Register · note | `note`, `note_revision` | unit | `note_revision` | yes | message ids parsed from the body | yes |
| Register · event | `events`, `event_revision`, `event_rsvps` | origin room, plus `unit_id` | `event_revision` (DEC-237) | per field | **none** | **no** |
| Standing · room | `standing`, `standing_revision` | room | `standing_revision` | yes | one message id, a stand-in | yes |
| Character · room (culture) | `culture`, `culture_revision` | room | `culture_revision` | yes | one message id, a stand-in | yes |
| memo; Character of a person, the unit, Nora; Standing of the unit and a person; Lessons | not built | | | | | |

Every table above keeps `row_history` (DEC-232). Built writers other than the
subconscious: the tag turn's `note_create`, `note_update` and event verbs, and
the debrief through `standing_revise` (DEC-239). The `reasoning` column holds
[10](10-tiers.md) §4's six modes (a database CHECK, and `standing_revise`
takes `deduced`, `abduced` or `said`), finer than the entry's two-value basis
(Q2).

**Gaps in the built code, each a migration item:**

- events are outside the `citation` table and the cascade: a withdrawn
  message keeps supporting an event;
- the `citation` table references messages only (`nora/infra/sql/citation.py`):
  Rollup keys are not edges;
- Standing and culture each reference one stand-in message id, not what
  their bodies rest on;
- the marts depart from one path in the ways §6 lists.

**The order.** Additive: no table is dropped ([70](70-migration.md) §5), and a
kind moves onto the entry only under render parity (its blocks and tool
results unchanged byte for byte).

| order | what | when |
|---|---|---|
| E1 | **memo, the first kind on the entry**, written only by Cognition: the entry store and chain, the reference table widened to every kind and to Rollup keys, `about` and `links`, the generic verbs, and the one mart builder serving `<memos>` and `memo_read` | right after step 3's shadow week, before the cut-over (ruled 2026-10-01; was step 7) |
| E2 | **the single writer**: the tag turn's `note_create`, `note_update` and event verbs and the debrief's Standing write move behind Cognition, replaced by the hand-off stimulus (§7) | at the cut-over, step 4, or right after it |
| E3 | Character of a person, the unit and Nora, Lessons, and Standing of the unit and a person are born on the entry | step 8 |
| E4 | culture, room Standing, events and notes converge onto the entry as each is next touched | per kind (culture's timing is Q1) |
| E5 | the marts converge on one builder, consumer by consumer, each with a parity test | alongside E1 to E4 |

**What the single writer costs** (E2). A note, event or memo a person asks for
appears a couple of minutes later instead of in the turn. The tag tools'
text and the doctrine that tells her to write (`nora/doctrine/rooms/conduct.md`,
the «記低» paragraph) change, each measured with `tool_selection` and the
eval before and after (ADR 0015); the speaker's own «記低» through `remember`
does not change. The debrief's reply-slot fold is retired with the ingest
Standing fold. The strong-cue list ([20](20-observe.md) §3) gains the tag
turn's hand-off.

**What step 4 needs.** Events inside the cascade before Cognition revises them
live; the grounding and the tag turn's blocks agreeing on withdrawal and
lineage for the same rows; the hand-off stimulus built before the tag-path
writes are withdrawn.

**What must not be disrupted until its kind converges.** The live Standing
path (the debrief and the ingest fold through `standing_revise` into
`standing` and `standing_revision`, and the `standing` blocks); live notes
(the tag verbs, the clocked note passes until step 4, `<notes>`); the event
verbs (DEC-237); history capture (DEC-232); the cascade (DEC-238);
`[lineage] mode` (DEC-236).

## 10. Questions for the operator

| # | question | the choice |
|---|---|---|
| Q1 | When does culture move onto the entry? Step 4 already rewrites culture into sections and hands it to the Character adaptor. | Move it then (one change, in the riskiest step), or rewrite it in place at step 4 and move it with the other Character kinds at step 8. |
| Q2 | The entry says only `observed` or `inferred`. The built column and `standing_revise` hold six finer modes (told, computed, deduced, induced, abduced, said). | Map them at convergence (told and computed to observed; the rest to inferred) and drop the finer modes, or keep them inside the column as the detail behind the two values. |
| Q3 | The portal's events page shows every event a person created or answered, across all their groups, with no per-room visibility and no withdrawal. | Keep it that way, or serve it from the one mart builder, showing only what the viewer's rooms may see. |

## Status / Checkpoint

**2026-10-01 — proposed, design only.** The concept was agreed with the
operator and corrected the same day (DEC-249): this node is the warehouse
only; the lake and memory are neighbours; evidence is only the lake; the
subconscious is the only writer; memo is the first kind on the entry, right
after the shadow week; the tier names stay. Nothing is built by this node.
