---
type: proposal
title: "The Proactive Adapter — A Handoff Fact Sheet, Not Yet a Design"
description: "What is verifiably true about the system a proactive adapter would be built into, gathered so the session that designs it starts from checked facts rather than from a summary. Records the operator's framing in his own terms, every fact with where it was checked, and the questions that are open. It decides nothing."
tags: [nora, proactive, initiative, dreamer, handoff, facts]
timestamp: '2026-09-08'
edges:
  - {dst: 33-note-dreamer-schedule, rel: extends, provenance: asserted}
  - {dst: 37-note-candidates-v2, rel: follows, provenance: asserted}
  - {dst: 35-ambient-participation, rel: relates_to, provenance: asserted}
version: 1
---

# The Proactive Adapter — Handoff

## 0. What this document is, and what it is not

**It is a fact sheet.** Every claim below was checked against the code or the
running deployment on 2026-09-08, and each says where. It exists so the
session that actually designs the proactive adapter starts from things it can
re-verify rather than from one session's retelling.

**It decides nothing.** Where a question is open it says so. Where the
operator has stated a position it is attributed to him and left as his.

**One warning it is worth carrying.** The session that produced this sheet
twice imported constraints from the AMBIENT PARTICIPATION occasion (design 35)
into proactive reasoning, where they do not apply. Participation and
proactive are two different occasions with two different triggers, and
`participation-conduct.md` carries `applies_to: [participation]` — it has
never governed anything else. Participation appears below only as a factual
precedent about the FIRE PATH, never as a source of rules.

## 1. The operator's framing (his words, his positions)

Recorded as given, 2026-09-08, not endorsed or extended here:

- **The occasion and the value judgement are one thing, and the thing is
  measurement.** «A 同 B 其實歸納落去同一樣嘢，就係 measurement.» The doctrine
  and the existing ambient work already carry the substance; what a proactive
  adapter needs is not a new philosophy but a measured one.
- **Delivery has two paths.** «一個就係經 reminder，用翻 reminder 嚟做翻同一件事,
  即係基本上其實就係 proactive create 幾個 reminder。第二就係透過直接 tool 去發送。»
  Note the plural — creating *several* reminders is the shape he named, and
  `remind` is a tool Nora already has rather than a row-write invented for
  this tier.
- **The scenario he wants to be possible.** A note exists for a dinner on a
  date, with neither the place nor the food settled. When the proactive pass
  sees it, can it (1) draw on each person's food preferences, (2) work out
  where everyone will be coming from and derive a convenient area, and (3)
  find restaurants there matching those preferences and write a recommendation
  with links?
- **Naming.** He has said before he is not sure "proactive adapter" is the
  right name. Nothing here depends on the name.

## 2. Verified facts

### 2.1 The Dreamer cannot speak, by construction

`DreamerRuntime.run` calls `tool_subset_faults(adaptor, ceiling=self._tools.
read_only())` before anything else and returns `unavailable` on any fault, so
an adaptor declaring an act verb refuses at startup rather than at call time.
This is R-1b's structural half. Checked: `nora/agent/dreamer.py`.

### 2.2 The tool ceiling depends on the adaptor's KEY

`WORKSPACE_SAFE_TOOLS` is exactly:

```
web_search  web_fetch  now  calculate  weather  version
social_fetch  media_search  note_read  note_search  search_memory_workspace
```

A workspace-keyed adaptor declaring anything else fails at startup with:

> «Dreamer adaptor X is keyed on a WORKSPACE and declares room-scoped tools:
> … A workspace is not a conversation, so these would answer EMPTY rather
> than fail — «I could not check» reported as «there was nothing».»

The comment above the set states the second reason: binding a room-scoped
tool to an arbitrary room of the workspace «would be a disclosure decision
(R-31) nobody has made». Checked: `nora/agent/dreamer.py`.

**Consequence for the operator's scenario, measured against the tool list
(`nora/agent/tools.py`):**

| his step | tool it needs | workspace-keyed | scope-keyed |
|---|---|---|---|
| food preferences per person | `search_memory_workspace` | available | available |
| where each person will be | `where_is`, `where_been`, `saved_places`, `route`, `transit`, `place_info` | **refused at startup** | reachable |
| restaurants + links | `web_search`, `web_fetch` | available | available |

**A note already carries the room it was born in** — `Note.established_in`,
documented in `nora/domain/notes.py` as «a routing key the tag path reads
later». `NoteCandidateAdaptor` is already `key_kind = "scope"`. So a
scope-keyed proactive adaptor is a shape the codebase already has, not a new
mechanism.

**Open, and it must be answered before step 2 is claimed as possible:** what
`where_is`'s disclosure gate (R-31) actually returns for a turn with no
asker. The gate is evaluated against who is in the room; a background pass
has no asker (`participation_occasion` uses `person_id=None`, and every
consequence of that is documented on `build_context`). Being *reachable* is
not the same as being *permitted*, and this has not been run.

### 2.3 There are three doctrine audiences, and one of them has no
"you may stay silent"

| file | applies_to |
|---|---|
| `conduct.md` | tag, heartbeat, participation |
| `constraints.md` | tag, heartbeat, participation |
| `identity.md` | tag, heartbeat, participation |
| `voice.md` | tag, heartbeat, participation |
| `silence.md` | **tag, participation** |
| `participation-conduct.md` | **participation** |

A fire turn reads `applies_to="heartbeat"` (`nora/app/fire.py`, `AUDIENCE =
"heartbeat"`), and `"participation"` for that one occasion.

