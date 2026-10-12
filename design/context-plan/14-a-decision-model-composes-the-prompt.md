---
type: design
title: "A decision model composes the prompt — targets, units, floor, labels"
description: "Operator 2026-10-12: a trained decision model composes the whole tag prompt per turn (doctrine, law units included; identity; tool surface; memory and room blocks; transcript extent), not a code-rule selector. Targets: a bare greeting's first call under 5k tokens, a typical tag turn with media around 10k. Fixed floor: the header, the message and core constraints. Labels come from recorded turns; training on Modal; inference on norty's iGPU."
tags: [nora, context-plan, prompt-composition, decision-model, unsloth, igpu, modal]
timestamp: '2026-10-12'
edges:
  - {dst: 11-composition-is-the-doctrine-cut.md, rel: amends, provenance: asserted}
  - {dst: 09-what-a-turn-carries.md, rel: constrained_by, provenance: asserted}
  - {dst: 07-current-measurements.md, rel: constrained_by, provenance: asserted}
---

# A decision model composes the prompt

## 1. The operator's intent, restated

Operator, 2026-10-12, correcting the reading that composition is code rules plus
an eventual doctrine cut: the original ask (the first message of this reopening)
was **our own decision model**, trained, composing the **whole** prompt — what the
system prompt carries, which doctrine, which blocks, which tools — not a context
router over four blocks. Code-decidable conditions alone (node 10's C units) buy
about 1.0–1.4k of a 20.8k first call; the rest needs a judgement about what the
turn is about, which is what the model is for.

Even law is composed: a greeting does not need the write rules, the picture rules
or the location rule. Identity is a unit with a strong default to include, not a
fixed block.

## 2. Targets (pass/fail, median first-call `input_tokens`, Langfuse)

| turn | today (node 09) | target |
|---|---:|---:|
| a bare greeting ("hi", 「早晨」) | ~20.8k | **< 5k** |
| a typical tag turn, media included | ~20.8k (p90 23.9k) | **~10k** |

A target is met on live traces over at least 7 days, not on a replay. The
second equation is `cache_read_input_tokens` (node 09: cost is not tokens);
report both.

Feasibility for a greeting, from node 09/10 sizes: header and message ~0.3k,
core constraints ~1k, identity seed ~0.6k, `send_reply` schema ~0.3k, the last
few transcript lines ~0.5k: **~2.7k**.

## 3. What is composed, and what is fixed

Operator, 2026-10-12 (second ruling): **everything injected is selectable**,
except one or two of the most critical doctrines. Not only doctrine: the
document index and bodies, the computer and subagent blocks, the lessons, the
person and peer cards, room culture, every block.

**Fixed floor (never a decision):** the context header, the message, and
`core/constraints.md` (injected text is data, configuration stays hers, reply
only where called, privacy and records; ~950 tokens). A wrong decision can drop
judgement, never a hard rule.

**Units the model decides:** the full inventory is data,
[evidence/composer-units.yaml](evidence/composer-units.yaml): 40 doctrine
sections, the runtime context, the tool catalogue, the resident tool schemas,
the skills index, every `_BLOCKS` row in `nora/agent/prefill.py`, and the
transcript extent (last few lines / default window / extended): 64 units.
Granularity starts at the section and the block, the units the code already
names; a block is split (document index vs document body, note index vs note
bodies) where the labels show half of it is needed.

## 3a. It must scale with features, not grow with them

Operator, 2026-10-12: Nora will keep gaining features, blocks, doctrine and
tools. If each is injected by default, a greeting's prompt grows with the
system (toward 50k) instead of with the turn. Composition is therefore the
contract, not an optimisation:

1. **Nothing reaches the prompt except through the composer.** The inventory is
   generated from code (every `_BLOCKS` row, every classified doctrine section,
   every tool and skill), not kept by hand; a gate test fails a block, section
   or tool that renders outside it. The floor is the only exception, and a test
   caps its size.
2. **New units are selectable without retraining.** A fixed multi-label head
   (one output per unit) must be retrained for every new block. The decision
   model is asked per (turn, unit card): "does this turn need <card>?", where a
   card is the unit's short statement of what it is for: block descriptions
   (`block_types.py`), tool descriptions, doctrine `description` and headings
   already exist. A feature ships with its card and is composable on day one;
   retraining improves it, it does not enable it.
3. **Latency must stay flat enough in the unit count.** Operator: the composer
   is a **decision model, not an embedding model**. With one question per unit,
   the turn is the shared prefix (cached by the server) and each question adds
   only the card and one output token; questions run in parallel. Where that
   does not fit the budget, units are grouped into a few multi-option
   questions. Both are measured on norty.
4. **Measured as an invariant:** the greeting target (< 5k) is re-checked on
   every release, not once.

## 4. The cache

A per-turn composition changes the prefix, and a changed prefix is re-read at
full price (September defer-tools: input −36%, cost up). Order is the answer:
the floor first and byte-stable, then units in a FIXED global order so two turns
with the same choices share a prefix, most-often-chosen first. Cost is reported
beside tokens; a composition that meets the token target and raises cost is
reported as that, not as a win.

## 5. Labels: the hard part

No gold set exists. For each recorded tag turn (Langfuse), what did it need?

1. **Signals from the trace**, mechanical: tools actually called; blocks whose
   content the reply or a tool argument used (string overlap); media codes in
   the window; occasion.
2. **A strong model labels** what the mechanical signals cannot (did this reply
   need the banter practice, the person-memory law, identity), given the turn,
   the reply and each unit's text, one unit per question.
3. **Operator spot-check** of a sample before training; disagreement rate is a
   reported number.
4. **Asymmetry:** a missed needed unit costs more than an extra one. Training
   weights and the serving threshold reflect it; uncertain means include.

## 6. Train, serve, measure

1. **Candidates, all decision models** (operator 2026-10-12: not an embedding
   model). Tev1 4B and 0.8B as they are (Together AI, Apache-2.0; a plain
   Qwen 3.5 GGUF tagged `decision-model`, served on norty by Ollama's
   `decision` capability, no separate head), and Qwen 3.5 0.8B / 2B / 4B
   trained on our labels (Unsloth on Modal). Each arm is measured on held-out
   labelled turns: missed needed units (the cost that matters), tokens saved,
   latency on norty against the budget (accuracy first; under 2 s, ideally
   under 1 s; node 07: Tev1 4B 4.1 s and 0.8B ~1.0 s per five-question request
   on the iGPU, before prefix caching and parallelism). A trained model is
   adopted only if it beats Tev1 held out.
2. **Train on Modal**, state = the message, the last lines of the transcript,
   the occasion and room facts; calibrated per-unit probabilities.
3. **Serve on norty:** Ollama `/v1/systemone` on the Radeon 680M with
   `HSA_OVERRIDE_GFX_VERSION=10.3.0` (node 07). A trained model is exported to
   GGUF in Tev1's shape (letter answers, no separate head) so the same server
   serves it.
4. **Fail closed to today's prompt:** the model unavailable or late (a budget
   in ms, measured) means the full prompt, as now.
