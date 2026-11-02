---
type: design
title: "The Notebook by memory type: note, topic, lesson, profile, decision"
description: "Notebook taxonomy: note, topic, lesson, profile and decision. Operator clarification 2026-10-31 keeps one Standing presentation and automatic faithful updater over completed Records; no self-report tool or new store. Cognition retains substantive Notebook judgment authority. Design target, not a claim of deployed behavior."
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

> **Authoritative clarification, operator 2026-10-31:**
> [20 · One Standing](20-standing.md) amends §§4–5: one existing Standing
> block across Engagement, Cognition and Proactive; the existing tools-less
> updater automatically organizes each completed loop's full retained Record
> and actual results. No producer self-report tool, separate shared-state block
> or new store. Cognition retains substantive Notebook judgment authority;
> Standing faithfully records expressed decisions and canonical execution facts.

## 1. Why

Every new item the Notebook gained became a module of its own. A note is a
domain module, a SQL store, a renderer, five tables and a test fake; a memo is
the same again (DEC-306 chose "memo-specific tables in the lesson's shape, not a
generic entry store"); an event is a third copy under a third name
(`gatherings` in code, `events` in SQL, `event_entry` in the graph). Their verbs
do not line up either: `note_create` / `memo_create` / `event_propose`,
`note_set_status` / `memo_set_status` / `event_set_status`. Adding a thread and a
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
and with it "Register", "Character" and "Standing" as tier names.
Standing remains the common derived presentation and block ([20](20-standing.md),
operator clarification 2026-10-31), rather than a parallel memory kind.

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
   grounded signal personalisation weights by. Amended 2026-10-29
   ([unified-memory/27](../unified-memory/27-the-memory-is-a-graph.md) §6b): the
   activity is counted by code from the judged `interested_in` relation's
   evidence, not built in the Rollup, because which messages are about a topic
   is a judgement.
3. Nora's **account** of it is the Notebook entry.

**A topic is internal.** Its activity is a statement about people's interests;
showing it is a disclosure, not a feature. This holds unless people ask to
follow a topic, and then the subscription is an external posting and the
topic behind it stays internal.

How these kinds connect to each other and to the profile (cite, about,
link; the entity as the hub): [10](10-relations.md).

## 4. Standing is the presentation; memory kinds remain distinct

Standing held what the Record cannot give back
([standing-ledger 00](../standing-ledger/00-intent.md) §3). The taxonomy maps
its content as below. The 2026-10-31 clarification keeps one Standing updater
and presentation over the existing stores; this build does not require a
storage-kind migration before consolidating the three renderings:

| standing today | goes to |
|---|---|
| decision (options, criteria, leaning, `Reopen if`) | **decision** |
| commitment | **note**, owed by Nora |
| thread | **topic activity**, counted from judged graph-relation evidence (2026-10-29 amendment above) |
| room `Where it stands` paragraph | splits into decisions and topic activity (already standing-ledger step 4) |
| person `I owe them` | **note** |
| person `How they take me` | **profile** of that person (it rests on the Rollup's responses to her) |
| person `Between us` | **dropped**: named in [12-schemas](../cognition-loop/12-schemas.md) §5, never defined, never written (operator, option A) |

Folding standing into profile was considered and refused: a profile is
third-person and re-derivable, standing first-person and by definition not.

## 5. Loops decide; Standing organizes their completed Records

**Amended by the operator, 2026-10-31.** Engagement and Cognition make their
ordinary decisions and retain their full exchanges and actual execution results.
The existing Standing updater automatically reads that Record after completion
and organizes what was decided, done, remains open or was not done. No separate
`decision_conclude` or tag-path self-report tool is required. A tool-only slice
of the Record is insufficient to retain an ordinary expressed decision.

The updater is a faithful tools-less summarizer, not another thinker. It can
materialize an expressed decision into derived Standing, using validated scoped
writers and source requirements; it cannot invent a commitment, choose an option,
create a reminder or turn an attempted action into a success. Cognition remains
the substantive Notebook judgment writer. Record-derived Standing is attributed
to its source and does not silently become a new Notebook judgment.

This supersedes the previous tag-path conclusion tool and optional-delta-only
updater contract, including the proposed writer switch in
[standing-ledger 30](../standing-ledger/30-turn-records.md) and
[standing-ledger 50](../standing-ledger/50-migration.md). The target is the
common model, automatic completion hooks and single `standing` block in
[20](20-standing.md). Canonical pending and execution facts are projected
immediately; they do not wait for a summary or a synchronous Cognition session.

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
| the model-facing verb names (one vocabulary over all kinds: write, revise, end, read, search). **Decided 2026-10-29** ([unified-memory/29](../unified-memory/29-one-cognition-session.md) §4): `create`, `revise`, `set_status`, `read`, `search` | ADR 0015's `tool_selection` before and after, per rename |
| the migration order of the existing stores onto the entry | a migration node, under render parity, no table dropped |

Amendment, 2026-10-29: the shared entry store, append-only revision chain and
`base_revision_id` compare-and-swap are built with topic as the only kind. A topic
is internal, about exactly one topic entity, cites Record/Rollup keys and has no
links. Existing stores stay on their own machinery. Topic activity reads current
`interested_in` evidence directly, grouped by person with asserted times and
source keys. Step 2 can record topic-account would-writes in its existing shadow
receipts; it does not write the entry store. No live topic writer, withdrawal
cascade, lineage rendering, search, or migration of other kinds is built.

Each kind's rules get their named tests when that kind is built on
the entry, as [05](../cognition-loop/05-data-model.md) §3 already requires.

**This holds unless** a kind converged onto the entry is measured losing a row,
a citation or a withdrawal its own store kept, or two kinds are found sharing
one test from §3, which would mean they are one kind.
