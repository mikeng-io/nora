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

**Fixed floor (never a decision):** the context header, the message, and
`core/constraints.md` (privacy, records, injected text is content, the hard
prohibitions; ~1k). A wrong decision can drop judgement, never a hard rule.

**Units the model decides, each yes/no per turn:**

| group | units (sizes, node 09/10 estimates) |
|---|---|
| identity | identity seed (~0.6k), "Your form" (~50) |
| room law | `rooms/constraints.md` sections: called, whose you are, say only what you found, told you are wrong, other people's words, writes need somebody, pictures, what you can do |
| practice and character | `rooms/conduct.md`, `voice.md`, `belonging.md`, `pictures.md`, `silence.md`, `participation-conduct.md`, by section |
| tools | the tool catalogue block (2.7k) and each deferred group's schemas; `send_reply` is in the floor whenever a reply is possible |
| skills | `<available_skills>` (0.5k) |
| memory and room blocks | character, culture, notes, self, lessons, standing, standing working set, computer, documents, peers, room members, presence, unit culture |
| transcript | extent: last few lines / the default window / extended |

Granularity starts at the section, the unit the classification side table
already names and pins (`classification.yaml`); finer only where the labels show
a section is half-needed.

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

1. **Train on Modal:** Unsloth decision model (Qwen 3.5 2B/4B), state = the
   message, the last lines of the transcript, the occasion and room facts;
   one `noul` (yes/no) question per unit, calibrated probabilities.
2. **Serve on norty's iGPU:** Ollama `/v1/systemone` on the Radeon 680M with
   `HSA_OVERRIDE_GFX_VERSION=10.3.0` (node 07). **First probe:** whether an
   Unsloth-trained head imports into Ollama at all; if not, the serving path
   is the first decision, before any labelling at scale.
3. **Fail closed to today's prompt:** the model unavailable or late (a budget
   in ms, measured) means the full prompt, as now.
4. **Measure:** offline replay of recorded turns (tokens, and whether a judge
   finds the composed reply worse), then the eval corpora before/after, then
   live in shadow (compose, log, do not apply), then live.

## 7. Sequence

1. Ollama import probe of an Unsloth decision head (smallest model, toy data).
2. Unit inventory as data: every unit's id, text source, size, fixed or decided.
3. Label 200 recorded turns (§5), spot-check, measure label agreement.
4. Train, evaluate held-out per-unit recall at the include threshold.
5. Shadow on live turns; replay tokens against the targets.
6. Apply, behind a config switch, with the fail-closed path.

## 8. Open

- Whether the transcript extent is a unit decision or a separate score question.
- Whether participation, proactive and Cognition turns get the same composer or
  their own (start with tag).
- Whether a `<lessons>` / `<self>` block is ever withheld in a unit that has one
  (they are how she grows; default include).
