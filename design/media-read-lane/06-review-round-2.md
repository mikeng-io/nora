---
type: design
title: "media-read-lane — independent adversarial review, round 2"
description: "Independent read-only review of the media-read-lane bundle at 84f11664 (fresh reviewer, 4C axes). Two BLOCKING findings: the settled vision_timeout_seconds = 120 is INERT because derive_vision_timeout still caps it at 54 s, and the settled numbers do not compose — the four-video turn the budget was held for still cannot finish inside a 360 s loop, and now dies silently instead of degrading per-window. Five MAJOR and four MINOR below."
tags: [nora, media, review, adversarial, blocking]
timestamp: '2026-09-23'
edges:
  - {dst: 04-timeout-and-models, rel: reviews, provenance: asserted}
  - {dst: 05-decisions-and-index, rel: reviews, provenance: asserted}
---

# Independent adversarial review — round 2 (fresh reviewer, 4C axes)

**Reviewer:** independent sub-agent, read-only, no access to the earlier rounds'
reasoning. Its brief named the load-bearing claims and told it to attack, not
summarise. **Every finding below was then re-verified by the operator's agent
against the named artifact** — the verifications are marked ✅ where done.

> **Status: OPEN. These are recorded, not resolved.** B1 changes a *settled
> setting* and B2 changes a *settled setting*, so neither is the reviewer's to
> decide and neither was silently "fixed". They are escalated to the operator.

---

## BLOCKING

### B1. ✅ CONFIRMED — the settled `vision_timeout_seconds = 120` is INERT. The effective cap is 54 s.

**Verified directly** (`nora/infra/vision/client.py:210-211`):

```python
per_call = loop_timeout_seconds * VISION_LOOP_SHARE / max(1, budget)
return min(configured, per_call) if configured > 0 else per_call
```

Plug the settled numbers in: `min(120, 360 × 0.6 / 4)` = `min(120, 54)` = **54 s**.

**The bundle never removes or bypasses this function.** `03 §4`'s integration
table has no row for `client.py:193-211`; `05 §5` *mandates tests that pin it*
(`test_the_derived_vision_timeout_never_exceeds_the_loop`). And the repo's own
config comment states the semantics the bundle ignored — 40 was chosen because it
keeps the *configured* ceiling **above** the derived one so the derived number is
the true cap (`config.toml`, above `vision_timeout_seconds`).

**Consequence:** §3c's rationale ("must clear the measured tail, 120.8 s") fails by
construction. Against the bundle's own n=19 pool, **7/19 ≈ 37% of runs exceed 54 s**
(120.8, 68.7, 62.6, 62.0, 61.6, 58.0, 55.7) — recreating at 54 s exactly the
high-failure lane this bundle exists to remove.

**And §3c contradicts §5 directly:** §3c says "the fix is to stop deriving the
timeout from the item count at all", while §5 mandates tests that *pin the
derivation*. Both readings are broken — keep the derivation → 54 s cap, tail blows
through; delete it → the aggregate bound (`budget × derived < loop`) is lost, which
is the only invariant keeping vision calls under the loop.

**Fix (operator decision required):** decide the derivation's fate explicitly —
remove the `/budget` division, or remove the `min`, or replace the invariant with a
wave-based one (`invocations × ceil(windows/concurrency) × derived < loop`). Then
add the `client.py:193-211` row to `03 §4`'s surface table and rewrite the two
budget tests in `05 §5`.

### B2. ✅ CONFIRMED — the settled numbers do not compose, and the failure is now SILENT.

Per the bundle's own frame: **4 invocations × ceil(10/4)=3 waves × 120 s tail =
1,440 s against a 360 s loop.** Even one wave per call fails: `4 × 120.8 = 483 s >
360`. Nor is it only a tail problem — the bundle's own median product already
exceeds the loop: `3 waves × 41.6 s × 3 calls = 374 s > 360` (`05 §4.2`). The 360
looks like 240 × 1.5, not a derived clearance.

**The failure mode is worse than "doesn't fit".** Verified in
`nora/engagement/stages/invoke.py:171-179`: when the loop's `wait_for` fires, the
turn is abandoned wholesale — it logs `invocation_timeout`, increments
`nora_silent_failures_total`, sets `ok = False`, and returns a blank trace. **The
user gets nothing.**

So the settled envelope converts honest per-window degradation (`timeout → None →
_UNREAD_MARKER`, a *named coverage gap*) into **silent turn death** — which is
precisely the "unreadable post" outcome the operator rejected budget-lowering to
avoid («斬腳趾避沙蟲»), reintroduced one layer up with a strictly worse failure mode.

**No product `waves × tail × budget ≤ loop` exists for the settled settings.** The
bundle computes `waves × tail` for ONE call and never multiplies by the held budget.

**Fix (operator decision required):** derive the loop from that product, or bound
`invocations × waves`. Note this is the *same* unit error §3a identified — the
bundle caught it in `derive_vision_timeout` and then repeated it in its own loop
arithmetic.

---

## MAJOR

