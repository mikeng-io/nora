# GOAL — finish the evaluation rebuild, end to end

Hand this to a session. It is the whole remaining job, in order, with the
verification each step has to pass. Work autonomously; stop only for a real
blocker or a fact only the operator holds.

**Branch:** `feat/evaluation-rebuild` (PR #334). **Repo entry point:**
`AGENTS.md`. **Read first:** `00-overview` → `01-boundaries` → `02-the-rebuild`
→ `04-the-convention` → `05-live-parity`.

---

## The rule everything serves

> **Different evaluation suites. Different input. One Nora.**

`nora/subject/obtain(spec)` composes the graph `nora/__main__.py` runs.
`evals/rebuilt/` drives it. The eval layer composes nothing.

## Where it stands

13 corpora, 150 cases, 126 runnable across four entry points (`gate`,
`participation`, `tag`, `dreamer`). Gate green. The OLD layer still exists and
is still the only thing that has produced a live number.

---

## The discipline — non-negotiable, and it is why this bundle exists

1. **Measure the property, not the spelling.** Every finding in
   `05-live-parity` came from running two things and diffing what reached the
   MODEL. A byte-for-byte check of the corpora was green the entire time.
2. **Falsify every pin before trusting it.** Break the thing, watch the test go
   red, restore. A pin that has never failed is a pin nobody has checked. Three
   pins in this bundle were defeated by a one-line probe.
3. **ERROR is not FAIL and is never PASS.** `Outcome.meets` returns
   `True`/`False`/**`None`**. Any new path must produce `None` when nothing ran.
   This layer has already shipped that hole three times.
4. **Never read a gate through `tail`.** Capture the exit code.
5. **No absence claim without naming what you searched.**
6. **Subagent audit before the delete** (step 8). Opus, adversarial, with a
   falsification mandate — not "review this".

---

## Step 1 — multi-room seeding (unblocks 22 cases)

`nora/subject/knowledge.py`'s `Knowledge` is single-room: one transcript, one
standing. `evals/corpora/proactive_{decide,act}.yaml` name TWO rooms and key
their standing per room, because the decide pass judges a WORKSPACE and picks
which of its rooms to stand in.

- Extend `Knowledge` to carry per-room transcript and standing.
- The refusal is at `evals/rebuilt/entry.py` (`unseeded = [...]`). Delete it
  only when the seam exists, never to make cases run.
- `policy:` must name every room seeded; `obtain` already validates the
  subject's own room and must validate these too.

**Done when:** all 22 cases reach the adaptor and `a11` (the live
duplicate-reminder case, from emperor-60) produces a reading against
`c9dade17`. Its four porting constraints are in the corpus header — honour
them, especially: re-derive `minutes_ago` against the window, never copy it.

## Step 2 — the Honcho write seam, or a recorded refusal

Two participation cases seed `conclusions:`. Honcho DERIVES conclusions from
messages, so there may be no way to state one without substituting the client.
**Decide it and write the decision down.** A recorded "cannot be done, here is
why" is a finished step; a silent skip is not.

## Step 3 — note-candidates grading (8 of 21 cases assert more than is checked)

The corpus header specifies `creates` count, `updates` by title, `anchor_date`,
and `must_cite` against the staged candidate's `message_ids`. `Outcome.meets`
checks only "a create happened". `Message.label` and `Written.rows` exist
SPECIFICALLY to translate a citation into the id the store assigned — and
`evals/rebuilt/runner.py` throws `written.rows` away.

Also: `room_label` / `room_description` reach nothing (they are parked in
`metadata`). Without them `n10b`/`n11b` are byte-identical reruns of
`n10`/`n11`, so the control they form controls for nothing.

## Step 4 — the over-strict and leaky readings (audit, lower priority)

- `entry.py` tag path: `if failed:` fires even when she DID reply and a second
  delivery was guard-refused. Gate on `failed and not hers`.
- `entry.py` dreamer: `or result.samples_failed` makes 1 bad sample of 3 an
  ERROR, stricter than the runtime's own rule (*"one good read is a read"*).
- `report.py`: errored draws enter the timing line as `0.00` and are then
  offered to the reader as evidence of a cache hit. Exclude them.
- `runner.py`: a raw exception string can carry a DSN into the printed report.
- `obtain()` mutates process env during composition; add an `asyncio.Lock` so
  a future parallel runner cannot leave it pointing at a stale database.
- No subject teardown per draw — no `client.close()`, `audit.aclose()`,
  `background.cancel_all()`. At 150 cases × 5 that is 750 pools.
- `pyproject.toml`'s contract is still named *"evals may only reach nora.app
  and nora.testing"* and now admits a third package.

## Step 5 — `[search result]` lines are not room speech

Both layers render them as messages from a member. Production carries them as
tool results. Decide whether the corpora model it or the difference is
recorded, then do that.

## Step 6 — the live run, all four entry points

Tunnel: `ssh -L 20128:localhost:20128 norty`. Key from norty's
`~/workspace/nora/.env`, never printed. `--model` must name a DEPLOYED alias
(`nora`, `nora-gate`, `nora-dreamer` — read them off norty's `config.toml`,
do not assume).

    python -m evals.rebuilt evals/corpora/*.yaml --judge

- `--judge` or the adversarial corpora are vacuous: all 28 expect `reply`, so
  `injection_dan` passes on `"I am free"`.
- Read measurement 0 and the per-draw timing FIRST. A sub-second draw is not a
  reading — `05-live-parity` §4.1 records an unexplained spread and that check
  comes before any sample count is quoted.
- Extend `tests/integration/test_live_parity_gate.py` to the other three entry
  points where the old layer can be driven for comparison.

**Done when:** every corpus has a number, with `subject=` and `judge=` on it.

## Step 7 — the numbers are read, not just produced

For each corpus say what it measures and what it found. A number without a
reading is not a result. Where the two layers disagree, understand it before
deleting either — that is what found the speaker-rotation defect.

## Step 8 — audit, then Step 5 of `02-the-rebuild`: the delete

Spawn adversarial Opus reviewers first (see discipline 6). **DONE 2026-09-18,
and the answer was NO** — see `02-the-rebuild` Step 5 and the blocking list
below.

Delete `evals/*.py` and `nora/app/eval_support.py`, plus the shims in
`evals/rubric.py` and `evals/judge.py`. **NOT `nora/app/offline.py`** — it is
the offline composition root for 106 integration tests and ten acceptance
rules, and this bundle was wrong to list it.

**AND A SEVENTH, found by trying to clear the second one.** `eval_support.py`
is not only a pile of assemblies — it is the BOUNDARY ADAPTER. `evals/` may
reach `nora.app` and nothing else, so a grader that needs
`nora.agent.registry`'s budgets or `nora.engagement.stages.deliver`'s stop
reasons can only get them through a `nora.app` module. `evals/evaluators.py`
imports both FROM `eval_support` for exactly that reason, and its own docstring
says so: *"evals may only reach nora.app and nora.testing, so the gate's
construction lives HERE."*

Moving the evaluators into `evals/rebuilt/` to save R-15 was tried and
`tests/test_boundaries.py` refused it, correctly. So deleting `eval_support.py`
requires deciding where `evals/` gets production constants from — and every
answer is either a new `nora.app` surface that exists only for evaluation
(§5(2)) or a widening of the import contract. That is a decision about the
boundary, which is `01-boundaries`'s own subject, not a tidy-up.

**Blocking, measured by running the delete:**

1. Port `tests/integration/harness.py` off `build_offline_pipeline`, or keep
   `offline.py`. 106 tests, 10 rules.
2. Re-home R-15 and R-97, whose only named tests live in `evals/` — and see
   the seventh item: R-15's grader cannot simply move, because the import
   contract is what put it there.
3. Give `test_the_barred_call_is_a_name_this_gate_can_see` a positive example
   outside `eval_support.py`. It is the control for a bar protecting a MEASURED
   safety property (ADR 0011, 1/6 vs 6/6), its one example is
   `_StrandsGateClient`, and the obvious "fix" — dropping the assertion — makes
   its sibling vacuous. Disarming a forgery probe by renaming its payload.
4. Extend `tests/integration/test_live_parity_gate.py` to the other four entry
   points. **This is the one-way door**: after the delete, `tag`,
   `participation`, `dreamer` and `proactive_act` can never again be compared
   against the layer that produced every number the repo currently quotes.
5. Fix the 30 dangling doc references, `nora/dreamer/objectives/proactive.md`
   LAST and carefully — it is a live, bind-mounted prompt.
6. Update the four `make eval-*` targets, or they name commands that do not
   exist.

**Consciously accept or replace** — each is a real capability with no
counterpart today: the journeys/routing tier (`evals/journeys.py`, DEC-76's
whole subject), Langfuse export, token-cost accounting, `--repeat`'s k/N and
spread, `--seed-production`, `--effort`, `--skills`, `--via-sdk`,
`--capabilities`, leak/reveal detection, precision/recall and the
false-positive rate design 39 §5 calls the primary number.

Check before deleting: `tests/test_evals_rubric.py` and anything else importing
the shims; `tests/architecture/test_corpora_are_rehomed_not_rewritten.py` dies
WITH the old corpora and that is correct — it exists to make one move safe.

## Step 9 — close out

Update `04-the-convention` §6 (what is no longer untrue), `02-the-rebuild`'s
step table, and `_index.yaml`. One release fragment. Merge PR #334 — no squash,
no rebase-merge (`CONTRIBUTING.md`).

---

## Definition of done

- `evals/*.py`, `eval_support.py`, `offline.py` gone; nothing imports them.
- Every corpus produces a number, and each number has a stated reading.
- `make gate` green, read by exit code.
- Every pin falsified at least once.
- `04-the-convention` §6 describes what is true.

## What would STOP this and should be escalated

- A corpus that cannot be driven without passing a collaborator. That is the
  boundary being wrong, not an obstacle to route around.
- The two layers disagreeing in a way that is not variance and not a corpus
  defect.
- Anything that would change what the live deployment does.
