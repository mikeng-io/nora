---
type: design
title: "The ledger: decisions, commitments and threads, revised by id"
description: "The three kinds of Standing entry, their typed columns and Markdown bodies under 12-schemas' one grammar, the operations a writer may emit, the rendered line format a model reads, and the room and unit scopes."
tags: [nora, standing, ledger, decisions, commitments, threads, schema, render]
timestamp: '2026-10-25'
edges:
  - {dst: 30-turn-records, rel: depends_on, provenance: asserted}
  - {dst: ../cognition-loop/12-schemas.md, rel: amends, provenance: asserted}
  - {dst: ../standing-compact/30-working-set.md, rel: supersedes, provenance: asserted}
---

# 20 · The ledger

Standing becomes a set of **entries**, each with a stable id, typed columns
for what code computes on, a Markdown body for what a model must understand,
and an append-only revision chain. It follows
[12-schemas](../cognition-loop/12-schemas.md) §2 whole: fixed English
headings, content in the room's language, every claim cites, refused and never
repaired, code-written sections never a model argument.

## 1. Why entries, not a better paragraph

A paragraph can only be replaced. An entry can be **revised by id**: a turn
that moves one decision touches that decision and nothing else, and the other
entries are carried forward byte for byte. Loss stops being a property of
storage and becomes a choice made at render time, against a budget, and it is
visible there (§5).

## 2. Decision

Her judgement on one question: open while she is still weighing it, closed
when it is settled or no longer hers to weigh.

**Columns:** `id`, `scope` (room or unit, §6), `status` (`open`, `closed`,
`dropped`), `subject` (one line), `note_id` (nullable: the group's question
this is her stance on, [10](10-what-standing-holds.md) §4), `opened_at`,
`last_revised_at`, `closed_at`, and the lineage columns migration
`0073_lineage` gave Standing rows.

**Body:**

| section | required | holds |
|---|---|---|
| `Options` | yes | one line per option: the option, its assessment (a score where she gave one, and its reasons), cited |
| `Criteria` | no | what the options are judged on ("worst-case travel, open past 11, under $300 a head"), cited where someone stated it |
| `Leaning` | open: yes | which option and why, in one or two lines; or `none yet` |
| `Blocked on` | no | what she needs before she can settle it, naming a person or a fact |
| `Reopen if` | no | the reading that would reopen a closed decision or move an open one; the bundle-wide convention of stating the reversal condition, applied to her |
| `Closed as` | closed: yes | the outcome and its grounds, cited |
| `History` | code | every revision, rendered from the chain |

