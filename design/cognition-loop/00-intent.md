---
type: design
title: "Intent — the ambient loop becomes one loop"
description: "Where orient and act live today, the passive failure it produces, the one decision (Observe is code; Orient, Decide and Act are one agent session per unit, acting only through reminder rows the fire path composes), why the single agent wins over separate agents, what it costs, the falsifier, and the operator decisions of 2026-09-28."
tags: [nora, ooda, cognition, dreamer, proactive, ambient-loop]
timestamp: '2026-09-28'
edges:
  - {dst: 10-tiers, rel: sequences, provenance: asserted}
  - {dst: 30-cognition, rel: sequences, provenance: asserted}
---

# 00 · Intent

> 2026-10-03 (DEC-277): the shadow period is over. The Cognition session writes live (`[cognition] enabled`) and is the one background writer of notes, events, Standing and her reminders. The clocked note/proactive adaptors, the shadow machinery and `[cognition] mode` / `session_mode` / `shadow_session_seconds` named here are deleted; wording below that describes them is the plan as written, not current behaviour. See `70-migration.md`.


## 1. The loop is named and not built

DEC-176 names Nora's ambient loop: observe, orient, decide, act, closed
through a human (design/ambient-loop/03-decisions.md; DEC-176 has no row in
the decision index). The parts that run it today:

| step | who does it today | when |
|---|---|---|
| observe | ingest (`nora/ingest/pipeline.py`), the presence collector (`nora/presence/collector.py`), memory feed | per message / per fix |
| orient | note-candidates, note-curation, note-merge, culture (Dreamer adaptors); the Standing debrief and the ingest-triggered Standing fold (`nora/ingest/stages/standing_event.py` (deleted 2026-10-03, DEC-277)) | each on its own clock or batch |
| decide | participation's gate and turn; the proactive adaptor's decide turn | quiescence window; hourly |
| act | the fire path (`nora/app/fire.py`) via a reminder the proactive pass set; the participation turn | when a row comes due |

Orient is six independent writers. None sees what another concluded in the
same hour, each re-reads the same messages, and every one of them waits for
its clock. Decide is two deciders joined by a shared log (DEC-179, in the
same file). The loop is real only as a diagram.

## 2. What that produces: a passive Nora

The operator's case, 2026-09-27: a unit's members changed restaurants several
times in one afternoon in untagged chat, and asked Nora only to look places
up. Her event (code: `Gathering`) kept the first restaurant.

From the code, not from a live read-back:

- An event's place, time or people change only when a tag turn calls
  `update_event`. No background path revises one; the curator's discharge can
  create one, behind `[notes] discharge_enabled` (default false).
- A note is revised at most hourly by note-candidates, never from presence,
  and nothing carries a change from a note to the event it links.
- Presence knows where people were at dinner time; nothing asks.

**An event is written only when someone tells her, at the moment they tell
her.** Being told everything is not a reasonable demand of people, and it is
not what an agent is for.

## 3. The decision

