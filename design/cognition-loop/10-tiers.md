---
type: design
title: "Tiers — Record, Rollup, Register, Character, Standing; and the words that are not tiers"
description: "Five stores, each answering exactly one question, each with one write path, one key and one citation direction; every derived row carries its reasoning mode, its confidence, its lineage and its age. Lineage is a set of (kind, subject, origin room) per field, checked per room at assembly. Cognition is the loop, memory is Honcho, doctrine is authored, tension is the operator's. The terminology table and the words retired."
tags: [nora, tiers, terminology, record, rollup, register, character, standing, culture, lineage, reasoning-mode]
timestamp: '2026-09-28'
edges:
  - {dst: 20-observe, rel: sequences, provenance: asserted}
  - {dst: 25-rollup, rel: motivates, provenance: asserted}
  - {dst: 40-register, rel: motivates, provenance: asserted}
  - {dst: 50-character-and-standing, rel: motivates, provenance: asserted}
---

# 10 · Tiers

**This node is the canonical definition.** Every other node, the GLOSSARY and
every objective or skill uses these words with exactly these meanings; a new
word for any of these things is a defect.

## 1. The rule the tiers are cut by

**Each tier answers one question, and no question is answered by two tiers.**

| tier | the one question | holds | written by | reasoning |
|---|---|---|---|---|
| **Record** | *what was observed?* | messages (hers included) with their edits, deletes and reactions; her loop decisions and reminder outcomes; presence fixes and the collector's polls; social items; play samples; perceptions of media (what it shows, whose face); face labels and `character_dispute` items | **code**: ingestion ([20](20-observe.md)); the decision log and the fire path for her own acts; two tag-path recording verbs, `face_label` and `character_dispute`, each recording what one person said as an item authored by that person and citing their message | none: observed (a perception carries its model's confidence) |
| **Rollup** | *what does the Record add up to over an interval?* | **episodes** (bounded happenings: a stay, a trip, a co-location, a face co-appearance, an outing, a play session, a person's day) and **aggregates** (counts, patterns and clusters over a window: place, rhythm, tie, play profile) | **code only**: a pure function of the Record up to a logical watermark ([25](25-rollup.md)) | **computed** |
| **Register** | *what is the state of the unit's affairs?* | **note** (open, closes), **memo** (settled, kept), **event** (dated, attended) | cognition and the tag path, through write verbs | **told**, **deduced**, **abduced** |
| **Character** | *what is this entity like, and how does it behave?* | one account per entity: a **person**, a **room**, the **unit**, **Nora** | cognition only | **induced** |
| **Standing** | *where does Nora stand?* | her held position toward a **room**, the **unit**, a **person** | cognition and the tag-path debrief | **deduced**, **abduced**, **said** |

Outside the five, and not tiers:

- **Doctrine** — authored by the operator (`nora/doctrine/`). Never derived,
  never written by Nora, and it wins over anything derived about her.
- **Memory** — Honcho, and only Honcho: a third party's derived model of
  people. Read as a hint, never cited. `remember` still writes a person's own
  settled claim about themselves into it.
- **Tension** — operator-only rows ([55](55-self.md) §4). Written by
  `character_revise`, the one exception to "one write path per tier" (§2 rule
  3). Code guarantees she never reads them: no read tool returns them, no
  block renders them, and a test pins both.
- **Reminders** — the machinery of act: every act is a reminder row
  ([30](30-cognition.md) §2), never a tier.

And one word names a process, never a store:

- **Cognition** — the loop in [30](30-cognition.md) that reads the Record and
  the Rollup and writes Register, Character and Standing. There is no
  "cognition tier".

## 2. The boundaries, as rules code enforces

1. **Citations point down.** Order: Record < Rollup < Register < Character <
   Standing. A row cites only rows below it: Rollup cites the Record; Register
   cites the Record and the Rollup; Character cites those and the Register,
   and a room's or the unit's Character may also cite the Characters of its
   members or rooms; Standing cites any of the four. **Nothing cites upward**:
   a note may not rest on "that person tends to cancel", which is a Character
   line, not a claim about the plan.
2. **Everything derived rests on the Record.** A derived row with no chain of
   citations ending in Record ids is refused.
3. **One write path per tier.** Code writes the Record and the Rollup; the
   two recording verbs of §1 write Record items authored by a person. Only
   write verbs write Register, Character and Standing, and each verb belongs
   to exactly one tier. The one exception is `character_revise`, which may
   also write a tension row.
4. **Nora's own words are Record, and ground almost nothing.** Her rows
   cannot ground the Register or anyone's Character (the note brake,
   GLOSSARY **note**, kept and widened). They may ground her Standing only in
   the mode `said`, and her own Character only paired with how people
   responded. **A person's assent to her own guess** ("you ended up at C,
   right?" — "yeah") **does not upgrade it**: the row stays abduced.
