---
type: design
title: "Observe — ingestion fills the Record and raises signals; only sources that cannot push are polled"
description: "The five streams (messages, presence, social, play, perception), what each records, which are pushed and which must be polled, the social graph within members only, the wake signals and their bounds."
tags: [nora, observe, ingestion, social-graph, instagram, presence, play, steam, discord, signals]
timestamp: '2026-09-28'
edges:
  - {dst: 30-cognition, rel: sequences, provenance: asserted}
  - {dst: 60-faces, rel: extends, provenance: asserted}
  - {dst: ../dreamer-agent-loop/06-trigger.md, rel: realizes, provenance: asserted}
---

# 20 · Observe

Observe is **code**. It never asks a model what is worth recording; it records
everything its sources give it and says what changed.

## 1. The five streams

| stream | records | arrives by | exists today |
|---|---|---|---|
| **messages** | every row of every room, edits, deletes, reactions | push (the platform adapters) | yes, `nora/ingest/pipeline.py` |
| **presence** | fixes (stays and arrivals are Rollup, [25](25-rollup.md)) | poll (the collector) | yes, `nora/presence/collector.py` |
| **social** | members' stories, posts and reels; follows between known accounts; tags and mentions; comments on members' posts | **poll** (Instagram does not push these) | reads only on request today |
| **play** | who is in which game, who is in which voice channel with whom | poll (Steam) and push (Discord voice and activity) | live Steam reads only (`nora/infra/steam.py`); [25](25-rollup.md) §6 |
| **perception** | what a media item shows, and whose faces are in it, stored on that item with a confidence | follows each new media item | `media_fetch` on request; faces: [60](60-faces.md) |

**On scheduling** (operator: "should it be scheduled?"): a source that cannot
push is polled — stories disappear in 24 hours, so the social poller runs at
least every 12 hours, and the presence collector already polls. That is
ingestion's clock, not cognition's. Cognition has no schedule of its own: it
runs only when signals wake it (§3), and `time` signals are the only ones a
clock raises.

## 2. The social stream

The social graph is the relationships and the activity between the unit's
known people, as observed:

- **items**: each member's current story frames, posts and reels, with time,
  caption, alt text, location tag, and who is tagged or mentioned;
- **edges**: follows between known accounts (the bridge already reads
  `following`), co-tags, mentions, comments — each an observation with a time,
  never a conclusion ("A and B are close" is Character, [50](50-character-and-standing.md));
- **reach: members only** (operator 2026-09-28): accounts the identity file
  maps to a known person of the unit. An account a member tags is recorded as
  a handle on that member's item and is never itself read. Nothing is
  crawled.

Each new item is perceived once (§1) — a vision reading and a face match — and
the perception is stored on the item, so cognition reads a described story,
not a URL.

This reverses the operator's 2026-09-24 rule that a story is opened only when
a person asks (design/instagram-adoption/18-instagram-read-and-search.md, the
section of that name) and rewrites its test,
`test_a_story_is_never_opened_in_a_turn_nobody_asked_for`, to pin the new
rule: stories are read by the ingestion poller, for members only. R-98 (a
story is read only for a Tier 0 person) is kept: members are Tier 0.

**What it needs from the bridge.** Stories by account exist
(`/accounts/{igid}/story`). Posts and reels have no mechanism yet (design
instagram-adoption P13). A follows list is not an endpoint today. Each is a
bridge change measured on its own. The poller is rate-capped: it reads through
the session Nora's messaging uses, and losing that session loses her
Instagram presence entirely.

## 3. Signals

Every ingested item, every Rollup change of §3's kinds, and every `time`
trigger raises a **signal**:
`(unit, kind, ref, cues, at)`.

- **kind**: `message`, `social` (a new item or edge), `play` (a sample),
  `rollup` (a co-location starts, a play session together starts, a
  **deviation** — a person's pattern broke, [25](25-rollup.md) §4), `time` (an
  anchor passed, a revisit due, an event's date reached, the daily floor at
  HK 00:00). Presence raises its signals through the Rollup: a raw fix is too
  fine-grained to wake anything.
- **cues** (deterministic, never a model): a date or time expression; an
  amount or a split; a keep instruction («記低», "keep that", "write that
  down"); an RSVP word; a reply to, edit of or delete of a message a Register
  row cites; a place name or a place link; an arrival at a place an open
  event names.
- An item whose author is Nora raises no signal.

A unit's signals collect until the first of: the unit's rooms quiet for
`quiet_seconds`; `batch_max` signals pending; a strong cue plus
`cue_quiet_seconds` of quiet; the oldest pending signal `max_age_seconds` old.
Then one cognition session runs ([30](30-cognition.md)). Every default is
sized from a shadow week of measured wake reasons, never from one sample.

**Bounds**: one session per unit at a time; at most `max_concurrent_sessions`
across units (design/dreamer-agent-loop/06-trigger.md §6 required a global
bound; the current `dream_loop` has none); **a daily token budget per unit**
(a chatty unit cannot spend without limit); per-unit backoff after a failed
session, where "nothing to do" is its own outcome and never resets backoff.

## 4. Metrics

`nora_signal_total{kind,cue}`, `nora_cognition_wake_total{reason}`,
`nora_signal_pending_age_seconds`, `nora_social_items_total{kind}`,
`nora_social_poll_total{outcome}`, `nora_play_samples_total{source}`,
`nora_cognition_unit_tokens_total{unit}`.
