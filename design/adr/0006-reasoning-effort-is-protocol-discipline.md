# 0006 — Reasoning effort buys protocol discipline, not answers

- **Status:** accepted.
- **Date:** 2026-08-10
- **Index:** DEC-84 (`llm.model` → ollamacloud, `llm.reasoning_effort` → `none`).
  Related: DEC-39 (tool naming is measured), DEC-78 (the abstaining pre-model
  router), ADR 0004 (the first "measure the model, don't reason about it" record).

## Context

Two questions arrived together and looked independent:

1. Which upstream should `llm.model` point at? `opencode-zen` was pinned because
   it was the only one of three that accepted a forced `tool_choice` while
   thinking was enabled — the other two returned
   `400 "Thinking mode does not support this tool_choice"`.
2. What should `llm.reasoning_effort` be? It was `low`, and the comment beside
   it recorded latency measurements that argued for `none` without the value
   ever being changed.

They are not independent. The first is settled by economics and the second is
settled by a property of this architecture that is easy to state and easy to get
backwards.

**The operator's framing, which was the right instinct:** *"higher effort should
be more accurate."* It is — for tasks with a hard reasoning core. This turned out
not to be one, and the measurement says so clearly enough to be worth recording
rather than re-deriving.

## The upstream: economics first, capability second

`ollama-cloud` is **subscription-based**; `opencode-zen` is **credit-based**. Over
a probing session on 2026-08-10, zen and `opencode-go` returned `403`/`429`
throughout, with the gateway saying plainly:

```
All credentials for model deepseek-v4-flash are cooling down
```

A route that rate-limits under load is the wrong route for the only path the
group can reach Nora by, independent of any capability question.

The capability question resolves the same way. The `400` that forced the zen pin
**does not occur on ollama-cloud**: 0 errors in 32 forced `tool_choice` calls
across all three published deepseek tags. Ollama publishes `deepseek-v4-flash:cloud`,
`:0731-cloud` and `:preview-cloud`; OmniRoute exposes them with the `-cloud`
suffix stripped, and `ollamacloud/deepseek-v4-flash` resolves.

`-pro` is excluded by operator decision, and the measurement agrees rather than
merely permitting it: `pro` obeyed a forced `tool_choice` 2/3 where `flash` at
`none` obeyed 8/8. The larger model is not better at the thing this system needs.

### A measurement trap worth keeping

Every early probe of the bare tag returned
`502 "upstream returned an empty response without usable output"`, which reads as
"this model does not exist". It was `max_tokens: 8`. **A thinking model spends
the whole budget on the reasoning block and emits no content**, and the gateway
reports that as an empty upstream response. Three separate tags were nearly
declared missing on the strength of it. Any probe against a reasoning model needs
a real token budget before its null results mean anything.

## The effort level: what was actually measured

Two independent probes on `ollamacloud/deepseek-v4-flash`, plus the live journeys
suite at three effort levels.

**Direct API probe** (n=5–8 per cell, one opening-hours question, `place_info`
forced or auto):

| effort | out tok p50 | out tok max | p50 latency | obeyed (forced) | correct (auto) |
|---|---|---|---|---|---|
| none   | **49** | 51  | **1.2s** | **5/5** | **5/5** |
| low    | 70     | 107 | 1.1s     | 3/5     | 3/5     |
| medium | 81     | 118 | 1.2s     | 3/5     | 2/5     |
| high   | 104    | 126 | 2.9s     | 4/5     | **1/5** |

**Journeys suite** (`--suite journeys`, majority-of-3 per journey):

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
| **`no_send_call` (silent turns)** | 5 | 4 | **15** |

## Decision

`reasoning_effort = "none"`.

**Not because routing is better at `none`. It is not.** By total samples the
order is `high` 21 > `none` 20 > `low` 19 — one sample apart at n=3, which is
noise. Anyone reading this later should not take away "less reasoning routes
better", because that is not what was measured.

The deciding column is `no_send_call`: **`high` ends the turn without ever
calling `send_reply` three times as often.** In this architecture that is Nora
going mute in a group that tagged her.

