---
type: proposal
title: "The Relevance Gate — Lightweight Ambient Admission, Not an Agent Turn"
description: "How Nora decides an untagged message deserves her attention: a single structured model call (DeepSeek V4 Flash, reasoning none, no tools, no doctrine, no agent loop) that reads only transcript + standing and returns verdict + relevance score. GO means the message is handed to Nora as normal ingestion; NO-GO means silence. Shadow mode runs ONLY the gate and delivers verdicts to a review channel — Nora is never involved. This replaces the failed 'fifth Event / full agent loop' participation design."
tags: [nora, relevance-gate, ambient, admission, lightweight, shadow]
timestamp: '2026-09-06'
edges:
  - {dst: 35-ambient-participation, rel: supersedes, provenance: asserted}
  - {dst: 06-trigger, rel: revises, provenance: asserted}
version: 1
---

# The Relevance Gate

## 1. What went wrong, and the shape this fixes

The first participation attempt (design/35-ambient-participation.md, since
superseded) built ambient as a FIFTH fire Event through `FirePath.speak` — a
FULL agent turn with the whole doctrine, every tool, a 20k-token budget, and
3.4 seconds of model reasoning. Miles's review was unambiguous: that is not an
evaluation, it is "抌入去 Nora 度 run 一次" — throwing the message into Nora
and asking her whether to reply. It injected everything (doctrine, header,
tools, standing, transcript) when the agreed design was only transcript +
standing, and it gave the model tools it must never use on a determination
path (the trace showed a weather tool call).

**The Relevance Gate is the opposite: a single structured model call that
decides whether the message is worth Nora's attention AT ALL.** No agent
loop, no tools, no doctrine, no 20k budget. One call, ~1-2 seconds, verdict
out.

## 2. The shape

```
message arrives (untagged, room opted in)
  │
  ▼
quiescence window closes (settle_seconds of quiet)
  │
  ▼
RELEVANCE GATE — one structured model call
  ├─ system prompt: short, purpose-built (a few lines, NOT Nora's doctrine)
  ├─ input: transcript window + standing (the only two things injected)
  ├─ model: DeepSeek V4 Flash (same tier the tag path uses), reasoning NONE
  ├─ no tools, no agent loop, max_tokens small (2k-4k)
  ├─ output: JSON — verdict (go/no_go) + relevance_score (0-1)
  │
  ├─ SHADOW MODE (default): the verdict goes ONLY to the review channel —
  │    Miles sees group name + incoming message + verdict + score. Nora is
  │    NEVER involved, whatever the score.
  │
  └─ LIVE MODE (shadow off, operator flip):
       ├─ no_go / low score → silence. Nothing happens.
       └─ go / high score → the MESSAGE IS HANDED TO NORA as normal
            ingestion — the standard ingest → (if she chooses) engagement
            path, with her full context and tools. The gate is admission
            control, never a reply generator.
```

## 3. The gate call (the whole thing)

### 3.1 Prompt

```
You are a relevance gate for a group-chat assistant. Decide whether THIS
message deserves the assistant's attention.

Context (nothing else is available to you):
- transcript: the last N messages in the room
- standing: the assistant's own position in this conversation

Rules:
- GO only if the message is a question the assistant can answer, a fact it
  can correct, a commitment it made that is being followed up, or something
  that specifically needs it. 
- NO-GO for: status updates, acknowledgements, small talk that needs no
  answer, private exchanges, inside jokes, commands for other services, and
  anything the assistant cannot add grounded value to.
- Do not suggest what the assistant would say. You only decide relevance.

Respond with JSON only:
{"verdict": "go" | "no_go", "relevance_score": 0.0}
```

### 3.2 Output contract

`verdict`: `"go"` | `"no_go"` — the admission decision.
`relevance_score`: 0.0-1.0 — how strongly the message needs the assistant.
The verdict is the load-bearing field; the score is for Miles's review and
for tuning the threshold.

The gate NEVER drafts a reply. It never calls a tool. It never runs an
agent loop. One call, structured JSON back.

### 3.3 Model + limits

