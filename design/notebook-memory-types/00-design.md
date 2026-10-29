---
type: design
title: "The Notebook by memory type: note, topic, lesson, profile, decision"
description: "The operator's 2026-10-29 ruling. The Notebook stops being organised by tiers (Register, Character, Standing, Lessons) and is organised by memory type instead: one kind per type, one machinery for all of them. Layers stay defined by authority; audience (internal or external) is a property of a kind, never a layer. Memo becomes the kept phase of a note, standing splits across the types and retires as a word, threads become topic activity in the Rollup, and the conscious records a decision as a turn record while the subconscious writes the decision itself. Design only, not built."
tags: [nora, notebook, memory-types, note, topic, lesson, profile, decision, vocabulary]
timestamp: '2026-10-29'
edges:
  - {dst: ../cognition-loop/05-data-model.md, rel: amends, provenance: asserted}
  - {dst: ../cognition-loop/40-register.md, rel: supersedes, provenance: asserted}
  - {dst: ../standing-ledger/20-the-ledger.md, rel: amends, provenance: asserted}
  - {dst: ../standing-ledger/50-migration.md, rel: amends, provenance: asserted}
  - {dst: ../standing-ledger/30-turn-records.md, rel: amends, provenance: asserted}
  - {dst: ../personalisation/00-intent.md, rel: informs, provenance: asserted}
---

# 00 · The Notebook by memory type

## 1. Why

