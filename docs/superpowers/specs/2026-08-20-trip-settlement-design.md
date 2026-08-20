---
kind: design
title: "Trip settlement — fixing Tier 5's trip fragmentation"
description: "extract_trips finalizes a trip the instant a batch ends, even mid-drive. This design gives a trip's trailing edge the same quiet-boundary protection segment_stays already has for a stay's arrival, using Life360's own is_driving signal plus a settled_seconds fallback."
tags: [nora, design, presence, analytics, mobility, trips]
timestamp: '2026-08-20'
status: proposed
---

# Trip settlement — fixing Tier 5's trip fragmentation

## 0. The problem, with live evidence

Tier 5 (design/27 §4, DEC-122) shipped and deployed 2026-08-20. Within the
first 20 minutes of production data, it fragmented a real, continuous drive
into six separate `presence_trips` rows.

Raw fixes for `aaron_novak`, 18:10–18:29 HKT, pulled directly from
`presence_fixes`: a single continuous highway drive — Luen On Street → Ma
Sik Road → Fbvuxrz Highway → Castle Peak Road (Kwu Tung) → San Tin Highway
→ Castle Peak Road (Tam Mi) → Tsing Long Highway → Yuen Long Highway —
fixes arriving every 5–45 seconds, movement continuous throughout. Tier 5
extracted this as:

| started | ended | distance | speed |
|---|---|---|---|
| 18:12 | 18:12 | 0.02 km | 4.0 km/h |
| 18:17 | 18:18 | 1.52 km | 96.1 km/h |
| 18:18 | 18:19 | 1.79 km | 92.2 km/h |
| 18:24 | 18:25 | 1.37 km | 93.4 km/h |
| 18:26 | 18:27 | 1.33 km | 106.7 km/h |
| 18:28 | 18:28 | 0.83 km | 83.0 km/h |

Six fragments of one ~20-minute, 15+ km drive. The 18:12–18:12 row isn't a
trip at all — its 5 fixes sit within 22 m of each other, a brief stop at a
light or junction caught mid-batch.

This also actively defeats Tier 5's own corridor-accumulation design
(DEC-122): six sub-2km fragments of one real route will never repeat as a
matching sequence, so corridors cannot form from fragmented input no matter
how many times the same drive happens.

## 1. Root cause

Confirmed by direct code investigation, not assumed from the general shape
of the earlier release-fragment disclosure.

`nora/analytics/__main__.py`'s `_tick` fires roughly every `tick_seconds`
(~120s) — its own docstring claims to evaluate three fire conditions
(`safety_lag`, `settled_seconds`, `max_age_seconds`, per DEC-115), but only
`max_age_seconds` is implemented; the other two don't exist in the code.
**This is real, but it is not the cause of the fragmentation** — even a
correctly-gated `_tick` would still slice a long continuous drive into
per-tick pieces, because the actual gap is one layer down.

`nora/domain/mobility.py`'s `extract_trips` protects exactly one boundary:
the trip-to-stay handoff, via `open_stay_started_ts` — when `segment_stays`
detects a genuine open stay at the tail of a batch, fixes from that point
on are excluded and left for a later cycle. But **there is no equivalent
protection for a trip's own trailing edge during continuous movement.**
When there's no open stay at all (the person is still driving, so nothing
satisfies the stay radius test), `open_stay_started_ts` is `None`, and
`extract_trips`'s unconditional final flush —

    if len(buffer) >= min_trip_fixes:
        trips.append(_trip_from_group(buffer, ...))

— turns whatever's in the buffer at batch-end into a finished trip, no
matter how far that boundary sits inside a still-continuing drive. Since
`run_once` only ever sees fixes since the last watermark (no cross-tick
accumulation exists for trips), each tick's batch becomes its own trip.

Stays are immune to this because their equivalent protection lives inside
`segment_stays` itself — the `open_stay_started_ts` exclusion — one layer
below `_tick`'s cadence, not dependent on it. Trips need the same kind of
protection, not a smarter scheduler.

## 2. The fix

