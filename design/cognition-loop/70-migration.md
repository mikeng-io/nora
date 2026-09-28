---
type: design
title: "Migration — inside the Dreamer runtime, in shippable steps"
description: "What of the Dreamer runtime is kept and what is replaced; the build order with the reading that gates each step, the snapshot command and the GLOSSARY conflicts step 0 resolves; every decision this supersedes or amends; open positions; data migrations, including the inventory of tables that change in place today; the cost model; rollback and failure modes; tag-path changes; privacy operations (soft forget, retention, prune); observability."
tags: [nora, migration, dreamer, refactor, shadow, eval]
timestamp: '2026-09-28'
edges:
  - {dst: 00-intent, rel: realizes, provenance: asserted}
---

# 70 · Migration

## 1. Inside the Dreamer, not beside it

The operator asked whether this is a great refactor or stays within the
Dreamer. It stays within: the runtime that makes a background agent safe is
kept, and what is replaced is how it is woken and how its work is divided.

| part | fate |
|---|---|
| `DreamerRuntime.run` (`nora/agent/dreamer.py`), the Strands dream agent, `AdaptorBudget`, `DreamGuard`, `AdaptorGrant` and home-room resolution, `UNIT_SAFE_TOOLS` and `tool_subset_faults`, `RegistryToolSource`, telemetry | **kept**: the cognition session runs on them; `AdaptorGrant` gains the named write verbs by a new DEC (step 0) |
| `dream_loop`, `_dream_adaptor_once`, `seed_watermarks` (`nora/app/runtime.py`), `AdaptorSchedule`, per-adaptor watermarks | **replaced** by signals and the unit session ([20](20-observe.md)) |
| the ingest-triggered Standing fold (`nora/ingest/stages/standing_event.py`) | **replaced** by cognition's `standing_revise` (room case) |
| each adaptor's envelope, `accept` and `commit` | **moved behind write verbs**: the validation survives, the per-adaptor envelope goes |
| the four orient adaptors (`note_candidates`, `note_curator`, `note_merge`, `culture` in `nora/language/`) | **retired** after cut-over; their objectives become skills ([30](30-cognition.md) §4) |
| the proactive adaptor | **becomes the act step**; its writes to the shared decision log (DEC-179) become the act step's |
| the Standing debrief | **kept on the tag path**; it writes through `standing_revise` to the [12](12-schemas.md) §5 room schema |
| participation | **unchanged** ([30](30-cognition.md) §7) |

"Dreamer" stays the name of the runtime in code. What it runs is cognition:
the orient, decide and act steps of the ambient loop (DEC-176); observe is
ingestion ([20](20-observe.md)).

## 2. The steps

Each step ships alone, behind a flag that defaults off, and is read back live
(`nora-readback`) before the next starts.

