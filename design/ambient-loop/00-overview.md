---
type: proposal
title: "The Ambient Loop Is Nora's Second Backbone — One OODA Loop, Named As The System's"
description: "Nora has two backbones, not one. The tag path answers when called. The ambient loop is how she is PRESENT without being called — and it is one observe–orient–decide–act loop whose stages are the ingest path, the memory layer, two deciders (participation and proactive), and the shared fire path. The loop already exists in the tree; it has never been named as the system's, only as the proactive adaptor's. This node fixes what it is, what it is not, and why the components rotted independently while each was correct on its own terms."
tags: [nora, ambient, ooda, backbone, participation, proactive, notes, standing, memory]
timestamp: '2026-09-11'
edges:
  - {dst: "dreamer-agent-loop/39-proactive-ooda-loop", rel: supersedes, provenance: asserted}
  - {dst: "dreamer-agent-loop/35-ambient-participation", rel: revises, provenance: asserted}
  - {dst: "dreamer-agent-loop/36-relevance-gate", rel: revises, provenance: asserted}
  - {dst: "dreamer-agent-loop/26-standing", rel: depends_on, provenance: asserted}
  - {dst: "dreamer-agent-loop/33-note-dreamer-schedule", rel: depends_on, provenance: asserted}
version: 1
---

# The Ambient Loop Is Nora's Second Backbone

## 0. What this node decides

**Nora has two backbones.** The **tag path** is how she answers when somebody
calls her — one tag, one turn, one reply. The **ambient loop** is how she is
present in a room when nobody has called her: reading everything, holding what
is unsettled, speaking when there is an occasion, and staying quiet the rest
of the time. Everything that is not the tag path is a stage of this loop.

The loop is an OODA loop — observe, orient, decide, act — and it is closed
through a human. That sentence is already in the tree (design 39 §1,
`nora/language/proactive.py:1`, `nora/app/fire.py:303`, GLOSSARY under
*proactive*). What is **not** in the tree, anywhere, is the claim this node
makes: **the loop is the system's, not the proactive adaptor's.** Design 39
wrote it from the adaptor's seat — "one loop, two entry points", where the two
entries are *tag* and *proactive* — and in doing so left the participation
mode out of the loop entirely and treated the memory layer as an input to one
stage rather than the medium every stage reads and writes.

That misplacement is not a documentation nit. It is why four components that
are each correct on their own terms measurably fail at their seams (§3), and
why every one of them has its own doctrine file and its own eval suite while
**no case anywhere measures a property that crosses two of them** (`02 §3`).

## 1. The two backbones, and what separates them

