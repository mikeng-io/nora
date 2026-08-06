---
kind: design
title: "Nora — multi-message replies: relaxing R-2"
description: "Letting one tag produce several messages, and why the tool_choice sequence is the whole mechanism."
tags: [nora, design, r-2, reply-guard, tool-choice, voice]
timestamp: '2026-08-06'
status: proposed
---

# Multi-message replies

**R-2 today: one reply per tag.** One tag, one `send_reply`, one message. The
operator wants two, three or four — because that is how people text, and
because it is what lets a GIF land on its own (`design/15` §1.1).

This document changes R-2 from *one message* to *one turn, bounded messages*.
It is not a GIF feature; it applies to every reply Nora sends, and GIFs merely
become possible once it exists.

## 1. Why this is not a one-line change

`ReplyGuard`'s `BeforeModelCallEvent` hook cancels the agent loop the instant
a reply lands (DEC-21). That cancellation is not an optimisation — with
`tool_choice="required"`, **it is the only thing that ends a turn**. The model
is forced to call a tool on every pass, so it cannot stop by choosing not to.

Make `send_reply` non-terminal and nothing terminates the loop. It runs to
`llm.limits.turns` and emits a message on each pass. A message *cap* does not
fix this — with no way to stop early, the cap becomes a quota that is always
spent in full, and every question gets the maximum number of messages.

So the question is not "how do we allow more than one message". It is **"what
lets her stop"**.

## 2. The mechanism: force the first call, then relax

`tool_choice="required"` until the first reply lands; `tool_choice="auto"` for
the rest of the turn. She is compelled to answer, then permitted to stop.

### 2.1 Measured, not assumed

`opencode-zen/deepseek-v4-flash`, `reasoning_effort="low"`, 2026-08-06:

    A. auto, fresh question          plain_text 5/6, called_tool 1/6
    B. auto, already replied         STOPPED 8/8
    C. required, already replied     kept_going 6/6            (control)
    D. auto throughout, invited      0 messages sent 6/6

    required until first reply, then auto:
       neutral prompt                1 message  6/6
       invited to multi-send         3 msg ×1, 4 msg ×3, 5 msg ×2

**A and D say why `required` cannot simply be dropped.** Under `auto` on the
*first* call the model answers in plain prose 5/6 — no tool call, therefore no
message, therefore Nora is silent in the group while the turn looks
successful. D confirms it end to end: zero messages, 6/6. That is R-5's exact
failure shape, and dropping `required` globally — the obvious simplification —
would have shipped it.

**B is the permission**, against C as its control: once a reply exists in
history, `auto` lets her stop 8/8, where `required` never stops 6/6. The loop
ends because she declines to call anything, not because a limit caught her.

**The count is a voice decision.** Identical mechanism, two system prompts:
neutral yields exactly one message 6/6 — today's behaviour, no regression —
and an invitation to chat like a person yields 3–5. How talkative she is
belongs in `voice.md`; the mechanism only makes more than one *possible*.

### 2.2 The cap is still required

Two of six invited runs hit the probe's five-hop ceiling, so she does not
reliably stop within a sensible number on her own. `llm.limits.messages`
(new) bounds it. The cap is a backstop against a bad prompt flooding the
group, not the primary stopping mechanism — §1's failure is precisely what
happens when a cap is asked to be the mechanism.

Five messages for 「講下你自己」 is also simply more than a person sends. That
is a `voice.md` tuning problem, and it should be tuned by reading real
replies, not by guessing a number here.

## 3. What R-2 becomes

> **R-2 (revised): one tag produces one turn, and a turn produces at most
> `llm.limits.messages` messages, all to the tagging conversation.**

The invariant worth preserving is not the number one — it is that **a tag
cannot produce unbounded output, and cannot produce output anywhere but where
it was tagged**. The second half is untouched: `send_reply` still goes to the
deliverer's bound target (DEC-21, R-3), so multi-message changes how many
messages, never where they go.

## 4. What moves with it

- **`ReplyGuard` stops being a canceller and becomes a counter.** It cancels
  at the cap instead of at the first reply.

