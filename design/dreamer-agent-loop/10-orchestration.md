---
type: design
title: "Orchestration — Cadence, Backoff, Budgets, Concurrency"
description: "A single chat message resets the backoff. The timeout wraps only the model call. Four of five budgets sit at the bare minimum the turn-budget rule tolerates. None of this survives a real multi-step loop."
tags: [nora, dreamer, scheduling, backoff, budgets, concurrency, r-24]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
  - {dst: 02-instrument-blindness, rel: depends_on, provenance: asserted}
---

# Orchestration

## 1. What is correct today, and should survive

Worth stating first, because most of the scheduling is right and a revision
should not disturb it:

- **Per-adaptor failure isolation is implemented**, and the backoff key moved
  with the isolation boundary — the exact trap the keystone flagged.
- **Adaptors declare their own key kind and the runtime schedules per adaptor.**
- **Watermark seeding is conditional on absence**, so it cannot clobber a live
  cursor, and it defuses the rename trap where a new adaptor name re-reads
  every room from the beginning.
- **The tick never awaits its tasks.** A long run does not delay the next tick;
  it is skipped by the in-flight lock. Isolation holds.
- **Backoff on `unavailable` was fixed** and is deployed and demonstrably
  working — the earlier claim that it fires only on an exception is **stale**
  and should be corrected wherever it is still repeated, which is currently
  three places in config and docstrings.

## 2. The backoff is destroyed by a single chat message

The dispatcher returns nothing when the fire conditions do not fire. The
failure counter treats **any** non-`unavailable` result — including that
"didn't fire" nothing — as success, and clears the count.

In an active room the quiet clock resets whenever somebody speaks, so the
adaptor does not fire, so **the backoff ladder resets to zero**.

Observed three times in three hours ([01-live-state.md](01-live-state.md) §4):
the ladder reached 960 s twice and 240 s once, and each time a single incoming
message returned it to zero. The one-hour ceiling is unreachable in practice.

Two smaller compounding facts:

- **Backoff does not survive a restart** — the state is per-loop-invocation,
  and the log carries 43 process starts.
- **Neither backoff nor an in-flight skip emits a log line.** They are
  metric-only, and the metrics are in-process
  ([02-instrument-blindness.md](02-instrument-blindness.md) §3).

**Required property:** "did not fire" is a third outcome and must not be folded
into success. A failing unit in a chatty room is precisely the case backoff
exists for.

## 3. The timeout wraps the model call, not the adaptor

The wall clock covers the model invocation only. The adaptor's own input
rendering, item acceptance and commit — plus the watermark queries around them
— are **outside any timeout**.

A hung commit or a hung store read leaves the unit marked in-flight
**forever**. It is then skipped on every subsequent tick, silently, and the
only tell is an in-process counter that no longer exists after a restart.

This is the exact failure the budget's own documentation warns about — *"a hung
Dreamer call is QUIET"* — reintroduced one layer out from where the fix was
applied.

**Required property:** the wall clock covers the whole run, or every path
outside it has its own bound. Preferably the former.

## 4. Budget arithmetic

The turn-budget rule (R-24) requires the loop's output budget to exceed
`turns × per-call ceiling`, or the turn count is dead config. **It is enforced
by a test that reads only the tag path's table.** Nothing checks it for the
Dreamer.

Against a per-call ceiling of 2,000:

| adaptor | turns | output budget | reachable calls | turns actually usable |
|---|---|---|---|---|
| room language | 6 | 4,000 | 2 | **2 of 6** |
| room exchange | 6 | 4,000 | 2 | **2 of 6** |
| fact extraction | 2 | 4,000 | 2 | 2 of 2 — **zero slack** |
| fact adjudication | 10 | 8,000 | 4 | **4 of 10** |
| unconfigured default | 6 | 4,000 | 2 | **2 of 6** |

Four of five declare turn counts they cannot afford. All five sit at exactly
**2.0×** the per-call ceiling — the bare minimum the rule tolerates — while the
tag path runs at 6.0×. And the factory's own documentation records that this
model emits **99.8%** of whatever per-call ceiling it is given, which makes "two
reachable calls" mean *one call plus a stub*.

**Two things are wrong here, and they are different.** The arithmetic is one.
The other is that the Dreamer inherits the **tag tier's** per-call ceiling and
reasoning effort — numbers sized for a chat reply, not for a research loop
whose deliberation is much larger. Neither has a Dreamer-specific knob.

**Unresolved and blocking any sizing:** the observed generation was 4,096
output tokens, which is neither the configured per-call ceiling nor a multiple
of it, and the loop ran one cycle. Either the gateway is ignoring the ceiling
and applying its own default, or the trace measures something else. If the
ceiling is being ignored, the loop's output budget is the *only* cap and a
single call spends it — exactly what R-24 describes. **Probe before sizing**
([13-open-items.md](13-open-items.md)).

## 5. What a real loop costs

The keystone's own description — search, read, notice the source does not
address the question, search differently, cross-check — is at minimum five
model calls, and eight to fifteen with several claims and any cross-checking.
One model call is one turn; a tool call and the reasoning about its result are
two separate turns.

Token shape: a search result set is roughly two thousand tokens and stays
resident for every subsequent call; a fetched page adds several hundred more,
likewise resident. Across eight turns with a few searches and fetches the
cumulative input runs to tens of thousands of tokens.

So: the total-token budget is roughly adequate; **the output budget is not**,
and the turn counts are aspirational.

## 6. Where the current cadence breaks

1. **The output budget is the hard stop, not turns.** Every declared turn count
   above four is currently unreachable.
2. **The extraction timeout is under one tick; the adjudication timeout is
   exactly one tick.** A genuine loop with several network tool calls will
   routinely exceed it, and on overrun everything the loop learned is discarded
   — there is no partial-progress path.
3. **A run longer than one tick silently halves that unit's throughput**, with
   no log line.
4. **The watermark-free adaptor is throttled by nothing but its own blank-input
   check.** The moment the extractor writes one candidate, adjudication fires
   **every tick, on every workspace, until the queue drains** — a full research
   loop each time.
5. **Failure is a ratchet.** A held watermark eventually pushes the lag past
   its threshold, at which point the adaptor fires on *every* tick regardless
   of quiet — and the only brake is the backoff a single message resets (§2).
6. **A quiet-clock asymmetry**: Nora's own reply resets the quiet timer but
   does not count toward the lag. Her speaking suppresses a fire.

## 7. Concurrency

There is **no global cap** — no semaphore, no pool, no rate limiter. The only
bounds are the per-unit in-flight lock, the per-unit backoff, and the fixed
population of scopes and workspaces.

Today that is up to ten concurrent tasks per tick. **After the migration it is
eighteen**, each potentially issuing several network tool calls, all sharing
one client.

**Required property:** a global concurrency bound, chosen rather than
inherited, before the migration lands. The existing per-unit isolation is
correct and is not a substitute for one.

## 8. Cadence is the open design question

Everything above is a bound. The unasked question is whether a 300-second tick
over every scope is the right *shape* for a research loop at all.

Extraction is watermark-driven and cheap; that fits a clock. Adjudication is
expensive, unbounded in duration, and its trigger is "there is something in the
queue" — which is a work queue, not a clock. Synthesis, if it lands, wants to
run rarely.

This bundle does not settle it. It records that the three have different
natural cadences, that one tick currently serves all of them, and that the
budget and concurrency work above is what makes any of the answers safe.