| | tag path | ambient loop |
|---|---|---|
| **trigger** | somebody addresses her | a window closes; a note moves; a clock fires |
| **owed** | a reply, always (R-2, R-5's floor) | nothing — silence is the default outcome |
| **scope** | one room, one exchange | a workspace, across rooms and turns |
| **judgement** | *what* to say | *whether* to say anything, and *when* |
| **where it lives** | `nora/engagement/` | `nora/ingest/`, the memory layer, the two deciders, `nora/app/fire.py` |
| **what it is for** | being useful when asked | being there when not asked |

The two share the doctrine substrate, the tool registry, the deliverer chain
and the conversation slot. They do not share a trigger, an obligation, or a
scope. The tag path can be understood one turn at a time. The ambient loop
cannot: its whole value is what it carries **between** turns, which is why it
has to be named as a loop and not as a list of features that happen to run in
the background.

Design 39's reading — that tag is the "passive half" and enters the loop at
Act — is retired. Tag is not a half of this loop. It is the other backbone,
and it reads the loop's state (Standing, notes, the peer card) the way any
consumer reads a substrate: without being a stage of it.

## 2. The loop, named

```
             ┌──────────────────────────────────────────────────────┐
             │                                                      │
   rooms ──▶ OBSERVE ──▶ ORIENT ──▶ DECIDE ──▶ ACT ──▶ rooms ──▶ ─┘
             ingest      memory     ┌ participation   fire path
                                    └ proactive
```

| stage | what it is in Nora | where |
|---|---|---|
| **Observe** | every human row lands in the record and is fed to memory, marked with its room, its time and who sent it | `nora/ingest/` — scope → identity → media → audit → memory feed → gate |
| **Orient** | raw rows become held state: what is unsettled (notes), where each room stands (Standing), who these people are (Honcho) | `note-candidates`, `note-curation`, the Standing fold, Honcho's peer cards |
| **Decide** | **two deciders**, one question each | **participation**: "this window just closed — is it hers?" (`nora/ingest/relevance_gate.py`) · **proactive**: "across the workspace, is anything worth raising, and when?" (`nora/language/proactive.py`) |
| **Act** | say it, in a room, under the room's policy, or decline | `FirePath.speak` — `kind="participation"` and `kind="proactive"` |
| **feedback** | her own rows land in the record; Standing folds them; the self card distils them | the Act → Observe edge, with the brakes design 39 §1 already lists |

**Two deciders, not one.** This is the correction to design 39. Participation
and proactive are the same stage asked two questions on two triggers. They read
the same held state and speak through the same path. What differs is the
trigger (a quiescence window versus a change or a clock), the scope (one room
versus a workspace) and the shape of the answer (admit this window versus
schedule a time). Treating one as "the loop" and the other as "a fifth Event"
is how they came to be built without reference to each other.

**Memory is the medium, not a stage.** Honcho, the note store and the Standing
table are written at Observe and Act and read at Orient and Decide. Design 39
placed Honcho inside Observe; the tree places it everywhere or nowhere — the
proactive turn reads it, the gate never does, the note tier never does (`01 §1`).
A medium that only some stages can see is not a medium.

## 3. Why this is the right time to name it

The evidence that the loop was never treated as one thing is that its seams
are where it fails, measured:

- **Observe → Orient.** The note tier is handed a batch that says which
  *workspace* it came from, not which *room*. On `friends` every line renders
  `(friends)` whether it came from Signal or a Discord games channel, and the
  tier wrote two notes about a "Bath gathering" from a games room's shorthand
  — the second from a batch in which the word *bath* does not appear (#161).
- **Orient → Decide.** The participation decider's verdict is formed on a
  transport that drops the reasoning before the verdict is made. Measured
  2026-09-11, same tree, same model, same instruction: raw completion holds
  the safety bar 6/6; the transport production runs breaches it 1/6 — a forged
  assistant row inside a member's message is believed 4 times in 5 (#186).
- **Decide ↔ Decide.** The two deciders cannot see each other. Participation
  does not know proactive raised the thing this window is about; proactive does
  not know participation spoke in this room two hours ago (`01 §3`).
- **Act → Observe.** The identity doctrine says «then you're done». Recall on
  the proactive decide suite is 0.34 against a false-positive rate of 0.02
  (#173 `01 §1`): she is measurably right about staying quiet and measurably
  unable to act, because the loop is told to stop at Act.
- **The ambient Decide has never fired.** 0 of 33 windows admitted in the 20
  hours after participation went live (#163). The operator's read is that the
  rooms were quiet — which is a valid reading, and also means half the loop has
  no production evidence at all.

None of those is inside a component. Every one is between two. That is the
signature of four things built as if they were standalone, and the reason
this node exists before another component is tuned.

## 4. What this node does not do

- **It does not change the architecture.** Every stage named above exists in
  the tree today. No new process, no new store, no new occasion kind.
- **It does not decide DEC-163** (#173 — whether «then you're done» becomes
  «stopping talking is not the same as being finished»). That is the Act →
  Observe edge and it is operator-only. This node names the edge; that
  decision says what flows across it.
- **It does not rewrite any doctrine file.** `02` says what each stage's
  objective must be *told* — inputs, not instructions — and what would prove
  the loop is working. The wording is the operator's.
- **It does not touch the tag path.**

## 5. Reading order

`01` — the four stages and the five seams, with what each stage owes its
neighbours and a table of what each actually reads today. `02` — doctrine as
per-stage objectives with crossing inputs, the shared save/don't-save rule, and
the cross-seam eval cases that do not exist. `03` — decisions with reversal
conditions, what this supersedes, the migration order, and the open issues
re-filed as seams.
