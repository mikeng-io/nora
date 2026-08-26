---
kind: design
title: "Nora — Relevance Classifier (True Ambient, A3)"
description: "Design brief for the ambient relevance classifier: model, prompt, threshold, off-path queue, and shadow-mode experiment. Replaces the boolean trigger gate for non-tagged messages without weakening explicit tags (R-5)."
tags: [nora, design, proposed, classifier, ambient, a3]
timestamp: '2026-08-26'
edges:
  - {dst: ../02-tagging-path.md, rel: amends, provenance: asserted}
  - {dst: ../00-overview.md, rel: amends, provenance: asserted}
  - {dst: ../../nora/domain/ports.py, rel: implements, provenance: asserted}
  - {dst: ../../nora/ingest/policy.py, rel: implements, provenance: asserted}
  - {dst: ../../docs/superpowers/specs/2026-08-04-nora-architecture-design.md, rel: derives, provenance: asserted}
---

# Relevance Classifier — True Ambient Hooks (A3)

## 1. Purpose

Today the only ambient→tagging hand-off is the deterministic `tag.is_tagged()`
gate (`design/02-tagging-path.md` §True Ambient Hooks). This brief designs the
A3 milestone: a **single cheap LLM completion on the ambient path** deciding
yes/no before the tagging path runs — so Nora can respond to a message that
*needs* a response without requiring "Nora" / @mention / reply-to-her.

Scope: **full ambient** (all messages, per operator directive 2026-08-26, "2").
Runs in **shadow mode first** — decision logged, not acted on — until the
experiment metrics say the classifier is worth trusting.

## 2. Constraints (non-negotiable, from existing doctrine)

| # | Constraint | Source |
|---|---|---|
| C1 | Ambient path never passes through the Agent SDK. Classifier is a stateless single completion. | D1 (as amended by A3), `design/00-overview.md` |
| C2 | Explicit tags keep working even when the classifier is down. Classifier is **additive**, never a replacement for the deterministic gate. | R-5; `policy.py` module docstring; `design/09-decision-index.md` A3 entry |
| C3 | `on_event` is awaited; an inline LLM call would stall the SSE reader for every adapter. Classifier must run **off-path behind a bounded queue**. | spec §4.3; `design/02-tagging-path.md` §True Ambient Hooks |
| C4 | Nora is invisible when things break: classifier failure → stay silent, log, do not block ingest. | `design/02-tagging-path.md` §Error Handling |
| C5 | The classifier's reason must reach the tagging agent's context prefill. | `design/02-tagging-path.md` §True Ambient Hooks; `EngageDecision.rationale` exists for this |
| C6 | Voice-note-only tag-based is NOT what is being built here. Operator reversed the earlier "any voice note engages" decision: voice notes without a tag now go through the same classifier as any other message. | Operator directive 2026-08-26 (reversal) |

## 3. Composite gate (unchanged shape)

The gate stays first-match-wins, deterministic first, short-circuiting
(`policy.py` docstring already names the final shape):

```
CompositePolicy([
    TriggerTextPolicy("nora"),   # explicit text tag — unchanged
    MentionPolicy,               # @mention — unchanged
    ReplyToNoraPolicy,           # reply-to-her — unchanged
    ClassifierPolicy,            # NEW — only consulted when all deterministic
                                 # policies said no
])
```

Consequences:
- Explicit tags are never classifier-dependent (C2).
- The classifier only *adds* engages; it can never suppress a tag.
- `VoiceNotePolicy` (operator 2026-08-26) is **replaced** by `ClassifierPolicy`
  for untagged voice notes. A voice note whose transcript contains "nora"
  still engages via `TriggerTextPolicy` exactly like text.

## 4. Classifier design

### 4.1 Model & inference knobs

- **Model:** `deepseek-v4-flash` (same route as the main loop, `config.toml`
  `[llm] default`, via OmniRoute). Never `-pro`: measured in this repo,
  flash-at-none obeyed a forced tool_choice 8/8 where pro did 2/3 — the larger
  model buys nothing and costs more (`config.toml:81-84`).
