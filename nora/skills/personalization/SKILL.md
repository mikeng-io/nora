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

Naming something for these people is a claim about them — their taste, their
state, what fits them. Work from what you know, not from a generic list.

## The one judgement: how much does the answer cost them?

A thrown-away question earns a quick read: one person, one layer, done. A
question the group will act on — which thing to commit to for a year, where to
book, what to buy — earns the full decomposition below.

## The ladder, when the answer matters

1. **What does this question want?** Not its words — its intent. What decision
   or taste is it feeding? State that before any query.
2. **Who and what does the answer touch?** The people whose taste it claims,
   the thing it names, the decision it feeds.
3. **Which sub-questions share, which stay apart?** Cross-person facts and
   group state belong together; one person's private slice stays separate. Do
   not fan out across the whole room — pick the one or two people the answer
   turns on, one deep, others shallow.
4. **Ask each layer the question it answers.** Memory for what people are like
   and what happened; knowledge for settled group state; notes for what is
   still open. Generate the query yourself — a situation question, not the
   message you were tagged in.
5. **Propose, then finish.** Form the candidate answer from what came back
   before reaching for anything else; only then decide what a live check, a
   venue, or a price still needs to add.

## Empty is a real answer

If the layers come back empty, the generic list is the honest answer — say it
is generic because you have nothing on them yet, not because you forgot to look.
Missing memory is never licence to invent a preference; a guess dressed as
their taste is the one unforgivable answer here.
