---
type: design
title: "Cognition — the one loop: comprehend on arrival, reshape what changed, decide and act on the wake"
description: "The Cognition loop, the one background driver (operator, 2026-10-28): three ordered steps, each its own bounded session, handing off only through committed state. Sections 1-5 describe step 3, the decide-and-act session as built. What the session is handed (the situation and an agenda whose items each bind a room), what it holds (every permitted read, write verbs per tier, one act verb whose rows the fire path composes in the target room, a closing verb), what code enforces on every write and act and what the Assess adaptor measures instead, skills injected by code, budgets, and what stays outside the loop."
tags: [nora, cognition, ooda, agent-loop, strands, write-verbs, proactive, skills]
timestamp: '2026-09-28'
edges:
  - {dst: 40-register, rel: depends_on, provenance: asserted}
  - {dst: 50-character-and-standing, rel: depends_on, provenance: asserted}
  - {dst: ../dreamer-agent-loop/39-proactive-ooda-loop.md, rel: supersedes, provenance: asserted}
  - {dst: ../unified-memory/27-the-memory-is-a-graph.md, rel: depends_on, provenance: asserted}
---

# 30 · Cognition

> **Authoritative amendments:** the operator's 2026-10-29
> [one-session ruling](../unified-memory/29-one-cognition-session.md) supersedes
> the three-session topology below. [One Standing](../notebook-memory-types/20-standing.md),
> clarified 2026-10-31, owns input and completion: Cognition reads/thinks/decides
> across the unit, then the existing Standing updater automatically organizes
> its full retained Record and actual results. No `decision_conclude` verb or
> extra self-report obligation. Room and unit state keep separate scope keys
> under the common model; room is source/delivery context, not an agenda-based
> read/edit boundary. Each consumer gets one `standing` block with appropriate
> source/disclosure rules. Cognition retains substantive Notebook authority;
> the updater adds no new judgment or independent wake. The tag-debrief writer
> wording in §7 and destination/exact-citation dedupe description below are
> historical where they conflict. The new node owns migration, automatic update
> flow and unit-wide action arbitration. Design targets are not runtime claims.

> 2026-10-03 (DEC-277): the shadow period is over. The Cognition session writes live (`[cognition] enabled`) and is the one background writer of notes, events, Standing and her reminders. The clocked note/proactive adaptors, the shadow machinery and `[cognition] mode` / `session_mode` / `shadow_session_seconds` named here are deleted; wording below that describes them is the plan as written, not current behaviour. See `70-migration.md`.


## 0. The loop, as ruled 2026-10-28

Cognition is the **one background driver** (operator, 2026-10-28,
[unified-memory/27](../unified-memory/27-the-memory-is-a-graph.md) §7, §9).
Everything said and done keeps shaping memory, profiles and plans, so one loop
owns all three, in three ordered steps. Each step is **its own bounded Strands
session** with its own focused input and its own verbs; the steps hand off
**only through committed state**, never by waiting on each other or passing
prose into another prompt (§8).

```
ingestion (code) ─▶ RECORD / ROLLUP ─┬─▶ work item (every item) ─▶ ① COMPREHEND ─▶ graph
                                     │                              (on arrival)
                                     │                                   │
                                     │                                   ▼
                                     │                              ② RESHAPE ─▶ profiles, culture,
                                     │                              (links changed)  lessons
                                     └─▶ stimulus ─▶ wake rules ─▶ ③ DECIDE & ACT ─▶ Register, Standing,
                                                                   (①'s readings     reminders, wake_at
                                                                    + preview, §1)
```