### The mechanism — CORRECTED 2026-08-10, after external review

**The first version of this ADR got the mechanism wrong, and the wrong version is
kept here because it is the more attractive story and someone will reach for it
again.** It claimed the cause was architectural: Nora is tool-mediated, she cannot
speak except through `send_reply`, so a larger deliberation budget lets the model
reason its way to a good answer and then simply *state* it rather than routing it
through the tool — answer improves, delivery fails. Plausible, consistent with the
failure shapes, and **not what is happening**.

**The actual cause is that thinking mode is structurally broken in this harness.**
Two facts, both verified directly:

1. **`none` and `low` are different DECODING PATHWAYS, not different amounts of
   thinking.** Measured against the live gateway:

   ```
   reasoning_effort="none" -> message keys: ['content', 'role']
   reasoning_effort="low"  -> message keys: ['content', 'reasoning', 'role']
   ```

   `none` takes the non-thinking path and emits no reasoning block at all.

2. **Strands DELETES the reasoning block between turns.** In
   `strands/models/openai.py::format_request_messages`:

   ```python
   if any("reasoningContent" in content for content in contents):
       logger.warning("reasoningContent is not supported in multi-turn "
                      "conversations with the Chat Completions API.")
   ...
   if any(block_type in content for block_type in
          ["toolResult", "toolUse", "reasoningContent"]):
       continue          # <- filtered OUT of the outgoing request
   ```

   DeepSeek V4 requires `reasoning_content` from a tool-calling turn to be passed
   back in the following request. It is not passed back. It is stripped, with a
   warning.

So in thinking mode the model's own reasoning trajectory is **severed at every
multi-turn hop**. It reasons, calls a tool, and on the next call has no record of
why it did so. `none` is unaffected because there is no reasoning block to lose.

This is what the warning counts were measuring all along — **0 / 51 / 86** across
none / low / high is a direct count of *how often the model's reasoning was
discarded mid-conversation*. This ADR's first draft dismissed those as harmless
noise because they did not correlate with `no_send_call`. They are not noise; they
are the defect.

The observed failure shapes still hold as *symptoms* — an unrequested second tool
call, or prose instead of a call — but they are downstream of a severed reasoning
trajectory, not of strict-silence.

A third fact fits the same frame: DeepSeek V4 thinking mode does not support
forced `tool_choice`, which is exactly the `400 "Thinking mode does not support
this tool_choice"` that forced the original opencode-zen pin. On ollama-cloud the
same incompatibility surfaces without an error — the forced call is accepted and
then quietly violated by adding a second tool. Same root, two provider behaviours.

**Credit where due:** this correction came from an external review that predicted
both facts before they were checked — that `none` and `low` would prove to be
different protocols rather than different budgets, and that the `reasoning_content`
round-trip would be broken. Both were verified here against the running system.

`low` is dominated outright: worst routing of the three, no cost advantage over
`none`, and 51 `reasoningContent is not supported in multi-turn conversations`
errors for nothing.

## What was ruled out, and how

**Token exhaustion is NOT the cause of the silent turns.** The obvious hypothesis
— reasoning burning `max_tokens` before the tool call, exactly the mechanism
behind the `502`s above — is refuted twice:

- Directly: at `max_tokens = 2000` (production's value), a single call at `high`
  returns `finish_reason: tool_calls` 8/8 with 82 out-tokens. 24× headroom.
- Structurally: `nora.engagement.pipeline` handles budget cut-offs on a *separate*
  branch — `stop_reason in BUDGET_STOP_REASONS` triggers the exhausted-fallback
  reply. `no_send_call` is the branch where the loop ended **normally** having
  never called the tool. A turn that ran out of budget is not labelled
  `no_send_call`.

The first attempt to rule this out was invalid and is recorded because the error
is easy to repeat: grepping the eval log for `limit_output_tokens` returned zero
matches, which looked like evidence. **The log records no stop reason at all** —
`evals.harness.Trajectory` has no such field. A null result from an instrument
that cannot measure the thing is not a null result (CLAUDE.md's rule, applied to
this ADR's own reasoning).

## Confidence, and what would change this

**Moderate, not high.**

- n=3 per journey. The only pattern that looks real rather than noisy is
  `carpark_alt`'s monotonic 0/3 → 1/3 → 2/3, and it favours `high` on the axis
  this decision declines to buy.
- `carpark_alt` fails at every effort level, and DEC-78 already records why: the
  canned `web_search` backend cannot authoritatively answer real-time vacancy, so
  that journey measures backend capability rather than routing. Two of eight
  journeys are effectively non-discriminating.
- The protocol-compliance *mechanism* is a hypothesis with a consistent failure
  shape behind it, not a measurement. What is measured is the correlation and the
  exclusion of budget exhaustion.

**The experiment that would actually settle it — and it is not the one this ADR
first proposed.** Because thinking mode is running with its reasoning trajectory
severed (see the corrected mechanism), every `low` and `high` number here is a
measurement of *crippled* thinking, not of thinking. The comparison to run is:

1. Fix the round-trip — either carry `reasoning_content` through in a provider
   that supports it, or stop using the Chat Completions path for thinking mode.
2. Re-run journeys at `none` / `low` / `high`.

**Note what this implies about the data already collected: `high` scored the BEST
routing (21/24) while its reasoning was being discarded on every hop.** That is
an argument for revisiting this decision once the round-trip works, not for
treating `none` as settled forever.

Secondary, still worth doing: `journey_samples = 9` to separate `where_work_alt`
and `carpark_alt` from noise, and adding `stop_reason` to
`evals.harness.Trajectory` — the harness currently cannot report why a turn
ended, which is what made this ADR's first mechanism unfalsifiable.

**Architectural option this opens up** (raised in the same external review, not
yet evaluated): reasoning effort need not be a single global setting. Routing is
constrained classification and wants `none`; planning and ambiguous synthesis
want more. A per-phase policy — route at `none`, then reason at `high` — is
coherent with everything measured here. It is blocked on the same round-trip fix,
because a high-effort phase inherits the same severed trajectory today.

## Consequences

### What got worse

- **Routing is measurably, if marginally, worse than `high`.** 20/24 samples
  against 21/24. This decision spends routing accuracy to buy delivery
  reliability, and that is a trade, not a free win.
- **The hardest journey regressed.** `carpark_alt` scored 2/3 at `high` and 1/3
  at `none` — the only journey where effort showed a clean monotonic benefit, and
  this decision moves away from its best setting.
- **Genuinely hard turns are untested at `none`.** Every journey in the suite is
  a short question whose answer is one tool call. A turn that legitimately needs
  multi-step planning — the `navigation` skill weighing departure times against a
  forecast, say — is exactly where reasoning should help and exactly what the
  suite does not cover. If plan-shaped turns start coming out thin, this is the
  first thing to re-examine.
- **We traded a known limit for an unmeasured one.** opencode-zen's failure mode
  was understood: credits, and it cools down. ollama-cloud's rate limits are
  undocumented, and transient `502`s were observed mid-probe. Subscription pricing
  removes the *cost* cliff; it does not prove the *capacity*. No soak test was run
  before pinning this, which is the honest gap in the upstream half of this ADR.
- **One argument made in favour of this change was withdrawn.** The `51 → 0`
  reduction in `reasoningContent is not supported in multi-turn conversations`
  errors was initially presented as disqualifying for `low`. It is not: those
  errors did not correlate with silent turns (`low` had 4, `none` had 5), so
  Strands is recovering from them. The claim is recorded here as retracted rather
  than deleted, because it was load-bearing in the discussion that produced this
  decision.

### What follows from it

- `where_is`, `place_info` and the rest keep DEC-78's abstaining router. Dropping
  it was considered on the strength of `none` scoring 5/5 on auto tool-choice —
  and rejected, because that is **one query on one provider**, and the router
  exists precisely because this class of question misrouted. The test that would
  justify removing it is a journeys run with `route_intent` disabled, not an
  extrapolation from a probe.
- The gateway streams by default. Any future probe must pass `stream: false` or
  it will not parse a response, which cost real time here.