**A trailing group of movement fixes is not finalized into a trip while
its own tail is still uncertain — mirroring exactly how a trailing open
stay is already withheld.**

A trailing group stays undecided (held back, not written) while either:

- its last fix has `is_driving = True` — Life360's own per-fix signal
  that the device is currently in a moving vehicle, sent on every fix
  (100% filled, unlike `speed_kmh`'s 14.7% — measured in design/27 §1);
  or, as a fallback for when that field is absent,
- not enough real time has passed since the last fix to be confident
  movement has stopped — reusing `settled_seconds`, the same number Tier 1
  already uses to decide a stay has genuinely started. One real-world
  signal ("has this person stopped moving"), one number, looked at from
  two different derivations.

`is_driving` is checked first and is the common case; the time-based
fallback only matters when the field is `None` (should be rare, given its
100% fill rate) or as the resolving condition once `is_driving` finally
flips false.

### 2.1 `RawFix` gains `is_driving`

`is_driving` is already captured into `presence_fixes` (an `INTEGER` 0/1
column, per `nora/infra/sql/schema.py`) — it has simply never been read
back out into the domain layer. Same situation `address` was in before
Tier 5's own corridor work surfaced it. Add:

    is_driving: bool | None = None

to `RawFix` (`nora/domain/mobility.py`), threaded through
`fixes_since` in both `SqlMobilityStore` and `FakeMobilityStore`
(`nora/infra/sql/mobility.py`, `nora/testing/mobility.py`), the same
pattern `address` already established.

### 2.2 `extract_trips` gains a settlement check

New parameters: `now_ms: int` and `settled_seconds: float` (or reuse the
existing `RollupConfig.settled_seconds` — no new config knob needed here
specifically, since this is the same physical signal Tier 1 already
measures).

Before the final unconditional flush, and before any mid-loop flush that
currently happens when a buffer's run ends at a detected stay boundary,
the function checks whether the trailing group is settled:

    def _is_settled(group: list[RawFix], now_ms: int, settled_seconds: float) -> bool:
        last = group[-1]
        if last.is_driving is True:
            return False
        if last.is_driving is None:
            return (now_ms - last.ts) / 1000 >= settled_seconds
        return True  # is_driving is False — Life360 itself says movement stopped

If the trailing group is not settled, it is **not** appended to `trips` —
it is returned separately (or its earliest fix id is returned) so the
caller knows not to advance the watermark past it.

This only applies to the group at the very *end* of the fixes read this
cycle — groups that close earlier in the batch (because a real stay was
detected between them) are already correctly finalized by the existing
`_in_a_stay` branch and are unaffected.

**This settlement check applies to exactly one call site, not every
call.** `run_once` calls `extract_trips` once per chunk
(`_chunk_by_gap` already having split the batch on real, observed time
gaps ≥ `DWELL_CAP_MS`). A non-final chunk's own trailing buffer is, by
construction, already followed by a confirmed large gap to the next
chunk — that boundary is real, not an artifact of where the batch
happened to end, and is already correctly finalized today. **Only the
FINAL chunk's trailing buffer is genuinely ambiguous** — exactly the
same scope `open_stay_started_ts` already uses (`if i == len(chunks) - 1`
in `run_once`). The settlement check is wired in alongside that existing
final-chunk-only condition, not applied unconditionally inside
`extract_trips` itself.

### 2.3 The watermark holds back the undecided tail

Today, `nora/analytics/rollup.py`'s `run_once` calls
`advance_fix_watermark(subject_id=..., fix_id=fixes[-1].id)`
unconditionally — the batch's true last fix, regardless of what's still
undecided.

The fix: when `extract_trips` reports a held-back trailing group, the
watermark advances only to the fix immediately *before* that group's
earliest fix — not past it. Those fixes remain readable via `fixes_since`
next cycle, where they're re-read together with whatever new fixes have
since arrived, naturally reconstructing the full group without any special
"this is a continuation" state — the buffer-building logic in
`extract_trips` already handles this correctly by construction, since it
just processes whatever `fixes` it's given in order.

This is deliberately the same shape DEC-122's own corridor-accumulation
fix uses (`corridor_id IS NULL` as the durable "not yet processed"
marker) — a real gate expressed as "don't consume this yet," not a
timer or a second watermark column.

**A note on scope, stated plainly rather than left implicit:** tracing
this raised a real question about whether the *existing* open-stay
mechanism has an analogous property — whether a stay spanning many ticks
without resolving in one batch could lose track of its true arrival
time, since its fixes are watermark-consumed the same unconditional way.
This was not confirmed as an actual bug (Tier 1 is live 13 days and has
been reviewed thoroughly; the investigation here was partial, not a full
audit), and it is explicitly **out of scope for this fix** — flagged
here so it isn't silently forgotten, not because it's known broken.

### 2.4 Safety valve — a maximum trip duration

If `is_driving` were ever stuck `True` (a stale flag on Life360's side, a
bug, a phone that stopped reporting mid-drive), a trip could in principle
never settle and grow forever, holding the watermark back indefinitely.
A max-duration cap forces finalization regardless of the settlement check
once a trailing group's span exceeds it.

**No real trip-duration data exists yet to measure this against** — the
9 trips in production today are exactly the fragments this fix corrects,
so there's nothing genuine to derive a percentile from. This is
therefore a judgment call, not a measured value, and is named as such:
`max_trip_duration_seconds`, default **10800 (3 hours)** — generous
enough to cover a genuine long drive (cross-territory, a road trip)
without leaving a stuck flag unbounded. Config knob on `AnalyticsConfig`
and `RollupConfig`, same pattern as every other Tier 5 knob
(`corridor_max_edit_distance`, `min_trip_fixes`, etc.).

## 3. Testing

Not tested today — confirmed no existing test proves or disproves
multi-tick trip settlement either way. New coverage needed:

- **The core fix, proven across real cycles**: seed a continuous drive
  spanning multiple `run_once` calls (mirroring the shape of the redesign's
  own `test_a_repeating_commute_actually_grows_corridors_over_days`, but
  for settlement rather than accumulation) — assert exactly ONE trip
  results, not one per tick, and that its `started_ts`/`ended_ts` span the
  whole drive.
- **`is_driving` as the primary signal**: a drive that ends (`is_driving`
  flips `False`) settles on the very next cycle, without waiting
  `settled_seconds`.
- **The `is_driving = None` fallback**: with the field absent, the group
  only settles once `settled_seconds` has genuinely elapsed since its last
  fix.
- **The safety valve**: a synthetic case where `is_driving` never flips
  (or the field is always `None` and quiet never arrives) is force-closed
  once `max_trip_duration_seconds` is exceeded.
- **The near-zero-movement fragment case**: the 18:12–18:12 shape (a brief
  stop mid-batch, still `is_driving=True` or not yet settled) should no
  longer be extracted as its own standalone trip — it should merge into
  whichever larger drive it's actually part of.
- **Watermark correctness**: a held-back tail's fixes are still returned by
  `fixes_since` on the following cycle (not silently dropped), and the
  eventual finalized trip is written exactly once (no duplicate row from
  re-processing the same fixes across cycles).
- **Existing behavior preserved**: the trip-to-stay boundary
  (`open_stay_started_ts`) and the mid-batch stay-detected flush both keep
  working exactly as before — this fix only changes what happens at the
  very end of a batch when nothing else has already decided the boundary.

## 4. Scope boundary

Out of scope for this fix, deliberately:

- `_tick`'s missing `safety_lag`/`settled_seconds` fire conditions
  (§1 above) — real, pre-existing, but orthogonal to fragmentation. Left
  for a separate pass.
- Any change to `segment_stays`'s own stay-arrival-time handling (§2.3's
  flagged, unconfirmed question).
- Backfilling or re-deriving the 9 already-fragmented trips currently in
  production. They stay as they are; new drives from deploy onward extract
  correctly. Whether to prune the known-fragmented rows is an operator
  decision, not part of this fix.
