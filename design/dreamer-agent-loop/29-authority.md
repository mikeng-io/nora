---
type: contract
title: "Authority — Which Tier Wins, and Why It Is Never the Tier That Decides"
description: "Honcho holds what someone is LIKE; the knowledge tier holds what is TRUE — the discriminator is epistemics, not subject. When two tiers disagree, precedence follows the WARRANT, not the tier, and Standing never wins anything."
tags: [nora, honcho, knowledge-tier, authority, conflict, warrant, precedence]
timestamp: '2026-08-24'
edges:
  - {dst: 19-between-tiers, rel: extends, provenance: asserted}
  - {dst: 21-substrate-contract, rel: depends_on, provenance: asserted}
  - {dst: 26-standing, rel: depends_on, provenance: asserted}
---

# Authority

## 1. The question this answers

[19-between-tiers.md](19-between-tiers.md) §4 designs **detection** — two tiers
can be noticed to disagree once they name things by the same handle — and then
explicitly declines resolution: *"Not a precedence order… a background pass
noticing that a lexeme and a fact disagree should write that down."*

**Write it down for whom, adjudicated how, and by what rule?** That was left
open. This node closes it, and closes the Honcho boundary alongside it, because
they turn out to be the same rule applied twice.

## 2. Honcho versus the knowledge tier: the line is epistemic, not topical

The tempting boundary is *by subject* — Honcho gets people, the knowledge tier
gets things. **That boundary is wrong, and [21](21-substrate-contract.md) §2
already broke it**: a person **is** an entity, because a note anchors on one and
an edge can point at one.

The real line:

| | Honcho | knowledge tier |
|---|---|---|
| holds | what someone is **like** | what is **true** |
| epistemics | **inferred**, and re-inferred forever | **asserted**, with a warrant |
| lifetime | may change its mind at any time | supersedes, and records that it did |
| availability | **best-effort — may be absent** | authoritative, always there |
| provenance | a deriver's conclusion | a source, premises, an observation, or a person |

**Both may be about Grant.** *"Grant replies late"* is Honcho's — a
disposition, re-inferred from behaviour, true until it isn't. *"Grant's
wedding is on the 9th"* is the knowledge tier's — asserted, warranted, and
wrong only if the world changed or the source was wrong.

ADR-0001 states the same split in one line — **the record versus the
interpretation** — and its operative test is the one to keep: *could she have
read this in the buffer?* If yes, Honcho is negative value.

**The authority that follows:**

> **A tier's authority comes from its warrant, not from its name.** Honcho has
> no warrant — by design; it is an inference engine. So on anything the
> knowledge tier can warrant, the knowledge tier is authoritative. On
> disposition, the knowledge tier **has no warrant to offer**, which is why it
> is excluded rather than outranked.

That is why *facts beat memory* is false as a general rule and true wherever
both actually speak: they rarely both speak, and where they do, one of them is
carrying evidence.

### 2.1 Nothing writes structured claims into Honcho

Worth stating because it removes a whole class of imagined conflict. Honcho is
written **only** by `feed` — fire-and-forget, from ingest and the post-reply
path — and what it receives is **messages**, not conclusions. No tier writes a
fact, a note or a lexeme into it.

So the tiers never *contradict* Honcho by writing to it. They can only disagree
with what it independently inferred, which is §3's case.

## 3. When two tiers disagree, precedence follows the warrant

Not a tier ranking. A **warrant** ranking — and the machinery already exists,
because `warrant_kind` is a column ([21](21-substrate-contract.md) §1).

| rank | warrant | why it sits here |
|---|---|---|
| 1 | **`stated`** | a person decided it. Nothing derived outranks somebody's decision |
| 2 | **`source`** | a page said it, and the page is recorded |
| 3 | **`premises`** | deduced from standing facts — as good as its premises, which are themselves ranked |
| 4 | **`observation`** | the room repeated it; true of the room, not necessarily of the world |
| 5 | **inference** (Honcho) | no warrant, by design, and re-inferable at any time |

