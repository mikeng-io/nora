---
type: design
title: "Intent — one Dreamer that is woken, not seven that are clocked"
description: "What runs today and how it is triggered, why adding the event and memo passes as two more adaptors is the wrong shape, the four shapes compared, the one chosen, and the falsifier."
tags: [nora, dreamer, conductor, trigger, event-driven, strands]
timestamp: '2026-09-27'
edges:
  - {dst: 10-vocabulary, rel: sequences, provenance: asserted}
  - {dst: 20-trigger, rel: sequences, provenance: asserted}
---

# 00 · Intent

## 1. What runs today

| unit | what wakes it | key | tools |
|---|---|---|---|
| note-candidates | the 300 s dream tick, then watermark lag ≥ 1 and 3600 s since last | room | read-only, capped (design 43 step 1) |
| note-curation | the tick, a daily slot at HK midnight (off by default) | unit | none |
| note-merge | the tick, an hourly slot (`[notes] merge_mode`, off by default) | unit | none |
| culture | the tick, a daily slot | room | memory, roster, `where_been`, web |
| proactive decide | the tick, hourly; renders only on the daily floor or a change while the home room is quiet | unit | `remind` + reads |
| Standing debrief | the end of a tag turn (not a Dreamer adaptor) | room | — |
| Standing event fold | ingest, every `[standing] fold_batch` messages | room | — |
| participation | ingest, a quiescence window closing | room | — |

Sources: `nora/app/runtime.py` (`dream_loop`, `_dream_adaptor_once`),
`nora/app/composition.py` (`dream_adaptors`),
`nora/ingest/stages/standing_event.py`, `nora/app/participation.py`.

Three facts carry the argument:

1. **Every Dreamer adaptor is clocked.** The runtime learns a room spoke only
   by polling each adaptor's watermark lag every tick. There is no event bus,
   no LISTEN/NOTIFY, no hook from ingest into the Dreamer.
2. **The only message-driven background work sits outside the Dreamer**: the
   Standing event fold and participation, each an ingest stage with its own
   debounce.
3. **Design 06 already decided the Dreamer should be woken, not clocked**
   ([06-trigger](../dreamer-agent-loop/06-trigger.md) §4: "a message arriving
   is exactly the event that should be asking the question"). It was never
   built; design 32's `AdaptorSchedule` shipped instead.

## 2. Why two more adaptors is the wrong shape

Two jobs are wanted: an **event** pass that finds gatherings in the transcript
(nothing does today; the curator's discharge makes one only when a note
closes, off by default), and a **memo** pass that keeps what the group settled
(nothing can hold it today; see [10-vocabulary](10-vocabulary.md)). As
adaptors they would be two more schedules, two more watermarks per room, two
more budget sections, and two more passes reading the same new messages the
note pass already read. The count reaches seven clocked passes plus three
off-clock folds, and the cost of each new job is linear in wiring rather than
in judgement.

## 3. The four shapes compared

| | A · status quo + 2 adaptors | B · one agent | C · Strands `Swarm` | **D · conductor + specialists** |
|---|---|---|---|---|
| woken by a message | no, clock | possible | possible | **yes, one seam** |
| adapts to what was said | inside each pass | yes | yes | **the plan is chosen per change** |
| a new job costs | adaptor, schedule, watermark, budget, wiring | a longer prompt | an agent + handoff rules | **an objective, a grant and a sink** |
| write-side guards | per pass | one place for every kind of write | handoffs blur who wrote what | **per specialist, unchanged** |
| room vs unit disclosure (R-31) | per pass | **broken: one agent spans both key kinds** | same | **per specialist** |
| a second call judges the act | yes | **no** | partly | **yes** |
| measurable | per-pass suites exist | one trajectory | nondeterministic handoffs | **per specialist + the plan** |

B and C fail on things this repo has already measured:

- **One completion does not judge its own act.** The Standing fold's
  groundedness verdict read 21/21 true while writing and judging in one call
  (design/standing-split-fold/00-intent.md); design 17 §2 generalises it.
- **A shared budget makes a model decline work.** Told of a hard memory cap,
  the proactive pass declined the note instead of spending less (`d02` 5/5 →
  1/5, the comment above `[dreamer.budgets.proactive]` in
  `config.example.toml`). One agent holding every job's budget is that
  pressure, multiplied.
- **"Making everything an agent is not the goal"**
  ([03](../dreamer-agent-loop/03-no-agent-loop.md) §7.5).

C has a structural problem of its own, read from the SDK
(strands-agents 1.50.2, `strands/multiagent/swarm.py`): agents hand off
through a `handoff_to_agent(agent_name, message, context)` tool into a
`SharedContext`. The receiving agent's input is the sender's prose. That is a
model writing another model's input, which is the self-talk the note tier's
one structural brake exists to stop (GLOSSARY, **note**: "Nothing Nora says
can create, revise or close a note").

## 4. The decision

**D.** One trigger seam fed by ingest ([20](20-trigger.md)); one conductor per
unit that reads the change once and returns a typed plan
([30](30-conductor.md)); the existing adaptors as specialists, dispatched by
code, each with its objective, grant, budget and guards intact
([40](40-specialists.md)). The event and memo passes are specialists, not
schedules. The Standing debrief stays on the tag path: it runs inside the
reply slot, and its separation is for impartiality.

**What is refactored is the runtime, not the adaptors.** `dream_loop`,
`_dream_adaptor_once`, `AdaptorSchedule` and the per-adaptor watermarks are
replaced; the `DreamAdaptor` port (`nora/domain/ports.py`) gains one cheap
method and loses its schedule. Everything behind `accept` and `commit` is
untouched. [40](40-specialists.md) §1 lists it line by line.

## 5. The falsifier

In shadow ([50](50-migration.md) step 2), for the same week of ingest:

- **Coverage.** Every job the clock ran that wrote a row is also in the
  conductor's plan for that change. A job the clock would have written from
  and the plan omitted is a miss; more than one miss in 50 plans that is not
  caught by the daily floor means the planner is wrong, and D reverts to A
  with the event trigger only.
- **Latency.** Median time from the message that settles a thing to the row
  that records it drops from the clock's (up to an hour) to under ten
  minutes.
- **Cost.** Model tokens per unit-day do not rise by more than the
  conductor's own calls; the re-reads the plan removes should pay for them.

If coverage holds and cost does not, the conductor's model call is the
problem, and the plan can fall back to the deterministic `wants` alone
([30](30-conductor.md) §3).

## Status / Checkpoint

**2026-09-27 — proposed, design only.** Nothing is built. DEC numbers are
taken at merge, per `nora-decision`.
