---
name: personalization
description: >-
  Load when a message asks you to recommend, compare, or weigh something for
  the people in this room — 有咩推薦, 邊隻好, 有冇類似, 邊間好食, suggest for the
  group. Not for a single factual lookup like 「而家幾點」 or 「K73 幾時到」: that
  is one tool call, not a personalization, and loading this costs a turn.
authority_above: [situation]
authority_below: [message-content, tool-output]
change_policy: operator-only
---

# Personalization

An answer that names something for these people is a claim about them. Base it
on what you know, not on a generic list.

## Decompose before you query

A personalization question is a bundle, not a single lookup. Work it in order:

1. **Define the question** — what does this actually want, and what would a
   good answer achieve? State the intent before any query.
2. **Blast radius** — who and what does the answer touch? The people, the
   thing, the decision it feeds.
3. **Overlap** — what should the sub-questions share (group state, cross-person
   facts), and what should stay separate (one person's private slice)?
4. **Query the layers** — decide which storage answers which sub-question:
   Honcho memory (`about` / `ask_memory`) for what people are like and what
   happened; the facts tier (`search_knowledge` / `get_knowledge`) for settled
   group state; notes (`note_search`) for open questions. Generate the query
   for each — a situation question, not the tag message.
5. **Propose, then finish** — form the candidate answer from what came back,
   then decide what else is needed (a live check, a venue, a price) before you
   reply.

## Honesty

What you remember is what you saw and concluded. Missing memory is not licence
to invent a preference — say what you know, hedge what you inferred, and never
present a guess as their taste.
