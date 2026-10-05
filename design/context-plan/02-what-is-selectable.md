---
type: design
title: "What is selectable — three classes over 21 blocks, and the transcript's floor"
description: "Every declared block classified as mandatory, selectable, or never-selector-controlled, each with the reason read off the code rather than judged: correctness, safety, attribution, cache and resolvers. The transcript's three knobs and the two resolvers that bound them."
tags: [nora, context-plan, blocks, transcript, correctness, safety]
timestamp: '2026-10-05'
edges:
  - {dst: 01-the-seam.md, rel: realizes, provenance: asserted}
  - {dst: ../context-assembly/04-every-occasion.md, rel: extends, provenance: asserted}
---

# What is selectable

## 1. The classification rule, and why it is not taste

A block is **never selector-controlled** when something outside the prompt reads
it or when its absence changes a *correctness* property rather than a relevance
one. That is a testable criterion: for each candidate, "if this block is absent,
does any code outside `prefill.py` behave differently, or does any test in
`tests/test_acceptance_rules.py::RULE_TESTS` change its meaning?"

Read off the current tree (19 rows in `_BLOCKS` at `nora/agent/prefill.py:122`,
two in `_TAG_MESSAGE_BLOCKS` at `:199`, plus media's `media_text` block):

| class | blocks | why |
|---|---|---|
| **Mandatory — correctness / resolvers** | `group_transcript`, `replying_to`, `tag_message` | The transcript is the input of two resolvers (`<replying_to>`'s parent lookup, R-20, `prefill.py:1131`; `who_said`'s window, DEC-146, passed at `pipeline.py:734`). `tag_message` is the thing the turn exists to answer. `replying_to` is the *verified* parent text — dropping it while the tagger replied to something makes her answer the wrong message. |
| **Mandatory — time / safety / system state** | the clock line (`_now_line`, `prefill.py:70`, rendered at `:1101`), `presence`, `room_members`, `tool_catalogue` | The clock is 15 tokens and its absence makes 今晚/琴日 ungrounded (`_now_line`'s docstring). `presence` is transport truth: a cached "up" is how she promises a send that then fails (`TagContext.presence`'s docstring). `room_members` is the room's own listed persons — the scoped answer to "who is here" (`build_prefill`'s docstring records the leak when an unscoped roster was used). `tool_catalogue` exists only under `[llm] defer_tools` and names the *same* admitted set the turn's tools were built from — it cannot name a tool the turn cannot load, and removing it removes her ability to load one. |
| **Mandatory — the tagger's own recall** | `peer_card`, `peer_history` | These are keyed on **who tagged her**, not on "a person in the room". A turn with no card is a turn where she does not know who is asking (the `build_context` trap in `nora/app/fire.py:822` documents the whole cascade when the tagger resolves to `None`). If a selector may withhold the tagger's card, the selector has become a personalisation off-switch. |
| **Selectable — room/unit context, per turn** | `culture`, `character`, `notes`, `loop_decisions`, `standing`, `standing_working_set`, `computer`, `documents`, `subagents`, `people_in_window`, `self_card`, `media_text` | Each is advisory: absence costs continuity or an affordance, never truth. `self_card`'s own docstring says it "is never the ground for a claim, so an empty one costs continuity, not truth". `computer`/`documents`/`subagents` are affordances ("what I have made in this room"), already conditional on config and on the room having something. `media_text` is a description of media in the window — advisory, and its absence degrades to the stored `[image …]` handle. |

**Consequence, and it is the sharpest finding in this bundle:** the class that
*is* selectable is the one the operator's example keeps. Their sample plan is
`{presence, peer_card, standing, group_transcript}` — three of those four are in
the mandatory class above, and the selectable class they would be choosing among
(`culture`, `character`, `notes`, `computer`, `documents`, `subagents`,
`loop_decisions`, `people_in_window`, `media_text`) is absent from it. A
selector that only ever *removes* from the selectable set is a selector whose
best case is "answer without the room's culture"; a selector that can remove a
mandatory block is a correctness regression.

## 2. `culture` and `standing` are the live question, and they get different answers

The operator asks (Q5) whether Culture and Standing must always reach the main
model, giving the right reason for asking — the selector needing them does not
imply the main model does. The two answers differ, and the code says why:

* **`standing` — keep it mandatory in practice, even though it is classed
  selectable.** Two facts point the same way. It is the only block that is her
  own position *in this conversation*, it sits immediately before the transcript
  on purpose so the pair reads "where I was, what has been said since"
  (`_standing_block`'s docstring), and it is revised in place rather than rebuilt
  (`TagContext.standing`'s docstring) — so withholding it on a turn and showing
  it on the next produces a position the model never saw it form. It is also
  capped at 1,200 chars (`DEFAULT_STANDING_MAX_CHARS`, `nora/domain/standing.py`),
  which is the cheapest mandatory block in the prompt. **A selector's win from
  dropping `standing` is small and its continuity cost is high**, so it should be
  measured, not assumed.
* **`culture` — this is the one genuinely worth testing.** It is the largest
  selectable block (capped prose, `CultureRevision.body`), it is about the room
  rather than the turn, and there is a real case where a turn does not need it
  (a factual lookup; a direct question about logistics). It is also the block the
  repo has the most evidence *against* removing by default: `culture` reached the
  tag path as one row in `_BLOCKS` and the whole point of the tier is that a
  window judged against nothing is judged in a vacuum
  (`nora/domain/culture.py` docstring). So `culture` is the right subject of the
  experiment and the wrong subject of an a-priori decision.

`character` is the other candidate of the same size and shape, and it has the
same argument as `culture` with one addition: it is keyed on the room's members,
so a selector would need `room_members` *and* the member list to decide, which
means the selector's substrate grows by a block that itself is a list.

## 3. The transcript: three knobs, one floor, and two the plan must not touch

The operator asks (Q6) for better than a binary `group_transcript=yes/no`. Under
the code there are four distinct things, and only two of them are the plan's:

1. **Extent** — `[store] context_window = 40` (`config.toml:371`). This is the
   plan's `transcript_window`. It is the knob the idea is really about, and it is
   already a config value per deployment rather than per turn.
2. **Quote growth** — `QuoteWindow.recent_for_quote` may read **past** the window
   to resolve a quoted parent (`nora/engagement/context.py:409` docstring,
   DEC-195; bound `context_window * quote_window_multiplier`). A plan must not
   shrink *below* what the quote resolver needs, or the reply quotes at a message
   the model was never shown. **This is a hard lower bound and it is not
   expressible as a scalar in the plan** — the honest shape is a floor the
   pipeline computes (`max(plan.transcript_window, rows_needed_for_quote)`) and a
   recorded reason when the floor wins.
3. **Recency / sender / threading** — *not available* on this prompt. The rows
   are one oldest-first tuple (`RecordStore.recent`), and the two things that
   look like filtering already exist elsewhere: `people_in_window` (what Honcho
   concluded about each speaker, `:709`) and `who_said` (a *tool* the model can
   call for sender-specific history, `nora/agent/tools.py`). A plan that filtered
   rows by sender would have to run *before* `name_transcript` and `MediaJoin`
   (`context.py:257`, `:271`), i.e. before the context is frozen — that is Phase 2,
   and it puts the plan on the critical path of the only concurrent gather there
   is.
4. **`first_by_ts` / `_by_ts`** — the same lookup feeds `<replying_to>` and the
   transcript's `reply_to` marks. Truncating the head of the window silently
   changes what `reply_to="n"` means, which is why §1 puts `group_transcript` in
   the mandatory class and why the knob is *extent from the newest end*, never
   a filter.

So the answer to Q6 is: **the plan gets extent, floored by the resolvers, and it
does not get recency, sender or threading until the plan runs before the context
is frozen** — which is the expensive version and belongs to `04`'s step 3, if
ever.

## 4. What a plan cannot say

Deliberately absent, so a later reader does not assume them:

* It cannot select a **tool**. The tool surface is built beside the prefill from
  the room's policy and `[llm] defer_tools` (`ToolRegistry.build`,
  `pipeline.py:~700`); `tool_catalogue` only *describes* it. A plan that wanted
  to narrow tools would be a different, larger change and would touch R-7's
  default-deny surface.
* It cannot select a **doctrine section**. Doctrine is authored and resolved by
  `applies_to` (`DoctrineStore.get(applies_to="tag")`, `pipeline.py:610`), and it
  is 72% of the prompt — the only place a big number is available. It is also
  the one place where "the model decides what the model may believe it can do"
  is exactly backwards (`design/17-prompt-substrate.md` is the whole argument).
* It cannot select a **skill** (`<available_skills>`), for the same reason.
* It cannot make a block **render smaller** — only present or absent. A
  `max_chars` per block would be a second, competing cap to the ones the
  renderers already own (`render_notes`' per-entry caps, `culture`'s
  `BODY_MAX_CHARS`, `standing`'s 1,200) and would be exactly the "two places one
  number lives" drift `design/context-assembly/00-overview.md` §"The drift this
  has already produced" records twice in one day.
