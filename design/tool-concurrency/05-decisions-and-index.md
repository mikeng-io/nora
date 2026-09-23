---
type: design
title: "Decisions and index — DEC-196, and the collision on the media lane's number"
description: "The decision: one shared, inheritable concurrency ceiling beneath the existing per-tool budget. Plus a delivery obligation to amend media-read-lane's deferred list, and a DEC-195 collision — RESOLVED 2026-09-23 before either PR merged."
tags: [nora, decisions, dec-196, concurrency, governance, delivery]
timestamp: '2026-09-23'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# Decisions and index

## 1. DEC numbering — checked, not counted

`design/09-decision-index.md` on `origin/master` currently ends at **DEC-195**
(`93c90f4f`). Verified three ways before claiming anything:

```bash
$ git show origin/master:design/09-decision-index.md | grep -c "DEC-196"
0
$ git grep -l "DEC-196" origin/master        # repo-wide
# (no output)
```

**This bundle claims DEC-196.**

> ### ✅ RESOLVED 2026-09-23 — the collision, and how it closed
>
> **`design/media-read-lane/` claims DEC-195 — and `origin/master` now also has a
> different DEC-195.**
>
> - `origin/master` DEC-195 = *"A reply's quote pulls the transcript window back to
>   reach its parent…"*, added by `722d56be` (*fix(threading): a reply's quote pulls
>   the window back…*).
> - `design/media-read-lane/` DEC-195 = *"The media read lane reads its windows
>   concurrently, keeps the video…"*, claimed in `48c15700`.
>
> **Checked, and this is not the media bundle's error:** the media worktree was cut
> from `origin/master` at `e3a646b4`, and `722d56be` is **not** an ancestor of that
> cut (`git merge-base --is-ancestor e3a646b4 722d56be` → the reverse holds). The
> number was genuinely free when it was claimed; master allocated it afterwards.
> The media branch is **3 commits behind** `origin/master`.
>
> **Consequence, and its resolution:** `design/media-read-lane/` renumbered to
> **DEC-197**, and its internal references moved with it — `05-decisions-and-index.md`
> §1 and the two `DEPRECATED IN PART 2026-09-22 — see DEC-195` prefixes on
> DEC-161 / DEC-114(3) kept as history where they describe the collision itself.
> That renumber (`df6aeb8c`, cherry-picked as `8e32eec1`) landed inside **PR #403**,
> which merged as `0a79b0ba`. Three `DEC-195` references remain, all of them inside
> this paragraph's subject matter — the collision being described — and none of them
> a claim.
>
> **Verified on the merged master:** `design/09-decision-index.md` still ends at
> DEC-195, and `DEC-196` is free (`grep -c "DEC-196"` → 0). **This bundle claimed
> DEC-196 correctly and needs no renumber of its own.**

## 2. The decision

**DEC-196 — Tool execution is bounded by ONE shared, inheritable concurrency
ceiling, beneath the existing per-tool budget ceiling.**

Nora's agent loop runs on Strands, whose default `ConcurrentToolExecutor` starts one
`asyncio.create_task` per `tool_use` block with no bound of any kind
(`concurrent.py:48-58`; no semaphore, no `max_concurrent`, no queue limit in that
file). Nora never overrides the default — `grep -n "tool_executor" nora/agent/factory.py`
returns nothing, so the default governs every tag turn (`factory.py:402`) and every
dream run (`factory.py:496`).

**This is live, not theoretical.** Read directly from the Langfuse ClickHouse store
on 2026-09-23 (51,452 observations, `2026-07-12 14:26` → `2026-09-22 19:30`,
`type='TOOL'` spans grouped by `parent_observation_id`): **2,622 of 9,998 turns
(26.2%) emit more than one tool call; 159 batches exceeded four; 16 exceeded eight;

> ⚠️ **Denominator (2026-09-23):** "9,998 turns" means 9,998 **tool-emitting model
> generations**, not all conversation turns — the bucket rows sum to 9,998. Also, the
> batch count is **method-dependent**: it clusters TOOL spans by a **100 ms** start-time
> window, and a different threshold yields different bucket counts — and fast
> *sequential* tools (<100 ms each, e.g. the 5 ms wall-time batches) cluster
> identically to concurrent ones. Treat the distribution as an upper bound on
> fan-out, not an exact histogram. (06-review-round-1 §m1.)
the maximum was 76 tool calls emitted within 104 ms** (parent
`cc333f839bcf8ccc`, all 76 starting within 104 ms of each other).