> 2026-10-28 (operator, ruled; [30](30-cognition.md#0-the-loop-as-ruled-2026-10-28), [unified-memory/27](../unified-memory/27-the-memory-is-a-graph.md)): the four-adaptor topology below is history. Cognition is the one loop, in three steps: ① comprehends every item on arrival into a memory graph, ② reshapes profiles, culture and lessons, ③ decides and acts on the wake rules. The Character and Lessons adaptors retire into ②; Assess stays outside. "Nothing is a knowledge graph" is reversed by [unified-memory/27](../unified-memory/27-the-memory-is-a-graph.md).

**What Nora is, in one sentence** (operator 2026-09-28, in this bundle's
words): *a member of the unit who keeps a working model of its people, places
and plans, and acts on it unasked, within what each room may be told.* Not an
assistant: README calls her "a persistent member of one circle of friends,
not a bot in their chat", and this bundle keeps that.

**Observe is code. Orient, decide and act are one agent session per unit per
wake. Reflection runs beside it, slower, in its own adaptors.** The Dreamer
runtime is kept (`DreamerRuntime`, `DreamGuard`, grants, budgets,
`RegistryToolSource`) and hosts four adaptors, split by timescale (operator
2026-09-28):

```
code jobs (no model)                         Dreamer runtime (kept): the subconscious
  ingestion ──────────▶ RECORD ─┐              ┌─ Cognition adaptor   fast, per unit, on stimuli
    messages, presence,         ├─ stimuli ───▶│    writes Register, Standing; acts only by remind()
    social, play, perception    │              ├─ Character adaptor   slow, per (unit, entity)
  Rollup builder ──────▶ ROLLUP ┘              │    sole writer of Character
                                               ├─ Lessons adaptor     slow, per unit
the conscious: the turns that speak            │    sole writer of Lessons
  tag turn + debrief (the reflex)              └─ Assess adaptor      after each session, nightly
    writes Register, room Standing                  reports and metrics only, never a tier
  fire turn ◀── remind(): the one bridge from the subconscious
  (Cognition also writes wake_at() into the stimuli: the subconscious scheduling itself)
```

The subconscious drives the conscious (through the pushed blocks and
`remind`); the conscious reaches the subconscious only as Record and shared
state, never as a command ([55](55-self.md) §6).

| adaptor | timescale | wakes on ([20](20-observe.md) §3) | writes | why its own |
|---|---|---|---|---|
| **Cognition** ([30](30-cognition.md)) | minutes, per unit | stimuli: settled, strong cue, Rollup stimulus, batch, max age, time, and its own self-wakes | Register and Standing (beside the tag path and its debrief); acts only through `remind` | orient, decide and act must share one context, or a conclusion is lost before the act |
| **Character** ([50](50-character-and-standing.md) §6) | days, per (unit, entity) | evidence for that entity past a threshold, or staleness | Character, alone | an account is induced from many instances; judging it per message would re-write it from noise |
| **Lessons** ([50](50-character-and-standing.md) §6) | days, per unit | outcomes of her acts past a threshold, or staleness | Lessons, alone | a lesson is induced from the outcomes of many acts, which exist only after the acts |
| **Assess** ([30](30-cognition.md) §6) | after each finished session, and nightly | a finished session; the nightly clock | reports and metrics only, never a tier | judging an act belongs outside it |

- **Observe** ([20](20-observe.md)): every source is ingested into the
  RECORD by code — messages, presence, the social stream, play, and
  perception of media (what a picture shows, whose face is in it). The
  Rollup builder computes the ROLLUP. Both raise wake stimuli into a durable
  table. No model decides what is recorded.
- **Cognition** ([30](30-cognition.md)): one Strands session per unit per
  wake, with an **agenda** code computes from the stimuli (each item bound to
  one room), the read tools that room permits, typed **write verbs** into the
  REGISTER and STANDING tiers ([10](10-tiers.md)), and one **act verb**,
  `remind`: every act is a reminder row whose words the fire path composes
  inside the target room, under that room's guards. It paces itself too:
  `wake_at` asks for its own next look at a subject, so it is woken by the
  world and by its own forecast of when to look next. It replaces
  note-candidates, note-curation, note-merge, the proactive adaptor and the
  ingest Standing fold.
- **Reflection**: the Character adaptor (today's culture adaptor, generalised
  to every entity) and the Lessons adaptor, each the sole writer of its tier.
- **The tag path stays the reflex**: it answers when called, reads what the
  adaptors wrote, and writes the Register and room Standing through the same
  verbs.

**The data model** ([05](05-data-model.md), operator 2026-10-01). Five
layers in one flow, each defined by who writes it: **Authored** (doctrine and
config, humans only) → **Record** (raw observations, ingestion only) →
**Rollup** (computed from the Record by code) → **Notebook** (Nora's
judgement, written only by the subconscious, one writer per tier, citing the Record and Rollup
and never evidence itself) → **Views** (per-reader slices assembled at read
time through one path, never stored), with **Memory** (Honcho) beside the
flow. Nothing writes backwards: what she says is observed into the Record. The
Notebook's tiers are the Register (note, memo, event), Character (culture
included), Standing and Lessons; a tier exists only inside the Notebook.
Speaking turns read Views and speak; to change the Notebook they raise a
stimulus. Nothing is a knowledge graph.

**Amended 2026-10-01 (DEC-249):** the tag path's Register writes and the
debrief's Standing write, described in this section and in D9 and D12, move
behind Cognition at or after the cut-over ([05](05-data-model.md) §9, E2).

## 4. One agent, not a conductor over agents

> 2026-10-28: still holds. The three steps of [30](30-cognition.md#0-the-loop-as-ruled-2026-10-28) hand off only through committed, code-admitted rows, never prose into a prompt; orient, decide and act stay one context in step ③ ([30](30-cognition.md) §8).

**The topology refines this, and does not reverse it.** Orient, decide and
act stay one context: the Cognition adaptor. Reflection (what an entity is
like, what she has learned) is a different job on a different timescale,
the split Honcho makes between its deriver and its dream pass and Letta
between its primary agent and its sleep-time agent. The adaptors never hand
state to each other: each reads the tiers the others wrote.

This session's first draft (commit 0e060cf) had a conductor plan and code
dispatch separate specialist agents. The operator's argument replaces it:
separate agents must hand state across a boundary, and every boundary loses
context. That draft had to invent a typed plan, `focus` ids and a default
order to carry what one agent simply knows. In one session, a state
transition is a tool call whose result returns into the same context.

The objections the first draft raised, and where each is answered now:

| objection | answered by |
|---|---|
| a model does not convict itself (the Standing verdict read 21/21 grounded) | writes are **tool calls code validates** (citations resolve, schema holds, modes allowed, provenance stamped); what code cannot check, an **assess** pass measures afterwards (design/dreamer-agent-loop/17-failure-classes.md §2; [30](30-cognition.md) §3, §6) |
| room vs unit disclosure (DEC-105, R-49) | every agenda item binds a room, and lineage is applied at assembly, per field, when a read returns and when a block renders ([10](10-tiers.md) §3). The unit is the session's read boundary, transcripts of every room included (DEC-231, operator 2026-09-28) |
| a model told of a cap declines the work | caps stay in code, never in words; an agenda item not closed returns to the next session with a strike instead of being dropped |
| one large prompt dilutes a cheap model | code injects the skill text for the agenda kinds present, not text in the base prompt (ADR 0016, proposed, is the placement policy) |
| per-pass eval suites exist | their cases become agenda-item cases; the verdicts they grade are the same rows |

## 5. What gets worse

- **One loop is one blast radius.** A bad session can touch notes, memos,
  events and Standing at once. Every write is revisable with history, and the
  session's trace is the whole log of what it did.
- **A heavier session.** One agent holding a busy unit's day is a longer
  context than any one adaptor had. Measured in step 3 ([70](70-migration.md)).
- **More model spend.** Every story frame and reel is a vision call; the cost
  model is [70](70-migration.md) §6.
- **The existing adaptor suites must be re-pointed**, not merely re-run.

## 6. The falsifier

- **The dinner case.** A recorded afternoon in which the plan moves twice in
  untagged chat and presence puts people at the last place: the event must
  end at the last place, citing the messages and the co-location episode,
  within one session of the last change; its presence-lineaged fields must
  render in no room without the location flag. Today it stays at the first
  place forever. At step 3 the case is truncated to what exists then
  ([40](40-register.md) §5).
- **Nothing lost in the switch.** For a shadow week, every row the clocked
  adaptors wrote is also written, or deliberately closed as not needed, by a
  cognition session on the same messages. A close the model wrote is its own
  word and does not count as evidence (DEC-252, amended 2026-10-02): only a
  matched write, or a close code proves, is.
- **Latency.** For a change carrying a strong cue (a date or time, a keep
  instruction, an RSVP, or a place link — [20](20-observe.md) §3's list):
  median time from a settling message to the row that records it stays
  under ten minutes via the 120 s cue-quiet wake. A cue-free change (a plan
  moved by naming a venue, today, carries no cue — the dinner case above)
  instead waits 1800 s of quiet at minimum, and up to `max_age_seconds`
  (3600 s) in a room that keeps talking (operator 2026-09-29, was 600 s) —
  no faster than today's clocked adaptors, and still never for events.
  (`[dreamer] quiet_seconds` is also 1800 today, independently configured;
  cognition's wake keys are its own, [20](20-observe.md) §3.)

## Status / Checkpoint

**2026-09-28 — proposed, design only; revised the same day after four
independent audits (architecture, terminology, grounding, privacy).** Nothing
is built. DEC numbers are taken at merge (`nora-decision`). Operator
decisions of 2026-09-28, binding:

- **D1** Every act is a `remind()` row; the fire turn composes the words in
  the target room under its guards; no `speak` or `ask` path; write verbs are
  granted through `AdaptorGrant` by a new DEC at step 0.
- **D2** `told` means any person stated it in words, including members
  agreeing among themselves; `abduced` means concluded from evidence nobody
  stated; her own words are the sixth mode, `said`, Standing only.
- **D3** Deviation is a stimulus, not a rule; the only act guards are the
  general ones; deviation is raised only on sources proven healthy.
- **D4** Step 8 stays whole.
- **D5** Memo bodies are exempt from `dream_screen`'s key-shape check and
  render normally.
- **D6** Nora never changes doctrine, in whole or in part; it changes only
  when the humans all agree. Her growth lives only in her own blocks, below
  the doctrine, and a disagreement becomes a tension row for the operator.
- **D7** Lessons, a tier of learned practice per unit: effective immediately,
  no approval step, visible on the portal and reversible by id.
- **D8** Everything is versioned in system time, so Nora can be restored as
  of any T for an eval; memory (Honcho) is the exception. Forget is a soft
  delete (hidden, reversible), so a restore is complete.
- **D9** Topology: the Dreamer hosts four adaptors split by timescale —
  Cognition (fast, writes Register and Standing, acts only by `remind`),
  Character (slow, the culture adaptor generalised, sole writer of
  Character), Lessons (slow, sole writer of Lessons), Assess (reports only).
- **D10** The wake policy of [20](20-observe.md) §3: code appends a stimulus
  per message and nothing else; a code evaluator wakes Cognition on the
  first of settled, strong cue, Rollup stimulus, batch, max age, time or a
  self-wake, behind a watermark and brakes; values sized from a shadow week.
- **D11** Cognition may request, move and cancel its own next wakes
  (`wake_at`, `wake_move`, `wake_cancel`); code surfaces a pending self-wake
  whenever its subject changes and never cancels one itself.
- **D12** The conscious and the subconscious: the adaptors are her subconscious, the speaking turns
  her conscious; the subconscious drives the conscious, and the conscious
  reaches the subconscious only as Record and shared state.

**2026-10-01 — the data model agreed and corrected twice the same day**
(DEC-249, [05](05-data-model.md)): five layers (Authored, Record, Rollup,
Notebook, Views) with Memory beside them; Layer › Tier › Kind › Entry, a tier
inside the Notebook only; one entry; evidence only the Record and Rollup; the
subconscious the Notebook's only writer, one writer per tier; memo first on the entry, right
after the shadow week.
