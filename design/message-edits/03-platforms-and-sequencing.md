---
type: design
title: "Message Edits — Platforms and Sequencing"
description: "Per-network wiring of the edit event (Signal targetSentTimestamp, WhatsApp edit_of, Instagram kind=edit, Discord on_raw_message_edit), the one-PR build order, the tests each claim owes, and what is left as history."
tags: [nora, platform, signal, whatsapp, instagram, discord, tests]
timestamp: '2026-09-23'
edges: []
---

# 03 — Platforms, order, proof

## 1. Per network

| network | edit arrives as | `edit_of` | sender | edit time |
|---|---|---|---|---|
| Signal | `envelope.editMessage` | `sent_ts = targetSentTimestamp` | envelope `source` | envelope `timestamp` |
| WhatsApp | `message` event with `edit_of` | `message_id = edit_of` | `sender_phone`/lid, as today | `timestamp_ms` |
| Instagram | `kind: "edit"` | `message_id` | none — `from_me` is null by contract | `timestamp_ms` when present, else arrival |
| Discord | `on_raw_message_edit` | `message_id = snowflake` | `payload.message.author` | `edited_at`; an update with none is not an edit and is dropped |

No bridge changes: both bridges already emit the edit (`bridge_helpers.js`
`extractMessageEvent`, `instagram-bridge/main.go` `EditMessageEvent`).
Discord uses the RAW event because `on_message_edit` fires only for messages
still in discord.py's cache; `payload.message` (discord.py ≥ 2.4) is the full
edited message and goes through `parse_discord_message` unchanged.

## 2. Order

One PR: every step below is needed for any network to behave, and none ships
a half that is safe on its own (a stored `edit_of` with no fold is today's
duplicate row with extra columns).

1. Schema: `message_edit`, `messages.platform_message_id` (+ backfill),
   `messages.mentions_json`. One migration.
2. Domain: `EditTarget`, `RowEdit`, `NormalizedMessage.edit_of`,
   `TranscriptRow.edit`; `RecordStore.find_edited`/`append_edit`; the fold in
   `SqlRecordStore` and `FakeRecordStore`.
3. Ingest: the edit path (`02` §4).
4. Render: `edited`/`was`, rehydration of both envelopes.
5. Parsers and the Discord listener.

## 3. What stays as history

Signal and WhatsApp edits ingested before the deploy are rows in `messages`.
They remain duplicates in the transcript until they age out of the window.
Relinking them is possible (`targetSentTimestamp`/`edit_of` are in their
`raw_json`) and is not done: the window is 40 messages, so they are gone from
every prompt within days, and a data migration that rewrites the audit is
not worth that.

## 4. Proof

Each claim is pinned by a test that reverting its line reddens:

- the store contract (both stores): fold returns latest body at the original
  position, `was` = first body, `raw_json` unchanged; `find_edited` by handle
  and by (sender, ts); cascade on prune;
- ingest: each of the five outcomes; the newly-addressed rule's four rows;
  identity from the original when the edit names no sender;
- render: `edited`/`was` present on an edited row, absent otherwise;
  rehydration uses the edit's envelope for the body and the original's for
  `was`;
- parsers: each network produces `edit_of` with the right target;
- Discord: an update to a never-edited message (no `edited_at`) never
  reaches ingest;
- the tag trace round-trips an edited row (`RowEdit` is not a JSON primitive).

Read-back after deploy: edit a message in a test room on each network and
read the `message_edit` row and the `edit_*` outcome counter on norty.
