---
type: design
title: "Message Edits — Model and Storage"
description: "EditTarget on NormalizedMessage; a message_edit table pointing at messages.id; messages gains platform_message_id (backfilled from raw_json) and mentions_json; recent/since fold the latest edit onto its original as TranscriptRow.edit, keeping the original's raw_json for handles."
tags: [nora, ingest, schema, transcript]
timestamp: '2026-09-23'
edges: []
---

# 01 — Model and storage

## 1. The edit event

`NormalizedMessage` gains one defaulted field:

```python
edit_of: EditTarget | None = None

@dataclass(frozen=True)
class EditTarget:
    message_id: str = ""      # the platform's handle of the EDITED message
    sent_ts: int | None = None  # Signal only: the edited message's sent-timestamp
```

Everything else on the message describes the NEW version: `body`, `mentions`,
`media`, `raw`, and `timestamp` (when it was edited). `sender_id` is who the
platform says edited it, and is `""` when the platform does not say
(Instagram — the edit delta carries no sender).

**One shape rather than an `IngestEdit` type** because every stage the edit
needs — scope, identity, custody, the gate — already takes a
`NormalizedMessage`, and a second type would need a second copy of each.

## 2. Storage

### `message_edit` (new)

| column | |
|---|---|
| `id` | identity PK |
| `message_id` | FK → `messages.id` **ON DELETE CASCADE**; `prune_all` deletes messages and must take their edits with them |
| `ts` | the platform's edit time, ms |
| `body` | the new text, placeholders and handles as custody wrote them |
| `raw_json` | the edit's own envelope |
| `mentions_json` | the edit's mention ids (see below) |
| `created_at` | |

Indexed on `(message_id, ts DESC)`: the fold reads the newest edit per row.

**A table, not rows in `messages`.** Five readers query `messages` directly
(`language`, `participation_state`, `participation_measures`, `snapshot`, the
stats). An edit stored as a message is counted as one by every one of them —
a second utterance, a second exchange, a second speaker turn. A side table
leaves them counting what they count today.

### `messages` gains two columns

- **`platform_message_id`** (nullable text) — `NormalizedMessage.message_id`,
  written in the same INSERT. Indexed with `conversation_id`. The migration
  BACKFILLS it from `raw_json` (`id` for WhatsApp and Discord, `message_id`
  for Instagram), so an edit to a message stored before the deploy still
  finds it.
- **`mentions_json`** (nullable text) — the sorted mention ids the parser
  produced. Needed for one question only: *did the version before this edit
  already address Nora?* (`02` §3). Interpreted once, at ingest, like every
  other parser output; the alternative — re-parsing `raw_json` per platform in
  ingest — interprets twice. NULL on rows from before the deploy, which the
  gate reads as «unknown» and refuses on (`02` §3).

### Finding the original

`RecordStore.find_edited(platform, conversation_id, target, sender_id)`:

- `target.message_id` → the row with that `platform_message_id` in that
  conversation;
- `target.sent_ts` (Signal) → the row with that `ts` from that `sender_id`.
  Signal addresses a message by (author, sent-timestamp) everywhere else too.

Returns the original with its CURRENT version (latest edit folded), because
the gate compares against the version the edit replaced, not the first one.

## 3. The fold

`recent`, `since` and `since_including_nora` fold the newest `message_edit` of
each row onto it. `TranscriptRow` gains:

```python
edit: RowEdit | None = None

@dataclass(frozen=True)
class RowEdit:
    ts: int                 # when it was last edited
    was: str | None         # what it FIRST said (the messages.body)
    raw_json: str | None    # the latest edit's envelope
```

and `body` becomes the latest text. **`raw_json` stays the ORIGINAL's.** Two
consumers read it and want different envelopes:

- `platform_message_id_of` (threading a reply, reacting) needs the original's
  handle. A WhatsApp edit's envelope carries the EDIT stanza's id; replying
  to that quotes the wrong message.
- mention rehydration (`nora.engagement.mentions`) must look spans up in the
  envelope the body came from (the Signal parser's own docstring:
  «a mention has to be looked up in the dataMessage the body came from»).

So the latest text's envelope travels as `edit.raw_json`, and `was` is
rehydrated against `raw_json`.

**Position is the original's.** `ts` and `id` do not move, so the message
stays where it was said and `replying_to` pointers into it keep resolving.

**`was` is the first text, not the previous one.** A reply or a Nora answer
between the two was to SOME earlier version; the first is the one the room
most likely read. Multiple edits are rare and v1 does not show the chain.

`media_json` stays the original's: custody runs on the edit (`02` §4) and
content addressing means the same bytes are the same handles.
