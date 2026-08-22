---
type: contract
title: "The Config Surface — Cadence Is Named After the Wrong Tier"
description: "Node 00 promised a config section and never wrote one. Per-adaptor budgets already exist and are wired; cadence does not, and every adaptor's schedule is literally configured under [language]. What the operator surface owes each seam."
tags: [nora, dreamer, config, cadence, trigger, transport, operator]
timestamp: '2026-08-23'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 06-trigger, rel: depends_on, provenance: asserted}
  - {dst: 05-transport, rel: depends_on, provenance: asserted}
---

# The Config Surface

## 1. The promise that was made and not kept

[00-overview.md](00-overview.md) §4 states the cost of separating the seams:

> The cost of separating the seams now is **a config section**, a port method,
> and a node that says what a turn is.

The port method exists. The node that says what a turn is exists
([07-loop.md](07-loop.md)). **The config section was never written**, and D1 and
D2 both bottom out in it — a transport nobody can set and a trigger nobody can
declare are decisions in prose only.

## 2. What already exists — including the part this bundle got wrong

**Per-adaptor budgets exist and are wired.** `[dreamer.budgets.<adaptor>]` blocks
ship for all four adaptors, each carrying `turns`, `total_tokens`,
`output_tokens` and `timeout_seconds`, resolved through `budget_for` and passed
into the call. A partial block fills its missing fields from defaults rather than
zeroing them, and the reason is written down: `output_tokens = 0` is not "no
limit", it is a subsystem that silently never produces anything.

**This node corrects an earlier claim in this bundle's own discussion that the
Dreamer had no transport knobs.** It has four, per adaptor. What it does not have
is `reasoning_effort`, which is absent from the budget model entirely — so the
one transport dimension [05-transport.md](05-transport.md) argues hardest about
is the one with no key.

## 3. The finding: every adaptor's cadence is named after the language tier

`dream_loop` carries every adaptor, including the fact tier's. Its schedule is
parameterised entirely from one section:

```
tick_seconds   = cfg.language.tick_seconds
safety_lag     = cfg.language.safety_lag
quiet_seconds  = cfg.language.quiet_seconds
max_age_seconds= cfg.language.max_age_seconds
batch_limit    = cfg.language.batch_limit
```

**So the knob that governs the fact adaptor is called `[language].tick_seconds`.**
An operator who wants the fact tier to run less often edits the language tier's
config, and there is no key anywhere that could express anything else.

`[dreamer]` has **no cadence keys at all**. It borrows them.

This is [06-trigger.md](06-trigger.md)'s finding made concrete: *"one tick
currently serves all three"* is not a scheduling accident to be tidied later — it
is the shape of the config, and no trigger can be declared until the surface
exists to declare it in.

### 3.1 The asymmetry, laid out

| section | knobs it has |
|---|---|
| `[language]` | **7** — cadence, batching, model, timeout, plus render caps |
| `[analytics]` | a full cadence set of its own — `tick_seconds`, `safety_lag`, `settled_seconds`, `max_age_seconds`, `batch_limit`, backfill bounds |
| `[facts]` | **3**, and **none of them is cadence** |
| `[notes]` | `enabled`, and nothing else |
| `[dreamer]` | corpus keys, `enabled`, `model`, and the per-adaptor budget map |

The two tiers with full cadence control are the two that **predate the Dreamer
runtime**. Every adaptor built after it inherited a schedule it cannot name.
That is the inheritance pattern [04-designed-vs-shipped.md](04-designed-vs-shipped.md)
catalogues, appearing in the operator surface rather than in the tool list.

## 4. On/off is two layers, and the semantics were never written down

Both layers exist and they answer different questions:

| layer | question | effect |
|---|---|---|
| `[dreamer].enabled` | *does the research loop run at all?* | off, no `DreamerRuntime` is constructed |
| `[<tier>].enabled` | *is this tier one of the things it runs?* | off, that tier's adaptors are not registered |

The composition code states the intent exactly: *"an operator turning on the
research loop has not thereby asked for a fact store to start filling."* **Two
questions, deliberately separate — and correct.**

What was missing is that nobody wrote it down as a contract, which is how
`[facts]` came to carry a comment saying *"Stays off with this first
deployment"* above a value reading `true` for weeks. The tier was dormant only
because the layer above it was off. Re-enabling the runtime would have started
the fact tier in the same motion, defeating
[09-measurement.md](09-measurement.md) §4's turn-it-on-for-**one**-scope
acceptance plan.

**The rule, stated so the next tier inherits it:**

> Each layer is independently off by default, and **neither implies the other**.
> A tier's flag is a statement about that tier only; the runtime's flag is a
> statement about the loop. A comment that describes an intent the value does not
> implement is a defect, not documentation.

### 4.1 The granularity is wrong by one level

`[facts].enabled` gates **two adaptors** — extraction and adjudication — with one
flag. They have different budgets (deliberately: 2 turns versus 10, 20k versus
120k tokens), different failure modes, and different cadences once §5 lands. The
extractor is cursor-shaped and the adjudicator is a work queue
([16-open-items.md](16-open-items.md) §4).

**A flag whose two halves want different schedules is one flag too few.** The
budget map already keys on adaptor name; the enable flag should too.

## 5. What the surface owes each seam

Stated as obligations, not as a key list — the names are a build detail, the
obligations are not.

| seam | the operator must be able to say |
|---|---|
| **Trigger** ([06](06-trigger.md)) | **per adaptor**: how often it may run at most, and the **floor** between passes. D2's floor is currently inexpressible |
| **Transport** ([05](05-transport.md)) | per adaptor: the four budget fields **plus `reasoning_effort`**, which today has no key |
| **Loop** ([07](07-loop.md)) | already served by `turns` |
| **State** ([08](08-state.md)) | which tools an adaptor is granted. Presently implicit in code |
| **concurrency** | how many adaptor runs may be in flight at once. **No key, and no bound in the loop** — [06](06-trigger.md) §5 notes a bound is not expressible until triggers are declared, and this is why |

**The shape is already established by the budget map**: a per-adaptor block,
keyed on adaptor name, with every field defaulted and a partial block filling
from defaults. Cadence and effort belong in that same block rather than in a
parallel mechanism — one place an operator looks per adaptor, not two.

That also fixes §3 without a migration: an adaptor with no cadence block keeps
today's inherited behaviour, and naming one overrides it.

## 6. What this node does not decide

- **The numbers.** Every value waits on [16-open-items.md](16-open-items.md) §1's
  transport measurement, for [09-measurement.md](09-measurement.md) §6's reason:
  the observed generation exceeded every cap Nora configures, so no number
  currently in the file was in force.
- **Key names.** Build detail; tests arbitrate.
- **Whether `[language]`'s cadence keys are renamed or kept as the fallback.**
  Keeping them costs a confusing name; renaming them is a breaking config change
  for a running deployment. An operator decision, and cheap either way while the
  Dreamer is off.

## Status / Checkpoint

2026-08-23 — written after the operator asked whether config needed per-adaptor
cadence and on/off detail. It answers yes, and **corrects this bundle**: the
per-adaptor budget blocks it implied were missing already exist and are wired.
The real gaps are cadence, `reasoning_effort`, a concurrency bound, and enable
granularity — plus the finding that `dream_loop`'s schedule for every adaptor is
read from `[language]`.

`[dreamer] enabled = false`; `[facts]`, `[language]` off as of 2026-08-23.
