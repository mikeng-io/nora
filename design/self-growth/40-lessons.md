---
type: design
title: "Lessons: accumulated from closed response episodes, with a correction fast path"
description: "A lesson is practice induced from the outcomes of many of her acts. The event is a response episode closing, not a message and not a clock. Episodes pile up; at a threshold one Lessons session per unit writes, reinforces, consolidates or retires lessons, each citing the episodes that taught it, under a size cap and the doctrine check. An explicit correction aimed at her wakes it at once. The hard part is judgement quality and a labelled set, not code."
tags: [nora, lessons, event-driven, dreamer, doctrine-check, tension]
timestamp: '2026-10-07'
edges:
  - {dst: ../cognition-loop/50-character-and-standing.md, rel: realizes, provenance: asserted}
---

# 40 · Lessons

## 1. The unit of evidence

A `response` episode (`nora/infra/sql/rollups.py`, Rollup step 2a): one of her
sends plus the replies, reactions and edits inside `response_window_seconds`
(3600 s), or `silent` where an answer was expected and the platform was proved
up, or `unknown` where it was expected but not proved. One episode is one data
point. A lesson is a pattern across many.

## 2. How lessons accumulate

1. **She sends.** The Rollup opens a `response` episode.
2. **The window closes.** The episode becomes a data point.
3. **Episodes pile up.** Nothing runs; inducing from one episode would rewrite
   her practice from noise.
4. **The Lessons adaptor wakes** for the unit on the first of:
   - closed episodes since its watermark ≥ `lessons_batch` (design start: 30);
   - `lessons_stale_seconds` (7 days) with at least one new episode;
   - a lesson reaching its horizon (60 days);
   - **an explicit correction aimed at her** (new, §3).
5. **One session** (Dreamer agent loop) reads the new episodes and the lessons
   held, and calls `lesson_write` or `lesson_retire`: write, reinforce,
   consolidate or retire.
6. **Takes effect on the next turn**, rendered as the `lessons` block after
   the self block. No approval step (D7).
7. **Decay.** A lesson not re-evidenced by its horizon is retired. The
   operator's rollback by id always wins and is never re-learned from outcomes
   older than it.

## 3. The correction fast path (new)

The cognition design gives Character a fast path (a dispute) and Lessons none.
When someone tells her directly how to act ("唔好成日俾五個選擇", "你講少啲啦"),
waiting for 30 episodes is wrong. Ingest marks a message that addresses her
with an instruction about her own behaviour as a strong cue; it wakes Lessons
for that unit after the usual cue quiet. The correction is a Record item the
lesson cites. How the cue is detected (a code pattern, or the tag turn raising
it) is decided in the PR.

## 4. Enforced by code on every write

- **Citations:** a lesson cites the `response` episodes (or the correction)
  that taught it. Her own sends alone are refused (the echo rule).
- **Size cap** per unit, about 800 tokens to start; a write past it is refused
  until the session consolidates or retires.
- **Doctrine check:** a separate instrument call reads the lesson and the law
  subset ([30](30-doctrine-sort.md) §2). A lesson that would loosen or
  contradict law is refused and written as a tension row. An instrument error
  is a refusal, counted, never a pass.
- **Rollback by id:** `lesson_retire`, reachable by the operator. A CLI first;
  the portal page later.

## 5. Tension rows

Operator-only: never returned by a read tool, never rendered in a block, with a
test pinning both (`design/cognition-loop/55-self.md` §4). The doctrine file
and line quoted, the evidence cited, and what the lesson would have said.

## 6. What is hard

- **Noisy labels.** A reaction, a reply or silence is a weak signal of whether
  something worked.
- **Drift toward engagement.** She learns whatever earns reactions
  (`design/cognition-loop/80-prior-art.md` §4). The cap, decay, rollback and
  the Assess pass push against it.
- **"Expected an answer" is thin.** Only a structured mention today; the
  brief's `ask` intent on a send is still NULL
  (`design/cognition-loop/25-rollup.md` §3). Until it is recorded, `silent` is
  under-counted. Recording it is a prerequisite worth its own PR.
- **A labelled set before going live**, including lessons that would loosen
  law, which must become tension rows. The largest single effort here.

## 7. Work, in PRs

| PR | content | check |
|---|---|---|
| L0 | record the brief's `ask` intent on each send | `silent` counted on expected asks |
| L1 | `lesson` table and revision chain; `lesson_write`, `lesson_retire` with citation, echo and cap checks; CLI rollback | write, cap refusal, rollback and no re-learn, in tests |
| L2 | the Lessons adaptor, its objective and its wake (batch, stale, horizon) | wakes at threshold only; a session writes or closes |
| L3 | the doctrine-check instrument on the law subset; tension rows, operator-only | a labelled set of loosening lessons all refused and written as tension |
| L4 | the correction fast path | a correction wakes Lessons within the cue quiet |
| L5 | the `lessons` block, after `self` | before/after eval (`nora-eval`) |
