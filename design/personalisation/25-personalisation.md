---
type: design
title: "Personalised replies: likes in her turns, never revealed to the room"
description: "How Nora's ordinary replies use what people like: profiles first, a likes block only when a turn is about a recommendation type's domain, the no-reveal rule for group rooms, and the before/after eval that decides whether prompt context is enough or a ranked step is needed."
tags: [nora, personalisation, likes, replies, disclosure, eval]
timestamp: '2026-10-25'
edges:
  - {dst: 10-likes, rel: depends_on, provenance: asserted}
  - {dst: 20-ranker, rel: relates_to, provenance: asserted}
---

# 25 · Personalised replies

A personalised reply is one that fits the people Nora is answering. She leads
with what they like, leaves out what they will not do, and checks the one thing
she does not know. Nothing in the reply announces it. This is the job with no
visible output ([00](00-intent.md) §2).

## 1. Profiles first, nothing new

Most personalisation needs no new input. Once the profile switch is on, a turn
already carries `<profile of="<person_id>" written_by="nora">` for the people
in the room, with their facts, traits and guesses
([ADR 0029](../../docs/adr/0029-a-prompt-block-names-its-subject-and-author-by-id.md)).
Reply style is a profile trait, for example "prefers short answers" or
"writes in Cantonese". So is a standing dislike. The model uses them the way a
friend would.

Step 1 of this job is therefore **measurement, not code**: is the profile
context alone already enough (§4)?

## 2. The likes block, only when the turn is about a domain

A profile is prose-shaped and broad. When a turn is about a domain that a
recommendation type covers ([30](30-recommendation-types.md)), such as dinner,
a game or a time to meet, the turn may also carry the likes view
([10](10-likes.md)) for the people the turn is for:

```xml
<likes of="ada" written_by="nora" source="what people said in chat" domain="food">
- likes: cuisine:japanese (strong), vibe:quiet (some)
- will not: beef (stated)
- unknown: budget
</likes>
```

The ids, domain and values are synthetic.

- **Code decides when it appears.** It is added when the frame names a domain:
  a recommendation type's trigger, or a tag turn that a cheap classifier maps
  to one. The model does not fetch it.
- **Only the people the turn is for**, resolved by id. It never covers everyone
  the unit knows.
- **Labels, not numbers.** Code maps the score and its support to
  `strong` / `some` / `unknown`. A raw probability invites false precision,
  and §1 of [10](10-likes.md) says the scores are not calibrated.
- **Hard constraints are a separate line** (`will not`), taken from the
  constraint reader. They are never averaged into a like.
- The block name and its labels pass ADR 0029's blind-reader test, with both
  Nora and a sub-agent as readers, before it ships.

## 3. Never revealed to the room

Personalising for ada in a group room must not tell the room what only Nora
knows about ada.

- **The same destination filter as the ranker** ([10](10-likes.md) §7). An
  input that fails the room's gates is removed before the block is built, not
  after. A like learnt in ada's DM is not in the block for a group room unless
  its lineage allows that room.
- **Use, don't cite.** When a like did pass, Nora acts on it ("there's a quiet
  ramen place on the way") rather than stating it ("ada likes quiet places").
  She states a person's like only when that person said it in the same room,
  or asks.
- **Constraints are pooled.** For a shared meal, she can say "everyone's rules
  are covered" without listing who has which rule.

**The no-reveal probe** gives the room a turn where ada's likes came only from
a DM. It passes when ada's DM-only like changes nothing in the reply and is
named nowhere in it.

## 4. Measurement: is prompt context enough?

This is a before/after eval with the repo's eval suite (`python -m evals` on the
deploy host). It uses a corpus of turns where fitting the person matters, such
as dinner, games, plans and replies to someone with a known style. It has three
arms:

1. **No personalisation:** the profile blocks are removed.
2. **Profiles only** (§1).
3. **Profiles plus the likes block** (§2).

- **Graded:** does the reply fit the stated likes and constraints of the people
  it is for? Does it break a constraint? Does it reveal anything that failed
  the room's gates? A reveal fails the case outright, whatever the score.
- **Decides:**
  - If arm 2 beats arm 1 and arm 3 adds nothing, the likes block does not ship
    for replies.
  - If neither beats arm 1 beyond run-to-run noise (the same corpus run twice
    on one build), this job stops at §1.
- **Later, only if arm 3 is not enough:** rank her own in-turn choices in code
  with the ranker ([20](20-ranker.md)), using her candidate options as the
  candidates. That is a separate proposal with its own eval, not part of this
  step.

## 5. What this does not do

- It writes nothing. Likes are a view of profiles, and the profile writers own
  what Nora believes about a person ([10](10-likes.md) §6).
- It does not change when Nora speaks. That is the suggestions job, on the
  existing speaking path ([40](40-feedback.md)).