So an occasion delivered through the ordinary fire path inherits a doctrine
set with **no may-speak clause at all**. That is coherent for what is there
today — a reminder turn is owed to whoever asked for it, so speaking is not
optional — and it is a real gap for an occasion nobody asked for. Checked by
reading every file's frontmatter.

### 2.4 The fire path already accepts a trigger with no stored row

`FirePath.speak(occasion, *, send_permitted=True)` is the entry point. Six
occasion factories exist in `nora/app/fire.py`: `reminder_occasion`,
`gathering_occasion`, `compute_occasion`, `relay_occasion`,
`lost_job_occasion`, `participation_occasion`.

The composition root's `event_registry` maps a due ROW's `kind` to a factory
and holds exactly two entries (`reminder`, `gathering`); its own comment says
«compute/relay/lost-job call speak directly and are not registry entries».
`participation_occasion`'s docstring says the same of itself: «NOT in the
reminder-derived event registry … The trigger calls `FirePath.speak` with
this Event directly.»

**Cited as a fact about the fire path, not as an argument from
participation:** an occasion with no stored row calling `speak` directly is
an established shape with four existing users.

### 2.5 The reminder tool and its store

`remind(when, text, repeat="none", every_n=None, times=None, until=None)`
already exists as a tag-path tool (`nora/agent/tools.py`). `Recurrence` is a
`StrEnum` of `none / minutely / hourly / daily / weekly / monthly / yearly`
(`nora/domain/reminders.py`) — deliberately not an RRULE, with the reasoning
recorded there.

`Reminder` carries `requester_person_id`, `platform`, `conversation_id`,
`conversation_type`, `text`, `due_at`, `state`, `kind` (default `"reminder"`)
and an optional `payload: Mapping[str, str]`. Config bounds:
`max_pending_per_requester = 20`, `min_lead_seconds = 120`,
`max_horizon_days = 365`.

`kind` and `payload` are the extension points a new occasion would use;
`gathering` already uses them.

**Open:** a reminder has ONE `requester_person_id`. The operator's phrasing
was «create 幾個 reminder» — several. Whether several people each get a row,
or one row addresses a room, is not decided, and the per-requester cap of 20
and the 120-second minimum lead are the numbers that constrain the answer.

### 2.6 The note tier's clock is already the trigger, and it already has a
writer

`Note.revisit_at` is written by the create path (`default_revisit_at`, anchor
minus `REVISIT_LEAD_DAYS = 3`, or `DATELESS_REVISIT_HORIZON_DAYS = 7` for a
dateless note) and by `record_wake`. `due_for_revisit(workspace, now_ms,
limit)` exists on both `SqlNoteStore` and `InMemoryNoteStore`, and the
curator already calls it every daily slot to decide what to judge.

Nothing else reads it.

### 2.7 The live state on the deployment (2026-09-08)

```
master / norty   54492e2a (+ this branch's parent)
[notes]          enabled=true  curator_enabled=false  candidates_enabled=true
                 candidate_samples unset (default 1)
note table       1 row
```

The one note:

| field | value |
|---|---|
| title | AI Hackathon dinner plan |
| anchor_date | 20260912 |
| revisit_at | 2026-09-09 08:00 HKT |
| established_in | the ridgeline test room |
| body | «Miles Novak said he will attend the AI Thinking Hackathon at Cyberport this Saturday (2026-09-12). He does not know who his teammates are yet, and does not know what to eat for dinner that night.» |

It was created by `note-candidates` on its own, from one message, and its
`revisit_at` falls the morning after this sheet was written. It is the
closest thing to a live test case the tier has.

### 2.8 How this tier measures anything

`evals.run --suite note_candidates` (design 37 §7, issue #118) drives the
REAL `DreamerRuntime` and adaptor over an in-memory store with only the model
live, and grades structurally on the state left behind — `unavailable` is
ERROR, never a passing "nothing". `--samples` is grading samples;
`--samples-per-fire` is `AdaptorSchedule.samples`, the union of N model calls
per fire. `docs/eval-sop.md` records how to run it from a dev machine.

The participation suite (`--suite participation`, 11 cases) grades on
`Trajectory.sent` — whether the room heard anything — with a judge verdict
printed and never binding. **Cited as the shape a speak/stay-quiet corpus
takes**, which is the only thing proactive would borrow from it.

## 3. What is open

1. **The key.** Workspace-keyed (today's other Dreamer adaptors) or
   scope-keyed on `Note.established_in`. §2.2 says what each costs in tools;
   nothing here decides it.
2. **R-31 with no asker.** §2.2. Unrun.
3. **Doctrine.** An occasion nobody asked for inherits a doctrine set with no
   may-speak clause (§2.3). Whether that is a new file, a widened
   `applies_to`, or something else is undecided.
4. **Delivery.** The operator named two paths (§1). §2.4 and §2.5 say what
   each already has. The cost asymmetry — a row commits before anything is
   said and is auditable; a direct `speak` costs nothing when she declines —
   is stated in the session's transcript but is not measured.
5. **Several reminders vs one.** §2.5.
6. **What earns an unprompted message.** Not decided. The operator's position
   is that this is a measurement question, not a philosophy question.
7. **Whether research happens in the Dreamer turn or the fire turn.** Both
   are possible; nothing here rules on it. Note only that the Dreamer has
   budgets, R-27 states and traces already, and that nothing is waiting on it.

## Status / Checkpoint

Handoff only. No code, no objective, no schedule, no doctrine change. The
build is issue #103 (deferred design) and the note tier's own follow-ups are
issue #118. A design node that DECIDES the questions in §3 should supersede
this one rather than edit it, so the fact sheet stays a fact sheet.
