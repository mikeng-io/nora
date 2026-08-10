# 0006 — `reasoning_effort = "none"`, on the narrowest justification that survived

- **Status:** accepted, with the root cause OPEN.
- **Date:** 2026-08-10
- **Index:** DEC-84 (`llm.model` → ollamacloud, `llm.reasoning_effort` → `none`).
  Related: DEC-39 (tool naming is measured), DEC-78 (the abstaining pre-model
  router), ADR 0004 (the first "measure the model, don't reason about it" record).

> **This document was rewritten twice.** Two confident, well-argued mechanisms
> were proposed here and then killed by direct measurement. Both are recorded
> below rather than deleted, because both are more attractive than the truth and
> the next person will reach for them. If you are here to change
> `reasoning_effort`, read "What was refuted" before you do.

## Context

Two questions arrived together and looked independent:

1. Which upstream should `llm.model` point at? `opencode-zen` was pinned because
   it was the only one of three that accepted a forced `tool_choice` while
   thinking was enabled — the other two returned
   `400 "Thinking mode does not support this tool_choice"`.
2. What should `llm.reasoning_effort` be? It was `low`, and the comment beside it
   recorded latency measurements that argued for `none` without the value ever
   having been changed.

The operator's framing was *"higher effort should be more accurate"*. That is
correct in general, and the interesting result is that it is also **correct
here** — and still does not decide the question.

## The upstream: economics first, capability second

`ollama-cloud` is **subscription-based**; `opencode-zen` is **credit-based**. Over
a probing session, zen and `opencode-go` returned `403`/`429` throughout:

```
All credentials for model deepseek-v4-flash are cooling down
```

A route that rate-limits under load is the wrong route for the only path the
group can reach Nora by, independent of any capability question.

The capability question agrees. The `400` that forced the zen pin **does not
occur on ollama-cloud** — 0 errors in 32 forced `tool_choice` calls across all
three published deepseek tags, and (verified direct, below) forced `tool_choice`
is accepted at *every* effort level. That `400` is an opencode-zen/go behaviour,
not a DeepSeek V4 property.

`-pro` is excluded by operator decision, and measurement agrees rather than
merely permitting it: `pro` obeyed a forced `tool_choice` 2/3 where `flash` at
`none` obeyed 8/8.

Ollama publishes `deepseek-v4-flash:cloud`, `:0731-cloud`, `:preview-cloud`;
OmniRoute exposes them with the `-cloud` suffix stripped.

### A measurement trap worth keeping

Every early probe of the bare tag returned
`502 "upstream returned an empty response without usable output"`, which reads as
"this model does not exist". **It was `max_tokens: 8`.** A thinking model spends
the whole budget on the reasoning block and emits no content; the gateway reports
that as an empty upstream response. Three tags were nearly declared missing on
the strength of it. Any probe against a reasoning model needs a real token budget
before its null results mean anything.

## What was measured

### Isolation benchmark — cognition vs. transport

Eight real Cantonese asks with ground-truth expected tools, 5 seeds each. **Arm
A**: the real tool schemas, `tools=[...]`, auto choice. **Arm B**: no tools at
all, the same descriptions in the prompt, answer as JSON. Arm B measures
classification with the function-calling protocol removed.

| effort | A: with tools | B: JSON only | extra calls (A) |
|---|---|---|---|
| none | 36/40 (90.0%) | 35/40 (87.5%) | 0 |
| low  | **39/40 (97.5%)** | 36/40 (90.0%) | 1 |
| high | 36/40 (90.0%) | **38/40 (95.0%)** | **6** |

- **Routing does not degrade with effort.** Flat, and `low` is the best.
- **Pure classification IMPROVES with effort** — 87.5% → 90% → 95%. The
  operator's intuition was right.
- **The only thing that degrades is extra tool calls** (0 → 1 → 6): a correct
  tool plus an unrequested second one, never a *wrong* tool. Absent from arm B,
  so it is a transport effect, not a cognition one.

### Journeys suite — the real multi-turn harness

Majority-of-3 per journey, through the full agent loop.

| journey | none | low | high |
|---|---|---|---|
| weather / weather_alt | 3/3, 3/3 | 3/3, 3/3 | 3/3, 3/3 |
| carpark | 3/3 | 3/3 | 3/3 |
| carpark_alt | 1/3 | 0/3 | 2/3 |
| mall / mall_alt | 3/3, 3/3 | 3/3, 3/3 | 3/3, 3/3 |
| where_work | 3/3 | 2/3 | 3/3 |
| where_work_alt | 1/3 | 2/3 | 1/3 |
| **journeys passed** | 6/8 | 7/8 | 7/8 |
| **total samples passed** | 20/24 | 19/24 | **21/24** |
| **`no_send_call` (silent turns)** | **5** | **4** | **15** |

Routing is a wash — one sample apart at n=3. The one column that is not a wash
is the last: **`high` ends the turn without ever calling `send_reply` three times
as often.** In a strict-silence architecture that is Nora going mute in a group
that tagged her.

## Decision

`reasoning_effort = "none"`, model `ollamacloud/deepseek-v4-flash`.

**The justification is narrow and should be stated as such: `none` is the only
mode with no unexplained multi-turn failure.** It is not a claim that reasoning
hurts — arm B measured the opposite. `low` is the first thing to re-enable once
`high`'s silent turns are understood, since on the clean single-call data it is
the best router (97.5%) with one extra call in 40.

DEC-78's abstaining router is **kept**. Dropping it was considered on the
strength of `none` scoring 5/5 on auto tool-choice, and rejected: that was one
query on one provider, and the router exists because this exact class of question
misrouted. The test that would justify removing it is a journeys run with
`route_intent` disabled — not an extrapolation.

