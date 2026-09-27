---
type: design
title: "Schemas — every derived row is structured Markdown with fixed sections, parsed by code"
description: "The operator's cascade (2026-09-28): no derived tier is free-form. One grammar for all (fixed English headings, cited lines, content in the room's language, refused never repaired), the split between typed columns and the Markdown body, and the schema of each kind: note, memo, event, the four Characters, the three Standings."
tags: [nora, schema, markdown, register, character, standing, culture, note-format]
timestamp: '2026-09-28'
edges:
  - {dst: 10-tiers, rel: extends, provenance: asserted}
  - {dst: ../dreamer-agent-loop/42-note-format.md, rel: generalises, provenance: asserted}
---

# 12 · Schemas

## 1. The rule

**Every row of Register, Character and Standing has a schema. Free-form is
refused.** The operator's reason (2026-09-28): the free-form notes taught
nothing, because a model cannot tell which formatting carries meaning when
none of it does. A schema says what each part *is*, which is what a model
reading it back needs (AGENTS.md, "when the measurer is a MODEL, its input
has to explain itself").

**Markdown is the format.** It is what a model writes and reads most
reliably, it renders straight into a prompt block, and a parser for fixed
headings is small. The note body has worked this way since DEC-213
(`nora/domain/note_body.py`): this node applies its grammar to every kind.

## 2. One grammar

Taken from the note body (design/dreamer-agent-loop/42-note-format.md §4) and
held by one parser per kind, each called by the write verb before commit:

1. **Fixed English headings, in a fixed order.** A heading not in the schema,
   out of order, or a required one missing is refused.
2. **Content in the room's own language.** Only headings and fixed tokens are
   English; code parses keys, never content.
3. **Every claim cites.** A Register line ends in the Record ids it rests on.
   An account paragraph (Character, Standing) ends in its ids. Ids must be
   ones this session was shown or fetched.
4. **Names are names the input showed; dates are absolute.**
5. **Some sections are code-written** (history, folds, ages) and a model never
   writes them.
6. **Refused, never repaired**, beyond whitespace. The refusal names the rule
   (a closed vocabulary, one counter label per rule) and is answered to the
   model so it can rewrite.

**Typed columns vs body.** What code must compute on is a column, not prose:
ids, unit, entity, status, dates, `derivation` (`told` / `inferred`),
confidence, revision links. What a model must understand is the body. Nothing
is in both.

## 3. Register

**note** — unchanged: `Decided`, `Open`, `Owed`, `Proposed`, `Ruled out`,
plus code-written `From memory` and `Folded` (DEC-213).

**memo**

| section | required | holds |
|---|---|---|
| `Kept` | yes | the settled thing, one line per fact, attributed and cited |
| `Conditions` | no | when it holds, exceptions ("only on weekdays") |
| `Superseded` | code | earlier versions, with when they changed |

**event** — columns: what, start, end, place, people, status, derivation per
field. Body:

| section | required | holds |
|---|---|---|
| `Plan` | yes | what, when, where, who — one line each, `told` or `inferred`, cited |
| `Open` | no | what is not settled, each line linking the note that tracks it |
| `Happened` | after | time range, place, who was there, what they did — cited to presence, social, perception |
| `Changes` | code | every revision: field, from, to, why, when |

## 4. Character

Paragraphs, not lists: an account says *what it is and why*, and the culture
tier's measured lesson is that a list turns an account into a record
(`nora/dreamer/objectives/culture.md`, "an account, not a record"). The
sections fix **what** is said; each paragraph stays prose and cites.

| entity | sections (all optional but one; `Unsure` always last) |
|---|---|
| **person** | `How they talk`, `What they care about`, `How they decide`, `What they reliably do`, `Who they are close to`, `Unsure` |
| **room** (culture) | `How they talk`, `How things get decided`, `Names and where they came from`, `What they do together`, `Unsure` |
| **unit** (culture) | the room's sections, plus `Across rooms` (how its rooms differ) |
| **Nora** | `How I come across`, `What they come to me for`, `What lands and what does not`, `How I sound here`, `Unsure` |

`Unsure` holds what the evidence suggests but does not yet carry; a line moves
out of it only with new evidence. Where-she-stands content that culture
carries today ("where you stand with them") moves to Standing: it answers
Standing's question, not Character's ([10](10-tiers.md) §1).

## 5. Standing

| subject | sections |
|---|---|
| **room** | `Where it stands` (her position, required), `I hold open`, `I owe`, `Found, not yet given` (today's working set; code ages lines out after 48 hours) |
| **unit** | `What I am following`, `What I think is going on`, `I owe` |
| **person** | `Between us`, `I owe them`, `How they take me` |

Room Standing is a paragraph plus a JSON `working_set` today
(`nora/dreamer/objectives/standing-debrief.md`); the working set becomes the
`Found, not yet given` section.

## 6. What changes for the existing tiers

| store | today | becomes |
|---|---|---|
| note | schema (DEC-213) | unchanged |
| culture | continuous prose, "no headings" by its objective | the sectioned account above; prose inside each section |
| room Standing | one paragraph + JSON working set | the sections above |
| event | typed columns, no body | columns + body |

Rows written before a schema still read back and are never rewritten in place;
the next revision is written to the schema (the rule note bodies follow,
design 42 §9).
