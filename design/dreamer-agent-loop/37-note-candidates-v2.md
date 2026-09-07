---
type: proposal
title: "Note Candidates v2 — One Recursive Loop for What the Conversation Says, Time for What It Does Not"
description: "The note tier after 2026-09-08 has two Dreamer passes and no note in production. This node records the operator's reframe: the conversation-driven lifecycle (create, update, CLOSE) belongs in one recursive pass over the message window, enriched at write time from memory; the clock-driven lifecycle (expiry, silence, strikes) stays in the curator because silence is the one signal a conversation cannot carry. Date becomes optional so a living thread («what game next») can be a note; time does not become ignorable. Memory is material, never proof."
tags: [nora, note-tier, dreamer, candidates, curator, memory, honcho, enrichment, dateless, lifecycle]
timestamp: '2026-09-08'
edges:
  - {dst: 33-note-dreamer-schedule, rel: extends, provenance: asserted}
  - {dst: 34-note-dreamer-consolidation, rel: extends, provenance: asserted}
  - {dst: 27-note-dreamer, rel: revises, provenance: asserted}
  - {dst: 19-between-tiers, rel: depends_on, provenance: asserted}
version: 1
---

# Note Candidates v2

## 0. What is true on 2026-09-08 (read back, not recalled)

- The note tier has **two** Dreamer passes. `note-candidates` (per room,
  fires when a room has new messages and its watermark is an hour old; reads
  the new window plus the notes index; stages, then creates or — on a
  matching anchor date — revises) and `note-curation` (per workspace, daily
  at 16:00 UTC; selects passed anchors and due `revisit_at`s, gathers 40
  messages of evidence per note in code, asks `open / closed / expired /
  moot`, retires only what code can ground). The entity-resolution pass and
  the curator's merge half were deleted the same day; R-75 records why.
- **Zero notes exist in production.** The tag-path tools have never fired;
  `note-candidates` ran on two rooms on 2026-09-07 and both were true
  negatives. Every claim below about behaviour is therefore a design
  position, not a measurement, and §7 says what would be measured first.
- The create path is **date-anchored only**: the objective asks for an
  `anchor_date`, and the gate compares nothing else. A dateless note can be
  born only through the tag path, and once born it never retires — strikes
  climb to a ceiling and are logged, not acted on.
- The passes inject **no memory**. Honcho appears in the tier only as a
  write target (discharge, off).

## 1. The operator's reframe

Two directions, stated in the operator's words and confirmed against the
code above:

1. **From the conversation towards the notebook** — look at the recent
   window, look at what is already written down, decide what should exist.
2. **From the notebook towards the conversation** — look at what is written
   down, decide what is duplicate, settled, or dead.

And two positions that follow from thinking about direction 1 as a loop that
keeps running:

- **Enrichment belongs at write time.** A note about «what game next» is
  useless as a bare thread; its value is the group's preferences across
  months, which live in memory and not in the window. When the loop decides
  a note should exist (or change), it should ask memory what this thread is
  about and write that in.
- **Time is not the judgement.** Whether a thread has closed is something
  the conversation says; a loop that is already reading the conversation
  knows it without a second pass consulting a calendar.

This node accepts both, with one boundary held and one gap named.

## 2. The division: what the conversation says vs what it does not

**Direction 1 is one recursive pass — `note-candidates` — and it owns every
lifecycle event the conversation CARRIES: create, update, and now close.**
Today a settling message («booked it») in the window can only ever produce
an update; the close waits for the curator's next slot, a second model call
over evidence the first pass already had in front of it. That is a pass
re-deriving what another pass just saw. The create pass may propose
`close` for a standing note, citing a message id in its own batch, under
the identical grounding rule the curator applies (the id must be one this
run rendered; nothing else is a citation).

