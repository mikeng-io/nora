---
name: nora-land
description: Lands a Nora change from first edit to merged master, covering a new worktree from origin/master, the test loop, the gate read by its exit code, a Conventional Commit that is the release note, a PR from the template, an adaptive review, an authored merge commit and the post-merge read-back. Load when starting work, committing, opening a PR or merging. Not for deploying to nora-host (nora-deploy).
---

# nora-land

Worktree → impact-based validation → PR → applicable review → merge → read-back. Rules: `CONTRIBUTING.md`, `AGENTS.md`.
`ORCHESTRATION.md` owns assignment/review policy; `nora-dispatch` owns worker
handoffs. Workers stop at their assigned boundary; the parent owns acceptance.
```bash
TYPE=<commit-type>; SLUG=<kebab-slug>; WT=<new-worktree-path>        # WT outside every checkout
LOG=${TMPDIR:-/tmp}/land-$SLUG; mkdir -p "$LOG"   # fixed, OUTSIDE the worktree
```

## 1. Start: intent and the assigned checkout

Use `nora-work` to recover the outcome and choose the next action;
`nora-dispatch` owns execution and handoff. This skill owns the source-control
and validation steps below. Respect the assigned base and stopping boundary.
If a delegated checkout is already prepared, verify it and skip worktree creation.
For new independent work, create the worktree below; dependent work uses its
assigned base instead of `origin/master`.
An operator agreeing to the proposed fix means implement it, not create a
planning ticket and stop. Deferred execution work belongs in Linear.

```bash
git fetch origin
git worktree add -b "$TYPE/$SLUG" "$WT" origin/master
cd "$WT" || exit 1
uv sync --extra evals        # only when selected Python checks need it
git status -sb; git config core.hooksPath
```

Check: branch/head matches the assigned base (fetched `origin/master` for new
independent work); preserve existing changes. hooksPath ends `.githooks`
(empty: `make hooks`).

- New independent work starts at fetched `origin/master`; inspect the final diff.
- One committing agent per worktree; never edit the primary checkout.
- Never hide a failed `cd`; stop rather than run in the wrong directory.

## 2. Iterate

```bash
make test                           # incremental; aliases: test-fast, test-affected
uv run python -m scripts.pytest_gate tests/unit/test_example.py::test_named
```

Incremental entries compare staged/unstaged/untracked work to HEAD, or HEAD~1 when clean; `BASE=<ref>` overrides auto. Only exact generated untracked LFS post-hooks are ignored.
Unknown/shared-helper changes or errors run ALL; read base/scope. Workers default to two; timing contracts stay serial. Gate is final, not the loop.

## 3. Select validation before running it

State what changed, what could break and what evidence proves it. For small
non-runtime documents, agent-skill wording and images, read the diff/content and
check only relevant links, image readability or references; no local gate or
independent reviewer is required. Commit, PR, merge and read back, subject to
required hosted checks. Do not set up dependencies or wait for a runtime test
slot just for these edits. Runtime prompts/config and executable skill helpers
follow their actual behavior/risk. Investigate unknown impact first; do not use
a full suite as a substitute for understanding. Keep explicit acceptance intact.

Use focused local checks; automatic hosted checks own fast-baseline PR acceptance; complete backend acceptance is on demand. Recurring SQL repairs also need generated history-size scaling and aggregate background/foreground probes under the normal lease.
For an explicitly selected local gate (not a PR prerequisite), run once per candidate:

```bash
make gate > "$LOG/gate.log" 2>&1; GATE_RC=$?; echo "GATE_RC=$GATE_RC" > "$LOG/gate.rc"
grep -E 'gate receipt:|^uv run (ruff|mypy|lint-imports)' "$LOG/gate.log"
grep -E '^affected_tests:|passed|failed|error' "$LOG/gate.log" | tail -n 5
```

`make gate`: code/mixed changes run static steps and affected tests against
`origin/master`; docs-only changes run reader tests without static steps. Unknown
paths/errors remain broad. Local exact-candidate/context success receipts can be
reused; a `REUSED` result is prior acceptance, never a newly executed test count.
Read the worktree Git directory's `nora-gate-receipt.json` (key, command, completion and rc),
alongside the matching original evidence. Dirty/context changes cannot
reuse it. `make gate-reuse` only checks reuse (nonzero miss, no checks), so a
readback cannot accidentally start a cold suite. `make gate-fresh` forces a run;
CI and `make full-gate` are always fresh, full-gate serial/unconditional.

PASS: `GATE_RC=0`, then either fresh checks/reader results (three static steps for
code), or the runner's validated matching success receipt. A changed candidate,
base or execution context requires impact reassessment. Receipt refreshes rerun nothing.

