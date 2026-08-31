---
type: design
title: "Event trigger — check on message arrival, not a loop"
description: "Every message that lands in a conversation triggers a standing check; the check folds only if there is new material since the watermark."
tags: [nora, standing, event-driven, ingest]
timestamp: 2026-09-01
edges:
  - {dst: 00-intent, rel: realizes, provenance: asserted}
  - {dst: 20-watermark-read, rel: depends_on, provenance: asserted}
---

# Event trigger

## The shape

No background loop. The ingest path already sees every message — tagged or
not — so the standing check rides there:

```
IngestPipeline.run
  └─ audit write (the row exists)
  └─ spawn(standing_check)   ← off the listener path, like memory_feed
       └─ read watermark for this conversation
       └─ if new rows since watermark:
            run fold (same fold as the tag path)
```

`spawn` is the same injection `memory_feed` uses
(`nora/ingest/stages/memory_feed.py:1-8`): the check is scheduled off the
listener's await path and returns immediately, so a slow fold never stalls
the socket read for every conversation on the adapter.

## Why event-driven beats a loop

- **Zero work when nothing happens.** A background loop ticks every N
  minutes and asks "is there anything new?" for every conversation — work
  that is almost always nothing. The event path only runs when a message
  actually arrived.
- **No tick cadence to tune.** A loop needs a `tick_seconds` knob and a
  decision about how stale is too stale. The event path is fresh by
  construction: the check runs on the message that made it stale.
- **Same infrastructure as the tag path.** The fold itself is unchanged —
  same objective, same store, same verdict call. Only the trigger differs:
  tag path folds after a reply; event path folds after an untagged message.

## What the check does

1. Read the conversation's watermark (`last_folded_row_id`).
2. Ask the record store: are there rows with `id > watermark`?
3. If yes, run the fold. If no, do nothing.

The check is cheap: one indexed read. It is not a model call until there is
actually something to fold.

## The accumulator — threshold equals batch

The check does not fold on every message. It counts rows since the
watermark, and folds only when the count reaches the batch cap:

```
message arrives → count rows since watermark
  └─ count < fold_batch (200) → do nothing, wait for the next message
  └─ count >= fold_batch (200) → enqueue an event fold
       └─ fold reads since watermark (at most 200 rows)
            → writes position + advances watermark
```

**The accumulator threshold IS the fold batch — one number, `[standing]
fold_batch = 200`.** The operator confirmed: same number, always. A
conversation that accumulates 500 rows folds 3 times (200 + 200 + 100),
each fold's position carrying the previous one forward (30-rollup).

## Serialization — the existing per-conversation slot

A burst of messages can trigger several checks before any of them folds.
The fold must be serialized per conversation, or two folds race on the same
watermark. The mechanism already exists: `ConversationSlots`
(`nora/engagement/slots.py`, R-10 single-in-flight lock + R-30
per-conversation FIFO queue).

```
Message A → accumulator at cap → enqueue(event_fold)
  └─ slot free → run fold (writes position + advances watermark)
       └─ release_and_dispatch
            └─ Message B's check runs → re-reads watermark → re-counts
                 └─ still >= 200 new rows → folds again
```

- **No two folds race the same watermark** — the single-in-flight lock
  guarantees one fold per conversation at a time.
- **No message is lost** — every message is already in the audit store
  before the check runs; the check only counts rows, and a fold that runs
  after a burst naturally counts the stragglers.
- **The event fold enqueues, it does not `try_acquire`-skip.** A tag that
  arrives while an event fold is in flight queues behind it in the same
  FIFO — the tag is delayed by the fold's bounded runtime (2-5s), never
  dropped. Tags keep the full `acquire`/`release_and_dispatch` path; the
  event fold uses the same queue, one slot, FIFO order.

## Restart safety — the queue is memory, the watermark is Postgres

`ConversationQueues` (`nora/engagement/queue.py:54-93`) is a pure in-memory
`deque` — no persistence. A container restart loses whatever event folds
are queued. **This loses no message and no backlog:**

- Messages are already in the Postgres audit store — restart does not touch
  them.
- The fold's state is the watermark (`last_folded_row_id`), which lives on
  the standing row in Postgres — restart does not touch it either.
- After restart, the next message's check re-reads the watermark, re-counts
  rows since it, and re-triggers the fold if the backlog is still there.

The queue is scheduling, not state. The watermark is the state, and it is
durable. This is the property that makes the event fold idempotent-ish:
a lost queue entry is re-derived from the watermark, never silently dropped.

**The tag path is different and this is existing behaviour, not new.** A
queued TAG lost to restart is a tag nobody answers — there is no watermark
to re-derive it from. That is R-30's accepted design (the queue is
transient by construction); the event fold does not inherit the problem
because its work is defined by the watermark, not by the queue.

## Interaction with the tag path

A tagged message flows through ingest too — it is a message like any other.
The event check fires on it, and the tag path's own fold fires after the
reply. Two folds for one tagged turn is the expected shape, and it is
correct: the event fold captures the conversation up to the tag, the tag
fold captures the turn itself. The watermark advances on both, so the next
fold starts where the last one ended.

## Failure modes

- **A fold that fails must not block the message.** The check is spawned,
  and a fold failure is logged and counted (`nora_debrief_total{outcome=...}`)
  exactly as the tag path's fold failure is. The message is already recorded;
  the position is a lossy tier and a missed fold is a stale position, not a
  lost message.
- **Concurrent folds on the same conversation.** A burst of messages can
  spawn several checks before any of them folds. The fold must be
  serialized per conversation — the same slot discipline the tag path uses —
  or two folds race on the same watermark. The watermark read + write must
  be atomic (see 20-watermark-read).

## Status / Checkpoint

_2026-09-01_ — proposed. Operator decision: event-driven, no background
loop. The spawn injection already exists in the ingest path.
