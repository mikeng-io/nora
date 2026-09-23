---
type: design
title: "Message Edits — Behaviour"
description: "How an edit renders (edited + was attributes), what Honcho receives (a framed correction under the speaker's peer), when an edit opens a turn (only when it newly addresses Nora), and the edit path's five fail-closed endings."
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

A non-engaging edit is never a Relevance Gate candidate: it is not new room
activity. The sender allowlist applies as it does to any tag.

## 4. The edit path

Scope → find the original → identity → custody → record → Honcho → gate.
Five endings, on `nora_ingest_outcomes_total`:

| outcome | when | written |
|---|---|---|
| `edit_recorded` | folded onto its original | `message_edit` row, Honcho feed |
| `edit_engaged` | as above, and it newly addresses her | same, plus a turn |
| `edit_unchanged` | the text equals the version it replaces | nothing |
| `edit_orphaned` | no original on record | see below |
| `edit_refused` | the original is Nora's own, or its sender differs from the editor's | nothing |

`edit_unchanged` is what absorbs Discord's non-edits: `MESSAGE_UPDATE` also
fires when a link preview resolves or a message is pinned, with the text
unchanged. Filtering on the text rather than on the event type is total — it
also absorbs a bridge redelivering an edit.

**Orphans.** An edit whose original was never recorded (it predates Nora in
the room, or was in a room not yet allowlisted) is the only version she will
ever see. When the platform names the sender it is ingested as an ordinary
message, exactly as Signal and WhatsApp did before this change; Instagram
names no sender, so an Instagram orphan is dropped and counted.

**Identity comes from the original** when the edit names no sender
(Instagram), and must MATCH it when it does. A mismatch is refused: an edit
is not a way to put words under someone else's row.

**No standing event.** The event-driven standing fold counts new messages; an
edit is not one.