**Direction 2 is the curator, and it owns what the conversation DOES NOT
carry: silence.** `note-candidates` reads the window after the watermark
and fires only on lag. A thread nobody mentions again is a thread the loop
never sees again; its only remaining signal is the calendar. «Dinner on the
9th, who books» with nothing said after the 5th is zero signal in the
record and one fact in the clock. This is not hypothetical: the deployed
config once said in its own words *«the notebook only grows»*, which is
exactly the failure of a lifecycle that can add and change but never
subtract. Subtraction needs a signal from outside the conversation, and in
a friends group an arrangement is the one thing that natively carries one.

So: **the anchor date becomes optional; the clock does not become
ignorable.** The held position is *date optional, time load-bearing for
absence*, and it holds unless §7's measurement shows threads almost never
fall silent unclosed — at which point the curator's expiry collapses into a
strike policy and this section is rewritten.

## 3. Dateless and living notes

The operator's headline case is a thread with no date and no end: the
room's running argument about what to play. It is a note in the notebook
sense of 33 §3 — the home for what must survive across turns — and today
it cannot be created by the pass that exists to create notes.

- **Anchor by topic.** A candidate may carry `anchor_date` or a topic
  string (the existing `anchor_entity_ids` column, already free text, already
  half of the staging index) and needs at least one — `Note`'s own rule.
- **The gate for a dateless candidate is a judgement, not a key.** A date is
  comparable in code; «is this the same thread as *Game night*» is not. The
  model already sees the notes index; for a dateless candidate it must name
  the standing note it extends or say none does, and code refuses a title
  that duplicates a standing title exactly. This is the one place the model
  decides update-vs-create, and the reason it is tolerable is the
  asymmetry: a wrong update appends a revision to a living note (visible,
  reversible, provenance-stamped); a wrong create makes a duplicate the
  next round can close. Neither loses content.
- **Lifecycle of a living note is *update until silence*.** It closes only
  if the conversation closes it (§2, direction 1) or if it falls silent
  long enough for the curator's strike policy to retire it as `moot` —
  which means the strike ceiling finally gets a consumer (§4).

## 4. What the curator becomes

Retire-only, as now, with its selection widened to the one case it cannot
handle today:

- passed anchors and due `revisit_at`s — unchanged, evidence-grounded;
- **dateless notes past the strike ceiling** — currently observed and
  logged, never acted on. A note woken `MAX_DATELESS_STRIKES` times with no
  tag-path progress goes to the model with its evidence window under the
  existing `moot` rule: retire only if the window shows the room moved on,
  else stand and reset nothing. Silence alone still retires nothing; silence
  plus a model reading the room's later traffic may.

The curator never writes a body. Merge stays gone; duplicates are handled
where they are first seen (33 §4's gate, now §3's judgement for dateless).

## 5. Enrichment: memory is material, never proof

Two stages inside `note-candidates`, because the question to ask memory is
not known until the model has named the thread:

- **Stage A (exists).** The model reads the window and the index and
  proposes: title, description, body (the thread as the room said it: who
  suggested what, what was ruled out and why), anchor, cited message ids.
  Code adds the involved `person_id`s — it knows who spoke in the batch and
  the model is not asked to guess names.
- **Stage B (new).** For each candidate that survives the gate, code asks
  Honcho ONE dialectic question — «about *<title/description>*, what do
  *<these people>* prefer, avoid, or have said over time» — through the
  existing `MemoryStore.chat` verb, and appends the answer to the body as a
  labelled paragraph: *from memory, as of <date>*. Honcho does the
  cross-month synthesis; this tier does not spend a second model call
  re-doing it. The update branch runs Stage B again, so a living note's
  memory paragraph is refreshed each time the thread moves and staleness is
  bounded by the thread's own cadence.

**The boundary.** The memory paragraph is content the reader (Nora, in a
turn; the proactive adapter, later) can use. It is never evidence. A
`closed` from either pass still cites a rendered message id; `expired` /
`moot` still need the window to span the moment. If memory says «they seem
to have settled on X», that is a hint to widen the window or a reason for
the proactive adapter to ask — never a retirement. This is what keeps the
tier from inheriting memory's nature: Honcho re-infers and cannot cite; a
note that closed on Honcho's word would be a note that changes its mind.

