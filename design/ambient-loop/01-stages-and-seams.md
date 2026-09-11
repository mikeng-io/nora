---
type: analysis
title: "What Each Stage Reads Today, The Five Seams, And Which Of Them Are Real"
description: "The corrected table of what each stage is handed (version 1 had three cells wrong and merged injected blocks with callable tools). Then the seams: two that are real and unwired (the two deciders cannot see each other; the debrief writes a room position from a turn outcome), two that are component bugs wearing seam clothing (#161 lever 1, #186), one that is an open measurement (memory into the note tier). Grounded on the code at 0a5933b4 and on #199."
tags: [nora, ambient, ooda, seams, participation, proactive, notes, standing, honcho, debrief]
timestamp: '2026-09-11'
edges:
  - {dst: 00-overview, rel: depends_on, provenance: asserted}
version: 2
---

# What Each Stage Reads Today, And The Five Seams

## 1. The table, corrected

Read from the code at `0a5933b4`. **Injected** means rendered into the
prompt; **callable** means the model may ask for it (39 D9: inject the index,
call for depth). Version 1 of this node merged the two and got three cells
wrong; the corrections are marked.

| stage | transcript | Standing | notes | memory | other decider | room named |
|---|---|---|---|---|---|---|
| **Orient · note-candidates** | batch, injected | ✗ | index, injected | ✗ | ✗ | **✗ — workspace name** (bug, #161) |
| **Orient · note-curation** | **✓ per-note window, injected** (v1 said ✗) | ✗ | ✓ | ✗ | ✗ | — |
| **Orient · Standing fold** (tag path) | the exchange, injected | prior | ✗ | ✗ | ✗ | **✗** (v1 said ✓; `render_debrief_turn` names no room) |
| **Decide · gate** | ✓ with ages | ✓ with age | ✗ *by design 36* | ✗ *by design 36* | ✗ | **✗** (v1 said ✓; the prompt names no room, label or platform) |
| **Decide · participation turn** | ✓ | ✓ | **✓ `<notes>`** | ✓ peer card | ✗ | ✓ |
| **Decide · proactive decide** | ✗ (39 D9) | ✓ per room, aged | ✓ | roster injected; Honcho **callable** (v1 called the `<people>` block Honcho — it is the roster) | own log only | home room |
| **Decide · proactive act** | home room's, injected | ✓ | ✓ | ✓ | ✗ | home room |

Where each cell comes from: `relevance_gate.py:14-27` (the gate's deliberate
blindness, quoted in §2.3), `:525-531` (no room in the prompt); `fire.py:319,
:551, :631` (the participation turn's `<notes>`); `proactive.py:449-479`
(`inputs()`), `:481-513` (`_people_block` reads the roster, not memory);
`note_candidates.py:491-495` (`room = workspace or key`); `note_curator.py:670`
(the per-note transcript window); `debrief_render.py:240-247` (no room label,
id-suppression at `:34-42`).

Three readings:

1. **Three stages do not know which room they are in** — the note tier, the
   Standing fold, and the gate. v1 counted one.
2. **The two deciders are mutually blind**, and so are participation and the
   tag path. The "other decider" column is empty in every row, and #199 is
   what that costs: the tag turn re-derived an answer participation had sent
   one second earlier.
3. **Memory reaches every stage that judges except two:** the gate (by
   design) and the note tier (#118 step 4, deferred).

## 2. The five seams

### 2.1 Decide ↔ Decide — *real, unwired, needs a store*

Three decision paths, none aware of the others: the gate + participation turn,
the proactive decide + act turns, and the tag path. The gate persists
**nothing durable** — metrics and trace attributes only (`relevance_gate.py:698-711`).
The proactive adaptor keeps a private log over its own reminder rows. The
determination log (`obs/determination.py`, written at `fire.py:1682`) is
write-only and read back into no prompt. v1 said "a `writer` column on a table
that exists". No such table exists. **This is the one new store the loop
needs** (`03` DEC-179), and it is being built tonight.

What crosses it, once it exists: the proactive decider sees participation
admitted and spoke in this room at 14:02, so it does not schedule a 16:00
reminder about the same thing; the gate sees a `kind="proactive"` row is
pending for 18:00 about tonight's dinner, so a 17:40 window that settles it is
admitted *and* the row is cancelled; the tag turn sees participation answered
one second ago, so it does not re-derive the route and die doing it (#199).

### 2.2 Act → Orient — *real, and #199 is its exact shape*

The tag path's debrief writes Standing and proposes notes from a render of the
finished turn. That render carries `<what_happened>` from the turn's outcome.
For `llm_error` it says *nothing was sent* — true of the turn, unscoped, and
the fold generalised it to *I have sent nothing today* with her sent row two
lines above the tag in the same window. The verdict, which exists to catch
unsupported claims, said `grounded: true`. The same fold proposed a note whose
closing condition her own row had already met.

Design 39 §1's brake — *nothing Nora says can create, revise or close a note* —
holds for her **words**. It does not hold for her **turn outcomes**, which the
debrief writes as held state, and it cuts the other way: her own row cannot
close a note whose closing condition it satisfies. What already exists on this
edge and must not be duplicated: `record_wake` / `strikes` / `revisit_at`
(`schema.py:1805-1809`, 39 D6) stamp *raised by her, unanswered*. v1's
`last_moved_at` would have been a second copy of that.

**Contract.** The outcome header is scoped to the turn; a `Nora:` row after
the tag is legible to the fold as a delivered reply regardless of outcome; the
verdict can see the contradiction from the transcript alone; a dead turn does
not propose notes; closure — not content — may be satisfied by her own row.
Fixes A1–A4 on #199, landing tonight.

### 2.3 Orient → Decide — *by design where the gate is concerned; real for the proactive decider*

**The gate.** `relevance_gate.py:18-27`, verbatim:

> "Does NORA hold something true to add?" It never sees notes, facts or the
> peer card, so it cannot — and must not pretend to. That question belongs to
> the participation turn behind the gate… A gate that started guessing the
> second question would be the 35 design again in a cheaper coat.

That is design 36 decision 5, and the turn behind the gate renders `<notes>`
and the peer card. v1's flagship seam case ("a note closes in a window →
admit") already exists twice in the participation suite and passes. The gate
is not missing notes; it is a cheap pre-filter *in front of* the decider that
has them. Nothing here changes that. The one crossing input the gate does have
— Standing — is set in **1 of 40** gate cases; that is the measurement to take
before anything else is added to the gate.

**The proactive decider.** 39 D9 dropped `load_transcript` from its tool set
on the argument that Standing already carries the room and the verbatim
window is where self-echo would be loudest. #199 is D9's own reversal
condition firing: faced with a note saying *未覆* and Standing saying *model
call 死咗*, the decide turn had no input that could contradict either, read
memory six times, and scheduled the reminder. **Contract:** the decide turn
gets a bounded way to check a note against the record — a recent window for
any room a note references — or the shared log (§2.1) carrying the
participation verdict that answered it. The second is smaller and lands
first.

### 2.4 Observe → Orient — *a component bug, plus one config field*

v1 said "a row must say where it came from". It does: `key` in
`note_candidates.py:491` **is** the room's conversation id, and the next line
overwrites it with the workspace. Observe is fine; Orient throws the room
away. Fix: render the room's label (`composition.py` already builds the map
the proactive adaptor uses). The only genuinely cross-stage part is the
operator's one-line `description` per room in `policy.toml`, which is config,
not a stage. Both land tonight (#161).

The same defect exists in two more places the table shows and #161 does not
name: the gate's prompt and the Standing fold's render carry no room label
either. Filed as part of the follow-up in `03 §5`.

### 2.5 The medium — *one open measurement*

Honcho reaches the participation turn, the proactive decider (callable) and
the tag path. The note tier extracts "is this an arrangement?" with no idea
who these people are to each other. #118 step 4 deferred that as "only if
bodies read as useless". This node says it is worth *measuring* — and stops
there, because the precedent on the gate (#144/#147: a capability list made
the verdict measurably worse) says more input is not always better. `03`
DEC-178 is an open question with the experiment attached, not a decision.

## 3. Which of v1's seams were not seams

- **#186** — the gate's verdict is formed on a transport that drops the
  decider's *own* reasoning. That is inside the gate. One-file fix, measured
  6/6 vs 1/6. Landing tonight.
- **#161 lever 1** — above. Inside the note tier.

Both are real, both matter, neither needs another stage. Keeping them out of
the seam list is what keeps the seam list honest: DEC-176's reversal
condition is exactly "a seam failure closed with no cross-stage input".