## What was refuted — read this before re-deriving either

### Refuted #1: "more reasoning degrades routing"

The first version of this ADR was titled *"reasoning effort buys protocol
discipline, not answers"* and claimed auto tool-choice degraded monotonically
**5/5 → 3/5 → 2/5 → 1/5**. That figure was **n=5 on a single query** (the mall
one). The isolation benchmark above, on eight questions, shows routing flat and
classification improving. **Retracted.**

### Refuted #2: "the severed `reasoning_content` round-trip explains it"

The second version claimed thinking mode is structurally broken in this harness.
The evidence for it is real and still worth knowing:

- `none` and `low` are different **decoding pathways**, not different amounts of
  thinking — `none` returns `['content','role']`, `low` returns
  `['content','reasoning','role']`.
- Strands' OpenAI provider **deletes** the block between turns
  (`format_request_messages` filters `reasoningContent` and logs a warning). The
  warning counts across none/low/high were **0 / 51 / 86**.
- DeepSeek documents `reasoning_content` as required round-trip state for
  tool-calling turns.

**Two direct tests then failed to support the causal link.**

*Via OmniRoute* — the same conversation replayed three ways: reasoning omitted
(what Strands sends), echoed back, and omitted with the `tool_call_id` mangled to
defeat any id-keyed gateway cache. **All three identical**; the terminal
`send_reply` fired correctly in every one.

*Direct to Ollama Cloud, no gateway* —

```
message keys: ['content', 'reasoning', 'role', 'tool_calls']
  reasoning_content : absent
  reasoning         : PRESENT        <- Ollama's NATIVE name, not a gateway rename

(a) OMITTED (what Strands sends) -> ok, ['send_reply'], reasoning_back=Y
(b) echoed as reasoning_content  -> ok, ['send_reply'], reasoning_back=N
(c) echoed as reasoning          -> ok, ['send_reply'], reasoning_back=N

forced tool_choice: none ok / low ok / high ok
```

Omitting the block is not merely tolerated — it is the variant where the model
**keeps** reasoning on the next hop. Echoing it back **suppresses** further
reasoning. Strands stripping it may be closer to correct than the docs imply.

**Retracted as an explanation.** The strip is real; its harm is unproven.

### Ruled out: token exhaustion

Not from a log grep — from the code. `nora.engagement.pipeline` handles budget
cut-offs on a **separate branch** (`stop_reason in BUDGET_STOP_REASONS` → the
exhausted-fallback reply), so `no_send_call` is a loop that ended **normally**
having never called the tool. Independently: at production's `max_tokens = 2000`,
a `high` call returns `finish_reason: tool_calls` 8/8 using 82 tokens.

**The first attempt to rule this out was invalid and is recorded because the
error is easy to repeat.** Grepping the eval log for `limit_output_tokens`
returned zero, which looked like evidence — but the harness recorded no stop
reason at all. A null result from an instrument that cannot measure the thing is
not a null result (CLAUDE.md). That gap is now closed:
`evals.harness.Trajectory.stop_reason`.

## The root cause is OPEN

`high`'s 15 silent turns against 5 are real, reproducible in the journeys suite,
and **unexplained**. Ruled out: token exhaustion, the round-trip, and a routing
deficit. Remaining untested suspects:

- the real prompt shape — 13–17k input, ~20 tools — none of which the single-call
  benchmark exercises;
- the extra-call tendency (0 → 1 → 6) compounding across hops until the loop ends
  without reaching the terminal verb.

## Consequences

### What got worse

- **Routing is measurably, if marginally, worse than `high`** (20/24 vs 21/24),
  and worse than `low` on the clean single-call arm (90% vs 97.5%). This decision
  spends routing accuracy to buy delivery reliability. That is a trade.
- **`carpark_alt` regressed** — 2/3 at `high`, 1/3 at `none`; the one journey
  where effort showed a clean monotonic benefit.
- **Plan-shaped turns are untested at `none`.** Every journey is a short question
  answered by one tool call. A turn that legitimately needs multi-step planning is
  exactly where reasoning should help and exactly what the suite does not cover.
- **We traded a known limit for an unmeasured one.** zen's failure mode was
  understood (credits, cools down). ollama-cloud's rate limits are undocumented
  and transient `502`s were seen mid-probe. No soak test was run before pinning.
- **An argument made for this change was withdrawn.** The `51 → 0` reduction in
  `reasoningContent` warnings was initially presented as disqualifying for `low`.
  It is not — those warnings did not correlate with silent turns (`low` had 4,
  `none` had 5).

### What follows

- The gateway streams by default; any probe must pass `stream: false` or the
  response will not parse.
- **Keep OmniRoute rather than going direct.** The two paths measured identical,
  so direct buys nothing on correctness and costs cross-provider failover, the
  cost/latency headers, and the caching layer.
- **Per-phase reasoning is coherent with all of this and is not yet evaluated:**
  route at `none`, reason at `high`. It is the natural shape given that
  classification improves with effort while the terminal-action failure worsens.
  Blocked on understanding `high`'s silent turns first.

## What would change this decision

1. **`journey_samples = 9`** (raised from 3 on 2026-08-10). At n=3, two journeys
   flipped on a single sample — exactly the resolution 3 cannot resolve.
2. **Read `Trajectory.stop_reason`** on a `high` run. If the silent turns come
   back `end_turn`, it is a model choice; if `limit_*`, it is the loop. That one
   field distinguishes the two remaining hypotheses and did not exist when this
   was first investigated.
3. If `high` holds its routing edge at n=9 **and** its `no_send_call` rate is
   explained, revisit — starting with `low`.
