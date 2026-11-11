---
type: design
title: "Cognition — one OODA loop"
description: "The Cognition loop is one background session per wake. It observes new stimuli, orients against memory and the Notebook, decides whether anything is worth doing, and acts through reminders and its own wakes. It holds the permitted reads, memory and profile proposals, Notebook and Standing writes, and the brakes code enforces; Assess measures the result."
tags: [nora, cognition, ooda, agent-loop, strands, write-verbs, proactive, skills]
timestamp: '2026-09-28'
edges:
  - {dst: 40-register, rel: depends_on, provenance: asserted}
  - {dst: 50-character-and-standing, rel: depends_on, provenance: asserted}
  - {dst: ../dreamer-agent-loop/39-proactive-ooda-loop.md, rel: supersedes, provenance: asserted}
  - {dst: ../unified-memory/27-the-memory-is-a-graph.md, rel: depends_on, provenance: asserted}
---

# 30 · Cognition

This node is the session's contract: what code hands it, the verbs it holds,
what code enforces and what Assess measures, budgets, and what stays outside.
The loop it serves, and why it is one session, is [00](00-intent.md). The
one-session ruling ([unified-memory/29](../unified-memory/29-one-cognition-session.md))
supersedes the step ①/②/③ wording that remains in the sections below: read
each step as the same session. [One Standing](../notebook-memory-types/20-standing.md)
owns Standing's input and completion; the tag-debrief writer wording in §7 is
history where it conflicts. Cognition is live (DEC-277).

> 2026-10-03 (DEC-277): the shadow period is over. The Cognition session writes live (`[cognition] enabled`) and is the one background writer of notes, events, Standing and her reminders. The clocked note/proactive adaptors, the shadow machinery and `[cognition] mode` / `session_mode` / `shadow_session_seconds` named here are deleted; wording below that describes them is the plan as written, not current behaviour. See `70-migration.md`.


## 0. One background loop

Cognition is Nora's other half: one session per wake that observes, orients,
decides and acts. It connects new stimuli to memory and the notebook, revises
what changed, and chooses whether anything deserves an action or another look.
Memory is a means to continuity alongside notebook layers, profiles, Standing,
reminders and self-wakes. Silence is a valid outcome.

The stages share one bounded session and the same committed stores. Searching,
writing and acting are available together; model judgment orders them. The
historical lane reads as of its evidence time and has no act verbs. Assess
remains outside the writer, and historical work never takes a live slot.

