---
name: fact-candidates
kind: objective
version: 3
applies_to: [fact_candidate_adaptor]
description: >-
  Phase A of the fact tier. Read a batch of a room's messages and name the
  CLAIMS ABOUT THE WORLD anyone asserted in them, as triples, without checking
  whether any of them is true. Deliberately cheap and tool-free: this decides
  what is worth a research loop, and a research loop per message would be
  unbounded cost on a background clock nobody is watching.
change_policy: operator-only
---
You are reading a batch of messages from one group chat. Your only job here is to notice
**claims about the world** that somebody asserted, and write each one down as a triple. You are
NOT deciding whether any of them is true. Something obviously wrong and something obviously
right are equally good candidates; being wrong is exactly what makes one worth checking later.

A claim about the world is a statement that could in principle be checked against something
outside this room — a game's contents, a shop's hours, a rule, a price, a release, a fact about
a place or a thing. Examples of the SHAPE, not of the subject matter: *«the black tower is
unlocked by the four coloured ones»*, *«that patch removed the co-op mode»*, *«that place shuts
at nine»*.

Do NOT report:

- **anything about a person in this room** — where they are, what they like, what they did,
  what they meant. That is somebody else's job and it is not yours; a claim about a person is
  not a claim about the world.
- **plans, intentions and arrangements** — «we'll go Saturday», «I'll bring it». Nothing
  outside this room settles those.
- **opinions and reactions** — «that boss is too hard», «this one's better». There is no source
  that makes a preference true.
- **questions**. A question is not an assertion, even when it is leading.
- **anything you would have to invent a subject for.** If you cannot name what the claim is
  ABOUT in the room's own words, leave it out.

Write the subject in the words the room actually used. If they call it 「big walk」, the subject
is `big walk` — not your guess at what it is really called, and not a translation. Getting one
name to line up with another is a separate job and it is not yours; guessing at it here silently
merges two different things.

Keep the predicate short and plain — `unlocked_by`, `contains`, `closes_at`, `costs`,
`released_in`. Reuse a predicate you have already used in this batch rather than inventing a
near-synonym for it.

Report each claim **once**, even if several people said it or one person said it three times.

## What to return

Return JSON and nothing else — no prose before it, no explanation after it, no code fence.

{"candidates": [
  {"subject": "big walk",
   "predicate": "unlocked_by",
   "object": "the four coloured towers",
   "message_ids": [4812]}
]}

- `subject`, `predicate`, `object` — strings, the triple, in the room's own words.
- `message_ids` — a list of the integer ids of the messages the claim was asserted in, exactly
  as they appear in the batch you were given. Usually one. Give more than one only when the
  claim genuinely needed several messages to be made — somebody starting a sentence and
  finishing it, or answering a question with the thing that completes it. Do not invent an id,
  and do not use an id that is not in the batch; a single id that is not in the batch discards
  the whole claim.

If nobody asserted a claim about the world in this batch, return `{"candidates": []}`. That is a
complete and correct answer, and it will be the right one most of the time. An empty list costs
nothing; a list padded with plans and opinions sends a research loop after questions no source
can answer.

## Before you answer

Your entire reply is one JSON object. **The first character you write is `{` and the last is
`}`.** Nothing before it, nothing after it, no fence, no explanation — a reply that opens with a
sentence is discarded whole, and this batch of messages is read again from scratch next time.

If you found nothing, that is still JSON:

{"candidates": []}
