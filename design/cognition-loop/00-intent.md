---
type: design
title: "Cognition is one OODA loop — Nora's other half"
description: "Entry point of the Cognition bundle. Nora has two halves: the conscious turn answers when called; Cognition, her subconscious, acts when nobody asked. It is one observe-orient-decide-act loop per unit: code observes, one session orients against memory and the notebook with every read Nora has, decides whether anything is worth doing and when, and acts only through reminders and its own wakes. States the loop, what code owns and what the model owns, the brakes, the simplicity rules, the falsifier, which document owns each part, and the per-item machinery the operator ruled deleted on 2026-11-11."
tags: [nora, ooda, cognition, subconscious, proactive, ambient-loop, dreamer]
timestamp: '2026-11-11'
edges:
  - {dst: 18-execution-and-time, rel: sequences, provenance: asserted}
  - {dst: 20-observe, rel: sequences, provenance: asserted}
  - {dst: 30-cognition, rel: sequences, provenance: asserted}
  - {dst: ../unified-memory/29-one-cognition-session.md, rel: depends_on, provenance: asserted}
---

# 00 · Cognition is one OODA loop

## 1. Nora's other half

Nora has two halves (DEC-176; [39](../dreamer-agent-loop/39-proactive-ooda-loop.md) §1,
whose frame this keeps):

- **The conscious half answers when called.** A tag, a reply or an admitted
  occasion opens a turn in one room; she reads the situation and speaks, acts
  or stays silent.
- **The subconscious half acts when nobody asked.** It is Cognition. It keeps
  a working model of the unit's people, places, plans and her own position,
  and decides, unasked, whether anything deserves attention and when.

What Nora is, in one sentence: *a member of the unit who keeps a working model
of its people, places and plans, and acts on it unasked, within what each room
may be told.* She is not an assistant waiting to be told everything. The
founding case: plans move from one restaurant to another in untagged chat, and
an event that only changes when someone tags her keeps the first restaurant
for ever. Being told everything is not a reasonable demand of people, and not
what an agent is for.

## 2. The loop

```
 the world ──▶ OBSERVE (code) ──▶ stimuli ──▶ wake ──▶ ONE COGNITION SESSION ───────────────┐
   messages, presence, social,     durable     settled, strong cue,  ORIENT  what changed, and   │
   play, media perception, the     table       batch, max age, time,         what it connects to │
   outcomes of her own acts                    her own wake_at       DECIDE  is anything worth   │
                                                                             doing, and when?    │
                                                                     ACT     a reminder, a wake, │
                                                                             or nothing          │
                ▲                                                                                │
                └──── her acts and what people do next are observed like everything else ◀──────┘
```

| step | who | what it does |
|---|---|---|
| **Observe** | code, no model | Ingestion records everything into the Record (messages, presence, the social stream, play, what media shows); the Rollup computes what it adds up to (stays, co-locations, rhythms); both raise durable stimuli ([20](20-observe.md), [25](25-rollup.md), [22](22-social-stream.md)). |
| **Orient** | the session | Read what came in against what she already knows: search and read memory and the notebook, and follow anything new she forms. Update her understanding: graph statements, profiles and notebook entries (notes, events, memos, topics, lessons) and her Standing. Orient can end in changed understanding and no act. |
| **Decide** | the session | Given the whole unit, is anything worth doing, for whom, and when? The answer is often no. |
| **Act** | the session writes, the conscious half speaks | `remind` writes an intention for a room at a time, and the fire path opens a conscious turn then, which looks again and speaks, acts or stays silent ([35](35-the-proactive-turn-reviews-the-situation.md)). `wake_at` brings Cognition itself back when time or a change could settle something. Cognition never speaks. |

The loop closes **through people**: what she says, and what they do next, is
observed into the Record and becomes the next session's input.

## 3. Code owns observation and the brakes; the model owns judgement

**Code** decides *when* she looks, *what she is shown first*, and *what may be
committed*. It does not decide what matters.

