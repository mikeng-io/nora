---
type: design
title: "Open plans: a dated arrangement on Standing leaves by its date or by an explicit close, never by omission"
description: "Standing folds drop open, dated plans (design/group-situation/10: at least 10 of 206 production folds, 5 substantive, the same afternoon plan lost twice). This node adds a third field to Standing, open_plans, which code carries forward across folds. A plan leaves when its day has passed (render-time, deterministic) or when a fold closes it by id with a reason. A fold that leaves it out does not remove it. Measured by five standing-corpus carry cases taken from the real drops, before and after."
tags: [nora, standing, fold, plans, carry, measurement]
timestamp: 2026-09-26
status: proposed
edges:
  - {dst: 30-working-set.md, rel: extends, provenance: asserted}
  - {dst: 20-verification.md, rel: measured_by, provenance: asserted}
  - {dst: ../group-situation/10-standing-drops-plans.md, rel: motivated_by, provenance: asserted}
---

# Open plans

## Where this picks up

`design/group-situation/10-standing-drops-plans.md` measured production
folds dropping plans that were still open and still ahead of them: at least
10 of 206, 5 of them substantive. The character budget was not the cause.
What fits is the fold's anti-recap test, which reads a plan a member stated
as a recap while the plan is still in the 40-message transcript window, plus
«RE-JUDGE the whole paragraph, every turn». Together they let a plan be lost
to a re-voicing even on a fold where nothing touched it. Notes did not
reliably catch it, and the one note that did was dated to another event.

The working set (`30-working-set.md`) has the same weakness by design: the
fold rewrites the array, and an item it leaves out is gone. That is right for
a finding, which is harmless to lose. It is wrong for a plan, where losing it
is the defect.

## 1. Shape

A third field on `Standing`, beside `body` and `working_set`:
`open_plans: tuple[OpenPlan, ...]`.

`OpenPlan = {id: str, what: str, on: date, at: str | None, first_seen: int}`

- `id` is short and stable (`p1`, `p2`, …), assigned by code when the plan
  first appears, never by the model. It is how a later fold refers to the
  plan.
- `what` is one line, in the room's language: what is planned and who is in
  it, as far as it is known.
- `on` is the plan's day, as an ISO date. It is required: a plan with no day
  is an undecided matter, and the paragraph already holds those.
- `at` is an optional time of day (`HH:MM`).
- `first_seen` is epoch ms, stamped by the write path from the fold's own
  instant (`domain/standing.py`: «there is no second clock»).

It has its own budget, separate from the paragraph's and the working set's.
Starting points, not load-bearing: `[standing] open_plans_max_items = 6`,
`open_plans_max_chars = 600` (rendered JSON). An oversized set is refused as
a whole and the prior is kept, exactly as the working set does. A DB CHECK
ceiling sits above the operating cap.

## 2. Lifetime: the brake is structural

| how a plan leaves | who acts | when |
|---|---|---|
| its day has passed | render, deterministic | `on` is before today in HKT; the item never renders, the same fail-closed filter the working set's TTL uses |
| the room settled, cancelled, moved or replaced it | the fold, **explicitly** | the fold lists it under `closed_plans` with its `id` and a one-line `why` |
| the fold left it out | **nobody: it stays** | code carries every prior plan that was not closed and has not expired |

The third row is the fix. A fold still re-judges everything, but it can only
remove a plan by saying so. An omission is carried and counted
(`nora_standing_open_plan_carried_total`), so the rate at which the fold
would have dropped plans stays measurable after it can no longer do it.

A fold that returns a plan under an existing `id` revises it (a moved day, a
new time, a new member). A `why` is screened and kept on the revision row,
so a close is auditable.

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
   the fold's own day, is refused and counted (`..._open_plan_refused_total`
   with a reason). A plan whose day has already passed is not open.
2. Merges: prior plans, minus `closed_plans` ids, updated by the returned
   items with those ids, plus new items with new ids. Every prior plan the
   fold did not mention is carried unchanged.
3. Screens each `what` and `why` through `nora.agent.dream_screen`, as the
   body and the working set already are.
4. Checks the budget. On overflow, the whole new set is refused and the prior
   kept. An unknown `id` in `closed_plans` is ignored and counted.

**Both folds write it.** The tag debrief and the watermark rollup
(`nora/ingest/stages/standing_event.py`) share the validator, as they do for
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

Migration `0066_standing_open_plans`: `open_plans jsonb NOT NULL DEFAULT
'[]'` on `standing` and `standing_revision`, with a CHECK on item count and
bytes. `read` and `read_as_of` return it beside `working_set`. An operator
edit through `write_if_unchanged` may not change it, the same rule the
working set has.

## 6. Measurement

**Before any build, the cases.** Five `carry` cases in `evals/corpora/
standing.yaml`, one per substantive drop in the study, each anchored on the
real tag turn whose fold dropped the plan. The judge reads the new position,
the working set and the open plans, so a plan held in the new field counts
as held.

- **Before arm (master):** these reproduce the drop, or they are not the
  defect.
- **After arm:** the five pass, and the existing 15 cases do not regress. In
  particular `recap` must not regress, since a field that holds room-stated
  plans is a new place for recap to creep in.

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
  calendar. Overlap is measured after it ships, not designed away first.
- It does not fix note-candidates writing a plan under another event's date.
  That is its own eval case (`note_candidates.yaml`), not this field.
- It does not wire the proactive pass (§4).

## Open questions

- Should a new plan cite the message row that stated it? That would ground
  it the way the working set's `source` grounds a finding. It is left out of
  v1 because the rollup's rows and the debrief's rows carry different
  handles.
- The 6-item cap is a guess. It is read back against the first week's live
  counts before anyone tunes it (`never-size-a-ceiling-on-n-1`).
