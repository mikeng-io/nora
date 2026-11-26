---
type: design
title: "Capability Drop — the two behavioural rules move to conduct"
description: "Closed-world ('this list is the whole of what you can do') and the sourcing nudge ('own sources before the open web') are rules, not catalogue. They move to conduct.md, phrased by category so no tool name appears."
tags: [nora, conduct, doctrine, closed-world, sourcing]
timestamp: '2026-08-30'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# The two behavioural rules move to conduct

## 1. What the block carried that a schema cannot

Two pieces of the capability block are not tool descriptions at all — they are
cross-tool rules with no schema slot:

1. **The closed-world clause (DEC-90)** — "That list is the WHOLE of what you
   can do — there is nothing else you can go and check, and no request you can
   put in on somebody's behalf. Never offer, promise or volunteer an action
   that is not one of the things above: no booking a table, no phoning anyone,
   no ordering, no buying…" It exists because a restaurant reply once ended
   with an offer to check a booking — a future act no tool could perform. A
   schema can describe what a tool does; it can never say "there is nothing
   else".

2. **The sourcing nudge (E1/DEC-77)** — "When a fact could come from more than
   one place, prefer your own: a Hong Kong thing you can confirm (the weather,
   a place, a bus, a route) and a place or person this household has named or
   saved both come before web_search, web_fetch, which reach the open web and
   are the last resort, not the first move." It exists because the model
   web-searched a mall's hours and answered "dunno" about a saved place — a
   closer source existed for both. A schema describes one tool; it cannot rank
   tools against each other.

Both are behavioural rules in every sense conduct.md already owns: judgement,
checking first, honesty. They belong there.

## 2. By-category phrasing keeps doctrine catalogue-free

conduct.md's front-matter already says *"No tool catalogue. What she can reach
for is generated from the tool registry."* The moved rules must not introduce
tool names — the moment conduct.md names `web_search`, the two-copies drift
class DEC-29 closed is back.

The sourcing nudge already contains its own by-category precedent: the
original (registry.py:506-519) deliberately describes the close tiers *by
category* ("a Hong Kong thing you can confirm", "a place or person this
household has named or saved") and names only the `web` tier — because that is
the tier the model over-reaches into. The moved version keeps that shape and
renders the web tier by category too ("the open web") so the sentence is stable
when a tool joins or leaves a tier.

## 3. Draft wording (for conduct.md)

```markdown
# What you can actually do

These are the tools you were given; their schemas are in front of you and say
what each one does and when to reach for it. That list is the WHOLE of what you
can do — there is nothing else you can go and check, and no request you can put
in on somebody's behalf. Never offer, promise or volunteer an action that is
not one of those tools: no booking a table, no phoning anyone, no ordering, no
buying, no filling anything in, and nothing you will "look into" later. If
someone wants one of those, say plainly that they have to do it themselves, and
give them what you DO have that helps.

When a fact could come from more than one place, prefer your own: a Hong Kong
thing you can confirm — the weather, a place, a bus, a route — and a place or
person this household has named or saved both come before the open web, which
is the last resort, not the first move.
```

Both sentences are in the register of the existing "You have judgement"
sections; neither names a tool. The one-verb agreement trick (web_search/web_fetch
"reaches/reach") vanishes because the category no longer has a count.

## 4. The live-only sentence

The block also rendered a sentence naming `transit`/`where_is`/`now` as
live-only ("describe this moment only"). This is genuinely per-tool routing —
it belongs in each tool's schema, and each already says it: `transit`'s
docstring has "It is LIVE ONLY", `where_is`'s has "forcing a fresh reading
first so the answer is where they ARE, not where they were", `now`'s has "in
Hong Kong time. Call this before answering anything that depends on when it
is". No merge needed; the schema already carries it.

## 5. The tail sentence — cost disclosure vanishes, failure register survives

The block's last sentence has two halves:

> "The things here that reach outside this conversation are live bus data and
> routing (a paid map service). When you cannot find something out, say so in
> one clause and move on."

- **The cost disclosure ("a paid map service") is not behavioural doctrine —
  it dies.** The turn's cost ceiling is enforced by machinery (`ReplyGuard`
  budgets, `llm.limits.turns`), not by telling the model a map API costs
  money. A model does not route better for knowing this; it is operator
  information in the wrong place (prompt instead of config), and it was
  already half-stale (the list names `transit`+`route` but not `web_fetch`,
  which also reaches out). Deleting it loses nothing load-bearing.
- **The failure register ("say so in one clause and move on") already lives
  in doctrine** — `conduct.md` Honesty ("or you do not and say so"), its
  "say it once, plainly" failure rule, and `constraints.md`'s "「我唔知」 and
  「我查唔到」 are real answers and you give them plainly" (constraints.md:79)
  together cover it. No merge needed; the register survives in two doctrine
  files.

This was the last unreviewed fragment of the block when the first adversarial
review's run budget expired; this section records the finding and its
disposition so the second review can check it in one pass.