- **The `tool_choice` flip CANNOT live in the guard — corrected 2026-08-06.**
  An earlier draft of this section said the guard would flip `required` →
  `auto` after the first send, "which keeps the sequence in one place". That is
  not implementable, and the naive version reintroduces the failure this
  document exists to prevent. Two verified blockers:

  1. **`BeforeModelCallEvent` cannot write it.** `_can_write` is
     `return name == "cancel"` (verified in the installed SDK). The hook can
     cancel the loop and nothing else — it cannot reach the model or its
     params. The only route is `agent.model.update_config(params=…)`.

  2. **There is one `OpenAIModel` per process, shared by every concurrent
     tag.** `nora/app/composition.py:230` constructs it once and hands it to
     `LiveAgentRunner`; `factory.create_model_provider` sets
     `params={"tool_choice": "required", …}` on that one object.
     `EngagementPipeline._in_flight` keys on `(platform, conversation_id)`, so
     two conversations run concurrently. Flipping `tool_choice` to `auto` for
     tag A therefore flips it for tag B's **first** call — and §2.1 A/D
     measured that a first call under `auto` sends nothing 5/6 to 6/6. **The
     fix for R-5's failure shape would reintroduce R-5's failure shape,
     non-deterministically, only under concurrent load, and therefore almost
     never in testing.**

  **Resolution: construct the `OpenAIModel` per tag**, over the process-wide
  `AsyncOpenAI` client. That keeps audit T2's "one client per process" — the
  guarantee that actually matters, since it is what pools connections — while
  making `tool_choice` per-tag mutable state. This is not a new pattern here:
  `factory.build_retry_policy`'s own docstring already argues it for
  `ModelRetryStrategy` ("one shared instance would let one tag's retry counter
  decide another tag's backoff"). The identical reasoning applies and this
  document originally missed it.

  It also buys something independently useful: `reasoning_effort` becomes
  per-tag, which is the escape hatch `config.toml` already flags as needed if
  the pinned route changes behaviour.

- **`Deliverer` is where R-2 actually lives, not `ReplyGuard`.** Its
  `_attempted` latch counts ATTEMPTS, not successes (`nora/agent/guards.py`),
  and it is R-2b's own registered rule. A change list that names only
  `ReplyGuard` would leave the real single-shot mechanism in place and the
  feature silently dead. `Deliverer._last_text` and
  `nora/engagement/stages/feedback.py`'s `text = deliverer.last_text or ""`
  are the same problem in the memory path: one string is fed to Honcho, so a
  four-message reply would feed the LAST message and silently drop the first
  three.
- **R-6's canary and R-18 run per message.** Each message is an independent
  egress event. A guard that ran once per turn would inspect the first
  message and wave the rest through.
- **A9 record-first applies per message.** Each message gets its own audit row
  and its own `memory.feed`. Recording a multi-message reply as one row leaves
  Honcho with a version of the conversation that did not happen — and this
  repo has already lost derived conclusions once by being careless about what
  memory receives.
- **The 👀 → ✅ lifecycle stays per TAG.** `indicate.resolve` moves to after
  the last message. Resolving on the first would mark the turn done while she
  is still typing, and `delivered` becomes "at least one message went out".
- **Debounce and R-10 are unaffected** — they key on the tag, not the reply.
- **Every "exactly one reply" assertion in the suite is the current rule's
  specification.** `_reply_turns`, the `adapter.texts == [...]` assertions,
  and the reaction tests all encode R-2. Rewriting them *is* the change, not
  cleanup afterwards — so each must be rewritten deliberately, not
  mechanically relaxed to "at least one".

## 5. Sequencing — this ships before GIFs

The measurement makes the order clear: under a neutral prompt the new
mechanism produces exactly one message 6/6, identical to today. So R-2's
relaxation can land, be deployed, and be observed on ordinary text replies
**without any behaviour change at all**, and the multi-message behaviour is
then enabled by a `voice.md` edit — a separate, revertable change.

That is strictly better than debuting it alongside GIFs, where a fault could
be either the new reply loop or the new byte-sending path. Ship the mechanism
inert, turn it on in the doctrine, then add GIFs.

## 6. What must be true before this ships

1. `tool_choice` is `required` for the first call of every turn and `auto`
   thereafter, with a probe proving a turn whose first call was `auto` is
   rejected by the guard — A/D show that path is silence.
2. `llm.limits.messages` exists and the guard cancels at it, with a probe
   proving a model that never stops is capped.
3. The canary and R-18 run on every message, probed by a turn whose *second*
   message contains a phone number.
4. Every message is recorded and fed independently, probed by asserting the
   audit row count and `memory.feed` count match the message count.
5. `indicate.resolve` fires once, after the last message.
6. Under the current neutral `voice.md`, behaviour is unchanged — one message
   — and that is asserted, not assumed.
7. R-2's revision is recorded in `design/09-decision-index.md` with the old
   wording preserved, and its `RULE_TESTS` entry updated rather than replaced.
