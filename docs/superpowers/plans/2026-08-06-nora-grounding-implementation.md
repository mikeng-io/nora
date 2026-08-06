---
kind: plan
title: "Nora — implementation plan: egress split through grounding"
description: "The ordered build sequence across design/14-18, with prerequisites, probes and what is deliberately deferred."
timestamp: '2026-08-06'
status: proposed
---

# Implementation plan

Baseline: master-equivalent at `be537f8` on `fix/persona-capability-audit`,
**1128 passed / 1 deselected**, four gates green, container healthy on the new
doctrine.

Every step below: probe observed failing first, four gates green, `git commit
-F`, no push. One agent at a time in this checkout — two concurrent writers
caused two commit-attribution failures today, and that is now a rule, not a
lesson.

## Deferred, with the reason

- **`ContextBudget` (design/18 §2).** The review argued it is a component where
  a constant would do, and that
  `nora_llm_stop_reason_total{reason="limit_total_tokens"}` is already live.
  **Read that counter after step 3 ships.** If it is ~0, the component is
  machinery for a hypothesis. Grounding ships with hard constants instead
  (design/14 §4).
- **Message handles `#N` + `read_thread` (design/18 §3).** No measured need;
  costs ~80–120 tokens on every call; `#\d+` in reply text would mute her on
  `#1` (`egress.py` documents deliberately not matching digit runs). The
  *snippet* half is kept — step 2.
- **Life360 (design/14 §5.2).** Needs the resolver's scope boundary first.
- **GIFs (design/15).** After multi-message, per its own §5.

## Step 1 — `scan_egress` split (prerequisite)

`scan_egress` becomes `scan_identifiers()` (numbers, ACIs, keys) and
`scan_reply()` (identifiers + model names). `Deliverer` uses the latter; every
outbound tool argument uses the former.

Why first: `_MODELNAME` matches `gemini|haiku|sonnet|opus|claude|grok`, and it
fails closed on the *whole reply*. Ship any outbound tool before this and
「搵首 haiku」 makes her mute.

Probe: a query containing `haiku` passes `scan_identifiers` and is refused by
`scan_reply`. Revert the split → red.

Also here: `recall`'s conclusion filter moves to `scan_identifiers` — it
currently withholds any memory naming an LLM, from a group of engineers.

## Step 2 — observability, and the ↳ snippet

**2a. Tools log that they ran.** No tool logs its own name on a success path
(DEC-28), which produced two confidently wrong conclusions today. Emit a
structured line per call and ensure `nora_room_lookups_total`-shaped counters
exist for every tool. Probe: a turn calling `lookup_person` produces a line
naming it.

