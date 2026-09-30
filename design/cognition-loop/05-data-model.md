---
type: design
title: "Data model — five layers, one flow, and the Notebook's entries"
description: "The canonical topology the operator ruled on 2026-10-01: five layers in flow order, each defined by who writes it and how it is derived (Authored, Record, Rollup, Notebook, Views), with Memory (Honcho) beside the flow; the boundary rules between them; the vocabulary Layer › Tier › Kind › Entry, where a tier is a subdivision of the Notebook only. Then the Notebook in detail: one object, the entry, with one machinery; its four tiers and their kinds; citations into the Record and Rollup and links between entries; Views assembled at read time through one path; the Cognition loop as the Notebook's only writer, the speaking turns reading Views and raising stimuli; the flow; what is built; the convergence order; the questions left."
tags: [nora, data-model, topology, layers, notebook, views, entry, register, character, standing, lessons, lineage]
timestamp: '2026-10-01'
edges:
  - {dst: 10-tiers, rel: sequences, provenance: asserted}
  - {dst: 15-map, rel: motivates, provenance: asserted}
  - {dst: 55-self, rel: depends_on, provenance: asserted}
  - {dst: 70-migration, rel: motivates, provenance: asserted}
  - {dst: 80-prior-art, rel: depends_on, provenance: asserted}
---

# 05 · Data model

