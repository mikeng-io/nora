---
type: design
title: "Message Edits — Overview & Scope"
description: "A member editing a message on any of the four networks becomes ONE typed edit event, recorded append-only beside the message it edits, folded into the transcript at read time (latest text, at the original's position, with when it changed and what it first said), fed to Honcho as a correction, and allowed to open a turn only when the edit newly addresses Nora."
tags: [nora, ingest, transcript, honcho, signal, whatsapp, instagram, discord]
timestamp: '2026-09-23'
edges: []
---

# Message Edits — Overview & Scope

## Why

All four networks let a member change a message after sending it. What Nora did
with that, measured against the tree on 2026-09-23:

| network | reached ingest? | how |
|---|---|---|
| Signal | yes | `editMessage.dataMessage` unwrapped (`8f3b5481`) and written as a NEW row |
| WhatsApp | yes | the bridge's `edit_of` event written as a NEW row; `edit_of` kept only in `raw_json` |
| Instagram | no | the bridge emits `kind: "edit"`; the parser drops every kind but `message` |
| Discord | no | the listener registers `on_message` only |

The two that arrive arrive as an ordinary message. Nothing links the new row
to the one it edits, so:

- **the model cannot tell a correction from a repetition.** The transcript
  shows both versions, the second at the time of the EDIT, unmarked;
- **an edit is a fresh tag.** Fixing a typo in a message that tagged Nora
  re-enters the engagement path as a new message;
- **there is nothing to join on.** The platform's message id is parsed onto
  `NormalizedMessage.message_id` for Discord, WhatsApp and Instagram and never
  stored in a column, so an edit that names its target by that id cannot
  find it.

## What this change is

One edit shape, one record, one fold:

1. **Every parser emits the same thing** — a `NormalizedMessage` whose
   `edit_of` names the message it edits, by the platform's handle
   (`message_id`) or, on Signal, by the original sent-timestamp (`sent_ts`).
   `01` §1.
2. **Ingest records it beside its original, never over it.** A
   `message_edit` row points at the `messages` row it edits; `messages` is
   untouched (A9, append-only). `01` §2.
3. **Reads fold it.** `RecordStore.recent`/`since` return the original row at
   its original position carrying the LATEST text, plus the time it changed
   and what it first said. Every transcript consumer gets the fold without a
   change of its own. `01` §3.
4. **Renders say so.** The `m` element gains `edited` (an age) and `was` (the
   first text). `02` §1.
5. **Honcho gets a correction**, not an overwrite: Honcho cannot change a
   message's content (only its metadata), and the deriver has usually already
   read the original. `02` §2.
6. **An edit opens a turn only when it newly addresses Nora.** `02` §3.

## Is not

- **Deletes/unsends.** Operator scope, 2026-09-23: «I don't think we need to
  capture deletes».
- **Re-answering an edited question she already answered.** Operator decision,
  2026-09-23: v1 does not; the corrected text is in front of her next turn.
  *The reading that would reopen it:* a room complaint that she answered the
  old version after it was corrected.
- **Relinking the Signal/WhatsApp edits already stored as their own rows.**
  They stay as history; see `03` §3.
- **Nora editing her own messages.** Unrelated path (`POST /edit` on the
  WhatsApp bridge exists already).

## Reading order

`01-model-and-storage.md` → `02-behaviour.md` → `03-platforms-and-sequencing.md`.