Every new item the Notebook gained became a module of its own. A note is a
domain module, a SQL store, a renderer, five tables and a test fake; a memo is
the same again (DEC-306 chose "memo-specific tables in the lesson's shape, not a
generic entry store"); an event is a third copy under a third name
(`gatherings` in code, `events` in SQL, `event_entry` in the graph). Their verbs
do not line up either: `note_create` / `memo_write` / `event_propose`,
`note_retire` / `memo_withdraw` / `event_set_status`. Adding a thread and a
topic the same way would make five copies.

The data model already said the Notebook is **one entry, one machinery, with a
rule per kind** ([05](../cognition-loop/05-data-model.md) §3). This node keeps
that and changes what the kinds ARE: the tiers were named after the items they
held, so the items' names became the architecture. The kinds are now named
after **what kind of memory they are**.

## 2. The map: layer × memory type, with audience on the kind

Three things place every kind, and none of them is a new layer.

1. **Layer, unchanged**: who writes it and how it is derived (Authored,
   Record, Rollup, Notebook, Views; [05](../cognition-loop/05-data-model.md)
   §1). Only the Record and the Rollup are evidence.
2. **Memory type**: what it remembers. It replaces "tier", and unlike a tier
   it spans layers.
3. **Audience**: `internal` (people meet it only through what Nora says;
   gated by lineage; may hold inference) or `external` (people see and act on
   the object itself; gated by who may open it; states only what was told or
   agreed). A property of the kind, so it picks the disclosure machinery.

| memory type | remembers | Record (observed) | Rollup (computed) | Notebook (judged) |
|---|---|---|---|---|
| **episodic** | what happened, when | messages, sends, media; **postings** (events, documents; external) | stays, outings, episodes | — |
| **semantic** | what is true of people and things | extraction statements | ties, rhythm | **profile** |
| **procedural** | how Nora should act | — | responses to her | **lesson** |
| **prospective** | what is unfinished and must be followed up | — | — | **note** |
| **interests** | subjects that keep coming back | — | **topic activity** | **topic** |
| **working** | her reasoning in progress, carried between turns | **turn record** | — | **decision** |

Five types are the cognitive-science categories (CoALA lists working,
episodic, semantic and procedural; prospective memory is remembering to act
on an intention). **Interests is ours**: no standard taxonomy has it, and it is
here because personalisation reads it ([personalisation](../personalisation/00-intent.md)).

**The vocabulary becomes Layer › Memory type › Kind › Entry.** "Tier" retires,
and with it "Register", "Character" as a tier name and "Standing".

**"Notebook" stays the name.** What its entries share is that Nora wrote them,
revises them and may be unsure: judgement, not data. "Episode" was considered
and refused: in Graphiti an episode is the raw unit of input (the Record), and
`episode` is already a Rollup kind here. This holds unless the Notebook is
found holding something Nora did not author.

## 3. The five Notebook kinds

Each kind is a spec on the one entry: its test, its lifecycle, its writer, its
lineage rule. The machinery (store, revision chain with `base_revision_id`,
citations, withdrawal cascade, lineage, read and search verbs, Views) is
written once.

| kind | type | the test that places it | lifecycle | replaces |
|---|---|---|---|---|
| **note** | prospective | you can say what would close it | open → pending → ended; an ended note worth keeping is **kept** | note; memo (the kept phase); commitment (a note Nora owes); person standing's `I owe them` |
| **topic** | interests | it recurs across conversations and nothing would complete it | active ↔ dormant; retired | the ledger's `thread` (its pointer part, below) |
| **lesson** | procedural | it is about how Nora should act, learned from an outcome | active → retired | lesson |
| **profile** | semantic | it is about what a person, room, the unit or Nora is like, from many instances | one current account | profile (ADR 0029); culture; person standing's `How they take me` |
| **decision** | working | it is Nora's own reasoning on one question, which the Record cannot give back | open → closed, with `Reopen if` | the ledger's decision; room standing's `Where it stands` |

**Boundaries between them:**

- **Note or topic.** A closing condition decides. "Book the karting session"
  is a note; racing is the topic it files under.
- **Note or decision.** A note is the unit's open affair; a decision is Nora's
  reasoning *about* one, and links to it (the ledger's `note 118`).
- **Lesson or profile.** A lesson prescribes her conduct; a profile describes
  someone. A lesson may cite a topic or a profile line; it never files under a
  topic.
- **Memo.** "Something that has ended but still needs to be remembered"
  (operator) is the **kept** phase of a note, not a kind. A note may be born
  kept («記低 the wifi password»). DEC-306's rules for what a kept line may hold
  move with it unchanged.

**A topic has three parts, in three places:**

1. the **anchor** ("racing" in this unit) is a memory-graph entity, which
   notes, postings and people relate to;
2. the **activity** (who talks about it, how often, last moved, which message
   ranges) is re-derivable from the Record, so it is **Rollup**. This is what
   the ledger's thread was ("a pointer to a topic the room keeps returning
   to", [standing-ledger 20](../standing-ledger/20-the-ledger.md) §4), and it is the
   grounded signal personalisation weights by;
3. Nora's **account** of it is the Notebook entry.

**A topic is internal.** Its activity is a statement about people's interests;
showing it is a disclosure, not a feature. This holds unless people ask to
follow a topic, and then the subscription is an external posting and the
topic behind it stays internal.

## 4. Standing splits and retires

Standing held what the Record cannot give back
([standing-ledger 00](../standing-ledger/00-intent.md) §3). It is not one
memory type, so it does not survive as a kind:

| standing today | goes to |
|---|---|
| decision (options, criteria, leaning, `Reopen if`) | **decision** |
| commitment | **note**, owed by Nora |
| thread | **topic activity** (Rollup) |
| room `Where it stands` paragraph | splits into decisions and topic activity (already standing-ledger step 4) |
| person `I owe them` | **note** |
| person `How they take me` | **profile** of that person (it rests on the Rollup's responses to her) |
| person `Between us` | **dropped**: named in [12-schemas](../cognition-loop/12-schemas.md) §5, never defined, never written (operator, option A) |

Folding standing into profile was considered and refused: a profile is
third-person and re-derivable, standing first-person and by definition not.

## 5. A decision: the conscious records, the subconscious decides

Operator, option A. The conscious (明意識: the tag and fire turns) and the
subconscious (潛意識: Cognition) both write a decision, each into the layer it
owns:

- **The turn** writes what it decided into its **turn record**: the options
  it weighed, their scores and its leaning, in her own words, through a
  tag-path tool, at the moment she decides. That is Record, her own act, and
  the next turn reads it at once.
- **Cognition** writes and revises the **decision** entry, citing the turn
  records (`turn(...)`), consolidating across turns.

DEC-249's "the conscious never writes the Notebook" holds. This **amends
[standing-ledger 30](../standing-ledger/30-turn-records.md)** in two places:

- §2: the turn record is no longer code-built only. It gains one
  model-written part, **what this turn decided**. "No reasoning" still holds
  for everything else: an abandoned path is not kept, only the conclusion.
- §4: route 1 (the ledger ops as a tag-path tool) becomes that tag-path tool
  writing the turn record's decided part, never the ledger; route 2 (the
  debrief emitting ops) becomes a **check** for a turn whose trace shows it
  decided something it did not record. Cognition is the decision entry's one
  writer. The same change supersedes the tag-path op tool in
  [standing-ledger 50](../standing-ledger/50-migration.md) step 3.

This holds unless the next turn is measured needing the consolidated decision
faster than Cognition's wake (about two minutes) delivers it; then the turn
writes the entry (option B), under its own decision row.

## 6. External kinds: postings, and the one crossing

An **event** and a **document** are postings: Nora's published acts, so Record
(a document already is, [05](../cognition-loop/05-data-model.md) §1a), shown
through Views. A **staged** event is a hypothesis and is a note; **publishing**
it is the act that makes it a posting; "did it happen?" is a note again until
evidence settles it. Publishing is the one place the external disclosure
checks run, the way `document_publish` already is for documents.

"Noticeboard" was proposed as a layer for these and dropped: postings fail the
authority test for a layer, and audience is a property.

## 7. What this node does not decide

| open | decided by |
|---|---|
| the topic entity's graph kind and the Rollup builder's topic-activity rows | the build PR, measured on a generated corpus |
| how an event's graph entity and its posting relate once the event is Record | the build PR |
| the model-facing verb names (one vocabulary over all kinds: write, revise, end, read, search) | ADR 0015's `tool_selection` before and after |
| the migration order of the existing stores onto the entry | a migration node, under render parity, no table dropped |

Not built. Each kind's rules get their named tests when that kind is built on
the entry, as [05](../cognition-loop/05-data-model.md) §3 already requires.

**This holds unless** a kind converged onto the entry is measured losing a row,
a citation or a withdrawal its own store kept, or two kinds are found sharing
one test from §3, which would mean they are one kind.