- Model: DeepSeek V4 Flash via OmniRoute (same tier as tag path)
- `reasoning_effort`: none
- `max_tokens`: 2000 (the tag path's per-call ceiling, 5.4x headroom over
  the 371-token measured max — a verdict JSON is far smaller)
- temperature: low (0-0.2) — a measurement instrument, not a voice
- JSON mode: `response_format={"type": "json_object"}` if the provider
  supports it, else strict prompt + parse with fail-closed on parse error
  (unparseable → no_go + counted)

## 4. Trigger

The quiescence window from the participation design stays: a candidate
window opens on a message and closes when the room has been quiet
`settle_seconds`. ONE gate call per closed window, with the whole burst in
context. The send cooldown concept is GONE — the gate does not send
anything, so there is nothing to cooldown. (Live mode hands the message to
Nora, and Nora's own existing guards/cadence apply from there.)

Per-room watermark state, same treatment as the participation design: the
"last window evaluated" cursor is per-room, not per-message.

## 5. Shadow mode

Shadow mode is the shipped default and the review surface:

- Every closed window runs the gate.
- The verdict (group name, incoming message, verdict, relevance_score) is
  posted to the configured review channel — exactly what Miles listed:
  `Group name - incoming message - verdict - score`.
- **Nora is never involved.** 100% of messages are gated, but 0% reach
  Nora while shadow is on, regardless of score.
- Config: `[relevance_gate] shadow = true` (default), `shadow_channel`,
  `shadow_platform`.

The review channel is how Miles tunes the threshold: seeing `go / 0.8` on a
message that needed no answer is a false-positive signal, seeing
`no_go / 0.2` on one that did is a miss. The score exists for this.

## 6. Live mode (the flip, operator-authorized only)

`shadow = false` flips the gate from review to admission:

- `no_go` (or score below the threshold, if one is set) → silence. Nothing
  is sent anywhere.
- `go` → **the original message is handed to Nora as normal message
  ingestion** — the same ingest pipeline, the same policy checks, the same
  (untagged) handling. Nora then does what she does: ambient traffic does
  not construct the agent; the tag path still requires a tag. (Whether a
  GO message should be re-tagged by the gate is an open question for Miles:
  if it is not tagged, Nora's existing behavior treats it as ambient and
  does not answer; if the intent is that GO means Nora SHOULD engage, the
  handoff must say so explicitly — see §8.3.)

The flip is Miles's call. Shadow stays on until he says otherwise.

## 7. What the gate is NOT

- NOT a reply generator — it never drafts, never speaks
- NOT an agent turn — no tools, no doctrine, no loop, no 20k budget
- NOT a classifier trained separately — it is a prompted model call, the
  same class of cheap structured call as the eval judge
- NOT a third send path — it only decides and (in shadow) posts to the
  review channel; live delivery to the room happens through Nora's existing
  paths

## 8. Open questions (for Miles)

1. **Name**: is "Relevance Gate" right, or do you prefer something else?
2. **JSON strictness**: should the gate fail-closed (unparseable → no_go +
   counted) or fail-open (unparseable → go)? Lean fail-closed: a gate that
   silently forwards everything is not a gate.
3. **GO handoff semantics**: when the gate says GO in live mode, should the
   message be delivered to Nora with a synthetic tag (so she MUST respond),
   or as plain ambient traffic (so she MAY consider it, and might still
   stay silent)? This determines whether GO means "must answer" or just
   "worthy of attention".
4. **Threshold**: is there a score floor below which GO is treated as
   NO-GO, or is the verdict the only gate? (Lean: verdict only in v1; the
   score is for tuning, not enforcement, until shadow data says otherwise.)
5. **Settle window**: keep 10s or tune after shadow data? (Lean: keep 10s
   initially — it is a trigger, not a latency budget.)

## 9. Governance

This is a governed change: it adds a new admission component to the ingest
path and a new review surface. It does NOT touch R-1's send-path rules (the
gate never sends to a room; the shadow channel is a new delivery target,
through the same Deliverer/guards with its own audience label). The failed
participation design (35-ambient-participation.md) is superseded but kept
for history — the parts worth keeping (quiescence trigger, shadow review
surface pattern) are inherited here.
