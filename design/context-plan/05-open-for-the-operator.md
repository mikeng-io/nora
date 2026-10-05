---
type: design
title: "Open for the operator — the seven questions, answered with what is known and what is not"
description: "Direct answers to the seven questions the operator asked, each marked Known (read off code or measured today) or Unmeasured, with the decision each one needs from Miles."
tags: [nora, context-plan, open, decisions]
timestamp: '2026-10-05'
edges:
  - {dst: 00-overview.md, rel: realizes, provenance: asserted}
---

# Open for the operator

Answers first, in the operator's numbering. Every line is marked **Known**
(read off the current tree, or measured today and recorded in `03`) or
**Unmeasured** (an inference, said so).

## 1. Where `ContextPlan` should live

**Known.** `nora/domain/blocks.py`, beside `ContextBlock` / `BlockKey` /
`assemble`; constructed in `nora/engagement/pipeline.py` immediately after
`self._context.assemble(...)` (`:606`) and before `build_prefill` (`:754`);
consumed by `build_prefill` alone, as a filter over its `_BLOCKS` walk
(`:287-298`). Not in `TagContext` (it is read downstream independently — `:708`,
`:734`, `:775`, and `culture` into the debrief at `:607`), not in
`EngageDecision` (whose meaning is the gate, and whose `rationale` seam is for a
classifier's reasoning, not a block list), not in admission (documented pure, no
I/O, `admission.py:75`). Reasoning in `01`.

## 2. Before or after `TranscriptInjection.assemble()`

* **Phase 1: after.** `03` §6 is the reason — a local decision call is 6–33 s,
  and putting it in front of the gather buys nothing in Phase 1 because nothing
  is being skipped.
* **Eventual ideal: before, and only with a fast hosted selector.** `01` §2 and
  `04` S4 carry the condition — a selector under ~150 ms whose substrate is
  already assembled. **Unmeasured:** whether a hosted JEV/Laya call actually
  meets the vendor's 70–500 ms from Nora's container; no key exists to try.

## 3. The minimum routing substrate

**Known, from the code, and it is smaller than the proposal assumes.** The tag
turn *already has* `ctx.message`, the resolved tagger (`ctx.tagger`, a pure
`Persons.resolve` dict lookup, no I/O), `culture`, `standing` and the transcript
at the moment the plan would be built — all of it from the same gather
(`context.py:239-254`). So the marginal substrate cost of a plan-after-assemble
is **zero reads**; the substrate is a subset of what has already been fetched.

Ranked by what a selector actually needs:

| substrate item | verdict | basis |
|---|---|---|
| the tagging message | **necessary** | it is the ask; `route_intent` already shows a pure-string measure over `msg.body` on this path (`nora/agent/intent_router.py`, called at `pipeline.py:686`) |
| tagger identity | **necessary if any block is tagger-keyed** | `peer_card`/`peer_history` are keyed on the tagger; dropping them is a correctness decision, so the identity belongs to the *classification*, not to the substrate |
| Culture | **useful, largest selectable** | `02` §2 — it is the candidate worth testing and the argument for keeping it is strong |
| Standing | **cheap and continuity-bearing** | 1,200-char cap; revised in place; `02` §2 argues keeping it |
| Presence | **not needed by the selector** | it is mandatory anyway; a selector never gets to consider it |
| recent transcript | **needed only for a window decision** | and a window decision is `02` §3's floored knob, not a substrate question |

**Unmeasured:** whether a 752M model can classify *any* of this correctly on
Nora's own Cantonese-mixed rooms. The plan questions were run on a hand-built
state, not on recorded turns — the model answered them (probabilities in `03`
§"the plan question set") but nothing graded the answers.

## 4. Mandatory / selectable / never-selector-controlled

**Known**, with the criterion in `02` §1 (a block is not selector-controlled
when code outside `prefill.py` reads it, or when its absence changes a named
acceptance rule). Summary:

* **Mandatory:** `group_transcript`, `replying_to`, `tag_message` (resolvers +
  the ask), the clock line, `presence`, `room_members`, `tool_catalogue`,
  `peer_card`, `peer_history` (tagger-keyed).
* **Selectable:** `culture`, `character`, `notes`, `loop_decisions`, `standing`,
  `standing_working_set`, `computer`, `documents`, `subagents`,
  `people_in_window`, `self_card`, `media_text`.
* **Never selector-controlled by construction, because they are not blocks in
  this table:** doctrine (authored, 72% of the prompt, `applies_to`-routed),
  the tool schemas (built from the room's policy), `<available_skills>`.

**The finding to act on:** the operator's own sample plan
(`presence`, `peer_card`, `standing`, `group_transcript`) contains **three
mandatory blocks and none of the selectable ones** — so as written it is not a
selection over the selectable class at all.

## 5. Must Culture and Standing always reach the main model

* **Standing: yes, in practice**, even though it is classed selectable —
  1,200-char cap, revised in place, and the transcript beside it only reads as
  "where I was, what has been said since" as a pair (`02` §2).
* **Culture: unmeasured, and it is the right experiment.** It is the largest
  selectable block and the strongest case *for* keeping it is the tier's own
  reason to exist (`nora/domain/culture.py`: a window judged against nothing is
  judged in a vacuum). **Decision needed:** is culture a per-turn relevance
  question or a per-room standing fact? `04` S2 answers it from recorded turns
  for the price of an eval run.

## 6. How transcript selection should work

**Known, and it is narrower than the question implies.** Three knobs exist
(extent, quote growth, and the `first_by_ts` id space); recency/sender/threading
filtering does **not** exist and cannot be added at the plan's current position,
because the rows are one oldest-first tuple and the two look-alikes are already
elsewhere (`people_in_window`; the `who_said` tool). The plan gets **extent from
the newest end**, **floored by the quote resolver**
(`QuoteWindow.recent_for_quote` + `quote_window_multiplier`, DEC-195) — a floor
the pipeline computes and records, not a scalar in the plan. `02` §3.

## 7. Selector failure

**Known / designed.** Fail-**open** in every case, and recorded: unreachable,
over-timeout, low confidence, malformed, refused-mandatory, refused-degenerate →
full context, each as its own `nora.plan_selector_total{outcome=…}` label
(`04` S3). An ERROR is never a PASS (R-27), and "nothing withheld" is a
legitimate answer. The call is bounded by the turn deadline
(`nora.domain.turn_deadline`) and is not a tool, so it does not spend the reply
budget (R-7).

## Decisions this bundle needs from Miles

1. **Is the iGPU override worth fixing?** `/etc/systemd/system/ollama.service.d/override.conf`
   has an unterminated `Environment="OLLAMA_VULKAN=1` line, so `OLLAMA_VULKAN` has
   never been set and the daemon has always dropped the Radeon 680M
   (`03` §4.3). Fixing it needs root; user `norty` is also not in `render`/`video`,
   so a user-level server cannot open the render node either. Until this is
   resolved, every local decision number is CPU-only.
2. **Is a hosted decision API in scope at all?** JEV/Laya are reachable only by
   their own protocol, are not implemented by OmniRoute's System One route (there
   is none), and no key is configured (`03` §4.1). Wiring one means a new
   outbound HTTPS destination from the Nora container and a new provider — an
   egress and cost decision, not a config edit.
3. **S1 + S2, or S2 alone?** S2 (measure the recorded turns) needs no production
   code and answers the premise. S1 (the value + filter) is inert and is what
   makes an experiment possible later. My recommendation is S2 first, S1 in the
   same PR only if the operator wants the instrument standing by.
4. **Does the token goal matter at all?** If yes, the lever is doctrine (72% of
   the request), not context selection — that is a prompt-authoring decision and
   `design/17-prompt-substrate.md` is where it lives. If the goal is purely
   cognition quality, S2 is still the right first step and the token arithmetic
   in `03` §6 stops being an objection.