| step | runs | input | holds | writes |
|---|---|---|---|---|
| ① comprehend | **on each arrival**, one work item (a message batch, a social item, a late media reading, a closed Rollup episode, a Register write) | the item fully read (media join, link, reply parent, a social item's caption and media) and the code-built **graph preview** of what the graph already knows (27 §7) | read tools, `memory_search` / `memory_read`, the read lanes (`media_fetch`, `instagram_read`, `instagram_search`, `x_read`, `x_search`), egress-screened web search and fetch (27 §8), one staged graph write | readings, entities, judgement relations with evidence (27 §5, §7a) |
| ② reshape | right after a step ① that changed relations | the subgraph of each changed entity, in the order persons → their rooms and units → Nora | graph reads, `character_revise`, `lesson_write`, `lesson_retire` | traits and guesses (facts are rendered by code), a room's or unit's culture, Nora's lessons (27 §9) |
| ③ decide and act | on the wake rules ([20](20-observe.md) §3: `settled`, `strong_cue`, `batch`, `max_age`, `time`, `self`) | the agenda, each item carrying step ①'s readings and preview (§1) | §2's verbs | Register items with their own links, Standing, reminders, `wake_at` (§2) |

**Rules that hold across the steps** (27 §7):

1. Step ① is complete; only step ③ is capped (`max_items`, §1).
2. Acting is a switch: the backfill runs ① and ② in its own lane, each item
   read as of its own time, and step ③ stays off until one catch-up wake at
   the end (27 §10).
3. Step ② runs only for entities whose relations changed.
4. No step grades itself: guesses and lessons are checked by an instrument
   other than their writer ([50](50-character-and-standing.md) §6; §6 below).
5. A step ① work item is never dropped: retried, then parked `failed` for the
   operator's re-queue; a wake failure only ever strikes step ③'s agenda.
6. Each step commits on its own; a step ③ failure undoes nothing before it.
7. The backfill has its own concurrency and never takes a live slot.

**What retires into the loop:** the extraction pass (into ①); the Character,
profile, culture and Lessons adaptors (into ②). Assess stays outside (§6).
Sections 1-5 below are **step ③**, the session as built since DEC-277,
amended in place where the ruling changed it.

## 1. What code hands it

Built once per session, in code, before any model call:

- **Step ①'s reading of each item** (2026-10-28, 27 §7 rule 1): every agenda
  item carries the readings step ① wrote for its Record items and the item's
  graph preview, so step ③ decides on one interpretation and never re-reads
  the raw text as a second one. The Record rows below stay as the grounding
  and are still readable through tools. An item whose work item has not
  settled waits for it or is marked unread.
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
| **write** | one family per tier: Register (`note_create`, `note_revise`, `memo_create`, `memo_set_status`, `event_propose` (status `staged` or `proposed`), `event_revise`, `event_set_status`), Standing (`standing_revise`); each takes `base_revision_id`, and each Register write is followed by its own graph links in the same session (27 §7a). Character's, culture's and Lessons' verbs belong to step ② and graph writes to step ① (§0) | code validates, then commits (§3) |
| **act** | `remind(at, room, brief)`, the one act verb; `room` is the agenda item's bound room. `remind_move(reminder_id, at, why)` and `remind_cancel(reminder_id, why)` for reminders Cognition itself wrote, never a person's | a reminder row; the fire path composes it when due |
| **pace** | `wake_at(at, subject_rows, why)`, `wake_move(wake_id, at, why)`, `wake_cancel(wake_id, why)` | write verbs on the unit's stimulus table, not a tier: a `self` stimulus due at `at` ([20](20-observe.md) §3); nothing leaves for a room |
| **close** | `close(item, reason)` | ends one agenda item |

**One time base, one room.** Every time a session verb takes (`at` on the
pace verbs and on `remind`, `remind_move`; `when` on an event; a note's
`anchor_date`) is ISO 8601 with an offset (a date, for the anchor), converted
in code; a time with no offset or no meaning is refused `bad_time`. Every write
and act verb resolves its `room` through the agenda (DEC-250).

**Every act is a reminder** (DEC-162's decide/compose split, kept). Cognition
decides and writes a reminder carrying a **brief**: an intent (`tell` or
`ask`) and the subject rows (Register ids, Rollup keys, Record ids), never
prose. "Speak now" is a reminder due now; "ask" is a brief whose intent is
`ask`. When it comes due, the fire path composes the words inside the target
room, under that room's guards: the brief's rows are assembled through the
room's lineage and the live DisclosureGuard, the room's `proactive` flag
applies, and `max_pending_per_requester` caps what is pending. The room is
always the agenda item's bound room: DEC-162's home room, generalised. There
is no separate speak or ask send path. `remind` is the tool the tag path
already holds, so adding `brief` changes a shared tool and is measured with
`tool_selection` ([70](70-migration.md) §8).

**A person's reminder after cognition revises its event** fires with the
event's current revision. If cognition cancelled the event, the reminder is
kept and fires flagged as being about a cancelled event: only the tag path
cancels a reminder a person set. **Amended by DEC-249 for after E2**
([05](05-data-model.md) §7): `remind` and `cancel_reminder` stay on the tag
turn as machinery (they change a reminder row, not the Notebook), and
Cognition may also cancel a reminder a person set when it cancels that
reminder's event on `told` evidence (a person's cancellation in words is
Record it reads). The senders stay those of R-1b: the tag
path, the fire path and, since DEC-212, the public summons.

**Self-wakes are not acts** ([20](20-observe.md) §3 defines the pace verbs,
their guards and their surfacing).

**Which holds what, the conscious or the subconscious** ([55](55-self.md) §6). The pace verbs and `remind_move`/`remind_cancel` belong to
the subconscious only: no tag or fire turn holds them, and neither can read
an agenda or a pending self-wake. `remind` is the bridge: Cognition holds it
to reach the conscious, and the tag turn holds it for a reminder a
person asks for. The write verbs on the Register and room Standing are held
by both the conscious and the subconscious; that is shared state, not a command. **Amended by DEC-249**
([05](05-data-model.md) §7): from the cut-over only the subconscious writes
the Notebook (one writer per tier), and the tag turn hands off through a strong-cue stimulus
instead of holding these write verbs.

**The grant.** The runtime's ceiling today is "may look, may not act": every
read verb and one act verb (DEC-162). A new DEC at step 0 extends it by
granting named **write** verbs through `AdaptorGrant` (write verbs are not act
verbs), and `remind_move`/`remind_cancel` by name for reminders Cognition
wrote; `remind` stays the only verb that sends.

**Closing.** An item closes only with a write that cites it (code checks that
the session recorded a write that would commit in the item's room, refusing
`written` with `no_write_for_item` otherwise, DEC-250), or with a reason
from a closed list (`no_change`, `waiting`, `covered`), counted per reason. An
item left open when the session ends, or closed `waiting`, returns to the next
agenda with a strike; at `max_strikes` (starting at 3) code closes it
`struck` and counts it,
so an item that can never close does not spend every session.

**Shared with the tag path.** `note_create`/`note_revise` as today. The tag
path's event tools (`create_event`, `update_event`, `cancel_event`,
`rsvp_event`, `list_events`) move to the event verbs and `event_search` at
step 2b, each rename measured with `tool_selection` before and after (ADR
0015), so a note or event written by cognition and one written on a tag turn
pass the same validator.

