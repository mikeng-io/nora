---
type: design
title: "Feedback: how a proposal's outcome teaches the engine, and when she proposes at all"
description: "Outcomes read from the Rollup's response episodes and the decision's close; what each outcome updates; a Thompson-sampling bandit over proposing or holding back, under a hard cap; and why the weights stay hand-set until replay justifies learning them."
tags: [nora, fit, feedback, bandit, thompson-sampling, response-episodes, cap]
timestamp: '2026-10-25'
edges:
  - {dst: 10-preference-model, rel: extends, provenance: asserted}
  - {dst: ../unified-memory/50-learning-from-acts.md, rel: extends, provenance: asserted}
---

# 40 · Feedback

## 1. The outcome of a proposal

A proposal is a send, so it already gets a **response episode**: replies,
reactions, edits and follows within the window, carrying the send's intent
([learning-from-acts](../unified-memory/50-learning-from-acts.md) §1). The
engine reads those episodes with intent `proposal`, and the close of the
decision the proposal wrote:

| outcome | read from | means |
|---|---|---|
| **picked** | the decision closes on one of its options (someone said "let's do the second one"; an event's venue is set to it) | the strongest signal |
| **engaged** | replies discussing the options, positive reactions | interest, not a choice |
| **redirected** | replies naming something else | the shortlist missed; the named thing is evidence |
| **rejected** | "not that", a negative reaction | evidence against the options' facets |
| **ignored** | no response inside the window | weak evidence against proposing, not against the options |

## 2. What each outcome updates

- **Preferences** ([10](10-preference-model.md) §2): picked and rejected add
  `response` evidence to the attendees on the chosen or rejected options'
  facets; redirected adds the named alternative as stated evidence for whoever
  named it.
- **The proposal policy** (§3): every outcome updates the acceptance posterior
  for the recipe and context in which she proposed.
- **Lessons**, unchanged: the Lessons adaptor already learns from response
  episodes in prose ("give two options, not five"). The engine does not write
  lessons; it reads none either, so the two cannot reinforce each other's
  mistakes.

## 3. Whether to propose: a bandit under a cap

Proposing is a choice with a cost: an unwanted suggestion is noise in a
friends' room. For each `(unit, recipe, context bucket)` (context: trigger
kind, lead-time band, time of day), keep a Beta posterior over "a proposal here
gets picked or engaged". When a trigger fires, Cognition **samples** from it
(Thompson sampling) and proposes only if the sample clears the recipe's
threshold; otherwise the stimulus is held and may fire again later.

- It explores where it knows little and backs off where proposals keep being
  ignored, without a hand-tuned schedule.
- **The cap is absolute.** The recipe's weekly cap per unit is checked before
  sampling; a capped trigger is held, never sent.
- **Asked is not sampled.** When someone asks, she answers.
- Each decision to propose or hold is logged with the sampled value, so the
  policy can be evaluated offline later without having to trust it.

## 4. Why the weights stay hand-set

The feature weights, evidence weights and half-lives could be learned from
outcomes. With a handful of proposals a month per unit, a learned weight would
mostly fit noise (the CMS measurement: the same 40 cases scored 0 to 2 from
run to run on one build). They stay recipe data, adjusted by a reviewed change,
until the replay set ([50](50-verification.md) §1) is large enough that a
learned set beats the hand-set one on held-out decisions. The bandit is the
exception because it learns one rate per bucket, which needs far less data.