- **Reasoning effort:** `none`. The classifier is not tool-mediated; reasoning
  cannot improve protocol compliance and only adds tokens/latency. Repo
  measurements (`config.toml:104-110`): effort `none` → ~49 output tokens,
  p50 1.2s; `low` → 70 tok / 1.1s; `high` → 104 tok / 2.9s. `none` is the
  fastest and cheapest; the output is a yes/no + one-line reason.
- **temperature:** 0. This is a gate, not a creative call. A gate must be
  near-deterministic (same principle as the eval judge, DEC-75).
- **max_tokens:** ~120. Output contract is `{"engage": bool, "reason": str}`
  — anything longer is a runaway.

### 4.2 Prompt contract

Single completion, system + user. System prompt (draft — to be refined in
implementation):

```
You decide whether Nora, an ambient assistant in this group chat, should
respond to a message. She does NOT need to respond to everything.

Respond YES when:
- The message asks a question or requests information
- The message directly addresses Nora or discusses something about her
- The message is a voice note whose content is conversational content aimed
  at the group (not ambient noise, not a command meant for another bot)
- The group is waiting on an answer / the conversation stalls without a reply
- A factual claim is made that Nora can usefully ground or correct

Respond NO when:
- The message is a status update, a link share, or small talk with no
  question and no ask
- The message is a command aimed at another bot or service
- The message is an acknowledgement ("ok", "thanks", "👍")
- You cannot tell what the message wants; absence of evidence is NO

Reply with JSON only: {"engage": true|false, "reason": "<one line>"}
```

Input includes:
- The message body (for voice notes: the transcription, already rendered
  `[voice note #hex: …]` by the media stage)
- Sender (person name, not raw id — resolved identity if available)
- Conversation type (group vs DM)
- **Bounded recent context** (see §4.4 — the digest question)

### 4.3 Off-path queue (C3)

- A bounded `asyncio.Queue` (e.g. max 256) fed by the ambient path after the
  deterministic gate says no.
- A single worker consumes: builds prompt → calls flash → writes decision.
- The worker **never blocks ingest**: queue full → drop the classifier call,
  log a counter (`nora_tags_classifier_dropped_total`), stay silent. Dropping
  a classifier decision is safe by construction: it can only *under*-engage,
  never break a tag (C2).
- Message ingest/audit/Honcho feed is unaffected — it happens before the
  queue enqueue, and the classifier decision is an additional async write.
- Latency budget: the classifier does not sit on the reply path. The
  engagement pipeline (when YES) starts from the decision, not from the
  message arrival — acceptable, since ambient responses are not latency-SLO'd
  the way tag replies are (`design/02` p95 ≤ 5s applies to explicit tags).

### 4.4 Context digest — the "ALL messages" question

Operator expectation: classifier must be **stunning fast and low token**
because it digests *all* messages. Per §4.1 the single completion is ~250-350
input tokens + ~50 output. That is already cheap at ~200 msgs/day
(≈60-70K input tokens/day total — cents/day on flash).

Design decision — **no full-conversation digest in v1**:
- Input context = the message itself + sender + room + ≤5 recent messages
  (bounded, cheap).
- A rolling digest of the whole conversation is a *different* capability
  (recall/state summarization) with real token cost and staleness problems.
  The classifier's job is a per-message relevance call, not a conversation
  summary. If the experiment shows relevance decisions need deeper context,
  that is a v2 finding — logged in the experiment, not assumed.
- The one exception: a lightweight per-room "what was the last thing Nora
  said/did" line (from SQLite, ~50 tokens) helps the "is she being asked to
  continue" case. Cheap, deterministic, no LLM.

### 4.5 Threshold mechanism

The completion returns a JSON decision, not a score — so there is no numeric
threshold in v1. `EngageDecision.confidence`/`threshold` stay `None` for
classifier decisions in v1; the `reason` string rides in `rationale` so the
tagging agent knows why it was invoked (C5).

Future v2 option (documented, not built): ask for a 0-1 confidence alongside
the decision, set a config threshold, and log the score distribution — that
is what turns the classifier from a gate into a tunable policy. The
`EngageDecision` fields exist precisely so this does not change the port.

## 5. Shadow-mode experiment