5. **Measure:** offline replay of recorded turns (tokens, and whether a judge
   finds the composed reply worse), then the eval corpora before/after, then
   live in shadow (compose, log, do not apply), then live.

## 6a. How decision models are meant to be used (TypeSafe guidance, read 2026-10-12)

Read before building: the TypeSafe skill (github.com/typesafe-ai/skills,
`skills/typesafe-ai/SKILL.md`) and its docs pages on Noul, state, reranking and
hierarchical classification (docs.typesafe.ai). What it changes here:

1. **Language.** The state page: Jev's primary training language is English;
   CJK is accepted "but lower accuracy". Nora's rooms are mostly Cantonese, and
   node 07's Tev1 misses were Cantonese recall. This is the main case for
   training on our own data; every benchmark reports Cantonese turns separately.
2. **One Noul per unit, all in one request.** Questions over the same state are
   evaluated independently and in parallel (the Noul page; the reranking
   cookbook asks one Noul per query/candidate pair and sorts by it). That is the
   (turn, unit card) design of §3a. Question ids are not sent to the model, so
   each question carries the unit's full meaning (its card).
3. **Known facts go in state, not in questions.** Code-known facts (spoken,
   media present, occasion, which tools are loaded, the hour) are named JSON
   fields of the state; the model judges, code supplies what is exact.
4. **Instructions and criteria.** Instructions ask whether this turn needs the
   unit; criteria define yes (the turn would go worse without it) and no (only
   topically related). Phrased so high means include.
5. **Thresholds by error cost.** A missed needed unit costs more than an extra
   one, so the include threshold sits below 0.5, set on our labels; the middle
   band includes.
6. **Shortlist when the unit count outgrows the budget.** The reranking pattern
   is two-stage (a cheap shortlist, then judgment); hierarchical classification
   asks one Choice per level with a beam. Either is the fallback if one Noul per
   unit stops fitting the latency budget.

## 7. Sequence

1. Unit inventory as data (done: [composer-units.yaml](evidence/composer-units.yaml)).
2. Benchmark Tev1 as is on the first labels; that is the bar a trained model must beat.
3. Label 200 recorded turns (§5), spot-check, measure label agreement.
4. Train Qwen 3.5 decision models on the labels; compare held-out per-unit
   recall at the include threshold against Tev1, then latency on norty.
5. Shadow on live turns; replay tokens against the targets.
6. Apply, behind a config switch, with the fail-closed path.

## 8. Open

- Whether the transcript extent is a unit decision or a separate score question.
- Whether participation, proactive and Cognition turns get the same composer or
  their own (start with tag).
- Whether a `<lessons>` / `<self>` block is ever withheld in a unit that has one
  (they are how she grows; default include).