| # | step | ships | the reading that gates the next |
|---|---|---|---|
| 0 | **Tiers, words, grants** | a DEC for [10](10-tiers.md), [12](12-schemas.md), [15](15-map.md); a DEC granting the named write verbs through `AdaptorGrant` (write verbs are not act verbs); each amendment of §3 as a decision row, and index rows for R-31 and DEC-176, which have none; the unit read boundary settled (§4); GLOSSARY rewritten to [10](10-tiers.md)'s words, resolving the conflicts below | — |
| 1 | **Signals, shadow** | the durable `signal` table, the ingest signal stage, cues, the pending set, wake metrics; reactions, edits and deletes or unsends ingested as items; collector polls and platform adapter health recorded; the clocks still run everything | a week of wake reasons; defaults sized from it |
| 1b | **History and snapshot** | the append-only migration of every table in §5's inventory (system time, heads as views, loop state as transition rows); `doctrine_revision`, `config_revision` and `deploy_revision` recorded at load (§5); `nora snapshot --at T` (below) | a test writes, revises and retires across every store, then snapshots at two moments and gets exactly what was held at each; a recorded tag turn's blocks, rendered from a snapshot at its moment, equal the blocks its Langfuse trace recorded, memory blocks excepted |
| 2 | **Rollup and lineage** | the Rollup as a projection (logical watermark, `build`, citation by overlap), design 27's presence rollup moved onto the watermark; co-location, outing; daybook and tie partial ([25](25-rollup.md) §3); the [10](10-tiers.md) §3 columns, every lineage kind and its per-room predicate; the lineage backfill, then the filter; the `citation` edge table; `rollup_read`, `record_query`; wake defaults re-sized with Rollup signals | incremental and batch builds of each rollup agree exactly; no row with unknown lineage renders outside its origin room; a presence-lineaged field is refused in a room without the location flag and in one where DisclosureGuard fails |
| 2b | **Events unit-wide** | the event migration (§5); `event_revision`, the lifecycle statuses, per-field modes; the event verbs with `base_revision_id`; the tag path's five event tools renamed everywhere (§8); `event_search` | existing `Gathering` tests and the tag path's event cases green; `tool_selection` no worse |
| 2c | **Withdrawal cascade** | `unsupported` marking over the citation table, fed by step 1's deletes, the PII scrub and a person leaving a unit; `nora person forget` and `unforget` (soft, §9) for the stores that exist | a cascade test withdraws one Record item and finds every row citing it `unsupported`, in every store; a forget followed by a snapshot before it restores everything |
| 3 | **Cognition, shadow** | the session and agenda; the note, event and room-Character verbs in dry run (would-write rows recorded, nothing committed); skills injected; `standing_revise` and the room Standing schema, live: the debrief and the ingest fold both write through it from here, so the head has one shape; the act verb mirrored and held ([30](30-cognition.md) §2), read against the live proactive pass's decisions; the assess pass, nightly data-quality checks and labelled corpora ([30](30-cognition.md) §6); `tool_selection` on the cognition catalogue | the truncated dinner case passes ([40](40-register.md) §5); the event, note and room-Character corpora meet their gates; over a shadow week every clocked-adaptor write is matched or deliberately closed; the act caps and dedupe hold in shadow |
| 4 | **Cut over** | cognition commits; the clocks, the four orient adaptors and the ingest Standing fold retired (with `rollup_max_folds` and its metric); proactive is the act step; culture migrated to the sectioned account and written by `character_revise` from here | the re-pointed adaptor eval suites green, master vs branch; the latency falsifier ([00](00-intent.md) §6) |
| 5 | **Social and play** | the story poller (members only, rate-capped, per-person off switch), perceived through ingest comprehension under the per-unit cap; the story rule and its test rewritten; posts and reels as the bridge can list them; `social_items`; Steam polling and Discord rich presence and voice sampled; play session and play profile; daybook and tie rebuilt with the new inputs | poll outcomes and item counts read back; the login unharmed for a week; the dinner case with stories |
| 6 | **Faces** | detector, embedder, templates keyed by workspace; labels by reference photos, the portal and `face_label`; face co-appearance; the custody and face checks | precision on the held-out labelled set ([60](60-faces.md) §6) |
| 7 | **Memos** | the memo table, `memo_write`/`memo_withdraw`/`memo_read`, `<memos>`, the «記低» routing, the key-shape exemption | the memo corpus meets its gates; `tool_selection` before and after; the dinner case whole |
| 8 | **Character and Standing** (whole) | person, unit and Nora Character; unit and person Standing, sectioned with per-section support and age; `character_read`, `standing_read`; the `self` block (her whole Character · Nora, capped, below the doctrine), and `self_card` out of the prompt; Honcho pushed as `explicit` plus the peer card; the correction portal and `character_dispute`; tension rows and their page; the rebuild-drift metric; **Lessons**: `lesson_write`/`lesson_retire`, the `lessons` block, the size cap, the doctrine-conflict check, the portal page with rollback by id, Character · Nora's `What lands and what does not` moved into Lessons | a labelled set of accounts judged against the Record they cite; a labelled set of lessons (including ones that would loosen doctrine, which must become tension rows); the lesson drift and churn baselines |

**`nora snapshot --at T`** builds an eval sandbox holding Nora as of T. It
extends `nora/infra/sql/snapshot.py`, which today reconstructs four stores
(transcript, standing, culture, notes) at one instant and leaves reminders
and loop state empty (its own docstring and `nora/subject/sandbox.py` list the
gaps), to every store's as-of-T view, and seeds the per-case sandbox
database `nora/subject/sandbox.py` already clones from the blank template.
It adds the doctrine, skills, config and model route in force at T, and the
Rollup at T (rebuilt at watermark T once step 2 makes it replayable; read
from the stored builds before then). A
case pins T and replays a wake or a turn against Nora-at-T. **Memory is the
exception**: Honcho cannot be restored to T; the case runs on its own empty
Honcho workspace (`nora/subject/memory_workspace.py`, one per draw) and either
seeds memory explicitly or declares itself memory-free.

