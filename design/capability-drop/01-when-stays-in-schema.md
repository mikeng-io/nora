---
type: design
title: "Capability Drop — when stays in the schema"
description: "The redundancy evidence (85% content-word overlap), the small set of when-clause content that is genuinely incremental, and how each piece merges into its docstring."
tags: [nora, tools, schema, docstring, measurement]
timestamp: '2026-08-30'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# When stays in the schema

## 1. The measurement

All 47 native tool docstrings (the schema descriptions) and all 48
`NativeTool.when` clauses (the capability block lines) were extracted and
compared by content words (stopwords removed).

| metric | value |
|---|---|
| tools compared | 47 (excluding `skills`, which is plugin-described) |
| when-clause content words already in docstring | **85% average** |
| tools at 100% overlap | `search_memory`, `weather`, `version`, `play_music`, `skip_music`, `top_music`, `stop_music`, `music_status`, `group_members`, `generate_image`, `who_said`, `lookup_person` |
| tools at ≥87% | `calculate`, `transit`, `route`, `ask_memory`, `send_to`, `make_artifact`, `web_search`, `note_revise` |
| capability block total | 6,345 chars (~1.6k tokens), paid on every call |
| docstrings total | 50,013 chars (~12.5k tokens), already in the request as schemas |

The capability block's own design (registry.py:470-476) says *"Not a schema
dump … this block adds exactly the one thing a schema has no slot for: when to
reach for a tool in this situation."* The measurement shows that one thing is
largely already said — in the docstring, which is the schema description. The
second sentence of nearly every when-clause is a safety admonition
("never state a bus time you did not get from here") that the docstring repeats
word-for-word.

## 2. What genuinely survives

Three when-clauses carry content the docstring does not. Each merges into the
docstring before the block dies:

| tool | when-clause-only content | merge target |
|---|---|---|
| `recall_lookups` | **more than the doubted/guessed trigger** — the DEC-92 evidence framing "your record of what you ACTUALLY checked" is when-clause-only; the docstring says only "re-running … costs nothing", which is the exact COST framing DEC-92 replaced. The when-clause's full unique content: "your record of what you ACTUALLY checked … whenever an earlier answer of yours is doubted or you cannot tell whether you checked something or guessed it" | docstring — replace the "reusing … costs nothing; re-searching costs a live call" sentence with the evidence framing (record of what you actually checked; reach for it when an earlier answer is doubted or you cannot tell whether you checked something or guessed it), keeping "re-running" (the acceptance test pins all three: `actually`, `doubted`/`guessed`, `re-running`) |
| `create_event` | "**genuinely** NEW gathering, not an update to one already being planned" | docstring first paragraph — the emphasis already exists ("a genuinely NEW gathering"); verify it reads as strongly without the block |
| `send_reply` | burst examples ("send SEVERAL when the moment wants a burst — a reaction, then the hit, then the twist") | docstring already carries the burst paragraph; the when-clause's phrasing adds nothing |

The rest of the when-clauses are paraphrase or pure repetition and die with the
block.

## 3. Why docstring, not skill

"when to reach for a tool" is routing metadata — it must be present on every
turn, in the schema, for the model to choose a tool at all. It cannot live in a
skill: skill bodies are progressive disclosure (`<available_skills>` shows
name + when-to-load only; the body loads only when the model calls
`skills()`). A skill whose body explains when to use `transit` would require
the model to call `skills()` **before** it can decide whether to call
`transit` — a loop with no first step. Skills are for *combinations* of tools
(``navigation`` already does this, DEC-48); the schema is for single-tool
routing. This is exactly the boundary Miles drew: skill is skill, doctrine is
doctrine, tools are the schema.

## 4. Risk and reversal condition

The schema descriptions are longer than the old when-clauses on average (922
vs ~135 chars per tool). If a regression shows a model routing worse with the
block gone — measured the way DEC-39/DEC-58 measured (first-tool selection on
real questions) — the reversal is to restore a *generated* from-schema routing
aide, never a hand-written catalogue. **This holds unless** a measurement shows
that; there is no plan to hand-write anything.
