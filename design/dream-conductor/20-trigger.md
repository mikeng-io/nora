---
type: design
title: "Trigger — ingest wakes the Dreamer; the clock is only a floor"
description: "One ingest stage marks a unit changed and scores the message for cues; a per-unit session fires on quiet, on batch size, or early on a cue; time-driven work gets a daily floor. Single-flight per unit and a global concurrency bound."
tags: [nora, dreamer, trigger, ingest, event-driven, debounce]
timestamp: '2026-09-27'
edges:
  - {dst: 30-conductor, rel: sequences, provenance: asserted}
  - {dst: ../dreamer-agent-loop/06-trigger.md, rel: realizes, provenance: asserted}
---

# 20 · Trigger

## 1. The seam

A new ingest stage, placed with the Standing event stage in
`nora/ingest/pipeline.py`, runs on every recorded human message. It does
three things, all in code and all cheap:

1. **Marks the change.** Appends `(unit, room, message id, received_at)` to a
   per-unit pending set. Nora's own rows are not marked: nothing she says
   wakes the Dreamer, for the same reason nothing she says creates a note.
2. **Scores cues** (the "message-sensitive" part). A cue is a deterministic
   feature, never a model call:
   - a date or time expression, or a weekday («禮拜六», "tmr 8pm");
   - a currency amount or a split («$200», «夾錢», "each");
   - a keep instruction («記低», "note that", "remember");
   - an RSVP word («去», «唔去», "count me in");
   - a reply to, or an edit or delete of, a message a live note, memo or
     gathering cites.
3. **Arms the unit's timer.** It never runs the session inline; ingest stays
   on its own budget.

This is the hook design 06 §4 asked for, and the same shape as
`nora/ingest/stages/standing_event.py`, which already spawns its fold from
ingest. The two stay separate: Standing is per room and belongs to the tag
path's position; the Dreamer is per unit.

## 2. When the session fires

For a unit with pending changes, the first of:

| condition | default | why |
|---|---|---|
| the unit's rooms have been quiet for `quiet_seconds` | 600 (today 1800) | never interrupt a live conversation (the rule `[dreamer] quiet_seconds` already states) |
| pending messages ≥ `batch_max` | 80 (note-candidates' `batch_limit`) | a room that never goes quiet is still read (today's `safety_lag`, 300) |
| a strong cue and quiet ≥ `cue_quiet_seconds` | 120 | a settled time or a «記低» should not wait ten minutes |
| the oldest pending change is `max_age_seconds` old | 3600 (today 86400) | the bound the hourly note-candidates slot gave, kept as a ceiling |

Every default is a first guess to be re-sized from the shadow week's
measured wake reasons ([50](50-migration.md) step 1), never from n=1.

A cue shortens the wait; it never decides a job. What the change needs is the
conductor's question ([30](30-conductor.md)).

## 3. Time is an event too

Some work has no message behind it: a note's anchor passes, a revisit comes
due, a gathering's date arrives, the proactive daily floor. These are
**time events**, produced by a query, not by ingest. One floor per unit per
day (HK 00:00, the curator's current slot) and the existing heartbeat's due
query put them in the same pending set as `(unit, kind=time, ref)`. The floor
is also the safety net for a plan that dropped something
([00](00-intent.md) §5).

## 4. Bounds

- **Single-flight per unit.** A unit has at most one session running. Changes
  that arrive during it wait for the next; the session commits a unit
  watermark only for the messages it was handed.
- **A global bound.** At most `max_concurrent_sessions` (default 2) sessions
  across all units, by semaphore. Design 06 §6 required this before any
  migration; the current `dream_loop` has none.
- **Backoff per unit**, kept from the current runtime: a session that fails
  doubles its unit's minimum wait, reset by the next success. "Nothing to do"
  is its own outcome and does not reset backoff (06's finding that it did).

## 5. Metrics

`nora_dream_wake_total{reason}` (quiet, batch, cue, max_age, floor),
`nora_dream_cue_total{cue}`, `nora_dream_session_seconds`,
`nora_dream_pending_age_seconds`. The latency falsifier in
[00](00-intent.md) §5 is read from the last two.
