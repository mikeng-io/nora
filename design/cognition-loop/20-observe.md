---
type: design
title: "Observe — ingestion fills the Record and raises signals; only sources that cannot push are polled"
description: "The five streams (messages, presence, social, play, perception), what each records today and what is added, which are pushed and which polled, the social stream within members only, the durable signals and their cues, and the wake policy: one table of triggers, the watermark, the brakes, self-wakes Cognition requests for itself, and the wake rules of the slow adaptors. Absence is a signal only when every source it rests on is proven healthy."
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
| **messages** | every row of every room, with edits; reactions and deletes or unsends as items attached to the message they touch | push (the platform adapters) | rows and edits yes (`nora/ingest/pipeline.py`). Reactions and deletes or unsends were dropped until [70](70-migration.md) step 1, which records them as items (`message_reaction`, `message_delete`, `nora/ingest/stages/touch.py`), never as transcript lines, on all four platforms: the withdrawal cascade, the cited-message cues (§3) and the tie need them |
| **presence** | fixes, and every collector poll with its outcome | poll (the collector) | yes, `nora/presence/collector.py`; its polls recorded in `source_health` since step 1 |
| **social** | members' story frames; posts and reels once listable; tags, mentions and comments on members' items | **poll** (Instagram does not push these) | reads only on request today |
| **play** | who is in which game, who is in which voice channel with whom | poll (Steam) and push (Discord) | live reads only ([25](25-rollup.md) §6) |
| **perception** | what a media item shows, and whose faces are in it, stored on that item with its model's confidence | follows each new media item | room media: yes, at ingest (`[media] comprehension`; `nora/ingest/stages/media.py` spawns `nora/ingest/comprehension.py`, results in `media_objects`). Social items reuse it; faces are new ([60](60-faces.md)) |

**Source health is Record.** From step 1, every collector poll and every
platform adapter's up and down transitions are recorded, so "every source
healthy for the interval" (§3) is a query, not an assumption.

**Perception cost.** Every story frame and every reel is a vision call. The
social stream reuses ingest comprehension and its concurrency cap
(`DEFAULT_MAX_CONCURRENT = 4`), under a per-unit daily perception cap; an item
past the cap (starting at 200 vision calls per unit per day) is recorded
unperceived, which is a true state, not an error.

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
resolved to platform accounts through `identity.toml`. It is a subset of
R-98's trust Tier 0 (anyone listed in any group room Nora is in). Around it:

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
  and the curator's 00:00 HK), and `self` (a wake Cognition requested for
  itself with `wake_at`, below). Presence raises its signals through the
  Rollup: a raw fix is too fine-grained to wake anything.
