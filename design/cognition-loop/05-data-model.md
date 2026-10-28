---
type: design
title: "Data model — five layers, one flow, and the Notebook's entries"
description: "The canonical topology the operator ruled on 2026-10-01: five layers in flow order, each defined by who writes it and how it is derived (Authored, Record, Rollup, Notebook, Views), with Memory (Honcho) beside the flow; the boundary rules between them; the vocabulary Layer › Tier › Kind › Entry, where a tier is a subdivision of the Notebook only. Then the Notebook in detail: one object, the entry, with one machinery; its four tiers and their kinds; citations into the Record and Rollup and links between entries; Views assembled at read time through one path; the subconscious as the Notebook's writer, one writer per tier (Cognition fast, the Character and Lessons adaptors slow), the speaking turns reading Views and raising stimuli; the flow; what is built; the convergence order; the questions left."
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

> 2026-10-21 (ADR 0026): Layer › Tier › Kind › Entry stays canonical. Character keeps person, room, unit and Nora kinds; its entries have profile, traits and hypotheses parts, and "account" now means only platform account. "Persona" is retired; one typed-line `character-*` writer family is the target, with the code rename deferred to one migration PR before the prompt switch. The body below is preserved. See [ADR 0026](../../docs/adr/0026-the-character-tier-names-its-entry-parts-profile-traits-hypotheses.md).

