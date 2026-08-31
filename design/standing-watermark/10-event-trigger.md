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
