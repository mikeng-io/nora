---
type: design
title: "Turn records: what a turn did, kept whole, cited by id, opened on demand"
description: "The Record layer the ledger cites. Every occasion keeps a turn record built by code from each occasion's trace (tag_trace today, plus the fire and Cognition exchanges that are not kept today); every message she sends carries its record's handle; a ledger line cites it as turn(id); one read opens it, gated item by item by the disclosure requirements recorded with each item. That read takes over DEC-112's job, which is what lets recall_lookups retire."
tags: [nora, turn-record, tag-trace, tool-call-log, provenance, lineage, recall-lookups, dec-112]
timestamp: '2026-10-25'
edges:
  - {dst: 20-the-ledger, rel: realizes, provenance: asserted}
  - {dst: ../standing-compact/00-intent.md, rel: extends, provenance: asserted}
---

# 30 · Turn records

## 1. What exists already

Most of a turn's work is already kept. The next turn just cannot reach it.

| store or path | holds | read by today |
|---|---|---|
| `tag_trace` (`nora/infra/sql/tag_traces.py`) | each completed **tag** turn whole: the agent messages (tool calls and results, sanitised, narration capped, a human's words never cut) and the transcript it was shown | the debrief fold; the `rebuild_state` stub that was never built |
| `tool_call_log` (`nora/infra/sql/tool_call_log.py`) | each successful call to a `cacheable` tool, with its input and result, keyed by conversation and `tag_id`, written by the `ToolCallLog` hook on **tag and fire** turns alike (`nora/engagement/stages/deliver.py` builds it for both) | `recall_lookups` only (7 days, 30 rows, 8000 characters) |
| the fire path | its cacheable tool calls, through the hook above; **not** its trace: it discards the `TagTrace` `AgentRunner.invoke` hands back (`nora/app/fire.py`), so a fire turn's non-cacheable calls and its whole exchange are gone, and it gets no debrief | nothing beyond `recall_lookups` |
| Cognition | the session's situation, agenda and provenance (`cognition_session`); **not** the executed exchange: `CognitionAgentRunner` returns only the stop reason and token usage (`nora/app/cognition_agent_runner.py`) | the Cognition loop |

## 2. The turn record

One record per occasion that ran a model loop, **built by code, never written
by a model**:

- `id`, `occasion` (`tag`, `fire`, `participation`, `cognition`), the room it
  ran in (for Cognition: the unit, plus the agenda items' bound rooms),
  `started_at`, `outcome`;
- **items**: each tool call with its result, every tool and not only
  `cacheable` ones, under the sanitiser and caps `tag_trace` uses; each item
  carries its own lineage (§5);
- the **messages it sent**, as message ids;
- **no reasoning.** Her reasoning is often a path she abandoned; the part worth
  keeping is the conclusion, which belongs in a decision she writes (§4).
  Whether any reasoning block survives today's sanitiser is checked in step 0
  ([50](50-migration.md)), not assumed.

Built from each occasion's **trace**, which already holds every tool call and
result: `tag_trace` for tag turns, plus two gaps closed in step 1: the fire
path keeps its trace, and `CognitionAgentRunner` returns its exchange and the
session persists it. A trace alone does not carry an item's disclosure
requirements (`tag_trace` copies the SDK messages, and a Character read
returns rendered text without its restrictions), so step 1 also records them
at read time for every item, on all three paths (§5). Retention follows
`tag_trace`'s.

**`tool_call_log` is left exactly as it is until step 5.** It is not widened
to every tool: `recall_lookups` reads its newest rows without filtering by
tool name, capped at 30 and unpaged (`nora/agent/tools.py`), so logging every
tool there would push real lookups out of its reach while it is still her
evidence. It keeps its `cacheable`-only selection until the turn-record read
has replaced it ([50](50-migration.md) step 5).

## 3. Every message she sends carries its record

DEC-112's diagnosis was that her replies "survive into `group_transcript` as
bare text with no provenance". Most checked answers never become a decision,
a commitment or a thread: a factual answer is said and done. So the handle
cannot live only in the ledger.

**Her own transcript rows render with the handle of the turn that produced
them**, written by code from the record's sent-message ids:

```
[9104] Nora (5h, turn t812): Saturday ferries leave Central at 18:30 and 19:15
```

Every surface that renders her rows gets it: the tag prompt's
`<group_transcript>`, `load_transcript`, `room_read`. A row older than the
window is reached the way any row is, and carries its handle too.

## 4. Citing it, and who writes what

A ledger line cites a turn record as `turn(<id>)`, beside the house grammar's
`[ids]` and `rollup(...)` (`nora/domain/note_body.py`). The validator holds a
`turn(...)` cite to the rule 12-schemas §2.3 holds every cite to: it must be a
turn this writer was shown or ran.

| part | written by | how |
|---|---|---|
| the turn record, and the handle on her rows | **code** | from the run, always, no model call |
| `turn(...)` handles on a ledger line | the writer names them; **code** checks them | validator |
| a decision's `Options`, `Leaning`, `Reopen if`; a commitment's `Owed` | **the model**, in her own words, at the moment she decides | two routes, below |
| thread spans, ages, `last_moved_at` | **code** | from the Record |

The model has two routes to write, and both are kept:

1. **During the turn**, through the ledger ops ([20](20-the-ledger.md) §7)
   exposed as a tag-path tool. Cheapest and most faithful: the scores are
   written by the call that computed them, before anything summarises them.
2. **In the debrief**, which keeps its place after the turn but changes job:
   from "rewrite the paragraph" to "emit ops for what this turn decided, opened
   or owed, copying the turn's own words, citing `turn(<this turn>)`". It is
   the safety net for a turn that decided something and did not write it.

Neither route folds an earlier fold. That is the change that stops the loss.

## 5. Opening it, and who may see what

One read tool opens a turn record by its handle. Its name follows ADR 0015 and
is measured with `tool_selection` before it ships ([40](40-verification.md)
§6); `record_read` is the working name. It returns the record's items, the
messages it sent, and the ledger entries that cite it.

**Disclosure is per item, derived by code, and uses the lineage predicates that
already exist** (`nora/agent/lineage.py`); unit membership alone authorises
nothing.

- **Each item keeps the complete disclosure requirements its source carried
  when it was read**, recorded by code at read time, not re-derived later from
  a subject: the lineage entries; `exclude_persons`; the `location` flag and
  location subjects (the fields `profile_lines_for` in `nora/agent/lineage.py`
  applies to a Character read, and which a subject alone cannot reproduce);
  and whatever further requirement that source's own gate applies. A
  transcript or `room_read` item has the rooms whose messages it returned as
  origins; a web search has the room the turn ran in. A Cognition record mixes
  several rooms, which is why this is per item and not per record.
- **Re-applied with the same predicates** the source's own read uses
  (`profile_lines_for` for a Character item, the lineage predicates for the
  rest), against the room the record is opened from, with its live audience.
  An item whose requirements cannot be resolved (an unknown kind, a missing
  field, a roster that cannot be read) is withheld: fail closed.
- **The record opens in room B** item by item: an item is returned only where
  its lineage passes for B, under the same fail-closed rules a Standing section
  gets (a degraded or partly unnamed audience withholds what names a person).
  Withheld items are counted in the result, never described, the shape
  `standing_read` returns today.
- **Another room of the same unit is not an unknown handle.** A unit-scope
  decision citing `turn(t812)` from the Signal room opens from the Discord
  room, for exactly the items whose lineage passes there.
- **Another unit's handle, or one that names nothing, is unknown**, and the
  two are byte-identical (the `room_read` rule, D1 = B).
- **A ledger entry carries its cited items' requirements**, exclusions and
  location restrictions included ([20](20-the-ledger.md) §5), so an entry never
  renders somewhere its own evidence could not be opened.

A handle on her transcript row (§3) needs no separate gate: the row is shown
only where it is already shown, and the record still opens item by item.

## 6. Why this is what lets `recall_lookups` retire

DEC-112 did not keep `recall_lookups` as a cache. It kept it as **her evidence
of what she actually checked**: on 2026-08-17, pushed on one wrong answer, she
disowned four correctly sourced searches as guesses, because her tool results
died with their turn and her true claims were indistinguishable from guesses on
the next one. `nora/doctrine/rooms/constraints.md` («When you are told you are
wrong») tells her the grounds she checked may still be in the record, and
names no tool on purpose.

`recall_lookups` answered that with a list she had to think of asking for: 0
calls in 30 live turns was DEC-112's measurement of how often she did. After
this bundle the evidence is in front of her when she is challenged. **Every
claim she is challenged on is a row of hers, and the row carries
`turn(t812)`** (§3); when the claim is also a decision, the decision cites the
same record. The read in §5 opens it. That covers both of `recall_lookups`'
jobs, the cache and the evidence, for ordinary answers and decisions alike, so
it retires and DEC-112 is amended rather than broken.

The retirement is gated on the being-contradicted corpus not regressing,
ordinary checked answers included ([40](40-verification.md) §2), not on this
argument.
