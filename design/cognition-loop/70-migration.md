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
| `DreamerRuntime.run` (`nora/agent/dreamer.py`), the Strands dream agent, `AdaptorBudget`, `DreamGuard`, `AdaptorGrant` and home-room resolution, `UNIT_SAFE_TOOLS` and `tool_subset_faults`, `RegistryToolSource`, telemetry | **kept**: they host the four adaptors ([00](00-intent.md) §3); `AdaptorGrant` gains the named write verbs by a new DEC (step 0) |
| `dream_loop`, `_dream_adaptor_once`, `seed_watermarks` (`nora/app/runtime.py`), `AdaptorSchedule`, per-adaptor clocks | **replaced** by the wake evaluator, the stimulus table and each adaptor's own watermark and wake rule ([20](20-observe.md) §3) |
| `note_candidates`, `note_curator`, `note_merge` (`nora/language/`) | **replaced by the Cognition adaptor** after cut-over; their objectives become its skills ([30](30-cognition.md) §4) |
| the proactive adaptor | **replaced by the Cognition adaptor**, whose act step it becomes; its writes to the shared decision log (DEC-179) become Cognition's |
| the ingest-triggered Standing fold (`nora/ingest/stages/standing_event.py`, deleted 2026-10-03, DEC-277) | **replaced by the Cognition adaptor**'s `standing_revise` (room case) |
| the `culture` adaptor (`nora/language/culture.py`) | **becomes the Character adaptor**, not retired: its room case at step 4, generalised to the unit, persons and Nora at step 8; its daily clock replaced by evidence thresholds and staleness |
| — | **the Lessons adaptor** (new, step 8) and **the Assess adaptor** (new, step 3) |
| each adaptor's envelope, `accept` and `commit` | **moved behind write verbs**: the validation survives, the per-adaptor envelope goes |
| the Standing debrief | **kept on the tag path**; it writes through `standing_revise` to the [12](12-schemas.md) §5 room schema |
| participation | **unchanged** ([30](30-cognition.md) §7) |

"Dreamer" stays the name of the runtime in code. It hosts four adaptors: the
fast Cognition adaptor runs the orient, decide and act steps of the ambient
loop (DEC-176); the Character and Lessons adaptors reflect on a slower
timescale; the Assess adaptor measures. Observe is ingestion and the Rollup
builder, code outside the Dreamer ([20](20-observe.md)). This refines the
single-agent decision and does not reverse it ([00](00-intent.md) §4).

## 2. The steps

Each step ships alone, behind a flag that defaults off, and is read back live
(`nora-readback`) before the next starts. **One exception, deliberate:** step
1b's history capture has no flag and is always on (DEC-232), because a
switch that can turn history off defeats the restore guarantee; its only
bypass is the session setting the snapshot's seeding and test resets use.

