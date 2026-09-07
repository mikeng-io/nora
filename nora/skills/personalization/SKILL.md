---
name: personalization
description: >-
  Load when a message asks you to recommend, compare, or weigh something for
  the people in this room — any ask for suggestions, which one is better,
  what is similar, where to eat, what to play, suggest for the group. Not for
  a single factual lookup like the current time or a live transit check: that
  is one tool call, not a personalization, and loading this costs a turn.
authority_above: [situation]
authority_below: [message-content, tool-output]
change_policy: operator-only
---

# Personalization

The principle — recommending something claims their taste, and empty memory
is an honest answer — lives in conduct.md. This is the procedure for when the
answer matters enough to decompose.

## The ladder

1. **What does this question want?** Not its words — its intent. What decision
   or taste is it feeding? State that before any query.
2. **Who and what does the answer touch?** The people whose taste it claims,
   the thing it names, the decision it feeds.
3. **Which sub-questions share, which stay apart?** Cross-person facts and
   group state belong together; one person's private slice stays separate. Do
   not fan out across the whole room — pick the one or two people the answer
   turns on, one deep, others shallow.
4. **Ask each layer the question it answers.** Memory for what people are like
   and what happened; notes for what the room has not settled. Generate the
   query yourself — a situation question, not the message you were tagged in.
5. **Propose, then finish.** Form the candidate answer from what came back
   before reaching for anything else; only then decide what a live check, a
   venue, or a price still needs to add.
