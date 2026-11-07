---
type: design
title: "The replay records why: every replayed session keeps what it saw, thought and did, and the judges read all of it"
description: "A recorded-wake replay measured how many facts a session saved, how many were supported and how many it missed, but never why: the recorder kept only tool calls and results, and dropped the model's reasoning, its own text, every later call's input, per-call usage and the judges' reasons. This node makes the replay record the whole trajectory, scopes what a judge may count as missed, adds a per-item decision judge that names the cause of every miss and slip, and makes a record that lost any of this an ERROR. Amends 31 §3 step 6 (the spike)."
tags: [nora, cognition, memory-graph, evals, replay, measurement]
edges:
  - {dst: 31-build-one-session, rel: amends, provenance: operator-requested}
  - {dst: 33-orient-kind-table, rel: measures, provenance: asserted}
  - {dst: ../evaluation/08-the-sdk-surface.md, rel: relates_to, provenance: asserted}
---

# 34 · The replay records why

Status: **design, for review.** Nothing below is built.

## 1. What was measured, and what could not be

The format experiment (four agenda formats) and the prompt experiment (four
instruction variants) ran about 25 replays of the same five recorded wakes.
They found that the merged rewrite saves about twice as many supported facts as
the instruction it replaces. They could not say why any one fact was written or
left out, because the replay kept no record of why:

- **The recorder dropped the model's side.** `scripts/memory_graph_spike.py`
  walks the session's full message list, the same one sent to the model, and
  keeps only paired tool calls and results. The reasoning blocks Nora carries
  between turns (`nora.agent.reasoning_carry`) and the model's own text are in
  that list and are discarded.
- **The input is a re-render, not the record of a call.** `inspect()` assembles
  the first call's input again and stops before the model. Later calls'
  inputs are not kept, and nothing proves the re-render equals what was sent.
- **Cost and routing are per session only.** No per-call usage, reasoning
  tokens, finish reason, latency or gateway model header. When a judge alias
  fell back to another model, the record could not show it; a probe did.
- **The judges' reasons were thrown away.** The support judge returns only a
  verdict. The scorer keeps the count of missed facts and drops the list.
- **The missed-fact judge scored outside the session's job.** First it counted
  chat mechanics and presence summaries, which code records. Then it counted
  facts found only in older messages the session read through tools. Most of
  the "missed memory" was these two.

The reasoning text is not stored in production either: the reasoning-strip
record (ADR 0013) counts dropped blocks, never their text.

A count says whether a session worked. Without the trajectory, nobody can say
why it worked or why it failed. Everything after this section closes that.

## 2. The record

Each replayed session writes one private record holding, in order:

1. **Every model call**, with:
   - **the request exactly as sent:** system text, messages, tool definitions,
     and the settings (model alias, reasoning effort, max tokens, temperature);
   - **the response:** reasoning, text, tool calls, finish reason, usage
     including reasoning tokens, latency, and the gateway's model and provider
     headers;
   - **whether the transport dropped reasoning** on this call, from the
     existing strip detector.
2. **Every tool result**, including each refusal reason code gives.
3. **The agenda's items** with their indices, and for each item what closed it:
   the saved statement that cites it, its `nothing_here`, its close reason, or
   that it stayed open.
4. **The judges' full outputs:** each verdict with its reason, and each missed
   fact with its quote and where the quote was found.
5. **Provenance:** source commit, snapshot migration revision, sample hash, a
   digest of the config the run read, and the judge models the gateway actually
   answered with.

The first call's recorded request replaces the `inspect()` re-render as the
evidence of what the session saw. Where both exist they must be byte-equal, or
the run is ERROR.

**Privacy.** The record holds production chat. It lives only in the private
output directory (mode 0600, the existing `private_directory`), never in the
repository, a PR, an issue or a report. Reports carry counts, causes and
record paths, never content.

## 3. What a judge may count

- **Support:** a saved statement is supported only by the rows it cites. A
  detail that appears only in an uncited row, even one the session read, makes
  it unsupported. This is what already caught the slip in the confirm replay.
- **Missed:** only facts stated in the agenda's new messages count. Older
  messages read through tools, the notebook, Standing and the graph are context
  for interpreting them, and a fact found only there is not a miss. Honcho's
  deriver uses other messages "only as context to interpret"; mem0 extracts
  "exclusively from New Messages". The judge gets the same definition of memory
  and the same skip list the writer works to: chat mechanics, presence
  summaries, transient screen states, greetings.

**Reversal condition.** If replies measurably need facts that only ever appear
in older messages a session read, and no session over their own wake wrote
them, then reads become a source and this scope widens.

## 4. The decision judge: why

For each agenda item, a judge reads that item's whole trajectory: the rows,
the reasoning, the text, the proposals, the refusals and the close. It assigns
exactly one cause:

| outcome | cause |
|---|---|
| written, supported | — |
| written, unsupported | took a detail from an uncited row · inferred and wrote it as said · wrong subject · other (named) |
| not written | never noticed (the reasoning never mentions it) · noticed and judged it not memory (quotes the reasoning) · planned and never proposed · proposed, refused and not retried · stopped by budget or turn limit |
| closed correctly with nothing to write | — |

Each cause carries the reasoning or tool step it rests on, by position in the
record. A cause the record cannot ground is `insufficient_evidence`, never a
guess.

This is a diagnostic over recorded runs. It never feeds the support or missed
counts, and it costs judge calls only: it can re-read any recorded replay
without running it again.

## 5. Fail-closed rules

- **A run is ERROR when the record is incomplete:**
  - a model call or tool result is missing;
  - the response carried reasoning and the record has none;
  - a judge's reason is missing.
- **ERROR is never PASS.**
- **Missing reasoning is `insufficient_evidence`.** The decision judge never
  reads a call without reasoning as "never noticed".
- **The decision judge has a positive control:** a generated trajectory with a
  known cause for each item, and the judge must name those causes. If it
  cannot, its output on real runs is not evidence.
- **The recorder has its test:** a generated session with reasoning, text and
  two tool rounds, recorded and read back. If any of §2's parts is missing,
  the test fails.

## 6. One recorder, not a spike-only part

The recorded-wake eval cases (`evals/corpora/cognition_session.yaml`) and the
spike replay the same subject. They read one record shape, produced by one
recorder at the session's model-call seam. The spike gets no record format of
its own. Whether production should persist reasoning for live debugging is a
separate decision (§8).

## 7. Corrections to 31 §3 step 6

- **The judges are the gateway aliases** `nora-judge` and `nora-judge-2`, two
  vendors, neither the writer's. Each run records which model actually
  answered. The raw ids named in step 6 are retired; one of them was banned
  upstream and every run that used it scored zero supported.
- **Fact support and missed facts are scored with §3's scope,** and every run
  is read by §4's decision judge.

## 8. Open, for the operator

- **Production reasoning.** Storing the reasoning text of live sessions would
  make a live miss explainable the same way. It is chat-derived content, and
  this node does not decide where it would live or for how long. Langfuse's
  generation spans may already hold it; that is unverified.
- **Thinking level.** The session runs at `reasoning_effort = "low"`. Whether
  `high` writes better is a replay question. It is worth one replay pair only
  after §4 shows misses that the reasoning never reached.

## 9. Acceptance

- A replay with the recorder on produces §2's record for every session. The
  recorder test and the positive control pass.
- The decision judge, over one replay of the current head, names a cause for
  every miss and slip, or `insufficient_evidence` with the reason.
- The judges' prompts and scope are §3's, pinned by a test that fails if the
  missed judge counts a fact found only in a read.
- No record content appears outside the private output directory. The PII
  push hook and a test over the report confirm it.