- **When:** a code evaluator wakes one session per unit on the first of
  settled, strong cue, batch, max age, time or a due self-wake ([20](20-observe.md) §3).
  Execution, ticks and ownership: [18](18-execution-and-time.md).
- **What she is shown first:** the clock and how long since she last looked;
  the new stimuli in their conversations (her own recent sends included, marked
  hers); the members and their profiles; what is open (notes, events, memos,
  Standing); and the graph around the batch's people, names, places and
  topics, with resolution candidates ([context-assembly/05](../context-assembly/05-the-cognition-context.md)).
  A block says what it is, how old it is and whether it is empty or
  unavailable. A prefetch is a starting point, not a boundary.
- **What she can reach:** every read Nora has, in every Cognition session,
  live or historical: memory (`memory_search`, `memory_read`), the notebook
  reads, transcripts of every room of the unit (DEC-231), media, the social
  reads, the web and places. An unwired read answers `unavailable`, never
  absent.
- **What may be committed:** every write is a tool call that code validates
  before commit ([30](30-cognition.md) §3): citations resolve to what she was
  shown or fetched, schema and modes hold, revisions name their base, lineage
  is computed from what is cited, the egress screen runs, provenance is
  stamped. A refusal returns its reason so she can repair it.
- **What may reach a room:** acts are guarded at fire time, not at decide
  time: disclosure re-run in the target room, rate and pending caps, an
  abduced value goes out as a question, and a person's own reminders are
  theirs.
- **How much:** rounds, tokens and time, never per tool, never stated in the
  prompt (DEC-247). A session that stops at a bound has failed, and its
  stimuli stay pending.

**The model** owns what code cannot compute: what an item means in its
conversation, whether it connects to something already held, whether
understanding changed, whether anything is owed or worth raising, and when to
look again. She judges from evidence, cites what a judgement rests on, keeps
uncertainty as uncertainty, and may conclude that nothing needs doing.

## 4. Simplicity rules

These keep the loop the size of the idea.

1. **One loop, one session.** No separate comprehend, reshape and act
   sessions, and no conductor over agents. A boundary between agents loses
   context ([unified-memory/29](../unified-memory/29-one-cognition-session.md)).
2. **Orient is about the situation, not each message.** She reads what
   changed and writes what changed her understanding. A message that adds
   nothing beyond what code already records (who said, shared, asked, replied
   or reacted) needs no statement.
3. **Search before creating.** Prefetch covers what code can seed. Anything she
   forms during the session (a combined fact, a new entity, a link between two
   people) is searched for before it is written; admission returns existing
   candidates for a proposed new entity.
4. **The prompt states the loop and the few rules code cannot enforce.**
   Purpose, the OODA steps, cite-what-you-rest-on, no cross-room surfacing, and
   confidences stay unwritten. Everything code enforces is the tool's refusal
   text, not prompt text. A section about a tool appears only when the session
   offers that tool.
5. **No guard between the halves.** Cognition may write a reminder that turns
   out stale; the conscious turn that fires it looks again. A wrong act is a
   defect in that turn's input or framing, fixed there, not a new screen
   ([35](35-the-proactive-turn-reviews-the-situation.md) §1).
6. **A guard needs a consequence.** A check, cap or state machine exists only
   for a harm shown to happen (AGENTS.md, «Build the least that works»).
7. **History does not act.** The historical lane orients in source-time order
   with every read, but holds no act verb: two months of history never
   creates a reminder, a present-day note or a wake.

## 5. Per-item machinery is deleted

The operator ruled on 2026-11-11 to delete the per-item machinery below. It
had grown on the loop and turned orientation into transcription. Measured on the 15-case memory replay of 2026-11-10 (candidate
`dff3cf8b2`): 16 `memory_propose` calls produced 25 readings, 0 relations and
0 corrections, and the disputed readings were chat events ("shared a link",
"asked for the music to be stopped"). The prompt required every agenda item
to be covered, so the session covered each one.

