---
type: design
title: "Watermark read — since the last fold, including Nora's own rows"
description: "A new read that returns everything since the watermark (row id), including Nora's own rows, capped at 200."
tags: [nora, standing, watermark, record-store]
timestamp: 2026-09-01
edges:
  - {dst: 00-intent, rel: realizes, provenance: asserted}
  - {dst: 10-event-trigger, rel: depends_on, provenance: asserted}
  - {dst: 30-rollup, rel: depends_on, provenance: asserted}
---

# Watermark read

## The read

A new method on the record store, alongside `recent` and `since`:

```python
async def since_including_nora(
    self, *, conversation_id: str, after_id: int, limit: int
) -> list[TranscriptRow]:
    """All rows with `id > after_id`, ASCENDING, at most `limit`.

    Unlike `since`, this does NOT exclude Nora's own rows. The fold needs
    her replies to re-judge her position ('I committed to...', 'I answered...');
    a read that strips her half of the conversation shows the fold a room
    where she never spoke.
    """
```

`since` stays unchanged — the Dreamer's derivation reads must keep excluding
Nora's own words (`audit.py:147-179`). The new read is for the fold only.

## Why row id, not timestamp

- `RecordStore.since` is already a cursor read on `id > after_id`
  (`audit.py:147-179`) — the pattern exists, this is not new machinery.
- Timestamps are platform-shaped: Discord snowflakes and Signal epoch ms do
  not share a scale, and two messages in one conversation can share a ts.
- The design doc already records why timestamp proxies fail
  (`26-standing.md` §4: `created_at > prior.updated_at` and `>=` were both
  wrong, in opposite directions). Row id is monotonic and unambiguous.

## The watermark column

`standing` gains a column:

```sql
last_folded_row_id bigint  -- the audit row id the last fold read up to
```

- `NULL` on a conversation that has never folded — the cold-start case
  (see 30-rollup).
- Written atomically with the position: the fold's `UPDATE` sets both
  `body` and `last_folded_row_id` in one statement, so a crash between the
  two can never leave a position that claims to have read further than it
  did, or a watermark that advanced past a position that was never written.
- The same atomicity the `consumed_verdict_tag` watermark already has
  (`26-standing.md` §4: the row carries the verdict it acted on, advanced
  only when the rebuild is WRITTEN).

## The cap

`limit = 200`, a config knob (`[standing] fold_batch`), replacing the
`window` semantics. The fold reads at most 200 rows per call.

- 200 is a batch size, not a window: the fold reads the NEXT 200 rows since
  the watermark, not the LAST 200 rows. The difference is the whole bundle.
- A conversation with 500 new rows folds 3 times (200 + 200 + 100), each
  fold's position carrying the previous one forward — the rollup in 30.
- The cap exists so one fold cannot blow the prompt budget; it is not a
  recency clip.

## Re-derivation and the watermark

Re-derivation (the `ungrounded` / `ceiling` trigger) re-reads the SAME
stretch — it must NOT advance the watermark, or it would skip the stretch it
was re-deriving. The watermark advances only on a normal fold that read new
rows. This mirrors the `consumed_verdict_tag` rule: a rebuild does not claim
to have consumed a verdict it did not act on.

## Status / Checkpoint

_2026-09-01_ — proposed. Operator decisions: row id watermark, cap 200,
new read including Nora's rows, watermark column on the standing row.
