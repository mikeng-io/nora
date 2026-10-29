---
type: design
title: "Relations: how the memory types meet, and why the entity is the hub"
description: "The operator's 2026-10-29 ruling on how the five Notebook kinds and the episodic layers connect. Three mechanisms (cite, about, link) that never stand in for each other; the entity, not the profile, is the hub; a person's interest is a graph edge weighted by the Rollup, never a profile line (option A); links point from the more derived entry to what it rests on. Design only, not built."
tags: [nora, notebook, memory-types, profile, topic, relations, links, citations]
timestamp: '2026-10-29'
edges:
  - {dst: 00-design, rel: details, provenance: asserted}
  - {dst: ../cognition-loop/05-data-model.md, rel: amends, provenance: asserted}
  - {dst: ../personalisation/00-intent.md, rel: informs, provenance: asserted}
---

# 10 · Relations

## 1. Three mechanisms, never interchangeable

[05](../cognition-loop/05-data-model.md) §5 already defines them; this node
applies them to the kinds of [00](00-design.md).

| connection | from → to | means | evidence? |
|---|---|---|---|
| **cites** | an entry → Record or Rollup rows | what it rests on | **yes, the only evidence** |
| **about** | an entry → an entity (person, room, unit, Nora, place, topic) | who or what it concerns | no: an index |
| **link** | an entry → another entry, typed | draws on, or files under | **no, never** |

**A link is never a citation.** No Notebook entry is evidence for another, so
a profile never cites a note, a decision or a topic account; it cites the
Record and Rollup beneath them. This is what keeps a profile from summarising
summaries, the fold-on-fold loss that retired the standing paragraph
([standing-ledger 00](../standing-ledger/00-intent.md) §1).

## 2. The entity is the hub, not the profile

Every kind is `about` entities. A profile is one account on an entity: the
semantic one, "what is this like". Making the profile the hub, with every
type feeding it, would make it the accumulator that
[standing-ledger 10](../standing-ledger/10-what-standing-holds.md) ruled out,
and a second copy of what the memory graph already holds.

| type | its relation to the profile | mechanism |
|---|---|---|
| episodic (Record, Rollup) | the profile's evidence | profile **cites** it |
| interests (topic) | a graph edge person → topic, weighted by topic activity; the profile adds only what the edge cannot carry, citing it | shared **about**, plus the edge |
| prospective (note) | concerns the same people; how notes turned out may later ground a profile line, through the messages, never the note | shared **about** only |
| procedural (lesson) | may draw on a profile line | lesson **links** → profile |
| working (decision) | its criteria may draw on a profile line | decision **links** → profile |

## 3. An interest is an edge, not a profile line

Operator, option A. A person's interest in a topic is the memory-graph edge
person → topic, its weight computed from topic activity in the Rollup (who,
how often, last moved): counted, so personalisation reads a measured signal.
The profile may say *how* someone engages ("follows the qualifying sessions,
tunes out the merchandise talk"), citing what shows it, and never states the
interest alone. Two copies of "likes racing" would drift.

This holds unless personalisation is measured needing an interest that has no
topic activity behind it (one stated once and never discussed); then that
statement is an extraction in the Record, still not a profile line.

## 4. Which way links point

[05](../cognition-loop/05-data-model.md) §5 rules that a link points from the
more derived entry to what it rests on. For the new kinds:

| from | may link to |
|---|---|
| decision | note, topic, profile, lesson |
| note | topic (files under) |
| lesson | profile, topic |
| profile | other profiles (a room's draws on its members') |
| topic account | nothing; it is reached through its entity |

Nothing links into a decision or a note: they change fastest, and a slower
account pointing at one breaks when it ends. This holds unless a profile line
is found that can only be stated by pointing at a live note, which would mean
the line belongs in the note.