5. **Counts and routines live in the Rollup.** A Character or Standing line
   about frequency, regularity or closeness ("usually", "often", "closest")
   cites a Rollup aggregate and never states a count or a time itself.
6. **Keys are fixed.** The unit is the key of everything derived, inside
   DEC-214's hierarchy (workspace > unit > room; one room per platform per
   unit), with one exception: the person-scoped Rollup rows (stay, trip,
   place, rhythm) are keyed by person and shared across that person's units,
   because a person moves as one stream; lineage decides which rooms see
   them. Record: by room, by person, by platform account. Rollup: (kind,
   person, interval) or (kind, unit, subject, interval), cited by (kind,
   subject, interval) and resolved by overlap ([25](25-rollup.md) §2).
   Register: by unit, with the origin room kept. Character: by (unit,
   entity). Standing: by (unit, subject). A person in two units has two
   Characters, each from that unit's evidence.
7. **Withdrawal cascades.** When a Record item is withdrawn (a message deleted
   or unsent, the PII scrub, a face label removed, a person leaving a unit,
   `nora person forget`), every derived row whose citation chain reaches it
   is marked `unsupported`, stops rendering, and goes on the next agenda for
   re-derivation. Code walks a `citation` edge table; it never parses bodies
   ([80](80-prior-art.md) §4).

## 3. What every derived row carries

Columns, computed or checked by code:

