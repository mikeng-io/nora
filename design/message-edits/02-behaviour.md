---
type: design
title: "Message Edits — Behaviour"
description: "How an edit renders (edited + was attributes), what Honcho receives (a framed correction under the speaker's peer), when an edit opens a turn (only when it newly addresses Nora), and the edit path's six fail-closed endings."
tags: [nora, ingest, prompt, honcho, engagement]
timestamp: '2026-09-23'
edges: []
---

# 02 — Behaviour

## 1. What the model reads

`render_messages` gains two optional accessors. An edited row renders:

```xml
<m n="12" age="9m ago" from="Miles" edited="2m ago" was="dinner at 7?">dinner at 8?</m>
```

- `edited` — the AGE of the last edit, the same clock as `age`. The
  judgement this feeds is about time («was her answer before or after the
  correction?»), so the block carries a time (AGENTS.md: *when the measurer is
  a model, its input has to explain itself*).
- `was` — the first text, collapsed to one line like every attribute.
  Without it the model can see that a message changed but not what her
  earlier reply was answering.

Both are declared on the `group_transcript` block type (`block_types.py`), so
the description every audience reads says what they mean. The relevance gate
renders both; the culture fold renders the body only (it reads how a room
talks, not when).

## 2. What Honcho receives

Honcho stores a message's content once: the API updates metadata only, and
the deriver has typically already read the original. So an edit is fed as a
NEW message, under the speaker's peer, in the room's session:

```
[edited an earlier message; it first said: «dinner at 7?»] dinner at 8?
```

with metadata `{"ts": <edit ts>, "edit_of_ts": <original ts>}`. The framing is
what lets the deriver treat it as a correction rather than a second
statement. Mapped senders only (D6), spawned off the listener path, exactly
like the ambient feed.

## 3. When an edit opens a turn

The engage policy runs on the edit. If it would engage, it runs AGAIN on the
version the edit replaced (same message, that version's `body` and
`mentions`). The edit engages only when the first says yes and the second
says no — **it newly addresses her**.

| edit | before | result |
|---|---|---|
| adds `@Nora` to a message that had none | no | **turn** |
| fixes a typo in a message that tagged her | yes | recorded, no turn |
| edits a reply to her (the quote cannot change) | yes | recorded, no turn |
| before-version's mentions unknown (row predates the column) | ? | recorded, no turn (R-5) |

«A reply to her» is read off the RECORDED message (`EditedMessage.replies_to_nora`),
not the edit event: Instagram's edit delta carries no quote, so reading it off
the edit made adding an `@Nora` to an answered reply look newly addressed
(review finding, 2026-09-24). An edit event that cannot see mentions at all
(Instagram, `EditTarget.mentions_known = False`) carries the original's
forward, and records NULL — «unknown» — when the original's were unknown too.

A non-engaging edit is never a Relevance Gate candidate: it is not new room
activity. The sender allowlist applies as it does to any tag.

## 4. The edit path

Scope → find the original → identity → custody → record → Honcho → gate.
Six endings, on `nora_ingest_outcomes_total`:

| outcome | when | written |
|---|---|---|
| `edit_recorded` | folded onto its original | `message_edit` row, Honcho feed |
| `edit_engaged` | as above, and it newly addresses her | same, plus a turn |
| `edit_unchanged` | the text equals the version it replaces | nothing |
| `edit_stale` | no newer than the newest edit on record (a redelivery, or out of order) | nothing |
| `edit_orphaned` | no original on record | nothing |
| `edit_refused` | the original is Nora's own, or its sender differs from the editor's | nothing |

Discord's non-edits never reach this path: `MESSAGE_UPDATE` also fires when a
link preview resolves, a message is pinned or a thread starts from it, and a
message its author never edited has no `edited_at`, so the parser answers None
(`03` §1). An update to a message that WAS edited earlier carries that older
`edited_at` and ends `edit_stale` or `edit_unchanged`. Filtering on the text
and the clock rather than on event types is what keeps this total across
platforms and redeliveries.

**Orphans are dropped, whoever sent them.** An edit whose original was never
recorded — pruned past the 500-per-room window, from before Nora joined or
before the room was allowlisted, or a Discord update racing its own message —
is counted and discarded. The first draft ingested an attributed orphan as an
ordinary message; review (2026-09-24) traced that to an OLD message landing at
the bottom of the transcript as if said just now, where a tag in it opened a
turn on something long settled, and to a duplicate row when an update raced
its message's insert. Losing the rare genuine edit of an unrecorded message is
the smaller cost.

**Identity comes from the original** when the edit names no sender
(Instagram), and must MATCH it when it does. A mismatch is refused: an edit
is not a way to put words under someone else's row.

**No standing event.** The event-driven standing fold counts new messages; an
edit is not one.
