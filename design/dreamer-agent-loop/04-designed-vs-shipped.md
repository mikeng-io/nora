---
type: analysis
title: "Designed vs Shipped — The Narrowing Nobody Decided"
description: "The Dreamer's tool set went from eleven to four with no recorded decision. It is the composition of two specs that disagree, a scope choice made for unrelated reasons, and a ceiling invented during implementation."
tags: [nora, dreamer, tools, corpus, archaeology, governance]
timestamp: '2026-08-22'
edges:
  - {dst: 03-no-agent-loop, rel: motivated_by, provenance: asserted}
---

> **PARTLY DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") and the language tier have both been deprecated and their code deleted.** Every claim below about either tier is retained as a record of the reasoning; **none of it describes code that exists.** The note tier, Standing, the relevance gate and the Dreamer runtime itself are unaffected; **recognition** ([31](31-recognition.md)) went with the two tiers whose rows it marked.
> Their tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.


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
one. [08-state.md](08-state.md) replaces it with a rule that
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
loop a tool that writes ([11-disclosure.md](11-disclosure.md)).

The ceiling's own rationale — *"a workspace has no single room to bind them
to"* — is true of every read tool **as currently built**, and is not a general
claim. The note read verbs are sometimes offered as the counter-example; they
are not one. They take a title or a query and **derive** the workspace from
`(platform, conversation_id)`, so they need a room exactly as the others do. It
is the *store port* beneath them that takes a workspace directly. The
distinction matters because it is the difference between "this already exists"
and "this needs building" — see [08-state.md](08-state.md) §10.

## 5. Three live specs still say "Design only. No code."

| spec | `status:` | says of itself | reality |
|---|---|---|---|
| dreamer-as-an-agent (keystone) | `proposed` | *"Design only. No code. No decision-index entry — a candidate."* | merged, deployed, was enabled |
| fact-tier | `proposed` | *"Design only. No code."* | merged, deployed, was enabled |
| prompt-substrate | `proposed` | *"Design only. No code."* | merged, deployed |

CLAUDE.md's reading order says specs are for *why*, not current state. But an
implementer opening fact-tier §4 sees an **unimplemented specification** rather
than one the shipped code deliberately departed from.

**The convention for marking this exists, and is applied inconsistently inside
single files.** The corpus does use in-file `SUPERSEDED` blockquotes, `CLOSED`
headings, strikethroughs, and in one case a whole "corrections found in
implementation" section. The fact-tier spec carries both at once: "Design only.
No code." and "Both are now **on master**" sit **five lines apart**. So the
failure is not an absent convention — it is one applied to some sections and not
their neighbours, which is worse, because its presence elsewhere in the file
implies the unmarked parts were checked.

That is the mechanism by which seven tools went missing without anyone
noticing, and it is why this bundle is a **revision layer** that supersedes
framing explicitly rather than another document beside them.

## 6. The pattern, and four more instances

The mechanism above is not a fact-tier accident. **A spec owns a decision, a
handoff or a docstring restates it, and nothing detects the drift.** Four
further instances, three found by investigations that were not looking for this:

1. **The debrief's outputs.** Its own design defines exactly three. The claim
   that notes get a fourth existed *only* in a handoff document, never in the
   design that owns the decision. Caught by the session building it, who added
   the output rather than let two documents silently disagree.
   *(Credit: the Standing/debrief session.)*
2. **The fact renderer's docstring** asserts that the store read is "not yet
   built" and that "nothing writes this tier yet, so this module always renders
   nothing". Both false. **The same stale claim is duplicated in the config
   module, where it is load-bearing for the argument that cut a startup check** —
   a decision now resting on a stale restatement.
3. **A spec says joining the shared sink contract suite is "one entry".** The
   suite's own docstring records that same assumption, measured on the previous
   attempt, as **four passed and nine failed**. The spec was never annotated.
4. **The three transfer documents**, at a larger grain — each written because
   the last did not survive a session boundary, each now partly false. See
   [15-roadmap.md](15-roadmap.md) §6.
5. **An accepted spec asserts a defect that has been fixed.** It records that
   the doctrine loader accepts authority cycles. The loader implements
   acyclicity, with named tests for the exact shapes, and one test's docstring
   records the history. Anyone reading that spec today would reopen closed work
   — the most expensive form of this failure, in a document whose whole subject
   is claims drifting from what they describe.
6. **Three more, all in files this bundle otherwise audits**: a retired-then-
   reinstated web tool still described as retired in config; a comment saying
   the adaptors are not built, twenty lines above the code that builds both; and
   the fact tier documented as shipping dormant directly above the line
   enabling it.

The common shape is that the **restatement is the artefact people read**, and
nothing binds it to the decision it restates. That is why this bundle is a
revision layer with an explicit supersedes field, and why
[15-roadmap.md](15-roadmap.md) carries a staleness register instead of claiming
to be permanently true.


## 7. One thing that is faithful, and should be said plainly

`FactCandidateAdaptor` holding no tools is **not** a regression. The fact-tier
spec says *"Phase A is one cheap call per tick, no tools, bounded output"*, the
decision index restates it, and the class docstring restates the cost argument
again.

It is a designed decision, argued on cost, that this bundle proposes to reopen
on evidence — not an oversight to be corrected. The evidence is that it is not
cheap: it spends its entire output budget producing nothing parseable, every
tick, and has never once succeeded. See
[08-state.md](08-state.md) for the replacement argument and
[14-migration-and-governance.md](14-migration-and-governance.md) for the test
that pins it and must be replaced rather than deleted.

## Status / Checkpoint

2026-08-24 — The drift catalogue. Gained two instances since: this bundle's own stale scope line about Standing, and the language tier being cited in eleven nodes while owned by none ([28](28-language-tier.md) §1).