> **⚠️ CORRECTED 2026-09-23** — the first version ended “whose children span
> 128,776 ms end-to-end — **they genuinely ran together**”. That inference was
> retracted after independent audit: the widest child (128,730 ms) **IS** the span
> (99.96% of it), so the span is one long call's duration and proves nothing about
> overlap. `01 §2` carries the correction; **this decision record was still
> carrying the retracted evidence** (flagged by 06-review-round-1 §M1). The
> concurrency conclusion stands on the **104 ms start-clustering** — impossible
> for a sequential executor — plus ≥8 children overlapping at 40–52 s each.

`nora/agent/call_ledger.py` already closes the **count** half of this exposure: it
reserves a call in-flight so a parallel batch cannot all clear the pre-batch budget
check, keyed on the SDK's `toolUseId` after a 2026-08-21 review found a vetoed
sibling could otherwise free another call's reservation. Its docstring records the
incident that motivated it — nine `place_info` calls against a budget of four, 148k
input tokens into a 128k ceiling, ending `limit_total_tokens` with an **empty
message**. **Independently reproduced against the traces for this bundle** (trace
`078eb948c214ffd873053657c0281e9c`, 2026-08-13: nine spans in two clusters, six
within 3 ms and three within 2 ms, `chat` generations summing 147,972 input tokens).

**But `CallLedger` bounds how many calls a tool may MAKE. Nothing bounds how many
RUN AT ONCE.** The decision: add that bound as one `asyncio.Semaphore` held by a
custom `ToolExecutor` that **wraps** the library's concurrent one, injected at both
construction sites, with **one limit shared across every tool** and **inherited by
sub-agents** via a contextvar (the mechanism `nora/obs/tracing.py:288` already
documents as working across `asyncio.create_task`).

