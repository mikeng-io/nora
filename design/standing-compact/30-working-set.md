---
type: design
title: "Working set — a structural field for options found and not yet given the room"
description: "#445's next step: objective v8 (PR #518) took the one valid residue case 0/5 -> 1/5 by naming the finding-and-usefulness test in prose; it now carries a single unsent FACT but rarely the unsent OPTIONS a search returns. Proposes a second, small, structured field beside the 1200-char paragraph, with its own budget, provenance, and a render-time expiry so an expired item is absent, not stale."
tags: [nora, standing, debrief, working-set, residue, 445]
timestamp: '2026-09-25'
edges:
  - {dst: 00-intent, rel: extends, provenance: asserted}
  - {dst: 20-verification, rel: depends_on, provenance: asserted}
---

# Working set

## Where this picks up

00-intent's claim 3 — showing the fold the turn's tool results at a size that
carries them lets it keep unsent findings — does not yet hold
(20-verification §5, residue 0/9 on both arms). #445's study
(`/private/tmp/claude-501/study-445/FINDINGS.md`) named the primary cause:
the objective's derivability test, closing line and only example still
defined a position as "concluded, committed to, or still holding open", with
no path for "found". PR #518 (objective v8) fixed that prose and, in the same
round, fixed `debrief_render.py`'s tool-result rendering (Python-repr
stripped, URLs deduped) and restated the corpus case to name a specific,
checkable property. Measured on the merged head: residue moved from 0/5 to
1/5. **The fold now writes one unsent fact tied to an open plan** ("it's a
Sunday, weekend pricing applies") **but the draws that fail still write only
the two restaurants she already sent — never the others the search
returned.** A fact is one clause competing inside a paragraph that is
rewritten whole every turn, ranked below "concluded or committed" in the
objective's own consolidation order (10-changes §5); a set of options is
several items of the *same* rank, and the paragraph shape gives the model no
way to hold more than one without either naming all of them in one crowded
sentence or dropping to zero. This node is the "does the working set need
its own field?" question 20-verification §5 left open, answered concretely.

## 1. Shape

A second array field on `Standing`, not a second block of prose:
`working_set: tuple[WorkingSetItem, ...]`, `WorkingSetItem = {what: str,
source: str, found_at: int, bears_on: str | None}`. `found_at` is epoch ms,
same clock as `Standing.updated_at` — stamped by the write path from the
fold's own instant (`domain/standing.py`'s existing rule: "there is no second
clock"), never model-supplied, for the same reason `standing_revision`
doesn't trust a model-authored timestamp anywhere else.

**Own budget, separate from the 1200-char paragraph, on purpose.** The
paragraph's cap is read against CJK density and is already "a position that
cannot be stated in 1200 characters is a summary, not a position"
(`domain/standing.py`). A working set is not prose under that same argument —
it is N short, independently droppable strings, and folding it into the same
1200 chars means every option found competes with "concluded or committed"
clauses for the same budget under a ranking that puts it last (10-changes
§5's found > room-said, but concluded/committed > found). That is very
likely part of why 1/5 carries a fact and 0/5 carries a second option: the
model has one budget and one rank order, and the paragraph shape only ever
lets it keep the option that also reads as the most position-like sentence.
A separate small budget removes that competition rather than re-ranking it.

Proposed sizes (starting points, tunable, not load-bearing): operating cap
`[standing] working_set_max_items` = 5, `[standing] working_set_max_chars`
(rendered JSON, all items) = 600 — refuse-and-consolidate on overflow,
exactly like the body (`STANDING_BODY_CEILING_CHARS` / `DEFAULT_STANDING_MAX_CHARS`
pattern in `domain/standing.py`), never a silent truncation. A DB-level
absolute ceiling, `WORKING_SET_CEILING_BYTES`, sits above the operating cap
the same way `STANDING_BODY_CEILING_CHARS` (4000) sits above 1200: the
config value is the judgement call, the ceiling is the backstop against a
config typo.

## 2. Lifetime

Three ways an item stops being current, matching the task's own framing:

| cause | who acts | when |
|---|---|---|
| TTL | render, deterministic | `found_at` older than a bound (proposed: 48h, the same order as DEC-203's two-day past-anchor lapse) |
| the plan it `bears_on` closes | the fold, on its next pass | the fold rewrites the array the same way it rewrites the paragraph, and drops an item whose plan settled |
| given to the room | the fold, on its next pass | the delivered reply now contains it, so it fails the derivability test the same way a stated fact does today |

**Fail-closed, by construction, not by a sweep.** The render function that
builds `<standing_working_set>` filters on `now - found_at` before it builds
the block — the same place `render_standing` already computes `written` as
an age at render time rather than trusting a stored label. An item past its
TTL is never emitted: it does not render as stale, it does not render at
all, identical to a room with an empty working set. No new deterministic
sweep job is added; #222's read-side-TTL repair was scoped to the whole
chain and explicitly deferred (00-intent, "not in scope"), and this is that
same property, narrowly, for one field. **Reversal condition:** if a live
reading shows items surviving past their TTL for days because a quiet room's
next fold is far off, a real sweep can be added later — the render-time
filter still holds in the meantime, so nothing is unsafe while that is
decided.

The fold still needs its own prior to prune correctly, so `Standing`'s
`read`/`read_as_of` (`nora/infra/sql/standing.py`) return `working_set`
exactly as they already return `body` and `fold_count` — no new store method.

## 3. Write path

**Schema.** The fold's JSON output gains one key beside `standing`:

```json
{"standing": "<paragraph>",
 "working_set": [{"what": "...", "source": "...", "bears_on": "..."}]}
```

`found_at` is not asked of the model; the write path stamps it. `bears_on`
is nullable — filled only when an open plan in the paragraph already names
what the option is for.

**The objective tells it what a working-set item IS, not a menu of cases.**
Per `context-framing-beats-capability-listing` and the doctrine
`judgement-not-a-menu`: no bullet enumerating "restaurants found by
web_search", "other candidates from place_info", "alternate time slots" —
that shape is exactly what the Relevance Gate regression showed costs
accuracy. One sentence, same register as the existing finding bullet
(standing-debrief.md v8): *"When a step found more than one thing that could
answer the same question and you only gave the room one, the rest are a
working set — each still worth offering, each with what it is, where you
found it, and what it's for if you know."* That is the finding bullet's own
test ("found ... not yet given the room ... still useful to someone here"),
applied per-item instead of folded into one paragraph clause — not a new
test, a second place the existing one is allowed to fire more than once per
turn.

**Provenance per item.** `source` names the step the item came from,
constrained to the same `<steps>` the model is already shown (`what I
called and what came back`) — not a paraphrase of the room's question. The
worked example in the objective should show one item's `source` as a literal
tool name plus a short description, the same way v8's finding clause names
its source now.

**Screened like the body.** Every `what`/`source`/`bears_on` string passes
through `nora.agent.dream_screen` before the write, same as `body`
(`standing_render.py`'s docstring: "the sink screens identifiers and
coordinates before the write"). A found phone number or coordinate does not
get a second, unscreened path into the prompt just because it sits in a new
field.

## 4. Read path

**Tag turn and participation turn — full field, adjacent to `<standing>`.**
Both reach the model through `build_prefill` (`nora/agent/prefill.py`);
`nora/app/fire.py` calls the same function for the participation occasion.
A new `_standing_working_set_block(ctx)` sits immediately after
`_standing_block(ctx)` and before the transcript, keeping the read order
"here is my position, here is what I have not told you, here is what has
been said since." A new block type in `block_types.py`,
`standing_working_set`, with per-item fields `source`, `found_at` (rendered
as age, the same `nora.domain.prompt.age` used elsewhere), `bears_on`. Its
description tells the reader what to DO with it, the way `<steps>`'s own
type description already states a fact about its shape rather than
instructing a behaviour case-by-case: *"options you found and did not send.
If this room asks for more, or pushes back on what you already gave them,
you can offer one of these without a new search — say in your reply that you
are offering it, and it is not a promise until you do."* That is the
"they asked for more -> offer the others" affordance stated as what the
block IS, not a conditional rule about when to use it.

**The fold's own input needs the prior too.** `nora/engagement/debrief_render.py`
gains a render for `<standing_working_set_prior>`, symmetric with
`<standing_prior>`, so the fold can see what it is revising rather than
re-deriving the set from zero every turn — the same reason the paragraph is
"re-judge the prior", not "write a fresh one."

**Proactive decide — deliberately not wired here.** `nora/language/proactive.py`
reads `standing.body` collapsed to one line per room, across every room in a
unit (`_standing_lines`). A structured, multi-item field does not fit a
one-line-per-room digest without either truncating to uselessness or
blowing the unit-wide prompt budget; see §7.

## 5. Storage

**No open PR touches this.** `gh pr list --state open` on 2026-09-25: #529
(instagram-bridge), #528 (note-candidates), #527 (notes S0) — none touch
`nora/infra/sql/standing.py` or its migrations. `git log -5 -- nora/infra/sql/standing.py`
ends at `db0d8b6e` (#489 review fixes, already on master). Migrations head
is `0064_proactive_open_note`.

**Columns**, added to both `standing` (the head) and `standing_revision`
(the append log), same pattern the `source` column followed in migration
0062 — carried on both tables so a revision records a value the head could
actually have held:

```
working_set  jsonb  NOT NULL  DEFAULT '[]'::jsonb
```

CHECK constraints mirroring `standing_revision_body_fits_the_ceiling_check`:
`jsonb_array_length(working_set) <= <item ceiling>` and
`octet_length(working_set::text) <= WORKING_SET_CEILING_BYTES`. No backfill
— every existing row predates the field, and `'[]'::jsonb` is both the
default for new rows and the correct reading of every row that predates the
column, the same argument migration 0059 made for not backfilling history
that never existed.

**Migration**: `0065_standing_working_set.py`, `down_revision =
"0064_proactive_open_note"`.

## 6. Measurement

Per the nora-eval skill, **an eval confirms a fix; it never finds one.**
This is case (a): a fix for the named failing case. Before spending a model
call:

- **Offline, no model call.** `--print-input residue-restaurants-found-two-sent`
  on `origin/master` and on this design's branch, each in its own eval
  worktree, diffed — proves the objective's new sentence and JSON-schema line
  reach the fold's prompt. Separately, a direct fixture call to the new
  `render_standing_working_set` against a `Standing` built with a non-empty
  `working_set` (no DB, no model) proves the same field reaches
  `build_prefill`'s tag-turn output as `<standing_working_set>` — the second
  half of "reaches the fold **and its readers**", since print-input only
  covers the fold's own input.
- **Arm definition.** Master = current merged head (objective v8, single
  paragraph). Branch = this design: schema + render + objective addendum.

**The confirming run**, cases from the existing corpus
(`evals/corpora/standing.yaml`) and 20-verification §1's table:

| case | n | why |
|---|---|---|
| `residue-restaurants-found-two-sent` | 5 | the one anchored, valid residue case; matches the sample size PR #518 already measured against (1/5) |
| `recap` (control) | 3 x 2 cases = 6 draws | cheapest, highest-value regression check — must stay 6/6; a new block is exactly the kind of change that could make a position read like a transcript again |

Stage 1 total: 11 draws x ~211s ≈ 39 minutes — under the skill's 60-minute
single-run bound. **Run stage 1 first and read it before spending more**: if
residue does not move, the field is not the fix and the schema/render/config
changes revert per §7 rather than being tuned further blind (the same
discipline 00-intent applied when one added objective sentence "moved
nothing" and was reverted rather than iterated on again).

If stage 1 shows movement, stage 2 confirms no regression on the claims this
bundle already shipped: `carry` (4 cases) and `stale` (4 cases) at the
corpus's existing n=3 — 8 cases x 3 = 24 draws x ~211s ≈ 84 minutes, itself
over the 60-minute bound, so it runs as its own sitting per the skill's "cut
the selection or ask," not appended to stage 1.

## 7. What this does not do

- **Does not touch the 1200-char paragraph's own budget or consolidation
  order.** Concluded/committed still outranks found still outranks
  room-said inside `body`; this field removes MULTIPLE found-options from
  ever entering that competition, it does not change the ranking.
- **Does not reach proactive decide's cross-room digest.** `_standing_lines`
  stays a one-line-per-room summary of `body` alone (§4). A future node
  could add a short existence flag ("N unsent options") if a live reading
  shows proactive turns undervalue them without it — not built here.
- **Does not generalize #222's read-side repair.** Only this field gets a
  render-time TTL; the paragraph's own revision-chain provenance and repair
  trigger stay open, tracked at #222.
- **Does not add a deterministic sweep.** Expiry is render-time filtering
  plus the fold's own next-pass rewrite (§2); no new background job.
- **Does not let an item stand as a commitment.** Screened like the body
  (§3), and its own block description says explicitly that offering one is
  something the reply has to do, not something holding it already did.

**Reversal condition** (restated from §6): stage 1 shows residue does not
move, or moves and then a live reading afterward shows
`nora_standing_overflow_refusals_total`'s working-set-scoped equivalent
firing constantly (the set crowds its own cap the way 20-verification §3
already watches for on the body) — either drops this field: the migration
rolls back, the render and block type are deleted, and #445 goes back to
tuning the objective's prose, which is the path PR #518 already showed
moves something.

## Status / Checkpoint

2026-09-25: proposed. Not built, not measured. Depends on 00-intent's claim
3 and 20-verification §5's open residue question; answers "does the working
set need its own field" concretely enough to build and run stage 1 of §6
against.