### M1. ✅ CONFIRMED — 120 fails the criterion option A was rejected by, on a false premise.

The adopted 120 sits **below** the observed max 120.8 with **zero margin** — the one
characterised tail observation is unhandled by definition. Meanwhile option A (150 s)
was struck down as "still below the 120.8 s tail plus compound" — but 150 > 120.8;
"compound" applies to the loop *product*, not a per-call ceiling. **A was rejected
for failing a test it passes; the adopted value fails the test A passed.**

The per-window failure itself is honest (timeout → None → `_UNREAD_MARKER` → named
coverage gap) — credit where due. But at the effective 54 s (B1), ~37% of windows
degrade.

### M2. ✅ CONFIRMED — the DEC-196 cross-reference answers a *different* question.

DEC-196's ceiling **queues** rather than refuses (tool-concurrency `02 §1`: "queues
the call; it runs when a slot frees"). Queueing reduces **concurrency**, not **total
wall-clock** — four queued 3-wave invocations still consume their sum inside the
*same* loop `wait_for`. So DEC-196 answers the fan-out / «10 個 concurrent» question
and does **nothing** for the latency-budget question §3c leans it into.

Compounding: `01 §3` explicitly hands an obligation forward — cross-clip queueing
"must be priced into §04's invocation budget" — and `04` contains **no queueing term
at all**. A dropped hand-off. Also DEC-196 is itself `proposed`/unlanded, so a
settled decision citing it as "answered" inherits its instability.

### M3. ✅ CONFIRMED — direct self-contradiction on whether parallel `tool_use` happens.

`_index.yaml:41` says **"ANSWERED as of 2026-09-23: they DO parallelise, and already
have"** (seven real batches, Langfuse). `00-overview.md:168` says **"Untested."**
Not cosmetic: whether `media_fetch` calls pile up in one turn is exactly what
multiplies B2's product.

### M4. ✅ CONFIRMED — the R-rule register still carries a twice-withdrawn number.

`05 §3` R-33: *"The 'own timeout' becomes **48 s** instead of 36 s."* Settled is 120
— or 54 effective (B1). The register DEC-195 will point at states the withdrawn v2
recommendation as current.

### M5. ✅ CONFIRMED — the persisted-data policy is raised, then not decided, while the overview claims scope completeness.

`02 §8` finds that already-comprehended media keeps its silent description
indefinitely and says it "needs a stated treatment". None is stated; the DEC-195 row
is silent on it; `00-overview` still says "no schema change, no new table, no
migration" as if the lane were fully specified. An open policy at merge time means
the headline benefit (audio) visibly **doesn't work on every existing clip** and
nothing records that acceptance. Rollback *is* covered (`02 §9`) — credit due.

---

## MINOR

- **m1.** `01 §3`'s correction (keep the two numbers independent) contradicts `04 §6`
  ("the concurrency default of 4 deliberately equals `media_fetch`'s budget so an
  operator sees one number meaning the same thing") — under §3c's own finding the
  units differ, so "the same thing" is the exact error `01 §3` rules out.
- **m2.** `05 §4` item 6 obligates the bundle to state `vision_max_tokens` 4096 is a
  **per-request** ceiling affecting images, audio, video *and* synthesis; `04 §6`
  never says so. Relatedly, raising the loop to 360 silently raises the
  *text-synthesis* call ceiling via the same shared derivation — a lane nobody decided on.
- **m3.** ✅ CONFIRMED — `02` **frontmatter** still asserts "9 of 11 stored clips
  carry an audio stream" as fact while `§1`'s correction marks it unreproducible
  (live probe: 8 audio / 156 without, of 164). **Frontmatter feeds indexes.** Fixed below.
- **m4.** ✅ CONFIRMED — `04 §3`'s withdrawn table still sits under the heading
  **"The safe settings"** with no strikethrough, unlike every other retained
  correction. A reader stopping at §3 takes 48 s as current advice. Fixed below.

## What the bundle gets right (reviewer's own words)

- **The §3a unit critique is substantively correct** — `budget × derived < loop`
  assumes one media item = one model call; with `gather` one invocation occupies
  `ceil(N/C)` waves. The diagnosis is right even though the prescribed cure (B1) is not.
- **Anchor discipline is real** — every `file:line` checked landed, including
  `tools.py:727`, `config.py:964`, `comprehension.py:97/:156`, `factory.py:69-74`,
  `preprocess.py:186-199/-an`, `client.py:84-88/:314-318/:399-402`, `config.toml:209/:994/:1003`,
  and both credential claims.
- **`02 §9`'s rollback** is a completeness item most bundles miss.
- **`03 §3`'s composition-built-client test obligation** pre-empts the right integration trap.
- **The n=19 amendment strengthens the tail against the bundle's own thesis** —
  adverse-to-self disclosure done properly.

## What the reviewer did not check

`comprehend.py:314-318` re-templating; `02 §8`'s anchors; `egress.py:45-46/:67`; the
DEC-161/DEC-114 quotes against `design/09-decision-index.md`; Langfuse traces; the
bench harness (statistics taken as given per brief).
