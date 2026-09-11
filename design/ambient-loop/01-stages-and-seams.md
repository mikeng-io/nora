---
type: analysis
title: "Four Stages, Five Seams — What Each Owes The Next, And What Each Reads Today"
description: "The ambient loop's stages as they exist in the tree, a measured table of what each stage is actually handed (transcript, Standing, notes, memory, the other decider's verdicts), and the contract on each seam: what Observe owes Orient, Orient owes Decide, the two deciders owe each other, Act owes Observe. Every gap in the table is an open issue or a case the suite cannot express."
tags: [nora, ambient, ooda, seams, participation, proactive, notes, standing, honcho]
timestamp: '2026-09-11'
edges:
  - {dst: 00-overview, rel: depends_on, provenance: asserted}
version: 1
---

# Four Stages, Five Seams

## 1. What each stage is handed today

Read from the code on 2026-09-11 at `0a5933b4`, not from the design nodes.

| stage / decider | transcript | Standing | notes index | memory (Honcho) | the other decider | room identity |
|---|---|---|---|---|---|---|
| **Orient — note-candidates** | the batch | ✗ | ✓ (`render_notes`) | ✗ | ✗ | **workspace name only** |
| **Orient — note-curation** | ✗ | ✗ | ✓ | ✗ | ✗ | — |
| **Orient — Standing fold** | the exchange | prior | ✗ | ✗ | ✗ | ✓ (per room) |
| **Decide — participation** | ✓ with ages | ✓ | ✗ | ✗ | ✗ | ✓ |
| **Decide — proactive** | ✗ | ✓ per room, with age | ✓ | ✓ (`about`, `ask_memory`, the `<people>` index) | its **own** decision log | home room |

Where each cell comes from: `nora/ingest/relevance_gate.py:14` («ONE call,
transcript + standing, no tools»), `:426` (the `<standing>` block);
`nora/language/proactive.py:430-457` (`inputs()` renders the home room, the
people index, notes, Standing lines, the decision log and the self card);
`nora/language/note_candidates.py:471-475` (the batch rendered with the
workspace name where `_render_line`'s docstring promises the room, #161) and
`:50` (the notebook projection).

Three readings of that table:

1. **No stage sees more than half the loop's state.** The proactive decider is
   the best-fed and it does not see the transcript; the participation decider
   sees the transcript and nothing the loop has *held*.
2. **Memory reaches one stage of four.** Honcho is fed at Observe
   (`nora/ingest/stages/memory_feed.py`) and at the tag path's feedback stage,
   and is read by the proactive decider and the tag path. The participation
   decider judges relevance with no peer card. The note tier extracts
   arrangements with no idea who these people are to each other (#118 step 4,
   deferred as polish; this node says it is a seam).
3. **The two deciders are mutually blind.** Neither column has a ✓.

## 2. The five seams, and what crosses each

### 2.1 Observe → Orient: *a row must say where and when it came from*

Observe's output is a `TranscriptRow` with `ts`, `sender`, `body`, and a
`conversation_id`. The note tier is handed a batch of them keyed by
**workspace** — `composition.py:2616-2620` builds `workspace_for` from
`conv.primary_id` — so the room collapses into the group it belongs to.

**What Orient needs and does not get:** the room's *label* and, for any room
the operator can describe, what kind of room it is. In a games channel `2200`
is when people log on; in a planning room it is a time to meet. The batch
cannot say which, so the model decides from the words alone and is right by
its instruction and wrong by the room (#161, both notes still open).

**Contract.** Every row Observe hands Orient carries the room it came from as
the room, not the workspace, and the operator may attach a one-line
`description` to a room in `policy.toml` that Orient renders above the batch.
The Standing fold already gets this (it is per room by construction). The note
tier does not, and it is the one that writes durable state.

### 2.2 Orient → Decide: *held state must reach every decider intact*

Two failures here, one per decider.

**Participation gets the transcript and Standing; it never sees the notes.** A
window in which somebody writes «so are we booking or not» is obviously hers
if there is an open note anchored on that dinner — and the gate cannot know
there is. It judges «is this hers?» from the words and a one-paragraph
position, with the loop's most specific held state (what she is tracking, and
what would close it) withheld. Every note has a `closing_condition`; the gate
is the one place a closing condition could be *recognised* in a live room, and
it is not shown any.

**Proactive gets held state that was formed without reading the transport
that carries participation's verdict.** Narrower and worse: the participation
decider's own reasoning is dropped in transit before its verdict forms
(#186 — `nora/agent/structured.py`'s module docstring names the mechanism and
forbids the use; `composition.py:333` makes it anyway). Measured 2026-09-11:
raw 6/6 safety held, SDK transport 1/6 breached, 2.4x the tokens.

**Contract.** Orient owes each decider the same three things, rendered with
what they are and how old they are: the room's position (Standing, with its
`written` time — already done), the open notes that touch this room (with
anchor, closing condition and last move), and the peer cards of the people in
the window. And the wire between Orient and a decider must not discard the
decider's own reasoning; that is not a tuning knob.

