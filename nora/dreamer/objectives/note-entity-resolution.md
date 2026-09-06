---
name: note-entity-resolution
kind: objective
version: 1
applies_to: [note_entity_resolution_adaptor]
description: >-
  The note tier's judgement pass over the shared entity table (issue #100).
  Decide whether an anchor-entity string nothing recognised is another way of
  writing something already known. The cheap exact pass has already run and
  handled everything it could; what reaches here is the genuine near-miss.
  Answering "no" is a full and correct answer, and the expected one.
change_policy: operator-only
attribution: >-
  The reasoning and the four worked examples below are adapted from
  graphiti-core's `prompts/dedupe_nodes.py` (Zep Software, Apache-2.0). See
  THIRD_PARTY_NOTICES.md for what was taken, what was refused, and why.
---
Two names refer to the same thing, or they do not. Your only job is to say which,
for each name you are given.

**Same thing means the same real-world object or concept** — not related, not
similar, not the same kind of thing. A name that merely *resembles* one you were
shown is a different thing with a similar name, and those are common.

## Say NO when

- they are related but distinct — a person and the company they run, a game and
  its sequel, a place and the district around it;
- they have similar names, or the same name, and refer to separate things;
- **you are not sure.**

That last one is not a hedge, it is the rule. Saying no leaves two entries that
might have been one, and somebody can join them later. Saying yes merges two
things that were not one, and everything said about either is now said about a
thing that does not exist — there is no way back from that. When the answer is
not obvious, it is no.

## Worked examples

```
NAME: "Sam"
KNOWN: [a] Sam — enjoys hiking and photography
→ [a].  The same person, mentioned again.

NAME: "NYC"
KNOWN: [a] New York City   [b] New York Knicks
→ [a].  An abbreviation of the same place. Not [b] — same city in the name,
        different thing entirely.

NAME: "Java" (the programming language)
KNOWN: [a] Java — an island in Indonesia
→ no match.  Same name, two unrelated real-world things.

NAME: "Marco's car"
KNOWN: [a] Marco's vehicle — Marco drives a red sedan
→ [a].  A synonym, and the same possessor.
```

## What you are given

Names to resolve, each with a number. Under each, the things already known that
your name might be another way of writing, each with a letter. The known things
were selected because they LOOK similar; looking similar is why the question is
being asked, and is not evidence of an answer.

A name with no known things under it has nothing to match against. Say no.

## What to return

Return JSON and nothing else — no prose before it, no explanation after it, no
code fence. One entry per NAME you were given, in any order.

{"resolutions": [
  {"name": "1", "entity": "a"},
  {"name": "2", "entity": null}
]}

- `name` — the number of the name you are deciding, as a string, exactly as it
  appears in square brackets. Never the name itself.
- `entity` — the letter of the known thing it is another way of writing, as a
  string, exactly as it appears in square brackets. **`null` when there is no
  match or you are not sure**, which will be most of the time.

Use only the numbers and letters you were shown. Anything else is discarded.

If none of the names matches anything, that is still a complete answer:

{"resolutions": [{"name": "1", "entity": null}, {"name": "2", "entity": null}]}

## Before you answer

Your entire reply is one JSON object. **The first character you write is `{` and
the last is `}`.** Nothing before it, nothing after it, no fence, no
explanation — a reply that opens with a sentence is discarded whole, and these
names are read again from scratch next time.
