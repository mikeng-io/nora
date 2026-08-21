---
type: analysis
title: "Designed vs Shipped — The Narrowing Nobody Decided"
description: "The Dreamer's tool set went from eleven to four with no recorded decision. It is the composition of two specs that disagree, a scope choice made for unrelated reasons, and a ceiling invented during implementation."
tags: [nora, dreamer, tools, corpus, archaeology, governance]
timestamp: '2026-08-22'
edges:
  - {dst: 03-no-agent-loop, rel: motivated_by, provenance: asserted}
---

# Designed vs Shipped — The Narrowing Nobody Decided

## 1. What was specified

The keystone records the operator's framing as *"the whole design"*:

> *"Dreamer should technically have the same toolset as Nora, because it
> requires something like a deep research agent."*

> *"it probably only needs to look up the **previous two calls, the image, the
> previous transcript, and the user profile** to cross-validate and create a
> synthesis…"*

Those four items map exactly onto `recall_lookups`, `media_fetch`,
`load_transcript`, and `about`/`ask_memory`/`lookup_person`.

**Zero of the four are wired to any shipped adaptor.**

The keystone's §4 then lists the runtime's ceiling — twelve names — and the
fact-tier spec's §4 lists the adjudicator's own subset at eleven. Shipped:
four, all web or utility. **Seven of eleven dropped**, and every dropped one is
a read of ourselves rather than of the world.

## 2. How it happened — three things that never met

There is no decision anywhere to remove them. The narrowing is emergent:

**(a) Two live specs give the same adaptor different subsets.** The fact-tier
spec grants eleven. The reflection spec's own table grants two —
`web_search`/`web_fetch`. The fact-tier spec claims ownership of *"its
adaptor's values"*, which makes it authoritative by the corpus's own division
of labour. **Shipped follows the reflection spec.** The keystone can be read as
endorsing either: it says the reflection spec's row *becomes* the adaptor's
tool-subset field, which promotes the two-tool row into the contract rather
than superseding it.

**(b) The workspace key was chosen for a disclosure reason, in a section that
never mentions tools.** The fact tier is workspace-keyed because a fact crosses
rooms within a workspace by design. Nothing in that argument is about
capability.

**(c) `WORKSPACE_SAFE_TOOLS` applies *because of* `key_kind`.** It was invented
during implementation, is argued only in the decision index and a docstring,
and **appears in no design document at all.**

So the fact-tier spec's §4 and §1.1 sit in one file and contradict each other
through a mechanism invented a day later.

## 3. A rule quoted to justify something it does not say

Fact-tier §4.2 is three sentences and the shipped code cites it as its warrant:

> *"This answers the handoff's §7.6 with a no. The subjects' live facts arrive
> as a bounded, code-selected **input**, not as a verb the model may call.
> Selection stays where it can be capped and counted, and the tool subset stays
> read-only about the **world** rather than read-only about **ourselves**."*

The question it answers is narrow: *should there be a `fact_read` tool?*

Its **rationale** is general, and applied consistently would strip
`search_memory`, `about`, `ask_memory`, `lookup_person`, `load_transcript`,
`recall_lookups` and `media_fetch` — every "about ourselves" tool — from the
list ten lines above it in the same document.

A rule scoped to one tool is being used to justify the absence of a different
one. [05-input-or-tool.md](05-input-or-tool.md) replaces it with a rule that
survives generalisation.

## 4. The ceiling is the hand-list the keystone forbids

Keystone §4 argues at length that the Dreamer's set must be **derived from a
field on the tool** and never hand-listed, citing the repo's own pre-DEC-29
stale-catalogue failure — and then demonstrates the point by omitting four act
verbs from its own illustrative list.

`WORKSPACE_SAFE_TOOLS` is a hand-list. It is mitigated — a test forces every
read-only tool into one of two buckets by name, so a tool added tomorrow turns
it red rather than silently joining or silently missing. That mitigation is
real and should survive. But it exists only in code and a test, in no spec, and
the classification it forces was a judgement call made once under time
pressure.

**It has already produced one misclassification.** Two note verbs are filed as
room-scoped with an inline note saying *"nothing wires their collaborators
yet"* — and that reason is factually false; both collaborators are wired. The
real blocker is the resolution key, not the wiring. A careful reader who checks
the stated reason, finds it wrong, and reclassifies would hand a background
loop a tool that writes ([08-disclosure.md](08-disclosure.md)).

Worse, the ceiling's own rationale — *"a workspace has no single room to bind
them to"* — is disproved by an existing counter-example. The note read verbs
take a workspace **directly** and resolve room→workspace through an injected
callable. A workspace-keyed read tool needing no room already exists in this
repo.

## 5. Three live specs still say "Design only. No code."

| spec | `status:` | says of itself | reality |
|---|---|---|---|
| dreamer-as-an-agent (keystone) | `proposed` | *"Design only. No code. No decision-index entry — a candidate."* | merged, deployed, was enabled |
| fact-tier | `proposed` | *"Design only. No code."* | merged, deployed, was enabled |
| prompt-substrate | `proposed` | *"Design only. No code."* | merged, deployed |

CLAUDE.md's reading order says specs are for *why*, not current state. But an
implementer opening fact-tier §4 sees an **unimplemented specification** rather
than one the shipped code deliberately departed from. There is no marker
distinguishing "not built yet" from "built differently, on purpose."

That is the mechanism by which seven tools went missing without anyone
noticing, and it is why this bundle is a **revision layer** that supersedes
framing explicitly rather than another document beside them.

## 6. One thing that is faithful, and should be said plainly

`FactCandidateAdaptor` holding no tools is **not** a regression. The fact-tier
spec says *"Phase A is one cheap call per tick, no tools, bounded output"*, the
decision index restates it, and the class docstring restates the cost argument
again.

It is a designed decision, argued on cost, that this bundle proposes to reopen
on evidence — not an oversight to be corrected. The evidence is that it is not
cheap: it spends its entire output budget producing nothing parseable, every
tick, and has never once succeeded. See
[05-input-or-tool.md](05-input-or-tool.md) for the replacement argument and
[12-migration-and-governance.md](12-migration-and-governance.md) for the test
that pins it and must be replaced rather than deleted.