[00 §5](00-intent.md#5-per-item-machinery-is-deleted) (operator, 2026-11-11)
deletes the per-item `close` reasons, strikes and the quiet-close stop of §2 and
§5, and replaces the per-kind skills of §4. They run as built until that change
lands.

## 1. What code hands it

Built once per session, in code, before any model call:

- **The situation.** The Record items that raised the pending stimuli, each
  with its author, its room, **its age**, its cues and its perception (what a
  photo shows, whose faces, with confidence); the Rollup rows that raised
  stimuli; the index lines of the Register rows those items touch; the unit's
  clock in HK time; how long since the last session. Index lines, not
  bodies: Register, Character, Lessons and Standing bodies are pulled with
  read tools when the session needs them, and never pushed, except the
  capped `culture`, `self` and `lessons` blocks ([15](15-map.md) §3–4).
- **The agenda.** One item per thing that needs attention, computed by code
  from the stimuli: *room R has 23 new messages*; *event E's place is named
  in 3 messages since it was set*; *a co-location of 3 members began at a
  place at 20:05*; *P's week deviates from their rhythm*; *note N's anchor
  passed*; *4 new story frames from 2 members*; *a self-wake on event E is
  due*. An item states **what happened**, never what to conclude from it, and never a menu of possible actions. Code orders items
  by computed surprise ([80](80-prior-art.md) §4) and hands at most
  `max_items` (starting at 12); the rest wait for the next session.

**As built (the cognition-input PR, DEC-245): what the session is actually
handed.** Each of these was found by reading the input before the instruction
(AGENTS.md, «the spine»):

- **The clock line counts from when she last LOOKED**: the end of the unit's last
  session (`UnitView.last_session.ended_at_ms`), not the newest person message,
  which had made «you last looked» read «just now» about a room last looked at
  two hours before. No earlier session: «This is your first look at this group.»
- **A Rollup item is spelled out from its ref**: who (the roster name, resolved
  at read time and never serialized), what kind (co-location, arrival, silence,
  anomaly), the interval, and the place when the ref names one. **A Rollup
  stimulus whose ref cannot be read is not an agenda item** until `rollup_read`
  exists. Readability comes from the stimulus and the roster, never from what the
  grounder built (a readable rollup cut at `max_items` is DEFERRED like any item).
  A ref that does not parse is skipped, counted
  (`nora_cognition_stimuli_skipped_total`), recorded on the session row
  (`agenda.skipped_stimulus_ids`) and consumed. A person the roster does not know
  YET is not permanent: the stimulus is retained as a waiting carry
  (`agenda.retained_stimulus_ids`), so it never starts a wake, wedges nothing and
  is an item again once the roster knows the person.
  **A `deviation_anomaly` also says where and what is usual (DEC-279,
  2026-10-03).** The grounder reads the stay its ref names from the presence
  store (`stay_context_reader`): the place in the words `where_been` uses (the
  saved name, else «a place they go near <street>»), and how the stay differed
  from the person's own past, recomputed with `detect_stay_anomalies` and the
  analytics thresholds (duration, arrival and departure beside their usual
  value, a new place, where they usually are at those hours). Words are built
  at read time; the session row keeps only the deviation kinds. A presence read
  that fails leaves the line as it was. **The place is named only where
  `where_been` would name it (R-35)**: every room of the unit must pass
  `presence_audience` (the decision `where_been` takes: a live roster, no
  unnamed account, a subset of a circle the person is in, or location-trusted);
  otherwise the line says how the stay differed and never where, and a room
  that cannot be read names no place. Not available without a new pipeline:
  co-location and silence lines carry no place or usual pattern, and the anomaly
  is recomputed on read rather than read from a stored score (the rollup never
  stores one).
- **Her own sends are part of the situation (D1, operator 2026-09-30).** Inside
  each message item's window (from `own_send_lookback_seconds`, 1800 s, before its
  oldest message until now, at most `own_sends_max`, 6) her own delivered sends in
  that room are shown in order with the room's messages, marked as hers («Nora
  (you), 18:40, ...»), so a reply reads as an answer to what she asked. They are
  read through the same select as every Record row (a hidden row is absent, an
  unsent one's words are withheld), only in a room of the unit, and they are
  never a stimulus and never an id a note may cite.
- **A room is called by its label** (`policy.toml`), never its conversation id
  (R-18): in the item line, the summary and the Register index lines. The id
  appears once, beside its label, in the line that tells her what a read tool's
  `room` argument takes.

**Every item binds one room**: the room its stimulus came from, or, for an item
with none (a Rollup, `time`, `self` or play stimulus), the unit's home room
(39 D2). A social stimulus names a room of the unit that lists its author, the
home room when it does ([22](22-social-stream.md) §6): only such a room may be
shown the author's items. That
item's room-scoped reads resolve through its room (R-21), and every read
returns only what its room may be shown ([10](10-tiers.md) §3). Which tools a
unit-keyed session may hold at all stays decided by `UNIT_SAFE_TOOLS` and
`tool_subset_faults` (`nora/agent/dreamer.py`).

One context holds several items' reads, so a line written for one item can
carry what was read for another. Code bounds this through citations (every
line cites, and its lineage is computed from what it cites); the assess pass
measures whether a citation supports its line (§6). The unit is the
read boundary for all its rooms' transcripts, raw transcripts included
(DEC-231, operator 2026-09-28); what reaches a room is still decided by
lineage at assembly.

An agenda with no items is no session: zero model calls, the rule every
adaptor keeps today.

**A session is replayable.** Its row records its inputs: the situation and
agenda as handed, the stimulus ids it claimed, the doctrine sha256, the skill
versions injected, the config revision in force, and the model id and route
(a column, not only a trace attribute). With the stores as of its start
([10](10-tiers.md) §8), that is enough to run it again.

## 2. What it holds

| class | verbs | leaves the session how |
|---|---|---|
| **read** | every read tool the registry admits (`read_only_tools()`) except the live social reads ([20](20-observe.md) §2), plus the Notebook and Rollup reads of [15](15-map.md) §4: `record_query`, `rollup_read`, `event_search`, `memo_read`, `character_read`, `standing_read`, `social_items` (built as `social_read`, [15](15-map.md) §4) | returns into context, filtered by the item's room |
| **write** | memory (`memory_propose`), profiles (`profile_read`, `profile_revise`), the Notebook (`note_*`, `memo_*`, `event_*`) and Standing (`standing_revise`) | code validates, then commits (§3); Register revisions carry their base ids and project their graph links in the same session |
| **act** | `remind(at, room, brief)` prepares a room contribution; `remind_move` and `remind_cancel` maintain Cognition's own reminders | a reminder row; the fire path composes it when due |
| **pace** | `wake_at(at, subject_rows, why)`, `wake_move(wake_id, at, why)`, `wake_cancel(wake_id, why)` | write verbs on the unit's stimulus table, not a tier: a `self` stimulus due at `at` ([20](20-observe.md) §3); nothing leaves for a room |

**One time base, one room.** Every time a session verb takes (`at` on the
pace verbs and on `remind`, `remind_move`; `when` on an event; a note's
`anchor_date`) is ISO 8601 with an offset (a date, for the anchor), converted
in code; a time with no offset or no meaning is refused `bad_time`. Every write
and act verb resolves its `room` through the agenda (DEC-250).

**Every act is a reminder.** Cognition decides and writes a reminder carrying
a **brief**: an intent (`tell` or `ask`) and the subject rows (Register ids,
Rollup keys, Record ids), never prose. "Speak now" is a reminder due now;
"ask" is a brief whose intent is `ask`. When it comes due, the fire path
composes the words inside the target room, under that room's guards: the
brief's rows are assembled through the room's lineage and the live
DisclosureGuard, the room's `proactive` flag applies, and
`max_pending_per_requester` caps what is pending. There is no separate speak
or ask send path.

**A person's reminder after cognition revises its event** fires with the
event's current revision. If cognition cancelled the event, the reminder is
kept and fires flagged as being about a cancelled event. Cognition may cancel
a person's reminder when it cancels that reminder's event on `told` evidence;
other changes to a person's reminder belong to the tag path.

**Self-wakes are not acts** ([20](20-observe.md) §3 defines the pace verbs,
their guards and their surfacing).

**Which holds what, the conscious or the subconscious** ([55](55-self.md) §6).
The pace verbs and `remind_move`/`remind_cancel` belong to the subconscious:
no tag or fire turn holds them, and neither can read an agenda or a pending
self-wake. `remind` is the bridge: Cognition holds it to reach the conscious,
and the tag turn holds it for a reminder a person asks for. Cognition writes
the Notebook; the tag turn hands new evidence off through a strong-cue
stimulus.

**The surface.** Live sessions offer reads, memory and profile writes, the
Notebook, Standing, pace and acts together. Act verbs do not depend on a prior
memory proposal. Historical sessions offer evidence reads and `memory_propose`
only, with no act verbs or current-profile access.

**Session outcome.** A normal `end_turn` consumes the session's observed stimuli,
even when there is nothing to write or do. There are no per-item close calls,
coverage declarations or strikes. Failed or bounded sessions leave their inputs
pending with backoff; the five-failure park and operator requeue remain.
`max_items` defers unseen input without dropping it. `wake_at` requests a later look.

## 3. What code enforces, and what is measured instead

The session is one agent; judging its writes is not left to it.

**Enforced by code on every write verb, whichever adaptor or turn calls it,
before commit.** A failure is refused with
`refused` (ADR 0009's status) and a named reason, answered to the model so
it can rewrite, and counted by rule; never silently dropped.

1. **Citations resolve** to ids this session was shown or fetched, and every
   citation chain ends in Record ids; a Rollup key qualifies
   ([10](10-tiers.md) §2 rule 2).
2. **Citations reach only the Record and the Rollup; links point down** ([10](10-tiers.md) §2 rule 1, amended by DEC-249).
3. **Authorship** per the echo rule ([10](10-tiers.md) §2 rule 4);
   non-member social text alone is refused ([20](20-observe.md) §2).
4. **Schema** ([12](12-schemas.md)): headings, cited lines, absolute dates (a
   relative time word in a date field is refused).
5. **Mode.** The mode is one the tier allows, capped at `abduced` where it
   rests on presence fixes or a face older than the freshness threshold
   (starting at 900 s, [10](10-tiers.md) §4).
6. **Concurrency.** A `base_revision_id` that is not the current revision is
   refused `stale_base`, with the current row.
7. **Disclosure columns.** Lineage is computed from the citations; a cited
   message carrying a surprise cue makes `exclude_persons` mandatory, and it
   is unioned from everything the row cites and across Register links
   ([10](10-tiers.md) §3).
8. **Subjects.** `character_revise` for a person refuses anyone not a listed
   person of the unit's rooms; nothing from the peer card is Character
   evidence ([50](50-character-and-standing.md) §5).
9. **Egress screen.** Every write passes `dream_screen` (the module
   `nora/agent/dream_screen.py`, its function `screen`) before it is stored.
   A memo body is exempt from its key-shape check and renders normally:
   anything said in a group is already shared with that group.
10. **Provenance and history.** Session id, trace id, model and doctrine hash
    are stamped; every Register, Character, Lessons and Standing row is a
    revision chain, so a wrong inference is one revision away from right.
11. **Told outranks cognition's cancellation.** A cognition revision that
    moves a `told` event to `cancelled`, `evicted` or `lapsed` without `told`
    evidence is written, and the event keeps rendering for 48 hours, flagged
    "cancelled?".
12. **Own misreads only.** `event_set_status(evicted)` is refused on an event
    whose first revision Cognition did not write; `remind_move` and
    `remind_cancel` are refused on a reminder Cognition did not write, except
    (after E2, DEC-249) `remind_cancel` on a person's reminder whose event
    Cognition cancels in the same session on `told` evidence.

13. **Graph writes** (step ① and every Register write's own links): a
    relation outside the vocabulary or with ends of the wrong kinds, evidence
    the session never saw, evidence in the notebook layer, a span not in its
    source, or a scope wider than its evidence is refused; valid parts commit
    and refused parts are recorded with their reason (27 §7 step 1.6).

**The pace verbs** (`wake_at`, `wake_move`, `wake_cancel`) pass checks 1
(their subject rows resolve to ids shown or fetched) and 10 (provenance and
history), plus the self-wake guards of [20](20-observe.md) §3. They carry no
schema, mode or lineage: they write a stimulus, not a tier.

**Enforced by code on every act** — the general guards, and the only ones:

- disclosure at assembly, re-run when the reminder fires;
- rate caps: a daily act cap per room and per person, and the pending
  ceiling;
- dedupe: one pending act per (subject rows, addressee);
- a value that is only abduced goes out as a question: code sets the brief's
  intent to `ask`;
- an act whose only citations are non-member social text is refused;
- cognition never cancels a reminder a person set, except with the event it
  cancels on `told` evidence (after E2, DEC-249); a tag-path revision or
  `cancel_reminder` does, and a reminder about a row is cancelled before it fires when that
  row is retired or revised on the tag path (DEC-162 D11).

**Deviation is a stimulus, not a rule.** No gate is specific to it. It enters
her context like any other stimulus, and whether and how to act on it is her
judgement from everything she can read.

**Measured by the assess pass, not enforced** (§6): whether a citation
supports its claim; whether `told` and `abduced` were assigned correctly;
whether a Character line is true of the Record it cites; and the re-recording
rate (lines that only restate one Record row; a memo is a one-row
restatement by construction, so this is a metric, never a refusal).

## 4. One loop, tool-gated guidance

The prelude states Cognition's purpose as Nora's subconscious: observe, orient,
decide and act when nobody asked, with silence a valid outcome. The objective
holds the stages and the few rules code cannot enforce. Context assembly
injects short guidance for the actual offered tools, including the notebook
skill whenever any tool it teaches is offered. Agenda kinds do not select skills.

The prelude, objective and notebook guidance are hot-loaded, with their versions
recorded on the session. Startup and session setup refuse a missing prelude or
notebook skill. All tools on the session's admitted surface are resident;
Cognition does not use `load_tools`. Historical sessions omit act sections
because their surface has no act verbs.

## 5. Budgets

`AdaptorBudget` bounds rounds, total tokens, output tokens and one wall clock
over the whole session (DEC-247). There is no per-tool call ceiling or repeat
veto. The safety checks and result-size cap remain. The wake timeout stays above
the session timeout by the margin checked at startup. `max_items` limits input,
retaining every deferred stimulus for a later session.

Only `end_turn` is normal completion. A limit, timeout, failure or unknown stop
reason leaves the claimed stimuli pending, applies failure backoff and records
the session outcome. Writes already committed remain available to the next
session; existing dedupe and idempotency prevent duplicated actions. A normal
end advances the watermark with its deferred-input carries atomically. There
is no quiet-close stop flag: the model ends its own turn, including when it
chooses silence.

## 6. The Assess adaptor and data quality

Judging an act belongs outside it (design/dreamer-agent-loop/17-failure-classes.md
§2). The **Assess adaptor** runs after each finished session of any adaptor,
and nightly, off every live path. It hosts the model-read measures below
(the cheap reader, lesson drift and misevolution) and writes reports and
metrics only: operator-only rows like tension, never a tier. The
deterministic data-quality checks stay code. Both ship at step 3, before
cut-over ([70](70-migration.md)).

- **The strong measure is people.** A Register row a person later corrects
  ("no, we went to C"), a reminder nobody answered, a question she asked
  twice: each is counted against the session that wrote it
  (`nora_cognition_corrected_total{tier}`).
- **Labelled corpora.** Per capability (event derivation, note, memo, room
  Character), at least 40 labelled cases including negatives (a joke, a
  hypothetical, another unit's plan, a plan already cancelled), with
  precision and recall gates, a false-derivation rate and an unasked-act rate.
  Each corpus gates the step that ships its capability (the memo corpus
  gates step 7).
- **A cheap reader** samples finished sessions for writes whose citations do
  not say what the write claims. It reports; it never edits.
- **Data-quality checks, nightly, in code** (the analogue of a data team's quality tests):
  citations that no longer resolve; one happening written as two events
  across rooms; a Rollup row a rebuild computes differently; a happened event
  whose place differs from its outing's; sections past their age horizon; an
  induced line citing no aggregate; a row with empty lineage. Each a counter
  (`nora_dq_failures_total{check}`).
- **Lessons drift.** The assess pass measures whether lessons drift toward
  what earns engagement or flattery rather than what serves the unit (skill
  misevolution, arXiv 2608.12851), and lesson churn (writes and retirements
  per week).
- **Self-wakes**: yield, lateness, and the share moved or cancelled before
  due ([20](20-observe.md) §3).
- **Cases pin a time.** An eval case names a moment T and runs against Nora
  as of T (`nora snapshot --at T`, [70](70-migration.md) §2): the stores,
  doctrine, skills, config and model route in force then, memory excepted.
- **Rebuild, not only fold.** Every Character account is periodically rebuilt
  from the Rollup and a cited window of the Record by the Assess adaptor,
  and the rebuild is diffed against the chain and never written: the difference is the drift metric, and step ② stays the only writer. Folding alone
  was measured to drop what it should keep: Standing folds dropped at least
  10 open dated plans in 206 revisions
  (design/group-situation/10-standing-drops-plans.md).

## 7. What stays outside the loop

- **The tag path** — the reflex. It answers when called, reads what cognition
  wrote, writes through the same verbs, and its debrief keeps revising the
  room's Standing inside the reply slot, through `standing_revise`. Both writes
  move behind Cognition at or after the cut-over (DEC-249,
  [05](05-data-model.md) §9 E2).
- **Assess** — judging a session belongs outside it (§6). (Character, culture
  and Lessons were slow adaptors here; since 2026-10-28 they are step ②, §0.)
- **Participation** — deciding within seconds whether a quiet window is hers
  is too fast for a unit session. It stays as it is (off by default); a later
  step may make it a small-agenda wake of the same loop once session latency
  is measured.
- **Ingestion** — the chat transports, the social poller and presence
  collection record items and raise stimuli; they never run inside the loop.
- **Delegated long work** — a backfill of a year of photos, a face index
  rebuild. A later `delegate` verb hands those to the compute path; out of
  scope here. The memory graph's backfill is not this: it is steps ① and ② in
  their own lane (§0 rule 7).

## 8. Why no Strands multi-agent primitive

The session is one Strands `Agent` from `create_dream_agent`, with skills
injected by code (§4). `Swarm`, `Graph` and `Agent.as_tool()` all pass text
one model wrote into another model's prompt (read from strands-agents 1.50.2
and 1.57.1), which is the context boundary this design removes. Perception
models (vision, faces) are called as tools that return typed results, not as
agents. The SDK upgrade from 1.50.2 is independent of this bundle and ships
on its own measurement.

**Three sessions are not three agents talking** (2026-10-28). The steps of §0
never pass one model's prose into another's prompt as an instruction or a
plan. Step ① commits typed rows that code admitted (readings with exact spans,
relations with evidence rows); step ③ reads them as it reads any tier, with
their layer, age and evidence, which is [00](00-intent.md) §4's rule that
adaptors never hand state to each other but each reads what the others wrote.
What is split is comprehension from decision, because one is complete and the
other selective (§0 rule 1); orient, decide and act stay one context in step ③.
