---
type: proposal
title: "The Ambient Loop Is Nora's Second Backbone — One OODA Loop, Named As The System's"
description: "Nora has two backbones. The tag path answers when called. The ambient loop is how she is present when not called — one observe–orient–decide–act loop whose stages are the ingest path, the memory layer, two deciders (participation and proactive) and the shared fire path. The loop exists in the tree and had only been named as the proactive adaptor's. Version 2: rewritten against an adversarial review that refuted four of version 1's claims, and against a live incident (#199) that crossed five of the loop's seams in ninety minutes."
tags: [nora, ambient, ooda, backbone, participation, proactive, notes, standing, memory]
timestamp: '2026-09-11'
edges:
  - {dst: "dreamer-agent-loop/39-proactive-ooda-loop", rel: revises, provenance: asserted}
  - {dst: "dreamer-agent-loop/36-relevance-gate", rel: depends_on, provenance: asserted}
  - {dst: "dreamer-agent-loop/35-ambient-participation", rel: depends_on, provenance: asserted}
  - {dst: "dreamer-agent-loop/26-standing", rel: depends_on, provenance: asserted}
  - {dst: "dreamer-agent-loop/33-note-dreamer-schedule", rel: depends_on, provenance: asserted}
version: 3
---

# The Ambient Loop Is Nora's Second Backbone

## 0. What this node decides

**Nora has two backbones.** The **tag path** is how she answers when somebody
calls her — one tag, one turn, one reply, owed always. The **ambient loop** is
how she is present when nobody has: reading everything, holding what is
unsettled, speaking when there is an occasion, and staying quiet the rest of
the time. It is one observe–orient–decide–act loop, closed through a human.

That sentence is in the tree already — design 39 §1, `proactive.py:1`,
`fire.py:303`, GLOSSARY under *proactive*. What is not in the tree is the
claim this node makes: **the loop is the system's, not the proactive
adaptor's.** Design 39 wrote it from the adaptor's seat. Its two entry points
are *tag* and *proactive*; the participation decider is not in the table; the
memory layer is one stage's input rather than the medium every judging stage
reads.

**What version 1 of this node got wrong, and version 2 corrects.** An
adversarial review of v1 found four false claims about the tree and one
circular rule, and they are worth naming here because each is a way this
framing can go wrong again:

1. The relevance gate is blind to notes and memory **on purpose** — design 36
   §6, «the only two things» — and the participation *turn* behind it already
   reads both. v1
   called that a seam. It is a two-layer Decide, and the layers are the design.
2. Two of v1's "seams" were component bugs: #186 is a one-file transport swap
   inside the gate; #161's `key` is already the room and the note tier throws
   it away. Neither needs input from another stage.
3. The tag path is a **writer** of the loop's held state — the debrief writes
   Standing and proposes notes. v1 called it a consumer. It feeds the loop.
4. v1's shared rule ("hold what the deciders would act on") was circular; with
   0 windows admitted it evaluates to hold nothing. Design 33 §3's test — a
   note has a closing condition — is decidable at write time and stays.

What survived the review, and is now grounded on an incident rather than an
argument, is in §2–§3.

## 1. The two backbones

| | tag path | ambient loop |
|---|---|---|
| **trigger** | somebody addresses her | a window closes; a note moves; a clock fires |
| **owed** | a reply, always | nothing — silence is the default outcome |
| **scope** | one room, one exchange | a workspace, across rooms and turns |
| **judgement** | *what* to say | *whether* to say anything, and *when* |
| **what it is for** | being useful when asked | being there when not asked |

They share the doctrine substrate, the tool registry, the deliverer chain and
the conversation slot. They do not share a trigger, an obligation, or a scope.

**And they share held state in both directions.** The tag path's debrief is
the principal writer of Standing and proposes notes (`debrief.py:551-608`);
the loop's deciders read both. So the tag path is not a stage of the loop and
not merely a consumer of it — it is the other backbone, and the loop's Orient
stage is fed by it. Design 39's "tag is the passive half, entering at Act" was
closer than v1 allowed: tag *does* enter, at Orient, as a writer.

## 2. The loop, named

```
                 ┌───────────────────────────────────────────────────────┐
                 │                                                       │
   rooms ──▶ OBSERVE ──▶ ORIENT ──▶ DECIDE ──▶ ACT ──▶ rooms ──▶ ────────┘
             ingest      notes      ┌ participation (gate → turn)    fire path
                         Standing   └ proactive (decide → act)
                         Honcho          ▲
   tag path ────────────────┘            │ each decider is two-layer
   (debrief writes Standing, notes)      │ and neither sees the other
```