> 2026-10-03 (DEC-277): the shadow period is over. The Cognition session writes live (`[cognition] enabled`) and is the one background writer of notes, events, Standing and her reminders. The clocked note/proactive adaptors, the shadow machinery and `[cognition] mode` / `session_mode` / `shadow_session_seconds` named here are deleted; wording below that describes them is the plan as written, not current behaviour. See `70-migration.md`.


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
| **Record** | raw observations: transcripts, audio, photos and every perception of them, reactions, edits, deletes, presence, social items, play, face labels; Nora's own sends, acts, decisions, **tool calls and their results**, and artifacts (§1a) | code, at the moment it happens: ingestion for what others do; the turn and fire paths for her own sends, acts, tool calls and artifacts; the recording verbs (`face_label`, `character_dispute`) for a person's statement, authored by that person | observed | append-only; hidden on forget, never deleted ([10](10-tiers.md) §8) | **yes** |
| **Rollup** | episodes and aggregates: stays, trips, co-locations, outings, a person's day, places, rhythm, ties, responses to her, face templates ([25](25-rollup.md)) | the Rollup builder, code only | computed from the Record, deterministic | rebuildable and disposable; versioned builds | **yes**, as computed from the Record |
| **Notebook** | Nora's judgement: entries that aggregate what the Record and Rollup show, in four tiers (§2) | the subconscious, one writer per tier: Cognition (Register, Standing), the Character adaptor (Character), the Lessons adaptor (Lessons) (§7); the operator may retire or correct, never author (rule 6) | judged by a model, citing the Record and Rollup | revised, withdrawn, versioned; hidden on forget | no |
| **Views** | per-reader slices: a turn's blocks, a session's agenda, a portal page, an operator page | one assembly path, code (§6) | assembled at read time under lineage and withdrawal | **never stored as data**; a View may be recorded as provenance (a session's situation, a trace), never read back as input | no |

> 2026-10-21 (unified-memory revision): Memory beside the flow is being decomposed into the existing Record, Rollup, Notebook and Views per [18 · Extraction](../unified-memory/18-extraction.md); no new layer is added. The historical description follows.

**Beside the flow: Memory.** Honcho: per-person understanding, and what people
say about themselves (`remember`). Outside our system; fed from the Record
(messages and the perceptions of media, which are Record items); may be read
into Views, never copied into the Notebook and never cited.

**Boundary rules:**

1. **One-way flow**: Record → Rollup → Notebook → Views. Nothing writes
   backwards. What Nora says and does enters the Record as an observation,
   like anyone else's words. Authored configures every layer and is written
   by none.
2. **Only the Record, and the Rollup computed from it, is evidence.** A
   Notebook entry cites them; it is never evidence itself, and a reference
   from one entry to another is a link, not a citation (§5).
3. **One writer per layer, and inside the Notebook one writer per tier**:
   the table's third column. Assess writes nothing in the Notebook: it
   reports.
4. **Speaking turns only read Views and speak.** To change the Notebook they
   raise a stimulus, which wakes the subconscious (§7).
5. **Every row states its home**: the person, room, unit or workspace it
   belongs to (DEC-214's hierarchy). The unit is the read boundary (DEC-231);
   lineage decides per room what a View may show ([10](10-tiers.md) §3).
6. **The operator may retire or correct a Notebook entry, never author one**:
   the revision carries `source='operator'` (as `scripts/standing_edit.py`
   already writes it, and the operator's rollback through `lesson_retire`).
7. **Machinery is not a layer.** Stimuli, wakes, sessions, self-wakes and
   reminders are the loop's append-only state, each with named writers:
   stimuli by ingestion, the Rollup builder, the wake evaluator's clock
   (`time`) and Cognition's pace verbs (`self`); wakes and sessions by the
   wake evaluator and the session runner; reminders by `remind` (tag turn,
   Cognition) and their transitions by the fire path. A reminder's outcome
   (sent, silent, answered) is Record. A reminder's brief may **link** to
   entries, never cite them.

### 1a. Everything else, placed

| what | layer | note |
|---|---|---|
| her tool calls and results (every call, every status, keyed to the turn) | Record | today only cacheable tools that returned `ok` are logged (`tool_call_log`, `nora/agent/guards.py`); a precondition of E2 (§9) |
| her artifacts: documents, generated images, computer sessions and exports | Record (her acts) | the portal page that shows one is a View |
| her Instagram comment replies | Record | today held only in `instagram_summons.reply_text`: a gap (§9) |
| perceptions (what a photo shows, whose face) | Record | stored on the item they read; fed to Memory as Record |
| social items, play samples | Record | step 5 |
| face labels · face templates (embeddings) | Record · Rollup | templates are computed from labelled crops ([60](60-faces.md)) |
| tension rows, assess reports | outside the flow, operator-only | read through an operator View (§6); tension has no table yet, `assess_report` exists |
| caches: `geocode_cache`, `sticker_index`, `video_readings`, the vision cache | outside the layers | rebuildable conveniences, never evidence and never cited |
| a table holding two layers | split when touched | `presence_places` holds Life360's saved places (Record, written through `nora/infra/sql/presence.py`) and, per the review of this node, fields the analytics process derives (Rollup); `messages` carries machinery columns (delivery status) beside the Record row ([10](10-tiers.md) §7) |

The operator's first analogy was a data lake, a warehouse and data marts: the
Record is the lake, the Notebook the warehouse, Views the marts. The layer
names above replace those words; they are not used as names.

## 2. The vocabulary: Layer › Tier › Kind › Entry

| level | means | the members |
|---|---|---|
| **layer** | one of the five above, by who writes it and how it is derived | Authored, Record, Rollup, Notebook, Views (Memory beside) |
| **tier** | a subdivision **inside the Notebook only**, by what it is about | Register, Character, Standing, Lessons |
| **kind** | the row type, at every layer: a Record kind (message, reaction, fix), a Rollup kind (stay, outing, tie), a stimulus kind, and in the Notebook a kind within a tier | Notebook: Register: note, memo, event · Character: person, room, unit, Nora · Standing: room, unit, person · Lessons: lesson |
| **entry** | one Notebook row | — |

The Record and the Rollup are layers, not tiers. "Tier" names nothing outside
the Notebook. A lineage `(kind, subject, origin room)` item is a **triple**,
never an entry ([10](10-tiers.md) §3).

**Words avoided because they collide:** "subject" is the eval subject
(`nora/subject/`), and "scope" is `record_query`'s argument; the entry's
fields are therefore **entity** and **home**.

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

> 2026-10-21 (extraction): statements live in the Record as [memory.extraction](../unified-memory/18-extraction.md); the Notebook aggregates them into judgements. This replaces “per-person facts live in Memory”.

**Raw statements and their readings live in the Record; the Notebook
aggregates.** Every message batch is read once on arrival by a model,
unconditionally scheduled by code. Its reading says about whom, the claim
and how (stated by the person / said by another / planned / asked / reported),
tied to its message ids. A reading records what was said, not a fact asserted
as truth; it is versioned like a Rollup build, rebuildable from the Record,
and superseded, not deleted. This is not #115's fact store: that model chose
what to save (5 rows in 11 days), while code runs a reading on every batch.
Statement-level provenance stays in the Record. Where a body says who settled
something (a memo's `Kept` line, [12](12-schemas.md) §3), the name is read from
the cited message when the View renders it, never stored.

## 3. The entry

> 2026-10-28 (operator, ruled; [30](30-cognition.md#0-the-loop-as-ruled-2026-10-28)): the Character and Lessons adaptors named in this section retire into step ② of the one Cognition loop, which writes Character (traits and guesses; facts are rendered by code), culture and Lessons right after step ① changes an entity's relations; it has no wake rule of its own. Read "the Character adaptor" / "the Lessons adaptor" below as step ②.

Every Notebook row is an **entry**: one shape, one machinery.

| part | what it holds | set by |
|---|---|---|
| **home** | the unit it belongs to, and its origin room | code |
| **entity** | what it is about: a person, a room or the unit, Nora, or the affair itself (a note, a memo, an event) | the write verb |
| **tier, kind** | §2 | the verb |
| **body** | Markdown under the kind's fixed schema ([12](12-schemas.md)); an event also has typed columns | the model; parsed by code, refused not repaired |
| **citations** | the Record ids and Rollup keys it was built from, each with the `part` (field or section) it supports | the model names them; code resolves them and writes the edges |
| **reasoning, basis** | the stored mode is one of [10](10-tiers.md) §4's six; a View shows it as `observed` or `inferred` (how the two relate is Q2, and both recommendations keep the six stored) | the verb, checked and capped by code |
| **confidence** | `low`, `medium`, `high` | the verb |
| **visibility** | lineage (the rooms it may be shown in) and `exclude_persons` ([10](10-tiers.md) §3), by the kind's lineage rule (below) | code; never the model |
| **time** | `as_of`, valid time on the Register, support and age on Character, Standing and lessons, system time | code |
| **lifecycle** | the kind's states (below) | the verb, under the kind's rules |
| **revisions** | an append-only chain; a write names `base_revision_id`, and a stale one is refused `stale_base`; each revision records its `source` (the adaptor, or `operator`) | code |
| **withdrawal** | `unsupported` when a cited item is withdrawn, `hidden` when forgotten or retained out; neither deletes | code |
| **about** | persons, places, rooms it names: an index for finding it | the verb, resolved by code |
| **links** | typed references to other entries (§5) | the verb |
| **provenance** | the adaptor and session that wrote it, the model, the instruction hash | code |

`reasoning` and `confidence` sit on the row, an event's field, or a section of
a Character or Standing entry, as [10](10-tiers.md) §3 sets the grain. **An
abduced value goes out as a question, never as a stated fact, whatever its
confidence, and a "yeah" to her own guess never upgrades it**
([40](40-register.md) §4, [10](10-tiers.md) §2 rule 4).

**One machinery for every kind**: storage, the revision chain and
compare-and-swap, the citation edges, the withdrawal cascade and hiding,
lineage, as-of reads, the read and search verbs, and Views (§6).

**What differs per kind is a rule, stated and tested on its own.** Unifying
the machinery hides the differences between kinds; it does not remove them.
Each cell is one rule, and each gets its own named test when its kind is
built on the entry, never one test run over a table that passes whatever the
table says.

| tier · kind | writer | body schema | lifecycle | lineage from | revisited when |
|---|---|---|---|---|---|
| Register · note | Cognition | [12](12-schemas.md) §3 (DEC-213) | open → retired or replaced | the union of its citations | its anchor, `revisit_at`, strikes |
| Register · memo | Cognition | [12](12-schemas.md) §3 | kept → withdrawn; never closes | the union of its citations | when a cited message is edited or deleted |
| Register · event | Cognition | columns + [12](12-schemas.md) §3 | staged → proposed → confirmed → happened; cancelled, lapsed, evicted ([40](40-register.md) §3) | per field, from the citations of that field (`part`) | its date; self-wakes |
| Character · person, room | the Character adaptor | [12](12-schemas.md) §4 | one current account; a section past 60 days moves to `Unsure` | per section, from its citations | enough new evidence, or 7 days stale; a dispute at once |
| Character · unit, Nora | the Character adaptor | [12](12-schemas.md) §4 | as above | per section: **from `about`** (the persons it names) when the section places nobody; else its citations | as above |
| Standing · room, person | Cognition | [12](12-schemas.md) §5 | one current position; a section past 14 days is dropped | per section, from its citations | each session that touches its entity |
| Standing · unit | Cognition | [12](12-schemas.md) §5 | as above | per section: **from `about`** when the section places nobody; else its citations | as above |
| Lessons · lesson | the Lessons adaptor; the operator retires | [12](12-schemas.md) §4a | active → retired, under a size cap per unit | **from `about`** when it places nobody; else its citations | 30 new outcomes, 7 days stale, a 60-day horizon |

**Why lineage from `about` for unit-wide kinds.** A lesson, the unit's
Character or the unit's Standing is induced from many rooms. Taking its
lineage as the union of every cited message makes it renderable only where
every source room's roster passes, which for a unit with rooms of different
rosters is nowhere. When the text places nobody and quotes nobody, what it
discloses is the persons it names: its `about`. A section that places a
person (presence, a face) keeps the citation rule.

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
| **citation** | entry (and the `part` it supports) → Record id or Rollup key | lineage is computed from it; nothing reads it since 2026-10-03 (the withdrawal cascade that walked it was removed) |
| **about** | entry → person, place, room, unit | finds entries by entity; forget hides the entries about a person ([70](70-migration.md) §9); the lineage source for unit-wide kinds (§3) |
| **link** | entry → entry, typed; **links point down the order Register → Character → Lessons → Standing** and never up: a note *tracks* an event, a note *closes into* a memo, an event *leaves behind* a memo, a room's or the unit's Character *draws on* its members' Characters, a lesson *draws on* a Character line, a Standing line *is informed by* a Character line or a lesson | `exclude_persons` is unioned across it ([10](10-tiers.md) §3); a link to a hidden or unsupported entry is not followed when a View is assembled |

A link is never evidence: the cascade does not walk it, and an entry rests on
its own citations. This amends [10](10-tiers.md) §2 rule 1, which now orders
links, not citations. A graph is a way of reading these references
("everything about Bo", "what this note became"): walks in SQL over typed
rows. There is no graph store.

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
   why. If the unit settles on one, Cognition later writes the event.

A read across the Notebook, the Rollup and Memory, then a decision.

## 6. Views: every reader gets one slice, cut at read time, by one path

A View selects what its reader declares (tiers, kinds, entities, window, size
cap), as of now or as of T; applies **lineage for the reader's rooms** (the
turn's room, each agenda item's bound room, a portal viewer's listed rooms);
applies **withdrawal** (an unsupported or hidden entry is absent, in every
lineage mode); and renders each row with its age, basis and confidence,
through `ContextBlock` for a prompt. Readers: the tag and fire turns (they
speak), the Cognition session (it thinks), the portal (a person reads; the
operator reads the operator-only pages, tension and assess reports, through
an operator View that no turn or session can declare), later recommendations.

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
| the fire turn's blocks | fire turn | its own `build_context` in `nora/app/fire.py`, sharing `build_prefill` | the same `LineagePolicy` | culture through `read_culture`, Standing through `render_supported` (`nora.domain.withdrawal`), as the tag turn | a second assembler |
| `<loop_decisions>` | fire turn | `nora/app/fire.py` | none | — | skips lineage |
| the agenda's grounding | Cognition session | `nora/app/cognition_grounding.py`, with its own renderer | its own call into `nora/agent/lineage.py`; a row must pass for every room on the agenda | in every mode | a third assembler and renderer |
| read tools | both | each tool: `note_read`/`note_search` (`nora/agent/tools.py`), `nora/agent/standing_read.py`, `nora/infra/sql/record_query.py`, `rollup_read` | each applies it itself | each applies it itself | one call site per tool |
| `media_search`, `media_send` | tag turn | `nora/agent/tools.py` | none: not scoped to the room | — | a read of the Record that no room bounds |
| the clocked adaptors' prompts | note-candidates, the curator, note-merge, proactive, culture, the ingest Standing fold, the debrief, the relevance gate | each module's own builder (`nora/language/note_candidates.py` (deleted 2026-10-03, DEC-277), `nora/language/proactive.py` (deleted 2026-10-03, DEC-277), `nora/language/culture.py`, `nora/ingest/stages/standing_event.py` (deleted 2026-10-03, DEC-277), `nora/engagement/debrief_render.py`, `nora/ingest/relevance_gate.py`) | none: they read unit-wide and never speak | notes through the store's reads; culture through `read_culture` and Standing through `render_supported`; a fold's OWN prior is read raw on purpose (`tests/architecture/test_withdrawal_reaches_every_prompt.py`) | retired with the adaptors at step 4 |
| portal pages | a person | `nora/web/gatherings.py` (events they created or answered), `nora/web/documents.py` | none: participation-scoped | none: events are outside the cascade | no page for notes, memos, Character, Standing or lessons yet |

## 7. The Notebook's writers, and how the speaking turns hand off

> 2026-10-28 (operator, ruled; [30](30-cognition.md#0-the-loop-as-ruled-2026-10-28)): the writers below change. Step ① of the Cognition loop writes the memory graph's judgement relations; step ② writes Character (traits and guesses; facts are rendered by code), culture and Lessons; step ③ writes the Register and Standing. One writer per tier still holds; the Character and Lessons adaptors retire.

The operator's model ([55](55-self.md) §6), the conscious and the
subconscious (明意識 / 潛意識): **the subconscious thinks and writes the
Notebook; the conscious speaks from it.** The subconscious writes it with
**one writer per tier**, split by timescale ([00](00-intent.md) D9):
**Cognition**, fast and per group (per unit, on stimuli), writes the Register
and Standing; the **Character adaptor** and the **Lessons adaptor**, slow
reflection over days, write Character and Lessons, each alone; **Assess**
writes nothing in the Notebook, only reports. The conscious (the tag and fire
turns) never writes the Notebook: it reads Views and speaks, and to change the
Notebook it raises a stimulus.

| tier · kind | written by | how a speaking turn hands off | the turn reads it (pushed · pulled) | timescale |
|---|---|---|---|---|
| Register · note, event | Cognition | the messages are already Record and raise stimuli; a date, time or RSVP is a strong cue; the turn's `handoff` carries what it looked up | `<notes>`, `<events>` · `note_read`, `note_search`, `event_search` | minutes |
| Register · memo | Cognition | «記低» about the unit is a keep instruction, a strong cue; «記低» about the speaker stays `remember`, into Memory | `<memos>` · `memo_read` | minutes |
| Standing | Cognition | the turn's `handoff` stimulus, in place of the debrief | `standing` (room) · `standing_read` | minutes |
| Character | the Character adaptor | none; a person's `character_dispute` is a Record item that wakes it | `culture`, `self` · `character_read` | days |
| Lessons | the Lessons adaptor | none; the operator retires on the portal | `lessons` | days |

**The `handoff` stimulus** (a new stimulus kind beside `message`, `social`,
`play`, `rollup`, `time` and `self`). Raised by code when a tag turn ends,
after delivery or a silent end, bound to the turn's room. It carries the tag
message's id, the id of each send she delivered in the turn, and the ids of
the turn's tool calls (Record, §1a). It is a strong cue, so it wakes
Cognition after `cue_quiet_seconds` (120 s today, [20](20-observe.md) §3); the
unit's cooldown (300 s) can delay it, and a strong cue may cut the cooldown
once. A wake claims it like a `message` stimulus, and the agenda item it
becomes shows the tag message, her reply and what her tool calls returned. **A
turn that sent nothing and called no tool raises none**: the tag message's own
`message` stimulus already carries everything that turn produced. It is the
one exception to "an item whose author is Nora raises no stimulus"
([20](20-observe.md) §3): her send still raises none; the hand-off is raised
by code, by name.

**What is lost without her tool calls in the Record.** The hand-off gives
Cognition the tag message and her reply. What she looked up to answer (a
place's details, a fetched page, a search result) exists today only for
cacheable tools that returned `ok`. The debrief builds Standing's working set
(`Found, not yet given`) from exactly those results, and a «記低» can rest on
one. Without every call and result in the Record, Cognition would re-fetch
them, get a different answer, or lose them. So recording them is a
precondition of E2 (§9).

The turn never says a thing is already written; it may say it will be.

**Reminders are machinery, and stay on the tag turn.** `remind` and
`cancel_reminder` change a reminder row, not the Notebook, so the tag turn
keeps both after E2 (rule 7). A person's cancellation in words is also Record
evidence Cognition reads: Cognition may cancel a reminder a person set when it
cancels that reminder's event on `told` evidence ([30](30-cognition.md) §2).

## 8. The flow

```
AUTHORED (humans): doctrine, config, policy, identity ──configures──▶ every layer below

RECORD (code, as it happens) ──▶ ROLLUP (Rollup builder, code)
  ▲   │                             │
  │   └──── stimuli ◀───────────────┘ ◀── `handoff` at the end of a tag turn; «記低» is a strong cue
  │            │
  │            ▼  wake evaluator (code): about two minutes on a strong cue
  │      THE SUBCONSCIOUS (the Dreamer's adaptors; one writer per tier)
  │        Cognition ──▶ Register, Standing ──┐
  │        Character ──▶ Character            ├─▶ NOTEBOOK (entries) ──cite──▶ Record, Rollup
  │        Lessons   ──▶ Lessons              ┘        │ links between entries
  │        Cognition ── remind() ──┐                   ▼
  │                                │        VIEWS (read time, one assembly path:
  │                                │         slice + lineage + withdrawal + age)
  │                                │          ├─▶ agenda grounding ─▶ Cognition session (thinks)
  │                                ▼          ├─▶ tag-turn blocks ──▶ tag turn  ─┐ THE CONSCIOUS
  │                         due reminder ────▶├─▶ fire-turn blocks ─▶ fire turn ─┤ (reads Views,
  │                                           ├─▶ portal pages ────▶ a person    │  speaks, hands off)
  │                                           └─▶ operator pages ──▶ the operator│
  └──── what she says and does (sends, tool calls, artifacts) is observed ◀───────┘

MEMORY (Honcho, beside the flow): fed from the Record; `remember`; read into Views, never into the Notebook
```

## 9. Migration

**Built today** (read from `nora/infra/sql/schema.py` on 2026-10-01):

| tier · kind | tables | keyed by | chain | lineage columns | citation edges | in the cascade |
|---|---|---|---|---|---|---|
| Register · note | `note`, `note_revision` | unit | `note_revision` | yes | message ids parsed from the body | yes |
| Register · event | `events`, `event_revision`, `event_rsvps` | origin room, plus `unit_id` | `event_revision` (DEC-237) | per field, but **no writer sets them** (`nora/infra/sql/gatherings.py`) | **none** | **no** |
| Standing · room | `standing`, `standing_revision` | room | `standing_revision` | yes | one message id, a stand-in | yes |
| Character · room (culture) | `culture`, `culture_revision` | `culture.key`, the room's `primary_id` from `policy.toml` | `culture_revision` | yes | one message id, a stand-in | yes |
| Register · memo (DEC-306, 2026-10-09) | `memo`, `memo_revision` | unit, plus the origin room | `memo_revision` (append-only) | no | the ids its body cites, in `citations` | no |
| Character · person, unit, Nora; Standing · unit, person; Lessons · lesson | not built here (Lessons: DEC-293) | | | | | |

Every table above keeps `row_history` (DEC-232), except the memo's two, which are
append-only and need none (`nora.infra.sql.history.APPEND_ONLY`). Built writers other than the
subconscious: the tag turn's `note_create`, `note_update` and event verbs,
the debrief through `standing_revise` (DEC-239), and the operator through
`scripts/standing_edit.py`. The `reasoning` column holds [10](10-tiers.md)
§4's six modes (a database CHECK; `standing_revise` takes `deduced`,
`abduced` or `said`); the entry's basis is computed from it (Q2).

**Gaps in the built code, each a migration item:**

- events are outside the `citation` table and the cascade: a withdrawn
  message keeps supporting an event;
- event lineage has no writer: with `[lineage] mode = "on"` every event would
  render only in its origin room;
- the `citation` table cites messages only, from notes, Standing and culture
  (its CHECKs, `nora/infra/sql/schema.py`): Rollup keys and events are not
  edges, and an edge has no `part` for per-field lineage;
- Standing and culture each cite one stand-in message id, not what their
  bodies rest on;
- her tool calls are logged only when cacheable and `ok`; her Instagram
  comment replies are not Record (§1a);
> **SUPERSEDED 2026-10-03 (guard removal; AGENTS.md «A guard needs a consequence»).** `--clear-unsupported` is deleted; clear a stuck mark with SQL.

- a head the fold re-derives keeps its `unsupported_at` (neither upsert clears
  it), so it stays hidden until `unforget --clear-unsupported`
  ([context-assembly/04](../context-assembly/04-every-occasion.md) §7);
- `<loop_decisions>` and `media_search`/`media_send` skip lineage (§6);
- Views depart from one path in the other ways §6 lists.

**The order.** Additive: no table is dropped ([70](70-migration.md) §5), and a
kind moves onto the entry only under render parity (its blocks and tool
results unchanged byte for byte), measured after the fire-turn fix.

| order | what | when |
|---|---|---|
| E1a | **the citation table widened**: events and every Notebook kind as citing stores, Rollup keys as cited, a `part` column (field or section) for per-field lineage; events brought into the cascade; event lineage written from its field citations | **gates step 4** |
| E1b | **Register · memo**, written only by Cognition. **As built (DEC-306, 2026-10-09):** memo-specific tables in the lesson's shape, not a generic entry store, and no `about` or `links`; the verbs `memo_write`, `memo_revise`, `memo_withdraw`, the read `memo_read` and the `<memos>` index, in one integrated change with no shadow week (operator) | landed 2026-10-09 |
| E1c | **her tool calls and results in the Record**: every call, every status, keyed to the turn; her Instagram comment replies too | **precondition of E2** |
| E2 | **one writer per tier**: the tag turn's `note_create`, `note_update` and event verbs and the debrief's Standing write move behind Cognition, replaced by the `handoff` stimulus (§7); `remind` and `cancel_reminder` stay on the tag turn | at the cut-over, step 4, or right after it |
| E3 | Character · person, unit, Nora, Lessons, and Standing · unit, person are born on the entry | step 8 |
| E4 | Character · room (culture, re-keyed from `primary_id` to its unit and room), Standing · room, events and notes converge onto the entry as each is next touched; the note body's `## From memory` section retires then, rendered by the View from Memory instead | per kind (culture: Q1) |
| E5 | Views converge on one builder, reader by reader, each with a parity test | alongside E1a to E4 |

**What one writer costs** (E2). A note, event or memo a person asks for
appears a couple of minutes later instead of in the turn. The tag tools' text
and the doctrine that tells her to write (`nora/doctrine/rooms/conduct.md`,
the «記低» paragraph) change, each measured with `tool_selection` and the
eval before and after (ADR 0015); the speaker's own «記低» through `remember`
does not change. The debrief's reply-slot fold is retired with the ingest
Standing fold. The curator's discharge into Memory (off by default) dies with
the curator at step 4: the Notebook never writes Memory.

**What step 4 needs.** E1a; the `handoff` stimulus and E1c before the tag-path
writes are withdrawn; the grounding and the tag turn's blocks agreeing on
withdrawal and lineage for the same rows.

**What must not be disrupted until its kind converges.** The live Standing
path (the debrief and the ingest fold through `standing_revise` into
`standing` and `standing_revision`, and the `standing` blocks); live notes
(the tag verbs, the clocked note passes until step 4, `<notes>`); the event
verbs (DEC-237); history capture (DEC-232); the cascade (DEC-238);
`[lineage] mode` (DEC-236).

## 10. Questions for the operator

Open: the operator has not ruled. Each has two recommendations, from this
node's author and from the independent review (Fable, 2026-10-01):

| # | question | author's recommendation | review's recommendation |
|---|---|---|---|
| Q1 | When does Character · room (culture) move onto the entry? Step 4 already rewrites culture into sections and hands it to the Character adaptor. | At step 4, in the same change, so culture is migrated once. | At step 4 only if E1a and E1b have landed and memo has held a shadow week on the entry; otherwise at step 8. |
| Q2 | The entry shows `observed` or `inferred`; the built column and `standing_revise` hold six finer modes (told, computed, deduced, induced, abduced, said). | Keep the six as the detail under the two. | Keep the six as the stored `reasoning`; the basis is a render-time function of it (this node's §3 already writes it so). |
| Q3 | The portal's events page shows every event a person created or answered, across all their groups, with no per-room visibility and no withdrawal. | Serve it through the one Views path, showing only what the viewer's rooms may see. | Through Views, the reader's room set being the viewer's listed rooms, a field shown if its lineage passes in any of them, withdrawal always applied, and "created or answered" as a filter on top; after E1a and the event-lineage writer. |

## Status / Checkpoint

**2026-10-01 — proposed, design only.** The concept was agreed with the
operator and corrected three times the same day (DEC-249): to scope it to
what Nora writes; to fix the topology and the vocabulary; to name the
Notebook's writers one per tier and keep "layer" for the data layers. It was
then revised on an independent review (Fable, 2026-10-01): her tool calls and
artifacts in the Record, the `handoff` stimulus, reminders as machinery,
lineage from `about` for unit-wide kinds, and E1 split into E1a, E1b and
E1c. Nothing is built by this node.