| # | step | ships | the reading that gates the next |
|---|---|---|---|
| 0 | **Tiers, words, grants — LANDED 2026-09-28** | DEC-217 for [10](10-tiers.md), [12](12-schemas.md), [15](15-map.md); DEC-218 granting the named write verbs through `AdaptorGrant` (write verbs are not act verbs) and `remind_move`/`remind_cancel` for Cognition's own reminders; each §3 amendment as its own row (DEC-219–DEC-230), and the missing index rows for R-31 and DEC-176; the unit read boundary settled (§4, DEC-231); GLOSSARY rewritten to [10](10-tiers.md)'s words, resolving the 14 conflicts below. Numbers as taken from `origin/master` when this landed; `nora-decision`'s numbering discipline may renumber them at merge. | — |
| 1 | **Stimuli, shadow — BUILT 2026-09-28, not yet deployed; renamed from `signal` 2026-09-29 (DEC-234)** | the durable `stimulus` table, the ingest stimulus stage, cues, the pending set, wake metrics; the wake evaluator in shadow (it logs each would-wake and its reason and fires nothing); reactions, edits and deletes or unsends ingested as items; collector polls and platform adapter health recorded; the clocks still run everything | a week of would-wakes and reasons; every wake value of [20](20-observe.md) §3 sized from it |
| 1b | **History and snapshot** | the append-only migration of every table in §5's inventory except the rows marked 2 or 2b, which migrate at those steps (system time, heads as views, loop state as transition rows; as built, DEC-232 amends "heads as views": the in-place tables stay the live heads and the chain is the trigger-written `row_history`, §5); doctrine, Dreamer-objective, config and deploy revisions recorded at load (§5); `nora snapshot --at T` (below) | a test writes, revises and retires across every store migrated here, then snapshots at two moments and gets exactly what was held at each; a recorded tag turn's blocks, rendered from a snapshot at its moment, equal the blocks its Langfuse trace recorded, with memory and the Live blocks (`tool_catalogue`, her platform presence, now) excepted and the transcript rendered through the media join as of T |
| 2 | **Rollup and lineage** — the Rollup half (2a) BUILT 2026-09-29, not yet deployed (DEC-235, [25](25-rollup.md) «As built»); lineage values are written on every Rollup row, the lineage half BUILT 2026-09-29, not yet deployed (DEC-236): the columns, backfill, predicates, the filter behind `[lineage] mode` shipped `off`, `record_query` behind `[record_query] enabled` shipped `false`; the `citation` table is still to come | the Rollup as a projection (logical watermark, `build`, citation by overlap), design 27's presence rollup moved onto the watermark; co-location, outing; daybook and tie partial ([25](25-rollup.md) §3); the [10](10-tiers.md) §3 columns, every lineage kind and its per-room predicate; the lineage backfill, then the filter; the `citation` edge table; `rollup_read`, `record_query`; wake defaults re-sized with Rollup stimuli | incremental and batch builds of each rollup agree exactly; no row with unknown lineage renders outside its origin room; a presence-lineaged field is refused in a room without the location flag and in one where DisclosureGuard fails. **Before `[lineage] mode = "on"`:** a note rendered in another room names the room it came from (DEC-225: "the origin room is shown with it"; not built), and a shadow week shows no same-room row withheld. **Before `[record_query] enabled = true`:** its three `tool_selection` cases run on both arms, with the eval subject's config enabling it |
| 2b | **Events unit-wide** | the event migration (§5); `event_revision`, the lifecycle statuses, per-field modes; the event verbs with `base_revision_id`; the tag path's five event tools renamed everywhere (§8); `event_search` | existing `Gathering` tests and the tag path's event cases green; `tool_selection` no worse |
| 2c | **Withdrawal cascade** (SUPERSEDED 2026-10-03: a delete only hides the message) | `unsupported` marking over the citation table, fed by step 1's deletes (a platform delete or unsend hides the message and runs the cascade over its EVIDENCE rows, notes, events and staged note proposals but not Standing or culture, which cite a watermark, in one transaction, `nora.app.delete_withdrawal`, DEC-276; `nora withdrawal reconcile-deletes` applies the same to the deletes recorded before that and runs at every start, 2026-10-02), by `nora person forget --apply`, and, when they exist, the PII scrub and a person leaving a unit (no caller yet); `nora person forget` and `unforget` (soft, §9) for the stores that exist | a cascade test withdraws one Record item and finds every row citing it `unsupported`, in every store; a forget followed by a snapshot before it restores everything |
| 3 | **Cognition, shadow** — split into three rows below because the row was too large for one PR to review or gate honestly: **3a** (the session, agenda and dry-run machinery), **3b** (the one live write path this step actually turns on), **3c** (measurement, off every live path). None depends on the others' code, so they ship in any order; each still gates on 3's own row above wherever this table is cited before the split. | — | — |
| 3a | **Cognition session, shadow** | the Cognition adaptor's session and agenda; its note, event and Standing verbs in dry run (would-write rows recorded, nothing committed); the pace verbs and self-wakes in shadow; skills injected; the act verb mirrored and held ([30](30-cognition.md) §2), read against the live proactive pass's decisions; `tool_selection` on the cognition catalogue | over a shadow week every clocked-adaptor write is matched or deliberately closed (a close the model wrote is not evidence: DEC-252, amended 2026-10-02); the act guards hold in shadow (the live pending ceiling and the dedupe rule; the live `remind` has no daily act cap, so none is checked) |
| 3b | **`standing_revise`, live — BUILT 2026-09-29 (DEC-239)** | `standing_revise` and the room Standing schema, live: the debrief and the ingest fold both write through it from here, so the head has one shape. As built: two new optional sections, `I hold open` and `I owe` (migration `0076_standing_sections`, additive, existing heads keep every fact and gain two `NULL` sections); the write verb enforces the schema (`Where it stands` required, a given section never blank) and the mode (`deduced`/`abduced`/`said`) before either caller's own body/working-set/screen checks reach the store; `mode="deduced"` by default, since neither caller yet asserts a position resting only on her own words (`said`) or a guess going out as a question (`abduced`) — open below. **The two new sections carry no model-written content yet**: no objective asks for them, so `hold_open`/`i_owe` stay `None` on every row this step writes; turning the model on for them is a doctrine change with its own eval, not part of this migration step (open position below) | the render-parity case: an existing head's rendered block is unchanged; the debrief and rollup-fold test suites stay green under the shared verb |
| 3c | **Assess, corpora** | the Assess adaptor, nightly data-quality checks and labelled corpora ([30](30-cognition.md) §6) | the truncated dinner case passes ([40](40-register.md) §5); the event and note corpora meet their gates |
| 4 | **Cut over — BUILT 2026-10-03 in one change (DEC-277; the operator's ruling: all at once, no shadow week, undo is `[cognition] enabled = false` and a restart)** | the Cognition adaptor commits; the clocks, note-candidates, note-curation, note-merge, the proactive adaptor and the ingest Standing fold retired (with `rollup_max_folds` and its metric); the culture adaptor becomes the Character adaptor's room case, on its wake rule, with culture migrated to the sectioned account and written by `character_revise` | the re-pointed adaptor eval suites green, master vs branch; the room-Character corpus meets its gate; the latency falsifier ([00](00-intent.md) §6) |
| 5 | **Social and play** | the story poller (members only, rate-capped, per-person off switch; source interface, adaptive cadence, Record tables and back-fill in [22](22-social-stream.md), built in its S0–S5), perceived through ingest comprehension under the per-unit cap; the story rule and its test rewritten; posts and reels as the bridge can list them; `social_items`; Steam polling and Discord rich presence and voice sampled; play session and play profile; daybook and tie rebuilt with the new inputs | poll outcomes and item counts read back; the login unharmed for a week; the dinner case with stories |
| 6 | **Faces** | detector, embedder, templates keyed by workspace; labels by reference photos, the portal and `face_label`; face co-appearance; the custody and face checks | precision on the held-out labelled set ([60](60-faces.md) §6) |
| 7 | **Memos** (moved: E1b, built right after step 3's shadow week as the first kind on the entry, gated on its corpus, written only by Cognition, DEC-249; the «記低» routing is a strong-cue hand-off, not a tag-path `memo_write`) | the memo table, `memo_write`/`memo_withdraw`/`memo_read`, `<memos>`, the «記低» routing, the key-shape exemption | the memo corpus meets its gates; `tool_selection` before and after; the dinner case whole |
| 8 | **Character, Lessons and Standing** (whole) | the Character adaptor generalised to the unit, persons and Nora; unit and person Standing, sectioned with per-section support and age; `character_read`, `standing_read`; the `self` block (her whole Character · Nora, capped, below the doctrine), and `self_card` out of the prompt; Honcho pushed as `explicit` plus the peer card; the correction portal and `character_dispute`; tension rows and their page; the rebuild-drift metric; **the Lessons adaptor**: `lesson_write`/`lesson_retire`, the `lessons` block, the size cap, the doctrine-conflict check, the portal page with rollback by id, Character · Nora's `What lands and what does not` moved into Lessons | a labelled set of accounts judged against the Record they cite; a labelled set of lessons (including ones that would loosen doctrine, which must become tension rows); the lesson drift and churn baselines |

**After the cut-over, read back live (DEC-279, DEC-280, 2026-10-03).** The
first day of step 4 measured three costs and one loss, each fixed without a
flag (undo is the revert): (1) a Rollup stimulus no longer wakes a session; it
rides the next wake, and the daily floors bound the wait ([20](20-observe.md)
§3); six of one unit's nine sessions had been a lone `deviation_anomaly` closed
`no_change`, and one person in two units woke two at once. (2) A
`deviation_anomaly` line says the stay's place and what is usual for the person
there ([30](30-cognition.md) §1). (3) A session whose every item closed
`no_change` or `covered`, with no other verb called, ends at the last close
instead of paying one more model call ([30](30-cognition.md) §5). (4) The
Character fold asks memory only about the people who wrote in the batch (eight
dialectic calls had been made where two people posted), and a misread reply
(two room accounts) holds its batch instead of consuming it, with a three-try
bound (`design/09-decision-index.md` DEC-280).

**The data model's convergence** ([05](05-data-model.md) §9, operator
2026-10-01, DEC-249). **E1a gates step 4**: the `citation` table widened to
events and every Notebook kind as citing stores and to Rollup keys as cited,
with a `part` column for per-field lineage; events brought into the cascade;
event lineage written from its field citations. **E1b: memo is the first kind
built on the entry**, right after step 3's shadow week (it moves up from step
7, row 7 below), gated on the memo corpus, and may land after step 4: the
entry store and chain, `about` and `links`, the generic verbs, the one View
builder serving `<memos>` and `memo_read`, written only by Cognition. **E1c is
a precondition of E2**: every tool call and its result, every status, keyed to
the turn, and her Instagram comment replies, recorded in the Record. **E2, one
writer per tier**: at or right after the cut-over the tag path's `note_create`,
`note_update` and event verbs and the debrief's Standing write move behind
Cognition, replaced by the `handoff` stimulus ([20](20-observe.md) §3), with
the tag tools' text and the «記低» doctrine changed under `tool_selection` and
the eval; `remember`, `remind` and `cancel_reminder` stay on the tag turn.
Character of a person, the unit and Nora, Lessons, and unit and person
Standing are born on the entry at step 8; culture, room Standing, events and
notes converge as each is next touched, under render parity measured after
the fire turn's withdrawal fix, their tables kept. **Step 4 needs**: E1a; E1c
and the `handoff` stimulus before the tag-path writes are withdrawn; the
grounding and the tag turn's blocks agreeing on withdrawal and lineage.
**Nothing live changes shape before its kind converges**: the debrief and
ingest fold through `standing_revise` (DEC-239), the tag path's note and event
verbs, the `<notes>` and `standing` blocks, history capture, the cascade.

