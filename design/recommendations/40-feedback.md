---
type: design
title: "Feedback: how a proposal's outcome teaches the engine, and when she proposes at all"
description: "Outcomes read from the Rollup's response episodes and the decision's close; what each outcome updates; learning from picks with Thompson sampling over proposing or holding back, under a hard cap; and why the weights stay hand-set until replay justifies learning them."
tags: [nora, recommendations, feedback, learning-from-picks, thompson-sampling, response-episodes, cap]
timestamp: '2026-10-25'
edges:
  - {dst: 10-likes, rel: extends, provenance: asserted}
  - {dst: ../unified-memory/50-learning-from-acts.md, rel: extends, provenance: asserted}
---

# 40 · Feedback

## 1. The outcome of a proposal

A **recommendation send** is a send with a separate identity:
`kind="recommendation"` and `decision_id` linking it to the Standing decision
whose options it posts. These are proposed send metadata, added in step 4,
not a third communicative intent. The engine reads episodes by this link,
including tag replies as well as fire turns; a bare send id or arbitrary
`proposal` intent does not identify one.

**Intent stays `tell` or `ask`.** A request to choose is `ask`; information is
`tell`. Validation and the abduced-claim question rule already depend on it
(`nora/domain/cognition_remind.py:103`, `nora/domain/cognition_remind.py:110`).
`_send_intent` drops every other value to `None`
(`nora/infra/sql/rollups.py:743`). The response reducer expects an answer only
for `ask` or a named person (`nora/domain/rollup.py:935`): relabelling an
unnamed send `proposal` would turn silence into `unanswered`, not a negative.
Step 4 preserves those rules and links each delivered message to its decision.

Episodes carry responders (`nora/domain/rollup.py:951`), but the aggregate
contains no per-option sentiment. Resolve each judgement against the original
reply/reaction and option, retaining the responder id. Generic engagement is
not a pick, and a negative reaction without a clear object is not dislike of
all options. Read the decision's close alongside that evidence:

| outcome | read from | means |
|---|---|---|
| **picked** | the decision closes on one of its options (someone said "let's do the second one"; an event's venue is set to it) | the strongest signal |
| **engaged** | replies discussing the options, positive reactions | interest, not a choice |
| **redirected** | replies naming something else | the shortlist missed; the named thing is evidence |
| **rejected** | an explicit rejection of an identified option | the proposal was rejected; no personal dislike is inferred without its stated reason |
| **ignored** | a closed `silent` episode, where an answer was expected and platform availability was proved | evidence against proposing, never against likes |
| **no reading** | `open`, `unknown` (availability not proved), or `unanswered` (no answer expected) | nothing is updated |

## 2. What each outcome updates

- **Likes** ([10](10-likes.md) §2): only a person's own explicit liking or
  dislike of an identified option/facet supplies personal response evidence.
  A pick, attendance, assent to a compromise, redirect or plan rejection alone
  supplies **none**, even for the chooser. If ada says "choose this because I
  like puzzles", the liking is evidence for ada; bea's agreement says nothing
  about bea's taste. The existing profile writer interprets the original
  Record evidence; recommendations never updates attendees' posteriors or
  writes a second preference store. A collective choice stays collective.
- **Learning from picks** (§3): once a linked outcome is settled, `picked` or
  unambiguous `engaged` adds 1 to acceptance `α`; explicit `rejected` or
  `redirected`, or closed proved `silent`, adds 1 to `β`. `open`, `unknown`,
  `unanswered` and ambiguous engagement add nothing to either side. An
  unresolved decision cannot count as a negative. Each linked send contributes
  its resolved classification once; if a source is revised, recompute from
  the eligible outcome records rather than adding another update. Do not
  count the same choice again through both an episode and a decision close.
- **Lessons**, unchanged: the Lessons adaptor already learns from response
  episodes in prose ("give two options, not five"). The engine does not write
  lessons; it reads none either, so the two cannot reinforce each other's
  mistakes.

## 3. Learning from picks: whether to propose under a cap

Proposing is a choice with a cost: an unwanted suggestion is noise in a
friends' room. For each `(unit, destination room, recommendation type, context bucket)` (context: trigger
kind, lead-time band, time of day), keep a Beta posterior over "a proposal here
gets picked or engaged". When a trigger fires, Cognition **samples** from it
(Thompson sampling) and proposes only if the sample clears the recommendation type's
threshold; otherwise the stimulus is held and may fire again later. Compute
this posterior only from outcome evidence eligible for that destination; a
unit-wide aggregate containing restricted outcomes cannot be reused.

- It explores where it knows little and backs off where proposals keep being
  ignored, without a hand-tuned schedule.
- **The cap is absolute.** The recommendation type's weekly cap per unit is checked before
  sampling; a capped trigger is held, never sent.
- **Asked is not sampled.** When someone asks, she answers.
- Each decision to propose or hold is logged with the sampled value, so the
  policy can be evaluated offline later without having to trust it.

## 4. Why the weights stay hand-set

The feature weights, evidence weights and half-lives could be learned from
outcomes. With a handful of proposals a month per unit, a learned weight would
mostly fit noise (the CMS measurement: the same 40 cases scored 0 to 2 from
run to run on one build). They stay recommendation type data, adjusted by a reviewed change,
until the replay set ([50](50-verification.md) §1) is large enough that a
learned set beats the hand-set one on held-out decisions. Learning from picks is the
exception because it learns one rate per bucket, which needs far less data.