- Never `make gate | tail`: the rc is tail's (merged a red master).
- In the background read `$LOG/gate.rc`, not the notice (last command's rc).
  Kill only a PID you recorded, never `pkill -f`: it kills other sessions' gates.

Runtime Markdown under `nora/`, `evals/`, `scripts/` and fixtures follows behavior
risk. Stage new docs only if selected reference checks need tracked files.

## 4. Commit

```bash
git add <paths> && git commit -F "$LOG/msg.txt"   # commit-msg runs check_commit_subject.py
git log -1 --format='%h %s'
```

- Subject `type(scope): description`, a full clause; the closed type list is
  `CONTRIBUTING.md` «Commit messages» — hooks reject undeclared types.
- Body: what was broken, what was measured, what shipped, what it does not cover.
- Hooks: `commit-msg` runs `scripts/check_commit_subject.py`, `pre-push`
  re-checks every unpushed commit. Refused: no type, an undeclared type, `wip:`,
  GitHub-default merge subjects. `--no-verify` only with the reason in the body.
- Commit on the step-2 checks the edit ran; the gate is once per candidate,
  not a commit precondition. A focused check is never reported as the gate.
  Name paths, not `git add -A`.

## 5. The subject is the release note

Follow `nora-release`: every releasable master push prepares a patch;
generated metadata auto-merges; an application PR merges on green once its
route's review passes, under the standing authorities (`.agents/lanes.md`).
Minor and major are owner milestones at an exact SHA. A change the host must follow
marks `!` or `BREAKING CHANGE:` with what the host must do; it still ships as a patch.

## 6. Merged tree, push, PR

For dependent PRs use [stacked delivery](reference.md#stacked-delivery) instead
of steps 6–9's standalone-master commands; return here for the final master PR.

```bash
git fetch origin; git rev-list --count HEAD..origin/master    # 0: branch is the merged tree
git merge origin/master -m "merge: origin/master into $SLUG, <what came in>"   # if needed: inspect upstream delta; validate affected interactions
git push -u origin HEAD
cp .github/pull_request_template.md "$LOG/pr.md"   # fill in, delete the comments
gh pr create --base master --title "<type(scope): description> (#N, DEC-M)" --body-file "$LOG/pr.md"
gh pr view <N> --json baseRefName,headRefOid       # master, and == git rev-parse HEAD
```

- A bare `git merge origin/master` writes a default subject the hook refuses.
- Title: append `(#N, DEC-M)` only when it closes an issue or lands a decision.
- Follow `.github/pull_request_template.md`; keep required headings and record
  author/tool/model, actual review and validation. Triage owns labels/assignee.
- Gate section: the real outcome (GATE_RC, pass count, origin/master sha merged
  in), the review level and, after step 7, the triage line; a pre-existing
  unrelated failure is named with its issue number. After the LAST push refresh
  the body to `headRefOid` — a head review did not cover is named plus its
  delta, never implied fully reviewed; a receipt refresh reruns nothing.
  A changed tree returns to impact selection, not an automatic full gate. Read it back: `gh pr view <N> --json body,headRefOid`.
- `Closes #N` only when this PR fully resolves it; otherwise `issue #N`.
- A fix reports its mutation result (`docs/maintainers/conventions.md` Proving a fix).

## 7. Review, when the change earns one

- Confirm the tier under `ORCHESTRATION.md`: none uses orchestrator read-back;
  otherwise resolve an independent reviewer and have it load `nora-review`.
- Supply original scope/non-goals, revisioned acceptance, exact base/head, diff,
  claims, evidence and prior findings. Check actual coverage, not just APPROVE.
- Triage under the policy: bounded repair for defects, scoping for invalid intent;
  advice is not scope. Prove code fixes with `nora-prove-fix`, without unrelated repair.
- Repair: validate the delta by impact, commit, push and refresh the PR. No BLOCKER ends
  review; the policy owns convergence.

## 8. Merge: a real merge commit, authored message

Before merge/auto-merge, confirm the route's required artifacts (`nora-work/references/lifecycle.md` «Ceiling and backstop»), then read checks and all PR findings on the latest head;
follow [scanner triage](reference.md#pre-merge-scanner-findings). Green CI alone is insufficient.

```bash
git status -sb     # nothing ahead: pushed head == reviewed head
git fetch origin && # inspect upstream delta; update branch if required by repository rules
gh pr merge <N> --merge --match-head-commit "$(git rev-parse HEAD)" \
  --subject "merge: <the arc> (#N)" --body-file "$LOG/merge-body.md"
```

- A moving master does not automatically invalidate passed validation. Inspect
  `git diff <validated-base>..origin/master` for interactions with this change,
  including shared dependencies/configuration; disjoint paths alone are not proof.
  Preserve original evidence and record the upstream delta assessment. If no
  relevant input changed, do not repeat the local gate or independent review.
  If interactions changed, run only the relevant checks; broaden only when impact
  cannot be bounded. This is an evidence assessment, not an exact-tree receipt hit.
- Bring master in when conflicts or repository branch rules require it, push and
  read required hosted checks. Never bypass those rules. Do not reserve a local
  test slot merely to merge; ready PRs do not wait behind unrelated local gates.
- Author the merge subject (`merge: <the arc> (#N)`) and body; GitHub's default
  subject does not follow this repository's convention.
- `--merge` only, never `--squash` or `--rebase`.
- Do not retarget native stack members or delete their bases with standalone
  branch-chain commands; use the stack procedure.
## 9. Read back

```bash
gh pr view <N> --json state,mergeCommit          # MERGED; note mergeCommit.oid
git fetch origin; git merge-base --is-ancestor <oid> origin/master; echo "ON_MASTER_RC=$?"   # 0 = landed
git log -1 --format='%p | %s' <oid>              # two parents, subject `merge: ...`
```

Touched a migration or the decision index? Check the landed tree (both exit 0 either way; count output):

```bash
git switch --detach origin/master
make heads > "$LOG/heads.txt" 2>&1; grep -c '(head)' "$LOG/heads.txt"    # must print 1
make decision-numbers > "$LOG/dn.txt" 2>&1; grep -vc '^==' "$LOG/dn.txt" # must print 0
```

Then resolve ([reference.md](reference.md#resolve)): settle the artifact (merged, or `make archive BRANCH=<b> REASON="..."`), run `make gc APPLY=1` and read back its totals, update the Linear brief with the reviewer's met/unmet/unavailable marks, and add or extend a lesson in `docs/maintainers/lessons.md` when the lifecycle's resolve step calls for one.
Close only a fully satisfied outcome; GitHub defect reports keep their separate resolution. Report completed/unmet obligations and stop at the agreed boundary. Deploying: nora-deploy.

Stacked delivery, scanner triage and resolve: `nora-land/reference.md`; runtime quirks: `.agents/runtimes/`.
