---
type: design
title: "Cognition — one session per unit per wake: orient, decide, act in one context"
description: "What the session is handed (the situation and an agenda whose items each bind a room), what it holds (every permitted read, write verbs per tier, one act verb whose rows the fire path composes in the target room, a closing verb), what code enforces on every write and act and what the assess pass measures instead, skills injected by code, budgets, and what stays outside the loop."
tags: [nora, cognition, ooda, agent-loop, strands, write-verbs, proactive, skills]
timestamp: '2026-09-28'
edges:
  - {dst: 40-register, rel: depends_on, provenance: asserted}
  - {dst: 50-character-and-standing, rel: depends_on, provenance: asserted}
  - {dst: ../dreamer-agent-loop/39-proactive-ooda-loop.md, rel: supersedes, provenance: asserted}
---

# 30 · Cognition

One Strands agent session per unit per wake ([20](20-observe.md) §3). It
orients, decides and acts in one context, so no conclusion is lost between
steps and no state is handed from one model to another.

## 1. What code hands it

Built once per session, in code, before any model call:

- **The situation.** The Record items that raised the pending signals, each
  with its author, its room, **its age**, its cues and its perception (what a
  photo shows, whose faces, with confidence); the Rollup rows that raised
  signals; the index lines of the Register rows those items touch; the unit's
  clock in HK time; how long since the last session. Index lines, not
  bodies: Register, Character and Standing bodies are pulled with read tools
  when the session needs them, and never pushed, except the capped `culture`
  and `self` blocks ([15](15-map.md) §3–4).
- **The agenda.** One item per thing that needs attention, computed by code
  from the signals: *room R has 23 new messages*; *event E's place is named
  in 3 messages since it was set*; *a co-location of 3 members began at a
  place at 20:05*; *P's week deviates from their rhythm*; *note N's anchor
  passed*; *4 new story frames from 2 members*; *the unit's culture was last
  revised 6 days ago*. An item states **what happened**, never what to
  conclude from it, and never a menu of possible actions. Code orders items
  by computed surprise ([80](80-prior-art.md) §4) and hands at most
  `max_items`; the rest wait for the next session.

**Every item binds one room**: the room its signal came from, or, for an item
with none (a Rollup or `time` signal), the unit's home room (39 D2). That
item's room-scoped reads resolve through its room (R-21), and every read
returns only what its room may be shown ([10](10-tiers.md) §3). Which tools a
unit-keyed session may hold at all stays decided by `UNIT_SAFE_TOOLS` and
`tool_subset_faults` (`nora/agent/dreamer.py`).

One context holds several items' reads, so a line written for one item can
carry what was read for another. Code bounds this through citations (every
line cites, and its lineage is computed from what it cites); the assess pass
measures whether a citation supports its line (§6). Whether the unit is the
read boundary for all its rooms' transcripts is open until step 0; if it is
not, cognition runs one session per room instead, with the same verbs
([70](70-migration.md) §4).

An agenda with no items is no session: zero model calls, the rule every
adaptor keeps today.

## 2. What it holds

| class | verbs | leaves the session how |
|---|---|---|
| **read** | every read tool the registry admits (`read_only_tools()`) except the live social reads ([20](20-observe.md) §2), plus the warehouse queries of [15](15-map.md) §4: `record_query`, `rollup_read`, `event_search`, `memo_read`, `character_read`, `standing_read`, `social_items` | returns into context, filtered by the item's room |
| **write** | one family per tier: Register (`note_create`, `note_update`, `memo_write`, `memo_withdraw`, `event_propose` (status `staged` or `proposed`), `event_revise`, `event_set_status`), Character (`character_revise`), Standing (`standing_revise`); each takes `base_revision_id` | code validates, then commits (§3) |
| **act** | `remind(at, room, brief)`, the one act verb; `room` is the agenda item's bound room | a reminder row; the fire path composes it when due |
| **close** | `close(item, outcome)` | ends one agenda item |

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
cancels a reminder a person set. The senders stay those of R-1b: the tag
path, the fire path and, since DEC-212, the public summons.

**The grant.** The runtime's ceiling today is "may look, may not act": every
read verb and one act verb (DEC-162). A new DEC at step 0 extends it by
granting named **write** verbs through `AdaptorGrant`; write verbs are not act
verbs, and `remind` stays the only act verb.

**Closing.** An item closes only with a write that cites it, or with a reason
from a closed list (`no_change`, `waiting`, `covered`), counted per reason. An
item left open when the session ends, or closed `waiting`, returns to the next
agenda with a strike; at `max_strikes` code closes it `struck` and counts it,
so an item that can never close does not spend every session.

**Shared with the tag path.** `note_create`/`note_update` as today. The tag
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