Ship the classifier **off**, wired but not engaged. Deterministic gate stays
the only path that engages. What runs in shadow:

1. Every message that reaches the classifier slot (deterministic policies
   said no) gets a real flash call.
2. Decision + reason logged to SQLite (`data/nora.log` + a new
   `classifier_decision` audit row) with message id, room, timestamp, latency.
3. Metrics: `nora_tags_classifier_total{decision="yes|no"}`,
   `nora_tags_classifier_latency_seconds`, `nora_tags_classifier_dropped_total`.
4. **No engagement happens from classifier decisions.** R-5 holds: a
   classifier bug in shadow can cause log noise, never a wrong reply.

Experiment question (what makes the shadow useful): if the classifier had
been live, which decisions would have changed Nora's behavior, and would
that have been right?

- **Precision** (of YES): for each YES decision, would a real reply have been
  warranted? Reviewed by operator daily on the log. A YES that would have
  been noise is a false positive.
- **Recall** (of NO): for each NO, would silence have been right? A NO where
  the group was clearly waiting is a false negative.
- Cross-check with the voice-note corpus already collected (design bundle
  `design/editorial-voice-planner-enforcement-2026-08-26.md`): does the
  classifier agree with human judgment on those notes?

Duration: **≥ 5 days** or ≥ 1,000 classifier decisions, whichever comes
later — enough volume to see real group dynamics, not a demo day.

Exit criteria (operator decides, after seeing the numbers):
- Precision ≥ 0.8 and recall ≥ 0.8 on the shadow log review → flip to live.
- Borderline → keep shadow, tune prompt, re-run.
- Poor (≤ 0.5 on either) → kill the classifier idea, keep the deterministic
  gate. A bad ambient gate is worse than no ambient gate: every false engage
  trains the group to ignore Nora.

## 6. Cost

| Item | Estimate |
|---|---|
| Traffic | ~200 msgs/day (measured: 257 envelopes / 30h, signal only) |
| Input/msg | ~300 tokens (message + sender + room + ≤5 recent + last-Nora line) |
| Output/msg | ~50 tokens (JSON) |
| Daily | ~60-70K tokens total |
| Flash tier | cents/day (exact price not pinned in repo — verify against OmniRoute catalog before shipping; `mcp__omniroute__check_quota`/`list_models_catalog`) |

Latency: p50 ~1.2s per decision, off-path, invisible to ingest.

## 7. Implementation sketch (NOT built — for the follow-up)

```
nora/ingest/classifier.py        # ClassifierPolicy + prompt builder
nora/ingest/classifier_queue.py  # bounded queue + single worker
nora/app/composition.py          # wire queue; CompositePolicy([..., ClassifierPolicy])
nora/config.py                   # [classifier] enabled/shadow/model knobs
nora/domain/ports.py             # unchanged (EngageDecision already fits)
nora/ingest/stages/audit.py      # classifier_decision row (shadow log)
tests/unit/test_classifier.py    # prompt contract, JSON parsing, fail-closed
tests/test_policy.py             # CompositePolicy ordering, VoiceNotePolicy removal
release-fragments/unreleased/    # feature fragment
```

Gate order in composition stays: TriggerText → Mention → ReplyToNora →
Classifier. `VoiceNotePolicy` is deleted from composition when the classifier
lands (its job — engage on voice content — is subsumed; an untagged voice
note still engages *only* if the classifier says yes, per C6).

## 8. Open questions (resolve before implementation, not before this brief)

1. **Room scoping**: classifier in every room, or only rooms where Nora is
   "ambient" (not `policy.toml`-denied)? Default: only rooms where she is
   allowed to speak at all — a denied room never reaches the classifier.
2. **Cost ceiling**: is cents/day acceptable long-term, or is there a daily
   decision budget (e.g. max N classifier calls/day, then NO)? Operator call.
3. **Voice-note precedence**: a voice note with a transcript containing
   "nora" engages via TriggerTextPolicy — correct? (I believe yes; it is
   exactly the same as text saying "nora".)
4. **Shadow review cadence**: operator reviews the log daily, or a cron
   digest? Default: daily Discord summary is enough for 200 msgs/day.
