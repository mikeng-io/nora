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

## 1. The loop is named and not built

DEC-176 names Nora's ambient loop: observe, orient, decide, act, closed
through a human (design/ambient-loop/03-decisions.md; DEC-176 has no row in
the decision index). The parts that run it today:

| step | who does it today | when |
|---|---|---|
| observe | ingest (`nora/ingest/pipeline.py`), the presence collector (`nora/presence/collector.py`), memory feed | per message / per fix |
| orient | note-candidates, note-curation, note-merge, culture (Dreamer adaptors); the Standing debrief and the ingest-triggered Standing fold (`nora/ingest/stages/standing_event.py`) | each on its own clock or batch |
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

**What Nora is, in one sentence** (operator 2026-09-28, in this bundle's
words): *a member of the unit who keeps a working model of its people, places
and plans, and acts on it unasked, within what each room may be told.* Not an
assistant: README calls her "a persistent member of one circle of friends,
not a bot in their chat", and this bundle keeps that.

**Observe is code. Orient, decide and act are one agent session per unit per
wake.**

- **Observe** ([20](20-observe.md)): every source is ingested into the
  RECORD by code — messages, presence, the social stream, play, and
  perception of media (what a picture shows, whose face is in it). Ingestion
  raises wake signals. No model decides what is recorded.
- **The cognition session** ([30](30-cognition.md)): one Strands agent per
  unit, woken by signals, with an **agenda** code computes from them (each
  item bound to one room), the read tools that room permits, typed **write
  verbs** into the REGISTER, CHARACTER and STANDING tiers ([10](10-tiers.md)),
  and one **act verb**, `remind`: every act is a reminder row whose words the
  fire path composes inside the target room, under that room's guards. The
  proactive adaptor becomes this act step.
- **The tag path stays the reflex**: it answers when called, reads what
  cognition wrote, and writes through the same verbs.

## 4. One agent, not a conductor over agents

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
| room vs unit disclosure (DEC-105, R-49) | every agenda item binds a room, and lineage is applied at assembly, per field, when a read returns and when a block renders ([10](10-tiers.md) §3). Whether the unit is the read boundary for all its rooms' transcripts is to be confirmed at step 0 ([70](70-migration.md) §4) |
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
  cognition session on the same messages.
- **Latency.** Median time from a settling message to the row that records it
  under ten minutes; today up to an hour, and never for events.
  (`[dreamer] quiet_seconds` is 1800 today; cognition's wake keys are its
  own, [20](20-observe.md) §3.)

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
- **D3** Deviation is a signal, not a rule; the only act guards are the
  general ones; deviation is raised only on sources proven healthy.
- **D4** Step 8 stays whole.
- **D5** Memo bodies are exempt from `dream_screen`'s key-shape check and
  render normally.
