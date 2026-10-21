---
type: design
title: "Schemas — every Register, Character, Lessons and Standing row is structured Markdown with fixed sections, parsed by code"
description: "The operator's cascade (2026-09-28): no row a model writes is free-form. One grammar for all (fixed English headings, cited lines, content in the room's language, refused never repaired), the split between typed columns and the Markdown body, and the schema of each kind: note, memo, event, the four Characters, a lesson, the three Standings. The Rollup is typed and rebuilt, never Markdown."
tags: [nora, schema, markdown, register, character, standing, culture, note-format]
timestamp: '2026-09-28'
edges:
  - {dst: 10-tiers, rel: extends, provenance: asserted}
  - {dst: ../dreamer-agent-loop/42-note-format.md, rel: generalises, provenance: asserted}
---

# 12 · Schemas

## 1. The rule

**Every row of Register, Character, Lessons and Standing has a schema. Free-form is
refused.** The operator's reason (2026-09-28): the free-form notes taught
nothing, because a model cannot tell which formatting carries meaning when
none of it does. A schema says what each part *is*, which is what a model
reading it back needs (AGENTS.md, "when the measurer is a MODEL, its input
has to explain itself").

**Markdown is the format.** It is what a model writes and reads most
reliably, it renders straight into a prompt block, and a parser for fixed
headings is small. The note body has worked this way since DEC-213
(`nora/domain/note_body.py`): this node applies its grammar to every kind a
model writes. Rollup rows are typed and rebuilt, never Markdown (§7).

## 2. One grammar

Taken from the note body (design/dreamer-agent-loop/42-note-format.md §4) and
held by one parser per kind, each called by the write verb before commit:

1. **Fixed English headings, in a fixed order.** A heading not in the schema,
   out of order, or a required one missing is refused.
2. **Content in the room's own language.** Only headings and fixed tokens are
   English; code parses keys, never content.
3. **Every claim cites.** A Register line ends in the Record ids and Rollup
   keys it rests on; a Character or Standing paragraph ends in its ids and
   keys; a lesson's `Why` ends in the outcomes it cites. Each must be one this
   session or turn was shown or fetched.
4. **Names are names the input showed; dates are absolute.**
5. **Some sections are code-written** (history, folds, ages) and a model never
   writes them. History is rendered from the revision chain; the head a
   block shows is derived from that chain, never a row updated in place
   ([10](10-tiers.md) §8).
6. **Refused, never repaired**, beyond whitespace. The refusal names the rule
   (a closed vocabulary, one counter label per rule) and is answered to the
   model as `refused` (ADR 0009's status) so it can rewrite.

**Typed columns vs body.** What code must compute on is a column, not prose:
ids, unit, entity, status, dates, and every column of [10](10-tiers.md) §3,
which is canonical for them. What a model must understand is the body.
Nothing is in both.

## 3. Register

**note** — unchanged: `Decided`, `Open`, `Owed`, `Proposed`, `Ruled out`,
plus code-written `From memory` and `Folded` (DEC-213). `Owed` holds what
members owe each other; what Nora owes is Standing's `I owe`, so no question
has two homes.

**memo**

| section | required | holds |
|---|---|---|
| `Kept` | yes | the settled thing, one line per claim, cited (who settled it is read from the cited message when a View renders it, never stored: DEC-249); a value the room settled is kept as said, a password included ([30](30-cognition.md) §3, D5) |
| `Conditions` | no | when it holds, exceptions ("only on weekdays") |
| `Superseded` | code | earlier versions, with when they changed |

**event** — columns: what, start, end, place, people, status, each with its
own `reasoning`, `confidence` and `lineage` ([10](10-tiers.md) §3). Body:

| section | required | holds |
|---|---|---|
| `Plan` | yes | the evidence for each column value, one line per field (what, when, where, who), cited; the value is the column |
| `Open` | no | what is not settled, each line naming the note that tracks it by id (a link, not a citation) |
| `Happened` | after | what they did, cited to social items and perceptions; who was there and when cite the outing episode ([25](25-rollup.md)) and are not restated |
| `Changes` | code | every revision: field, from, to, mode, why, when |

## 4. Character

> 2026-10-22 (operator addition; proposed): [47 · Character dimensions](../unified-memory/47-character-dimensions.md) supersedes this section for person, room, unit and Nora, mapping every proposed heading to a dimension, profile field or context. These Character headings were not built; the culture objective still requires prose with “No headings”. Phase 4 changes that contract with the Character rework. This amendment does not supersede §4a Lessons or change its implementation.

Paragraphs, not lists: an account says *what it is and why*, and the culture
store's measured lesson is that a list turns an account into a log of what
happened (`nora/dreamer/objectives/culture.md`, "an account, not a record").
The sections fix **what** is said; each paragraph stays prose and cites.

| entity | sections (all optional; at least one besides `Unsure`; `Unsure` always last) |
|---|---|
| **person** | `How they talk`, `What they care about`, `How they decide`, `What they reliably do`, `Who they are close to`, `Unsure` |
| **room** (culture) | `How they talk`, `How things get decided`, `Names and where they came from`, `What they do together`, `Unsure` |
| **unit** (culture) | the room's sections, plus `Across rooms` (how its rooms differ) |
| **Nora** | `How I come across`, `What they come to me for`, `How I sound here`, `Unsure` |

Character is descriptive: what the entity is like. What she has learned to
*do* is a lesson (§4a), which is why Character · Nora has no `What lands and
what does not`.

`Unsure` holds what the evidence suggests but does not yet carry; a line moves
out of it only with new evidence. Where-she-stands content that culture
carries today ("her own standing there", GLOSSARY **culture**) moves to
Standing: it answers Standing's question, not Character's
([10](10-tiers.md) §1).

## 4a. Lessons

One row per lesson, per unit:

| section | required | holds |
|---|---|---|
| the lesson | yes | one short prescriptive line ("when they ask for food, give two options, not five") |
| `Why` | yes | the outcomes that taught it, cited as `response` episodes ([25](25-rollup.md) §3): corrections, replies that landed, reactions, silence where a reply was expected; never her own words alone |
| `Applies when` | no | the situations it is for |
| `History` | code | every revision and retirement, with its id, so any one can be rolled back |

A lesson carries `last_supported_at` and `support_count`; one past its
horizon (60 days, to be sized from the first month) renders with its age and
wakes the Lessons adaptor to re-evidence or retire it
([20](20-observe.md) §3).

## 5. Standing

| subject | sections |
|---|---|
| **room** | `Where it stands` (her position, required), `I hold open`, `I owe`, `Found, not yet given` (today's working set; code ages lines out after 48 hours) |
| **unit** | `What I am following`, `What I am watching for`, `I owe` |
| **person** | `Between us`, `I owe them`, `How they take me` |

- **Undated only.** `I hold open` and `What I am following` hold undated
  positions. A dated plan is the note's (the withdrawal of
  design/standing-compact/40-open-plans.md, whose PR #565 was never merged;
  design/group-situation/10-standing-drops-plans.md is the measurement).
- **No `Unsure`.** Standing is what she holds now; a section past its age
  horizon is dropped on the next revision, not moved (§8).
- Room Standing is a paragraph plus a JSON `working_set` today
  (`nora/dreamer/objectives/standing-debrief.md`); the working set becomes
  the `Found, not yet given` section.

## 6. What changes for the existing stores

| store | today | becomes |
|---|---|---|
| note | schema (DEC-213) | unchanged |
| culture | continuous prose, "no headings" by its objective | the sectioned account above; prose inside each section |
| room Standing | one paragraph + JSON working set, the head rewritten by every fold (its chain, `standing_revision`, exists) | the sections above, each with its own support and age; the head is no longer a lossy fold ([50](50-character-and-standing.md) §2) |
| event | typed columns, no body, updated in place | columns with per-field modes + body, revision chain |
| Lessons | — | new ([§4a](#4a-lessons)) |

Rows written before a schema still read back and are never rewritten in place;
the next revision is written to the schema (the rule note bodies follow,
design 42 §9).

## 7. Rollup is typed, not Markdown

Rollup rows are typed columns only ([25](25-rollup.md)): code writes them and
code reads them, and a model sees them rendered by one renderer, the way
design 27's substrate is read today. No body, no prose: a Rollup row is
rebuilt into a new build, never revised, and superseded builds are kept, so
the Rollup as of T is readable ([10](10-tiers.md) §8).

## 8. Age on every section

Every Character and Standing section carries its own `as_of`,
`last_supported_at` and `support_count`, rendered beside the heading as an
age. A section older than its kind's horizon (Character 60 days, Standing 14
days, both to be sized from the first month) renders with its age; on the
next revision a Character section moves to `Unsure` unless re-evidenced, and
a Standing section is dropped.