**Cost bound.** Stage B is per surviving candidate (a few a day at most),
never per tick and never per note per tick. Dialectic latency is off the
tag path entirely. `representation`/`peer_card` are not injected up front
for every speaker: topic-agnostic profiles for eight people per fire is
the wrong grain, and the question is better asked once the topic exists.

## 6. What does NOT change

- One root span per run; Stage B is a child span with the question, the
  peer set and the answer's length — a Honcho call a trace cannot see is a
  cost nothing measures.
- The passes stay **tool-free judges** with inputs assembled in code.
  `search_memory_workspace` is workspace-safe and stays out: a judge handed
  a search tool becomes a researcher, and 33 §4.1 already paid for that.
- Three gates, R-27 three states, provenance on every revision
  (`established_by`, instruction hash, model). A memory-enriched revision is
  distinguishable from a tag-path one at the row.
- The proactive adapter (33 §8, #103) remains the only thing that speaks.

## 7. Measure before build

Nothing above is worth a line of code until one real note exists. In order:

1. **Positive control.** A human posts an explicit pending arrangement in
   the test room; `candidates_enabled` on; the hourly fire creates a note.
   Read the Langfuse trace and the `note` row.
2. **Close-from-candidates.** A settling message in the same room; the
   next fire should be able to close it. Today it can only update — that
   is the first change, and it is the smallest.
3. **A dateless thread.** «What game next» across two or three fires.
   Today: nothing is created. That is the second change.
4. **Only then Stage B**, and only if the created bodies read as useless
   without the group's history — measured by reading them, not assumed.

The reading that reorders this: if the positive control shows the model
proposing nothing for a plain dated arrangement, the objective is the
problem and none of the above applies yet.

## 8. Decisions this node records (operator, 2026-09-08 session)

| position | holds unless |
|---|---|
| Direction 1 is one recursive pass; it may close what it sees settle | a close-from-candidates lands wrongly in a way the curator's identical grounding rule would have caught — then the rule, not the placement, is what to fix |
| Date is optional; a topic string is a valid anchor | dateless duplicates pile up faster than the curator's strike policy retires them |
| Time stays load-bearing for absence; the curator keeps expiry | §7 shows threads almost never fall silent unclosed |
| Enrichment is write-time, through Honcho `chat`, appended and labelled | Stage B's answers read as noise, or its cost per candidate is not the few-a-day this node assumes |
| Memory is never a citation | — this one has no reversal condition; it is the tier's identity |

## Status / Checkpoint

**2026-09-08, §7 step 1 executed.** The positive control fired at 02:19 HKT
on the operator's own dated arrangement and returned an empty envelope; the
trace showed the prompt, not the pipeline. The scenario suite
(`evals.run --suite note_candidates`, issue #118) was built and run against
the deployed model (`nora-dreamer`, reasoning none, 3 samples per case):

| prompt | precision | recall | majority-pass |
|---|---|---|---|
| as deployed (`c9a73442`) | 1.00 | 0.60 | 8/10 |
| objective v2 only | 1.00 | 0.87 | 10/10 |
| judge doctrine only | 1.00 | 0.67 | 9/10 |
| both (shipped) | 1.00 | 0.93 | 10/10 |

No variant wrote a false note on any negative sample. Objective v2 and
doctrine v2 are the shipped prompt. Steps 2–4 remain as written.

Design as of this node's first version: no code, no objective change, no
schedule change. Issue #118 tracks the build, sequenced behind §7. Related: #98 (create path, shipped), #99/#102 (curator, shipped),
#101 (discharge, shipped, off), #103 (proactive adapter, design only),
#100 (entity resolution — resolved by removal, 2026-09-08).
