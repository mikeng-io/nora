---
kind: finding
title: "A production turn burned 130,809 tokens and said nothing — the veto-retry loop, an unreachable `turns` cap, and dead cost tracking"
description: "Investigation of Langfuse trace 0459c8b9cb9f61c0bf9566418ae26981. The R-8 total_tokens cap is correct and fired correctly; four other things are wrong. A budget-vetoed tool call costs a full round trip, so six refusals burned ~86k tokens; turns=20 is unreachable because per-call is 2.4x R-24's sizing constant; the cap counts prompt-cache reads at full weight; and Langfuse had no model definition, so every cost read 0."
tags: [nora, finding, budgets, observability, langfuse, cost, R-5, R-8, R-24]
timestamp: '2026-08-08'
status: findings verified — remediation not started
---

# A turn that spent 130,809 tokens and sent an empty message

## 0. Origin

The operator noticed a Langfuse trace whose total usage (130,809) exceeded
`[llm.limits] total_tokens = 128000` and asked whether the accounting was wrong.

**It was not.** The cap is correct and fired correctly. But answering the
question surfaced four other things that are.

## 1. Method

Two independent instruments, because neither alone was sufficient:

1. **Read the enforcement code** — `strands/event_loop/event_loop.py:63`
   (`_check_limits`) and its call site at `:233`.
2. **Fetched the trace** from the Langfuse public API using the credential in
   the container's `LANGFUSE_OTLP_HEADERS` (URL-decode it first — passing it
   raw yields `401 Invalid authorization header`).

The local metric `nora_llm_stop_reason_total` was checked **and rejected as
evidence**: the container had been up 11 minutes, every counter read 0, and a
labelled counter does not exist until its first increment. A null result there
is uninformative, not exculpatory. (CLAUDE.md: never read an instrument's output
as evidence without first checking it can measure the thing.)

## 2. The cap is not the bug

```python
# strands/event_loop/event_loop.py:63
invocation = agent.event_loop_metrics.latest_agent_invocation   # per-invoke, not lifetime
total_tokens = invocation.usage.get("totalTokens", 0)
if total_cap is not None and total_tokens >= total_cap:
    return "limit_total_tokens"
```

Checked at the **top of a cycle**, before the model call. So the cycle that
crosses the line always completes, and **overshoot by one cycle's usage is
by-design**. 130,809 − 128,000 = 2,809. Working as intended.

`total_tokens` is a *threshold for declining more work*, not a ceiling on spend.
Worth stating in `config.toml`, where it currently reads like a ceiling.

## 3. The trace

`0459c8b9cb9f61c0bf9566418ae26981` — one `invoke_agent nora`, 2026-08-07T17:33.

```
observations   1 AGENT, 9 SPAN (event loop cycles), 9 GENERATION, 10 TOOL
output         {"message": "", "finish_reason": "limit_total_tokens"}

input reported     129,543
  of which cached   98,929   (76.4%)
  fresh             30,614
output               1,266
─────────────────────────
TOTAL              130,809
```

Per-call input ran 11,949 → 15,720. It barely grows: the accumulating tool
results are only ~3.8k. The other **~12k is a fixed prompt** — system prompt,
doctrine, capability block, tool schemas — re-sent identically nine times.

All ten tool calls were `web_search` / `web_fetch`.

---

## Finding 1 — she sent nothing (R-5)

`{"message": ""}`. The turn consumed 130,809 tokens and the group received
silence. This is R-5's exact failure shape reaching production.

The stop reason is *recorded* (`nora_llm_stop_reason_total`, invoke.py:128) but
nothing **alarms**, and nothing substitutes a message. A turn that runs out of
budget should still say something — 「我搵嘢搵到一半停咗，等我再試」 beats nothing.

## Finding 2 — a vetoed tool call still costs a full round trip

**Six of the ten tool calls were refused by `ReplyGuard`** for exceeding their
per-tool budget:

```
TOOL web_search   "You already called web_search 2 time(s) …"   x4
TOOL web_fetch    "You already called web_fetch 2 time(s) …"    x2
```

The veto blocks the *tool*, not the *cost*. Each refusal is returned to the
model as a message, and the model then makes **another model call** — a full
~14.4k round trip — to be told no again. Roughly **86k of the 130,809 was spent
being refused.**

The budget was 2 at the time (doubled to 4 on 2026-08-08). Doubling raises the
ceiling but does not change the shape: once the new budget is spent, the same
loop resumes.

**The veto is a soft refusal the model can ignore, and ignoring it is expensive.**
The mechanical fix is to make an exhausted tool *unavailable* rather than
*refused* — remove it from the schema the model is offered once its budget is
spent, so the call cannot be formed. That converts an asserted rule into a
mechanical one, which is design/12's own distinction.

## Finding 3 — `turns = 20` is dead config

R-24 requires `turns` to be reachable: `total_tokens >= turns × ~6124`
worst-case-per-call. That constant is why 8 → 20 was paired with 64k → 128k.

**Measured per-call here is ~14,400 — 2.4× the sizing constant.**

```
128,000 / 14,400 ≈ 8.9 cycles        observed: 9
```

The token cap binds at ~9 turns; turns 10–20 are unreachable. This is precisely
the trap the 2026-08-07 tuning was meant to escape, and it did not escape it —
the per-call constant was too small. Twenty reachable turns at measured rates
would need `total_tokens ≈ 288,000`.

