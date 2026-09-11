---
type: analysis
title: "Doctrine Per Stage, Inputs Across Stages, And The Cases That Do Not Exist"
description: "Where the real change is. The architecture is in place; what each stage is TOLD, and what proves the loop works, are not. One objective per stage stays — but each objective's inputs cross the seams, one save/don't-save rule is shared by all of them, and the eval layer gains cases that can only pass when two stages agree. Written to the repo's own lesson: when a model judges badly, read its input before its instruction."
tags: [nora, ambient, ooda, doctrine, objectives, evals, measurement]
timestamp: '2026-09-11'
edges:
  - {dst: 01-stages-and-seams, rel: depends_on, provenance: asserted}
version: 1
---

# Doctrine Per Stage, Inputs Across Stages

## 1. The change is not the wording

The operator's framing, which this node adopts: *the tools and the structure
are already there; what has to change is the doctrine and the measurement —
what she saves, what she does not, and how we know.*

That is exactly the repo's own rule, applied to the loop instead of to one
gate. `AGENTS.md`: **when a model judges badly, read its INPUT before you
rewrite its instruction.** Measured three times on 2026-09-09/10 and it went
the same way each time; the Relevance Gate's three instruction rewrites changed
nothing or made it worse, and rendering an age per row cut its error rate by
two thirds. #161 is the same shape (the room was never in the prompt). #186 is
the same shape one layer down (the reasoning was never on the wire).

So the order here is: inputs first (`§2`), the one rule that has to be shared
(`§3`), then the cases that would show it working (`§4`). Instruction wording
is last and is the operator's.

## 2. What each objective is told — the crossing inputs

Five objectives exist and stay five. Each keeps one question. What changes is
the block list each is handed.

| objective | today | adds | why |
|---|---|---|---|
| `participation.md` (gate) | transcript with ages, Standing | the open notes touching this room, with anchor / closing condition / last move; the peer cards of the people in the window; proactive's rows for this room, last 24h | "is this hers?" turns on what she is holding and who is talking (`01 §2.2`) |
| `proactive.md` (decide) | notes, Standing, people, own log, self card | participation's verdicts for each room, last 24h — admitted / declined, and whether she spoke | "worth raising?" turns on whether the room has already heard from her, or has been declining the subject (`01 §2.3`) |
| `note-candidates.md` | batch, notebook | the room's label and its operator description; the room's Standing; the people involved, from memory | "is this an arrangement?" turns on what room this is and who these people are (`01 §2.1`, `§2.5`) |
| `note-curation.md` | notebook | each note's *last move* — including her own act on it | "has this gone quiet?" needs to know whether the last thing that happened was her asking (`01 §2.4`) |
| `standing-debrief.md` | the exchange, prior | the open notes for this room | the room's position should know what is unsettled in it — today the fold can contradict the notebook |

Every addition is a render block over state that already exists. None needs a
new store. Two need a new column (`last_moved_at` on `note`, and a `writer` on
the decision log — `03 §3`).

**The rule for every block, restated because it is what was missing each
time:** a block says what it is, how old it is, and which way it reads. A note
line carries its anchor *and* when it last moved. A decider's row carries who
decided, what, and when. A peer card carries when it was last distilled. The
gate's fix was an age per row; there is no reason to learn it a fifth time.

## 3. The one rule that has to be shared

Today three files decide, separately, what enters held state:
`note-candidates.md` decides what becomes a note; `standing-debrief.md`
decides what stays in the position; the curator decides what retires. Three
authors, three thresholds, no common test.

The rule this node proposes for all of them, in one sentence:

> **What the loop holds is what the loop would act on.** A thing the deciders
> would never raise and the gate would never admit a turn about is not tracked
> uncertainty — it is noise that happens to be phrased like a plan.

Consequences, each of which is checkable:

- **A note earns its place by having a decider that could use it.** "Bath
  gathering" from a games room fails this test the moment the gate is shown it:
  no window in that room would be admitted *because of* it, and the proactive
  turn would decline to raise it. A candidate the deciders would ignore is not
  written. (This is what `01 §2.3`'s shared log makes measurable: a subject the
  gate has declined N times is evidence against extracting it.)
- **Standing holds what would change a decision.** `Tyler 話食緊飯` stays out
  because no decider's verdict turns on it. #78 fixed the writer's person; this
  gives the fold the test it was missing.
- **Retirement is when no decider could act on it any more**, not when a date
  has passed — a note anchored on the 25th whose owed answer was due the 8th
  (#159, #162) is *more* live on the 9th, not less.

This is not a new value. It is design 33 §3's "a note is a staging area for
something that will be acted on" made into a rule the other stages are also
bound by, so the three files stop disagreeing about the same object.

## 4. The cases that do not exist

Every suite in the tree scores one stage against inputs its own author chose:

| suite | cases | what it measures | what it cannot see |
|---|---|---|---|
| `relevance_gate` | 40 | admit / decline, safety | whether an open note should have changed the verdict |
| `proactive_decide` | 11 | remind / decline, when | whether participation already spoke |
| `proactive_act` | — | speak / silent | — |
| `note_candidates` | 18 | create / none | whether the deciders would ever use the note |
| `participation` | 11 | speak / silence through the real path | anything the gate held |

Four green suites and a loop that admitted zero windows. The seam cases that
would have been red:

**S1 — a note closes in a window.** An open note with a closing condition;
a window in which the condition is met. *Participation admits.* Today's gate
declines (it cannot see the note) and today's suite cannot express the case.

**S2 — she already spoke.** Participation admitted and spoke in a room at
14:00 about X. Proactive at 16:00 with X still open. *Proactive holds.* Today
it cannot know, and the decide suite has no participation input.

**S3 — the room keeps declining.** Five consecutive windows on subject X
declined by the gate. A batch mentioning X reaches the note tier. *No note.*
Today the tier writes it; the candidates suite has no gate input.

**S4 — her own act moved the thread.** Proactive raised X at 09:00; nobody
answered. Curator at 16:00. *The note reads "asked, unanswered since 09:00",
not "untouched since Tuesday".* Today the note looks identical to before she
spoke (`01 §2.4`).

**S5 — a pending row is overtaken.** A `kind="proactive"` row for 18:00 about
tonight's dinner; a 17:40 window settles it. *The gate admits AND the row is
cancelled.* Today D11 cancels only on a tag-path note revision.

**S6 — the same people, a different room.** The same arrangement mentioned in
the games channel and in the Signal room. *One note, anchored where plans are
made.* Today: two notes or one wrong one, because the tier cannot tell the
rooms apart.

Six cases, each spanning two stages, each expressible with the fixtures the
suites already have — a seeded note, a scripted window, a clocked decide turn.
They are the acceptance criteria for `03 §4`'s migration, and **they are a
precondition for tuning any single component again**: #158 already shows the
decide suite cannot separate a prompt change from noise at n=5; a seam that is
unmeasured cannot be tuned at all.

## 5. What proves the loop, end to end

One scenario, run through the real stages, is the loop's positive control —
the thing that says "this exists" rather than "each part exists":

> A human row lands in a planning room (**Observe**). The note tier writes a
> note with an anchor and a closing condition; Standing records the room is
> waiting on it (**Orient**). A window closes with the condition unmet;
> participation declines — correctly — and the decline is logged. The clock
> fires; proactive reads the note, the log, and the people, and schedules a
> reminder for the evening (**Decide**). The reminder fires; she asks the
> question in the room (**Act**). Her row lands; the note is stamped as moved
> by her act. The next window answers her; participation admits — because the
> note's condition is now met — and the curator retires it (**Observe → Orient
> → Decide → Act**, second pass).

Every step of that is buildable today except the two stamps (`03 §3`). It
belongs in `evals/` as one journey case, and it is red on the current tree at
the third step.