**Enforced by code on every write, before commit.** A failure is refused with
`refused` (ADR 0009's status) and a named reason, answered to the model so it can
rewrite, and counted by rule; never silently dropped.

1. **Citations resolve** to ids this session was shown or fetched, and every
   citation chain ends in Record ids; a Rollup key qualifies
   ([10](10-tiers.md) §2 rule 2).
2. **Citations point down** ([10](10-tiers.md) §2 rule 1).
3. **Authorship.** Nora's own rows ground only `said` Standing lines and her
   own Character paired with responses; a write whose only citations are
   non-member social text is refused ([20](20-observe.md) §2).
4. **Schema** ([12](12-schemas.md)): headings, cited lines, absolute dates (a
   relative time word in a date field is refused).
5. **Mode.** The mode is one the tier allows, capped at `abduced` where it
   rests on presence fixes or a face older than the freshness threshold
   ([10](10-tiers.md) §4).
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
    are stamped; every Register, Character and Standing row is a revision
    chain, so a wrong inference is one revision away from right.
11. **Told outranks cognition's cancellation.** A cognition revision that
    moves a `told` event to `cancelled`, `evicted` or `lapsed` without `told`
    evidence is written, and the event keeps rendering for 48 hours, flagged
    "cancelled?".

**Enforced by code on every act** — the general guards, and the only ones:

- disclosure at assembly, re-run when the reminder fires;
- rate caps: a daily act cap per room and per person, and the pending
  ceiling;
- dedupe: one pending act per (subject rows, addressee);
- a value that is only abduced goes out as a question: code sets the brief's
  intent to `ask`;
- an act whose only citations are non-member social text is refused;
- cognition never cancels a reminder a person set; only a tag-path revision
  does, and a reminder about a row is cancelled before it fires when that
  row is retired or revised on the tag path (DEC-162 D11).

**Deviation is a signal, not a rule.** No gate is specific to it. It enters
her context like any other signal, and whether and how to act on it is her
judgement from everything she can read.

**Measured by the assess pass, not enforced** (§6): whether a citation
supports its claim; whether `told` and `abduced` were assigned correctly;
whether a Character line is true of the Record it cites; and the re-recording
rate (lines that only restate one Record row; a memo is a one-row
restatement by construction, so this is a metric, never a refusal).

## 4. Skills, not one long prompt

The base prompt is the Dreamer doctrine (what the runtime is), the situation
and the agenda. What each kind of work asks for — how to judge a note, when a
plan has moved, what a culture account is — lives in a **skill**. **Code
injects the text of the skill for each agenda kind present**, after the
doctrine and in a fixed order, so the doctrine prefix stays cached and only
the skills vary. `create_dream_agent` (`nora/agent/factory.py`) has no
`skills_dir`, by design, and ADR 0016 (the skill policy) is still proposed:
the injection is code, not SDK skill loading. The adaptors' objectives
(`note-candidates.md`, `note-curation.md`, `note-merge.md`, `culture.md`,
`proactive.md`) become those skills; `standing-debrief.md` stays the
debrief's. A session that only sees chatter never loads the note skill's 280
lines. `tool_selection` is measured on the cognition catalogue, on the
runtime model, before step 3.

## 5. Budgets

Per session, in code and never stated in the prompt (a model told of a cap
declines work, measured on the proactive pass): `AdaptorBudget` holds turns,
total and output tokens and the timeout, one wall clock over the whole
session; `DreamGuard` holds the per-tool, memory and pass ledgers; `max_items`
caps the agenda. A session that runs out closes nothing further; its open
items return with a strike (§2).

## 6. The assess pass and data quality

Judging an act belongs outside it (design/dreamer-agent-loop/17-failure-classes.md
§2). Both ship at step 3, before cut-over ([70](70-migration.md)).

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
- **Data-quality checks, nightly, in code** (the analogue of warehouse tests):
  citations that no longer resolve; one happening written as two events
  across rooms; a Rollup row a rebuild computes differently; a happened event
  whose place differs from its outing's; sections past their age horizon; an
  induced line citing no aggregate; a row with empty lineage. Each a counter
  (`nora_dq_failures_total{check}`).
- **Rebuild, not only fold.** Every Character account is periodically rebuilt
  from the Rollup and a cited window of the Record, and the rebuild is
  diffed against the chain; the difference is the drift metric. Folding alone
  was measured to drop what it should keep: Standing folds dropped at least
  10 open dated plans in 206 revisions
  (design/group-situation/10-standing-drops-plans.md).

## 7. What stays outside the loop

- **The tag path** — the reflex. It answers when called, reads what cognition
  wrote, writes through the same verbs, and its debrief keeps revising the
  room's Standing inside the reply slot, through `standing_revise`.
- **Participation** — deciding within seconds whether a quiet window is hers
  is too fast for a unit session. It stays as it is (off by default); a later
  step may make it a small-agenda wake of the same loop once session latency
  is measured.
- **Delegated long work** — a backfill of a year of photos, a face index
  rebuild. A later `delegate` verb hands those to the compute path; out of
  scope here.

## 8. Why no Strands multi-agent primitive

The session is one Strands `Agent` from `create_dream_agent`, with skills
injected by code (§4). `Swarm`, `Graph` and `Agent.as_tool()` all pass text
one model wrote into another model's prompt (read from strands-agents 1.50.2
and 1.57.1), which is the context boundary this design removes. Perception
models (vision, faces) are called as tools that return typed results, not as
agents. The SDK upgrade from 1.50.2 is independent of this bundle and ships
on its own measurement.