Either raise `total_tokens` to match `turns`, or lower `turns` to ~9 so the two
numbers stop lying to each other. **Do not leave them as they are** — the current
pair asserts a capability that does not exist.

## Finding 4 — the cap counts prompt-cache reads at full weight

76.4% of that input was `cache_read_input_tokens`, billed at $0.028/1M against
$0.14/1M for fresh input — a fifth the rate.

Strands counts them at **full weight**. So of 130,809 charged against the cap,
only ~**32k was new work** (30,614 fresh input + 1,266 output). The budget
penalises the exact mechanism that makes the turn cheap.

Had the cap counted non-cached tokens, this turn would have sat at ~32k and
`turns = 20` would be comfortably reachable — Finding 3 dissolves.

This is a Strands-side accounting choice, not something Nora configures. It is
recorded because it explains *why* Finding 3's constant is so far off: the
re-sent fixed prompt is nearly free in money and full price in budget.

## Finding 5 — cost tracking was entirely dead

`model: "opencode-zen/deepseek-v4-flash"`, `internalModelId: None`,
`calculatedTotalCost: 0`.

Langfuse's 87 built-in model definitions contain no DeepSeek entry, so **every
generation Nora has ever produced has a recorded cost of $0.**

### 5.1 Real pricing

Verified at source — `opencode.ai/docs/zen`, which is who bills, **not**
DeepSeek direct (whose cache-hit rate is 10× lower and would understate cost):

| | per 1M | per token |
|---|---|---|
| input (cache miss) | $0.14 | 1.4e-7 |
| output | $0.28 | 2.8e-7 |
| cached read | $0.028 | 2.8e-8 |
| cached write | — | — |

### 5.2 Fixed

A model definition was created via the Langfuse public API:

```json
{ "modelName": "deepseek-v4-flash",
  "matchPattern": "(?i)^(.*/)?(deepseek-v4-flash)$",
  "unit": "TOKENS",
  "pricingTiers": [{ "name": "Standard", "isDefault": true, "priority": 0,
    "conditions": [],
    "prices": { "input": 1.4e-7, "output": 2.8e-7,
                "cache_read_input_tokens": 2.8e-8 } }] }
```

→ `id: cmsjecry3001nmy07pgxzwf1l`.

**Note for anyone repeating this:** flat `prices` is rejected on create
(`Must provide either flat prices … OR pricingTiers`). The cache tier only goes
in through `pricingTiers[].prices`, and the MCP `createModel` tool cannot express
it — only `inputPrice`/`outputPrice`. Using the MCP tool would have overstated
cost by **2.5×** by billing cache reads as fresh input.

Pattern verified against `opencode-zen/deepseek-v4-flash`,
`deepseek/deepseek-v4-flash` and the bare name; correctly rejects
`deepseek-v4-pro`.

### 5.3 What this turn actually cost

```
cache-aware      $0.007410
if uncached      $0.018491   (2.5x)
Langfuse showed  $0.000000
```

**Three-quarters of a cent.** The turn that blew a 128,000-token budget was
economically trivial. The budget is not a cost control; treating it as one will
mislead.

---

## 6. Verified vs not

| claim | status |
|---|---|
| trace sums to 130,809, one invocation | **verified** — 9 generations summed from the API |
| `finish_reason: limit_total_tokens`, `message: ""` | **verified** — trace output |
| 6 of 10 tool calls vetoed | **verified** — TOOL `statusMessage` |
| overshoot is one cycle, by design | **verified** — read `_check_limits` and its call site |
| per-call ~14,400 vs R-24's ~6,124 | **verified** — computed from the trace |
| pricing figures | **verified** — fetched from `opencode.ai/docs/zen` |
| model definition created with cache tier | **verified** — API returned the stored prices |
| **new traces will now carry cost** | **NOT verified** — needs Nora to run a turn |
| **historical traces** | **will stay $0** — Langfuse costs at ingestion; needs a backfill |
| how often this failure occurs | **unknown** — the counter resets on restart |

## 7. Recommended order

1. **Alarm on `nora_llm_stop_reason_total{reason=~"limit_.*"}`.** Everything else
   is guesswork until the frequency is known — and the counter resets on every
   restart, so this needs to reach something that persists.
2. **Never end a turn silently.** A `limit_*` stop with no delivered message
   should emit a fallback line. Silence is the R-5 failure.
3. **Withdraw exhausted tools from the schema** instead of vetoing calls to them
   (Finding 2). Biggest token saving, and turns an asserted rule into a
   mechanical one.
4. **Reconcile `turns` with `total_tokens`** (Finding 3) — raise one or lower the
   other, using ~14,400/call, not ~6,124.
5. **Trim the ~12k fixed prompt.** Cheap in money (it caches) but full price in
   budget, and it is the single largest per-cycle cost.
6. **Backfill historical Langfuse costs**, or accept that everything before
   2026-08-08 reads $0.

Items 1–3 are independent of the reminders/heartbeat work. Item 2 is
**load-bearing for it**: a tagged human notices silence; a reminder that dies
this way is invisible, because nobody was waiting for it.

## 8. Related

- `2026-08-08-reminders-heartbeat-design.md` §9.2 — records this as the budget
  constraint that design is built under.