- **cues** (deterministic, never a model): a date or time expression; an
  amount or a split; a keep instruction («記低», "keep that", "write that
  down"); an RSVP word; a reply to, edit of, reaction to or delete of a
  message a Register row cites; a place name or a place link; a surprise cue ("don't tell her", «唔好話俾佢知»),
  which makes `exclude_persons` mandatory on the write that cites it
  ([10](10-tiers.md) §3). An arrival at a place an open event names is not a
  cue on a message: the Rollup raises it as a signal ([25](25-rollup.md) §4).
- An item whose author is Nora raises no signal. A person's reply to her
  message within `reply_quiet_seconds` (starting at 600 s) does not by
  itself wake Cognition: it waits for the next wake.

**Absence is a signal only on healthy sources.** A deviation
([25](25-rollup.md) §4) is raised only when every source it rests on is
proven healthy for the interval: the collector's recorded polls succeeded,
and the adapters' recorded health shows the platforms up. Missing data from an outage is not absence of
activity (R-27). A deviation is then a signal like any other; no gate is
specific to it ([30](30-cognition.md) §3).

**Signals are durable.** They are rows in a per-unit `signal` table, not
memory: the Rollup raises them from the analytics process into nora, and
they survive a restart. **A `self` or `time` signal is pending only from its
due time**: the watermark, `batch_max` and max age count only due signals,
so a self-wake set for 19:40 does not hold the unit's queue open at 15:00.

### The wake policy

The operator's question (2026-09-28): what is the measurement — watermark,
threshold, timeout? It must not run on every message. The answer, as one
table. **Per message, code appends one row to the unit's `signal` table with
its cues, and nothing else: no model.** A code evaluator checks each unit
about every 30 seconds (one query; no model runs on this tick) and starts a
Cognition session on the **first** trigger that holds:

| trigger | fires when | starting value |
|---|---|---|
| **settled** | at least one signal pending, and the unit's rooms quiet for `quiet_seconds` | 600 s (`[dreamer] quiet_seconds` is 1800 today) |
| **strong cue** | a pending signal carries a strong cue (a date or time, a place change, a keep instruction, an RSVP, an edit of a cited message), then `cue_quiet_seconds` of quiet | 120 s |
| **rollup** | a Rollup signal (co-location start, a stay starting at a place an open event names, play-together start, deviation) has waited `rollup_delay_seconds` | 60 s |
| **batch** | pending signals ≥ `batch_max` | 80 |
| **max age** | the oldest pending signal ≥ `max_age_seconds` old | 3600 s |
| **time** | an anchor passed, a revisit due, an event's date reached, the daily floors (07:00 HK and 00:00 HK) | at the time |
| **self** | a self-wake Cognition requested is due (below) | at its time |

**Watermark.** Each unit has a durable signal watermark. A session consumes
the signals up to the watermark it reads at its start; signals that arrive
during it stay pending for the next session. The watermark survives a
restart, and a failed session does not advance it.

**Brakes, all in code:**

- single-flight per unit, and at most `max_concurrent_sessions` across units,
  starting at 2 (design/dreamer-agent-loop/06-trigger.md §6 required a global
  bound; the current `dream_loop` has none);
- a per-unit **cooldown** between sessions, starting at 300 s; a strong cue
  may cut it once;
- her own messages raise no signal, and a person's reply to her message
  within `reply_quiet_seconds` does not wake a session by itself;
- an empty agenda makes no model call;
- a daily token budget per unit, with a reserve held for strong cues and
  Rollup signals, so a chatty day cannot starve a co-location;
- per-unit backoff after a failed session, where "nothing to do" is its own
  result and never resets backoff.

**Expected volume.** The dinner afternoon ([40](40-register.md) §5) is about
5–7 sessions for the whole day; a quiet day, 1–2; never one per message.

**Every starting value is a guess.** Step 1 runs the evaluator in shadow for
a week (it logs each would-wake and its reason, fires nothing, and the old
clocks still run); the values are sized from that log, and again after step
2 adds Rollup signals. The keys live in a `[cognition]` section;
`quiet_seconds` and `max_age_seconds` reuse `[dreamer]`'s names, not its
values.

**As built (step 1, 2026-09-28; DEC-232).** Where the build fixed a value or
a name this table leaves open:

- `[cognition]` holds the table's keys under the names above plus `mode`
  (`off` shipped, `shadow`), `tick_seconds` (30), `time_floors`
  (`["07:00", "00:00"]`), `timezone`, `floor_grace_seconds` (3600: a floor is
  raised only while no more than this late, so a deploy at 15:00 does not
  raise that morning's 07:00) and `shadow_session_seconds` (60: how long a
  would-wake counts as a running session, so single-flight and the cooldown
  are exercised while nothing runs).
- `settled` and `strong cue` count observed items only (`message`, `social`,
  `play`); a pending `time`, `self` or `rollup` signal wakes under its own
  row, so a 07:00 floor is never recorded as `settled`. «Quiet» is measured
  from the unit's newest person-authored item, `waits` ones included.
- A strong cue is a date or time, a keep instruction, an RSVP, or a place
  LINK (standing in for «a place change» until place names have a
  gazetteer); an edit of a cited message waits for step 2's `citation`
  table, as do the other cited-message cues.
- «A strong cue may cut it once» is read as: a wake that cut a cooldown
  leaves a cooldown no cue can cut.
- A reaction on her message inside `reply_quiet_seconds` counts as a reply
  and is marked `waits`, like a quote of hers.
- The watermark is the unit's newest `cognition_wake` row in its mode; a
  signal is consumed when its id and its pending-since time are both at or
  under that row's.
- `nora_cognition_wake_total` carries `mode` beside `reason`, and
  `nora_cognition_wake_deferred_total{brake}` counts unit-ticks a brake held.

### Self-wakes: Cognition paces itself

The loop is woken by the world (signals, Rollup, time anchors) **and by her
own forecast of when to look next** (operator 2026-09-28). At the end of a
session she may call `wake_at(at, subject_rows, why)`: "the event is at
20:00, check at 19:40 whether the place moved"; "they said they would decide
after lunch, look at 14:30"; or, with nothing pending, no self-wake. It
writes a `self` signal due at `at`.

**A self-wake is not an act.** `remind` is outward: a message to a room,
through the fire path. `wake_at` is inward: it asks for her own next look at
the subject rows it names, and nothing leaves. The loop is therefore
self-paced: woken by the world and by her own forecast of when to look
next.

- **Fresh evidence, not old reasoning.** When a self-wake is due, code builds
  its agenda item from the subject rows as they are now, plus what changed
  since the wake was requested; the `why` is shown as the reason she asked,
  not as a conclusion.
- **She keeps, moves or cancels them.** `wake_move(wake_id, at, why)` and
  `wake_cancel(wake_id, why)` are versioned transitions, logged. Whenever a
  session's changes or incoming signals touch a subject with a pending
  self-wake (an event rescheduled, a note closed, an event cancelled), code
  puts that self-wake on the agenda beside the change ("pending self-wake W
  for this event at 19:40, requested because …; the event now reads …"), so
  she decides then. Code never cancels one itself; it only surfaces it.
- **Guards, in code**: at most `max_self_wakes` pending per unit (starting at
  5) and one per subject: a new self-wake that shares any subject row with a
  pending one supersedes it; a horizon no shorter than the unit's cooldown
  and no longer than 7 days (longer is a note's or an event's anchor); the
  same budget, single-flight and cooldown as any wake, but never the reserve
  held for strong cues and Rollup signals; a self-wake whose session writes
  nothing and acts on nothing is a **miss** against its subject, and after
  `self_wake_misses` (starting at 3) her self-wakes for that subject are
  refused for a day.
- **Measured** by the Assess adaptor: yield (the share of self-wakes that led
  to a write or an act), lateness against the moment it anticipated, and the
  share moved or cancelled before due.

### The slow adaptors and Assess

Each has its own per-key watermark and its own wake rule; none runs on a
fixed schedule, and none is woken by a single message, except a
`character_dispute`.

| adaptor | key | wakes when | starting value |
|---|---|---|---|
| **Character** | (unit, entity) | new evidence for that entity since its watermark (items it could cite: the entity's messages and social items, Rollup rows about it, Register rows naming it, a `character_dispute`) reaches `character_batch`; or its account is older than `character_stale_seconds` with any new evidence at all; a dispute wakes it at once | 200 items (Standing's `fold_batch` today); 7 days |
| **Lessons** | unit | `response` episodes ([25](25-rollup.md) §3) closed since its watermark reach `lessons_batch`; or `lessons_stale_seconds` with any new one; or a lesson passes its horizon ([12](12-schemas.md) §4a) | 30 episodes; 7 days; 60 days |
| **Assess** | a finished session; the unit, nightly | a session of any adaptor ends; the nightly clock | — |

The Character adaptor replaces the culture adaptor's daily clock
(`[culture] fold_interval_seconds = 86400`). The same week of shadow sizes
these values.

## 4. Metrics

`nora_signal_total{kind,cue}`, `nora_cognition_wake_total{reason}`,
`nora_signal_pending_age_seconds`, `nora_social_items_total{kind}`,
`nora_social_poll_total{outcome}`, `nora_play_samples_total{source}`,
`nora_perception_total{source,outcome}`,
`nora_cognition_unit_tokens_total{unit}`,
`nora_self_wake_total{transition}` (requested, fired, moved, cancelled,
superseded, refused).
