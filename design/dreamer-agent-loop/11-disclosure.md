---
type: contract
title: "Disclosure — What Must Be True Before a Background Loop Reads a Room"
description: "The egress screen does not make reading safe. A room read changes the content class the fact tier persists, and R-49's second condition has no analogue on the fact path. Also: an act verb currently classified as a read."
tags: [nora, dreamer, disclosure, r-49, screening, security]
timestamp: '2026-08-22'
edges:
  - {dst: 00-overview, rel: realizes, provenance: asserted}
  - {dst: 10-room-access, rel: depends_on, provenance: asserted}
---

> **PARTLY DEPRECATED 2026-09-07 — the knowledge tier (the "fact tier") and the language tier have both been deprecated and their code deleted.** Every claim below about either tier is retained as a record of the reasoning; **none of it describes code that exists.** The note tier, Standing, the relevance gate and the Dreamer runtime itself are unaffected; **recognition** ([31](31-recognition.md)) went with the two tiers whose rows it marked.
> Their tables are **not** dropped by that change: the code goes, the schema stays, and dropping them is a separate explicit decision.
> The evidence that decided it is in [20-knowledge-tier.md](20-knowledge-tier.md) §0. See issue #115.


# Disclosure — What Must Be True Before a Background Loop Reads a Room

This node is a **contract**: the conditions that must hold before any room read
reaches a durable, cross-room store. Where a condition does not currently hold,
it says so and blocks.

## 1. The argument that does not work

> *"Reading is safe because the output is screened on the way out."*

This bundle asserted it in draft. It is unsound, and the refutation should be
recorded so it is not re-derived.

The screen walks every string in the model's envelope and tests **three
identifier patterns and a coordinate pair** — four in total. That is its entire
alphabet. It
cannot see names, nicknames, quoted messages, memory conclusions, roster
composition, membership counts, or relationship claims. It never sees tool
arguments or tool results at all — only the model's final restatement of them.

**The concrete path.** A memory search returns the words somebody actually said
in room A. The model restates them into a claim's object or source excerpt. The
screen passes them — no phone number, no UUID. The result is persisted **keyed
on the unit**. The renderer then reads unit-recent facts into
`<facts>` **in every room of that unit, on every tag.**

The memory tool's own docstring says its session scoping exists precisely so
that *"a unit-wide search would surface a roster member's PRIVATE DM words
into this room."*

> The gate was placed on the read. The leak moves to the write.

The same shape applies one level up to the profile tools: Nora's own distilled
conclusions about a person in room A, laundered through a fact row, rendered in
room B.

## 2. The rule that actually governs, and its missing half

**R-31 is not the relevant rule.** It is the presence disclosure gate — a
conversation must be a verified subset of a target's circle. It would not fire
on a fact carry at all. Citing it here (as earlier drafts did) mistakes the
protection.

The governing rule is **R-49/DEC-105**, and it has **two** conditions:

> *the unit bounds which rooms may be **paired**, the persons list decides
> who may **use** the pair*

The fact path implements the first and has **no analogue for the second
anywhere**. Nothing on that path examines the destination room's person list.

Three facts make this a live gap rather than a theoretical one:

- a room's person list is **per room**, and two rooms of one unit may
  carry different ones;
- a person list of `"*"` admits **unmapped** senders;
- **nothing validates** that rooms sharing a unit share a person list.

Both files on the fact path already admit they are borrowing the sanction
rather than enforcing it — one says the honesty is *"structural, not a checked
guard"*, the other that *"that argument is borrowed, and it is only as strong
as the unit configuration is honest."*

## 3. The content class is what changes

This is the crux, and it is why the existing sanction does not stretch.

Today `<facts>` carries claims about **the world**, because the only tools that
feed it are web tools. A world-claim crossing rooms within a unit is what
R-49's first condition contemplates.

Room reads change the content class to claims about **the people in room A** —
carried automatically, durably, and unattended into room B, by a loop with
nobody watching it.

**Contract:** before any room-scoped read is granted to an adaptor whose sink
is unit-keyed, R-49's second condition must have a mechanism on the fact
path. It does not today. This is why
[10-room-access.md](10-room-access.md) blocks Path B.

For a **scope**-keyed adaptor the question is narrower but not absent: the
adaptor reads room A and writes to a unit-keyed store, so the carry still
happens one step later. The mitigation available today is that extraction
proposes *claims about the world* by objective, and the screen plus the typed
parse discard anything else. That is weaker than a checked guard and should be
recorded as such rather than relied on silently.

## 4. An act verb currently classified as a read

The sharpest concrete finding of the investigation.

`note_search` is declared read-only. On every miss it records the miss: an
upsert that increments a counter and appends the conversation id to an array.
Durable, non-idempotent, model-authored, and it **bypasses the envelope screen
entirely** — the screen sees the model's final output, not a tool's side
effect.

The rule it breaks is stated in the read-only field's **own** comment, which
grants a narrow exemption to one content-addressed cache and then says:

> *"That is a narrow exemption for a cache keyed on a hash, NOT a general
> 'writes are fine if they are cheap'. **A tool that records that something
> HAPPENED — a call, a decision, a message — is an act verb whatever it
> costs.**"*