**Shadow before cut-over.** Until step 4, every reminder cognition writes is
mirrored to a review channel and held, as participation's shadow is
(`nora/app/fire.py`).

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

## 4. Skills, not one long prompt

The base prompt is Cognition's own **prelude**
(`nora/dreamer/preludes/cognition-prelude.md`: what the runtime is, for a
session whose verbs write), the objective, the situation and the agenda. It is
NOT the shared Dreamer doctrine (`doctrine/dreamer.md`), which every other
adaptor still reads and which tells a judge «nobody sees this… you look; you do
not act»: false of a writer (operator decision D2, step 4 PR-1, 2026-10-02).
The loader serves a prelude in place of the doctrine to the objectives its
`applies_to` names, and startup refuses a `cognition` objective that resolved
without it, and so does the start of every session, so a hot reload that
deletes the prelude fails the wake instead of reading the shared doctrine. What each kind of work asks for — how to judge a note, when a plan
has moved, when to look again — lives in a **skill**. **Code injects the text
of the skill for each agenda kind present**, after the prelude and the
objective and in a fixed order, so the prefix stays cached and only the skills
vary. `create_dream_agent` (`nora/agent/factory.py`) has no `skills_dir`, by
design, and ADR 0016 (the skill policy) is still proposed:
the injection is code, not SDK skill loading. The replaced adaptors'
objectives (`note-candidates.md`, `note-curation.md`, `note-merge.md`,
`proactive.md`) become Cognition's skills; `culture.md` and the lessons objective become step ②'s skills, and step ① has its own objective (27 §7), each a declared occasion in context assembly (ADR 0021); `standing-debrief.md` stays the debrief's. As built
(step 4 PR-1) the Register skill (about 130 lines) is the one skill, mapped to
the `message`, `rollup` and `self` kinds, so every session with an agenda
loads it, chatter included, and an agenda of any other kind loads none.
`tool_selection` is measured on the cognition catalogue, on the runtime
model, at step 3 ([70](70-migration.md) §2).

