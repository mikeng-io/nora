# 0003 — `ContextBudget` is deferred; read the counter first

- **Status:** accepted
- **Date:** 2026-08-06
- **Index:** R-23 (a turn that ran out of budget is never reported as a turn
  that chose not to speak), R-24 (no single call or tool result may spend the
  whole turn's budget), DEC-31 (the `↳` snippet, shipped),
  `design/proposed/18-context-budget-and-handles.md` (the proposal),
  `design/proposed/14-grounding.md` §4 (which depends on this outcome)

## Context

`design/proposed/18` proposes `ContextBudget`: a per-tag object that knows the
turn's remaining `total_tokens` and hands every fetching tool a number, so
`read_buffer(60)` early in a turn returns 60 rows and the same call after a
large fetch returns 20. It is a well-argued document. An adversarial review on
the day it was written argued it should not be built yet, and three findings
carried that argument.

**Finding 1 — the problem is unmeasured, and the instrument already exists.**
`nora_llm_stop_reason_total{reason="limit_total_tokens"}` is **live**
(`nora/engagement/stages/invoke.py:128`, `nora/obs/metric_specs.py`, R-23). It
counts exactly the event `ContextBudget` exists to prevent: a turn that ended
because it ran out of input budget. Nobody has read it. Building a component to
manage a pressure whose counter has never been looked at is building against a
guess.

The numbers design/18 does have are per-call costs, not turn outcomes
(measured 2026-08-06, `opencode-zen/deepseek-v4-flash`, `usage.prompt_tokens`):

    base prompt (doctrine + schemas + 40-row window)   4,952   every call
    read_buffer(60) result                            ~2,000   every call after
    read_buffer(200) result                            6,592   every call after
    about / recall (5 conclusions)                       151
    lookup_person                                        112
    total_tokens ceiling                              64,000

None of those is evidence that a turn has ever hit the ceiling.

**Finding 2 — design/18 rations linearly for a cost that is multiplicative.**
Every tool result stays in the message list and is re-sent on **every
subsequent model call** (`llm.limits.turns = 8`). So admitting X tokens on call
2 of 6 does not cost X; it costs roughly 5X. A budget that decrements a
remaining-token counter by the size of what it admits is modelling the wrong
quantity — the true cost is `tokens × calls remaining`, which the design does
not compute and could not compute without knowing how many calls are left, a
thing not known at admission time. design/14 §4 states the same arithmetic
independently and draws the same conclusion.

**Finding 3 — there is no usable estimator to feed it.** Strands' proactive
context management reads `event.projected_input_tokens`
(`strands/_middleware/stages.py`, `strands/agent/conversation_manager/conversation_manager.py`).
That value comes from `strands/models/model.py`, whose docstring says it "uses
tiktoken's `cl100k_base` encoding when available, otherwise falls back to a
heuristic (characters / 4 for text, characters / 2 for JSON)". **tiktoken is
not installed in this environment** — it is absent from
`.venv/lib/python3.13/site-packages/`, so the heuristic is the only path. At
chars/4 a Traditional Chinese message is counted at 0.25 tokens per character
against a real cost closer to 1–1.5, an under-count of **4–6×** on Nora's
primary language.

So a budget built today would be a wrong model (linear, for a multiplicative
cost), fed by a wrong estimator (chars/4 on Cantonese), for a problem nobody
has measured.

## Decision

**`ContextBudget` is deferred. The counter is read first.**

`nora_llm_stop_reason_total{reason="limit_total_tokens"}` is the evidence
gate. Until it shows real pressure in production traffic, intake is bounded by
**constants in code** — a result count and a byte ceiling per tool, with
truncation stated in the return value — which is what design/14 §4 commits
grounding to, and why grounding does not wait on design/18.

**Reading the counter is the operator's next step and has not happened.** This
ADR does not record a number; it records that no number has been read and that
the component is blocked on one.

**The cheap half of design/18 shipped anyway.** DEC-31 / `624955d` landed the
`↳` snippet: `_parent_of` already resolved the exact parent row and
`_transcript_block` threw it away, rendering only the author's label, so two
messages from one author made `↳ Grant` unable to say which was answered. It
now renders `[Tyler ↳ Grant「去邊食」]`, clipped at 14 characters. Measured on
the pinned route, 40-row window with 6 reply-lines: transcript block **774 →
817 tokens (+43**, ~7 per reply-line), `_context_header` +18 fixed, whole
prefill 1,024 → 1,085. Only reply-lines pay it and replies are a minority of a
window.

**The handle map, `#N` labels and `read_thread` are deferred**, for two reasons
that are separate from the budget:

1. **Cost with no measured need.** ~80–120 tokens on *every* call regardless of
   whether the window is threaded at all, against +43 on a threaded window for
   the snippet that solves the same disambiguation problem.
2. **`#\d+` in an egress predicate would mute her.** design/18 §3.1 closes both
   escape routes for a handle with a `#\d+` predicate over reply text and the
   memory feed. `nora/agent/egress.py` documents, in its own module docstring,
   that free-standing digit runs are **deliberately not matched** — "prices,
   dates, counts and version numbers are ordinary chat content, and blocking
   them would make her mute at random" — and the guard **fails closed on the
   whole reply**. So 「#1」 in an ordinary message would silence her. Adding
   that predicate reverses an explicitly reasoned decision, and it should not
   be reversed to serve a feature whose need is also unmeasured.

## Consequences

**Better.** Grounding (`design/14`) is unblocked: it ships with hard constants
rather than waiting on a component. The turn-budget work that *did* have
evidence — R-23 and R-24, both measured 2026-08-06 — shipped and is enforced,
so a turn that runs out is at least legible today even though it is not
rationed. And the estimator gap is now written down, so nobody builds a
second thing on `projected_input_tokens` without knowing what it returns for
Cantonese.

**Worse, and accepted.**

- **The hole design/18 opens with is real and is still open.**
  `read_buffer`'s clamp was cut from 200 to 60 for this exact reason, and at
  `budget: 2` a turn can still spend two of them. **The clamp bounds one call;
  nothing bounds the turn.** Deferring means shipping grounding on top of that,
  with per-tool constants that are four independent guesses — which design/18
  correctly identifies as how the `read_buffer(200)` hole existed in the first
  place. This ADR accepts a known structural gap in exchange for not building
  against a guess.
- **"Read the counter first" has no owner and no date.** It is an instruction
  in a document, which is the weakest kind of gate this repo has, and exactly
  the sort of thing CLAUDE.md's "never read an instrument's output as evidence
  without first checking it can measure the thing" was written after. The
  counter can measure this — R-23's tests drive the real SDK — but nobody has
  looked.
- **The `↳` snippet does not solve pointing, only reading.** She can now tell
  which message a reply answers; she still cannot *refer* to a specific message
  when calling a tool. `read_thread` therefore does not exist, and design/18's
  argument that handles-without-`read_thread` are decoration cuts the other way
  too: `read_thread`-without-handles would have to take a timestamp the model
  would invent, which DEC-24 forbids. Both stay deferred together or neither
  works.
- **Deferring costs +43 tokens per threaded window forever** for a partial
  answer, where the full one was ~80–120 per call. That is the better trade
  today and it is not free.

## Rejected

**Building `ContextBudget` now, on the strength of the argument.** The design
is coherent and its "never evict" property is genuinely right — mid-turn
eviction lets Nora reason from a result that is then deleted, so she
contradicts herself with no trace of why, and budgeting the fetch never removes
something she has already seen. That property survives this deferral and should
be kept when the component is eventually built. The rejection is of *timing*,
not of the idea.

**Mid-turn eviction** (Strands' `SlidingWindowConversationManager`) — rejected
outright, not deferred, for the reason above. Nora runs `NullConversationManager`
because she is stateless per tag, and that stays.

**"Sliding window" as the name.** Nothing slides. A finite per-turn resource is
rationed. The term was used loosely in conversation before design/18 existed
and is recorded here only so it is not picked back up.

**Adding a `#\d+` egress predicate to unblock handles.** Rejected on
`nora/agent/egress.py`'s own documented reasoning, which predates the proposal
and is more specific than it.