**A lower rank never silently overwrites a higher one.** It becomes a
**candidate**, which is exactly the mechanism the knowledge tier already runs —
so a detected disagreement enters adjudication rather than needing a new
subsystem. That is the answer to *"written down for whom"*: **for the
adjudicator**, as a candidate carrying both sides.

**And Honcho is never corrected.** It re-infers continuously, so writing a
correction into it is writing into a process built to change its mind
([20](20-knowledge-tier.md)'s wedding-venue argument). Where the knowledge tier
disagrees with an inference, the fact stands and the inference is simply not
consulted for that question.

## 4. The other three tiers do not conflict — they mean different things

The cross-tier cases that look like conflicts mostly are not, and saying which
is which prevents inventing machinery for a problem that does not exist.

**Knowledge versus language.** A lexeme is about **what a term means**; a fact is
about **what is so**. If a gloss and a fact appear to disagree about a subject,
that is an **entity-resolution failure**, not a truth conflict — two names for
one thing that never got merged. The fix is [21](21-substrate-contract.md) §5,
not a precedence rule. *(The live example: two lexeme rows for `Big Walk` and
`big walk`, disagreeing with each other before any fact exists.)*

**Knowledge versus notes — and this one is a mechanism, not a conflict.** A note
is a **question with a closing condition**; a fact is an **answer**. When a fact
satisfies a note's closing condition, the note should **close**.

> A fact landing is an event the note tier should react to. That is the
> knowledge tier *resolving* a note, not competing with it.

This is the only genuinely **new** cross-tier behaviour this node asks for, and
it belongs to the curator ([27](27-note-dreamer.md) §2), which already acts
across notes on the Dreamer's clock.

**Standing versus everything: Standing never wins.** It is *lossy by design*,
rewritten every turn, and explicitly *"a position, not a record"*
([26](26-standing.md) §8). It is downstream of every other tier and authoritative
over none. A disagreement between Standing and anything else is **Standing being
stale**, which the next fold fixes by construction.

## 5. What Nora does in the moment

Precedence is a **write-time and adjudication-time** rule. On the tag path she is
not running a conflict resolver, and should not be:

- **She is shown both**, with their warrants. A fact block that renders a claim
  without its warrant is asking her to arbitrate blind.
- **Marked disagreement beats silence, and silence beats a wrong answer.** A
  detected conflict renders *as* a conflict — the thing the two lexeme rows
  conspicuously failed to do ([01](01-live-state.md)).
- **She never writes the resolution.** Propose/dispose is unchanged: she may say
  *"I've got two different answers on this"*, which is honest and often the right
  reply. Deciding between them is the adjudicator's, on the Dreamer's clock,
  with tools.

## 6. What this owes its instrument

| reading | why |
|---|---|
| **disagreements detected, by tier pair** | §3's whole premise is that overlap is rare. If this counter is large, the routing in [19](19-between-tiers.md) §1 is not holding and the fix is routing, not precedence |
| **candidates raised from a disagreement, and their adjudicated outcome** | a disagreement that never reaches a verdict is a flag nobody reads |
| **notes closed by a fact** | §4's new behaviour. Never firing means the closing conditions are not written in terms anything can satisfy |
| **Honcho consulted where the knowledge tier could have answered** | ADR-0001's operative test, made countable — *could she have read this in the buffer?* |

## 7. What this node does NOT introduce

No new store, no new verb on the tag path, no precedence field on any row, and
no resolver service. **Every part of §3 runs on `warrant_kind`, the candidate
table and the adjudicator that already exist** — which is the test of whether a
boundary was real or invented.

The one addition is §4's fact-closes-a-note, and it is a curator behaviour on an
adaptor that does not exist yet, so it costs nothing today.

## Status / Checkpoint

2026-08-24 — written to close the operator's last open question: the Honcho /
knowledge-tier boundary, and cross-tier conflict resolution. Both resolve to one
rule — **authority follows the warrant, not the tier** — which is why they are
one node. It closes [19](19-between-tiers.md) §4's *"write that down"* by naming
the reader (the adjudicator) and the rule (warrant rank).
