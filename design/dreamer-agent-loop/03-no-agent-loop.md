---
type: analysis
title: "There Is No Agent Loop"
description: "Four of five hold no tools; three make a single completion call and the fourth makes none. The fifth has a real loop and can only look outward. The runtime's guards, ceilings and stopping condition all exist and guard a loop that never runs."
tags: [nora, dreamer, agent, loop, tools, diagnosis]
timestamp: '2026-08-22'
edges:
  - {dst: 01-live-state, rel: motivated_by, provenance: asserted}
---

# There Is No Agent Loop

## 1. The mechanism

`FactCandidateAdaptor` declares an empty tool set. The tool source intersects
the declared names with the read-only ceiling, finds nothing, and returns an
empty list. The agent is constructed with no tools.

The Dreamer's stopping condition is documented as *"the model declines to call
anything"*, under `tool_choice="auto"`. Give it nothing it **could** call and
that condition is satisfied on the first pass.

> The loop does not fail to terminate. It terminates immediately. There is no
> loop — it is one completion with a JSON instruction attached.

This matters for diagnosis, because the natural reading of the production
failure is backwards. The 4,096 output tokens were not burned across a
runaway multi-turn loop; the Langfuse trace carries exactly **one** generation
observation. They were burned inside a single completion. A plain
`chat.completions.create` with the same prompt would fail identically — and
more quietly, returning a truncated body instead of raising.

**Corollary: the SDK shape is not the defect and changing it is not the fix.**
The causes are the prompt, the input bound, and the absence of anything to
look at.

## 2. What is guarding nothing

Every piece of loop machinery is built, correct, and inert for this adaptor:

| mechanism | purpose | state for a tool-less adaptor |
|---|---|---|
| `DreamGuard` R-7 default-deny | refuse an ungranted tool | budget map is empty; vetoes everything by omission |
| the read-only ceiling | a background loop may look, not act | intersects to nothing |
| `WORKSPACE_SAFE_TOOLS` | narrow a workspace key further | never reached |
| `tool_choice="auto"` | *"a loop that is permitted to stop is a loop that can stop"* | nothing to stop calling |
| per-tool budgets via the call ledger | bound tool spend | no tools to bound |
| `turns` | bound loop length | never reaches turn 2 |

The runtime is not wrong. It is a correct implementation of a research agent,
wired to adaptors that give it nothing to research with.

## 3. The loop that does exist looks only outward

`FactAdaptor` holds `web_search`, `web_fetch`, `now`, `calculate`. That is a
genuine loop — it can search, read, and search differently.

It cannot read the room, the transcript, Nora's memory of the people in it, the
notes, or its own prior conclusions. Everything it knows about *itself* arrives
as a string that Python rendered **before the loop started**, and it can never
ask for more, ask differently, or ask again.

Its rendered input carries pending claims and standing facts as bare triples
with source URLs. No transcript. No speaker. No room. No language.

## 4. The consequence, stated precisely

The extraction objective **requires** the model to emit the room's private
vocabulary:

> *"Write the subject in the words the room actually used. If they call it
> 「big walk」, the subject is `big walk` — not your guess at what it is really
> called, and not a translation."*

The adjudicator is then denied every tool that could tell it what the room
means by that vocabulary.

> **The `big walk` failure is reconstructed inside the machine built to fix
> it.**

## 5. Participant inventory

| participant | on the runtime | tools | real loop |
|---|---|---|---|
| `LexemeObserver` | no — legacy one-shot | none | no |
| `ExchangePatternObserver` | no — legacy one-shot | none | no |
| `RoomPortraitSynthesizer` | no — on the clock, not the runtime | none, and **no model call at all** | n/a, correct |
| `FactCandidateAdaptor` | yes | none | no |
| `FactAdaptor` | yes | web only | partial — outward only |

Four of five have no loop; the portrait is the one that should not. The portrait is the one legitimate case: its
objective is executed in code and the keystone argues, correctly, that a third
model call synthesising prose from already-vetted output would reintroduce the
risk the tier removed. Making everything an agent is not the goal.

## 6. What a real loop needs to be able to do

Taking the room's own messages as the worked example — short fragments, links,
mixed script, an @-mention, one long game clue — a loop that could actually
comprehend them would:

- resolve a pronoun by reading around it in the transcript;
- find out who the participants are, and who an @-mention names;
- discover what an unfamiliar proper noun refers to by opening a link somebody
  pasted;
- check whether the tier already knows this thing, and under what name;
- check whether it already tried and failed to settle it.

Not one of those is possible today. The first three are room reads
([10-room-access.md](10-room-access.md)); the last two are reads of the tier's
own state ([08-state.md](08-state.md),
[08-state.md](08-state.md)).

The output of such a loop over that batch is not sixty-five triples. It is two
or three grounded, attributed claims and a decision that the rest was chatter.

## Status / Checkpoint

2026-08-24 — The mechanism behind F1, unchanged. Cited by [28](28-language-tier.md) §2 — all three language adaptors are tools-less, so each is a single completion.
