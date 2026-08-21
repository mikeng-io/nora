---
type: analysis
title: "Open Items"
description: "What this bundle does not answer, ranked by what blocks what. The gateway probe blocks all budget sizing; the disclosure question blocks workspace room access."
tags: [nora, dreamer, open-questions, backlog]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
---

# Open Items

## Blocking

**1. Does the gateway honour the per-call token ceiling?**

The observed generation was 4,096 output tokens. The configured per-call
ceiling is 2,000, the loop's output budget is 4,000, and the trace carries
exactly one generation. 4,096 is neither number, and it is a very common
provider default.

Either the gateway is ignoring the ceiling and applying its own, or the trace
measures something other than one completion. It matters directly: if the
ceiling is ignored, the loop's output budget is the **only** cap in play and a
single call spends all of it — which is exactly the failure observed.

**A direct probe against the gateway settles it.** Nothing in
[10-orchestration.md](10-orchestration.md) should be sized until it is run.
This is the cheapest open item and the one most other numbers depend on.

**2. R-49's second condition on the fact path.**

The workspace bounds which rooms may be paired; the persons list decides who
may use the pair. The fact path implements the first and has no mechanism for
the second. Until it does, Path B in [07-room-access.md](07-room-access.md) is
blocked and the content-class change in [08-disclosure.md](08-disclosure.md) is
unsanctioned.

This is a policy question, not an implementation one. It may be answerable by
asserting a property of the deployment (rooms in one workspace share a person
list) and **checking it at startup** — which would turn a borrowed argument
into a guard. That is a proposal, not a decision.

**3. A global concurrency bound.**

None exists. Ten concurrent units today, eighteen after the migration, each
potentially issuing several network calls, all on one client. The bound should
be chosen before the migration lands, not discovered after.

## Open, not blocking

**4. Cadence per adaptor.** Extraction is watermark-driven and suits a clock.
Adjudication is a work queue wearing a clock's clothing. Synthesis, if it
lands, wants to run rarely. One tick currently serves all three
([10-orchestration.md](10-orchestration.md) §8).

**5. Entity resolution.** The shared dependency of both tiers
([09-self-correction.md](09-self-correction.md) §4). Its absence bounds how
useful any search tool can be, and the fact tier is on course to reproduce the
language tier's fragmentation. Mechanism still open; this bundle records only
that it is shared, not fact-tier-local.

**6. Does the instruction digest need to cover the tool subset?**

Two runs over identical instruction text — one with a fetch tool, one without —
can write different rows. The provenance column cannot distinguish them, and
this work **changes tool subsets**, so it walks straight into the gap. The
substrate spec left it open and noted that if the answer is "more", it is a
second column rather than a different one — so today's stamp stays correct
either way. Still owed an answer.

**7. Language-tier supersession.** Deliberately not decided here
([09-self-correction.md](09-self-correction.md) §1). Any proposal must say what
happens to the overwrite bug the compound key was introduced to fix.

**8. What `<facts>` renders.** Currently the workspace's most recent standing
facts with no subject filter. Whether it becomes query-relevant, becomes a tool
Nora calls, or both, is a tag-path cost question
([06-tool-surface.md](06-tool-surface.md) §5).

**9. Was the model ever the problem?**

The keystone made a falsifiability commitment: the Dreamer runs Nora's own
model *because* the claim is that it does better by being **differently
situated** — no social pressure, a loop, a fetch — not by being smarter. It
committed to verifying that under a research loop, against the motivating
corpus.

**That verification has never run**, because no research loop has ever
completed. Until one does, the design's central claim is untested. The
keystone's own condition still stands: if a loop still cannot tell a source
that contradicts from one that merely does not cover, that reopens the
**design**, not the model.

## Recorded, owned elsewhere

- **The fact tier's store.** Owned by its own workstream.
- **Standing and the debrief.** Tag-path work, separately owned. The
  input-versus-tool rule ([05-input-or-tool.md](05-input-or-tool.md)) applies
  to it; that is the whole overlap. Its read is one known row, so it is an
  input — unless it ever asks "have I concluded something like this before,
  elsewhere", which is discovery and needs the seam.
- **Game-state tools.** Excluded fail-closed because tools arriving over the
  external protocol carry no read/act classification. The cost is real and
  named: the fact adaptor can research the open web and cannot read the group's
  own game state, which is the one thing that would settle the motivating
  claim. Reopen by giving those tools a classification surface.
- **Transitive supersession, premise cardinality, two writerless warrant
  kinds.** [09-self-correction.md](09-self-correction.md) §6.