**2b. `↳` disambiguation via snippet.** `_parent_of` already resolves the exact
parent row and `_transcript_block` throws it away. Render
`[Tyler ↳ Grant「去邊食」]`, truncated 12–16 chars. One f-string. No handle
map, no new rule. (design/18 §3's genuinely cheap half.)

**2c. Per-turn message histogram** — prerequisite for step 5's observability.

## Step 3 — `transit` (the first grounding tool)

`transit(route)` over `rt.data.gov.hk`. Free, keyless, verified live (K73: 27
stops, next bus 112s), bounded typed JSON, no injection surface, no egress
question.

- `NATIVE_TOOLS` entry: budget, `when`, and **time-shiftable: false** (§5.1).
- Three states: `ok` / `nothing_found` / `unavailable`, `{tool, status}` counter.
- Typed response is **not** wrapped `untrusted="true"` (design/14 §2.1).
- Operator-visible payoff: 「K73 幾時到」 works.

R-27 registered + `RULE_TESTS`.

**After this ships: read the stop-reason counter.** It decides `ContextBudget`.

## Step 4 — `ToolSpec.terminal` + `send_reply` budget reconciliation

**SHIPPED 2026-08-06 (DEC-32).** Two prerequisites for multi-message that the
design docs did not declare.

**4a.** `ToolSpec` gains `terminal: bool`; `ReplyGuard` reads it from the
registry instead of hardcoding a verb class. design/15 §1 proposed hardcoding
it *in the guard* — that is the pre-DEC-10 `BUDGETS` mistake under a new name.
Terminality is a property of the tool.

**4b.** `NATIVE_TOOL_BUDGETS["send_reply"] = 1` and R-7's veto runs **before**
R-2's latch, so relaxing R-2 alone leaves multi-message silently dead. The cap
must be ONE number — the budget table or config, never both.

Probe for 4b: with R-2 relaxed but the budget still 1, a second `send_reply` is
refused by R-7. Red before the fix.

### Where the message cap lives — decided, for step 5

**`NATIVE_TOOL_BUDGETS["send_reply"]` IS the cap. There is no second number.**

design/16 §2.2 asks for `llm.limits.messages` "(new)". Read literally — a
config key the guard checks *beside* the budget — that is two numbers for one
question, and the R-7-before-R-2 ordering guarantees the smaller one wins
silently: a `messages = 4` in `config.toml` sitting above a budget of 1 caps
her at one message and reports the refusal as R-2. Two numbers whose
disagreement is invisible is precisely the drift `nora/agent/registry.py`
exists to prevent (DEC-10: "the number `ReplyGuard` enforces and the number a
tool is declared to cost can never drift apart").

So step 5 raises the number in `NATIVE_TOOLS["send_reply"].budget` and revises
`Deliverer`'s latch to match — both, or multi-message stays dead in one of the
two ways this step made testable.

**If the cap must become operator-tunable**, config supplies it *to the
registry* — `ToolRegistry(message_cap=cfg.llm.limits.messages)` overriding that
one table entry, with the table's value as the default — so `budgets()` still
hands `ReplyGuard` exactly one number and `ToolRegistry.specs()` declares that
same number to the model. Never a `limits.messages` read directly by the guard
or by `Deliverer`. **Not implemented in step 4**: there is no measured need for
a tunable cap yet, and a config key with no reader is how the last one rotted.

## Step 5 — per-tag `OpenAIModel`, then multi-message

**5a.** Construct the model per tag over the process-wide `AsyncOpenAI` client.
One shared instance today (`composition.py`) means flipping `tool_choice` for
one tag flips it for another tag's first call — measured to send nothing 5/6.
Probe: two concurrent tags cannot observe each other's `tool_choice`.

**5b.** `required` until the first reply, then `auto`. The flip lives in
`ReplyGuard` via `event.agent.model.update_config` — reachable, contrary to
design/16's corrected §4.

**5c. Partial-delivery observable.** A turn truncated after message 2 of 4
reports `delivered=True`, shows ✅ and feeds Honcho. R-23 only relabels
*silent* turns. Count it, or the feature's main failure is invisible.

**5d. One Honcho feed per tag**, messages joined — not one per message, which
would feed the tagger's question four times and teach Honcho a conversation
that did not happen. Audit rows stay per-message.

**5e. The cap is `NATIVE_TOOLS["send_reply"].budget`** — see step 4's decision
above. Raising it is not optional bookkeeping: leave it at 1 and R-7 refuses
the second message before R-2 is consulted, so every other part of 5 can be
correct and the feature still ship dead
(`tests/test_guards_replyguard.py::test_relaxing_r_2_alone_does_not_buy_a_second_message`).

Ships inert: neutral doctrine gives 1 message 6/6.

## Step 6 — `route` + the place resolver

`route(origin, destination, mode, arrive_by)` over Google. `arrive_by` is the
point.

The resolver is the hard part and is a **scope boundary**, not a wrapper: it
sits beside `Room`, and a person's name or live position is hard-blocked from
leaving in an argument (design/14 §2.2, R-28).

Needs: a Google key with billing, and the budget guard made real (a paid call
must not fire for 「早晨」).

## Step 7 — `search` + `fetch`

Last, per design/14 §6. `fetch` accepts only a URL from a `search` in the same
turn — everything on this host listens on localhost, so a model-chosen URL
makes Firecrawl an SSRF pivot. Probe: `http://127.0.0.1:18080/api/v1/rpc` is
refused.

Hard intake constants; truncation stated in the return value.

## Step 8 — the wayfinding skill

Only once there is something to bundle.

## Cross-cutting

- Merge `fix/persona-capability-audit` → master before step 1. Master is 6
  commits behind and the divergence is a live hazard.
- Rebuild + restart after any config or doctrine change — both load once.
- Never send anything to the Signal group from a probe.
