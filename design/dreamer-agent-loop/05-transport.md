---
type: design
title: "Transport — A Second Workload Class, Given No Transport of Its Own"
description: "The Dreamer inherits the tag path's model, ceilings, reasoning effort and client wholesale. A chat reply and a research loop are opposite workloads; sharing one transport is why every budget number is nonsense and why the wire-level JSON lever the older path already has was never carried over."
tags: [nora, dreamer, transport, budgets, model, tokens]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
---

# Transport — A Second Workload Class, Given No Transport of Its Own

## 1. The error, stated once

The Dreamer reads `tier = cfg.llm` — the **tag path's** tier. From it come the
model, the per-call token ceiling, the temperature, the reasoning effort and the
client. There is no `[dreamer.llm]`. The only Dreamer-owned numbers are the four
per-adaptor loop budgets, and those are expressed *against* an inherited ceiling
nobody chose for them.

That is not a missing config section. It is a **second workload class that was
never recognised as one.**

| | tag reply | research loop |
|---|---|---|
| shape | one-shot; a terminal verb ends it | multi-turn; ends when the question is answered |
| latency | a human is waiting | nobody is waiting |
| context | small, fixed | accumulates across turns |
| reasoning | costs the user seconds | *is* the product |
| output | prose for a person | a schema for a machine |
| failure | visible in one turn | silent for weeks |

Every column differs. They share one transport.

## 2. What the sharing actually caused

Each of these was found independently; each traces to the same root.

**The per-call ceiling is a chat message's.** A research turn's deliberation is
much larger than a reply's, and it competes with the answer for the same
allowance.

**Reasoning effort is inherited too.** The tag tier sets it for a turn where
deliberation costs a person seconds of waiting. For a background loop the
tradeoff is the opposite, and nobody made it.

**The loop budgets are arithmetic against a number nobody chose.** Four of the
five sit at exactly the minimum ratio the turn-budget rule tolerates, while the
tag path runs at three times that. Four declare turn counts they cannot afford.
Details in §3.

**The wire-level JSON lever was lost going forward.** The *older* one-shot
observer path sets a structured-output format on the request, and its own
comment records that prompt-only enforcement had already been proven
insufficient once. The Dreamer's provider does not set it. The newer, more
sophisticated path lost a guarantee the path it was meant to replace already
had — and its signature failure is unparseable output.

**Nothing verified that the request's tool behaviour holds for this case.** The
provider's own documentation argues at length that a particular tool-choice
setting is load-bearing *for a turn that must end in a reply*. The Dreamer's
loop is the case that argument explicitly does not reach, and the setting it
uses instead was reasoned about but never measured. See
[16-open-items.md](16-open-items.md) §1 — if the route does not sustain it, the
loop this bundle designs cannot run as designed, and that outranks everything
else here.

## 3. The budget arithmetic, and what it actually shows

Against the inherited per-call ceiling:

| budget | ratio to ceiling | reachable calls | declared turns |
|---|---|---|---|
| room language | 2.0× | 2 | 6 |
| room exchange | 2.0× | 2 | 6 |
| fact extraction | 2.0× | 2 | 2 |
| **fact adjudication** | **4.0×** | **4** | 10 |
| unconfigured default | 2.0× | 2 | 6 |

**Four of five sit at the rule's bare minimum; adjudication is at twice that.**
Four declare turn counts they cannot afford. The tag path runs at six times the
ceiling.

And the rule that would have caught this is **enforced by a test that reads only
the tag path's table.** Nothing checks the relation for the Dreamer, so its
budgets can be internally incoherent and stay green.

**But the arithmetic is not the finding.** The observed generation exceeded
*both* Nora-side caps — the per-call ceiling and the loop budget. So it is not
that the loop budget became the binding cap; **no number Nora configures reached
the wire at all.** The only cap that bound was the provider's own default.

That reframes the whole section: sizing these numbers correctly would have
changed nothing, because they were not in force. Which is exactly why the
measurement in [16-open-items.md](16-open-items.md) §1 comes before any sizing.

## 4. The design

**A transport is a property of a workload class, not of a deployment.** The
Dreamer gets its own, and it is complete rather than a partial override — model,
per-call ceiling, reasoning effort, structured-output format, and the tool
behaviour it needs, each stated for research rather than inherited from chat.

Four properties it must hold:

1. **Nothing inherited silently.** A Dreamer-side value either is stated or
   falls back *explicitly and visibly*. The current failure was invisible
   precisely because inheritance looks like a decision.
2. **The budget relation is checked for the Dreamer too**, not only for the tag
   path. A turn count that cannot be reached is dead config, and this is the
   rule the repo already has and did not apply here.
3. **The ceiling in force is verified against the wire, not assumed from the
   config.** This is the one property that cannot be satisfied by writing code
   — see §3. It is a measurement, and it gates the rest.
4. **A structured-output format on the request**, because the older path has it,
   because its absence is documented as already-proven-insufficient, and because
   the failure mode it prevents is the one in production.

**What this does not decide:** which model. That question is closed by operator
decision and carries a falsifiability commitment that has never been discharged
because no research loop has ever completed — see
[16-open-items.md](16-open-items.md) §9. Giving the Dreamer its own transport
makes that question *answerable*; it does not answer it.

## 5. Why now rather than after the migration

Two more tiers are due to move onto this runtime. Each will inherit the same
transport, and each will then have budgets expressed against a ceiling chosen
for a chat reply.

The cost of separating the transport is one config section and one conversion
point. The cost of not doing it is that the error propagates to every adaptor
built afterwards, and becomes indistinguishable from intent — which is precisely
what happened to the tool subsets
([04-designed-vs-shipped.md](04-designed-vs-shipped.md)).