## 5. Budgets

Steps ① and ② have their own budgets, set in 27 §7 and measured in the spike;
what follows is step ③'s, and the rule (rounds, tokens and time, never per
tool, DEC-247) holds for all three. The one per-tool cap is step ①'s on paid
outbound web lookups (27 §7).

Per session, in code and never stated in the prompt (a model told of a cap
declines work, measured on the proactive pass): `AdaptorBudget` holds turns,
total and output tokens and the timeout, one wall clock over the whole
session. **Those four are the only bounds (DEC-247, operator 2026-10-01): a
session is bounded by rounds, tokens and time, never per tool**, and each of
the four is at least 10x the tag turn's (turns 200, total tokens 10,240,000,
output tokens 120,000, 3600 s, against the tag turn's 20, 1,024,000, 12,000
and 360 s). There is no per-tool, memory-cluster or pass-wide call ceiling and
no repeat veto («you already called X»): the session's `DreamGuard` keeps only
the safety checks (R-7, R-99, the result size cap). The wake's
`session_timeout_seconds` (3900) stays above the session's clock by the 60 s
margin the runner checks at start. `max_items` caps the agenda.

**A session that stops at a bound has not finished (DEC-247).** Strands
RETURNS normally on `limit_turns`, `limit_total_tokens` and
`limit_output_tokens`, so the runner reads `stop_reason` and treats the three
as a FAILED session with cause `infra` (DEC-244): outcome `failed`, no item
closed, no strike, no carry row, its stimuli stay pending, and the failure
backoff applies (so a runaway agenda is held to the backoff rate, about four
sessions a day at the 6 h cap, and never drops a person's message).
`nora_cognition_session_limit_total{limit}` counts them. **What the stopped
session already wrote stays as recorded**: a dry-run would-write, a mirrored
remind, a self-wake and its stimulus are rows, not part of the close. The next
session reads them back as pending, so a re-run does not duplicate them: its
`wake_at` on the same subject row supersedes the earlier self-wake (one live,
the old stimulus retracted) and its identical `remind` is refused
`duplicate_pending`. The per-day self-wake cap counts both creations. Only a
session that ends on its own (`end_turn`) settles its open items with a strike
(§2).

**A session whose every item closed quiet ends at the last close (DEC-279,
2026-10-03).** When every agenda item is closed `no_change` or `covered` and no
verb but `close` was called, `close` sets Strands' `request_state
["stop_event_loop"]`, and the loop makes no further model call (its
`stop_reason` is then `tool_use`, which is not a bound). Measured live: that
follow-up call wrote nothing on every session that closed quiet. A `written` or
`waiting` close, or any other verb called, refused or not, keeps the loop
running as before, including a verb in the same model turn as the last close: Strands reads the
flag only after the whole turn's tools ran, so such a verb takes the stop back.

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
