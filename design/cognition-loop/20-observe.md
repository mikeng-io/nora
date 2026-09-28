---
type: design
title: "Observe — ingestion fills the Record and raises signals; only sources that cannot push are polled"
description: "The five streams (messages, presence, social, play, perception), what each records today and what is added, which are pushed and which polled, the social stream within members only, the durable signals, their cues and bounds, and the rule that absence is a signal only when every source it rests on is proven healthy."
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

| stream | records | arrives by | today |
|---|---|---|---|
| **messages** | every row of every room, with edits; reactions and deletes or unsends as items attached to the message they touch | push (the platform adapters) | rows and edits yes (`nora/ingest/pipeline.py`). Reactions are dropped on purpose (`nora/platform/signal/parser.py`: a reaction is metadata on another message, not a transcript line) and deletes are not ingested. Both are added at [70](70-migration.md) step 1, as items, never as transcript lines: the withdrawal cascade, the cited-message cues (§3) and the tie need them |
| **presence** | fixes, and every collector poll with its outcome | poll (the collector) | yes, `nora/presence/collector.py`; recording the polls is new |
| **social** | members' story frames; posts and reels once listable; tags, mentions and comments on members' items | **poll** (Instagram does not push these) | reads only on request today |
| **play** | who is in which game, who is in which voice channel with whom | poll (Steam) and push (Discord) | live reads only ([25](25-rollup.md) §6) |
| **perception** | what a media item shows, and whose faces are in it, stored on that item with its model's confidence | follows each new media item | room media: yes, at ingest (`[media] comprehension`; `nora/ingest/stages/media.py` spawns `nora/ingest/comprehension.py`, results in `media_objects`). Social items reuse it; faces are new ([60](60-faces.md)) |

**Perception cost.** Every story frame and every reel is a vision call. The
social stream reuses ingest comprehension and its concurrency cap
(`DEFAULT_MAX_CONCURRENT = 4`), under a per-unit daily perception cap; an item
past the cap is recorded unperceived, which is a true state, not an error.

**On scheduling** (operator: "should it be scheduled?"): a source that cannot
push is polled. Stories disappear in 24 hours, so the social poller runs at
least every 12 hours; Steam every 1–5 minutes per member; the presence
collector already polls. That is ingestion's clock, not cognition's.
Cognition has no schedule of its own: it runs only when signals wake it (§3),
and `time` signals are the only ones a clock raises.

## 2. The social stream

What is observed between the unit's known people:

- **items**: each member's current story frames, and posts and reels once the
  bridge can list them, with time, caption, alt text, location tag, and who is
  tagged or mentioned;
- **edges**: co-tags, mentions, comments — each a Record item with a time,
  never a conclusion ("A and B are close" is Character,
  [50](50-character-and-standing.md)). **Follows are not recorded**: the
  bridge's `following` is one boolean (does Nora follow this platform
  account), and no endpoint lists who a member follows.

**Membership is one function**: the listed persons of the unit's rooms,
resolved to platform accounts through `identity.toml`. It is the same set as
R-98's trust Tier 0 (every person in a room Nora is in). Around it:

- A platform account a member tags, or that comments on a member's item, is
  recorded as a handle on that item and never read. Nothing is crawled.
- A per-person off switch in `identity.toml` stops that person's items being
  read at all.
- A write or act whose only citations are non-member social text is refused
  ([30](30-cognition.md) §3): text a stranger wrote is the prompt-injection
  path.
- The live social read verbs (`instagram_*`, `x_*`, `linkedin_*`) stay
  withheld from cognition; granting them is an open operator decision
  ([70](70-migration.md) §4).

Each new item is perceived once (§1), so cognition reads a described story,
not a URL.

This reverses the operator's 2026-09-24 rule that a story is opened only when
a person asks (design/instagram-adoption/18-instagram-read-and-search.md, the
section of that name) and rewrites its test,
`test_a_story_is_never_opened_in_a_turn_nobody_asked_for`, to pin the new
rule: stories are read by the ingestion poller, for members only. R-98's
*who* is kept; its *only on request* is reversed ([70](70-migration.md) §3).

**What it needs from the bridge.** Stories by platform account exist
(`/accounts/{igid}/story`). Listing a member's posts and reels has no
mechanism yet (design instagram-adoption P13); a single-post read exists.
Each is a bridge change measured on its own.

**The login is at risk, and that is the operator's accepted risk.** The poller
reads through the login Nora's Instagram messaging uses; a ban or checkpoint
on it loses her Instagram presence entirely. The poller is rate-capped and
its outcomes counted (§4).

## 3. Signals

Every ingested item, every Rollup change of [25](25-rollup.md) §4's kinds,
and every `time` trigger raises a **signal**: `(unit, kind, ref, cues, at)`.

- **kind**: `message` (including an edit, reaction or delete), `social`,
  `play`, `rollup` ([25](25-rollup.md) §4), `time` (an anchor passed, a
  revisit due, an event's date reached, the daily floors: proactive's 07:00 HK
  and the curator's 00:00 HK). Presence raises its signals through the
  Rollup: a raw fix is too fine-grained to wake anything.
- **cues** (deterministic, never a model): a date or time expression; an
  amount or a split; a keep instruction («記低», "keep that", "write that
  down"); an RSVP word; a reply to, edit of, reaction to or delete of a
  message a Register row cites; a place name or a place link; an arrival at a
  place an open event names; a surprise cue ("don't tell her", «唔好話俾佢知»),
  which makes `exclude_persons` mandatory on the write that cites it
  ([10](10-tiers.md) §3).
- An item whose author is Nora raises no signal. A person's reply to her
  message within `reply_quiet_seconds` does not by itself wake cognition: it
  waits for the next wake.

**Absence is a signal only on healthy sources.** A deviation
([25](25-rollup.md) §4) is raised only when every source it rests on is
proven healthy for the interval: the collector's recorded polls succeeded,
the platforms were up. Missing data from an outage is not absence of
activity (R-27). A deviation is then a signal like any other; no gate is
specific to it ([30](30-cognition.md) §3).

**Signals are durable.** They are rows in a `signal` table, not memory: the
Rollup raises them from the analytics process into nora, and they survive a
restart. A session claims the signals it runs on and releases them if it
fails.

**Wake.** A unit's signals collect until the first of: the unit's rooms quiet
for `quiet_seconds`; `batch_max` signals pending; a strong cue plus
`cue_quiet_seconds` of quiet; the oldest pending signal `max_age_seconds` old.
Then one cognition session runs ([30](30-cognition.md)). These are
cognition's own keys: `[dreamer] quiet_seconds` is 1800 today, which alone
would miss the ten-minute latency falsifier ([00](00-intent.md) §6). Every
default is sized from a shadow week of measured wake reasons, never from one
sample, and re-sized after step 2 adds Rollup signals.

**Bounds**: one session per unit at a time; at most `max_concurrent_sessions`
across units (design/dreamer-agent-loop/06-trigger.md §6 required a global
bound; the current `dream_loop` has none); **a daily token budget per unit**,
with a reserve held for strong cues and Rollup signals so a chatty day cannot
starve a co-location; per-unit backoff after a failed session, where "nothing
to do" is its own outcome and never resets backoff.

## 4. Metrics

`nora_signal_total{kind,cue}`, `nora_cognition_wake_total{reason}`,
`nora_signal_pending_age_seconds`, `nora_social_items_total{kind}`,
`nora_social_poll_total{outcome}`, `nora_play_samples_total{source}`,
`nora_perception_total{source,outcome}`,
`nora_cognition_unit_tokens_total{unit}`.