**`nora snapshot --at T`** builds an eval sandbox holding Nora as of T. It
extends `nora/infra/sql/snapshot.py`, which today makes five reads for one
room at one instant (the transcript, standing through `read_as_of`, culture,
notes, and pending reminders through `_reminders`), omits cancelled
reminders because cancelling records no time, and reads four of a note's
loop columns as they are now (its own docstring and `nora/subject/sandbox.py`
list the gaps), to every store's as-of-T state, and seeds the per-case sandbox
database `nora/subject/sandbox.py` already clones from the blank template.
It adds the doctrine, skills, config and model route in force at T, and the
Rollup at T (rebuilt at watermark T once step 2 makes it replayable; before
step 2 the Rollup at T is not reconstructable, and the snapshot says so). A case pins T and replays a wake or a turn against Nora-at-T. **Memory is the
exception**: Honcho cannot be restored to T; the case runs on its own empty
Honcho workspace (`nora/subject/memory_workspace.py`, one per draw) and either
seeds memory explicitly or declares itself memory-free.

**Lessons ship at step 8**, though their evidence is ready earlier (reactions
at step 1, the assess pass at step 3): a lesson takes effect with no approval
step, so the portal page that shows and rolls it back, and the tension path
that catches one loosening doctrine, must exist first, and both ship at 8.

Steps 5 and 6 need step 1 (stimuli), step 2 (lineage kinds, rollup tables)
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
    lineage, reasoning, stimulus, cognition, event (code `Gathering`).

## 3. What this supersedes and amends

**Superseded:**

- **Design 39 and DEC-162** as a separate adaptor: its decide and act turns
  become cognition's act step. What survives of DEC-162: the home room (39
  D2), generalised to each agenda item's bound room; the decide/compose split, the per-room `proactive` flag, D11's screening (a row
  whose subject was retired or revised on the tag path is cancelled when
  claimed; Cognition may also move or cancel its own rows, [30](30-cognition.md) §2), silent recorded as done, the pending
  ceiling (D5), strikes.
- **The 2026-09-24 story rule** (design/instagram-adoption/18-instagram-read-and-search.md):
  stories are read by ingestion.
- **The 2026-09-21 social-read retention rule**, for the social stream only
  (design/instagram-adoption/06-retention-and-custody.md; operator 2026-10-16):
  a member's posts and reels are kept whole, media included, with no retention;
  of a story, what it said (metadata, caption, perception text, its image or
  video embedding) is kept and its media bytes are deleted at a bound
  ([22](22-social-stream.md) §5).
  A live read on the tag path stays ephemeral.
- **Design 43**: extended from three note passes to the whole loop, and its
  `WITHHELD_TOOLS` list (`nora/language/note_candidates.py`, deleted 2026-10-03, DEC-277) retired: every
  read tool is granted except the live social reads, and the budget is the
  control.
- **DEC-155's "spec §7.4"** (the Dreamer is not granted `where_been`) and its
  90-day cap, for the Dreamer's adaptors only: they read presence through
  `rollup_read`, under lineage ([25](25-rollup.md) §5).
- **Design 27's Tier 6** (co-location, speculative): built as a Rollup; design
  27's incremental path moved onto the logical watermark, and its rebuild by
  truncation replaced by versioned builds that are kept.
- **Standing "lossy and rewritten every turn"** (design/standing-compact/00-intent.md):
  the head stops being a lossy fold; its sections carry their own support
  and age, and hold undated positions only ([12](12-schemas.md) §5). The chain, `standing_revision`
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
| R-18 | extended to images (the face check). R-18 guards replies, and it still blocks a key-shaped value in a reply, one read from a memo included |
| R-28 | `route` refuses coordinates taken from a presence rollup |
| DEC-214, [50](50-character-and-standing.md) §5 | Character is per unit; the peer card, unscoped within its Honcho workspace, is never Character evidence |
| ARCHITECTURE §8 | "Background passes never speak" holds in form: cognition's one act is a reminder row the fire path speaks. "Every Dreamer write screened" gains one exemption: memo bodies skip `dream_screen`'s key-shape check (`nora/agent/dream_screen.py`, D5) and render normally. "Not governed yet" lists each gap this bundle opens until its step's DEC lands |
| DEC-176 | has no index row (it lives in design/ambient-loop/03-decisions.md); step 0 adds one |