Recording a miss records that something happened. It is not content-addressed,
not idempotent, and it grows an array.

**It is currently harmless only by accident.** No adaptor declares the tool,
and it sits in the room-scoped bucket — but the *recorded reason* for that
classification is factually wrong (it claims the collaborators are unwired;
they are wired). A careful reader who checks the stated reason, finds it false,
and reclassifies would hand a background loop a write.

**Contract:** reclassify before any note verb is granted to any adaptor. Either
the miss-recording moves out of the read path, or the tool is an act verb. It
cannot be both. Under the Dreamer's key it would also corrupt the evidence it
records — the array answers *"in how many different rooms was this asked"*, and
a unit name written in as a room makes one background loop look like an
extra room.
### 4.1 The same defect, larger, in a spec rather than a test

The revisit adaptor's specced tool subset includes an **act verb** while
labelling itself *"read verbs only"*. The startup check would refuse it, so the
adaptor cannot be built as written — and that verb is the entire mechanism by
which its own spec has a background pass reach a person, so removing it leaves
that section with no implementation path.

`note_search` is a tool misfiled as a read. This is a **design** misfiled as
one, and it is the reason
[15-roadmap.md](15-roadmap.md) §3 records that adaptor as spec-blocked rather
than deploy-blocked.

## 4.2 A precondition, recorded in a spec, unmet in production today

The known-entity check calls the search provider **directly, one layer below**
the tool-level egress screen. Its own spec says so, and says that deserves *"a
second look before it is ever flipped true in the live config."*

It is true in the live config. Nothing records the second look.

So a model-authored string derived from room messages leaves the process to a
third party, below the screen this node spends its length on — on the only
Dreamer-adjacent component **currently running**. Today's exposure is nil: the
values are two spellings of a game's name. The unmet gate is the finding, not
the exposure.

That spec is also the only one in the set with no frontmatter, and its own
status line records *"no live human review of this specific doc before
implementation."*

### 4.3 The same leak, one tier over, through memory rather than a room

The note tier defends unit-scoped rendering on the grounds that rooms
sharing a unit *"are one social unit"*, so a note established in one room
rendering into another crosses no boundary.

**That argument was made about notes written on the tag path, from what was said
in the room.** The revisit adaptor is granted memory reads, and its output is a
note revision — unit-keyed, whose title and description render in **every**
tag, and whose body is one read verb away in any room of the unit.

So §1's finding arrives again, with a shorter fuse. A fact carries a *screened
restatement*; a note body is **model-authored prose whose whole purpose is to
preserve arrangement**, reachable by an explicit read rather than only by a
recency slice. And the "one social unit" defence does not cover memory-derived
content about a person, **because that content never entered the room at all.**

**Contract:** §3's condition applies to the revisit adaptor via *memory*, not via
a room read. [08-state.md](08-state.md) §9 currently grants it exactly that, and
this node blocks the content class it produces. **That is a contradiction between
two nodes of this bundle, and it resolves the way §3 does — the grant waits on a
mechanism for R-49's second condition, or the adaptor's memory column comes off.**

### 4.4 A refusal message is an information channel

A stale-write refusal distinguishes *"not there"* from *"not there **from your
unit**"* — deliberately not distinguishable, *"so a cross-unit probe
cannot confirm a note exists."*

**Every refusal a concurrency guard emits is observable by the model, and a guard
that separates "does not exist" from "exists but is not yours" is an existence
oracle across the tenancy boundary.** This is the disclosure counterpart to
[08-state.md](08-state.md) §11.1: the screen inspects the model's *output*; this
leaks on the *input* side, inside a tool result.

## 5. Trace egress

Tool inputs and outputs are exported to the trace store. A room read therefore
ships transcripts, memory conclusions and roster names to a third-party sink on
every background tick, for every room, unattended.

The *class* is not new — the tag path already does this. The **volume** is: the
tag path is human-triggered and bounded by conversation, the Dreamer is a clock
over every scope. Nothing bounds it today. This is not a blocker; it is a cost
that should be named and sized rather than discovered.

## 6. What is genuinely protected, and stays protected

Stated so the revision does not weaken it by accident:

- **Act verbs are unreachable**, by two independent gates — the tool source
  intersects with the read-only ceiling before building, and the subset check
  refuses at startup and again per run.
- **There is no deliverer.** Nothing the Dreamer holds can reach the group.
- **Identifiers and coordinates in the envelope** are blocked at item
  granularity, counted, and logged without the offending text.
- **The tools carry their own defence in depth.** The profile and memory tools
  filter conclusions and withhold identifier-bearing answers *before the model
  ever sees the text*. That survives a grant unchanged and is the strongest
  existing protection.

## 7. Residual risk to record rather than resolve

The envelope screen drops one item and keeps its siblings. Its own docstring
anticipates a number split across fields and dismisses it because no tier
renders numeric fields into prose. That argument does not cover an identifier
split across **two string fields** of one item, and the fact renderer does emit
two such fields as prose in the same block. Low severity today; it should be
stated in the screen's own reasoning rather than left as a gap between what the
docstring claims and what it checks.

## Status / Checkpoint

2026-08-24 — F3. Its argument recurs in [26](26-standing.md) §4.3 — a screen on the wrong side of a persistence boundary — which is the same finding one tier over.
