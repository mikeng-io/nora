---
name: fact-dispute
kind: objective
version: 1
applies_to: [recogniser]
description: >-
  Step 3 of the correction loop. Somebody has just said something in a room
  where Nora is holding claims about the things they mentioned. Decide whether
  they are CONTRADICTING one of those claims, or merely talking. Almost always
  merely talking — that is the expected answer and it costs nothing.
change_policy: operator-only
---
Below is one message somebody just sent, and the things Nora currently believes
about what they mentioned. Your only job is to say whether the message
**contradicts** any of them.

## Contradicting means asserting something different

They are contradicting a claim when they say the thing is otherwise — a
different answer to the same question. *"no, big walk is a video game"* against
*«big walk means a walking activity»* is a contradiction: same subject, two
incompatible answers.

## Say NO when

- they are **talking about** the thing without disagreeing — asking about it,
  planning around it, joking, agreeing;
- they are **refusing or declining** something. *"no I can't make the big walk"*
  says nothing about what big walk is. This is the most common way a message
  looks like a contradiction and is not one, and getting it wrong is the whole
  reason you are being asked rather than a word list;
- they are **surprised or disbelieving** without saying otherwise — 「唔係呀嘛」,
  *"no way"*, *"seriously?"*;
- they are contradicting something that is **not in the list** — somebody else,
  an earlier message, the weather;
- **you are not sure.**

That last one is not a hedge. Saying no leaves a claim standing that somebody
might have been correcting, and the next person to mention it corrects it again.
Saying yes marks a claim as disputed in front of everyone and sends it for
research. When it is not obvious, it is no.

## What you are given

The message, and under it the claims Nora holds about things it mentioned, each
with a number. The claims were selected because the message MENTIONS them;
being mentioned is why the question is being asked and is not evidence of an
answer.

## What to return

Return JSON and nothing else — no prose before it, no explanation after it, no
code fence.

{"disputes": [
  {"claim": "1", "reason": "says it is a video game, not a walk"}
]}

- `claim` — the number of the claim being contradicted, as a string, exactly as
  it appears in square brackets. Never the claim's text.
- `reason` — one short line saying what they asserted instead. It is read by
  whoever researches this later, and by Nora when she says she has been told
  this is wrong.

Use only the numbers you were shown. Anything else is discarded.

If the message contradicts nothing, that is the complete and expected answer:

{"disputes": []}

## Before you answer

Your entire reply is one JSON object. **The first character you write is `{` and
the last is `}`.** Nothing before it, nothing after it, no fence, no
explanation — a reply that opens with a sentence is discarded whole, and the
message is read again from scratch next time.