**Kept**: the note brake (GLOSSARY **note**), widened ([10](10-tiers.md) §2
rule 4); DEC-213 (note body schema).

## 4. Open positions

| position | what would decide it |
|---|---|
| ~~the unit as the read boundary for transcripts of all its rooms~~ | **SETTLED at step 0** (DEC-231): the unit is the Cognition session's read boundary, transcripts of every room included (operator, 2026-09-28), extending design 39's home-room reads to the whole unit. Each agenda item's room-scoped reads still resolve through its own bound room (R-21, amended by DEC-223); the unit bounds the SESSION, the room bounds each ITEM's disclosure. The fallback this row named — one session per room instead of per unit — is not taken |
| granting cognition the live social reads (`instagram_*`, `x_*`, `linkedin_*`) | the operator; the risk is the messaging login |
| one agent on the runtime model (a cheap model) holding a busy unit's day | step 3's shadow: items closed per session, refusals per rule, context size |
| participation as a wake of the same loop | session latency measured after step 4 |
| a `delegate` verb for long work | the first backfill that needs it (faces, step 6) |
| listing posts and reels on the bridge (P13) | built and measured with the bridge |
| strands-agents 1.50.2 → 1.57.1 | its own PR, measured before and after |
| game-specific sources (CS2, Valorant, Apex) | after step 5; each a source feeding play samples |
| DEC numbers | taken at merge (`nora-decision`) |
| when culture moves onto the entry (step 4 or step 8); whether the entry's two-value basis replaces the six reasoning modes; the portal's events page and per-room visibility | the operator; [05](05-data-model.md) §10 |
| populating `I hold open` and `I owe` from the model (step 3b, DEC-239) | a doctrine change to `nora/dreamer/objectives/standing-debrief.md` asking for the two new JSON keys, gated by its own eval read-back (`AGENTS.md`, gate-prompt-changes-need-the-eval-suite) — not this migration step, which ships the schema, the write verb and the render but leaves both sections `None` on every row it writes |
| per-section `as_of`/`last_supported_at`/`support_count` on room Standing (12-schemas.md §8) | step 3b keeps these at the ROW level (unchanged from before this step); per-section age is deferred to whichever step gives Character the same columns (step 8), so room Standing gets it once rather than twice |
| `standing_revise`'s `mode` argument for the two live callers (step 3b) | both default `"deduced"` today (no caller yet asserts `abduced` or `said`); revisited once the debrief/rollup objectives are taught to pick a mode themselves, alongside the `I hold open`/`I owe` population row above |

## 5. Data migrations

All additive: no table is dropped (the rule the deprecated tiers set).

**The in-place inventory** (step 1b). Every table that updates or deletes in
place today, read from its store's code, and what it becomes under
[10](10-tiers.md) §8:

| table (store) | changes in place today | becomes |
|---|---|---|
| `messages` (`nora/infra/sql/audit.py`) | delivery status and `person_id` (adoption) updated; rows deleted by `prune_all` | status and adoption as transition rows; prune hides (§9) |
| `note_candidate` (`notes.py`) | occurrence counts upserted | occurrence rows, appended; retired with note-candidates at step 4 |
| `note` (`nora/infra/sql/notes.py`) | seven columns: head pointer, `strikes`, `revisit_at`, `last_wake_revision_id`, and the retirement trio (`nora/infra/sql/snapshot.py` lists them) | transition rows; the head derived from `note_revision` |
| `culture`, `standing` (`culture.py`, `standing.py`) | heads upserted over their chains | heads derived from `culture_revision` and `standing_revision` |
| `events`, `event_rsvps` (`gatherings.py`; `add_rsvp` upserts) | updated in place and upserted | `event_revision` and RSVP transition rows (**2b**) |
| `reminders` (`reminders.py`) | status, `fired_at`, outcome updated | transition rows |
| `compute_jobs`, `instagram_summons`, `version_announcements` | status updated | transition rows |
| `participation_window_state`, `dream_schedule_state`, `consolidation_watermark`, `analytics_watermark`, `tag_debounce_state` | upserted loop state | transition rows (the Dreamer's schedule and watermarks retire at step 4) |
| `media_objects` (`media_objects.py`, `media_descriptions.py`), `video_readings` | descriptions upserted ("a later describer may refresh it") | a perception row per model and prompt version |
| `presence_fixes` (`presence.py`) | deleted past retention | hidden past retention |
| `presence_places` (`presence.py`); `presence_stays`, `presence_trips`, corridors, derived places, `presence_occupancy`, `presence_place_rhythm` (`mobility.py`, `nora/analytics/rollup.py`) | stays and trips updated in place, occupancy and rhythm deleted and rebuilt, places deleted on rebuild | versioned builds, kept (**2**) |
| `music_queue`, `music_session`, `geocode_cache` | updated or upserted | transition rows; cache rows kept with `recorded_at` |

**As built at step 2a (2026-09-29, migration `0071_rollup_builds`).** The
rows marked 2: every presence Rollup table carries `build_id`, `lineage`
and `recorded_at` and, with `presence_places` and the new tables
(`rollup_build`, `rollup_fold_state`, the five unit rollups), keeps
`row_history` through 1b's trigger. A new build writes new rows and a
superseded build's rows are never touched again, so «rebuild by truncation»
is gone; within the current build a run's changes are in-place writes (a
relink, a label, a diffed unit rollup, occupancy diffed per bin) and are
versions in the chain. Existing rows became the `legacy` build, backfilled
into `row_history` at the migration. `presence_corridor_speed` (its writer
retired, design 27 §4.4 M2) is `NOT_RESTORED`, and `analytics_watermark`
is no longer written (the fold's position is `rollup_fold_state`).

**As built at step 1b (2026-09-28), the details this table left open.**
Every row not marked 2 or 2b is one mechanism, not one per row: the trigger
`nora_row_history()` (migration `0068_system_time_history`) appends each
INSERT, UPDATE and DELETE to a single `row_history` table (the row's key,
the whole row as JSONB, `recorded_at`, `superseded_at`, `superseded_by`) in
the same transaction, and a DELETE appends a `delete` version holding the
removed row. So "transition rows", "occurrence rows", "a perception row per
model and prompt version" and "prune hides" are all versions in that one
chain, and the live tables stay as they are: each head is the
compare-and-swap cache §8 allows, which is why no reader changed. The
heads of `note`, `culture` and `standing` are therefore NOT rebuilt as SQL
views; the revision tables stay their content chains, and the head's
history says which revision was head at T. Chosen over per-site appends
because a history that misses one of ~60 write statements is silently
incomplete, and over typed history tables because a JSONB image survives a
later column. `sticker_index`, an in-place cache the table above missed,
is covered too, and so is `message_edit`: written append-only, but a
prune deletes a message's edits by ON DELETE CASCADE with no store
asking, and only a row trigger sees that. The migration LOCKs every covered
table while it backfills (the presence collector and the analytics process
keep writing while `nora` restarts), and a BEFORE TRUNCATE trigger refuses
a TRUNCATE, which fires no row trigger, unless capture is switched off.
Versions are dated by the database clock when the write ran, not when its
transaction committed, so a restore at T can include a row whose
transaction was still open at T. History growth is read hourly as
`nora_row_history_rows` and `nora_row_history_bytes` per table. The
append-only tables a restore reads (`note_revision`,
`standing_revision`, `culture_revision`, `message_edit`, `decision_log`,
`note_merge_decision`) gained `recorded_at`. Hiding is the head DELETE the
stores already do plus the `delete` version; `unforget` (step 2c) re-inserts
from the chain. Existing rows were backfilled at their best known time (the
migration's docstring gives the column per table); a row deleted before
step 1b is gone. `nora.infra.sql.history` classifies every table in the
schema, and `tests/architecture/test_in_place_writes_keep_history.py`
fails when code gains an in-place write on a table that keeps no history.

**Authored and configured state.** Doctrine hot-reloads on mtime from the
host checkout, which collects unpushed commits, so a git sha is not enough:
each doctrine load writes a `doctrine_revision` row (its sha256, as `/health`
reports it, and its content, content-addressed). `config.toml`, `policy.toml`
and `identity.toml` live only on the host: the loader writes a
`config_revision` row (file, content hash, content, loaded at) at every
start. `nora/dreamer/` (the Dreamer's doctrine and objectives) is
bind-mounted and hot-reloads on mtime exactly like doctrine
(`docker-compose.yml`), so its loads are recorded the same way,
content-addressed. `nora/skills/` is in the image, so each start writes a
`deploy_revision` row (git sha, image id), matching what
`~/nora-backups/deploys.log` records by hand. The model id and route are
columns on each session.

As built at step 1b: `doctrine_revision` carries both corpora (`corpus` is
`doctrine` or `dreamer`), each load's files as `path -> sha256` with the
bodies in a content-addressed `revision_blob`, and for the doctrine the tag
audience's assembled `sha256` (the `/health` value). `config_revision` is
one row per file per start. `deploy_revision` records the baked manifest's
version, commit and build time when the image was stamped, and otherwise the
commit and build time every build now bakes in (`NORA_GIT_SHA`,
`NORA_BUILT_AT`: Dockerfile ARG to ENV, passed by the build scripts and
nora-deploy's compose build); `image_id` is read from `NORA_IMAGE_ID` when
the deployment sets it. A row naming no version, commit or image is
reported unavailable by the snapshot. Skills
are identified by that deploy row. The daemon writes all four
(`nora.app.revisions`); an eval subject records nothing.

- **Events.** `Gathering` gains a unit key; `conversation_id` becomes the
  origin room. Rows in different rooms of one unit with overlapping times and
  the same place merge into one event with both origins kept; other
  candidates are listed for the operator, never merged by guess. A new
  `event_revision` table, the lifecycle statuses, and a mode per field:
  `told` where a tag turn wrote the row, `abduced` otherwise. **As built
  (migration `0074_event_revision`, DEC-237):** `events` stays the head (a
  compare-and-swap cache over `event_revision`, 10 §8); `unit_id` is added
  and written from the tag path's own unit resolver (`_unit()`,
  `nora.agent.tools`, the same one `note_create` shares), but existing rows
  backfill to `''` — the database alone cannot resolve a room to a unit.
  **SUPERSEDED IN PART 2026-10-22 (Aikido high-triage, PR #188):** the
  read-side `room_ids` widening and the `nora events backfill-units` write
  side are deleted. A row's owner is its persisted `(unit_id, platform)`; a
  row with no stored platform is quarantined and is never attributed from the
  current `policy.toml` (migration `0127_event_platform`, which backfills
  nothing and preserves every row). The cross-room de-duplication of legacy
  per-room events into one row is still **not built here**: merging
  same-gathering rows from different rooms of a unit into one is a separate,
  unbuilt step this row still hands off rather than guesses at. `reasoning` and
  `lineage` follow `0073`'s row-level simplification (`lineage` sectioned by
  FIELD name, reusing `nora.domain.lineage.Lineage`); no writer states either
  yet (step 3 is the first). **Step 3's first writer (DEC-253, migration
  `0086_event_citations`, the E1a slice step 4 is gated on):** the event verbs
  carry `cited_ids`, `reasoning` and `confidence`, the `citation` table admits
  events, Rollup keys and a `part`, the store writes the edges and the
  per-field lineage, and the withdrawal cascade and every event reader honour
  `events.unsupported_at`. `event_search(place?, person?, interval?)`
  reads the unit when `unit_id` resolves and falls back to `list_events`'s
  old per-room scope otherwise (a choice the design left open).
- **Culture → sectioned Character.** Old revisions stay readable; the next
  revision is written to the schema.
- **Standing → sections.** The JSON working set becomes `Found, not yet
  given`; `standing_revision` continues unchanged.
- **Lineage backfill.** Computed from each existing row's citations; a row
  whose citations cannot be resolved gets origin-room-only lineage. **As
  built (migration `0073_lineage`, DEC-236):** a note revision rests on every
  room its cited `messages.id`s were said in, its first revision also on the
  room the note was raised in, and a later revision citing nothing, or citing
  a pruned message, gets `unresolved` (origin room only); a note is the union
  of its revisions. Standing and culture rows rest on their own room plus
  `unresolved` (their folds are shown the unit's note index uncited). `as_of`
  is the newest cited message's time, the time of the message a Standing fold
  read up to, or the write's own time.
- **`citation` edge table.** Filled from existing citations, then written with
  every revision.
- **New tables**: `stimulus`, `session` (with its agenda items, close reasons
  and self-wake transitions), `event_revision`, `memo`, `character`,
  `lesson`, `tension`, `assess_report`, face labels and templates, the
  transition tables of the inventory, `doctrine_revision` (doctrine and the
  Dreamer's objectives), `config_revision`, `deploy_revision`; `build` and
  system-time columns on every Rollup table.

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
  hard-deleted, only when storage forces it (§9), except a story frame's,
  deleted at its own bound ([22](22-social-stream.md) §5). Growth is read monthly
  from table sizes; history is kept as long as the Record it derives from.

## 7. Rollback and failure modes

**Rollback.** Every step is a flag defaulting off, and turning it off restores
the previous behaviour; migrations are additive. Step 2b's rename is a code
revert. Step 4, as built (DEC-277), deleted the retired writers outright
rather than keeping them for a green week: undo is `[cognition] enabled =
false` and a restart (the notebook then has no background writer until it is
turned back on), or a database backup. Step 5's poller
and step 6's matcher each have their own off switch.

| failure | what happens |
|---|---|
| database down | no session; ingestion fails as it does today; pending stimuli are rows, so nothing is lost once it returns |
| model down | the session fails, its stimuli are released, per-unit backoff; nothing is written |
| Honcho down | memory hints are absent; `character_revise` for a person is refused rather than written from partial evidence |
| analytics process down | no Rollup stimuli; the recorded polls show the gap, so no deviation is raised |
| bridge or login down | poll outcomes counted; no social stimuli; nothing inferred from the absence |
| perception cap reached | items recorded unperceived |
| process restart or `docker stop` | the process handles SIGTERM and SIGINT (`nora.__main__.run_until_stopped`, built 2026-10-02 as the Cognition cut-over's PR-7; before it a SIGTERM killed the process with no unwind, and a session in flight left its wake open for the rest of `session_timeout_seconds` and then counted as a failed session). New wakes stop; a session already running gets `SESSION_SHUTDOWN_GRACE_SECONDS` (30 s) to finish, then it is cancelled and records its wake `cancelled` with a `restart` detail, freeing its unit and returning its stimuli to pending. If the process was killed anyway, the next one releases every wake left open before it started, the same way, at boot (`WakeEvaluator.reclaim_orphans`). `cancelled` neither counts toward the failure backoff nor strikes a stimulus, so a restart never penalises a unit (DEC-244) |

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

> **SUPERSEDED 2026-10-03 (guard removal; AGENTS.md «A guard needs a consequence»).** `nora person forget`/`unforget`, the withdrawal cascade and `--clear-unsupported` are deleted (row 2c's marking, DEC-238, DEC-264 and DEC-276 are superseded). A platform delete hides the message and nothing else.

- **`nora person forget <id>` is a soft delete** (operator 2026-09-28). It
  **hides** ([10](10-tiers.md) §8) the rows authored by or about the person:
  their messages and social items, their templates and reference photos,
  perceptions naming them, and their own Character and Standing rows; it
  excludes them from every new derivation and Rollup build. Rows that only
  **cite** them become `unsupported` through the withdrawal cascade
  ([10](10-tiers.md) §2 rule 7). Reference photo files move to a hidden
  directory beside their folder that the loader skips, and their templates
  are flagged hidden in the template index. Everything stays in history, so `nora snapshot --at T`
  restores completely; `nora person unforget <id>` makes it visible again.
  **Documents** (ADR 0020, DEC-264, operator 2026-10-01): every document the person
  **created** is soft-deleted through the `DocumentStore` port (a `document_state` row, `set_by`
  `forget:<person>`), never one they only took part in. They stay forgotten: Nora and the portal
  cannot restore them and their titles leave her context; `unforget` does not restore them, only
  the operator, in the database.
  Memory cannot hide a row, so the person's conclusions and peer are deleted
  in each Honcho workspace they are in (memory is not restorable anyway).
  Langfuse is not read by Nora; its traces are left to Langfuse's retention,
  and the command reports how many name the person. A cascade test covers
  every store; each step that adds a store extends both.
- **Retention is soft too**: presence fixes and face data each have a
  retention key, set before the step that ingests them ships, and past it
  rows are hidden, not deleted. Social items have none (operator 2026-10-16,
  [22](22-social-stream.md) §5): posts and reels are kept whole, and a story
  keeps its rows. **Media blobs are the one exception**: photos are hidden
  like the rest until storage forces deletion, and a story frame's bytes are
  deleted at its own bound once perceived and embedded; either deletion
  leaves a tombstone (id, kind, time) that marks a restore incomplete for it.
  Post and reel bytes are kept like room media.
- **Prune is hiding**: `[store] prune_enabled` hides what it trims today.

## 10. Observability

- `nora_cognition_item_total{kind,outcome}`;
  `nora_cognition_corrected_total{tier}`; refusals by rule,
  `nora_cognition_refused_total{verb,rule}`; acts by result;
  `nora_cognition_unit_tokens_total{unit_kind}` (built 2026-10-02 with `outcome`
  for `reason` and `unit_kind`, the agenda's kind or `mixed`, for `unit`: a unit
  is a name, and no metric label is a room, a person or a unit); the stimulus
  metrics of [20](20-observe.md) §4; `nora_dq_failures_total{check}`;
  `nora_face_match_total{outcome}`; `nora_self_tension_total{doctrine_file}`;
  `nora_self_wake_total{outcome}` (created, moved, cancelled, refused, fired:
  built 2026-10-02, with `outcome` for `transition`); per-adaptor wakes and sessions;
  `nora_lesson_total{result}` (written, retired, refused by rule, rolled
  back).
- One Langfuse root per session, its model calls and tool calls as children.
> **SUPERSEDED 2026-10-03 (guard removal; AGENTS.md «A guard needs a consequence»).** `nora cognition health`, the five cognition alerts (`NoraRestartLoop`, `NoraErrorsRising`, `CognitionWakeStalled`, `StimulusBacklog`, `CognitionSessionsFailing`), their zero-primed counters and the wake-loop and process-start gauges are deleted. KEPT: the SIGTERM handler, the orphan-wake reclaim at boot (a real incident: 47 starts in 3.3 days) and the token columns on `cognition_wake_outcome` (0090), whose removal waits on the Cognition session rewrite.

- The operational floor (built 2026-10-02, PR-7 of the cut-over):
  `nora_cognition_wake_last_tick_seconds{mode}` (the wake loop's pulse, written from
  inside the loop; absent while `[cognition] mode` is off), `nora_process_start_time_seconds`
  (what a restart-loop alert counts changes of), `nora_cognition_wake_reclaimed_total`
  (wakes a previous process left open, released at boot). A session's input, output and
  cache-read tokens and its root trace id are durable on `cognition_wake_outcome`
  (migration 0090), because the token counter above is reset by every restart.
  `nora cognition health` reads the rows (sessions by day and outcome, pending
  stimuli, failure causes, tokens per session, refusals by rule, process starts) and
  exits 0 / 1 / 2 for PASS / FAIL / ERROR; five alerts read the series
  (`docs/observability.md`, `docs/runbook.md` «The cognition loop is not healthy»).

## Status / Checkpoint

**2026-09-28 — proposed; revised the same day after four independent audits
(architecture, terminology, grounding, privacy).** The operator's decisions D1–D12 are listed once, in [00](00-intent.md)'s
Status.

**Step 0 landed 2026-09-28**: DEC-217 (the canon, [10](10-tiers.md)/[12](12-schemas.md)/[15](15-map.md)),
DEC-218 (the write-verb grant), DEC-219–DEC-230 (the §3 amendments, one row
each), DEC-231 (the unit read boundary, settling §4), and the missing index
rows for R-31 and DEC-176 — all in `design/09-decision-index.md`. GLOSSARY.md
is rewritten to [10](10-tiers.md)'s words, resolving its 14 conflicts. Numbers
are as taken from `origin/master` when this branch was written and may be
renumbered at merge (`docs/maintainers/conventions.md` "Numbering discipline").

**Step 1b built 2026-09-28** (branch `mikeng-io/cognition-step1b-history`,
DEC-232, number to be re-read at merge): `row_history` and its trigger on
every §5 table not marked 2 or 2b, `recorded_at` on the append-only tables,
the four revision tables written at load, and `nora snapshot --at T` (dry
run by default; `--into` seeds an empty sandbox, `--files-out` writes the
doctrine, Dreamer corpus and config in force). Both gates are tests
(`tests/integration/test_snapshot_holds_what_was_held.py`,
`tests/integration/test_snapshot_renders_the_recorded_blocks.py`); the live
form of the block gate is `scripts/snapshot_block_equivalence.py`, not yet
run against a production trace. The details the design left open are in
§5's two "As built" paragraphs. Not in 1b: the eval runner's cases still
seed through the room snapshot (`sandbox_from_snapshot`); moving them onto
`sandbox_as_of` changes what every case sees and ships with its own eval
run.

**Step 1 built 2026-09-28** (branch `mikeng-io/cognition-step1-signals`, DEC-233,
migration `0069_cognition_signal`, on 1b's `0068`): the append-only `signal` table and the
ingest signal stage with deterministic cues (`nora/domain/cues.py`); the
shadow wake evaluator (`nora/app/wake.py`), which records every would-wake in
`cognition_wake` (`mode = 'shadow'`, also the shadow watermark) and fires
nothing; reactions and deletes/unsends ingested as Record items on Signal,
Instagram, Discord and WhatsApp (`message_reaction`, `message_delete`; the
WhatsApp bridge emits them from this step); presence collector polls and
adapter state transitions in `source_health`. Only the signals and the
evaluator sit behind `[cognition] mode`, shipped `"off"`; the reaction and
delete rows, the WhatsApp bridge's new event types, the presence poll rows
and the adapter transitions are always on (Record, acting on nothing). The
operator sets `"shadow"` at deploy and the gate — a week of would-wakes and
reasons — is read from `cognition_wake` and
`nora_cognition_wake_total{reason}`. Where the build fixed a value or a name,
[20](20-observe.md) §3's «As built» note says so. Not built at step 1: the
cited-message cues (they need step 2's `citation` table), place-name cues
(no gazetteer), and `time` signals for anchors, revisits and event dates (the
agenda reads those Register rows, step 3); the daily floors are the only
`time` producer. Nothing reads the signal table for a decision yet. Under
1b's system time, `signal`, `cognition_wake` and `source_health` are
append-only (`recorded_at`), and `message_reaction` and `message_delete`,
which cascade from `messages`, keep `row_history` like `message_edit`.

**Step 1's `signal` renamed to `stimulus` 2026-09-29** (branch
`mikeng-io/cognition-stimulus-rename`, DEC-234, migration `0070_stimulus_rename`,
on step 1's `0069`): "signal" collided with Signal, the platform Nora lives
on. Everywhere above that names step 1's build keeps its 2026-09-28 spelling
as the historical record of what shipped that day; from 2026-09-29 the table,
its indexes and constraints, `cognition_wake`'s `from_signal_id` /
`through_signal_id` / `signal_count` columns, every Python symbol, and the
metrics `nora_signal_total` / `nora_signal_pending_age_seconds` are `stimulus`
/ `stimuli` throughout the tree — see DEC-234 for the full old→new map. The
Signal platform itself (`nora/platform/signal/`, `signal-cli`, the `signal`
platform string, room platform names) is untouched: only the cognition-loop's
own concept moved.

**Step 3a built 2026-09-29** (branch `mikeng-io/cognition-step3a`, DEC-241,
migration `0078_cognition_session`, on step 3c's `0077_assess_report`): the
session and agenda, pure (`nora.domain.cognition_session`), and dry-run
versions of the note, event and Standing write verbs
(`nora.agent.cognition_dry_run`, `nora.domain.cognition_write`) — every call
runs the live verb's own validation and ends in exactly one
`CognitionDryRunStore.record_would_write`, never a live store's mutating
method. `[cognition] session_mode` (`off`/`shadow`, ships `off`, requires
`mode = "shadow"`) is the switch a live loop will read; none exists yet.
This row was split further than 3a/3b/3c into 3a-i (this build) and 3a-ii,
because wiring an actual session that starts on the wake evaluator's
would-wakes and calls a real model is its own scope: 3a-ii is the pace
verbs and self-wakes in shadow, the `remind` act mirrored and held and read
against the live proactive pass's decisions, and `tool_selection` on the
cognition catalogue — the rest of this table's own 3a row.

**Step 3a-ii built 2026-09-29** (DEC-242, #627): the session loop, the pace
verbs, the mirrored and held `remind`, `nora cognition compare`; unwired.

**Step 3a-iii built 2026-09-30** (DEC-244; migration `0080_cognition_outcome_cause`): the composition root
builds the shadow session under `[cognition] session_mode = "shadow"`
(off builds nothing), hands its hook to the wake evaluator and stops it at
shutdown; the session rides the Dreamer's model and takes its budget from
`[dreamer.budgets.cognition]`; its read tools are the catalogue's that exist
(`rollup_read`, `memo_read`, `character_read`, `standing_read` and
`social_items` are not built yet; `social_items` is built since 2026-10-17
as `social_read`, [15](15-map.md) §4); its instruction is
`nora/dreamer/objectives/cognition.md`; the tool_selection corpus runs through
`CognitionSessionRunner.probe`. Nothing has run against a live model: a
shadow week is the measurement, and the `tool_selection_cognition` eval runs
first.

**Step 3a gate instrument built 2026-10-01** (DEC-252; review B1, D5): `nora cognition compare
--since ISO [--until ISO] [--unit U] [--tolerance-minutes N] [--json]` reports, per tier and per
clocked writer (`note-candidates`, `note-curation`, `note-merge`, `event:tag`, `standing-debrief`,
`standing-ingest-fold`, `culture`, `proactive`, `proactive-decide`), matched / cognition-only /
clocked-only, with a clocked-only row EXPLAINED when a `no_change`, `waiting` or `covered` close
names it (same room, same tier, names the row's id, after the write, one close per row;
DEC-252), and since 2026-10-02 (below) that explanation is counted as `closed by code` only when the
store proves it (a row with a note or event id, a `covered` close, and a write of the same session
for that id; a row with no id is never closed by code), and otherwise as `closed self-attested`,
which is not evidence and stays in `open`; the JSON is `schema` 2 and has no `closed` key. The default tolerance is 65 minutes, derived from the hourly writers' cadence;
`event:tag` is the live tag path and is reported apart, in no verdict; a Standing pair is
`coincident`, not matched. An unreadable source is ERROR, never zero. It prints counts and does not
decide the gate; step 4 retires a writer only on its own line. Matching rules: the module
docstring of `nora.domain.cognition_shadow_compare`.

**Step 3a measurability fixes built 2026-10-02** (branch `mikeng-io/fix-shadow-measures`; an
independent audit of the live shadow data found it could not yet produce evidence the step 4
cut-over could be judged on). None of it changes a live behaviour; the session is still
shadow-only. (1) The measured model is no longer told it is measured: the objective's section
«Everything you write here is recorded and not committed» and every «in this run the write is
recorded, not committed» in a verb's text are gone (objective version 5), and a refusal detail names
no dry run and no number; a test scans every string the model can read (the instruction, the
prompt, each verb's description, each result, each refusal detail). (2) Caps stay in code: `wake_at`
no longer says «too many pending or too many today», the cooldown or seven days, and no refusal
detail states a limit. (3) The dry-run verbs make the live store's own refusals: a `note_update`
against a moved head, a retired note or one the unit does not hold is refused in the ledger (`stale_base`,
`note_retired`, `not_found`) and no longer recorded `would_commit`, and the model reads a moved head
as the live verb says it (`unavailable`, with no head id); a session's own earlier writes count (a
second note under the first's title is `duplicate_title`, a second revision on the same parent or
event head is stale, a move out of a terminal event status is refused); the event verbs already
checked existence and the base. `remind` makes the live tool's own checks from the same constants (a
moment that is past, nearer than `min_lead_seconds` or further than `max_horizon_days`, and Nora's
own pending rows against `max_pending_per_requester`, counted in the live store plus the mirrored
ones; the mirrored count is per UNIT while the live one is per requester, so across several units the
shadow can undercount by the other units' mirrored reminders, which is the conservative direction), and two of §3's act guards: the room's `proactive` flag and `abduced_must_ask` (by
`field_mode`, the field's own mode over the row's). There is NO daily act cap: the live `remind` has
none, so none is invented (a first build of this paragraph added two `[cognition]` keys for caps; the
review found they matched nothing live, and they are gone). A verb whose store or read fails answers
`unavailable` and never shows the exception.
(4) `nora cognition compare` is an ERROR, with no
tiers, for a window with no session; every report carries a coverage line (sessions, units, per day
in the `[cognition]` zone, wakes that never became a session, sessions with no skill injected); a close the model wrote is never
summed into `closed`: `closed by code` is a `covered` close whose own session recorded a
`would_commit` write of the same tier, room and id (and only for a row that has one), and every
other explaining close is `closed self-attested`, which the report says is not evidence; and every report says what `matched` does and
does not establish (the same kind and subject; not that the two say the same thing). (5) The metrics of §10 that
3a-iii had not shipped now exist: `nora_cognition_item_total{kind,outcome}`,
`nora_cognition_refused_total{verb,rule}`, `nora_cognition_unit_tokens_total{unit_kind}` and
`nora_self_wake_total{outcome}`, none with a room, person or unit as a label. (6) The shadow week still
injects no skill (`AgendaSkills(by_kind={})`, unchanged): the compare output says so, so a week of
base-objective sessions is never read as the full design.

Steps 4–8 remain design only.

**Step 4 built 2026-10-03 (DEC-277), in one change.** The Cognition session commits: each
write verb runs its validator (`parse_note_body`, the event tier's modes and citations,
`standing_revise`'s checks) and commits through the tier's own store, stamped
`cognition:<unit>` (a Standing revision `source = 'cognition'`); `remind` writes the
`kind="proactive"` reminder row the one fire path fires; two verbs, `note_retire` and
`note_merge` (`NoteStore.supersede`, `NoteStore.merge_into`), take the curator's and
note-merge's jobs, and the `time` floors are mapped, so a daily item asks for the notebook
review the note index now marks (`review="anchor_passed|revisit_due"`, `may_duplicate`).
Deleted, not switched off: the dry-run ledger and its tables (0091), the remind mirror,
`nora cognition compare`, `session_mode`/`mode` (now `[cognition] enabled`), and
note-candidates, the note curator, note-merge, the proactive decide pass and the ingest
Standing fold with their objectives, config keys, metrics and tests. Their judgement is in
`cognition-register` v2. The culture adaptor is unchanged (still its own clocked pass, not
yet the Character adaptor).

