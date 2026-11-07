---
type: design
title: "The replay records why: every replayed session keeps what it saw, thought and did, and the judges read all of it"
description: "Recorded-wake replays measured how many facts a session saved, how many were supported and how many it missed, but never why. The spike reduced the session's stored trace to tool calls and results and threw the rest away, including reasoning that production already stores. This node extends the existing private run evidence so a replay keeps the whole trajectory, scopes what a judge may count as missed by the writer's own kind criteria, adds a per-statement decision judge that reports evidenced observations, and makes incomplete evidence an ERROR. Amends 31 §3 step 6 (the spike)."
tags: [nora, cognition, memory-graph, evals, replay, measurement]
edges:
  - {dst: 31-build-one-session, rel: amends, provenance: operator-requested}
  - {dst: 33-orient-kind-table, rel: measures, provenance: asserted}
  - {dst: ../evaluation/08-the-sdk-surface.md, rel: relates_to, provenance: asserted}
---

# 34 · The replay records why

Status: **design, for review.** Nothing below is built.

## 1. What was measured, and what could not be

**The runs.** A format experiment and an instruction experiment replayed the
same frozen sample of five recorded wakes (sample hash `a19a66d3…`) many times
over. They found that the merged instruction rewrite saved about twice as many
facts its citations support as the instruction it replaces. These outcomes are
operator-reported until each is read against its private receipts: the run
directories `fmt-*`, `prm-*` and `prod-637-*` under the spike's private output
on the deploy host, and their re-scoring files. Those hold each run's source
revision, sample and counts. They could not say why any one fact was written or
left out.

**Why not:**

- **The spike throws away what it already had.**
  - The session stores its messages in `tag_trace.messages`, after
    `extract_tag_trace` and the sanitizer.
  - The sanitizer caps an assistant `text` block's length and keeps its
    `reasoningContent` blocks (`nora/agent/tag_trace_sanitize.py`).
  - The spike reads that stored trace and reduces it to tool calls and results
    (`tool_exchanges` in `scripts/memory_graph_spike.py`), discarding the
    reasoning and text it held.
  - The stored trace is not a complete record of model calls in any case: it is
    sanitized, capped, and has no per-call request, settings, usage or routing.
- **Production already stores the reasoning.** On the frozen snapshot, all 38
  stored Cognition traces carry reasoning blocks (247 of 760 tag-path traces
  do). ADR 0013's reasoning-strip log is a different thing: it stores counts of
  blocks the transport dropped, never text.
- **The input is a separate rendering, not the request.**
  - `inspect()` returns the system text, the agenda and the work list from a
    second assembly that stops before the model. It has no tool definitions or
    settings, and the transport still rewrites punctuation afterwards.
  - The spike inspects with `historical=False` and replays with
    `historical=True`.
  - Later calls' inputs are not kept anywhere.
- **Cost and routing are per session only.** Nothing records per-call usage,
  reasoning tokens, finish reason, latency or the model the gateway actually
  served.
- **The judges' reasons are discarded.** The support judge returns a verdict
  only, and the scorer keeps a count of missed facts but not the list.
- **The missed-fact judge counted things outside the session's job.** It
  counted chat mechanics and presence summaries, which code records, and facts
  found only in older messages the session read through tools.

A count says whether a session worked. Without the trajectory, nobody can say
why it worked or why it failed.

## 2. One evidence path, extended

The recorded-wake eval cases (`evals/corpora/cognition_session.yaml`) and the
spike replay the same subject. Both write through what already exists:

- **The private run evidence.** `nora.subject.eval_evidence.RunEvidence`
  already persists events, spans, logs, trace ids and incomplete-run evidence.
- **The formatted-request capture.** `record_eval_model_request` already
  records each formatted request.
- **The evaluator trajectory.** `evals.backbone.traced_nora_task` and its
  `TracedHandler` give the SDK-mapped `Session` the evaluators read.
  Evaluation 08 §7 forbids replacing that mapper with a hand-built trajectory.
  The `Session` stays the evaluators' trajectory, and nothing overwrites its
  `trajectory` key.

This node adds linked private events to that path. It adds no second record
shape and no second runner. The spike stops reducing the stored trace and
reads the run evidence instead.

**Seams:**
- The formatted request is captured at the existing
  `ReasoningCarryingOpenAIModel` → `DashFreeOpenAIModel.format_request`
  boundary, after the transport's own normalisation.
- Response fields and gateway headers are captured by extending the
  model/transport boundary.
- Strands hooks give call lifecycle and tool order.
- The final `agent.messages` alone is not enough: it is what survived, not what
  was sent.

## 3. The record

Each replayed session's evidence holds:

1. **Every model call**, with:
   - **correlation:** invocation, call and attempt ids, and global order;
   - **the formatted request as sent:** system, messages, tool definitions and
     descriptions, and the settings (alias, reasoning effort, max tokens,
     temperature);
   - **the response:** reasoning, text, tool calls with their ids, finish
     reason, usage including reasoning tokens, wall-clock latency, and the
     gateway's served model and provider headers;
   - **incoming reasoning availability:** `observed`, `absent`, `redacted` or
     `capture_unavailable`;
   - **outgoing carry loss,** separately: whether this call's continuation
     omitted earlier reasoning (the existing strip detector);
   - **retries, exceptions and cancellation.**
2. **Every tool call and result**, by tool-call id, including each refusal and
   its reason. Exact requests and results already contain the rendered graph
   preview, the resolution candidates and the returned connected subgraph.
3. **The agenda's items** with their indices, and how each closed: the saved
   statements that cite it, its `nothing_here`, its close reason, or that it
   stayed open.