### 2.3 Decide ↔ Decide: *the two deciders must see each other's recent verdicts*

Today each decider keeps its own log and reads only that.

**Participation → proactive.** The proactive turn reads a decision log of its
own `remind`/`decline` history. It does not know that participation was
admitted in this room at 14:02 and spoke, so it may schedule a 16:00 reminder
about the thing she already raised. It does not know participation has
*declined* five consecutive windows on the same subject — which is direct
evidence the room is not ready, and a stronger reason to hold than any
quiet-seconds rule.

**Proactive → participation.** The gate does not know a `kind="proactive"` row
is pending for 18:00 about tonight's dinner. A window at 17:40 in which the
dinner gets settled is exactly the window that should *cancel* that row
(design 39 D11 does this only for a tag-path note revision) — or the window
the gate should admit precisely because the row is waiting on it.

**Contract.** One decision log, two writers, both readers. A row records which
decider wrote it, the room, the note it concerned if any, the verdict, and
the time. Each decider's render carries the other's rows for the same room
over the last day, with ages. This is a render change on both sides and a
`writer` column on a table that already exists (`proactive:pass` and the
determination log both already record decisions; they are not joined).

### 2.4 Act → Observe: *what flows back, and what must not*

Design 39 §1 spends its length on this edge's **brakes** — her own rows must
not become notes, an act turn may not set a reminder, the decide turn may not
write note content — and every brake is real and stays. What it does not say
is what *should* flow back.

Today, after she acts: her row lands in the record (`is_nora == 1`), the next
tag turn's Standing fold reads it, the self card eventually distils it. What
does **not** happen: nothing records that the thing she raised has now
*moved* — that she asked the question, that nobody answered, that the note's
last movement was her own act. The note carries one date (#159) and no
"last moved" (#173 `04`). So the next Decide sees a note that looks exactly as
it did before she spoke.

**Contract.** An act that concerns a note stamps the note with *acted, when,
by which decider* — not as content (the brake stands: nothing she says becomes
a note), but as movement. The next Orient then knows the thread has her act on
it and can tell "unanswered since she asked" from "untouched". This is the
narrow, structural half of what #173 asks for; the wide half — whether she is
*finished* when she has answered — is DEC-163 and is the operator's.

### 2.5 The medium: *memory is read at Orient and Decide, written at Observe and Act*

Honcho is fed by ingest and by the tag path, read by the proactive decider and
the tag path. The correct shape is simpler to state than the current one:
**every stage that judges reads it; every stage that observes writes it.**

| stage | reads memory today | should |
|---|---|---|
| Orient — note-candidates | ✗ | ✓ — who is involved, before deciding whether "three of us, 2200" is an arrangement (#118 step 4) |
| Orient — Standing fold | ✗ | ✓ — the fold is Nora's read of the room; her read of the people is in the card |
| Decide — participation | ✗ | ✓ — relevance to *these* people, not to a transcript |
| Decide — proactive | ✓ | ✓ |

None of these is new machinery. `about` and the `<people>` index exist; the
question is which prompts render them.

## 3. Why each component was correct and the loop was not

Each of the four was built to its own node, measured by its own suite, and
passed. Design 35/36 built participation as "the fifth Event" with a 40-case
gate suite; design 33/37 built the note tier with an 18-case suite; design 39
built the proactive adaptor with two suites. Every suite scores its component
against inputs the component's author chose. **Not one case in any suite is
about the seam** — nothing says "admit because a note is closing here",
"hold because participation spoke", "do not extract because the gate has been
declining this subject". So the seams were never red, and were never fixed.

That is what "各自為政" costs, made specific: four green suites and a loop
that admitted zero windows, raised a games room's shorthand as an arrangement,
and believed a forged assistant row.