Operator rationale, verbatim: «我冇可能假設個 budget 之後 raise 到 10，佢就同我 10
個 concurrent 一齊 run 㗎？» — the ceiling must exist **before** the budget rises,
because the budget is not the thing that would stop the fan-out. And
«呢個唔單止係呢個要解決嘅問題，其他 tools 都需要解決» — hence one limit for all
tools, not a media-specific key. Asked for as **shareable** (one limit, all tools),
**inheritable** (a child uses the parent's, does not restate it), and an
**independent bundle**.

**Designed, not built.** No production code changed; the evidence is read-only
against the live store and `origin/master`.

## 3. Delivery obligations

1. **Inject at ALL FOUR `Agent(...)` construction sites, not the two this list first
   named** — `factory.py:402`, `factory.py:496`, and the two the review found
   (`06-review-round-1 §M6`; ⚠️ **`nora/compute/smith/agent.py:223` builds seven real
   tools with no executor at all and is UNRESOLVED — it must be ruled on before this
   anchors an obligation**). Missing any leaf leaves a whole path under the unbounded
   default. (⚠️ The adaptor that motivated this, `dreamer:fact-adjudication`, is
   DELETED — see `02 §5`; a surviving adaptor must be identified and re-measured
   before this anchors an obligation. Historical observation, 16 calls, reaching
   batches of 16.)
2. ✅ **DONE 2026-09-23 (`0a79b0ba`).** **Amend `design/media-read-lane/`'s deferred
   list.** It carried *"Concurrent `media_fetch` tool calls inside one agent turn
   (whether Strands parallelises multiple tool_use blocks is UNTESTED)"*. **This
   bundle answered that question** — they do parallelise; here is the ceiling. The
   line now reads *"ANSWERED as of 2026-09-23: they DO parallelise, and already
   have"* and names `design/tool-concurrency/ (DEC-196)` as the ceiling, with this
   bundle's own caveat that DEC-196 **queues** rather than refuses.
3. ✅ **DONE 2026-09-23 (`0a79b0ba`).** **Renumber `design/media-read-lane/`'s
   DEC-195 → DEC-197** (§1).
4. **A config key in the template**, with the comment §2 of `02-the-ceiling.md`
   specifies — the repo's config-key discipline requires every key to appear in the
   template.
5. **The tests in `04` §2 A–E**, of which A (the bound is provably binding, not
   merely present) and D (inheritance) are the load-bearing ones.

## 3b. ⚠️ The invariant this ceiling creates with the media lane (ADDED 2026-09-23)

This ceiling does not exist in isolation. **Because it QUEUES rather than refuses**
(`02 §1`: "queues the call; it runs when a slot frees"), it converts simultaneity
into serialization — so once a turn requests more tool calls than the ceiling
admits, the wall clock those calls consume stops being `max(per_call)` and becomes
`ceil(n / ceiling) × per_call`.

That matters because the vision per-call timeout is a large number. Measured
against the settled settings from `design/media-read-lane/` (`vision_timeout`
120 s, loop 360 s, `media_fetch` budget 4):

| `media_fetch` budget | this ceiling | waves | wall clock | vs the 360 s loop |
|---|---|---|---|---|
| 4 | 4 (equal) | 1 | 120 s | **fits** |
| 10 | 4 | 3 | **360 s** | exceeds (boundary) |
| 10 | 2 | 5 | **600 s** | **exceeds — silent turn death** |

**The row the operator actually asked about is the middle one.** «budget 升到 10，
佢就同我 10 個 concurrent 一齊 run 吖？» — this ceiling stops the 10 running at once,
correctly. But it stops them by **making 6 of them wait**, and the wait is what
pushes the turn past the loop. When the loop's `wait_for` fires,
`nora/engagement/stages/invoke.py:171-179` abandons the turn whole — log,
`nora_silent_failures_total`, blank trace, **the user gets nothing**.

**The invariant:**

```
ceil(media_fetch_budget / tool_concurrency) × vision_timeout < [llm].timeout_seconds
```

Today (`4 / 4 = 1`, `1 × 120 < 360`) it holds with 240 s of room. **Nothing in the
repo asserts it**, so a future budget raise or a ceiling change fails **silently at
runtime** instead of at startup. This is the same class of guard
`derive_vision_timeout`'s own docstring argues for — an invariant "nobody can
violate if it is never written down twice" — and this one is currently written down
nowhere.

**Recommended:** assert it as a config-validation test spanning **both** keys
(`[llm.budgets].media_fetch` and this ceiling), so an operator raising the budget
gets a startup failure naming the two numbers rather than losing turns quietly.
Cross-referenced from `design/media-read-lane/07-timeout-derivation.md` §6, which
records the same finding from the media side.

**Status:** recommended, not yet decided. The alternative is to accept the residual
risk explicitly — which is a decision, and must be recorded as one rather than
left implicit.

## 4. Deferred

- **The value above 4.** Needs traffic under the ceiling; a saturation counter
  (`04` §3) is the instrument, and the raise is a config change — which is exactly
  the operator's stated forward path.
- **Per-agent limits** (a dream run bound differently from a tag turn).
- **A single global pool shared between parent and child** (`03` §6 gives the
  tradeoff: a background job could delay a live reply).
- **Prompt/doctrine changes** that encourage or discourage batching (`04` §4).
- **Whether `media_fetch` should keep budget 4** — that is
  `design/media-read-lane/`'s, and the operator has settled it for now
  («暫時呢一個 setting 先啦»).

## 5. Index

| file | what it holds |
|---|---|
| `00-overview.md` | Why / what it is and is not / out of scope / reading order / open questions |
| `01-the-hole-in-the-executor.md` | The unbounded fan-out; Langfuse evidence; the count/concurrency distinction; why it is not a media problem |
| `02-the-ceiling.md` | The wrapping executor; semaphore vs chunking; the limit's value; the two composing ceilings; injection sites |
| `03-sharing-and-inheritance.md` | Shared across tools, inherited by sub-agents; the contextvar mechanism and its repo precedent; separate pools, not one global pool |
| `04-measurement-and-tests.md` | Tests A–E; observability; what is deliberately not measured; residual risk |
| `05-decisions-and-index.md` | This file — DEC-196, the collision, delivery obligations, deferred list |