| stage | what it is in Nora | where |
|---|---|---|
| **Observe** | every human row lands in the record, marked with its room, its time and who sent it, and is fed to memory | `nora/ingest/` |
| **Orient** | rows become held state: what is unsettled (notes), where each room stands (Standing), who these people are (Honcho). Written by the note tier, the curator, and **the tag path's debrief** | `note_candidates.py`, `note_curator.py`, `debrief.py`, Honcho |
| **Decide** | **two deciders, each two-layer.** *Participation:* the gate (cheap — transcript + Standing, blind to notes and memory by design 36 §6, a pure function that writes nothing) admits a closed window; the turn behind it (everything, including `<notes>` when the tier is enabled) decides whether she holds anything to add. *Proactive:* the decide turn (notes, Standing, roster, own log — no transcript, 39 D9) schedules a time; the act turn (the home room's transcript) speaks or declines | `relevance_gate.py` + `fire.py` participation; `proactive.py` + `fire.py` proactive |
| **Act** | say it, in a room, under the room's policy, or decline | `FirePath.speak` |
| **feedback** | her rows land; the debrief folds them into Standing; `record_wake`/`strikes` stamp a note she raised | the Act → Observe edge, with 39 §1's brakes |

**Memory is the medium.** Written at Observe and after every tag; read by the
proactive decide turn, the participation *turn*, and the tag path. Not read by
the gate (by design) or the note tier (#118 step 4). Whether the note tier
should read it is an open question with a measurement attached (`03` DEC-178),
not a decision.

## 3. The incident that grounds this node — #199, 2026-09-11

Design arguments are cheap. This one happened, in the operator's own rooms,
and it crossed five seams in ninety minutes. Every row is in `03 §5`'s table
with its trace id.

16:42, Signal, untagged: *how do I get to Cyberport at 10 tomorrow?* The gate
**admitted** the window; the participation turn **answered in full** at 16:43.
One second later the operator — not having seen the answer — tagged her. The
tag turn re-derived the whole route from scratch (`saved_places`, `route` ×2),
hit six `reasoningContent` warnings, and **died** with `APIError`. Its debrief
was handed `<what_happened>the model call failed; nothing was sent</what_happened>`
plus a 133-row transcript whose second-to-last row was her own sent answer.
The fold wrote Standing as *«我今日冇出過任何回覆»* — a room-scoped claim
generalised from a turn-scoped header — and **proposed a note** with the same
false premise. The verdict returned `grounded: true`. At 18:03 the proactive
decider, which has no transcript, read the note and Standing and scheduled a
reminder: *«got no route at all — raise at 18:11»*. At 18:11 the act turn
fired into the **home room — Discord**, read Discord's transcript, found no
Cyberport in it, and told him it was catching up on a question it had never
answered and could not find his address.

Five seams, in order:

| seam | what crossed it wrongly |
|---|---|
| **Decide ↔ Decide** (participation ↔ tag) | the tag turn could not see that participation had just answered; it re-derived and died |
| **Act → Orient** (the debrief) | a turn outcome became a room position; the header outranked a row the model could see |
| **Orient** (debrief → note) | the false position became a false note with a closing condition her own answer had already met |
| **Orient → Decide** (proactive) | no input could contradict the held state; the decider trusted it and read memory six times |
| **Act** (the room) | home-room-only; the answer to a Signal question landed in Discord |

None of those is inside a component. That is the whole argument of this node,
and it needed to happen before it could be made honestly.

## 4. What this node does not do

- **It does not change the architecture.** No new process, no new occasion
  kind. It adds **one store** — the shared decision log (`03` DEC-179) — which
  v1 wrongly claimed already existed.
- **It does not reverse design 36 §6.** The gate stays cheap, blind to notes
  and memory, and a pure function — it neither reads nor writes the decision
  log. The participation *turn* behind it does both. Version 2 gave the gate
  a `<decisions>` block; a pending proactive row carries a note title
  (`fire.py:317`), so that was 36's second question under another name.
- **It narrows one brake and says so.** Design 39 §1's row «Nora's message
  → note | curator filter» (`note_curator.py:691`, whose comment calls a
  closure on her own row *self-attestation deciding a retirement*) is narrowed,
  not kept: her own sent row becomes admissible to a note's **closing
  citation slot only**, and is excluded from discharge grounding, because
  `_discharge` grounds every commitment and person claim on the cited row
  (`:1199-1220`) and 39 D7 forbids her citing herself as the state of the
  world. The `self_only` state stays for the case where her row is the only
  evidence. Whether that narrowing is even buildable inside the current commit
  path is being tested on branch A tonight; if it is not, it waits.
- **It does not decide DEC-163** (#173). §3's Act → Orient seam is the
  structural half of "keeps track between turns"; whether she is *finished*
  when she has answered is a sentence in `identity.md` and is the operator's.
- **It does not rewrite any objective.** `02` specifies inputs; wording is the
  operator's — with one exception stated there: the debrief's outcome header
  is a render, not an instruction, and it is what #199 turns on.

## 5. Reading order

`01` — the corrected table of what each stage reads today, the five seams
with their contracts, and which of v1's seams were component bugs. `02` —
inputs per objective, the write-time rule that stays, the seam cases with
their real fixture costs, the positive control. `03` — decisions with reversal
conditions, what is superseded, the migration as it is actually being landed
tonight, and the open issues re-read as seams.
