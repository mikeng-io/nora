---
name: nora-dispatch
description: Turns a design bundle or issue into bounded work units and builder briefs, launches the builder, reads its result back and routes it to independent review. Load before delegating any build, before writing a brief, or when a delegated build drifts. Not for writing the design (nora-design), reviewing (nora-review) or landing a PR (nora-land).
---

# nora-dispatch

The orchestrator's loop from design to delivered work: **design → units →
brief → launch → read back → review → integrate**. `ORCHESTRATION.md` owns the
policy (tiers, worker choice, independence, the two-round limit); this skill is
the order of the steps and the check after each one.

A brief is the builder's whole understanding of the task. A brief the builder
must decode, or whose acceptance it cannot see, produces work no reviewer can
judge: comprehension fails at the first step, and everything after it is
measured against the wrong thing.

## 1. Bind the source

Read the design bundle subject and the issue that own the work. Pin their
revisions (`git rev-parse origin/master`). Write the acceptance down as
checkable statements, each tied to a line of the design or the issue.

- Acceptance lives in the issue or the design. Never only in a temporary file
  or in chat.
- A statement nobody can check ("works well", "robust") is not acceptance;
  restate it as an observable result, or ask the operator.

Check: every acceptance statement names where it comes from and how it will be
shown — a named test, a read-back, an eval case.

## 2. Cut work units

A unit is one bounded outcome a reviewer can read in one sitting.

- One subsystem or one problem class per unit. Split independent problem
  classes into separate units, even when one branch carries them all.
- Order units by dependency. Units that run at the same time must not touch
  the same files or claim the same migration number.
- **Units are review slices, not separate releases.** When the design is one
  integrated change, the units land on one integration branch, each reviewed
  on its own diff, and the whole is measured once at the end.

Check: each unit states its outcome in one sentence, its files or area, what
it depends on, and which acceptance statements it satisfies. Every acceptance
statement is covered by some unit.

## 3. Write the brief

Use this shape, in complete sentences a fresh agent can follow without the
conversation that produced it:

```
Outcome      one sentence: what is true when this unit is done
Why          the design or issue it serves, with revision; the decisions it
             must respect, as links
Acceptance   the checkable statements from step 1 that this unit owns
Inputs       base SHA, branch, worktree; the design files and code paths to
             read; prior findings, if this is a repair
Allowed      the paths and areas it may change
Non-goals    what it must not do, including adjacent fixes
Rules        the skills to load and repo rules that bite here
Checks       the named commands to run once, with what PASS looks like
Stop         where to stop, and the report: head SHA, checks and their rc,
             unmet acceptance, limits
```

**The inclusion rule.** Anything a fresh agent can recover from the repo — file
contents, history, a design section — is an anchor: a path, a revision, a link.
Write out only what it cannot recover: the intent, the decisions and their
reasons, the deltas from the design, the traps already found. A brief that
pastes the design is long; a brief that compresses its own sentences is
unreadable. Neither helps.

Check: read the brief cold. Can someone with only the repo restate the outcome,
the acceptance and the stop point in their own words? Is any sentence
shorthand ("localFFauthorized", run-together words, private abbreviations)?
Rewrite until the answer is yes and no.

## 4. Launch

Follow `ORCHESTRATION.md` «Choosing a worker»: pass provider, model and options
explicitly, and read back what actually launched. One unit per launch. A
builder never starts review, other builders or a wider audit.

Check: the launched model and options match the ones chosen.

## 5. Read back the result

Never accept the builder's report as the result.

- Check out the reported head. Read the diff against the brief's base.
- Confirm the diff stays inside the unit's allowed paths and outcome. Work
  outside it is a finding, not a bonus.
- Read each named check's output and exit code yourself.
- Map every acceptance statement to met, unmet or unavailable, with evidence.

Check: no statement is marked met on the builder's word alone. A clean exit
with no diff, or a diff with no checks run, is not delivered work.

## 6. Review

Route the unit to an independent reviewer under `ORCHESTRATION.md`: a fresh
session on a different vendor from the builder, with the brief, the acceptance,
the base and head, and the diff command. The reviewer loads `nora-review`.

- Triage findings by the policy's verdicts. A repair gets its own brief, built
  the same way, naming the findings it fixes.
- **Two rounds at most.** After round two, report to the operator with what is
  fixed and what is not; only the operator authorises another round.

Check: the repair brief names each finding it addresses, and round two reviews
only the repair delta.

## 7. Integrate and measure

Land each unit per `nora-land`. For an integrated change, merge the units onto
the integration branch and measure the whole once — the eval harness or
snapshot replay the design names — before the integration reaches master.

- **No "ready" without a rehearsal.** Before any claim that a paid run, eval or
  deploy is ready, run its exact path at zero cost: the real SDK path, budgets,
  judges and report, end to end.

Check: the final measurement ran on the integrated head, and its result is
recorded in the issue with the run's evidence.

## Signs the loop is failing

| sign | what it means | fix |
|---|---|---|
| the builder asks what the brief means, or guesses | the brief failed step 3 | rewrite it in full sentences |
| one PR grows past what a reviewer can read | units were cut too large | split at step 2 |
| review finds blockers every round | the unit or its acceptance was unclear | stop at round two; re-cut the unit |
| acceptance exists only in `/tmp` or chat | step 1 skipped | move it to the issue or design |
| "ready" claimed, then the paid run errors | no rehearsal | step 7's rehearsal before any claim |
