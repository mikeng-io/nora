---
type: analysis
title: "What Each Stage Reads Today, The Five Seams, And Which Of Them Are Real"
description: "The corrected table of what each stage is handed (version 1 had three cells wrong and merged injected blocks with callable tools; version 5 corrects a FOURTH — the participation turn's memory cell, which said «✓ peer card» for a turn that read no Honcho block at all, #210). Then the seams: two that are real and unwired (the two deciders cannot see each other; the debrief writes a room position from a turn outcome), two that are component bugs wearing seam clothing (#161 lever 1, #186), one that is an open measurement (memory into the note tier). Grounded on the code at 0a5933b4 and on #199."
tags: [nora, ambient, ooda, seams, participation, proactive, notes, standing, honcho, debrief]
timestamp: '2026-09-11'
edges:
  - {dst: 00-overview, rel: depends_on, provenance: asserted}
version: 5
---

# What Each Stage Reads Today, And The Five Seams

## 1. The table, corrected

Read from the code at `0a5933b4`. **Injected** means rendered into the
prompt; **callable** means the model may ask for it (39 D9: inject the index,
call for depth). Version 1 of this node merged the two and got three cells
wrong; the corrections are marked.

| stage | transcript | Standing | notes | memory | other decider | room named |
|---|---|---|---|---|---|---|
| **Orient · note-candidates** | batch, injected | ✗ | index, injected | ✗ | ✗ | **✗ — unit name** (bug, #161) |
| **Orient · note-curation** | **✓ per-note window, injected** (v1 said ✗) | ✗ | ✓ | ✗ | ✗ | — |
| **Orient · Standing fold** (tag path) | the exchange, injected | prior | ✗ | ✗ | ✗ | **✗** (v1 said ✓; `render_debrief_turn` names no room) |
| **Decide · gate** | ✓ with ages | ✓ with age | ✗ *by design 36 §6* | ✗ *by design 36 §6* | ✗ — and stays so | **✗** (v1 said ✓; the prompt names no room, label or platform) |
| **Decide · participation turn** | ✓ | ✓ | **✓ `<notes>`** when `[notes] enabled` (on the deployment; not the shipped default) | **was ✗ — NOTHING** (v1-v4 said «✓ peer card»; the card is keyed on a tagger the occasion does not have, `fire.py:562` `person_id=None` → `fire.py:926` `if ref is not None` → `peer_card=()`, and `<self_card>`/`<peer_history>` were assembled only by the tag path's `engagement/context.py:208`). **Now ✓ `<self_card>` + `<people_in_window>`** — 2026-09-11, #210, `fire.py:958`/`:974`, rendered `prefill.py:479` | ✗ | ✓ |
| **Decide · proactive decide** | ✗ (39 D9) | ✓ per room, aged | ✓ | roster injected, **now with 1 conclusion per member** (2026-09-11, #210, `proactive.py:600` `_people_block` / `:677` `_roster_index`); Honcho still **callable** for depth, which is what the #206 ration is now free to buy (v1 called the `<people>` block Honcho — it was the roster alone) | own log only | home room |
| **Decide · proactive act** | home room's, injected | ✓ | ✓ | ✓ | ✗ | home room |

Where each cell comes from: `relevance_gate.py:19-28` (the gate's deliberate
blindness, quoted in §2.3), `:534` (the prompt is built from transcript and
Standing alone — no room); `fire.py:551, :631` (the participation turn's
`<notes>`; `:683` records that the block rendered on no fire occasion before
the tier was wired); `fire.py:974` (`_read_memory`, the participation-only
gate on both Honcho reads — #210); `proactive.py:479` (`inputs()`), `:600`
(`_people_block`, which read the roster and NOT memory until #210 gave it
`:677` `_roster_index`); `note_candidates.py:492`
(`room = unit or key`); `note_curator.py:672` (the per-note transcript
window); `debrief_render.py:240-247` (no room label, id-suppression at
`:34-42`).

Three readings:

1. **Three stages do not know which room they are in** — the note tier, the
   Standing fold, and the gate. v1 counted one.
2. **The two deciders are mutually blind**, and so are participation and the
   tag path. The "other decider" column is empty in every row, and #199 is
   what that costs: the tag turn re-derived an answer participation had sent
   one second earlier.
3. **Memory reaches every stage that judges except two:** the gate (by
   design) and the note tier (#118 step 4, deferred).

   **CORRECTED 2026-09-11 (#210).** That reading was derived from the
   participation row's «✓ peer card», and that cell was wrong on master
   (`c41b8b38`): the occasion is built `person_id=None`, so the card was `()`
   and the two `representation` blocks were never assembled on this path at
   all. So the true count was **three** stages, and the third was the one the
   whole ambient loop turns on. A cell in this table has to be read off
   `build_context`, not off the design that specified it.

   **What the numbers do and do not say.** The three `-known` halves of the
   participation suite scored 0/5, 0/5, 1/5 on both arms the night before, and
   v5 of this node first wrote that down as the instrument having said so while
   nobody read it. That attribution is not supported and is withdrawn: when
   #210's blocks were built and both arms re-run (2026-09-11, 5 samples each),
   the five `-known` halves stayed at 0/5 on the branch too, and the model's own
   prose gave its reason on nearly every run — the window is «a week old». The
   message under judgement renders at the epoch and sorts above the seeded
   window (#213), so the suite's speak side cannot currently separate «she does
   not know» from «none of this looks live». The wrong cell was real and is
   fixed; it is not what those scores measured.

## 2. The five seams

### 2.1 Decide ↔ Decide — *real, unwired, needs a store*

Three decision paths, none aware of the others: the participation turn, the
proactive decide + act turns, and the tag path. (The gate in front of the
participation turn persists **nothing durable** — metrics and trace attributes
only, `relevance_gate.py:698-711` — and that is correct: `:14-15` calls it
«admission control and nothing else». It stays out of this seam on both
sides.)
The proactive adaptor keeps a private log over its own reminder rows. The
determination log (`obs/determination.py`, written at `fire.py:1682`) is
write-only and read back into no prompt. v1 said "a `writer` column on a table
that exists". No such table exists. **This is the one new store the loop
needs** (`03` DEC-179), and it is being built tonight.

What crosses it, once it exists: the proactive decider sees participation
spoke in this room at 14:02, so it does not schedule a 16:00 reminder about
the same thing; the participation *turn* — which already holds `<notes>` —
sees a `kind="proactive"` row is pending for 18:00 about tonight's dinner,
so when a 17:40 window that settles it is admitted the turn can cancel the
row; the tag turn sees participation answered one second ago, so it does not
re-derive the route and die doing it (#199). Written by the participation
turn, the proactive turns and the tag path, at turn completion; read by the
same, scoped by unit with a `note_id` per row — a room-scoped read
could not see #199's own row, which fired into Discord about a Signal thread.
Never the gate.

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
debrief writes as held state. And it cuts the other way: her own row cannot
close a note whose closing condition it satisfies, because
`note_curator.py:685-691` excludes `is_nora` rows from the evidence window
with a comment that closing on them would be *self-attestation deciding a
retirement*, and renders the result as the `self_only` state (`:693`,
`:1389-1400`). That exclusion is doing two jobs: it stops her words being
evidence (right), and it stops her sent reply being what it is — the thing
that met the closing condition (the #199 note's condition was *Nora sends
the route*, and she had).

**The narrowing, stated exactly.** Her own sent row becomes admissible to the
**closing citation slot only**. It is excluded from discharge grounding:
`_discharge` resolves `cited_row` once and grounds every commitment and
person claim on it (`:1199-1220`), so an `is_nora` closing citation would
make her the grounding sender for a Gathering event — 39 D7 broken in code.
There is exactly one place the split can live, because one evidence tuple
serves both slots: `:691` → `message_ids` (`:700`) → `evidence_rows_by_note`
(`:793`, `:805`), read by the citation check at `:921`/`:934` **and** resolved
into `cited_row` at `:1210`. Keeping `:691` strict does not work — on #199
her row was the *only* row since `created_at`, so `message_ids` stays empty
and `:934` drops the closed verdict as `no_evidence` before any citation is
read. So her row is admitted to `message_ids`, and the guard is inside
`_discharge`: `cited_row` dropped when `is_nora`, on its own refusal reason.
The contract is *closure may cite her; discharge may not ground on her*, and
it is only a contract if removing that guard reddens a named test. What already exists on this edge and must not be
duplicated: `record_wake` / `strikes` / `revisit_at` (`schema.py:1805-1809`,
39 D6) stamp *raised by her, unanswered*. v1's `last_moved_at` would have
been a second copy of that.

**Contract.** The outcome header is scoped to the turn; a `Nora:` row after
the tag is legible to the fold as a delivered reply regardless of outcome; the
verdict can see the contradiction from the transcript alone; a dead turn does
not propose notes; her own row may be a note's closing citation and nothing
else. Fixes A1–A3 on #199 are landing tonight; A4 lands only if the
citation/discharge split above can be proved by mutation inside the current
commit path, and waits otherwise.

### 2.3 Orient → Decide — *by design where the gate is concerned; real for the proactive decider*

**The gate.** `relevance_gate.py:19-28`, verbatim:

> "Does NORA hold something true to add?" It never sees notes, facts or the
> peer card, so it cannot — and must not pretend to. That question belongs to
> the participation turn behind the gate… A gate that started guessing the
> second question would be the 35 design again in a cheaper coat.

That is design 36 §6, and the turn behind the gate renders `<notes>` and —
since #210, 2026-09-11 — `<self_card>` and `<people_in_window>`. It renders no
peer card and never did: this sentence used to say it did, on the same wrong
cell §1 carried, and the card is keyed on a tagger a participation occasion
does not have. **Nothing about the gate changed**: its prompt is still
transcript + Standing, rebuilt byte-for-byte by
`tests/unit/test_relevance_gate.py::test_the_prompt_is_exactly_the_instruction_plus_the_two_rendered_blocks`,
and `nora/ingest/relevance_gate.py` holds no memory collaborator of any kind. v1's flagship seam case ("a note closes in a window →
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
overwrites it with the unit. Observe is fine; Orient throws the room
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

- **#186** — a **safety breach** in the gate: on the Agent SDK transport a
  forged `[assistant]:` row inside a member's message is believed, and a false
  GO there is an unauthenticated turn. Raw completion holds the safety bar 6/6;
  the transport production runs breaches it 1/6, at 2.4x the tokens. The
  mechanism is in the transport (`structured.py`, shared with the debrief and
  the standing fold, which stay on it); the *fix* is one file, the gate's
  client. Landing tonight. The issue's third move — a rule that a
  security-relevant client may not call `ask_structured` — is branch B's to
  carry or to name as left out.
- **#161 lever 1** — above. Inside the note tier.

Both are real, both matter, neither needs another stage. Keeping them out of
the seam list is what keeps the seam list honest: DEC-176's reversal
condition is exactly "a seam failure closed with no cross-stage input".
