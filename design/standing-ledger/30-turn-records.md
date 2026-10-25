---
type: design
title: "Turn records: what a turn did, kept whole, cited by id, opened on demand"
description: "The Record layer the ledger cites. Every occasion keeps a turn record built by code from what already exists (tag_trace, tool_call_log); a ledger line cites it as turn(id); one read tool opens it. That read takes over DEC-112's job, which is what lets recall_lookups retire."
tags: [nora, turn-record, tag-trace, tool-call-log, provenance, recall-lookups, dec-112]
timestamp: '2026-10-25'
edges:
  - {dst: 20-the-ledger, rel: realizes, provenance: asserted}
  - {dst: ../standing-compact/00-intent.md, rel: extends, provenance: asserted}
---

# 30 · Turn records

## 1. What exists already

Most of a turn's work is already kept. The next turn just cannot reach it.

| store | holds | read by today |
|---|---|---|
| `tag_trace` (`nora/infra/sql/tag_traces.py`) | each completed **tag** turn whole: the agent messages (tool calls and results, sanitised, narration capped, a human's words never cut) and the transcript it was shown | the debrief fold; the `rebuild_state` stub that was never built |
| `tool_call_log` (`nora/infra/sql/tool_call_log.py`) | each successful call to a `cacheable` tool, with its input and result, keyed by conversation and `tag_id` | `recall_lookups` only (7 days, 30 rows, 8000 characters) |
| `cognition_session` and its siblings | a Cognition wake's session | the Cognition loop |
| nothing | **fire turns**: proactive, participation, reminders. The fire path discards the `TagTrace` `AgentRunner.invoke` hands back (`nora/app/fire.py`) | nothing |

## 2. The turn record

One record per occasion that ran a model loop, **built by code, never written
by a model**:

- `id`, `occasion` (`tag`, `fire`, `participation`, `cognition`), the room or
  unit, `started_at`, `outcome`;
- the **tool calls and their results**, every tool, not only `cacheable` ones,
  under the same sanitiser and caps `tag_trace` uses;
- the **message it sent**, as a message id;
- **no reasoning.** Her reasoning is often a path she abandoned; the part worth
  keeping is the conclusion, which belongs in a decision she writes (§4).
  Whether any reasoning block survives today's sanitiser is checked in step 0
  ([50](50-migration.md)), not assumed.

Built as a view over `tag_trace` and `tool_call_log` for tag turns, plus the
two gaps closed: the fire path keeps its trace, and the `ToolCallLog` hook
logs every tool rather than only `cacheable` ones. Retention follows
`tag_trace`'s.

## 3. Citing it

A ledger line cites a turn record as `turn(<id>)`, beside the house grammar's
`[ids]` and `rollup(...)` (`nora/domain/note_body.py`). The validator holds a
`turn(...)` cite to the rule 12-schemas §2.3 holds every cite to: it must be a
turn this writer was shown or ran. A debrief may cite the turn it is folding;
a Cognition session may cite turns it opened with the read in §5.

## 4. Who writes what: the hybrid

| part | written by | how |
|---|---|---|
| the turn record | **code** | from the run, always, with no model call |
| `turn(...)` handles on a ledger line | **code** checks them; the writer names them | validator |
| a decision's `Options`, `Leaning`, `Reopen if`; a commitment's `Owed` | **the model**, in her own words, at the moment she decides | two routes, below |
| thread ranges, ages, `last_moved_at` | **code** | from the Record |

The model has two routes to write, and both are kept:

1. **During the turn**, through the ledger ops ([20](20-the-ledger.md) §7)
   exposed as a tag-path tool. Cheapest and most faithful: the scores are
   written by the call that computed them, before anything summarises them.
2. **In the debrief**, which keeps its place after the turn but changes job:
   from "rewrite the paragraph" to "emit ops for what this turn decided, opened
   or owed, copying the turn's own words, citing `turn(<this turn>)`". It is
   the safety net for a turn that decided something and did not write it.

Neither route folds an earlier fold. That is the change that stops the loss.

## 5. Opening it

One read tool opens a turn record by its handle, scoped to the room or unit the
calling turn is in, the room rule `nora/agent/subagent_desk.py` applies to job
ids: a handle from another room behaves exactly as an unknown handle. Its name
follows ADR 0015 and is measured with `tool_selection` before it ships
([40](40-verification.md) §3); `record_read` is the working name.

It returns the tool calls and results the turn made, the message it sent, and
the ledger entries that cite it.

## 6. Why this is what lets `recall_lookups` retire

DEC-112 did not keep `recall_lookups` as a cache. It kept it as **her evidence
of what she actually checked**: on 2026-08-17, pushed on one wrong answer, she
disowned four correctly sourced searches as guesses, because her tool results
died with their turn and her true claims were indistinguishable from guesses on
the next one. `nora/doctrine/rooms/constraints.md` («When you are told you are
wrong») tells her the grounds she checked may still be in the record, and
names no tool on purpose.

After this bundle the claim she is challenged on is a ledger line, and the
line cites `turn(t812)`. The evidence is in front of her when she is
challenged, rather than behind a tool she has to think of calling: 0 calls in
30 live turns was DEC-112's measurement of how often she thought of it. The
read in §5 opens it. That replaces both halves of `recall_lookups` (the cache
and the evidence), so it retires, and DEC-112 is amended rather than broken.
The retirement is gated on the being-contradicted corpus not regressing
([40](40-verification.md) §2), not on this argument.