| machinery | where | ruling | why |
|---|---|---|---|
| Per-input coverage: every agenda input needs a saved statement citing it or an explicit `nothing_here`, else it stays open | unified-memory/29 §2 | **delete** | Forces a statement per message, which is the noise measured above. Completeness is kept by the stimulus rule below, not by per-message bookkeeping. |
| Per-item `close(item, reason)` with `written` / `no_change` / `waiting` / `covered`, strikes and `max_strikes` | 30 §2, DEC-250, DEC-279 | **delete** | One session outcome replaces it. A session that ends on its own consumes its stimuli; a failed or bounded session leaves them pending (kept). A matter that needs another look is a `wake_at`, which already carries a reason and a time. |
| Act verbs locked until a `memory_propose` is admitted | unified-memory/29 §2 | **delete** | It rewards writing something before acting. Orient-before-decide is the loop's order, stated once in the prompt. No incident shows acts without orientation. |
| Per-agenda-kind skill injection | 30 §4 | **replace** | One short prompt, with sections gated by the tools the session offers (rule 4). |
| `max_items` and surprise ordering | 30 §1 | **keep, as a budget** | It bounds input size per session and defers, never drops. |
| Room binding | 30 §1 | **keep, for acts and lineage** | The unit is the read boundary (DEC-231). A room matters where an act lands and where a written line may render. |
| Write validation, act guards, budgets, the Assess adaptor | 30 §3, §5, §6 | **keep** | These are the brakes, and no step grades itself. |
| `wake_at` / `wake_move` / `wake_cancel` | 20 §3 | **keep** | Her own forecast of when to look again is half of being proactive. |

Unchanged by any ruling here: stimuli are never consumed uncomprehended; a
stimulus that fails five times is parked for the operator, not discarded
(unified-memory/29 §3).

The reading that would reopen this: a replay on the same frozen sample in
which the session without per-input coverage misses supported facts that the
coverage contract caught, beyond draw noise.

## 6. The falsifier

- **The dinner case.** A recorded afternoon in which the plan moves twice in
  untagged chat and presence puts people at the last place: the event ends at
  the last place, citing the messages and the co-location, within one session
  of the last change, and its presence-derived fields render in no room
  without the location flag.
- **Connected, not transcribed.** On a frozen replay, statements attach to
  existing entities and relations where the graph holds them; chatter adds
  nothing; a correction supersedes rather than duplicates.
- **Silence is measured.** On the proactive cases, an act that people later
  correct, a reminder nobody answered and a repeated question count against
  the session that caused them (30 §6).

## 7. Which document owns what

| part | owner |
|---|---|
| Execution layers, ticks, ownership | [18 execution and time](18-execution-and-time.md) |
| Observe: sources, stimuli, wake rules, self-wakes | [20 observe](20-observe.md), [22 social stream](22-social-stream.md), [25 rollup](25-rollup.md), [60 faces](60-faces.md) |
| The session: inputs, verbs, enforcement, budgets, Assess | [30 cognition](30-cognition.md) |
| Act: the conscious turn a reminder opens | [35 the proactive turn](35-the-proactive-turn-reviews-the-situation.md) |
| Understanding and connecting: graph, proposal contract, backfill | [unified-memory](../unified-memory/00-intent.md), [29 one session](../unified-memory/29-one-cognition-session.md) |
| Notebook kinds and Standing | [notebook-memory-types](../notebook-memory-types/00-design.md), [20 standing](../notebook-memory-types/20-standing.md) |
| What the session is shown | [context-assembly/05](../context-assembly/05-the-cognition-context.md) |
| Data layers, tiers, schemas, self | [05](05-data-model.md), [10](10-tiers.md), [12](12-schemas.md), [55](55-self.md) (40 and 50 are their earlier per-tier detail) |
| How it was built, and what it learned from | [70 migration](70-migration.md), [80 prior art](80-prior-art.md) |

Decisions still in force from this bundle's history: DEC-176 (two
backbones), DEC-231 (the unit is the read boundary), DEC-247 (rounds, tokens
and time only), DEC-249 (the data model), DEC-277 (Cognition is the one
background writer). The operator's founding decisions of 2026-09-28 (D1–D12)
and their amendments are in git history and [70](70-migration.md); where they
conflict with this document, this document holds.
