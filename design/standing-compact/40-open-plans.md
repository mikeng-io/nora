---
type: design
title: "Open plans: a dated arrangement on Standing leaves by its date or by an explicit close, never by omission"
description: "Standing folds drop open, dated plans (design/group-situation/10: at least 10 of 206 production folds, 5 substantive, the same afternoon plan lost twice). This node adds a third field to Standing, open_plans, which code carries forward across folds. A plan leaves when its day has passed (render-time, deterministic) or when a fold closes it by id with a reason. A fold that leaves it out does not remove it. Measured by five standing-corpus carry cases taken from the real drops, before and after."
tags: [nora, standing, fold, plans, carry, measurement]
timestamp: 2026-09-26
status: withdrawn
edges:
  - {dst: 30-working-set.md, rel: extends, provenance: asserted}
  - {dst: 20-verification.md, rel: measured_by, provenance: asserted}
  - {dst: ../group-situation/10-standing-drops-plans.md, rel: motivated_by, provenance: asserted}
---

> **Withdrawn 2026-09-26.** The operator ruled that remembering a dated plan
> is the note tier's job (design/dreamer-agent-loop/42), not Standing's, so
> Standing gets no `open_plans` field. The build (PR #565, never merged) was
> closed; its one fix that stands alone, `scripts/standing_edit.py` keeping
> `working_set` on an operator edit, landed separately. On today's fold the
> drop this design answered was 1 substantive in 79 revision pairs
> (design/group-situation/10). The rest of this node is the record of the
> proposal, not a plan.

# Open plans

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


## Where this picks up

`design/group-situation/10-standing-drops-plans.md` measured production
folds dropping plans that were still open and still ahead of them: at least
10 of 206, 5 of them substantive. The character budget was not the cause.
Most of those drops predate the fold rewrite of 2026-09-24 (7fdb24b4), and
two came from the rebuild-after-N-folds path that commit deleted. On today's
fold the study saw one substantive drop and one minor one in 79 pairs, and
the substantive one is the drop behind the wedding turn that started this.
What fits the one on today's fold is the anti-recap test, which reads a plan
a member stated as a recap while the plan is still in the 40-message
transcript window, plus «RE-JUDGE the whole paragraph, every turn». Notes did
not reliably catch it, and the one note that did was dated to another event.

The working set (`30-working-set.md`) has the same weakness by design: the
fold rewrites the array, and an item it leaves out is gone. That is right for
a finding, which is harmless to lose. It is wrong for a plan, where losing it
is the defect.

## 1. Shape

A third field on `Standing`, beside `body` and `working_set`:
`open_plans: tuple[OpenPlan, ...]`.

`OpenPlan = {id: str, what: str, on: date, at: str | None, first_seen: int}`

- `id` is short and stable (`p1`, `p2`, …), assigned by code when the plan
  first appears, never by the model, and **never reused** in the room: the
  next id is one past the highest ever issued in the room's revision chain,
  so a fold closing a stale id can never remove a different plan. It is how
  a later fold refers to the plan.
- `what` is one line, in the room's language: what is planned and who is in
  it, as far as it is known.
- `on` is the plan's day, as an ISO date. It is required: a plan with no day
  is an undecided matter, and the paragraph already holds those.
- `at` is an optional time of day (`HH:MM`).
- `first_seen` is epoch ms, stamped by the write path from the fold's own
  instant (`domain/standing.py`: «there is no second clock»).

It has its own budget, separate from the paragraph's and the working set's.
Starting points, not load-bearing: `[standing] open_plans_max_items = 6`,
`open_plans_max_chars = 600` (rendered JSON). **Unlike the working set, the
budget never refuses a carried plan.** It refuses only the NEW items that do
not fit, and counts them. Refusing the whole set would wedge it: once full,
no new plan could enter until one expired. A DB CHECK ceiling sits above the
operating cap.

## 2. Lifetime: the brake is structural

| how a plan leaves | who acts | when |
|---|---|---|
| its day has passed | write and render, deterministic | `on` is before the day in Asia/Hong_Kong: the write path removes it (so it frees its cap slot) and the render never shows it (so a quiet room's stale row is still safe) |
| the room settled, cancelled, moved or replaced it | the fold, **explicitly** | the fold lists it under `closed_plans` with its `id` and a one-line `why` |
| the fold left it out | **nobody: it stays** | code carries every prior plan that was not closed and has not expired |

The third row is the fix. A fold still re-judges everything, but it can only
remove a plan by saying so. An omission is carried and counted
(`nora_standing_open_plan_carried_total`), so the rate at which the fold
would have dropped plans stays measurable after it can no longer do it.

A fold that returns a plan under an existing `id` revises it (a moved day, a
new time, a new member). A returned item with no `id` is first matched
against existing plans on (`on`, whitespace- and case-normalised `what`), so
a fold that re-sends a plan without its id revises it rather than
duplicating it. A close's `id`, `what` and screened `why` are kept on the
revision row (`closed_plans`, §5), so a close is auditable.

**An operator can close a plan.** `write_if_unchanged` (the operator edit
path) may remove plans, recorded as a close with the operator as its `why`,
but may not add or edit one. A misdated or invented plan (the note tier
shows the producer can misdate) is otherwise sticky until its date, and the
operator needs a way out that does not wait for a fold.

## 3. Write path

The fold's JSON gains two optional keys:

```json
{"standing": "...", "working_set": [...],
 "open_plans": [{"id": "p1", "what": "...", "on": "2026-09-27", "at": "14:00"},
                {"what": "...", "on": "2026-10-17"}],
 "closed_plans": [{"id": "p2", "why": "..."}]}
```

An item with no `id` is new. The write path:

1. Parses `on` as a date. A date the model cannot write as ISO, or one before
   the fold's own day in Asia/Hong_Kong, refuses **that item only** and is
   counted (`..._open_plan_refused_total` with a reason). A plan whose day
   has already passed is not open.
2. Merges: prior plans, minus `closed_plans` ids, updated by the returned
   items with those ids, plus new items with new ids. Every prior plan the
   fold did not mention is carried unchanged.
3. Screens each `what` and `why` through `nora.agent.dream_screen`, as the
   body and the working set already are.
4. Checks the budget: carried and revised plans always stay; new items that
   do not fit are refused and counted. An unknown `id` in `closed_plans` is
   ignored and counted.

**Both folds write it.** The tag debrief and the watermark rollup
(`nora/ingest/stages/standing_event.py` (deleted 2026-10-03, DEC-277)) share the validator, as they do for
the working set. Unlike the working set, the rollup may ADD plans, because
most plans are stated in stretches nobody addressed Nora in (drop 3 of the
study was one).

**The objective says what an open plan is, not a menu.** One paragraph in
`standing-debrief.md`, in the register of the working-set paragraph: an open
plan is an arrangement the room, or I, still mean to carry out, with the day
it happens. It stays until that day has passed or someone settles, cancels or
moves it. To let one go, close it by its id and say why. It is not a recap
even while the transcript still shows it, because the transcript moves on
and this does not.

## 4. Read path

- **Tag and participation prefill:** `<standing_open_plans>`, straight after
  `<standing>` and `<standing_working_set>`, via the one `build_prefill`.
  Each line carries the day as a date *and* its distance from now
  («Sun 2026-09-27, in 1 day»), because the judgement it feeds turns on when.
- **The fold's own input:** `<standing_open_plans_prior>` with ids, unfiltered
  by date (the fold closes what it sees has happened).
- **Not the proactive pass in this node.** The proactive decider is the
  natural consumer of dated plans (step 3 of `design/group-situation`), but
  wiring it there is a behaviour change with its own measurement.

## 5. Storage

Migration `0067_standing_open_plans` (`0066` is `instagram_summons`):
`open_plans jsonb NOT NULL DEFAULT '[]'` on `standing` and
`standing_revision`, with a CHECK on item count and bytes, and
`closed_plans jsonb NOT NULL DEFAULT '[]'` on `standing_revision` only. `read` and `read_as_of` return it beside `working_set`. An operator
edit through `write_if_unchanged` may not change it, the same rule the
working set has.

## 6. Measurement

**Before any build, the cases.** Five `carry` cases in `evals/corpora/
standing.yaml`, one per substantive drop in the study, each anchored on the
real tag turn whose fold dropped the plan. The judge reads the new position,
the working set and the open plans, so a plan held in the new field counts
as held.

- **Four of the five drops predate today's fold.** Drops 1 to 4 (09-22) ran on
  the fold before 7fdb24b4 (09-24), and drops 1 and 2 were its
  rebuild-after-N-folds path (`fold_count` reset to 1), which that commit
  deleted. Only drop 5 (09-25) is known to fail on today's fold. **The before
  arm decides what this node is for:** a case that passes on master is a
  regression guard, not evidence of the defect. If none reproduce except
  drop 5, the build is sized to one confirmed failure, and that is stated
  before it merges.
- **Before arm, measured 2026-09-26** (`python -m evals standing --judge
  --sample 3`, norty, eval source newest row 2026-09-25 20:56, arm 234ee8e5 =
  master's fold with these cases): drops 1, 2 and 3 PASS 3/3 each, so today's
  fold does not reproduce them and they are regression guards. Case 4 (the
  control) 1 pass / 2 fail. Case 5 (the wedding meet-up and the stops before
  dinner) 2 pass / 1 fail. **The confirmed defect on today's fold is one dated
  case, dropped in about one draw in three.** The build is sized to that; its
  after arm needs a higher n on case 5 than 3 to show a change.
- **Case 4 is a control, not a target.** It is her own open question with no
  day, and §1 keeps an undated matter in the paragraph, so the field cannot
  hold it. The after arm is read as 4 targets plus 1 control.
- **After arm:** the targets that reproduced now pass, and the existing
  standing cases do not regress (15 in the corpus, 11 of them runnable; 4
  are parked). In particular `recap` must not regress, since a field that
  holds room-stated plans is a new place for recap to creep in.

**Deterministic tests**, each revert-proved: an omitted plan is carried; an
explicit close removes it; a past `on` never renders; a past `on` in the
fold's output is refused; an overflow keeps the prior; screening applies to
`what` and `why`; the rollup can add a plan; `read_as_of` returns the field.

**Live read-back after deploy:** `..._open_plan_carried_total` > 0 means the
fold is still omitting plans and the brake is catching them. Rows in
`standing_revision.open_plans` for the rooms of the study.

## 7. What this does not do

- It does not stop the paragraph from dropping a plan. The plan survives in
  the field instead.
- It does not dedupe against notes. Notes are the unit's durable open
  questions and survive across rooms; open plans are one room's working
  calendar. When a note and a plan name the same event with different days,
  neither wins: the tag turn sees both, each with its own day and its
  distance from now, and the disagreement is in front of her rather than
  resolved silently. Overlap is measured after it ships.
- It does not fix note-candidates writing a plan under another event's date,
  and that case is **not** in the corpus yet. At the real moment (the wedding
  room, 09-24, a booking on the 17th and a dinner on the 27th in one batch)
  the unit already held a note dated the 27th, so a correct pass may either
  create a correctly dated note or update that one. The harness grades a
  date only on `create`, and `either` grades nothing about the date. The
  case needs a grader that accepts «a create dated 2026-09-27, or an update
  of the 27th's note» before it can be added without failing a correct
  answer.
- It does not wire the proactive pass (§4).

## Open questions

- Should a new plan cite the message row that stated it? That would ground
  it the way the working set's `source` grounds a finding, and make an
  invented plan detectable. It is left out of v1 because the rollup's rows
  and the debrief's rows carry different handles; the operator close path
  (§2) is the v1 answer to a sticky mistake.
- The 6-item cap is a guess. It is read back against the first week's live
  counts before anyone tunes it (`never-size-a-ceiling-on-n-1`).