**Lessons ship at step 8**, though their evidence is ready earlier (reactions
at step 1, the assess pass at step 3): a lesson takes effect with no approval
step, so the portal page that shows and rolls it back, and the tension path
that catches one loosening doctrine, must exist first, and both ship at 8.

Steps 5 and 6 need step 1 (signals), step 2 (lineage kinds, rollup tables)
and the act verb (step 4); their ingestion may be built from step 2 on, in
shadow. Perception reuses ingest comprehension throughout
([20](20-observe.md) §1).

**The GLOSSARY conflicts step 0 resolves** (GLOSSARY.md line numbers as of
this revision; this bundle does not edit GLOSSARY):

1. persona-sense "character", and "group" for a room or unit (:25);
2. "account" as a platform login (:35) → "platform account";
3. "Memory" as three layers including Postgres, with `search_memory` misfiled
   (:47–61);
4. the Dreamer as a "background tier … never speaks", and "units are
   adaptors" (:105–113);
5. culture holding "her own standing", keyed `primary_id` (:128–136);
6. note "of a group", and "note tier" (:144, :155);
7. "rooms' sessions" → Honcho sessions (:167);
8. two divergent Standing entries, "keeps nothing", "stance" (:190–263);
9. superseded proactive and ambient-loop claims (:216–229, :297);
10. "persona names a character" (:268);
11. "a tier … a cognition layer" (:394);
12. "conversation — the unit" and "scope" (:452, :462);
13. the peer card "returned null in this deployment" (:93–95);
14. missing entries: Record, Rollup, Register, memo, episode, aggregate,
    lineage, reasoning, signal, cognition, event (code `Gathering`).

## 3. What this supersedes and amends

**Superseded:**

- **Design 39 and DEC-162** as a separate adaptor: its decide and act turns
  become cognition's act step. What survives of DEC-162: the home room (39
  D2), generalised to each agenda item's bound room; the decide/compose split, the per-room `proactive` flag, cancellation
  only by a tag-path revision (D11), silent recorded as done, the pending
  ceiling (D5), strikes.
- **The 2026-09-24 story rule** (design/instagram-adoption/18-instagram-read-and-search.md):
  stories are read by ingestion.
- **Design 43**: extended from three note passes to the whole loop, and its
  `WITHHELD_TOOLS` list (`nora/language/note_candidates.py`) retired: every
  read tool is granted except the live social reads, and the budget is the
  control.
- **DEC-155's "spec §7.4"** (the Dreamer is not granted `where_been`) and its
  90-day cap, for cognition only: cognition reads presence through
  `rollup_read`, under lineage ([25](25-rollup.md) §5).
- **Design 27's Tier 6** (co-location, speculative): built as a Rollup; design
  27's incremental path moved onto the logical watermark, and its rebuild by
  truncation replaced by versioned builds that are kept.
- **Standing "lossy and rewritten every turn"** (design/standing-compact/00-intent.md):
  the head stops being a lossy fold; its sections carry their own support
  and age, and hold undated positions only. The chain, `standing_revision`
  (migration 0059, #352), is kept as it is ([50](50-character-and-standing.md) §2).
- **This session's first draft** (commit 0e060cf, a conductor over specialist
  agents).

**Amended**, each by its own line in the decision index at step 0:

| rule | amendment |
|---|---|
| R-1 | cognition sends nothing; its acts are reminder rows the fire path sends. Ambient traffic reaches a background session as Record it reads, and that session cannot send |
| R-1b | unchanged: three senders since DEC-212 (tag path, fire path, public summons); cognition adds none |
| R-98 | *who* kept: the unit's roster (the listed persons of its rooms) is a subset of trust Tier 0 (anyone listed in any group room); *only on request* reversed: the poller reads members' stories |
| R-31, DEC-79 | the live DisclosureGuard re-runs at render for every presence-lineaged field; DEC-79's room `location` flag is the `presence` predicate. R-31 has no index row today; step 0 adds one |
| R-21 | an agenda item's room-scoped reads resolve through its bound room |
| R-33 | a face crop goes only to the room its media came from; every image she sends passes the custody and face check |
| R-49 | extended to derived rows: `message:A` renders in room B only when B's listed persons are a subset of A's and B lists no `*` person; the origin room is shown |
| R-18 | extended to images (the face check). R-18 guards replies, and it still blocks a key-shaped value in a reply, one read from a memo included, unless the operator rules otherwise |
| R-28 | `route` refuses coordinates taken from a presence rollup |
| DEC-214, [50](50-character-and-standing.md) §5 | Character is per unit; the peer card, unscoped within its Honcho workspace, is never Character evidence |
| ARCHITECTURE §8 | "Background passes never speak" holds in form: cognition's one act is a reminder row the fire path speaks. "Every Dreamer write screened" gains one exemption: memo bodies skip `dream_screen`'s key-shape check (`nora/agent/dream_screen.py`, D5) and render normally. "Not governed yet" lists each gap this bundle opens until its step's DEC lands |
| DEC-176 | has no index row (it lives in design/ambient-loop/03-decisions.md); step 0 adds one |

**Kept**: the note brake (GLOSSARY **note**), widened ([10](10-tiers.md) §2
rule 4); DEC-213 (note body schema).

## 4. Open positions

| position | what would decide it |
|---|---|
| the unit as the read boundary for transcripts of all its rooms | the operator's 2026-09-09 ruling covers proactive; to be confirmed for cognition at step 0. If it is not, cognition runs one session per room instead of per unit, with the same verbs and agenda |
| granting cognition the live social reads (`instagram_*`, `x_*`, `linkedin_*`) | the operator; the risk is the messaging login |
| one agent on the runtime model (a cheap model) holding a busy unit's day | step 3's shadow: items closed per session, refusals per rule, context size |
| participation as a wake of the same loop | session latency measured after step 4 |
| a `delegate` verb for long work | the first backfill that needs it (faces, step 6) |
| listing posts and reels on the bridge (P13) | built and measured with the bridge |
| strands-agents 1.50.2 → 1.57.1 | its own PR, measured before and after |
| game-specific sources (CS2, Valorant, Apex) | after step 5; each a source feeding play samples |
| DEC numbers | taken at merge (`nora-decision`) |

## 5. Data migrations

All additive: no table is dropped (the rule the deprecated tiers set).

**The in-place inventory** (step 1b). Every table that updates or deletes in
place today, read from its store's code, and what it becomes under
[10](10-tiers.md) §8:

| table (store) | changes in place today | becomes |
|---|---|---|
| `messages` (`nora/infra/sql/audit.py`) | delivery status and `person_id` (adoption) updated; rows deleted by `prune_all` | status and adoption as transition rows; prune hides (§9) |
| `note` (`nora/infra/sql/notes.py`) | seven columns: head pointer, `strikes`, `revisit_at`, `last_wake_revision_id`, and the retirement trio (`nora/infra/sql/snapshot.py` lists them) | transition rows; the head a view over `note_revision` |
| `culture`, `standing` (`culture.py`, `standing.py`) | heads upserted over their chains | heads become views over `culture_revision` and `standing_revision` |
| `events` (`gatherings.py`) | updated in place and upserted | `event_revision` (step 2b) |
| `reminders` (`reminders.py`) | status, `fired_at`, outcome updated | transition rows |
| `compute_jobs`, `instagram_summons`, `version_announcements` | status updated | transition rows |
| `participation_window_state`, `dream_schedule_state`, `consolidation_watermark`, `analytics_watermark`, `tag_debounce_state` | upserted loop state | transition rows (the Dreamer's schedule and watermarks retire at step 4) |
| `media_objects` (`media_objects.py`, `media_descriptions.py`), `video_readings` | descriptions upserted ("a later describer may refresh it") | a perception row per model and prompt version |
| `presence_fixes`, `presence_places` (`presence.py`) | deleted past retention, and places deleted on rebuild | hidden past retention; places as versioned builds |
| trips, corridors, derived places, occupancy (`mobility.py`, `nora/analytics/rollup.py`) | updated in place, replaced, rebuilt by truncation | versioned builds, kept (step 2) |
| `music_queue`, `music_session`, `geocode_cache` | updated or upserted | transition rows; cache rows kept with `recorded_at` |

**Authored and configured state.** Doctrine hot-reloads on mtime from the
host checkout, which collects unpushed commits, so a git sha is not enough:
each doctrine load writes a `doctrine_revision` row (its sha256, as `/health`
reports it, and its content, content-addressed). `config.toml`, `policy.toml`
and `identity.toml` live only on the host: the loader writes a
`config_revision` row (file, content hash, content, loaded at) at every
start. Skills are in the image, so each start writes a `deploy_revision` row
(git sha, image id), matching what `~/nora-backups/deploys.log` records by
hand. The model id and route are columns on each session.

- **Events.** `Gathering` gains a unit key; `conversation_id` becomes the
  origin room. Rows in different rooms of one unit with overlapping times and
  the same place merge into one event with both origins kept; other
  candidates are listed for the operator, never merged by guess. A new
  `event_revision` table, the lifecycle statuses, and a mode per field:
  `told` where a tag turn wrote the row, `abduced` otherwise.
- **Culture → sectioned Character.** Old revisions stay readable; the next
  revision is written to the schema.
- **Standing → sections.** The JSON working set becomes `Found, not yet
  given`; `standing_revision` continues unchanged.
- **Lineage backfill.** Computed from each existing row's citations; a row
  whose citations cannot be resolved gets origin-room-only lineage.
- **`citation` edge table.** Filled from existing citations, then written with
  every revision.
- **New tables**: `signal`, `event_revision`, `memo`, `character`,
  `lesson`, `tension`, face labels and templates, the transition tables of
  the inventory, `doctrine_revision`, `config_revision`, `deploy_revision`;
  `build` and system-time columns on every Rollup table.

## 6. Cost model

Per unit per day: sessions × tokens per session, plus perception calls.
Nothing is measured yet; each term is measured before the budget it sets.

- **Sessions per day per unit**: step 1's wake counts.
- **Tokens per session**: the doctrine prefix is cached; injected skills sit
  after it in a fixed order and are paid uncached whenever the agenda's kinds
  differ from the last session's. Measured at step 3, with
  `cache_read_input_tokens` as the check on any claimed split.
- **Perception**: one vision call per story frame and per reel, through
  ingest comprehension's concurrency cap, under a per-unit daily cap.
  Measured at step 5. Faces are CPU, not model calls.
- The per-unit daily token budget and its reserve ([20](20-observe.md) §3)
  are sized from these readings, never before them.
- **History storage**: nothing derived is overwritten, so the database grows
  with every revision and transition. Revision bodies are short text; media
  blobs dominate, are hidden past retention and are the only thing
  hard-deleted, and only when storage forces it (§9). Growth is read monthly
  from table sizes; history is kept as long as the Record it derives from.

## 7. Rollback and failure modes

**Rollback.** Every step is a flag defaulting off, and turning it off restores
the previous behaviour; migrations are additive. Step 2b's rename is a code
revert. At step 4 the retired adaptors' code and clocks stay in the tree for
one green week, and flipping the flag back re-enables them. Step 5's poller
and step 6's matcher each have their own off switch.

| failure | what happens |
|---|---|
| database down | no session; ingestion fails as it does today; pending signals are rows, so nothing is lost once it returns |
| model down | the session fails, its signals are released, per-unit backoff; nothing is written |
| Honcho down | memory hints are absent; `character_revise` for a person is refused rather than written from partial evidence |
| analytics process down | no Rollup signals; the recorded polls show the gap, so no deviation is raised |
| bridge or login down | poll outcomes counted; no social signals; nothing inferred from the absence |
| perception cap reached | items recorded unperceived |

## 8. Tag-path changes

Each measured with `tool_selection` before and after (ADR 0015), in its
step's PR:

- **new blocks**: `memos`, `events`, `self`, `lessons`;
- **new tools**: `memo_write`, `memo_withdraw`, `memo_read`, `event_search`,
  `character_read`, `standing_read`, `face_label`, `character_dispute`;
- **changed**: `remind` gains `brief` for cognition's acts; it is the tool the
  tag path already holds, so the change is measured like a new one;
- **renamed**: `create_event`, `update_event`, `cancel_event`, `rsvp_event`,
  `list_events` → the event verbs and `event_search`, in tools, doctrine,
  skills, evals and tests at once (step 2b).

## 9. Privacy operations

- **`nora person forget <id>` is a soft delete** (operator 2026-09-28). It
  hides ([10](10-tiers.md) §8) every row about or authored by the person:
  their messages and social items, revisions and sections citing them,
  their templates and reference photos, perceptions naming them; it excludes
  them from every new derivation and Rollup build, and marks citing rows
  `unsupported`. Everything stays in history, so `nora snapshot --at T`
  restores completely; `nora person unforget <id>` makes it visible again.
  Memory cannot hide a row, so the person's conclusions and peer are deleted
  in each Honcho workspace they are in (memory is not restorable anyway).
  Langfuse is not read by Nora; its traces are left to Langfuse's retention,
  and the command reports how many name the person. A cascade test covers
  every store; each step that adds a store extends both.
- **Retention is soft too**: story items, other social items, presence fixes
  and face data each have a retention key, set before the step that ingests
  them ships, and past it rows are hidden, not deleted. **Media blobs are the
  one exception**: story frames, reels and photos are hidden like the rest
  until storage forces deletion; then the blob is hard-deleted and leaves a
  tombstone (id, kind, time) that marks a restore incomplete for it.
- **Prune is hiding**: `[store] prune_enabled` hides what it trims today.

## 10. Observability

- `nora_cognition_item_total{kind,outcome}`;
  `nora_cognition_corrected_total{tier}`; refusals by rule,
  `nora_cognition_refused_total{verb,rule}`; acts by outcome;
  `nora_cognition_unit_tokens_total{unit}`; the signal metrics of
  [20](20-observe.md) §4; `nora_dq_failures_total{check}`;
  `nora_face_match_total{outcome}`; `nora_self_tension_total{doctrine_file}`;
  `nora_lesson_total{outcome}` (written, retired, refused by rule, rolled
  back).
- One Langfuse root per session, its model calls and tool calls as children.

## Status / Checkpoint

**2026-09-28 — proposed; revised the same day after four independent audits
(architecture, terminology, grounding, privacy).** Operator confirmed: one
agent for orient, decide and act; events unit-wide and derived, cognition may
revise, confirm and cancel; no new statuses; stories, posts, reels and the
social stream ingested; faces now; passwords may be kept in memos; culture is
the Character of a collective; doctrine is law she never changes, in whole
or in part, and changes only when the humans all agree; her growth lives only
in her own blocks, below the doctrine, with any disagreement recorded as
tension for the operator; every Register, Character, Lessons and Standing row
is schema'd Markdown in a revision chain, and the Rollup is typed and rebuilt
into versioned builds.
Operator decisions, binding:

- **D1** Every act is a `remind()` row whose brief carries subject rows; the
  fire turn composes the words in the target room under its guards; no
  `speak` or `ask` path; named write verbs granted through `AdaptorGrant` by a
  new DEC at step 0.
- **D2** `told` = any person stated it in words, members agreeing among
  themselves included; `abduced` = concluded from evidence nobody stated; her
  own words are the mode `said`, Standing only.
- **D3** Deviation is a signal, not a rule; the only act guards are the
  general guards ([30](30-cognition.md) §3); deviation is raised only on
  sources proven healthy for the interval.
- **D4** Step 8 stays whole.
- **D5** Memo bodies are exempt from `dream_screen`'s key-shape check and
  render normally.
- **D6** Doctrine is the law: Nora never changes it, in whole or in part; it
  changes only when the humans all agree. Her growth lives only in her own
  blocks, below the doctrine; a disagreement becomes a tension row, never a
  proposal from her.
- **D7** Lessons: a tier of learned practice per unit, effective immediately
  with no approval step, every change visible on the portal and reversible
  by id.
- **D8** Everything is versioned in system time, so Nora can be restored as
  of any T for an eval, except memory (Honcho). Forget, retention and prune
  are soft (hidden, reversible); only a media blob hard-deleted for storage
  leaves a tombstone.

Nothing is built.
