# GOAL — finish the evaluation rebuild, end to end

> 2026-10-03 (DEC-277): retired; the Cognition session does this now. The note-candidates, curator, note-merge and proactive DECIDE passes, the ingest Standing event/rollup fold and the shadow machinery named here are deleted. This document is the record of what was decided at the time.


> **STATUS, 2026-09-19 — superseded by scope, not withdrawn.**
> `GOAL-2-backbone.md` is the live task order and reads first. Its own scope
> puts porting the thirteen corpora, the gate's span problem, red teaming,
> simulators, chaos, and deleting `evals/rebuilt/` **out of scope** for its
> goal — this file's remaining unexecuted steps (2-4, 6-7, 9) are that later,
> larger work, not something to resume concurrently. Do not pick this file's
> step order back up without checking `GOAL-2-backbone.md`'s own definition of
> done first.

Hand this to a session. It is the whole remaining job, in order, with the
verification each step has to pass. Work autonomously; stop only for a real
blocker or a fact only the operator holds.

**Branch:** `feat/evaluation-rebuild` (PR #334). **Repo entry point:**
`AGENTS.md`. **Read first:** `07-sdk-native` → `08-the-sdk-surface` → `00-overview` →
`01-boundaries` → `02-the-rebuild` → `04-the-convention` → `05-live-parity`.
The first two are the correction; reading them last is how a session rebuilds
the hand-rolled layer.

---

## The rule everything serves

> **Different evaluation suites. Different input. One Nora.**

`nora/subject/obtain(spec)` composes the graph `nora/__main__.py` runs.
`strands_evals.Experiment` drives it through a task function over that
`obtain()`. The eval layer composes nothing — **and, corrected 2026-09-18, it
does not build a harness either** (`07-sdk-native` §2).

## Where it stands

13 corpora, 150 cases, 126 runnable across four entry points (`gate`,
`participation`, `tag`, `dreamer`). Gate green.

**UPDATED 2026-09-18 — the line above was never corrected after Step 8 ran.**
Step 8's audit-and-delete landed the same day: the OLD layer
(`nora/app/eval_support.py`, `nora/app/offline.py`, the original `evals/*.py`)
is gone, 8,187 lines. `evals/rebuilt/` — this bundle's OWN harness, built to
replace it — is what remains, and `07-sdk-native` found IT duplicates
`strands-agents-evals` rather than using it. `evals/rebuilt/` has not itself
been deleted; see `GOAL-2-backbone.md`'s scope and `08-the-sdk-surface.md`
§2.1.

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

> **DO NOT DO STEPS 3 AND 4 AS WRITTEN.** They are a work order to FIX
> `runner.py` and `report.py` — the two files `07-sdk-native` §2 retires,
> 1,037 of 2,267 lines. A session working this file top to bottom repairs code
> it is supposed to delete. What survives is Step 3's ASSERTIONS
> (`creates`/`updates`/`anchor_date`/`must_cite`), which become `StateEquals`
> inputs where they can — `08-the-sdk-surface` §4.5 measures that only one of
> four translates. Check the SDK equivalent does not carry each defect below;
> do not repair ours.

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

> **NOT DONE, and it was reported as done.** No decision is recorded anywhere
> in this bundle. The commit message `Step 5 done` refers to
> `02-the-rebuild`'s step 5 — the delete — which is a different step wearing
> the same number. Measured 2026-09-18:
> `grep -niE "tool_result|tool_call_log" evals/rebuilt/*.py` returns nothing,
> so every `knows.transcript` row is still a room message.
>
> `07-sdk-native` §4.2 names the instrument: `ToolSimulator` models a tool
> result AS a tool result. **Two different step fives in one bundle is the
> mechanism** — rename one before the next session repeats this.

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
- ~~Extend `tests/integration/test_live_parity_gate.py`~~ — that file was
deleted with the old layer (Step 8). Parity cannot outlive one of its two arms.

**Done when:** every corpus has a number, with `subject=` and `judge=` on it.

## Step 7 — the numbers are read, not just produced

For each corpus say what it measures and what it found. A number without a
reading is not a result. Where the two layers disagree, understand it before
deleting either — that is what found the speaker-rotation defect.

## Step 8 — audit, then the delete. **DONE 2026-09-18.**

Three adversarial Opus reviewers ran first. Production safety came back clean;
the other two refuted most of the first live run, and everything they found is
fixed and falsified (`06-first-full-run` §1, `04-the-convention` §5).

The delete landed: **8,187 lines** of old CLI and drivers deleted outright,
and three files RE-HOMED into `tests/` rather than removed —
`tests/offline_pipeline.py`, `tests/offline_eval_support.py`,
`tests/offline_eval_harness.py` (+ its evaluators).

**The blocking list above was solved by reading the constraint correctly, not
by clearing seven items.** Those three files were in `nora/` for one reason,
which each said itself: `evals/` may import `nora.app` and `nora.testing` only,
so a shared TEST helper had to live inside the application to be reachable from
`evals/`. Once `evals/` had no harness, every consumer was under `tests/`,
which has no contract — and the workaround was unnecessary.

**What it cost — one of these two is recoverable, and this list said otherwise:**

1. **The routing tier.** DEC-76 and DEC-84 are marked NOT ENFORCED.
   `Outcome` carries no tool calls and no corpus is a journey, so "which tool
   did she reach for" is unmeasured. `docs/eval-sop.md` step 2 says so.

   **CORRECTED 2026-09-18.** This was written as unrecoverable. It is not:
   `strands-agents-evals` ships `ToolCalled`,
   `ToolSelectionAccuracyEvaluator`, `ToolParameterAccuracyEvaluator` and
   `TrajectoryEvaluator`, in the version pinned at `pyproject.toml:47` the
   whole time. `07-sdk-native` §4.1.
2. **Live parity.** `test_live_parity_gate.py` is gone: parity cannot outlive
   one of its two arms. The GATE was compared once and that comparison found a
   real defect (`05-live-parity` §2). The other four entry points never were,
   and now never can be against the old layer.

## Step 9 — close out

> **HELD 2026-09-18. Do not merge PR #334 on this step's instruction.** The
> operator's anchor: the suite is built on `strands-agents-evals`, and that is
> enforced rather than restated. `07-sdk-native` is the investigation; the
> harness half of this branch is to be replaced before merge. The branch stays
> open — `nora/subject/` and the thirteen corpora are the base the replacement
> builds on, and closing it would mean deriving them again.

Update `04-the-convention` §6 (what is no longer untrue), `02-the-rebuild`'s
step table, and `_index.yaml`. One release fragment. Merge PR #334 — no squash,
no rebase-merge (`CONTRIBUTING.md`).

---

## Definition of done

- `evals/rebuilt/{runner,report,domain}.py` gone; the harness is
  `strands_evals`. (The older form of this line — *"`evals/*.py`,
  `eval_support.py`, `offline.py` gone"* — is both stale and wrong:
  `offline.py` was RE-HOMED, not deleted, and that clause read as satisfied
  while the basis was unmet.)
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