**This node is canonical for the topology and the vocabulary** (operator
2026-10-01, DEC-249: "we need clean topologies and hierarchies before we really
start building"; "I prefer meaningful and accurate names"). Each name below is
defined once, here, and used with this meaning everywhere else.
[10](10-tiers.md) stays canonical for the columns, keys, modes and remaining
words of the Notebook's tiers.

## 1. The topology

**Layers, in flow order.** A layer is defined by its authority: who writes it,
and how it is derived.

| layer | holds | written by | derived how | lifecycle | evidence? |
|---|---|---|---|---|---|
| **Authored** | doctrine, the Dreamer's objectives and skills, `config.toml`, `policy.toml`, `identity.toml`, reference photos for faces | humans only | written by hand | versioned files, each load recorded ([70](70-migration.md) §5) | no: it is the law, not a claim about the world |
| **Record** | raw observations: transcripts, audio, photos and what they show, reactions, edits, deletes, presence, social items, play, Nora's own sends and decisions | ingestion code only | observed | append-only; hidden on forget, never deleted ([10](10-tiers.md) §8) | **yes** |
| **Rollup** | episodes and aggregates: stays, trips, co-locations, outings, a person's day, places, rhythm, ties, responses to her ([25](25-rollup.md)) | the Rollup builder, code only | computed from the Record, deterministic | rebuildable and disposable; versioned builds | **yes**, as computed from the Record |
| **Notebook** | Nora's judgement: entries that aggregate what the Record and Rollup show, in four tiers (§2) | the Cognition loop only (§7) | judged by a model, citing the Record and Rollup | revised, withdrawn, versioned; hidden on forget | no |
| **Views** | per-reader slices: a turn's blocks, a session's agenda, a portal page | one assembly path, code (§6) | assembled at read time under lineage and withdrawal | **never stored** | no |

**Beside the flow: Memory.** Honcho: per-person understanding, and what people
say about themselves (`remember`). Outside our system; fed from the Record; may
be read into Views, never copied into the Notebook and never cited.

**Boundary rules:**

1. **One-way flow**: Record → Rollup → Notebook → Views. Nothing writes
   backwards. What Nora says enters the Record as an observation, like anyone
   else's words. Authored configures every layer and is written by none.
2. **Only the Record, and the Rollup computed from it, is evidence.** A
   Notebook entry cites them; it is never evidence itself, and a reference
   from one entry to another is a link, not a citation (§5).
3. **One writer per layer**: humans for Authored, ingestion for the Record, the
   Rollup builder for the Rollup, the Cognition loop for the Notebook, the one
   assembly path for Views.
4. **Speaking turns only read Views and speak.** To change the Notebook they
   raise a stimulus, which wakes the Cognition loop (§7).
5. **Scope is explicit on every row**: a person, a room, the unit or the
   workspace (DEC-214's hierarchy). The unit is the read boundary (DEC-231);
   lineage decides per room what a View may show ([10](10-tiers.md) §3).

Not layers, and outside the flow: operator-only rows (tension, assess
reports), which no View shows her; and the loop's machinery (stimuli, wakes,
sessions, reminders), which is state, not data about the world.

The operator's first analogy was a data lake, a warehouse and data marts: the
Record is the lake, the Notebook the warehouse, Views the marts. The layer
names above replace those words; they are not used as names.

## 2. The vocabulary: Layer › Tier › Kind › Entry

| level | means | the members |
|---|---|---|
| **layer** | one of the five above, by who writes it and how it is derived | Authored, Record, Rollup, Notebook, Views (Memory beside) |
| **tier** | a subdivision **inside the Notebook only**, by what it is about | Register, Character, Standing, Lessons |
| **kind** | a row type within a tier | Register: note, memo, event · Character: person, room, unit, Nora · Standing: room, unit, person · Lessons: lesson |
| **entry** | one row of a kind | — |

The Record and the Rollup are layers, not tiers. "Tier" names nothing outside
the Notebook.

| tier | about | kind of statement | the question |
|---|---|---|---|
| **Register** | the unit's affairs | a state of affairs: open (note), settled (memo), dated (event) | what is going on in the unit? |
| **Character** | what someone or something is like; culture is the Character of a room or the unit | a description | what is this entity like? |
| **Standing** | where Nora stands | her position | where does she stand toward this room, the unit, this person? |
| **Lessons** | how Nora acts here | a prescription | what has she learned about acting in this unit? |

The two axes keep a question from having two homes: "who has not paid" is a
note, "Cy usually pays late" is a Character line (it rests on an aggregate),
"I owe them the receipt" is Standing, "when they split a bill, give the
per-head amount first" is a lesson.

**Raw statements live in the Record; per-person facts live in Memory; the
Notebook aggregates.** The Notebook models no statement-level provenance (who
said what, to whom): that is the Record's and Memory's job. Nothing here is a
knowledge graph: no bare fact is stored as truth, and no store of extracted
facts or entity clusters is built ([80](80-prior-art.md) §5; the deprecated
fact tier, #115, was one).

## 3. The entry

Every Notebook row is an **entry**: one shape, one machinery.

| part | what it holds | set by |
|---|---|---|
| **scope** | the unit it belongs to, and its origin room | code |
| **subject** | what it is about: a person, a room or the unit, Nora, or the affair itself (a note, a memo, an event) | the write verb |
| **tier, kind** | §2 | the verb |
| **body** | Markdown under the kind's fixed schema ([12](12-schemas.md)); an event also has typed columns | the model; parsed by code, refused not repaired |
| **citations** | the Record ids and Rollup keys it was built from, for audit | the model names them; code resolves them and writes the edges |
| **basis** | `observed` (the Record or Rollup shows it: people settled it, or code computed it) or `inferred` (Nora concluded it) | the verb, checked by code: presence fixes or a face older than the freshness threshold cap it at `inferred` ([10](10-tiers.md) §4) |
| **confidence** | `low`, `medium`, `high` | the verb |
| **visibility** | lineage (the rooms its citations may be shown in) and `exclude_persons` ([10](10-tiers.md) §3) | code, from the citations; never the model |
| **time** | `as_of`, valid time on the Register, support and age on Character, Standing and lessons, system time | code |
| **lifecycle** | the kind's states (below) | the verb, under the kind's rules |
| **revisions** | an append-only chain; a write names `base_revision_id`, and a stale one is refused `stale_base` | code |
| **withdrawal** | `unsupported` when a cited item is withdrawn, `hidden` when forgotten or retained out; neither deletes | code |
| **about** | persons, places, rooms it names: an index for finding it | the verb, resolved by code |
| **links** | typed references to other entries (§5) | the verb |
| **provenance** | the adaptor and session that wrote it, the model, the instruction hash | code |

`basis` and `confidence` sit on the row, an event's field, or a section of a
Character or Standing entry, as [10](10-tiers.md) §3 sets the grain. **An
inferred value goes out as a question, never as a stated fact**, unless its
confidence is `high` ([40](40-register.md) §4's rule over the basis); a "yeah"
to her own guess changes nothing ([10](10-tiers.md) §2 rule 4).

**One machinery for every kind**: storage, the revision chain and
compare-and-swap, the citation edges, the withdrawal cascade and hiding,
lineage, as-of reads, the read and search verbs, and Views (§6).

**What differs per kind is a rule, stated and tested on its own.** Unifying
the machinery hides the differences between kinds; it does not remove them.
Each cell is one rule, and each gets its own named test when its kind is
built on the entry, never one test run over a table that passes whatever the
table says.

| tier · kind | writer | body schema | lifecycle | basis | revisited when |
|---|---|---|---|---|---|
| Register · note | Cognition | [12](12-schemas.md) §3 (DEC-213) | open → retired or replaced | observed, inferred | its anchor, `revisit_at`, strikes |
| Register · memo | Cognition | [12](12-schemas.md) §3 | kept → withdrawn; never closes | observed | when a cited message is edited or deleted |
| Register · event | Cognition | columns + [12](12-schemas.md) §3 | staged → proposed → confirmed → happened; cancelled, lapsed, evicted ([40](40-register.md) §3) | per field | its date; self-wakes |
| Character · person, room, unit, Nora | the Character adaptor | [12](12-schemas.md) §4 | one current account; a section past 60 days moves to `Unsure` | inferred, from many instances | enough new evidence, or 7 days stale; a dispute at once |
| Standing · room, unit, person | Cognition | [12](12-schemas.md) §5 | one current position; a section past 14 days is dropped | observed, inferred | each session that touches its subject |
| Lessons · lesson | the Lessons adaptor; the operator retires | [12](12-schemas.md) §4a | active → retired, under a size cap per unit | inferred, from outcomes | 30 new outcomes, 7 days stale, a 60-day horizon |

## 4. Where each layer is specified

| layer | node |
|---|---|
| Authored | [55](55-self.md) §3 (doctrine), [70](70-migration.md) §5 (versioned at load) |
| Record | [20](20-observe.md), [60](60-faces.md) |
| Rollup | [25](25-rollup.md) |
| Notebook | this node §3, §5; [10](10-tiers.md), [12](12-schemas.md), [40](40-register.md), [50](50-character-and-standing.md) |
| Views | this node §6; [15](15-map.md) §3–4 |
| Memory (beside) | [80](80-prior-art.md) §6 |

## 5. Citations, about, links

Storage stays relational. The edges are three typed references, each a table
or a column, never parsed out of a body:

| reference | from → to | what code does with it |
|---|---|---|
| **citation** | entry → Record id or Rollup key | lineage is computed from it; the withdrawal cascade walks it (`nora/app/withdrawal.py` over the `citation` table) |
| **about** | entry → person, place, room, unit | finds entries by subject; forget hides the entries about a person ([70](70-migration.md) §9) |
| **link** | entry → entry, typed: a note *tracks* an event, a note *closes into* a memo, an event *leaves behind* a memo, a Standing line *is informed by* a Character line | `exclude_persons` is unioned across it ([10](10-tiers.md) §3); a link to a hidden or unsupported entry is not followed when a View is assembled |

A link is never evidence: the cascade does not walk it, and an entry rests on
its own citations. This amends [10](10-tiers.md) §2 rule 1. A graph is a way
of reading these references ("everything about Bo", "what this note
became"): walks in SQL over typed rows. There is no graph store.

**Recommendation needs no new store** (the operator's future use case),
worked through with synthetic people. Bo asks the room where to eat on Friday.

1. The turn's View already holds the unit's `<events>` and `<memos>` indices,
   the room's culture and the unit's lessons (§6).
2. It pulls what the question needs: `character_read(Bo)` (prefers noodles,
   inferred from a place aggregate); `event_search(person=Bo)` (the last
   three dinners); `rollup_read(place, Bo)` (where Bo spends Friday
   evenings); Memory's `about(Cy)`, which says Cy is vegetarian, a
   per-person fact Memory holds; and the lesson "two options, not five".
3. It decides in the turn: two places, one with vegetarian dishes, each with
   why. If the unit settles on one, the Cognition loop later writes the event.

A read across the Notebook, the Rollup and Memory, then a decision.

## 6. Views: every reader gets one slice, cut at read time, by one path

A View selects what its reader declares (tiers, kinds, subjects, window, size
cap), as of now or as of T; applies **lineage for the reader's room** (the
turn's room, each agenda item's bound room, the viewer's rooms on the portal);
applies **withdrawal** (an unsupported or hidden entry is absent, in every
lineage mode); and renders each row with its age, basis and confidence,
through `ContextBlock` for a prompt. Readers: the tag and fire turns (they
speak), the Cognition session (it thinks), the portal (a person reads), later
recommendations.

**One assembly path.** Every reader gets its View from one builder. A reader
declares its slice and never writes its own selection, lineage or withdrawal
code. The repo already holds this rule one level down: one block injector
(`ContextBlock`, `tests/architecture/test_one_block_injector.py`), one
transcript renderer (`render_messages`,
`tests/architecture/test_one_transcript_renderer.py`), one media join. The
per-reader difference stays visible as the declared slice, pinned by that
reader's own test.

**The Views that exist today, and where they depart from one path** (read
from the tree on 2026-10-01; each departure is a migration item, §9):

| View | reader | assembled by | lineage | withdrawal | departure |
|---|---|---|---|---|---|
| the tag turn's blocks | tag turn | `nora/engagement/context.py` with `build_prefill` (`nora/agent/prefill.py`) | per room, behind `[lineage] mode` (shipped `off`) | notes in the note store's reads; culture and Standing in `nora/engagement/context.py` | the reference path; no `<events>`, `<memos>`, `self` or `lessons` block yet |
| the fire turn's blocks | fire turn | its own `build_context` in `nora/app/fire.py`, sharing `build_prefill` | the same `LineagePolicy` | as the tag turn's | a second assembler |
| the agenda's grounding | Cognition session | `nora/app/cognition_grounding.py`, with its own renderer | its own call into `nora/agent/lineage.py`; a row must pass for every room on the agenda | in every mode | a third assembler and renderer |
| read tools | both | each tool: `note_read`/`note_search` (`nora/agent/tools.py`), `nora/agent/standing_read.py`, `nora/infra/sql/record_query.py`, `rollup_read` | each applies it itself | each applies it itself | one call site per tool |
| the clocked adaptors' prompts | note-candidates, the curator, note-merge, proactive, culture, the ingest Standing fold, the debrief | each module's own builder (`nora/language/note_candidates.py`, `nora/language/proactive.py`, `nora/language/culture.py`, `nora/ingest/stages/standing_event.py`, `nora/engagement/debrief_render.py`) | none: they read unit-wide and never speak | notes through the store's reads | retired with the adaptors at step 4 |
| portal pages | a person | `nora/web/gatherings.py` (events they created or answered), `nora/web/documents.py` | none: participation-scoped | none: events are outside the cascade | no page for notes, memos, Character, Standing or lessons yet |

## 7. The Notebook's writer, and how the speaking turns hand off

The operator's model ([55](55-self.md) §6): **the subconscious thinks and
writes the Notebook; the conscious speaks from it.** The Notebook's one writer
is the **Cognition loop**: the Dreamer's adaptors, Cognition for the Register
and Standing and its slow reflection adaptors for Character and Lessons
([00](00-intent.md) D9). The speaking turns read Views and speak; to change
the Notebook they raise a stimulus.

| tier · kind | written by | how a speaking turn hands off | the turn reads it (pushed · pulled) | timescale |
|---|---|---|---|---|
| Register · note, event | Cognition | the messages are already Record and raise stimuli; a date, time or RSVP is a strong cue | `<notes>`, `<events>` · `note_read`, `note_search`, `event_search` | minutes |
| Register · memo | Cognition | «記低» about the unit is a keep instruction, a strong cue; «記低» about the speaker stays `remember`, into Memory | `<memos>` · `memo_read` | minutes |
| Standing | Cognition | the tag turn raises a hand-off stimulus with a strong cue when it ends, in place of the debrief | `standing` (room) · `standing_read` | minutes |
| Character | the Character adaptor | none; a person's `character_dispute` is a Record item that wakes it | `culture`, `self` · `character_read` | days |
| Lessons | the Lessons adaptor | none; the operator retires on the portal | `lessons` | days |

**The hand-off.** A strong cue wakes Cognition after `cue_quiet_seconds` of
quiet, 120 s today ([20](20-observe.md) §3), so a «記低» about the unit lands
in about two minutes; the unit's cooldown (300 s) can delay it, and a strong
cue may cut the cooldown once. The turn never says a thing is already
written; it may say it will be. Reminders stay the tag turn's: they are
machinery, not the Notebook.

## 8. The flow

```
AUTHORED (humans): doctrine, config, policy, identity ──configures──▶ every layer below

RECORD (ingestion code) ──▶ ROLLUP (Rollup builder, code)
  ▲   │                        │
  │   └──── stimuli ◀──────────┘ ◀── hand-off from a speaking turn: «記低», the end of a tag turn
  │            │
  │            ▼  wake evaluator (code): about two minutes on a strong cue
  │      COGNITION LOOP (the subconscious: the Dreamer's adaptors)
  │        Cognition ──▶ Register, Standing ──┐
  │        Character ──▶ Character            ├─▶ NOTEBOOK (entries) ──cite──▶ Record, Rollup
  │        Lessons   ──▶ Lessons              ┘        │ links between entries
  │        Cognition ── remind() ──┐                   ▼
  │                                │        VIEWS (read time, one assembly path:
  │                                │         slice + lineage + withdrawal + age)
  │                                │          ├─▶ agenda grounding ─▶ Cognition session (thinks)
  │                                ▼          ├─▶ tag-turn blocks ──▶ tag turn  ─┐ SPEAKING TURNS
  │                         due reminder ────▶├─▶ fire-turn blocks ─▶ fire turn ─┤ (read Views,
  │                                           └─▶ portal pages ────▶ a person    │  speak, hand off)
  └──────────────── what she says is observed into the Record ◀──────────────────┘

MEMORY (Honcho, beside the flow): fed from the Record; `remember`; read into Views, never into the Notebook
```

## 9. Migration

**Built today** (read from `nora/infra/sql/schema.py` on 2026-10-01):

| tier · kind | tables | scope | chain | lineage columns | citation edges | in the cascade |
|---|---|---|---|---|---|---|
| Register · note | `note`, `note_revision` | unit | `note_revision` | yes | message ids parsed from the body | yes |
| Register · event | `events`, `event_revision`, `event_rsvps` | origin room, plus `unit_id` | `event_revision` (DEC-237) | per field | **none** | **no** |
| Standing · room | `standing`, `standing_revision` | room | `standing_revision` | yes | one message id, a stand-in | yes |
| Character · room (culture) | `culture`, `culture_revision` | room | `culture_revision` | yes | one message id, a stand-in | yes |
| Register · memo; Character · person, unit, Nora; Standing · unit, person; Lessons · lesson | not built | | | | | |

Every table above keeps `row_history` (DEC-232). Built writers other than the
Cognition loop: the tag turn's `note_create`, `note_update` and event verbs,
and the debrief through `standing_revise` (DEC-239). The `reasoning` column
holds [10](10-tiers.md) §4's six modes (a database CHECK; `standing_revise`
takes `deduced`, `abduced` or `said`), finer than the entry's two-value basis
(Q2).

**Gaps in the built code, each a migration item:**

- events are outside the `citation` table and the cascade: a withdrawn
  message keeps supporting an event;
- the `citation` table cites messages only (`nora/infra/sql/citation.py`):
  Rollup keys are not edges;
- Standing and culture each cite one stand-in message id, not what their
  bodies rest on;
- Views depart from one path in the ways §6 lists.

**The order.** Additive: no table is dropped ([70](70-migration.md) §5), and a
kind moves onto the entry only under render parity (its blocks and tool
results unchanged byte for byte).

| order | what | when |
|---|---|---|
| E1 | **Register · memo, the first kind on the entry**, written only by Cognition: the entry store and chain, the `citation` table widened to every kind and to Rollup keys, `about` and `links`, the generic verbs, and the one View builder serving `<memos>` and `memo_read` | right after step 3's shadow week, before the cut-over (was step 7) |
| E2 | **one writer**: the tag turn's `note_create`, `note_update` and event verbs and the debrief's Standing write move behind Cognition, replaced by the hand-off stimulus (§7) | at the cut-over, step 4, or right after it |
| E3 | Character · person, unit, Nora, Lessons, and Standing · unit, person are born on the entry | step 8 |
| E4 | Character · room (culture), Standing · room, events and notes converge onto the entry as each is next touched | per kind (culture: Q1) |
| E5 | Views converge on one builder, reader by reader, each with a parity test | alongside E1 to E4 |

**What one writer costs** (E2). A note, event or memo a person asks for
appears a couple of minutes later instead of in the turn. The tag tools' text
and the doctrine that tells her to write (`nora/doctrine/rooms/conduct.md`,
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

Open, each with a recommendation:

| # | question | recommendation |
|---|---|---|
| Q1 | When does Character · room (culture) move onto the entry? Step 4 already rewrites culture into sections and hands it to the Character adaptor. | **At step 4**, in the same change that rewrites it, so culture is migrated once. |
| Q2 | The entry states only `observed` or `inferred`; the built column and `standing_revise` hold six finer modes (told, computed, deduced, induced, abduced, said). | **Keep the six as the detail under the two**: told and computed are observed; deduced, induced, abduced are inferred; said stays Standing's mode for her own words. |
| Q3 | The portal's events page shows every event a person created or answered, across all their groups, with no per-room visibility and no withdrawal. | **Serve it through the one Views path**, showing only what the viewer's rooms may see. |

## Status / Checkpoint

**2026-10-01 — proposed, design only.** The concept was agreed with the
operator and corrected twice the same day (DEC-249): first to scope it to
what Nora writes, beside the Record and Memory; then to fix the topology (five
layers, Memory beside them, the boundary rules) and the vocabulary (Layer ›
Tier › Kind › Entry, tier inside the Notebook only; Notebook and Views
replacing "warehouse" and "mart"). Nothing is built by this node.