A **recommendation run** (the preference and ranking engine's bundle) writes
its result as a decision: candidates as `Options` with their scores, the
recipe's constraints as `Criteria`. That is the only coupling between the two
bundles.

## 3. Commitment

Something she said she would do.

**Columns:** `id`, `scope`, `status` (`open`, `done`, `released`), `owed_to`
(person ids or the room), `due_at` (nullable), `made_at`, `closed_at`,
lineage. **Body:** `Owed` (what, in one line, citing the message where she
said it), `Done as` (on close, citing the message or turn that discharged it).

What members owe each other stays in the note's `Owed`
([12-schemas](../cognition-loop/12-schemas.md) §3).

## 4. Thread

A pointer to a topic the room keeps returning to. **Code-heavy on purpose**:
a thread's facts (which rooms, which message range, when it last moved) are
computed from the Record, and the model contributes only the topic line and
which messages belong to it.

**Columns:** `id`, `scope`, `topic` (one line, the model's), `last_moved_at`,
`note_ids`, `decision_ids`, and one **span per room** (room, first and last
message id, first and last message time), computed by code. **No body.** A
thread restates nothing.

**How a turn opens it.** Today's readers cannot: `load_transcript` takes only
`limit` and a time `before`, and `room_read` adds a room but no message range
(`nora/agent/tools.py`). So the readers gain a `thread` argument, the thread's
id as rendered, which code resolves to the spans the calling room may see; the
model never handles a message-id range. It is a change to two tools' arguments,
measured with `tool_selection` ([40](40-verification.md) §6), and it ships
before the topic-recall gate ([50](50-migration.md) step 2).

A thread is what fixes "she loses the topic after 20 messages": a topic last
discussed 40 messages ago stays in the prompt as one line with an age, and
she opens it by id.

## 5. The rendered form

What a turn reads, inside the existing envelope (ADR 0012, ADR 0013: a tagged
block that says what it is). One dense line per entry, ages rendered,
citations in the house grammar (`[ids]` for messages, `rollup(...)` for Rollup
keys, and the new `turn(...)` for turn records, [30](30-turn-records.md) §3):

```
<standing description="…">
## Decisions
d4 · open · 3d · r5 · Halloween venue · note 118
  Options: Sai Kung bar .8, on 3 of 4 routes, open past 1am turn(t812) · TST rooftop .6, Kit +40 min from work turn(t812)
  Criteria: worst-case travel, open past 11 [9087]
  Leaning: Sai Kung · Blocked on: when Kit finishes
  Reopen if: Ana rules out Sai Kung, or a booking fails
## I owe
c2 · open · 5h · r1 · ferry times for Saturday, to the room [9104]
## Threads
th7 · 2h · Halloween plans · signal-dinner, discord-general · d4, note 118
th3 · 6d · co-op games for the weekend · discord-general
</standing>
```

- **Ages on every entry**, from `last_revised_at` or `last_moved_at`. A
  judgement about whether something is still open turns on time, so the input
  carries the time (the Relevance Gate measurement of 2026-09-09/10).
- **Budget.** Rendered newest-moved first under a token budget per section;
  what does not fit renders as a count ("+3 older closed decisions"), and
  `standing_read` returns everything. The budget is a render choice, so it can
  be tuned without losing anything.
- **Closed decisions** render only while young (a horizon sized from the first
  month, as [12-schemas](../cognition-loop/12-schemas.md) §8 does for its
  sections) and stay readable through `standing_read`.
- **Lineage per entry.** An entry renders only where its own lineage passes for
  the room, the rule `nora/agent/standing_read.py` already applies per section,
  moved down one level. An entry's disclosure requirements are derived by code
  from everything it cites, `turn(...)` items included with their recorded
  requirements (lineage, exclusions, location restrictions,
  [30](30-turn-records.md) §5); never the writing room's own lineage alone,
  which is what Standing rows are stamped with today.
- **Each entry renders with its revision**, which is the base a later op on it
  asserts (§7).

## 6. Scopes

| scope | keyed on | holds | rendered in |
|---|---|---|---|
| **room** | `(platform, conversation_id)`, as Standing is today | decisions, commitments and threads of one room | that room's tag, fire and participation turns |
| **unit** | `unit` | the same three kinds, for anything that spans rooms (a thread with messages in two rooms, a decision whose note is the unit's) | every room of the unit, each entry gated by its lineage |

The unit scope is what cross-room shared state IS: one Halloween decision,
visible from the Signal dinner room and the Discord channel alike, instead of
two room paragraphs that each know half. It replaces the unit sections
[12-schemas](../cognition-loop/12-schemas.md) §5 sketched (`What I am
following`, `What I am watching for`, `I owe`), which map onto threads,
decisions and commitments.

An entry moves from room to unit scope when a second room touches it; the move
is a revision, so its history shows when and why.

## 7. The operations

A writer never sends a whole Standing. It sends operations:

| op | effect |
|---|---|
| `open(kind, scope, subject, body)` | a new entry |
| `revise(id, sections, base)` | replaces the named sections only; the rest carries forward |
| `close(id, outcome, cites, base)` | `closed`, `done` or `dropped`, with its grounds |
| `touch(thread_id, message_ids)` | adds messages to a thread's spans; code recomputes the spans and `last_moved_at` |

**Every op that changes an existing entry asserts a base**: the entry's
revision as the turn saw it rendered, captured by the tool from the render
rather than typed by the model. `touch` is the exception because it commutes:
adding messages to a set in either order gives the same set. The base is
required, not optional, for every ledger op from step 2 on. The legacy
paragraph writes that coexist with the ledger until step 4 (the debrief passes
no base today, and Cognition's `standing_revise` lets one be omitted) keep
today's optional base until they switch to ops at step 3; the requirement is
the ledger's, not a change to the shared legacy check. Today's `check_stale_base` lets a caller that asserts
no base through (`nora/domain/standing_revise.py`), which is safe while one
room's turns are serialised by its own slot (`nora/engagement/slots.py`); a
unit entry is written from rooms whose slots are independent, so a stale room
turn could otherwise close a decision another room has since changed. The
comparison and the revision append are one transaction, holding a row lock on
the entry. A stale base is refused `stale_base` and the turn re-reads.

Every op passes the same validator before commit, the one-verb rule
`nora/domain/standing_revise.py` states today. A refused op is answered
`refused` with its rule (ADR 0009) and counted by rule.
