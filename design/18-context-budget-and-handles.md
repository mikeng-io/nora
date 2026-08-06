---
kind: design
title: "Nora — asking for more without drowning: the turn's context budget and message handles"
description: "Rationing fetched content per turn, and giving her a way to point at a specific message without inventing a second identity for it."
tags: [nora, design, context, budget, tokens, message-id, threading]
timestamp: '2026-08-06'
status: proposed
---

# The turn's context budget, and message handles

Two pieces that arrive together because neither is much use alone: a way for
Nora to ask for **more** of the conversation, and a way to stop that from
killing the turn.

## 1. The problem, measured

Every tool result stays in the message list for the rest of the turn and is
re-sent on **every subsequent model call**. Measured 2026-08-06:

    base prompt (doctrine + schemas + 40-row window)   4,952   every call
    read_buffer(60) result                            ~2,000   every call after
    read_buffer(200) result                            6,592   every call after
    recall (5 conclusions)                               151
    lookup_person                                        112
    total_tokens                                      64,000   the turn's ceiling

`read_buffer`'s clamp was cut from 200 to 60 for exactly this reason, and at
`budget: 2` she can still spend two of them. **The clamp bounds one call; it
does not bound the turn.** Nothing today does.

That gap is why adding `read_thread` (§3) or `search` (`design/14`) without
this component would be irresponsible: each is another unbounded intake on a
budget with no accounting.

## 2. `ContextBudget` — ration the fetch, never evict

A per-tag object, constructed beside `Room` and dying with the turn. It knows
the base cost and the remaining `total_tokens`, and it tracks what fetched
content has already been spent. Every fetching tool asks it **how much it can
afford right now** and is given a number, rather than deciding for itself.

So `read_buffer(60)` early in a turn returns 60 rows; the same call after a
large fetch returns 20. The tool degrades; the turn does not die.

**Why not eviction.** The obvious alternative is dropping old tool results as
the turn grows — what Strands' `SlidingWindowConversationManager` does, and
which Nora deliberately does not use (`NullConversationManager`, because she is
stateless per tag). Mid-turn eviction is worse than it looks here: she may have
already reasoned from a result that is then deleted, so she contradicts herself
with no trace of why. **Budgeting the fetch never removes something she has
already seen.** That property is worth more than the tokens eviction would
recover.

**"Sliding window" is the wrong name** and was used loosely in conversation
before this document existed. Nothing slides. A finite per-turn resource is
rationed, and `ContextBudget` says so.

Two properties, both non-negotiable:

- **Truncation is legible in the return value.** If she asked for 60 and got
  20, the result says so. Otherwise she tells the group 「我睇晒喇」 having seen
  a third of it — a truncated answer that reads as complete, which is the
  failure shape `CLAUDE.md` records three instances of, and the same
  distinction `recall` already draws between "nothing known" and "could not
  ask".
- **It is per-tag state and must die with the tag.**
  `tests/architecture/test_no_retained_per_tag_state.py` exists because
  `Handler._prefills`/`_guards` leaked exactly this way. The new object is
  covered by that same gc-counted assertion, or the leak has been rebuilt under
  a new name.

**It is also the answer to `design/14`'s open question.** That document asks
"how much retrieved text can a turn afford" and leaves it unresolved. It is
this object, with a different data source. One budget serving `read_buffer`,
`read_thread`, `recall` and eventually Firecrawl means one place to reason
about intake — rather than four tools each guessing, which is how the
`read_buffer(200)` hole existed in the first place.

## 3. Message handles — `#7`, not an ID

`_parent_of` already resolves `quoted_ts` to the exact parent `TranscriptRow`,
and `_transcript_block` then **throws the row away** and renders only the
author's name. So:

    (2h ago) [Grant]: 去邊食
    (2h ago) [Grant]: 我夜啲得閒
    (1h ago) [Tyler ↳ Grant]: 好呀

`↳ Grant` is ambiguous — 好呀 answers one of two different questions and she
cannot tell which. The information exists; it is discarded at render time.

Two different needs, and they want different answers:

**Reading — a short quoted snippet.** `[Tyler ↳ Grant「去邊食」]`. An index
would make the model resolve a reference by scanning back and matching a
number, an attention task it does *mostly* right, which is the worst kind. A
snippet is understood in place. It also matches `_replying_to_block`, which
already quotes the resolved parent for the tag message — extending an existing
convention rather than inventing a second. Truncate hard, 12–16 characters;
only reply-lines pay it and replies are a minority.

**Pointing — a per-window handle.** `#1…#40`, assigned at render time over
whatever window this turn happens to see, so she can pass one to a tool.

### 3.1 `ts` is the identity; `#7` is a label

**No new column, no incremental per-conversation ID.** `ts` is already the
canonical identifier — globally unique, Signal-native, and literally what
`sendReaction` and quotes target. A persisted `#n` would give each row **two
identities** that can diverge under backfill, deletion, out-of-order arrival or
the adoption sweep, and nothing would notice.

The handle is **ephemeral**: built when the prompt is built, used to translate
a tool argument back to `ts`, discarded when the turn ends. The map lives on
the per-tag object for exactly as long as the labels mean anything. The model
never sees a raw timestamp, the same way it never sees a conversation id
(DEC-21).

Because it is per-window, the same message is `#7` this turn and `#12` next.
That is safe *only* while it stays inside the turn:

> **A handle is an input, never an output.**

Two escape routes, both closed by a predicate rather than by instruction:

- **Into Honcho.** 「#7 講嗰句」 fed to memory is meaningless once the window
  moves — and worse, it will later *resolve* to a different message and read as
  coherent. Silently wrong beats loudly wrong here.
- **Into the group.** 「你 #7 講嗰句」 is machine-speak to five humans in a
  chat. She should say 「你頭先講去邊食嗰句」 like a person.

`#\d+` is caught in reply text and in the memory feed. That is a real gate,
testable by reverting it — unlike a doctrine line asking her not to.

### 3.2 `read_thread`

`read_thread(handle)` returns the reply chain around one message — its parent
and its replies — bounded by `ContextBudget`. It takes a handle, never a `ts`,
never a conversation id (DEC-24: a value Nora already knows must not appear in
the schema).

This is the tool the handles exist for. Without it they are decoration; without
them it would have to take a timestamp the model would invent.

## 4. What must be true before this ships

1. `ContextBudget` is per-tag, constructed beside `Room`, and covered by
   `test_no_retained_per_tag_state.py`'s gc-counted assertion.
2. Every fetching tool asks the budget rather than clamping independently;
   `read_buffer`'s fixed clamp becomes a budget query.
3. A truncated result says so in its return value, probed by asserting a
   short-fetch result is distinguishable from a complete one.
4. Handles are render-time only. No schema change, no new column, and a test
   that `ts` remains the only persisted identity.
5. `#\d+` cannot reach the group or Honcho — probed by a reply and a feed that
   each contain one.
6. `read_thread` takes a handle and nothing else.
7. `design/14` §3's "how much retrieved text can a turn afford" is answered by
   pointing here rather than restating it.
8. New rules registered in `design/09-decision-index.md` and `RULE_TESTS`,
   each probed failing first. Next free numbers are R-25/R-26 (R-23/R-24 went
   to the budget-legibility work).
