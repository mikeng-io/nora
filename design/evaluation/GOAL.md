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

## Step 8 — audit, then the delete. **DONE 2026-09-18.**

Three adversarial Opus reviewers ran first. Production safety came back clean;
the other two refuted most of the first live run, and everything they found is
fixed and falsified (`06-first-full-run` §1, `04-the-convention` §5).

The delete landed: **8,218 lines** of old CLI and drivers deleted outright,
and three files RE-HOMED into `tests/` rather than removed —
`tests/offline_pipeline.py`, `tests/offline_eval_support.py`,
`tests/offline_eval_harness.py` (+ its evaluators).

**The blocking list above was solved by reading the constraint correctly, not
by clearing seven items.** Those three files were in `nora/` for one reason,
which each said itself: `evals/` may import `nora.app` and `nora.testing` only,
so a shared TEST helper had to live inside the application to be reachable from
`evals/`. Once `evals/` had no harness, every consumer was under `tests/`,
which has no contract — and the workaround was unnecessary.

**What it cost, and neither is recoverable by trying harder:**

1. **The routing tier.** DEC-76 and DEC-84 are marked NOT ENFORCED.
   `Outcome` carries no tool calls and no corpus is a journey, so "which tool
   did she reach for" is unmeasured. `docs/eval-sop.md` step 2 says so.
2. **Live parity.** `test_live_parity_gate.py` is gone: parity cannot outlive
   one of its two arms. The GATE was compared once and that comparison found a
   real defect (`05-live-parity` §2). The other four entry points never were,
   and now never can be against the old layer.

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