| column | what it is | on | set by |
|---|---|---|---|
| **reasoning** | how it was reached ([§4](#4-reasoning-modes)) | every derived row; **per field** on an event | code for `computed`; otherwise the write verb's argument, checked against the tier's allowed modes (§1) and capped by code (§4) |
| **confidence** | the writer's stated certainty: `low`, `medium`, `high` | Register, Character and Standing rows | the write verb |
| **lineage** | the set of `(kind, subject, origin room)` its citations reach down to; **per field** on an event, **per section** on a Character or Standing row | every derived row | code, from the citation chain; never the model |
| **exclude_persons** | people this row is never shown to (a surprise) | Register rows, and by union every row citing one; always empty on the Rollup | the write verb; code unions it down the citation chain |
| **as_of** | when its newest evidence was observed | every derived row | code |
| **valid_from / valid_to** | when what it says held in the world, not when it was written | Rollup episodes and Register rows only | code for Rollup; the write verb for Register |
| **last_supported_at / support_count** | the newest Record time its citations reach, and how many independent citations support it | Character and Standing sections | code |
| **base_revision_id** | the revision a write replaces; a stale one is refused `stale_base` with the current row | Register, Character and Standing revisions | the write verb |
| **build** | the code version that computed it | Rollup rows | code |

### Lineage decides who may see a row

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
| `memory:<unit>` | B belongs to that unit |

- **Per field.** A field (on an event) or section (on a Character or Standing
  row) whose lineage fails for a room is not shown there; the rest of the
  row is. A status or people field that places people is `presence`-lineaged.
- **Placing a person is presence.** Any row that places a person somewhere at
  a time carries `presence` for that person, whatever its source: a
  co-location, a face in a media item, a story's location tag.
- **The guard runs live.** Rendering re-runs DisclosureGuard; R-31's subset
  check is a rule about a *person*, which is why every lineage entry names
  its subject.
- **Unknown is deny.** An empty or unknown lineage renders only in its origin
  room, and nowhere if it has none. Existing rows get their lineage by a
  backfill before the filter ships ([70](70-migration.md) step 2).
- **Surprises.** A row with a non-empty `exclude_persons` never renders in a
  room holding an excluded person, never in a room with unnamed platform
  accounts, and is re-checked when a reminder about it fires.

## 4. Reasoning modes

`told`, `deduced` and `induced` are Honcho's `explicit`, `deductive` and
`inductive` levels under Nora's names; `abduced`, `computed` and `said` are
Nora's own (Honcho's code has no abductive level, [80](80-prior-art.md) §1):

| mode | meaning | allowed in | rule |
|---|---|---|---|
| **computed** | arithmetic over the Record; no inference | Rollup | same Record and watermark, same row |
| **told** | a person stated it in words, to Nora or to each other; members agreeing among themselves counts ("ok B then" tells B) | Register | cites the message |
| **deduced** | follows necessarily from rows already held | Register, Standing | "the event is at C from 20:00, and a co-location on fresh fixes puts P at C at 20:05, so P attended" |
| **induced** | a generalisation from many instances | Character | cites a Rollup aggregate; one instance is never induction |
| **abduced** | the best explanation of evidence nobody stated | Register, Standing | a value resting only on abduction goes out as a question ("you ended up at C, right?"); never upgraded by assent (§2 rule 4) |
| **said** | Nora herself said it; the row asserts only that she said it | Standing | cites her own Record row; grounds nothing but itself |

**Code caps the mode.** A value resting on presence fixes, or on a face in
media ([60](60-faces.md)), older than the freshness threshold at the time it
asserts is capped at `abduced` whatever the verb's argument. Attendance
deduced from a co-location therefore holds only on fresh fixes; otherwise it
is abduced.

## 5. Culture is Character

Culture answers the question person Character answers — *what is it like,
how does it behave* — about a room or the unit instead of a person. "Culture"
survives as the word for the Character of a collective; the existing
`culture` store becomes its room case. Confirmed by the operator 2026-09-28.

## 6. The words

| word | means exactly | never means |
|---|---|---|
| **Record** | the observed tier | a database row in general |
| **Rollup** | the computed tier | a model's summary; Standing's fold of folds (below) |
| **episode** | a Rollup row for one bounded happening, with a start, an end and participants | a raw ingested item (Graphiti's episode); an interaction exemplar (LangMem's) |
| **aggregate** | a Rollup row of counts, patterns or clusters over a window (a place cluster is one) | a Character line |
| **daybook** | the Rollup episode of one person's day | a diary Nora writes |
| **Register** | the unit's affairs: note, memo, event | the voice register of `nora/doctrine` (always "voice register" there) |
| **note** | an open thing the unit must not drop; it closes | a jotting, an idea |
| **memo** | a settled thing the unit wants kept; it does not close | a thing about the speaker alone (that is `remember`) |
| **event** | a dated thing people attend (code: `Gathering`) | a fire-turn occasion (code: `Event`, DEC-139), a wake signal, an episode |
| **Character** | what an entity is like and how it behaves | a person's attributes (Honcho), a persona (DEC-29) |
| **account** | the body of one Character row | a platform account (a login: always "platform account") |
| **culture** | the Character of a room or the unit | `<room>`, the operator's statement of what a room is FOR |
| **Standing** | Nora's held position toward a subject | status, queue position, `NoteStore.standing()` |
| **tension** | an operator-only row: evidence pulling against a doctrine line | Honcho's `contradiction`; a Character line |
| **reasoning** | the mode a derived row was reached by (§4) | the model's chain of thought |
| **confidence** | the writer's stated certainty (§3); on a perception, its model's score | a probability; the reasoning mode |
| **lineage** | the `(kind, subject, origin room)` set a row's citations reach down to (§3) | the citations themselves |
| **citation** | a Record id or a lower-tier key a line rests on | a hint read from memory |
| **fold** | a model call that rewrites a row from its last version plus new evidence (the Standing fold; a note's `Folded`) | a Rollup; a revision chain |
| **memory** | Honcho | any store of ours |
| **conclusion** | a derived row in Honcho | a Record item (Honcho calls its conclusions "observations"; Nora never does) |
| **signal** | what ingestion, the Rollup or the clock raises to wake cognition | an event |
| **agenda** | the items code computes from a unit's pending signals for one session | a plan the model writes; a menu of actions |
| **session** | one cognition run for one unit | a Honcho session (a conversation thread; always written "Honcho session") |
| **cognition** | the loop | a tier |
| **write verb** | a tool that writes one tier's row, validated by code before commit | an act |
| **act verb** | `remind`: the one tool whose row leaves through the fire path | a send |
| **tag path** | the turn a tag starts; the reflex | cognition |
| **fire path** | `FirePath.speak` (`nora/app/fire.py`), the turn a due row starts; one of the senders of R-1b | a background session |
| **unit** | the social unit a room belongs to (DEC-105); the key of everything derived | a Honcho named scope (how memory stores it); a "circle" |
| **room** | one conversation Nora is in, on one platform (`[[conversations]]` in `policy.toml`) | a unit; a voice channel (a surface of a room, DEC-191) |
| **workspace** | a group of units, the boundary of a person's model (DEC-214) | a Honcho workspace (always written so) |
| **block** | one labelled unit of context in a prompt (`_BLOCKS`, `nora/agent/prefill.py`) | a Letta memory block; "context block" |
| **perception** | a model's reading of a Record item (vision, face match), stored on that item with its confidence | an interpretation of what it means |
| **play** | the stream of who is in which game or voice channel, with whom | a game's own match data (that is a source feeding it) |

**"Rollup" collides twice.** `nora/analytics/rollup.py` is the presence part
of this tier and keeps its name. Standing's `[standing] rollup_max_folds` and
`nora_standing_rollup_folds_total` name a fold of Standing folds, not this
tier: both go when the ingest Standing fold is retired ([70](70-migration.md)
step 4), and until then their help text says "Standing fold, not the Rollup
tier".

Retired: **idea** (a proposal is a `## Proposed` line of a note, DEC-213);
**stance** (it is Standing); **knowledge** and **fact** (the deleted tier,
#115; say "claim" or "typed row"); **gathering** in prose (the code name
`Gathering` stays); **circle** for the people of a unit (say unit; R-47's
"circle", the presence roster, keeps its meaning); **observation** (Honcho's
word for a conclusion; say "Record item" or "report"); **storage tier**;
**mart** and **trace** (the operator's metaphors; "trace" is Langfuse's in
this repo); **inferred** (replaced by the three inference modes); **community**
(Graphiti's clustered group; rooms and units are configured); **reflection**
as a process name (it is cognition).

## 7. Where today's stores land

| today | lands in |
|---|---|
| transcript rows, presence fixes, media, `media_objects` comprehension | Record |
| reactions, deletes, unsends | Record (not ingested today; added at [70](70-migration.md) step 1) |
| `decision_log` | Record |
| presence stays, trips, places, rhythm, occupancy, corridors (`nora/analytics/`, design 27) | **Rollup** (derived and rebuilt; this bundle's first drafts filed them as Record) |
| `note`, `note_revision` | Register · note |
| `Gathering` (per room today) | Register · event, **unit-wide** |
| — | Register · memo (new) |
| `culture`, `culture_revision` | Character · room |
| — | Character · person, unit, Nora (new) |
| `standing` (per room) and its working set | Standing · room |
| — | Standing · unit, person (new) |
| Honcho representation, self card, peer card | memory (a hint, never a citation, never Character evidence) |
| — | tension (new, operator-only) |
| reminders | the machinery of act, not a tier |