4. **A terminal session manifest:**
   - the outcome or error;
   - the effective budgets and which bound stopped the session;
   - the replay clock and timezone, distinct from wall-clock timings;
   - the resolved doctrine sha256 and skill versions the session already
     records;
   - the wake and session identities and the trace ids;
   - source commit, snapshot migration revision, sample hash, and a digest of
     the config read.
5. **The judges, through the same capture path:** exact requests, settings,
   evidence inputs, outputs with reasons, every missed fact with its quote and
   where the quote was found, and each judge's served model.

**Inspection.** The recorded formatted request is authoritative. `inspect()`
may be compared only through a defined projection of system and agenda, with
the same snapshot, clock, historical mode and resolved instruction versions,
and after the same transport normalisation. It is never compared byte for byte
with the complete request.

## 4. Privacy

Full requests, responses, reasoning and judge evidence are production content.

- **Where they live.** They are persisted only in the private artifact: a
  `private_directory` (0700) holding `freeze`d files (0600).
- **Where they must not go.** Replay capture keeps that content out of
  non-private trace and log exporters, stdout, public receipts and CI uploads.
  `record_eval_event` today also adds its body to the current OTel span
  (`nora/obs/tracing.py`), and replay capture must not export it.
- **Public reports** carry fixed cause codes, counts and opaque references,
  never judge-authored explanations or paths derived from source content.
- **Each sink is tested with generated sentinel content.** The Git PII hook is a
  backstop, not the check.

## 5. What a judge may count

- **Support.** A saved statement is supported only by the rows it cites. A
  detail found only in an uncited row, even one the session read, makes it
  unsupported.
- **Missed.** These count:
  - only statements that originate in the agenda's new source rows,
    interpreted using the context the session was shown;
  - eligibility derived from the writer's rendered kind criteria. A reading
    about a passing moment is eligible, because the writer is told to record it.
  
  These do not count:
  - **what code records:** who said, shared, reacted or replied, and presence
    summaries;
  - **a message that states nothing.**
  
  No durability or future-usefulness threshold is added.
- **Context-only statements are reported separately.** These are found only in
  older messages, the notebook, Standing or the graph. They are never counted
  as misses.

**Reversal condition.** Reopen this denominator when both of these exist:
- a later answer fails for want of a fact;
- an audited record shows the source wake of that fact left it unrecorded.

## 6. The decision judge: why

The decision judge diagnoses each missed or unsupported statement on its own,
linked to its agenda item and source span. One item can have several outcomes:
one statement saved, one refused and one missed. Item summaries are derived
from the per-statement results.

For each statement the judge returns **an evidenced observation** or
`insufficient_evidence`:

| for | observations |
|---|---|
| unsupported | took a detail from an uncited row · wrote an inference as said · wrong subject · other (named) |
| missed | recorded reasoning mentions it and treats it as not memory (quoted) · recorded reasoning mentions it and no statement followed · proposed and refused, not retried · the session stopped on a named bound before it · not mentioned in recorded reasoning |

Each observation cites the reasoning span, call or tool step it rests on, by id.

**"Not mentioned in recorded reasoning" is an observation, not proof that the
model never noticed.** Reasoning is only what the model emitted. When incoming
reasoning is `absent`, `redacted` or `capture_unavailable`, the judge returns
`insufficient_evidence`.

The judge never feeds the support or missed counts, and it re-reads recorded
evidence without a new replay.

## 7. Fail-closed rules

- **A record is ERROR when:**
  - an event is unmatched (a call start with no end, a tool call with no
    result);
  - terminal evidence is missing;
  - capture or a write failed;
  - anything was truncated;
  - a judge's output or reason is missing.
- **ERROR is never PASS.**
- **The recorder's tests:**
  - a generated session with reasoning, text and two tool rounds, read back
    complete;
  - an interrupted session;
  - a deliberately dropped event, which must turn the record ERROR.
- **The decision judge's controls** are generated trajectories covering:
  - missing reasoning, which must give `insufficient_evidence`;
  - an explicit rejection in the reasoning;
  - a refused proposal;
  - a retry;
  - a budget stop;
  - a detail taken from an uncited row.
  
  If the judge cannot report what each control's record shows, its output on
  real runs is not evidence.

## 8. Corrections to 31 §3 step 6

- **Judges.** They are requested through the gateway aliases `nora-judge` and
  `nora-judge-2`. Their independence from each other and from the writer is
  established from the served identities each run records, never from alias
  names or caller-supplied vendor labels. Unavailable identity evidence is
  reported as unavailable. This follows 31 §6's requirement of resolved
  identities.
- **Scoring.** Fact support and missed facts are scored with §5's scope, and
  each run's misses and slips are read by §6's decision judge.

## 9. Open, for the operator

- **How long production keeps reasoning.** Production already keeps Cognition
  reasoning in `tag_trace` (§1). Whether that retention and its readers are
  what the operator wants is not decided here.
- **Thinking level.** The session runs at `reasoning_effort = "low"`. Whether
  `high` writes better is a replay question, worth one replay pair only after
  §6 shows misses whose recorded reasoning never reached them.

## 10. Acceptance

- A replay produces §3's evidence for every session, and §7's recorder tests
  and judge controls pass.
- The evaluators' SDK `Session` is unchanged. A test fails if the `trajectory`
  key is replaced.
- The decision judge, over one replay of the current head, returns an
  observation or `insufficient_evidence` for every missed and unsupported
  statement.
- The judges' prompts and scope are §5's. A test fails if a context-only
  statement is counted as missed.
- Generated sentinel content remains available in private, process-local
  capture and the SDK-mapped `Session`, and appears in no non-private trace or
  log exporter, stdout, public receipt or CI upload.
