---
type: design
title: "Intent — the ambient loop becomes one loop"
description: "Where orient and act live today, the passive failure it produces, the one decision (Observe is code; Orient, Decide and Act are one agent session per unit), why the single agent wins over separate agents, what it costs, and the falsifier."
tags: [nora, ooda, cognition, dreamer, proactive, ambient-loop]
timestamp: '2026-09-28'
edges:
  - {dst: 10-tiers, rel: sequences, provenance: asserted}
  - {dst: 30-cognition, rel: sequences, provenance: asserted}
---

# 00 · Intent

## 1. The loop is named and not built

DEC-176 names Nora's ambient loop: observe, orient, decide, act, closed
through a human. The parts that run it today:

| step | who does it today | when |
|---|---|---|
| observe | ingest (`nora/ingest/pipeline.py`), the presence collector (`nora/presence/collector.py`), memory feed | per message / per fix |
| orient | note-candidates, note-curation, note-merge, culture (Dreamer adaptors); the Standing debrief and the ingest-triggered Standing fold (code: `standing.event_fold`) | each on its own clock or batch |
| decide | participation's gate and turn; the proactive adaptor's decide turn | quiescence window; hourly |
| act | the fire path (`nora/app/fire.py`) via a reminder the proactive pass set; the participation turn | when a row comes due |

Orient is six independent writers. None sees what another concluded in the
same hour, each re-reads the same messages, and every one of them waits for
its clock. Decide is two deciders joined by a shared log (DEC-179). The loop is
real only as a diagram.

## 2. What that produces: a passive Nora

The operator's case, 2026-09-27: a unit's members changed restaurants several times in
one afternoon in untagged chat, and asked Nora only to look places up. Her
`Gathering` kept the first restaurant.

From the code, not from a live read-back:

- A gathering changes only when a tag turn calls `update_event`. No
  background path writes gatherings at all.
- A note is revised at most hourly by note-candidates, never from presence,
  and nothing carries a change from a note to the gathering it links.
- Presence knows where people were at dinner time; nothing asks.

**A gathering is written only when someone tells her, at the moment they
tell her.** Being told everything is not a reasonable demand of people, and it
is not what an agent is for.

## 3. The decision

**Observe is code. Orient, decide and act are one agent session per unit per
wake.**

- **Observe** ([20](20-observe.md)): every source is ingested into the
  RECORD by code — messages, presence, the social graph, and perception of
  media (what a picture shows, whose face is in it). Ingestion raises wake
  signals. No model decides what is recorded.
- **The cognition session** ([30](30-cognition.md)): one Strands agent per
  unit, woken by signals, with an **agenda** code computes from the signals,
  every read tool, typed **write verbs** into the REGISTER, CHARACTER and
  STANDING tiers ([10](10-tiers.md)), and **act verbs** that leave through the
  fire path. The proactive adaptor becomes its act step.
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
| a model does not convict itself (the Standing verdict read 21/21 grounded) | writes are **tool calls code validates** (citations resolve, schema holds, provenance stamped), not a verdict the model grades; an **assess** pass reads finished sessions afterwards (design/dreamer-agent-loop/17-failure-classes.md §2) |
| room vs unit disclosure (R-31) | the unit as the session's read boundary: the 2026-09-09 ruling covers proactive; for cognition it is open until step 0 ([70](70-migration.md) §4); anything leaving for a room passes the same egress screens a reply does |
| a model told of a cap declines the work | caps stay in code, never in words; an agenda item not closed stays pending for the next session instead of being dropped |
| one large prompt dilutes a cheap model | each kind of work is a **skill** loaded when the agenda holds that kind of item (ADR 0016's mechanism), not text in the base prompt |
| per-pass eval suites exist | their cases become agenda-item cases; the verdicts they grade are the same rows |

## 5. What gets worse

- **One loop is one blast radius.** A bad session can touch notes, memos,
  events and Standing at once. Every write is revisable with history, and the
  session's trace is the whole account of what it did.
- **A heavier session.** One agent holding a busy unit's day is a longer
  context than any one adaptor had. Measured in step 3 ([70](70-migration.md)).
- **The existing adaptor suites must be re-pointed**, not merely re-run.

## 6. The falsifier

- **The dinner case.** A recorded afternoon in which the plan moves twice in
  untagged chat and presence puts people at the last place: the event must
  end at the last place, citing the messages and the presence stays, within
  one session of the last change. Today it stays at the first place forever.
- **Nothing lost in the switch.** For a shadow week, every row the clocked
  adaptors wrote is also written, or deliberately closed as not needed, by a
  cognition session on the same messages.
- **Latency.** Median time from a settling message to the row that records it
  under ten minutes; today up to an hour, and never for gatherings.

## Status / Checkpoint

**2026-09-28 — proposed, design only.** Nothing is built. DEC numbers are
taken at merge (`nora-decision`).
